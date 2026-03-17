"""
create_matviews.py — Crea tablas materializadas mv_cross + mv_resumen para el dashboard.

Se ejecuta DESPUÉS de seed_pg.py (o después de cargar datos reales).

Pipeline de datos:
    python seed_pg.py          # 1. Crear schema + cargar datos
    python create_matviews.py  # 2. Crear + popular tablas materializadas
    python refresh_matviews.py # 3. (mensual) Refrescar tablas después de carga de datos

Uso:
    DATABASE_URL=postgresql://user:pass@host/rub python create_matviews.py
"""
import os
import sys
import time
import psycopg2

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost/rub")

# ──────────────────────────────────────────────
# Table DDL + indexes (no INSERT — see shared temp table below)
# ──────────────────────────────────────────────

TABLES = [
    # ── mv_cross (multidimensional per-program, ~130-180K rows) ──
    (
        "mv_cross",
        """
        CREATE UNLOGGED TABLE mv_cross (
            periodo_mes TEXT, nombre_programa TEXT, secretaria_origen TEXT,
            provincia TEXT, sexo TEXT, grupo_etario TEXT, cant_prestaciones TEXT,
            personas BIGINT, beneficios BIGINT, montos NUMERIC(14,2)
        )
        """,
        None,  # INSERT handled by shared temp table
        [
            "CREATE UNIQUE INDEX ON mv_cross(periodo_mes, nombre_programa, secretaria_origen, provincia, sexo, grupo_etario, cant_prestaciones)",
            "CREATE INDEX ON mv_cross(periodo_mes) INCLUDE (personas, beneficios, montos)",
            "CREATE INDEX ON mv_cross(periodo_mes, nombre_programa) INCLUDE (personas, beneficios, montos)",
            "CREATE INDEX ON mv_cross(periodo_mes, provincia) INCLUDE (personas, beneficios, montos)",
            "CREATE INDEX ON mv_cross(periodo_mes, secretaria_origen) INCLUDE (personas, beneficios, montos)",
            "CREATE INDEX ON mv_cross(periodo_mes, cant_prestaciones) INCLUDE (personas, beneficios, montos)",
        ],
    ),

    # ── mv_resumen (deduplicated persons, ~23K rows) ──
    (
        "mv_resumen",
        """
        CREATE UNLOGGED TABLE mv_resumen (
            periodo_mes TEXT, provincia TEXT, sexo TEXT,
            grupo_etario TEXT, cant_prestaciones TEXT,
            personas BIGINT, beneficios BIGINT, montos NUMERIC(14,2)
        )
        """,
        None,  # INSERT handled by shared temp table
        [
            "CREATE UNIQUE INDEX ON mv_resumen(periodo_mes, provincia, sexo, grupo_etario, cant_prestaciones)",
            "CREATE INDEX ON mv_resumen(periodo_mes) INCLUDE (personas, beneficios, montos)",
        ],
    ),
]

# ──────────────────────────────────────────────
# Shared temp table: one scan → both MVs
# ──────────────────────────────────────────────

# Per-person temp table: single scan of benefits+payments+beneficiaries
# Groups by all 7 dimensions (mv_cross superset), mv_resumen re-aggregates from here
# Includes NULL beneficiary_id rows (non-identified) via LEFT JOINs + COALESCE
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
        FROM benefits
        WHERE periodo_mes = %s AND beneficiary_id IS NOT NULL
        GROUP BY beneficiary_id
    ) bpc ON bpc.beneficiary_id = b.beneficiary_id
    LEFT JOIN (
        SELECT beneficiary_id, program_id, periodo_mes,
               SUM(monto_prestacion) AS total_monto
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
       beneficiary_id,
       COUNT(*) AS cant_benefits,
       SUM(monto) AS person_monto
FROM base
GROUP BY periodo_mes, nombre_programa, secretaria_origen,
         provincia, sexo, grupo_etario,
         CASE WHEN cant_prog = 1 THEN '1' WHEN cant_prog = 2 THEN '2' ELSE '3+' END,
         beneficiary_id
"""

# Aggregate temp table → mv_cross (7 dims, already at right granularity)
INSERT_CROSS_FROM_PP = """
INSERT INTO {table}
SELECT periodo_mes, nombre_programa, secretaria_origen,
       provincia, sexo, grupo_etario, cant_prestaciones,
       COUNT(*) AS personas,
       SUM(cant_benefits) AS beneficios,
       SUM(person_monto) AS montos
FROM _pp
GROUP BY periodo_mes, nombre_programa, secretaria_origen,
         provincia, sexo, grupo_etario, cant_prestaciones
"""

# Re-aggregate temp table → mv_resumen (5 dims, deduplicate persons across programs)
INSERT_RESUMEN_FROM_PP = """
INSERT INTO {table}
WITH person_agg AS (
    SELECT periodo_mes, provincia, sexo, grupo_etario, cant_prestaciones,
           beneficiary_id,
           SUM(cant_benefits) AS cant_benefits,
           SUM(person_monto) AS person_monto
    FROM _pp
    GROUP BY periodo_mes, provincia, sexo, grupo_etario, cant_prestaciones, beneficiary_id
)
SELECT periodo_mes, provincia, sexo, grupo_etario, cant_prestaciones,
       COUNT(*) AS personas,
       SUM(cant_benefits) AS beneficios,
       SUM(person_monto) AS montos
FROM person_agg
GROUP BY periodo_mes, provincia, sexo, grupo_etario, cant_prestaciones
"""

# ── Periods lookup table ──
PERIODS_TABLE = """
CREATE TABLE IF NOT EXISTS periods (periodo_mes TEXT PRIMARY KEY);
INSERT INTO periods SELECT DISTINCT periodo_mes FROM benefits ON CONFLICT DO NOTHING;
"""

PROVINCIAS_TABLE = """
CREATE TABLE IF NOT EXISTS provincias_lookup (provincia TEXT PRIMARY KEY);
INSERT INTO provincias_lookup SELECT DISTINCT provincia FROM beneficiaries ON CONFLICT DO NOTHING;
"""


def _apply_tuning(cur):
    """Apply PG session tuning for heavy aggregation queries."""
    cur.execute("SET work_mem = '4GB'")
    cur.execute("SET maintenance_work_mem = '4GB'")
    cur.execute("SET max_parallel_workers_per_gather = 2")
    # Additional planner hints
    cur.execute("SET effective_cache_size = '16GB'")
    cur.execute("SET random_page_cost = '1.1'")
    cur.execute("SET effective_io_concurrency = '0'")  # macOS lacks posix_fadvise()
    cur.execute("SET temp_buffers = '256MB'")


def _process_period(cur, conn, period, cross_table="mv_cross", resumen_table="mv_resumen"):
    """Process one period: temp table → insert into both MVs.

    NULLs (non-identified beneficiaries) are included in the main query
    via LEFT JOINs + COALESCE — no separate NULL queries needed.
    """
    period_date = f"{period[:4]}-{period[5:]}-01"
    # Params: period (bpc) + period (pa) + period_date (LATERAL) + period (WHERE)
    params = [period, period, period_date, period]

    # 1. Single scan → temp table _pp (includes NULL beneficiary_id rows)
    cur.execute(TEMP_TABLE_SQL, params)

    # 2. Aggregate for mv_cross (reads temp table only — fast)
    cur.execute(INSERT_CROSS_FROM_PP.format(table=cross_table))
    cross_rows = cur.rowcount

    # 3. Re-aggregate for mv_resumen (reads temp table only — fast)
    cur.execute(INSERT_RESUMEN_FROM_PP.format(table=resumen_table))
    resumen_rows = cur.rowcount

    # Commit drops the temp table (ON COMMIT DROP)
    conn.commit()

    return cross_rows, resumen_rows


def run():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    cur = conn.cursor()

    t0 = time.time()
    print("=" * 60)
    print("Creando tablas materializadas para RUB Dashboard")
    print(f"DB: {DATABASE_URL.split('@')[-1] if '@' in DATABASE_URL else DATABASE_URL}")
    print("=" * 60)

    # Drop existing tables and legacy MVs
    print("\nLimpiando tablas y MVs existentes...")
    for name in ["mv_cross", "mv_resumen", "mv_cross_new", "mv_resumen_new"]:
        print(f"  DROP TABLE {name}...", end=" ", flush=True)
        cur.execute(f"DROP TABLE IF EXISTS {name} CASCADE")
        print("OK")
    # Drop legacy materialized views (from before the table-based approach)
    legacy_mvs = [
        "mv_summary", "mv_pagos_summary", "mv_concentracion",
        "mv_incompatibilidades", "mv_by_programa", "mv_by_secretaria",
        "mv_by_provincia", "mv_by_sexo", "mv_by_grupo_etario",
        "mv_evolucion", "mv_cobertura",
    ]
    for name in legacy_mvs:
        cur.execute(f"DROP MATERIALIZED VIEW IF EXISTS {name} CASCADE")
    cur.execute("DROP FUNCTION IF EXISTS refresh_all_matviews()")
    conn.commit()
    print("  Legacy MVs limpiadas")

    # Session tuning
    _apply_tuning(cur)

    # Create empty UNLOGGED tables
    for name, create_ddl, _, _ in TABLES:
        cur.execute(create_ddl)
    conn.commit()

    # Get available periods
    cur.execute("SELECT DISTINCT periodo_mes FROM benefits ORDER BY 1")
    periods = [r[0] for r in cur.fetchall()]
    print(f"\nPeríodos encontrados: {len(periods)}")

    # Process all periods — shared temp table feeds both MVs
    print(f"\n{'─' * 60}")
    print("Procesando periodos (temp table compartida → mv_cross + mv_resumen)")
    print(f"{'─' * 60}")

    total_cross = 0
    total_resumen = 0
    for i, p in enumerate(periods, 1):
        t_p = time.time()
        cross_rows, resumen_rows = _process_period(cur, conn, p)
        total_cross += cross_rows
        total_resumen += resumen_rows
        elapsed_p = time.time() - t_p
        bar = "█" * int(i / len(periods) * 30)
        bar += "░" * (30 - len(bar))
        print(f"  {bar} {i}/{len(periods)} │ {p} │ cross:{cross_rows:>7,} res:{resumen_rows:>6,} │ {elapsed_p:>5.1f}s")

    print(f"\n  mv_cross: {total_cross:,} rows total")
    print(f"  mv_resumen: {total_resumen:,} rows total")

    # SET LOGGED + indexes
    for name, _, _, indexes in TABLES:
        print(f"\n  SET LOGGED {name}...", end=" ", flush=True)
        cur.execute(f"ALTER TABLE {name} SET LOGGED")
        conn.commit()
        print("OK")

        if isinstance(indexes, str):
            indexes = [indexes]
        for idx_i, idx_sql in enumerate(indexes, 1):
            print(f"  Índice {idx_i}/{len(indexes)} en {name}...", end=" ", flush=True)
            cur.execute(idx_sql)
            print("OK")
        conn.commit()

    # Create lookup tables
    print(f"\n{'─' * 60}")
    print("Tablas lookup")
    print(f"{'─' * 60}")
    print("  periods...", end=" ", flush=True)
    cur.execute(PERIODS_TABLE)
    conn.commit()
    print("OK")

    print("  provincias_lookup...", end=" ", flush=True)
    cur.execute(PROVINCIAS_TABLE)
    conn.commit()
    print("OK")

    # ANALYZE tables
    print(f"\n{'─' * 60}")
    print("ANALYZE")
    print(f"{'─' * 60}")
    conn.autocommit = True
    for name, _, _, _ in TABLES:
        print(f"  {name}...", end=" ", flush=True)
        cur.execute(f"ANALYZE {name}")
        print("OK")
    for lk in ["periods", "provincias_lookup"]:
        print(f"  {lk}...", end=" ", flush=True)
        cur.execute(f"ANALYZE {lk}")
        print("OK")
    conn.autocommit = False

    total_time = time.time() - t0
    mins = int(total_time // 60)
    secs = total_time % 60
    print(f"\n{'=' * 60}")
    print(f"Tablas materializadas creadas en {mins}m {secs:.0f}s")
    for name, _, _, _ in TABLES:
        cur.execute(f"SELECT COUNT(*) FROM {name}")
        cnt = cur.fetchone()[0]
        print(f"  {name}: {cnt:,} rows")
    print(f"{'=' * 60}")

    cur.close()
    conn.close()


if __name__ == "__main__":
    run()
