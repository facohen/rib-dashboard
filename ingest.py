"""
ingest.py — Ingesta de datasets CSV al esquema RUB (PostgreSQL)

Usa Polars para leer CSVs en chunks, limpiar y transformar en memoria,
y psycopg2 execute_values para escribir directo a las tablas finales.

Uso:
    python ingest.py

Reglas de ingesta:
  - Programa: si no existe en DB, se crea automáticamente
  - Secretaría: ídem
  - Beneficiario: se resuelve por CUIL; si no existe, se crea
  - Periodo: normalizado a YYYY-MM
  - Dedup benefit por CUIL + programa + periodo
  - Dedup pago por CUIL + programa + periodo + monto
  - CUIL inválido (<8 chars): benefit con beneficiary_id=NULL
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
from decimal import Decimal

import polars as pl
import psycopg2
from psycopg2.extras import execute_values

from ingest_config import DATASET_CONFIGS, DATASETS_ROOT, PROVINCIA_NORMALIZE
from ingest_log import setup_logger, log_report

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost/rub"
)
CHUNK_SIZE = 200_000
BATCH_SIZE = 10_000
MAX_ROWS = 50_000  # 0 = sin límite; >0 = cortar después de N filas (modo test)

log = logging.getLogger("ingest")

_MESES = {
    "enero": "01", "febrero": "02", "marzo": "03", "abril": "04",
    "mayo": "05", "junio": "06", "julio": "07", "agosto": "08",
    "septiembre": "09", "octubre": "10", "noviembre": "11", "diciembre": "12",
}

_STAT_KEYS = ("rows", "ben_new", "ben_upd", "benefits_new", "benefits_skip",
              "pay_new", "pay_skip", "cuil_invalido", "errores")

def _empty_totals():
    return {k: 0 for k in _STAT_KEYS}


# -------------------------------------------------------------
# Preprocesamiento genérico y por dataset
# -------------------------------------------------------------

def _split_apellido_nombre(df, src_col):
    """Divide 'APELLIDO NOMBRE...' en _apellido (primer token) y _nombre (resto)."""
    parts = pl.col(src_col).cast(pl.Utf8).str.strip_chars()
    df = df.with_columns([
        parts.str.extract(r"^(\S+)", 1).fill_null("S/D").alias("_apellido"),
        parts.str.replace(r"^\S+\s*", "").fill_null("S/D").alias("_nombre"),
    ])
    return df


def pp_belgrano(df, csv_path=None):
    """Preprocesamiento Belgrano: desdoblar multi-periodos y dividir monto."""
    fallback_year = None
    if csv_path:
        ym = re.search(r'(\d{4})', os.path.basename(csv_path))
        if ym:
            fallback_year = ym.group(1)

    def _parse_periodos(val):
        if not val:
            return [""]
        low = val.strip().lower()

        # Extraordinario / pago anual → enero del año mencionado
        if "extraordinario" in low or "extra" in low or "pago anual" in low:
            years = re.findall(r'\d{4}', low)
            yr = years[-1] if years else (fallback_year or "2024")
            return [f"{yr}-01"]

        # Extraer años con posición
        years = re.findall(r'\d{4}', low)
        year_positions = [(m.start(), m.group()) for m in re.finditer(r'\d{4}', low)]

        # Extraer meses con posición
        found_months = []
        for mes, num in _MESES.items():
            pos = low.find(mes)
            if pos >= 0:
                found_months.append((pos, mes, num))
        found_months.sort()

        if not found_months:
            return [val.strip()]

        # Asignar año a cada mes: año más cercano DESPUÉS del mes
        result = []
        for pos, mes_name, mes_num in found_months:
            assigned_year = None
            for ypos, yval in year_positions:
                if ypos > pos:
                    assigned_year = yval
                    break
            if not assigned_year:
                assigned_year = years[-1] if years else fallback_year
            if assigned_year:
                result.append(f"{assigned_year}-{mes_num}")

        return result if result else [val.strip()]

    rows_before = len(df)

    # Parsear periodos → lista
    df = df.with_columns(
        pl.col("periodo_liquidado")
          .map_elements(_parse_periodos, return_dtype=pl.List(pl.Utf8))
          .alias("_periodos")
    )

    # Dividir monto entre cantidad de periodos
    n = pl.col("_periodos").list.len()
    df = df.with_columns(
        (pl.col("monto_liquidado").cast(pl.Utf8)
         .str.replace_all(r"[^0-9.,\-]", "")
         .str.replace(",", ".")
         .cast(pl.Float64, strict=False) / n)
        .round(2).cast(pl.Utf8)
        .alias("monto_liquidado")
    )

    # Explotar: 1 fila con 3 periodos → 3 filas con monto/3
    df = df.explode("_periodos")
    df = df.drop("periodo_liquidado").rename({"_periodos": "periodo_liquidado"})

    rows_after = len(df)
    if rows_after > rows_before:
        log.info(f"    Filas desdobladas: {rows_before:,} -> {rows_after:,} (+{rows_after - rows_before:,})")

    return df


PREPROCESS_REGISTRY = {
    "pp_belgrano": pp_belgrano,
}


# -------------------------------------------------------------
# Conexión
# -------------------------------------------------------------

def get_conn():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    return conn


# -------------------------------------------------------------
# Caches en memoria (se cargan una vez, se actualizan al insertar)
# -------------------------------------------------------------

class IngestCache:
    """Caches de lookup para evitar SELECTs repetidos."""

    def __init__(self, conn):
        self.conn = conn
        self.secretarias = {}   # {nombre: id}
        self.programs = {}      # {nombre_programa: id}
        self.cuils = set()      # set de CUILs que ya existen en beneficiaries
        self.benefits = set()   # set de (cuil, program_id, periodo) ya existentes
        self.payments = set()   # set de (cuil, program_id, periodo, monto) ya existentes
        self._load(conn)

    def _load(self, conn):
        cur = conn.cursor()
        t0 = time.time()

        cur.execute("SELECT nombre, id FROM secretarias")
        self.secretarias = {r[0]: r[1] for r in cur.fetchall()}

        cur.execute("SELECT nombre_programa, id FROM programs")
        self.programs = {r[0]: r[1] for r in cur.fetchall()}

        cur.execute("SELECT cuil FROM beneficiaries")
        self.cuils = {r[0] for r in cur.fetchall()}

        cur.execute("""
            SELECT cuil_raw, program_id, periodo_mes FROM benefits
            WHERE cuil_raw IS NOT NULL
        """)
        self.benefits = {(r[0], r[1], r[2]) for r in cur.fetchall()}

        cur.execute("""
            SELECT b.cuil, pay.program_id, pay.periodo_mes, pay.monto_prestacion
            FROM payments pay
            JOIN beneficiaries b ON b.id = pay.beneficiary_id
        """)
        self.payments = {(r[0], r[1], r[2], float(r[3])) for r in cur.fetchall()}

        cur.close()
        elapsed = time.time() - t0
        log.info(f"  Cache cargado ({elapsed:.1f}s):")
        log.info(f"    {len(self.secretarias)} secretarías, {len(self.programs)} programas")
        log.info(f"    {len(self.cuils):,} CUILs, {len(self.benefits):,} benefits, {len(self.payments):,} pagos")


# -------------------------------------------------------------
# Resolvers (pocos registros: secretarías y programas)
# -------------------------------------------------------------

def _ensure_secretaria(conn, cache, nombre):
    if nombre in cache.secretarias:
        return cache.secretarias[nombre]
    cur = conn.cursor()
    cur.execute("INSERT INTO secretarias (nombre) VALUES (%s) RETURNING id", (nombre,))
    sid = cur.fetchone()[0]
    conn.commit()
    cur.close()
    cache.secretarias[nombre] = sid
    log.info(f"    + Secretaría: '{nombre}' (id={sid})")
    return sid


def _ensure_program(conn, cache, nombre_programa, secretaria):
    if nombre_programa in cache.programs:
        return cache.programs[nombre_programa]
    sec_id = _ensure_secretaria(conn, cache, secretaria)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO programs (secretaria_id, nombre_programa) VALUES (%s, %s) RETURNING id",
        (sec_id, nombre_programa)
    )
    pid = cur.fetchone()[0]
    conn.commit()
    cur.close()
    cache.programs[nombre_programa] = pid
    log.info(f"    + Programa: '{nombre_programa}' (id={pid})")
    return pid


# -------------------------------------------------------------
# Limpieza de datos en Polars (en memoria, por chunk)
# -------------------------------------------------------------

def clean_chunk(df, col_map, defaults):
    """
    Toma un chunk de Polars (DataFrame crudo del CSV),
    renombra columnas, aplica defaults, limpia datos.
    Retorna DataFrame con columnas normalizadas.
    """
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

    # Limpiar CUIL: solo dígitos, al menos 8 chars
    df = df.with_columns(
        pl.col("cuil").str.replace_all(r"[^0-9]", "").alias("cuil")
    )

    # Limpiar monto: detectar formato automáticamente
    # 1. Strip caracteres no numéricos (excepto .,-)
    # 2. Si tiene coma Y punto → argentino (1.234,56): quitar puntos, coma→punto
    # 3. Si tiene solo coma → coma decimal (1234,56): coma→punto
    # 4. Si tiene solo punto → estándar (1234.56): dejar
    monto_clean = pl.col("monto").str.replace_all(r"[^0-9,.\-]", "")
    has_comma = monto_clean.str.contains(",")
    has_dot = monto_clean.str.contains(r"\.")

    df = df.with_columns(
        pl.when(has_comma & has_dot)
          .then(monto_clean.str.replace_all(r"\.", "").str.replace(",", "."))
          .when(has_comma)
          .then(monto_clean.str.replace(",", "."))
          .otherwise(monto_clean)
          .alias("monto")
    )

    # Normalizar sexo (F/M/X, 1=M, 2=F)
    sexo = pl.col("sexo").str.strip_chars()
    df = df.with_columns(
        pl.when(sexo.str.to_uppercase().str.starts_with("F")).then(pl.lit("F"))
          .when(sexo.str.to_uppercase().str.starts_with("M")).then(pl.lit("M"))
          .when(sexo.str.to_uppercase().str.starts_with("X")).then(pl.lit("X"))
          .when(sexo == "1").then(pl.lit("M"))
          .when(sexo == "2").then(pl.lit("F"))
          .otherwise(pl.lit("NI"))
          .alias("sexo")
    )

    # Default estado
    df = df.with_columns(
        pl.when(pl.col("estado") == "").then(pl.lit("ACTIVO"))
          .otherwise(pl.col("estado"))
          .alias("estado")
    )

    # Normalizar periodo a YYYY-MM (genérico)
    # Soporta: "2024-04", "202404", "122023", "cuota diciembre 2023", "diciembre 2023"
    def _normalize_periodo(val):
        if not val:
            return ""
        val = val.strip()
        # Ya es YYYY-MM
        if len(val) == 7 and val[4] == "-":
            return val
        # 5-6 dígitos: MMYYYY (sin/con zero-pad) o YYYYMM
        if val.isdigit() and len(val) in (5, 6):
            if len(val) == 5:
                # MYYYY → 0M + YYYY
                return val[1:5] + "-0" + val[0]
            first2 = int(val[:2])
            if first2 > 12:
                return val[:4] + "-" + val[4:6]  # YYYYMM
            else:
                return val[2:6] + "-" + val[:2]  # MMYYYY
        # Texto con mes en español: "cuota diciembre 2023", "diciembre 2023"
        low = val.lower()
        for mes, num in _MESES.items():
            if mes in low:
                year_match = re.search(r"(\d{4})", low)
                if year_match:
                    return year_match.group(1) + "-" + num
        return val

    df = df.with_columns(
        pl.col("periodo").map_elements(_normalize_periodo, return_dtype=pl.Utf8).alias("periodo")
    )

    # Normalizar provincia: siempre debe tener valor, nunca vacío
    _ACCENT_MAP = str.maketrans("áéíóúÁÉÍÓÚ", "aeiouAEIOU")

    def _normalize_prov(val):
        if not val or val.strip() == "":
            return "No informado"
        # Limpiar: non-breaking spaces, tildes
        clean = val.strip().replace("\xa0", " ")
        key = clean.upper()
        # Buscar con tildes primero, luego sin tildes
        if key in PROVINCIA_NORMALIZE:
            return PROVINCIA_NORMALIZE[key]
        key_no_accent = key.translate(_ACCENT_MAP)
        if key_no_accent in PROVINCIA_NORMALIZE:
            return PROVINCIA_NORMALIZE[key_no_accent]
        return clean.title()

    df = df.with_columns(
        pl.col("provincia").map_elements(_normalize_prov, return_dtype=pl.Utf8).alias("provincia")
    )

    return df


# -------------------------------------------------------------
# Escritura directa a tablas finales (por chunk)
# -------------------------------------------------------------

def write_chunk(conn, cache, df, default_programa, default_secretaria, skip_dedup=False):
    """
    Toma un chunk limpio (Polars DataFrame) y escribe directo
    a beneficiaries, benefits, payments. Sin staging.
    Retorna dict con stats del chunk.
    """
    stats = _empty_totals()
    stats["rows"] = len(df)

    # Resolver programa default
    prog_name = default_programa
    sec_name = default_secretaria
    _ensure_program(conn, cache, prog_name, sec_name)

    # Convertir a lista de dicts para iterar
    rows = df.to_dicts()

    # Acumular batches
    ben_batch = []    # para INSERT beneficiaries
    benf_batch = []   # para INSERT benefits
    pay_batch = []    # para INSERT payments

    for row in rows:
        try:
            cuil = row["cuil"]
            programa = row["programa"] or prog_name
            secretaria = row["secretaria"] or sec_name
            periodo = row["periodo"]
            estado = row["estado"]

            # Resolver programa si es distinto al default
            if programa != prog_name:
                _ensure_program(conn, cache, programa, secretaria)
            prog_id = cache.programs.get(programa)
            if not prog_id:
                stats["errores"] += 1
                continue

            # Monto
            try:
                monto = float(row["monto"]) if row["monto"] else 0.0
            except (ValueError, TypeError):
                monto = 0.0

            # CUIL inválido
            if not cuil or len(cuil) < 8:
                stats["cuil_invalido"] += 1
                benf_batch.append((cuil or None, prog_id, periodo, estado))
                stats["benefits_new"] += 1
                continue

            # Beneficiario: ¿existe?
            if cuil not in cache.cuils:
                fn = row["fecha_nacimiento"]
                # Validar fecha
                try:
                    if fn and len(fn) >= 10:
                        date.fromisoformat(fn[:10])
                    else:
                        fn = "1900-01-01"
                except ValueError:
                    fn = "1900-01-01"

                ben_batch.append((
                    cuil,
                    row["nombre"] or "S/D",
                    row["apellido"] or "S/D",
                    row["sexo"] or "NI",
                    fn[:10],
                    row["provincia"],
                    row["codigo_provincia_indec"] or "",
                    row["departamento"] or "Sin dato",
                    row["codigo_departamento_indec"] or "",
                    row["cp"] or None,
                ))
                cache.cuils.add(cuil)
                stats["ben_new"] += 1

            # Benefit: dedup por (cuil, prog_id, periodo)
            bkey = (cuil, prog_id, periodo)
            if not skip_dedup and bkey in cache.benefits:
                stats["benefits_skip"] += 1
            else:
                benf_batch.append((cuil, prog_id, periodo, estado))
                cache.benefits.add(bkey)
                stats["benefits_new"] += 1

            # Payment: dedup por (cuil, prog_id, periodo, monto)
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

    # -- Flush all batches (single cursor, single commit) --
    cur = conn.cursor()

    for i in range(0, len(ben_batch), BATCH_SIZE):
        execute_values(cur, """
            INSERT INTO beneficiaries
                (cuil, nombre, apellido, sexo, fecha_nacimiento,
                 provincia, codigo_provincia_indec,
                 departamento, codigo_departamento_indec, cp)
            VALUES %s
            ON CONFLICT (cuil) DO UPDATE SET
                nombre = CASE WHEN EXCLUDED.nombre != 'S/D'
                              THEN EXCLUDED.nombre ELSE beneficiaries.nombre END,
                apellido = CASE WHEN EXCLUDED.apellido != 'S/D'
                                THEN EXCLUDED.apellido ELSE beneficiaries.apellido END,
                sexo = CASE WHEN EXCLUDED.sexo != 'NI'
                            THEN EXCLUDED.sexo ELSE beneficiaries.sexo END,
                fecha_nacimiento = CASE WHEN EXCLUDED.fecha_nacimiento != '1900-01-01'
                                        THEN EXCLUDED.fecha_nacimiento
                                        ELSE beneficiaries.fecha_nacimiento END,
                provincia = CASE WHEN EXCLUDED.provincia != 'Sin dato'
                                 THEN EXCLUDED.provincia ELSE beneficiaries.provincia END,
                departamento = CASE WHEN EXCLUDED.departamento != 'Sin dato'
                                    THEN EXCLUDED.departamento
                                    ELSE beneficiaries.departamento END
        """, ben_batch[i:i+BATCH_SIZE])

    for i in range(0, len(benf_batch), BATCH_SIZE):
        execute_values(cur, """
            INSERT INTO benefits
                (beneficiary_id, cuil_raw, program_id, periodo_mes, estado_beneficio)
            SELECT
                (SELECT id FROM beneficiaries WHERE cuil = v.cuil_raw),
                v.cuil_raw, v.program_id::int, v.periodo_mes, v.estado
            FROM (VALUES %s) AS v(cuil_raw, program_id, periodo_mes, estado)
        """, benf_batch[i:i+BATCH_SIZE])

    for i in range(0, len(pay_batch), BATCH_SIZE):
        execute_values(cur, """
            INSERT INTO payments
                (beneficiary_id, program_id, fecha_pago, periodo_mes, monto_prestacion)
            SELECT
                (SELECT id FROM beneficiaries WHERE cuil = v.cuil),
                v.program_id, v.fecha_pago::date, v.periodo_mes, v.monto::numeric
            FROM (VALUES %s) AS v(cuil, program_id, fecha_pago, periodo_mes, monto)
        """, pay_batch[i:i+BATCH_SIZE])

    conn.commit()
    cur.close()
    return stats


# -------------------------------------------------------------
# Pipeline completo
# -------------------------------------------------------------

def ingest_csv(conn, csv_path, config):
    """
    Pipeline genérico: CSV → Polars chunks → preprocess → clean → write.

    Toda la configuración viene del dict `config`:
      col_map, defaults, separator, preprocess, skip_dedup,
      dedup_exact, split_name_col
    """
    col_map = config["col_map"]
    defaults = config.get("defaults", {})
    separator = config.get("separator", ",")
    skip_dedup = config.get("skip_dedup", False)
    dedup_exact = config.get("dedup_exact", False)
    split_name_col = config.get("split_name_col")
    preprocess = PREPROCESS_REGISTRY.get(config.get("preprocess"))

    t0 = time.time()

    log.info(f"\n{'-'*55}")
    log.info(f"  Ingestando: {csv_path}")
    log.info(f"{'-'*55}")

    # Detectar columnas
    schema = pl.scan_csv(csv_path, infer_schema_length=0,
                         encoding="utf8-lossy", separator=separator).collect_schema()
    log.info(f"  Columnas CSV: {schema.names()}")
    if MAX_ROWS > 0:
        log.info(f"  *** MODO TEST: límite de {MAX_ROWS:,} filas ***")

    # Cargar caches
    cache = IngestCache(conn)

    # Resolver programa y secretaría default
    prog = defaults.get("programa", "Sin programa")
    sec = defaults.get("secretaria", "Sin asignar")
    _ensure_program(conn, cache, prog, sec)

    # Preprocess: detectar si acepta csv_path (una sola vez)
    pp_wants_path = (preprocess and
                     'csv_path' in inspect.signature(preprocess).parameters)

    # Leer en chunks
    reader = pl.read_csv_batched(
        csv_path, infer_schema_length=0,
        encoding="utf8-lossy", batch_size=CHUNK_SIZE,
        separator=separator
    )

    totals = _empty_totals()
    chunk_num = 0

    while True:
        batches = reader.next_batches(1)
        if batches is None:
            break
        raw_chunk = batches[0]
        if len(raw_chunk) == 0:
            break

        chunk_num += 1

        # Limitar filas en modo test
        if MAX_ROWS > 0:
            remaining = MAX_ROWS - totals["rows"]
            if remaining <= 0:
                break
            if len(raw_chunk) > remaining:
                raw_chunk = raw_chunk.head(remaining)

        # Operaciones genéricas (declarativas desde config)
        if dedup_exact:
            before = len(raw_chunk)
            raw_chunk = raw_chunk.unique()
            dropped = before - len(raw_chunk)
            if dropped > 0:
                log.info(f"    Filas duplicadas exactas eliminadas: {dropped:,}")

        if split_name_col and split_name_col in raw_chunk.columns:
            raw_chunk = _split_apellido_nombre(raw_chunk, split_name_col)

        # Preprocesamiento custom (solo si hay lógica compleja, ej: Belgrano)
        if preprocess:
            if pp_wants_path:
                raw_chunk = preprocess(raw_chunk, csv_path=csv_path)
            else:
                raw_chunk = preprocess(raw_chunk)

        # Limpiar en memoria
        clean = clean_chunk(raw_chunk, col_map, defaults)

        # Escribir directo
        stats = write_chunk(conn, cache, clean, prog, sec, skip_dedup=skip_dedup)

        # Acumular
        for k in totals:
            totals[k] += stats.get(k, 0)

        elapsed = time.time() - t0
        rate = totals["rows"] / elapsed if elapsed > 0 else 0
        log.info(f"  Chunk {chunk_num}: {totals['rows']:,} filas [{rate:,.0f}/s]"
                 f"  ben={totals['ben_new']:,} benf={totals['benefits_new']:,}"
                 f" pay={totals['pay_new']:,}")

    elapsed = time.time() - t0
    log.info(f"\n  Ingesta completada en {elapsed:.1f}s")
    log.info(f"  {'Filas procesadas:':<30} {totals['rows']:>10,}")
    log.info(f"  {'Beneficiarios nuevos:':<30} {totals['ben_new']:>10,}")
    log.info(f"  {'Benefits nuevos:':<30} {totals['benefits_new']:>10,}")
    log.info(f"  {'Benefits omitidos (dup):':<30} {totals['benefits_skip']:>10,}")
    log.info(f"  {'Pagos nuevos:':<30} {totals['pay_new']:>10,}")
    log.info(f"  {'Pagos omitidos (dup):':<30} {totals['pay_skip']:>10,}")
    log.info(f"  {'CUIL inválidos:':<30} {totals['cuil_invalido']:>10,}")
    log.info(f"  {'Errores:':<30} {totals['errores']:>10,}")
    return totals


# -------------------------------------------------------------
# Helpers de limpieza / consulta
# -------------------------------------------------------------

def truncate_data(conn):
    cur = conn.cursor()
    cur.execute("""
        TRUNCATE payments, benefits, incompatibility_rules,
                 programs, secretarias, beneficiaries
        RESTART IDENTITY CASCADE
    """)
    conn.commit()
    cur.close()
    log.info("  Tablas de datos truncadas.")


def truncate_periodo(conn, periodo):
    cur = conn.cursor()
    cur.execute("DELETE FROM payments WHERE periodo_mes = %s", (periodo,))
    pay_del = cur.rowcount
    cur.execute("DELETE FROM benefits WHERE periodo_mes = %s", (periodo,))
    ben_del = cur.rowcount
    conn.commit()
    cur.close()
    log.info(f"  Periodo {periodo}: {ben_del:,} benefits y {pay_del:,} payments eliminados")


def db_summary(conn):
    cur = conn.cursor()
    tables = [
        ("secretarias", "SELECT COUNT(*) FROM secretarias"),
        ("programs", "SELECT COUNT(*) FROM programs"),
        ("beneficiaries", "SELECT COUNT(*) FROM beneficiaries"),
        ("benefits", "SELECT COUNT(*) FROM benefits"),
        ("payments", "SELECT COUNT(*) FROM payments"),
        ("incompatibility_rules", "SELECT COUNT(*) FROM incompatibility_rules"),
        ("users", "SELECT COUNT(*) FROM users"),
    ]
    log.info("\n  Estado actual de la base de datos:")
    log.info(f"  {'Tabla':<25} {'Registros':>12}")
    log.info(f"  {'-'*25} {'-'*12}")
    for name, sql in tables:
        cur.execute(sql)
        count = cur.fetchone()[0]
        log.info(f"  {name:<25} {count:>12,}")

    cur.execute("""
        SELECT periodo_mes, COUNT(*) FROM benefits
        GROUP BY periodo_mes ORDER BY periodo_mes
    """)
    periodos = cur.fetchall()
    if periodos:
        log.info(f"\n  Periodos ({len(periodos)}):")
        for p, c in periodos:
            log.info(f"    {p}: {c:,} benefits")
    cur.close()


# -------------------------------------------------------------
# Datasets configurados (desde ingest_config.py)
# -------------------------------------------------------------

def _make_loader(config):
    """Genera un loader(conn) a partir de un config dict."""
    def loader(conn):
        t0 = time.time()
        paths = sorted(glob.glob(os.path.join(DATASETS_ROOT, config["path"])))
        if not paths:
            log.warning(f"  No se encontraron archivos: {config['path']}")
            return
        log.info(f"  Archivos encontrados: {len(paths)}")
        grand_totals = _empty_totals()
        for csv_path in paths:
            totals = ingest_csv(conn, csv_path, config)
            for k in grand_totals:
                grand_totals[k] += totals.get(k, 0)

        elapsed = time.time() - t0
        log_report(log, config["name"], grand_totals, elapsed, _log_path)
    return loader


# -------------------------------------------------------------
# Loader custom: Alimentar (cruce menores + titulares)
# -------------------------------------------------------------

def _clean_monto_expr(col_name):
    """Expresion Polars para limpiar monto con deteccion automatica de formato.
    punto+coma=argentino, solo coma=decimal, solo punto=estandar."""
    raw = pl.col(col_name).cast(pl.Utf8).str.replace_all(r"[^0-9,.\-]", "")
    hc = raw.str.contains(",")
    hd = raw.str.contains(r"\.")
    return (
        pl.when(hc & hd).then(raw.str.replace_all(r"\.", "").str.replace(",", "."))
          .when(hc).then(raw.str.replace(",", "."))
          .otherwise(raw)
    )


def _copy_csv_to_temp(conn, csv_path, table_name, columns, separator=",",
                      select_exprs=None):
    """Lee CSV en chunks con Polars, COPY a tabla temporal de PG via StringIO."""
    cur = conn.cursor()
    col_defs = ", ".join(f"{c} text" for c in columns)
    cur.execute(f"DROP TABLE IF EXISTS {table_name}")
    cur.execute(f"CREATE TEMP TABLE {table_name} ({col_defs})")

    reader = pl.read_csv_batched(
        csv_path, separator=separator,
        infer_schema_length=0, encoding="utf8-lossy",
        batch_size=CHUNK_SIZE
    )
    total = 0
    while True:
        batches = reader.next_batches(1)
        if batches is None:
            break
        chunk = batches[0]
        if len(chunk) == 0:
            break
        if select_exprs:
            chunk = chunk.select(select_exprs)
        else:
            chunk = chunk.select(columns)
        buf = io.StringIO()
        chunk.write_csv(buf, include_header=False)
        buf.seek(0)
        cur.copy_expert(f"COPY {table_name} FROM STDIN CSV", buf)
        total += len(chunk)

    conn.commit()
    cur.close()
    return total


def load_alimentar(conn):
    """
    Ingesta Alimentar: cruza menores con titulares para prorratear monto.
    Usa tablas temporales de PostgreSQL para el JOIN pesado (~100MB RAM Python).

    Flujo:
      1. COPY menores y titulares a temp tables de PG (chunks via StringIO)
      2. CREATE INDEX + counts en PG
      3. Server-side cursor: JOIN menores+titulares+counts → fetch → clean → write
      4. Prenatal: titulares con prenatal=1 como beneficiarios (cursor separado)
      5. DROP temp tables
    """
    alim_config = next(c for c in DATASET_CONFIGS if c.get("loader") == "load_alimentar")
    col_map = alim_config["col_map"]
    defaults = alim_config.get("defaults", {})

    menores_path = os.path.join(DATASETS_ROOT, alim_config["path"])
    titulares_path = os.path.join(DATASETS_ROOT, alim_config["titulares_path"])

    if not os.path.exists(menores_path):
        log.warning(f"  No se encontro archivo de menores: {menores_path}")
        return
    if not os.path.exists(titulares_path):
        log.warning(f"  No se encontro archivo de titulares: {titulares_path}")
        return

    t0 = time.time()
    log.info(f"\n{'-'*55}")
    log.info(f"  ALIMENTAR -- Cruce menores + titulares (PG temp tables)")
    log.info(f"  Menores:   {menores_path}")
    log.info(f"  Titulares: {titulares_path}")
    log.info(f"{'-'*55}")

    prog = defaults.get("programa", "Tarjeta Alimentar")
    sec = defaults.get("secretaria", "Secretaria de Inclusion Social")

    cache = IngestCache(conn)
    _ensure_program(conn, cache, prog, sec)

    cur = conn.cursor()

    try:
        # ── Fase 1: COPY a temp tables ──
        log.info("  Fase 1: cargando menores a tabla temporal...")
        menores_cols = ["cuil_beneficiario", "cuil_titular",
                        "apellido_nombre_beneficiario",
                        "fecha_nacimiento_beneficiario",
                        "sexo_beneficiario", "periodo"]
        n_men = _copy_csv_to_temp(conn, menores_path, "_alim_menores",
                                  menores_cols, separator=",")
        log.info(f"    {n_men:,} filas copiadas ({time.time()-t0:.0f}s)")

        log.info("  Fase 1: cargando titulares a tabla temporal...")
        tit_cols = ["cuil_titular", "apellido_nombre_titular",
                    "fecha_nacimiento_titular", "sexo_titular",
                    "provincia_titular", "prenatal", "monto_titular", "periodo"]
        tit_select = [
            pl.col("cuil_titular").cast(pl.Utf8).str.strip_chars(),
            pl.col("apellido_nombre_titular"),
            pl.col("fecha_nacimiento_titular"),
            pl.col("sexo_titular"),
            pl.col("provincia_titular").cast(pl.Utf8).str.strip_chars(),
            pl.col("prenatal").cast(pl.Utf8).str.strip_chars(),
            _clean_monto_expr("monto_titular").alias("monto_titular"),
            pl.col("periodo").cast(pl.Utf8).str.strip_chars(),
        ]
        n_tit = _copy_csv_to_temp(conn, titulares_path, "_alim_titulares",
                                  tit_cols, separator=";",
                                  select_exprs=tit_select)
        log.info(f"    {n_tit:,} filas copiadas ({time.time()-t0:.0f}s)")

        # ── Fase 2: Indexes + counts ──
        log.info("  Fase 2: creando indices y contando hijos...")
        cur.execute("CREATE INDEX ON _alim_menores(cuil_titular, periodo)")
        cur.execute("CREATE INDEX ON _alim_titulares(cuil_titular, periodo)")
        cur.execute("""
            CREATE TEMP TABLE _alim_counts AS
            SELECT cuil_titular, periodo, COUNT(*)::int AS n
            FROM _alim_menores
            GROUP BY cuil_titular, periodo
        """)
        cur.execute("CREATE INDEX ON _alim_counts(cuil_titular, periodo)")
        conn.commit()
        log.info(f"    Indices y counts creados ({time.time()-t0:.0f}s)")

        # ── Fase 3: Fetch menores enriquecidos (server-side cursor) ──
        log.info("  Fase 3: procesando menores (server-side cursor)...")
        if MAX_ROWS > 0:
            log.info(f"  *** MODO TEST: limite de {MAX_ROWS:,} filas ***")

        totals = _empty_totals()
        chunk_num = 0

        srv_cur = conn.cursor(name="alim_menores_cur")
        srv_cur.itersize = CHUNK_SIZE
        srv_cur.execute("""
            SELECT m.cuil_beneficiario,
                   m.apellido_nombre_beneficiario,
                   m.fecha_nacimiento_beneficiario,
                   m.sexo_beneficiario,
                   m.periodo,
                   COALESCE(t.provincia_titular, ''),
                   COALESCE(
                       ROUND(t.monto_titular::numeric
                             / NULLIF(c.n + COALESCE(t.prenatal::int, 0), 0), 2),
                       0
                   )::text
            FROM _alim_menores m
            LEFT JOIN _alim_titulares t
                ON m.cuil_titular = t.cuil_titular AND m.periodo = t.periodo
            LEFT JOIN _alim_counts c
                ON t.cuil_titular = c.cuil_titular AND t.periodo = c.periodo
        """)

        while True:
            rows = srv_cur.fetchmany(CHUNK_SIZE)
            if not rows:
                break
            chunk_num += 1

            if MAX_ROWS > 0:
                remaining = MAX_ROWS - totals["rows"]
                if remaining <= 0:
                    break
                rows = rows[:remaining]

            df = pl.DataFrame({
                "cuil": [r[0] for r in rows],
                "apellido_nombre": [r[1] for r in rows],
                "fecha_nacimiento": [r[2] for r in rows],
                "sexo": [r[3] for r in rows],
                "periodo": [r[4] for r in rows],
                "provincia": [r[5] for r in rows],
                "monto": [r[6] for r in rows],
            })

            df = _split_apellido_nombre(df, "apellido_nombre")
            df = df.select([
                "cuil",
                pl.col("_nombre").alias("nombre"),
                pl.col("_apellido").alias("apellido"),
                "sexo", "fecha_nacimiento", "provincia", "monto", "periodo",
            ])

            clean = clean_chunk(df, col_map, defaults)
            stats = write_chunk(conn, cache, clean, prog, sec)
            for k in totals:
                totals[k] += stats.get(k, 0)

            elapsed = time.time() - t0
            rate = totals["rows"] / elapsed if elapsed > 0 else 0
            log.info(f"  Chunk {chunk_num}: {totals['rows']:,} filas [{rate:,.0f}/s]"
                     f"  ben={totals['ben_new']:,} benf={totals['benefits_new']:,}"
                     f" pay={totals['pay_new']:,}")

        srv_cur.close()

        # ── Fase 4: Prenatal (titulares con prenatal=1 como beneficiarios) ──
        log.info("  Fase 4: procesando titulares con prenatal...")
        srv_cur2 = conn.cursor(name="alim_prenatal_cur")
        srv_cur2.itersize = CHUNK_SIZE
        srv_cur2.execute("""
            SELECT t.cuil_titular,
                   t.apellido_nombre_titular,
                   t.fecha_nacimiento_titular,
                   t.sexo_titular,
                   t.provincia_titular,
                   ROUND(t.monto_titular::numeric
                         / NULLIF(c.n + 1, 0), 2)::text,
                   t.periodo
            FROM _alim_titulares t
            JOIN _alim_counts c
                ON t.cuil_titular = c.cuil_titular AND t.periodo = c.periodo
            WHERE t.prenatal::int = 1
        """)

        n_prenatal = 0
        while True:
            rows = srv_cur2.fetchmany(CHUNK_SIZE)
            if not rows:
                break
            n_prenatal += len(rows)

            df = pl.DataFrame({
                "cuil": [r[0] for r in rows],
                "apellido_nombre": [r[1] for r in rows],
                "fecha_nacimiento": [r[2] for r in rows],
                "sexo": [r[3] for r in rows],
                "provincia": [r[4] for r in rows],
                "monto": [r[5] for r in rows],
                "periodo": [r[6] for r in rows],
            })

            df = _split_apellido_nombre(df, "apellido_nombre")
            df = df.select([
                "cuil",
                pl.col("_nombre").alias("nombre"),
                pl.col("_apellido").alias("apellido"),
                "sexo", "fecha_nacimiento", "provincia", "monto", "periodo",
            ])

            clean = clean_chunk(df, col_map, defaults)
            stats = write_chunk(conn, cache, clean, prog, sec)
            for k in totals:
                totals[k] += stats.get(k, 0)

        srv_cur2.close()
        if n_prenatal:
            log.info(f"    Prenatal: {n_prenatal:,} titulares procesados")

    finally:
        # ── Cleanup: drop temp tables ──
        for tbl in ("_alim_counts", "_alim_titulares", "_alim_menores"):
            try:
                cur.execute(f"DROP TABLE IF EXISTS {tbl}")
            except Exception:
                pass
        try:
            conn.commit()
        except Exception:
            pass
        cur.close()

    # -- Resumen final --
    elapsed = time.time() - t0
    log.info(f"\n  Ingesta ALIMENTAR completada en {elapsed:.1f}s")
    log.info(f"  {'Filas procesadas:':<30} {totals['rows']:>10,}")
    log.info(f"  {'Beneficiarios nuevos:':<30} {totals['ben_new']:>10,}")
    log.info(f"  {'Benefits nuevos:':<30} {totals['benefits_new']:>10,}")
    log.info(f"  {'Benefits omitidos (dup):':<30} {totals['benefits_skip']:>10,}")
    log.info(f"  {'Pagos nuevos:':<30} {totals['pay_new']:>10,}")
    log.info(f"  {'Pagos omitidos (dup):':<30} {totals['pay_skip']:>10,}")
    log.info(f"  {'CUIL invalidos:':<30} {totals['cuil_invalido']:>10,}")
    log.info(f"  {'Errores:':<30} {totals['errores']:>10,}")

    log_report(log, "ALIMENTAR", totals, elapsed, _log_path)


LOADER_REGISTRY = {
    "load_alimentar": load_alimentar,
}


DATASETS = []
for _c in DATASET_CONFIGS:
    if "loader" in _c:
        DATASETS.append((_c["name"], _c["description"], LOADER_REGISTRY[_c["loader"]]))
    else:
        DATASETS.append((_c["name"], _c["description"], _make_loader(_c)))


# -------------------------------------------------------------
# Menú interactivo
# -------------------------------------------------------------

_log_path = None  # se setea en menu()


def menu():
    global log, _log_path
    log, _log_path = setup_logger()

    conn = get_conn()
    db_url_display = DATABASE_URL.split("@")[-1] if "@" in DATABASE_URL else DATABASE_URL
    log.info(f"\n{'='*55}")
    log.info(f"  RUB — Ingesta de Datasets")
    log.info(f"  DB: {db_url_display}")
    log.info(f"  Log: {_log_path}")
    log.info(f"{'='*55}")

    while True:
        print(f"\n  Opciones:")
        print(f"    [1] Ver estado de la base de datos")
        print(f"    [2] Cargar dataset")
        print(f"    [3] Limpiar periodo específico")
        print(f"    [4] Limpiar TODAS las tablas de datos")
        print(f"    [0] Salir")

        opcion = input("\n  Seleccionar opción: ").strip()

        if opcion == "1":
            db_summary(conn)

        elif opcion == "2":
            if not DATASETS:
                print("\n  No hay datasets configurados aún.")
                print("  Agregá configs en ingest_config.py")
                continue
            print("\n  Datasets disponibles:")
            for i, (name, desc, _) in enumerate(DATASETS, 1):
                print(f"    [{i}] {name} — {desc}")
            if len(DATASETS) > 1:
                print(f"    [T] Cargar TODOS")
            print(f"    [0] Volver")
            sel = input("\n  Seleccionar dataset: ").strip()
            if sel == "0" or not sel:
                continue
            if sel.upper() == "T":
                confirm = input(f"  Cargar los {len(DATASETS)} datasets? (s/n): ").strip()
                if confirm.lower() == "s":
                    for name, desc, loader in DATASETS:
                        log.info(f"\n  Cargando: {name}...")
                        loader(conn)
                        log.info(f"  Carga de {name} completada.")
                    log.info(f"\n  Todos los datasets cargados.")
                continue
            try:
                idx = int(sel) - 1
                if 0 <= idx < len(DATASETS):
                    name, desc, loader = DATASETS[idx]
                    log.info(f"\n  Cargando: {name}...")
                    loader(conn)
                    log.info(f"  Carga de {name} completada.")
                else:
                    print("  Opción inválida.")
            except (ValueError, IndexError):
                print("  Opción inválida.")

        elif opcion == "3":
            periodo = input("  Periodo a limpiar (YYYY-MM): ").strip()
            if periodo:
                confirm = input(f"  Confirmar borrado de {periodo}? (s/n): ").strip()
                if confirm.lower() == "s":
                    truncate_periodo(conn, periodo)

        elif opcion == "4":
            confirm = input("  ATENCIÓN: Esto borra TODOS los datos. Confirmar? (s/n): ").strip()
            if confirm.lower() == "s":
                truncate_data(conn)

        elif opcion == "0":
            break
        else:
            print("  Opción inválida.")

    conn.close()
    log.info("\n  Hasta luego.\n")


if __name__ == "__main__":
    menu()
