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

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost/rub")
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
            provincia TEXT, departamento TEXT,
            cant_prestaciones TEXT, cant_beneficios INTEGER,
            monto_total NUMERIC(14,2))""",
     ["CREATE INDEX ON {name}(periodo_mes, apellido, nombre)",
      "CREATE INDEX ON {name}(periodo_mes, provincia)",
      "CREATE INDEX ON {name}(periodo_mes, sexo)",
      "CREATE INDEX ON {name}(periodo_mes, cant_prestaciones)",
      "CREATE INDEX ON {name}(cuil)"]),
]

# ─── SQL compartido ──────────────────────────────────────

# Temp table per-person: single scan → ambas MVs
# Params: [period, period, period_date, period]
TEMP_TABLE_SQL = """
CREATE TEMP TABLE _pp ON COMMIT DROP AS
WITH base AS (
    SELECT COALESCE(b.beneficiary_id, -b.id) AS beneficiary_id, b.periodo_mes,
           p.nombre_programa, p.secretaria_origen,
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
           COALESCE(pa.total_monto, 0) AS monto
    FROM benefits b
    JOIN programs p ON b.program_id = p.id
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
    CROSS JOIN LATERAL (
        SELECT EXTRACT(YEAR FROM AGE(%s::date, ben.fecha_nacimiento))::int AS edad
    ) calc
    WHERE b.periodo_mes = %s
)
SELECT periodo_mes, nombre_programa, secretaria_origen,
       provincia, sexo, grupo_etario,
       CASE WHEN cant_prog = 1 THEN '1' WHEN cant_prog = 2 THEN '2' ELSE '3+' END AS cant_prestaciones,
       beneficiary_id, COUNT(*) AS cant_benefits, SUM(monto) AS person_monto
FROM base
GROUP BY periodo_mes, nombre_programa, secretaria_origen,
         provincia, sexo, grupo_etario,
         CASE WHEN cant_prog = 1 THEN '1' WHEN cant_prog = 2 THEN '2' ELSE '3+' END,
         beneficiary_id
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
           SUM(person_monto) AS monto_total
    FROM _pp
    WHERE beneficiary_id > 0
    GROUP BY periodo_mes, beneficiary_id, provincia, sexo, grupo_etario,
             cant_prestaciones
)
SELECT pa.periodo_mes, pa.beneficiary_id,
       ben.cuil, ben.nombre, ben.apellido,
       pa.sexo,
       EXTRACT(YEAR FROM AGE(({period_date})::date, ben.fecha_nacimiento))::int AS edad,
       pa.grupo_etario, pa.provincia, ben.departamento,
       pa.cant_prestaciones, pa.cant_beneficios, pa.monto_total
FROM person_agg pa
JOIN beneficiaries ben ON ben.id = pa.beneficiary_id
"""


# ─── Funciones compartidas ───────────────────────────────

def _apply_tuning(cur):
    for sql in ["SET work_mem = '4GB'", "SET maintenance_work_mem = '4GB'",
                "SET max_parallel_workers_per_gather = 2", "SET effective_cache_size = '16GB'",
                "SET random_page_cost = '1.1'", "SET effective_io_concurrency = '0'",
                "SET temp_buffers = '256MB'"]:
        cur.execute(sql)


def _process_period(cur, conn, period, cross_table="mv_cross", resumen_table="mv_resumen",
                    nominal_table="mv_nominal"):
    """Procesa un periodo: temp table -> insert en las 3 MVs."""
    period_date = f"{period[:4]}-{period[5:]}-01"
    cur.execute(TEMP_TABLE_SQL, [period, period, period_date, period])
    cur.execute(INSERT_CROSS.format(table=cross_table))
    cross_rows = cur.rowcount
    cur.execute(INSERT_RESUMEN.format(table=resumen_table))
    resumen_rows = cur.rowcount
    cur.execute(INSERT_NOMINAL.format(table=nominal_table, period_date=f"'{period_date}'"))
    nominal_rows = cur.rowcount
    conn.commit()
    return cross_rows, resumen_rows, nominal_rows


def _process_all_periods(cur, conn, cross_table="mv_cross", resumen_table="mv_resumen",
                         nominal_table="mv_nominal"):
    """Procesa todos los periodos con progress bar."""
    cur.execute("SELECT DISTINCT periodo_mes FROM benefits ORDER BY 1")
    periods = [r[0] for r in cur.fetchall()]
    print(f"\nPeriodos: {len(periods)}")

    total_cross, total_resumen, total_nominal = 0, 0, 0
    for i, p in enumerate(periods, 1):
        t_p = time.time()
        cr, rr, nr = _process_period(cur, conn, p, cross_table, resumen_table, nominal_table)
        total_cross += cr
        total_resumen += rr
        total_nominal += nr
        bar = "█" * int(i / len(periods) * 30) + "░" * (30 - int(i / len(periods) * 30))
        print(f"  {bar} {i}/{len(periods)} | {p} | cross:{cr:>7,} res:{rr:>6,} nom:{nr:>9,} | {time.time()-t_p:.1f}s")

    print(f"\n  {cross_table}: {total_cross:,} rows")
    print(f"  {resumen_table}: {total_resumen:,} rows")
    print(f"  {nominal_table}: {total_nominal:,} rows")
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
    tables = ["mv_cross", "mv_resumen", "mv_nominal", "periods", "provincias_lookup"]
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
    for name in ["mv_cross", "mv_resumen", "mv_nominal",
                  "mv_cross_new", "mv_resumen_new", "mv_nominal_new"]:
        cur.execute(f"DROP TABLE IF EXISTS {name} CASCADE")
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
    _process_all_periods(cur, conn)

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
    _process_all_periods(cur, conn, "mv_cross_new", "mv_resumen_new", "mv_nominal_new")

    # Indexes en shadow tables
    for name, _, indexes in TABLES:
        _set_logged_and_index(cur, conn, f"{name}_new", indexes)

    # Atomic swap
    print("\n  Swap atomico...", end=" ", flush=True)
    t_swap = time.time()
    for name in ["mv_cross", "mv_resumen", "mv_nominal"]:
        cur.execute(f"DROP TABLE IF EXISTS {name}_old")
        cur.execute(f"ALTER TABLE {name} RENAME TO {name}_old")
        cur.execute(f"ALTER TABLE {name}_new RENAME TO {name}")
        cur.execute(f"DROP TABLE IF EXISTS {name}_old")
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
    cur.execute("""SELECT COUNT(*) FROM information_schema.tables
                   WHERE table_schema='public' AND table_name IN ('mv_cross','mv_resumen','mv_nominal')""")
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
