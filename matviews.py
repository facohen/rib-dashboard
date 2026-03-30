"""
matviews.py — Genera tablas materializadas mv_cross + mv_resumen.

Detecta automaticamente si crear desde cero o refrescar (blue/green swap).

Uso:
    python matviews.py           # auto-detecta create vs refresh
    python matviews.py create    # forzar creacion desde cero
    python matviews.py refresh   # forzar blue/green refresh
"""
import os
import sys
import time
import psycopg2

from config import load_dotenv
load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL no está seteada. Exportala antes de iniciar:\n"
        "  export DATABASE_URL=postgresql://user:pass@host/rib_dev"
    )
APP_URL = os.environ.get("APP_URL", "http://localhost:5000")


# ─── Definicion de tablas ────────────────────────────────

TABLES = [
    ("mv_cross",
     """CREATE UNLOGGED TABLE {name} (
            periodo_mes TEXT, nombre_programa TEXT, secretaria_origen TEXT,
            provincia TEXT, sexo TEXT, grupo_etario TEXT, cant_prestaciones TEXT,
            personas BIGINT, beneficios BIGINT, montos NUMERIC(14,2))""",
     ["CREATE UNIQUE INDEX ON {name}(periodo_mes, nombre_programa, secretaria_origen, provincia, sexo, grupo_etario, cant_prestaciones)",
      "CREATE INDEX ON {name}(periodo_mes) INCLUDE (personas, beneficios, montos)",
      "CREATE INDEX ON {name}(periodo_mes, nombre_programa) INCLUDE (personas, beneficios, montos)",
      "CREATE INDEX ON {name}(periodo_mes, provincia) INCLUDE (personas, beneficios, montos)",
      "CREATE INDEX ON {name}(periodo_mes, secretaria_origen) INCLUDE (personas, beneficios, montos)",
      "CREATE INDEX ON {name}(periodo_mes, cant_prestaciones) INCLUDE (personas, beneficios, montos)"]),

    ("mv_resumen",
     """CREATE UNLOGGED TABLE {name} (
            periodo_mes TEXT, provincia TEXT, sexo TEXT,
            grupo_etario TEXT, cant_prestaciones TEXT,
            personas BIGINT, beneficios BIGINT, montos NUMERIC(14,2))""",
     ["CREATE UNIQUE INDEX ON {name}(periodo_mes, provincia, sexo, grupo_etario, cant_prestaciones)",
      "CREATE INDEX ON {name}(periodo_mes) INCLUDE (personas, beneficios, montos)"]),

    ("mv_nominal",
     """CREATE UNLOGGED TABLE {name} (
            periodo_mes TEXT, beneficiary_id INTEGER,
            cuil TEXT, nombre TEXT, apellido TEXT,
            sexo TEXT, edad INTEGER, grupo_etario TEXT,
            provincia TEXT,
            cant_prestaciones TEXT, cant_beneficios INTEGER,
            monto_total NUMERIC(14,2),
            program_ids INTEGER[], active_program_ids INTEGER[])""",
     ["CREATE INDEX ON {name}(periodo_mes, apellido, nombre)",
      "CREATE INDEX ON {name}(periodo_mes, provincia)",
      "CREATE INDEX ON {name}(periodo_mes, sexo)",
      "CREATE INDEX ON {name}(periodo_mes, cant_prestaciones)",
      "CREATE INDEX ON {name}(cuil)",
      "CREATE INDEX ON {name} USING gin(program_ids)",
      "CREATE INDEX ON {name} USING gin(active_program_ids)"]),

    ("mv_cross_tc",
     """CREATE UNLOGGED TABLE {name} (
            periodo_mes TEXT, nombre_programa TEXT, secretaria_origen TEXT,
            provincia TEXT, sexo TEXT, grupo_etario TEXT, cant_prestaciones TEXT,
            personas BIGINT, beneficios BIGINT, montos NUMERIC(14,2))""",
     ["CREATE UNIQUE INDEX ON {name}(periodo_mes, nombre_programa, secretaria_origen, provincia, sexo, grupo_etario, cant_prestaciones)",
      "CREATE INDEX ON {name}(periodo_mes) INCLUDE (personas, beneficios, montos)",
      "CREATE INDEX ON {name}(periodo_mes, nombre_programa) INCLUDE (personas, beneficios, montos)"]),

    ("mv_resumen_tc",
     """CREATE UNLOGGED TABLE {name} (
            periodo_mes TEXT, provincia TEXT, sexo TEXT,
            grupo_etario TEXT, cant_prestaciones TEXT,
            personas BIGINT, beneficios BIGINT, montos NUMERIC(14,2))""",
     ["CREATE UNIQUE INDEX ON {name}(periodo_mes, provincia, sexo, grupo_etario, cant_prestaciones)",
      "CREATE INDEX ON {name}(periodo_mes) INCLUDE (personas, beneficios, montos)"]),

    ("mv_nominal_tc",
     """CREATE UNLOGGED TABLE {name} (
            periodo_mes TEXT, cuil_titular TEXT,
            nombre_titular TEXT, apellido_titular TEXT,
            sexo_titular TEXT, edad_titular INT,
            grupo_etario_titular TEXT, provincia_titular TEXT,
            cant_td INT, cuils_td TEXT[],
            cant_beneficios INT, monto_total NUMERIC(14,2),
            program_ids INT[], active_program_ids INT[])""",
     ["CREATE INDEX ON {name}(periodo_mes)",
      "CREATE INDEX ON {name}(periodo_mes, cuil_titular)"]),
]

# ─── SQL compartido ──────────────────────────────────────

# Temp table per-person: single scan → all MVs (TD + TC)
# Params: [period, period, period, period_date, period_date, period]
TEMP_TABLE_SQL = """
CREATE TEMP TABLE _pp ON COMMIT DROP AS
WITH base AS (
    SELECT COALESCE(b.beneficiary_id, -b.id) AS beneficiary_id, b.periodo_mes,
           p.id AS program_id, b.estado_beneficio,
           p.nombre_programa, s.nombre AS secretaria_origen,
           COALESCE(ben.provincia, 'Sin dato') AS provincia,
           COALESCE(ben.sexo, 'Sin dato') AS sexo,
           CASE
             WHEN ben.fecha_nacimiento IS NULL THEN 'Sin dato'
             WHEN calc.edad <= 4  THEN '0-4 años'
             WHEN calc.edad <= 12 THEN '5-12 años'
             WHEN calc.edad <= 17 THEN '13-17 años'
             WHEN calc.edad <= 29 THEN '18-29 años'
             WHEN calc.edad <= 59 THEN '30-59 años'
             ELSE '60+ años'
           END AS grupo_etario,
           COALESCE(bpc.cant_prog, 1) AS cant_prog,
           COALESCE(pa.total_monto, 0) AS monto,
           b.cuil_titular,
           COALESCE(ben.cuil, b.cuil_raw) AS cuil_td,
           b.nombre_titular, b.apellido_titular,
           COALESCE(b.provincia_titular, 'Sin dato') AS provincia_titular,
           COALESCE(b.sexo_titular, 'NI') AS sexo_titular,
           CASE
             WHEN b.fecha_nacimiento_titular IS NULL THEN 'Sin dato'
             WHEN calc_tc.edad_tc <= 4  THEN '0-4 años'
             WHEN calc_tc.edad_tc <= 12 THEN '5-12 años'
             WHEN calc_tc.edad_tc <= 17 THEN '13-17 años'
             WHEN calc_tc.edad_tc <= 29 THEN '18-29 años'
             WHEN calc_tc.edad_tc <= 59 THEN '30-59 años'
             ELSE '60+ años'
           END AS grupo_etario_titular,
           calc_tc.edad_tc AS edad_titular,
           COALESCE(bpc_tc.cant_prog_tc, 1) AS cant_prog_tc
    FROM benefits b
    JOIN programs p ON b.program_id = p.id
    JOIN secretarias s ON p.secretaria_id = s.id
    LEFT JOIN beneficiaries ben ON b.beneficiary_id = ben.id
    LEFT JOIN (
        SELECT beneficiary_id, COUNT(DISTINCT program_id) AS cant_prog
        FROM benefits WHERE periodo_mes = %s AND beneficiary_id IS NOT NULL
        GROUP BY beneficiary_id
    ) bpc ON bpc.beneficiary_id = b.beneficiary_id
    LEFT JOIN (
        SELECT beneficiary_id, program_id, periodo_mes, SUM(monto_prestacion) AS total_monto
        FROM payments WHERE periodo_mes = %s
        GROUP BY beneficiary_id, program_id, periodo_mes
    ) pa ON pa.beneficiary_id = b.beneficiary_id
        AND pa.program_id = b.program_id AND pa.periodo_mes = b.periodo_mes
    LEFT JOIN (
        SELECT cuil_titular, COUNT(DISTINCT program_id) AS cant_prog_tc
        FROM benefits WHERE periodo_mes = %s AND cuil_titular IS NOT NULL
        GROUP BY cuil_titular
    ) bpc_tc ON bpc_tc.cuil_titular = b.cuil_titular
    CROSS JOIN LATERAL (
        SELECT EXTRACT(YEAR FROM AGE(%s::date, ben.fecha_nacimiento))::int AS edad
    ) calc
    CROSS JOIN LATERAL (
        SELECT EXTRACT(YEAR FROM AGE(%s::date, b.fecha_nacimiento_titular))::int AS edad_tc
    ) calc_tc
    WHERE b.periodo_mes = %s
)
SELECT periodo_mes, program_id, estado_beneficio, nombre_programa, secretaria_origen,
       provincia, sexo, grupo_etario,
       CASE WHEN cant_prog = 1 THEN '1' WHEN cant_prog = 2 THEN '2' ELSE '3+' END AS cant_prestaciones,
       beneficiary_id, COUNT(*) AS cant_benefits, SUM(monto) AS person_monto,
       cuil_titular, cuil_td, nombre_titular, apellido_titular,
       provincia_titular, sexo_titular,
       grupo_etario_titular, edad_titular,
       CASE WHEN cant_prog_tc = 1 THEN '1' WHEN cant_prog_tc = 2 THEN '2' ELSE '3+' END AS cant_prestaciones_tc
FROM base
GROUP BY periodo_mes, program_id, estado_beneficio, nombre_programa, secretaria_origen,
         provincia, sexo, grupo_etario,
         CASE WHEN cant_prog = 1 THEN '1' WHEN cant_prog = 2 THEN '2' ELSE '3+' END,
         beneficiary_id,
         cuil_titular, cuil_td, nombre_titular, apellido_titular,
         provincia_titular, sexo_titular,
         grupo_etario_titular, edad_titular,
         CASE WHEN cant_prog_tc = 1 THEN '1' WHEN cant_prog_tc = 2 THEN '2' ELSE '3+' END
"""

INSERT_CROSS = """
INSERT INTO {table}
SELECT periodo_mes, nombre_programa, secretaria_origen,
       provincia, sexo, grupo_etario, cant_prestaciones,
       COUNT(*), SUM(cant_benefits), SUM(person_monto)
FROM _pp
GROUP BY periodo_mes, nombre_programa, secretaria_origen,
         provincia, sexo, grupo_etario, cant_prestaciones
"""

INSERT_RESUMEN = """
INSERT INTO {table}
WITH person_agg AS (
    SELECT periodo_mes, provincia, sexo, grupo_etario, cant_prestaciones,
           beneficiary_id, SUM(cant_benefits) AS cb, SUM(person_monto) AS pm
    FROM _pp
    GROUP BY periodo_mes, provincia, sexo, grupo_etario, cant_prestaciones, beneficiary_id
)
SELECT periodo_mes, provincia, sexo, grupo_etario, cant_prestaciones,
       COUNT(*), SUM(cb), SUM(pm)
FROM person_agg
GROUP BY periodo_mes, provincia, sexo, grupo_etario, cant_prestaciones
"""


INSERT_NOMINAL = """
INSERT INTO {table}
WITH person_agg AS (
    SELECT periodo_mes, beneficiary_id, provincia, sexo, grupo_etario,
           cant_prestaciones,
           SUM(cant_benefits) AS cant_beneficios,
           SUM(person_monto) AS monto_total,
           array_agg(DISTINCT program_id) AS program_ids,
           array_agg(DISTINCT program_id) FILTER (WHERE estado_beneficio = 'ACTIVO') AS active_program_ids
    FROM _pp
    GROUP BY periodo_mes, beneficiary_id, provincia, sexo, grupo_etario,
             cant_prestaciones
)
SELECT pa.periodo_mes, pa.beneficiary_id,
       COALESCE(ben.cuil, 'SIN CUIL') AS cuil,
       COALESCE(ben.nombre, 'No identificado') AS nombre,
       COALESCE(ben.apellido, 'No identificado') AS apellido,
       pa.sexo,
       CASE WHEN ben.id IS NOT NULL
            THEN EXTRACT(YEAR FROM AGE(({period_date})::date, ben.fecha_nacimiento))::int
            ELSE NULL END AS edad,
       pa.grupo_etario, pa.provincia,
       pa.cant_prestaciones, pa.cant_beneficios, pa.monto_total,
       pa.program_ids, COALESCE(pa.active_program_ids, ARRAY[]::int[])
FROM person_agg pa
LEFT JOIN beneficiaries ben ON ben.id = pa.beneficiary_id
"""

INSERT_CROSS_TC = """
INSERT INTO {table}
SELECT periodo_mes, nombre_programa, secretaria_origen,
       provincia_titular, sexo_titular, grupo_etario_titular, cant_prestaciones_tc,
       COUNT(DISTINCT cuil_titular), SUM(cant_benefits), SUM(person_monto)
FROM _pp
WHERE cuil_titular IS NOT NULL
GROUP BY periodo_mes, nombre_programa, secretaria_origen,
         provincia_titular, sexo_titular, grupo_etario_titular, cant_prestaciones_tc
"""

INSERT_RESUMEN_TC = """
INSERT INTO {table}
WITH tc_agg AS (
    SELECT periodo_mes, provincia_titular, sexo_titular, grupo_etario_titular, cant_prestaciones_tc,
           cuil_titular, SUM(cant_benefits) AS cb, SUM(person_monto) AS pm
    FROM _pp WHERE cuil_titular IS NOT NULL
    GROUP BY periodo_mes, provincia_titular, sexo_titular, grupo_etario_titular, cant_prestaciones_tc, cuil_titular
)
SELECT periodo_mes, provincia_titular, sexo_titular, grupo_etario_titular, cant_prestaciones_tc,
       COUNT(*), SUM(cb), SUM(pm)
FROM tc_agg
GROUP BY periodo_mes, provincia_titular, sexo_titular, grupo_etario_titular, cant_prestaciones_tc
"""

INSERT_NOMINAL_TC = """
INSERT INTO {table}
SELECT periodo_mes, cuil_titular,
       MAX(nombre_titular), MAX(apellido_titular),
       MAX(sexo_titular), MAX(edad_titular),
       MAX(grupo_etario_titular), MAX(provincia_titular),
       COUNT(DISTINCT cuil_td),
       ARRAY_AGG(DISTINCT cuil_td),
       SUM(cant_benefits)::int,
       SUM(person_monto),
       ARRAY_AGG(DISTINCT program_id),
       ARRAY_AGG(DISTINCT program_id) FILTER (WHERE estado_beneficio = 'ACTIVO')
FROM _pp
WHERE cuil_titular IS NOT NULL
GROUP BY periodo_mes, cuil_titular
"""


# ─── Funciones compartidas ───────────────────────────────

def _apply_tuning(cur):
    for sql in ["SET work_mem = '1GB'", "SET maintenance_work_mem = '1GB'",
                "SET max_parallel_workers_per_gather = 2", "SET effective_cache_size = '16GB'",
                "SET random_page_cost = '1.1'", "SET effective_io_concurrency = '0'",
                "SET temp_buffers = '256MB'"]:
        cur.execute(sql)


def _process_period(cur, conn, period, cross_table="mv_cross", resumen_table="mv_resumen",
                    nominal_table="mv_nominal",
                    cross_tc_table=None, resumen_tc_table=None, nominal_tc_table=None):
    """Procesa un periodo: temp table -> insert en TD + TC MVs."""
    period_date = f"{period[:4]}-{period[5:]}-01"
    cur.execute(TEMP_TABLE_SQL, [period, period, period, period_date, period_date, period])
    cur.execute(INSERT_CROSS.format(table=cross_table))
    cross_rows = cur.rowcount
    cur.execute(INSERT_RESUMEN.format(table=resumen_table))
    resumen_rows = cur.rowcount
    cur.execute(INSERT_NOMINAL.format(table=nominal_table, period_date=f"'{period_date}'"))
    nominal_rows = cur.rowcount

    cross_tc = resumen_tc = nominal_tc = 0
    if cross_tc_table:
        cur.execute(INSERT_CROSS_TC.format(table=cross_tc_table))
        cross_tc = cur.rowcount
    if resumen_tc_table:
        cur.execute(INSERT_RESUMEN_TC.format(table=resumen_tc_table))
        resumen_tc = cur.rowcount
    if nominal_tc_table:
        cur.execute(INSERT_NOMINAL_TC.format(table=nominal_tc_table))
        nominal_tc = cur.rowcount

    conn.commit()
    return cross_rows, resumen_rows, nominal_rows, cross_tc, resumen_tc, nominal_tc


def _process_all_periods(cur, conn, cross_table="mv_cross", resumen_table="mv_resumen",
                         nominal_table="mv_nominal",
                         cross_tc_table=None, resumen_tc_table=None, nominal_tc_table=None):
    """Procesa todos los periodos con progress bar."""
    cur.execute("SELECT DISTINCT periodo_mes FROM benefits ORDER BY 1")
    periods = [r[0] for r in cur.fetchall()]
    print(f"\nPeriodos: {len(periods)}")

    total_cross, total_resumen, total_nominal = 0, 0, 0
    total_cross_tc, total_resumen_tc, total_nominal_tc = 0, 0, 0
    for i, p in enumerate(periods, 1):
        t_p = time.time()
        cr, rr, nr, ctc, rtc, ntc = _process_period(
            cur, conn, p, cross_table, resumen_table, nominal_table,
            cross_tc_table, resumen_tc_table, nominal_tc_table)
        total_cross += cr
        total_resumen += rr
        total_nominal += nr
        total_cross_tc += ctc
        total_resumen_tc += rtc
        total_nominal_tc += ntc
        bar = "█" * int(i / len(periods) * 30) + "░" * (30 - int(i / len(periods) * 30))
        print(f"  {bar} {i}/{len(periods)} | {p} | cross:{cr:>7,} res:{rr:>6,} nom:{nr:>9,} tc:{ctc:>7,} | {time.time()-t_p:.1f}s")

    print(f"\n  {cross_table}: {total_cross:,} rows")
    print(f"  {resumen_table}: {total_resumen:,} rows")
    print(f"  {nominal_table}: {total_nominal:,} rows")
    if cross_tc_table:
        print(f"  {cross_tc_table}: {total_cross_tc:,} rows")
    if resumen_tc_table:
        print(f"  {resumen_tc_table}: {total_resumen_tc:,} rows")
    if nominal_tc_table:
        print(f"  {nominal_tc_table}: {total_nominal_tc:,} rows")
    return total_cross, total_resumen, total_nominal


def _set_logged_and_index(cur, conn, table_name, indexes):
    """SET LOGGED + crear indices."""
    print(f"\n  SET LOGGED {table_name}...", end=" ", flush=True)
    cur.execute(f"ALTER TABLE {table_name} SET LOGGED")
    conn.commit()
    print("OK")
    for i, idx in enumerate(indexes, 1):
        print(f"  Indice {i}/{len(indexes)} en {table_name}...", end=" ", flush=True)
        cur.execute(idx.format(name=table_name))
        print("OK")
    conn.commit()


def _update_lookups(cur, conn):
    """Actualiza tablas lookup (periods, provincias)."""
    cur.execute("CREATE TABLE IF NOT EXISTS periods (periodo_mes TEXT PRIMARY KEY)")
    cur.execute("TRUNCATE periods")
    cur.execute("INSERT INTO periods SELECT DISTINCT periodo_mes FROM benefits")
    cur.execute("CREATE TABLE IF NOT EXISTS provincias_lookup (provincia TEXT PRIMARY KEY)")
    cur.execute("TRUNCATE provincias_lookup")
    cur.execute("INSERT INTO provincias_lookup SELECT DISTINCT provincia FROM beneficiaries")
    conn.commit()
    print("  Lookups actualizados (periods, provincias_lookup)")


def _analyze_and_report(cur, conn):
    """ANALYZE + mostrar row counts."""
    conn.autocommit = True
    tables = [t[0] for t in TABLES] + ["periods", "provincias_lookup"]
    for t in tables:
        cur.execute(f"ANALYZE {t}")
    print("\n  Resultado:")
    for t in tables:
        cur.execute(f"SELECT COUNT(*) FROM {t}")
        print(f"    {t}: {cur.fetchone()[0]:,} rows")


def _clear_flask_cache():
    """Intenta limpiar cache de Flask si el server esta corriendo."""
    try:
        import requests
        r = requests.post(f"{APP_URL}/api/admin/clear-cache",
                          cookies={"session": "admin"}, timeout=5)
        if r.status_code == 200:
            print("  Cache Flask limpiado")
        else:
            print(f"  Cache Flask: HTTP {r.status_code} (se limpiara por TTL)")
    except Exception:
        print("  Cache Flask: servidor no disponible (se limpiara por TTL)")


# ─── Create: desde cero ─────────────────────────────────

def create():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    cur = conn.cursor()
    t0 = time.time()

    db_display = DATABASE_URL.split('@')[-1] if '@' in DATABASE_URL else DATABASE_URL
    print(f"{'='*60}\nCreando tablas materializadas\nDB: {db_display}\n{'='*60}")

    # Drop existentes + legacy
    for tname, _, _ in TABLES:
        cur.execute(f"DROP TABLE IF EXISTS {tname} CASCADE")
        cur.execute(f"DROP TABLE IF EXISTS {tname}_new CASCADE")
    for name in ["mv_summary", "mv_pagos_summary", "mv_concentracion", "mv_incompatibilidades",
                  "mv_by_programa", "mv_by_secretaria", "mv_by_provincia", "mv_by_sexo",
                  "mv_by_grupo_etario", "mv_evolucion", "mv_cobertura"]:
        cur.execute(f"DROP MATERIALIZED VIEW IF EXISTS {name} CASCADE")
    cur.execute("DROP FUNCTION IF EXISTS refresh_all_matviews()")
    conn.commit()
    print("  Tablas existentes limpiadas")

    _apply_tuning(cur)

    # Crear tablas vacias
    for name, ddl, _ in TABLES:
        cur.execute(ddl.format(name=name))
    conn.commit()

    # Procesar periodos
    _process_all_periods(cur, conn,
                         cross_tc_table="mv_cross_tc",
                         resumen_tc_table="mv_resumen_tc",
                         nominal_tc_table="mv_nominal_tc")

    # Indexes
    for name, _, indexes in TABLES:
        _set_logged_and_index(cur, conn, name, indexes)

    _update_lookups(cur, conn)
    _analyze_and_report(cur, conn)

    elapsed = time.time() - t0
    print(f"\n{'='*60}\nCreacion completada en {int(elapsed//60)}m {elapsed%60:.0f}s\n{'='*60}")
    cur.close()
    conn.close()


# ─── Refresh: blue/green swap ────────────────────────────

def refresh():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    cur = conn.cursor()
    t0 = time.time()

    db_display = DATABASE_URL.split('@')[-1] if '@' in DATABASE_URL else DATABASE_URL
    print(f"{'='*60}\nRefrescando tablas materializadas (blue/green)\nDB: {db_display}\n{'='*60}")

    _apply_tuning(cur)

    # Shadow tables
    for name, ddl, _ in TABLES:
        shadow = f"{name}_new"
        cur.execute(f"DROP TABLE IF EXISTS {shadow}")
        cur.execute(ddl.format(name=shadow))
    conn.commit()

    # Procesar periodos en shadow tables
    _process_all_periods(cur, conn, "mv_cross_new", "mv_resumen_new", "mv_nominal_new",
                         cross_tc_table="mv_cross_tc_new",
                         resumen_tc_table="mv_resumen_tc_new",
                         nominal_tc_table="mv_nominal_tc_new")

    # Indexes en shadow tables
    for name, _, indexes in TABLES:
        _set_logged_and_index(cur, conn, f"{name}_new", indexes)

    # Atomic swap
    print("\n  Swap atomico...", end=" ", flush=True)
    t_swap = time.time()
    for tname, _, _ in TABLES:
        cur.execute(f"DROP TABLE IF EXISTS {tname}_old")
        cur.execute(f"ALTER TABLE IF EXISTS {tname} RENAME TO {tname}_old")
        cur.execute(f"ALTER TABLE {tname}_new RENAME TO {tname}")
        cur.execute(f"DROP TABLE IF EXISTS {tname}_old")
    conn.commit()
    print(f"OK ({time.time()-t_swap:.3f}s)")

    _update_lookups(cur, conn)
    _analyze_and_report(cur, conn)

    elapsed = time.time() - t0
    print(f"\n{'='*60}\nRefresh completado en {int(elapsed//60)}m {elapsed%60:.0f}s\n{'='*60}")
    cur.close()
    conn.close()
    _clear_flask_cache()


# ─── Auto-detect + CLI ───────────────────────────────────

def _tables_exist():
    """Detecta si mv_cross y mv_resumen ya existen."""
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    table_names = ",".join(f"'{t[0]}'" for t in TABLES)
    cur.execute(f"""SELECT COUNT(*) FROM information_schema.tables
                   WHERE table_schema='public' AND table_name IN ({table_names})""")
    n = cur.fetchone()[0]
    cur.close()
    conn.close()
    return n >= 2


def run(mode=None):
    """Ejecuta create o refresh. mode=None auto-detecta."""
    if mode is None:
        mode = "refresh" if _tables_exist() else "create"
    if mode == "create":
        create()
    else:
        refresh()


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else None
    run(mode)
