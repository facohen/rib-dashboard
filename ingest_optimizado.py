"""
ingest_optimizado.py — Pipeline de ingesta RUB (PostgreSQL)

Polars para leer/limpiar CSVs, psycopg2 execute_values para escribir.

Reglas:
  - Programa/Secretaria: se crean si no existen
  - Beneficiario: se resuelve por CUIL; si no existe, se crea
  - Periodo: normalizado a YYYY-MM
  - Dedup: benefit por CUIL+programa+periodo, pago por CUIL+programa+periodo+monto
  - CUIL invalido (<8 chars): benefit con beneficiary_id=NULL
"""

import io
import os
import re
import sys
import glob
import time
import inspect
import logging
from datetime import date

import polars as pl
import psycopg2
from psycopg2.extras import execute_values

from ingest_config import DATASET_CONFIGS, DATASETS_ROOT, PROVINCIA_NORMALIZE
from ingest_log import setup_logger, log_report

from config import load_dotenv
load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL no seteada. export DATABASE_URL=postgresql://user:pass@host/rub")

CHUNK_SIZE = 200_000
BATCH_SIZE = 10_000
MAX_ROWS = 50_000  # 0 = sin limite; >0 = modo test

log = logging.getLogger("ingest")

_MESES = {
    "enero": "01", "febrero": "02", "marzo": "03", "abril": "04",
    "mayo": "05", "junio": "06", "julio": "07", "agosto": "08",
    "septiembre": "09", "octubre": "10", "noviembre": "11", "diciembre": "12",
}

_STAT_KEYS = ("rows", "ben_new", "ben_upd", "benefits_new", "benefits_skip",
              "pay_new", "pay_skip", "cuil_invalido", "errores")

_ACCENT_MAP = str.maketrans("áéíóúÁÉÍÓÚ", "aeiouAEIOU")


def _empty_totals():
    return {k: 0 for k in _STAT_KEYS}


def _add_totals(dst, src):
    for k in dst:
        dst[k] += src.get(k, 0)


def _log_totals(label, totals, elapsed):
    """Resumen de ingesta en formato tabla."""
    log.info(f"\n  {label} completada en {elapsed:.1f}s")
    for key, lbl in [("rows", "Filas"), ("ben_new", "Beneficiarios nuevos"),
                      ("benefits_new", "Benefits nuevos"), ("benefits_skip", "Benefits dup"),
                      ("pay_new", "Pagos nuevos"), ("pay_skip", "Pagos dup"),
                      ("cuil_invalido", "CUIL invalidos"), ("errores", "Errores")]:
        log.info(f"  {lbl + ':':<25} {totals[key]:>10,}")


def _log_chunk(chunk_num, totals, t0):
    elapsed = time.time() - t0
    rate = totals["rows"] / elapsed if elapsed > 0 else 0
    log.info(f"  Chunk {chunk_num}: {totals['rows']:,} [{rate:,.0f}/s]"
             f"  ben={totals['ben_new']:,} benf={totals['benefits_new']:,}"
             f" pay={totals['pay_new']:,}")


# ─── Conexion ─────────────────────────────────────────────

def get_conn():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    return conn


# ─── Preprocesamiento ────────────────────────────────────

def _split_apellido_nombre(df, src_col):
    parts = pl.col(src_col).cast(pl.Utf8).str.strip_chars()
    return df.with_columns([
        parts.str.extract(r"^(\S+)", 1).fill_null("S/D").alias("_apellido"),
        parts.str.replace(r"^\S+\s*", "").fill_null("S/D").alias("_nombre"),
    ])


def _clean_monto_expr(col_name):
    """Expr Polars: limpia monto detectando formato argentino/estandar."""
    raw = pl.col(col_name).cast(pl.Utf8).str.replace_all(r"[^0-9,.\-]", "")
    hc, hd = raw.str.contains(","), raw.str.contains(r"\.")
    return (pl.when(hc & hd).then(raw.str.replace_all(r"\.", "").str.replace(",", "."))
              .when(hc).then(raw.str.replace(",", "."))
              .otherwise(raw))


def pp_belgrano(df, csv_path=None):
    """Belgrano: desdoblar multi-periodos y dividir monto."""
    fallback_year = None
    if csv_path:
        ym = re.search(r'(\d{4})', os.path.basename(csv_path))
        if ym:
            fallback_year = ym.group(1)

    def _parse_periodos(val):
        if not val:
            return [""]
        low = val.strip().lower()

        if any(x in low for x in ("extraordinario", "extra", "pago anual")):
            years = re.findall(r'\d{4}', low)
            return [f"{years[-1] if years else (fallback_year or '2024')}-01"]

        years = re.findall(r'\d{4}', low)
        year_positions = [(m.start(), m.group()) for m in re.finditer(r'\d{4}', low)]
        found_months = sorted((low.find(m), m, n) for m, n in _MESES.items() if m in low)

        if not found_months:
            return [val.strip()]

        result = []
        for pos, _, mes_num in found_months:
            yr = next((yv for yp, yv in year_positions if yp > pos), None)
            yr = yr or (years[-1] if years else fallback_year)
            if yr:
                result.append(f"{yr}-{mes_num}")
        return result or [val.strip()]

    before = len(df)
    df = df.with_columns(
        pl.col("periodo_liquidado")
          .map_elements(_parse_periodos, return_dtype=pl.List(pl.Utf8))
          .alias("_periodos"))

    n = pl.col("_periodos").list.len()
    df = df.with_columns(
        (pl.col("monto_liquidado").cast(pl.Utf8)
         .str.replace_all(r"[^0-9.,\-]", "").str.replace(",", ".")
         .cast(pl.Float64, strict=False) / n)
        .round(2).cast(pl.Utf8).alias("monto_liquidado"))

    df = df.explode("_periodos").drop("periodo_liquidado").rename({"_periodos": "periodo_liquidado"})
    if len(df) > before:
        log.info(f"    Desdobladas: {before:,} -> {len(df):,}")
    return df


PREPROCESS_REGISTRY = {"pp_belgrano": pp_belgrano}


# ─── Cache en memoria ────────────────────────────────────

class IngestCache:
    """Lookup cache para evitar SELECTs repetidos."""
    def __init__(self, conn):
        self.secretarias = {}   # nombre -> id
        self.programs = {}      # nombre_programa -> id
        self.cuils = set()
        self.benefits = set()   # (cuil, program_id, periodo)
        self.payments = set()   # (cuil, program_id, periodo, monto)
        self._load(conn)

    def _load(self, conn):
        cur = conn.cursor()
        t0 = time.time()
        cur.execute("SELECT nombre, id FROM secretarias")
        self.secretarias = dict(cur.fetchall())
        cur.execute("SELECT nombre_programa, id FROM programs")
        self.programs = dict(cur.fetchall())
        cur.execute("SELECT cuil FROM beneficiaries")
        self.cuils = {r[0] for r in cur.fetchall()}
        cur.execute("SELECT cuil_raw, program_id, periodo_mes FROM benefits WHERE cuil_raw IS NOT NULL")
        self.benefits = {tuple(r) for r in cur.fetchall()}
        cur.execute("""SELECT b.cuil, p.program_id, p.periodo_mes, p.monto_prestacion
                       FROM payments p JOIN beneficiaries b ON b.id = p.beneficiary_id""")
        self.payments = {(r[0], r[1], r[2], float(r[3])) for r in cur.fetchall()}
        cur.close()
        log.info(f"  Cache ({time.time()-t0:.1f}s): {len(self.cuils):,} CUILs, "
                 f"{len(self.benefits):,} benefits, {len(self.payments):,} pagos")


# ─── Resolvers ────────────────────────────────────────────

def _ensure_secretaria(conn, cache, nombre):
    if nombre in cache.secretarias:
        return cache.secretarias[nombre]
    cur = conn.cursor()
    cur.execute("INSERT INTO secretarias (nombre) VALUES (%s) RETURNING id", (nombre,))
    sid = cur.fetchone()[0]
    conn.commit()
    cur.close()
    cache.secretarias[nombre] = sid
    log.info(f"    + Secretaria: '{nombre}' (id={sid})")
    return sid


def _ensure_program(conn, cache, nombre_programa, secretaria):
    if nombre_programa in cache.programs:
        return cache.programs[nombre_programa]
    sec_id = _ensure_secretaria(conn, cache, secretaria)
    cur = conn.cursor()
    cur.execute("INSERT INTO programs (secretaria_id, nombre_programa) VALUES (%s, %s) RETURNING id",
                (sec_id, nombre_programa))
    pid = cur.fetchone()[0]
    conn.commit()
    cur.close()
    cache.programs[nombre_programa] = pid
    log.info(f"    + Programa: '{nombre_programa}' (id={pid})")
    return pid


# ─── Limpieza Polars ─────────────────────────────────────

def _normalize_periodo(val):
    if not val:
        return ""
    val = val.strip()
    if len(val) == 7 and val[4] == "-":
        return val
    if val.isdigit() and len(val) in (5, 6):
        if len(val) == 5:
            return val[1:5] + "-0" + val[0]
        return (val[:4] + "-" + val[4:6]) if int(val[:2]) > 12 else (val[2:6] + "-" + val[:2])
    low = val.lower()
    for mes, num in _MESES.items():
        if mes in low:
            ym = re.search(r"(\d{4})", low)
            if ym:
                return ym.group(1) + "-" + num
    return val


def _normalize_prov(val):
    if not val or not val.strip():
        return "No informado"
    clean = val.strip().replace("\xa0", " ")
    key = clean.upper()
    if key in PROVINCIA_NORMALIZE:
        return PROVINCIA_NORMALIZE[key]
    if key.translate(_ACCENT_MAP) in PROVINCIA_NORMALIZE:
        return PROVINCIA_NORMALIZE[key.translate(_ACCENT_MAP)]
    return clean.title()


def clean_chunk(df, col_map, defaults):
    """Renombra columnas, aplica defaults, normaliza datos."""
    staging_cols = [
        "cuil", "nombre", "apellido", "sexo", "fecha_nacimiento",
        "provincia", "codigo_provincia_indec", "departamento",
        "codigo_departamento_indec", "cp", "programa", "secretaria",
        "periodo", "estado", "monto", "fecha_pago"
    ]
    csv_cols = df.columns
    exprs = []
    for scol in staging_cols:
        if scol in col_map and col_map[scol] in csv_cols:
            exprs.append(pl.col(col_map[scol]).cast(pl.Utf8).str.strip_chars().alias(scol))
        elif scol in defaults:
            exprs.append(pl.lit(str(defaults[scol])).alias(scol))
        else:
            exprs.append(pl.lit("").alias(scol))
    df = df.select(exprs)

    # CUIL: solo digitos
    df = df.with_columns(pl.col("cuil").str.replace_all(r"[^0-9]", "").alias("cuil"))

    # Monto: formato argentino/estandar
    monto = pl.col("monto").str.replace_all(r"[^0-9,.\-]", "")
    hc, hd = monto.str.contains(","), monto.str.contains(r"\.")
    df = df.with_columns(
        pl.when(hc & hd).then(monto.str.replace_all(r"\.", "").str.replace(",", "."))
          .when(hc).then(monto.str.replace(",", "."))
          .otherwise(monto).alias("monto"))

    # Sexo: F/M/X/NI
    sexo = pl.col("sexo").str.strip_chars().str.to_uppercase()
    df = df.with_columns(
        pl.when(sexo.str.starts_with("F")).then(pl.lit("F"))
          .when(sexo.str.starts_with("M")).then(pl.lit("M"))
          .when(sexo.str.starts_with("X")).then(pl.lit("X"))
          .when(pl.col("sexo").str.strip_chars() == "1").then(pl.lit("M"))
          .when(pl.col("sexo").str.strip_chars() == "2").then(pl.lit("F"))
          .otherwise(pl.lit("NI")).alias("sexo"))

    # Estado default
    df = df.with_columns(
        pl.when(pl.col("estado") == "").then(pl.lit("ACTIVO"))
          .otherwise(pl.col("estado")).alias("estado"))

    # Periodo y provincia: map_elements (necesario por logica compleja)
    df = df.with_columns(
        pl.col("periodo").map_elements(_normalize_periodo, return_dtype=pl.Utf8).alias("periodo"),
        pl.col("provincia").map_elements(_normalize_prov, return_dtype=pl.Utf8).alias("provincia"))

    return df


# ─── Escritura a DB ──────────────────────────────────────

def write_chunk(conn, cache, df, default_programa, default_secretaria, skip_dedup=False):
    """Escribe chunk limpio a beneficiaries/benefits/payments. Retorna stats."""
    stats = _empty_totals()
    stats["rows"] = len(df)

    prog_name, sec_name = default_programa, default_secretaria
    _ensure_program(conn, cache, prog_name, sec_name)

    ben_batch, benf_batch, pay_batch = [], [], []

    for row in df.to_dicts():
        try:
            cuil = row["cuil"]
            programa = row["programa"] or prog_name
            secretaria = row["secretaria"] or sec_name
            periodo, estado = row["periodo"], row["estado"]

            if programa != prog_name:
                _ensure_program(conn, cache, programa, secretaria)
            prog_id = cache.programs.get(programa)
            if not prog_id:
                stats["errores"] += 1
                continue

            try:
                monto = float(row["monto"]) if row["monto"] else 0.0
            except (ValueError, TypeError):
                monto = 0.0

            # CUIL invalido
            if not cuil or len(cuil) < 8:
                stats["cuil_invalido"] += 1
                benf_batch.append((cuil or None, prog_id, periodo, estado))
                stats["benefits_new"] += 1
                continue

            # Beneficiario nuevo
            if cuil not in cache.cuils:
                fn = row["fecha_nacimiento"]
                try:
                    if fn and len(fn) >= 10:
                        date.fromisoformat(fn[:10])
                    else:
                        fn = "1900-01-01"
                except ValueError:
                    fn = "1900-01-01"

                ben_batch.append((cuil, row["nombre"] or "S/D", row["apellido"] or "S/D",
                                  row["sexo"] or "NI", fn[:10], row["provincia"],
                                  row["codigo_provincia_indec"] or "",
                                  row["departamento"] or "Sin dato",
                                  row["codigo_departamento_indec"] or "", row["cp"] or None))
                cache.cuils.add(cuil)
                stats["ben_new"] += 1

            # Benefit dedup
            bkey = (cuil, prog_id, periodo)
            if not skip_dedup and bkey in cache.benefits:
                stats["benefits_skip"] += 1
            else:
                benf_batch.append((cuil, prog_id, periodo, estado))
                cache.benefits.add(bkey)
                stats["benefits_new"] += 1

            # Payment dedup
            if monto > 0:
                pkey = (cuil, prog_id, periodo, monto)
                if not skip_dedup and pkey in cache.payments:
                    stats["pay_skip"] += 1
                else:
                    fp = row["fecha_pago"]
                    if not fp or len(fp) < 10:
                        fp = periodo + "-01"
                    pay_batch.append((cuil, prog_id, fp[:10], periodo, monto))
                    cache.payments.add(pkey)
                    stats["pay_new"] += 1

        except Exception as e:
            stats["errores"] += 1
            if stats["errores"] <= 3:
                log.warning(f"      ERROR: {e}")

    # Flush batches
    cur = conn.cursor()

    for i in range(0, len(ben_batch), BATCH_SIZE):
        execute_values(cur, """
            INSERT INTO beneficiaries (cuil, nombre, apellido, sexo, fecha_nacimiento,
                 provincia, codigo_provincia_indec, departamento, codigo_departamento_indec, cp)
            VALUES %s ON CONFLICT (cuil) DO UPDATE SET
                nombre = CASE WHEN EXCLUDED.nombre != 'S/D' THEN EXCLUDED.nombre ELSE beneficiaries.nombre END,
                apellido = CASE WHEN EXCLUDED.apellido != 'S/D' THEN EXCLUDED.apellido ELSE beneficiaries.apellido END,
                sexo = CASE WHEN EXCLUDED.sexo != 'NI' THEN EXCLUDED.sexo ELSE beneficiaries.sexo END,
                fecha_nacimiento = CASE WHEN EXCLUDED.fecha_nacimiento != '1900-01-01'
                    THEN EXCLUDED.fecha_nacimiento ELSE beneficiaries.fecha_nacimiento END,
                provincia = CASE WHEN EXCLUDED.provincia != 'Sin dato'
                    THEN EXCLUDED.provincia ELSE beneficiaries.provincia END,
                departamento = CASE WHEN EXCLUDED.departamento != 'Sin dato'
                    THEN EXCLUDED.departamento ELSE beneficiaries.departamento END
        """, ben_batch[i:i+BATCH_SIZE])

    for i in range(0, len(benf_batch), BATCH_SIZE):
        execute_values(cur, """
            INSERT INTO benefits (beneficiary_id, cuil_raw, program_id, periodo_mes, estado_beneficio)
            SELECT (SELECT id FROM beneficiaries WHERE cuil = v.cuil_raw),
                   v.cuil_raw, v.program_id::int, v.periodo_mes, v.estado
            FROM (VALUES %s) AS v(cuil_raw, program_id, periodo_mes, estado)
        """, benf_batch[i:i+BATCH_SIZE])

    for i in range(0, len(pay_batch), BATCH_SIZE):
        execute_values(cur, """
            INSERT INTO payments (beneficiary_id, program_id, fecha_pago, periodo_mes, monto_prestacion)
            SELECT (SELECT id FROM beneficiaries WHERE cuil = v.cuil),
                   v.program_id, v.fecha_pago::date, v.periodo_mes, v.monto::numeric
            FROM (VALUES %s) AS v(cuil, program_id, fecha_pago, periodo_mes, monto)
        """, pay_batch[i:i+BATCH_SIZE])

    conn.commit()
    cur.close()
    return stats


# ─── Pipeline CSV generico ────────────────────────────────

def ingest_csv(conn, csv_path, config):
    """CSV -> Polars chunks -> preprocess -> clean -> write."""
    col_map = config["col_map"]
    defaults = config.get("defaults", {})
    separator = config.get("separator", ",")
    skip_dedup = config.get("skip_dedup", False)
    dedup_exact = config.get("dedup_exact", False)
    split_name_col = config.get("split_name_col")
    preprocess = PREPROCESS_REGISTRY.get(config.get("preprocess"))
    pp_wants_path = preprocess and 'csv_path' in inspect.signature(preprocess).parameters

    t0 = time.time()
    log.info(f"\n{'-'*50}\n  Ingestando: {csv_path}\n{'-'*50}")
    if MAX_ROWS > 0:
        log.info(f"  *** MODO TEST: {MAX_ROWS:,} filas ***")

    cache = IngestCache(conn)
    prog = defaults.get("programa", "Sin programa")
    sec = defaults.get("secretaria", "Sin asignar")
    _ensure_program(conn, cache, prog, sec)

    reader = pl.read_csv_batched(csv_path, infer_schema_length=0,
                                  encoding="utf8-lossy", batch_size=CHUNK_SIZE, separator=separator)
    totals = _empty_totals()
    chunk_num = 0

    while True:
        batches = reader.next_batches(1)
        if not batches or len(batches[0]) == 0:
            break
        raw = batches[0]
        chunk_num += 1

        # Modo test: limitar filas
        if MAX_ROWS > 0:
            remaining = MAX_ROWS - totals["rows"]
            if remaining <= 0:
                break
            if len(raw) > remaining:
                raw = raw.head(remaining)

        if dedup_exact:
            before = len(raw)
            raw = raw.unique()
            if len(raw) < before:
                log.info(f"    Duplicados eliminados: {before - len(raw):,}")

        if split_name_col and split_name_col in raw.columns:
            raw = _split_apellido_nombre(raw, split_name_col)

        if preprocess:
            raw = preprocess(raw, csv_path=csv_path) if pp_wants_path else preprocess(raw)

        stats = write_chunk(conn, cache, clean_chunk(raw, col_map, defaults),
                           prog, sec, skip_dedup=skip_dedup)
        _add_totals(totals, stats)
        _log_chunk(chunk_num, totals, t0)

    _log_totals("Ingesta", totals, time.time() - t0)
    return totals


# ─── Loader Alimentar (cruce menores + titulares) ─────────

def _copy_csv_to_temp(conn, csv_path, table_name, columns, separator=",", select_exprs=None):
    """COPY CSV a tabla temporal PG via StringIO chunks."""
    cur = conn.cursor()
    cur.execute(f"DROP TABLE IF EXISTS {table_name}")
    cur.execute(f"CREATE TEMP TABLE {table_name} ({', '.join(f'{c} text' for c in columns)})")

    reader = pl.read_csv_batched(csv_path, separator=separator,
                                  infer_schema_length=0, encoding="utf8-lossy", batch_size=CHUNK_SIZE)
    total = 0
    while True:
        batches = reader.next_batches(1)
        if not batches or len(batches[0]) == 0:
            break
        chunk = batches[0].select(select_exprs or columns)
        buf = io.StringIO()
        chunk.write_csv(buf, include_header=False)
        buf.seek(0)
        cur.copy_expert(f"COPY {table_name} FROM STDIN CSV", buf)
        total += len(chunk)

    conn.commit()
    cur.close()
    return total


def _fetch_and_write(conn, cache, col_map, defaults, prog, sec, cursor_name, query, totals, t0):
    """Server-side cursor -> DataFrame -> clean -> write. Patron compartido por menores y prenatal."""
    srv = conn.cursor(name=cursor_name)
    srv.itersize = CHUNK_SIZE
    srv.execute(query)
    chunk_num, n_rows = 0, 0

    while True:
        rows = srv.fetchmany(CHUNK_SIZE)
        if not rows:
            break
        chunk_num += 1
        n_rows += len(rows)

        if MAX_ROWS > 0:
            remaining = MAX_ROWS - totals["rows"]
            if remaining <= 0:
                break
            rows = rows[:remaining]

        df = pl.DataFrame({
            "cuil": [r[0] for r in rows], "apellido_nombre": [r[1] for r in rows],
            "fecha_nacimiento": [r[2] for r in rows], "sexo": [r[3] for r in rows],
            "periodo": [r[4] for r in rows], "provincia": [r[5] for r in rows],
            "monto": [r[6] for r in rows],
        })
        df = _split_apellido_nombre(df, "apellido_nombre")
        df = df.select(["cuil", pl.col("_nombre").alias("nombre"),
                         pl.col("_apellido").alias("apellido"),
                         "sexo", "fecha_nacimiento", "provincia", "monto", "periodo"])

        stats = write_chunk(conn, cache, clean_chunk(df, col_map, defaults), prog, sec)
        _add_totals(totals, stats)
        _log_chunk(chunk_num, totals, t0)

    srv.close()
    return n_rows


def load_alimentar(conn):
    """Cruce menores + titulares via PG temp tables."""
    alim_config = next(c for c in DATASET_CONFIGS if c.get("loader") == "load_alimentar")
    col_map, defaults = alim_config["col_map"], alim_config.get("defaults", {})

    menores_path = os.path.join(DATASETS_ROOT, alim_config["path"])
    titulares_path = os.path.join(DATASETS_ROOT, alim_config["titulares_path"])
    for p in (menores_path, titulares_path):
        if not os.path.exists(p):
            log.warning(f"  Archivo no encontrado: {p}")
            return

    t0 = time.time()
    log.info(f"\n{'-'*50}\n  ALIMENTAR -- Cruce menores + titulares\n{'-'*50}")

    prog = defaults.get("programa", "Tarjeta Alimentar")
    sec = defaults.get("secretaria", "Secretaria de Inclusion Social")
    cache = IngestCache(conn)
    _ensure_program(conn, cache, prog, sec)
    cur = conn.cursor()

    try:
        # Fase 1: COPY a temp tables
        n_men = _copy_csv_to_temp(conn, menores_path, "_alim_menores",
            ["cuil_beneficiario", "cuil_titular", "apellido_nombre_beneficiario",
             "fecha_nacimiento_beneficiario", "sexo_beneficiario", "periodo"])
        log.info(f"  Menores: {n_men:,} filas")

        tit_select = [
            pl.col("cuil_titular").cast(pl.Utf8).str.strip_chars(),
            pl.col("apellido_nombre_titular"), pl.col("fecha_nacimiento_titular"),
            pl.col("sexo_titular"),
            pl.col("provincia_titular").cast(pl.Utf8).str.strip_chars(),
            pl.col("prenatal").cast(pl.Utf8).str.strip_chars(),
            _clean_monto_expr("monto_titular").alias("monto_titular"),
            pl.col("periodo").cast(pl.Utf8).str.strip_chars(),
        ]
        n_tit = _copy_csv_to_temp(conn, titulares_path, "_alim_titulares",
            ["cuil_titular", "apellido_nombre_titular", "fecha_nacimiento_titular",
             "sexo_titular", "provincia_titular", "prenatal", "monto_titular", "periodo"],
            separator=";", select_exprs=tit_select)
        log.info(f"  Titulares: {n_tit:,} filas")

        # Fase 2: Indexes + counts
        cur.execute("CREATE INDEX ON _alim_menores(cuil_titular, periodo)")
        cur.execute("CREATE INDEX ON _alim_titulares(cuil_titular, periodo)")
        cur.execute("""CREATE TEMP TABLE _alim_counts AS
                       SELECT cuil_titular, periodo, COUNT(*)::int AS n
                       FROM _alim_menores GROUP BY cuil_titular, periodo""")
        cur.execute("CREATE INDEX ON _alim_counts(cuil_titular, periodo)")
        conn.commit()

        # Fase 3: Menores enriquecidos
        totals = _empty_totals()
        log.info("  Procesando menores...")
        _fetch_and_write(conn, cache, col_map, defaults, prog, sec, "alim_menores_cur", """
            SELECT m.cuil_beneficiario, m.apellido_nombre_beneficiario,
                   m.fecha_nacimiento_beneficiario, m.sexo_beneficiario, m.periodo,
                   COALESCE(t.provincia_titular, ''),
                   COALESCE(ROUND(t.monto_titular::numeric
                       / NULLIF(c.n + COALESCE(t.prenatal::int, 0), 0), 2), 0)::text
            FROM _alim_menores m
            LEFT JOIN _alim_titulares t ON m.cuil_titular = t.cuil_titular AND m.periodo = t.periodo
            LEFT JOIN _alim_counts c ON t.cuil_titular = c.cuil_titular AND t.periodo = c.periodo
        """, totals, t0)

        # Fase 4: Prenatal
        log.info("  Procesando prenatal...")
        n_pre = _fetch_and_write(conn, cache, col_map, defaults, prog, sec, "alim_prenatal_cur", """
            SELECT t.cuil_titular, t.apellido_nombre_titular, t.fecha_nacimiento_titular,
                   t.sexo_titular, t.provincia_titular,
                   ROUND(t.monto_titular::numeric / NULLIF(c.n + 1, 0), 2)::text, t.periodo
            FROM _alim_titulares t
            JOIN _alim_counts c ON t.cuil_titular = c.cuil_titular AND t.periodo = c.periodo
            WHERE t.prenatal::int = 1
        """, totals, t0)
        if n_pre:
            log.info(f"    Prenatal: {n_pre:,} titulares")

    finally:
        for tbl in ("_alim_counts", "_alim_titulares", "_alim_menores"):
            try: cur.execute(f"DROP TABLE IF EXISTS {tbl}")
            except Exception: pass
        try: conn.commit()
        except Exception: pass
        cur.close()

    _log_totals("ALIMENTAR", totals, time.time() - t0)
    log_report(log, "ALIMENTAR", totals, time.time() - t0, _log_path)


# ─── Registro de datasets ────────────────────────────────

LOADER_REGISTRY = {"load_alimentar": load_alimentar}


def _make_loader(config):
    def loader(conn):
        t0 = time.time()
        paths = sorted(glob.glob(os.path.join(DATASETS_ROOT, config["path"])))
        if not paths:
            log.warning(f"  No se encontraron archivos: {config['path']}")
            return
        log.info(f"  Archivos: {len(paths)}")
        grand = _empty_totals()
        for p in paths:
            _add_totals(grand, ingest_csv(conn, p, config))
        log_report(log, config["name"], grand, time.time() - t0, _log_path)
    return loader


DATASETS = []
for _c in DATASET_CONFIGS:
    fn = LOADER_REGISTRY[_c["loader"]] if "loader" in _c else _make_loader(_c)
    DATASETS.append((_c["name"], _c["description"], fn))


# ─── Helpers de limpieza / consulta ──────────────────────

def truncate_data(conn):
    cur = conn.cursor()
    cur.execute("""TRUNCATE payments, benefits, incompatibility_rules,
                   programs, secretarias, beneficiaries RESTART IDENTITY CASCADE""")
    conn.commit()
    cur.close()
    log.info("  Tablas truncadas.")


def truncate_periodo(conn, periodo):
    cur = conn.cursor()
    cur.execute("DELETE FROM payments WHERE periodo_mes = %s", (periodo,))
    pay = cur.rowcount
    cur.execute("DELETE FROM benefits WHERE periodo_mes = %s", (periodo,))
    ben = cur.rowcount
    conn.commit()
    cur.close()
    log.info(f"  {periodo}: {ben:,} benefits, {pay:,} payments eliminados")


def db_summary(conn):
    cur = conn.cursor()
    log.info("\n  Estado de la base de datos:")
    for tbl in ["programs", "beneficiaries", "benefits", "payments",
                "incompatibility_rules", "users", "secretarias"]:
        try:
            cur.execute(f"SELECT COUNT(*) FROM {tbl}")
            log.info(f"  {tbl:<25} {cur.fetchone()[0]:>12,}")
        except Exception:
            conn.rollback()
    cur.execute("SELECT periodo_mes, COUNT(*) FROM benefits GROUP BY 1 ORDER BY 1")
    periodos = cur.fetchall()
    if periodos:
        log.info(f"\n  Periodos ({len(periodos)}):")
        for p, c in periodos:
            log.info(f"    {p}: {c:,}")
    cur.close()


# ─── Helpers de menu y subprocess ────────────────────────

_log_path = None


def _confirm(msg="Confirmar?"):
    return input(f"  {msg} (s/n): ").strip().lower() == "s"


def _pick(title, options):
    print(f"\n  --- {title} ---")
    for key, label in options:
        print(f"    {key}. {label}")
    print(f"    0. Volver")
    return input("\n  Opcion: ").strip()


def _run_script(script, *args):
    import subprocess
    proc = subprocess.Popen(
        [sys.executable, "-u", script] + list(args),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, env={**os.environ, "DATABASE_URL": DATABASE_URL})
    for line in proc.stdout:
        print(f"    {line}", end="", flush=True)
    proc.wait()
    return proc.returncode


def _clear_cache():
    import shutil
    cache_dir = os.path.join(os.path.dirname(__file__), ".cache")
    if os.path.isdir(cache_dir):
        shutil.rmtree(cache_dir, ignore_errors=True)


def _matviews_exist(conn):
    cur = conn.cursor()
    cur.execute("""SELECT COUNT(*) FROM information_schema.tables
                   WHERE table_schema='public' AND table_name IN ('mv_cross','mv_resumen')""")
    n = cur.fetchone()[0]
    cur.close()
    return n == 2


# Backward compat: create_matviews.py y refresh_matviews.py siguen funcionando
# pero matviews.py es el script unificado


# ─── Acciones del menu ───────────────────────────────────

def _do_init_schema(conn):
    conn.close()
    _run_script("seed_real.py")
    return get_conn()


def _do_seed(conn):
    sizes = [("1", "10K  (~12s)", "--small"), ("2", "100K (~2min)", "--medium"),
             ("3", "1M   (~5min)", "--1m"),    ("4", "8M   (~30min)", "")]
    sel = _pick("Datos sinteticos", [(k, lbl) for k, lbl, _ in sizes])
    flag = {k: f for k, _, f in sizes}.get(sel)
    if flag is None or not _confirm("Borra datos existentes. Continuar?"):
        return conn
    conn.close()
    _run_script("seed_sintetico.py", *([flag] if flag else []))
    return get_conn()


def _do_ingest_csv(conn):
    if not DATASETS:
        print("\n  No hay datasets configurados. Ver ingest_config.py")
        return conn
    options = [(str(i), f"{name} -- {desc}") for i, (name, desc, _) in enumerate(DATASETS, 1)]
    if len(DATASETS) > 1:
        options.append(("T", "Cargar TODOS"))
    sel = _pick("Datos reales (CSV)", options)
    if sel == "0" or not sel:
        return conn
    if sel.upper() == "T":
        if _confirm(f"Cargar {len(DATASETS)} datasets?"):
            for name, _, loader in DATASETS:
                log.info(f"\n  Cargando: {name}...")
                loader(conn)
        return conn
    try:
        idx = int(sel) - 1
        if 0 <= idx < len(DATASETS):
            name, _, loader = DATASETS[idx]
            log.info(f"\n  Cargando: {name}...")
            loader(conn)
    except (ValueError, IndexError):
        print("  Opcion invalida.")
    return conn


def _do_generate_matviews(conn):
    conn.close()
    _run_script("matviews.py")  # auto-detecta create vs refresh
    _clear_cache()
    return get_conn()


def _do_status(conn):
    db_summary(conn)
    cur = conn.cursor()
    for name in ["mv_cross", "mv_resumen"]:
        cur.execute("""SELECT 1 FROM information_schema.tables
                       WHERE table_schema='public' AND table_name=%s""", (name,))
        if cur.fetchone():
            cur.execute(f"SELECT COUNT(*) FROM {name}")
            log.info(f"    {name}: {cur.fetchone()[0]:,} filas")
        else:
            log.info(f"    {name}: no existe")
    cur.close()
    return conn


def _do_check(conn):
    cur = conn.cursor()
    results = []

    cur.execute("""SELECT COUNT(*) FROM pg_tables WHERE schemaname='public'
                   AND tablename IN ('beneficiaries','benefits','payments',
                                     'programs','users','incompatibility_rules')""")
    n = cur.fetchone()[0]
    results.append(("ok" if n >= 5 else "ERR", f"Schema: {n}/6 tablas"))

    cur.execute("""SELECT COUNT(*) FROM information_schema.tables
                   WHERE table_schema='public' AND table_name IN ('mv_cross','mv_resumen')""")
    mv = cur.fetchone()[0]
    results.append(("ok" if mv == 2 else "ERR", f"Matviews: {mv}/2"))

    if mv == 2:
        for label, sql, ok_fn in [
            ("mv_resumen vs raw",
             """SELECT (SELECT SUM(personas) FROM mv_resumen WHERE periodo_mes =
                  (SELECT MIN(periodo_mes) FROM mv_resumen))
                - (SELECT COUNT(DISTINCT beneficiary_id) + COUNT(*) FILTER (WHERE beneficiary_id IS NULL)
                   FROM benefits WHERE periodo_mes = (SELECT MIN(periodo_mes) FROM mv_resumen))""",
             lambda v: v == 0),
            ("Cross-table beneficios",
             """SELECT COUNT(*) FROM (
                  SELECT periodo_mes FROM mv_cross GROUP BY 1 HAVING SUM(beneficios) !=
                    (SELECT SUM(beneficios) FROM mv_resumen r WHERE r.periodo_mes = mv_cross.periodo_mes)
                ) x""",
             lambda v: v == 0),
        ]:
            try:
                cur.execute(sql)
                results.append(("ok" if ok_fn(cur.fetchone()[0]) else "ERR", label))
            except Exception:
                results.append(("--", f"{label} (skip)"))
                conn.rollback()

    try:
        import requests as req
        r = req.get("http://localhost:5000/api/health/api", timeout=3)
        results.append(("ok" if r.status_code == 200 else "--", "API health"))
    except Exception:
        results.append(("--", "API (no corriendo)"))

    for status, label in results:
        print(f"  {status:>3}  {label}")
    print(f"\n  {sum(1 for s, _ in results if s == 'ok')}/{len(results)} passed")
    return conn


def _do_clean(conn):
    sel = _pick("Limpiar datos", [("1", "Periodo especifico"), ("2", "TODAS las tablas")])
    if sel == "1":
        periodo = input("  Periodo (YYYY-MM): ").strip()
        if periodo and _confirm(f"Borrar {periodo}?"):
            truncate_periodo(conn, periodo)
    elif sel == "2":
        if _confirm("ATENCION: Esto borra TODOS los datos"):
            truncate_data(conn)
    return conn


def _do_dashboard(conn):
    conn.close()
    log.info("\n  Dashboard: http://localhost:5000  (Ctrl+C para detener)\n")
    try:
        import subprocess
        subprocess.run([sys.executable, "app.py"], env={**os.environ, "DATABASE_URL": DATABASE_URL})
    except KeyboardInterrupt:
        pass
    return get_conn()


# ─── Menu principal ──────────────────────────────────────

MENU_OPTIONS = [
    ("1", "Inicializar DB (schema + indices)",  _do_init_schema),
    ("2", "Cargar datos sinteticos (seed)",      _do_seed),
    ("3", "Ingestar datos reales (CSV)",         _do_ingest_csv),
    ("4", "Ver estado de la base de datos",      _do_status),
    ("5", "Generar tablas materializadas",       _do_generate_matviews),
    ("6", "Check consistencia",                  _do_check),
    ("7", "Limpiar datos",                       _do_clean),
    ("8", "Servir dashboard",                    _do_dashboard),
]


def menu():
    global log, _log_path
    log, _log_path = setup_logger()

    conn = get_conn()
    db_url = DATABASE_URL.split("@")[-1] if "@" in DATABASE_URL else DATABASE_URL
    log.info(f"\n{'='*50}\n  RUB -- Pipeline de Datos\n  DB: {db_url}\n{'='*50}")

    actions = {k: fn for k, _, fn in MENU_OPTIONS}
    while True:
        print()
        for key, label, _ in MENU_OPTIONS:
            print(f"    {key}. {label}")
        print(f"    0. Salir")

        sel = input("\n  Opcion: ").strip()
        if sel == "0":
            break
        fn = actions.get(sel)
        if fn:
            result = fn(conn)
            if result is not None:
                conn = result
        else:
            print("  Opcion invalida.")

    conn.close()
    log.info("  Hasta luego.\n")


if __name__ == "__main__":
    menu()
