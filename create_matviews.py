"""
create_matviews.py — Crea tablas materializadas mv_cross + mv_resumen para el dashboard.

Se ejecuta DESPUÉS de seed_pg.py (o después de cargar datos reales).

Pipeline de datos:
    python seed_pg.py          # 1. Crear schema + cargar datos
    python create_matviews.py  # 2. Crear + popular tablas materializadas
    python refresh_matviews.py # 3. (mensual) Refrescar tablas después de carga de datos

Uso:
    DATABASE_URL=postgresql://user:pass@host/rib_dev python create_matviews.py
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

    # ── mv_cross_tc (per-program breakdown by TC dimensions) ──
    (
        "mv_cross_tc",
        """
        CREATE UNLOGGED TABLE mv_cross_tc (
            periodo_mes TEXT, nombre_programa TEXT, secretaria_origen TEXT,
            provincia TEXT, sexo TEXT, grupo_etario TEXT, cant_prestaciones TEXT,
            personas BIGINT, beneficios BIGINT, montos NUMERIC(14,2)
        )
        """,
        None,
        [
            "CREATE UNIQUE INDEX ON mv_cross_tc(periodo_mes, nombre_programa, secretaria_origen, provincia, sexo, grupo_etario, cant_prestaciones)",
            "CREATE INDEX ON mv_cross_tc(periodo_mes) INCLUDE (personas, beneficios, montos)",
            "CREATE INDEX ON mv_cross_tc(periodo_mes, nombre_programa) INCLUDE (personas, beneficios, montos)",
        ],
    ),

    # ── mv_resumen_tc (deduplicated TCs across programs) ──
    (
        "mv_resumen_tc",
        """
        CREATE UNLOGGED TABLE mv_resumen_tc (
            periodo_mes TEXT, provincia TEXT, sexo TEXT,
            grupo_etario TEXT, cant_prestaciones TEXT,
            personas BIGINT, beneficios BIGINT, montos NUMERIC(14,2)
        )
        """,
        None,
        [
            "CREATE UNIQUE INDEX ON mv_resumen_tc(periodo_mes, provincia, sexo, grupo_etario, cant_prestaciones)",
            "CREATE INDEX ON mv_resumen_tc(periodo_mes) INCLUDE (personas, beneficios, montos)",
        ],
    ),

    # ── mv_nominal_tc (per-TC nominal with aggregated TD info) ──
    (
        "mv_nominal_tc",
        """
        CREATE UNLOGGED TABLE mv_nominal_tc (
            periodo_mes TEXT,
            cuil_titular TEXT,
            nombre_titular TEXT,
            apellido_titular TEXT,
            sexo_titular TEXT,
            edad_titular INT,
            grupo_etario_titular TEXT,
            provincia_titular TEXT,
            departamento_titular TEXT,
            cant_td INT,
            cuils_td TEXT[],
            cant_beneficios INT,
            monto_total NUMERIC(14,2),
            program_ids INT[],
            active_program_ids INT[]
        )
        """,
        None,
        [
            "CREATE INDEX ON mv_nominal_tc(periodo_mes)",
            "CREATE INDEX ON mv_nominal_tc(periodo_mes, cuil_titular)",
        ],
    ),
]

# ──────────────────────────────────────────────
# Shared temp table: one scan → both MVs
# ──────────────────────────────────────────────

# Per-person temp table: single scan of benefits+payments+beneficiaries
# Groups by all 7 dimensions (mv_cross superset), mv_resumen re-aggregates from here
# Includes NULL beneficiary_id rows (non-identified) via LEFT JOINs + COALESCE
# Also includes TC (titular de cobro) columns for TC materialized views
# Params: [period, period, period, period_date, period]
TEMP_TABLE_SQL = """
CREATE TEMP TABLE _pp ON COMMIT DROP AS
WITH base AS (
    SELECT COALESCE(b.beneficiary_id, -b.id) AS beneficiary_id, b.periodo_mes,
           p.nombre_programa, s.nombre AS secretaria_origen,
           b.program_id,
           COALESCE(ben.provincia, 'Sin dato') AS provincia,
           COALESCE(ben.sexo, 'NI') AS sexo,
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
           b.nombre_titular,
           b.apellido_titular,
           COALESCE(b.provincia_titular, 'Sin dato') AS provincia_titular,
           COALESCE(b.sexo_titular, 'NI') AS sexo_titular,
           b.departamento_titular,
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
           COALESCE(bpc_tc.cant_prog_tc, 1) AS cant_prog_tc,
           b.estado_beneficio
    FROM benefits b
    JOIN programs p ON b.program_id = p.id
    JOIN secretarias s ON p.secretaria_id = s.id
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
SELECT periodo_mes, nombre_programa, secretaria_origen,
       provincia, sexo, grupo_etario,
       CASE WHEN cant_prog = 1 THEN '1' WHEN cant_prog = 2 THEN '2' ELSE '3+' END AS cant_prestaciones,
       beneficiary_id,
       COUNT(*) AS cant_benefits,
       SUM(monto) AS person_monto,
       cuil_titular, cuil_td, nombre_titular, apellido_titular,
       provincia_titular, sexo_titular, departamento_titular,
       grupo_etario_titular, edad_titular,
       CASE WHEN cant_prog_tc = 1 THEN '1' WHEN cant_prog_tc = 2 THEN '2' ELSE '3+' END AS cant_prestaciones_tc,
       program_id, estado_beneficio
FROM base
GROUP BY periodo_mes, nombre_programa, secretaria_origen,
         provincia, sexo, grupo_etario,
         CASE WHEN cant_prog = 1 THEN '1' WHEN cant_prog = 2 THEN '2' ELSE '3+' END,
         beneficiary_id,
         cuil_titular, cuil_td, nombre_titular, apellido_titular,
         provincia_titular, sexo_titular, departamento_titular,
         grupo_etario_titular, edad_titular,
         CASE WHEN cant_prog_tc = 1 THEN '1' WHEN cant_prog_tc = 2 THEN '2' ELSE '3+' END,
         program_id, estado_beneficio
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

# ── TC: Aggregate temp table → mv_cross_tc (by TC dimensions) ──
INSERT_CROSS_TC = """
INSERT INTO {table}
SELECT periodo_mes, nombre_programa, secretaria_origen,
       provincia_titular, sexo_titular, grupo_etario_titular, cant_prestaciones_tc,
       COUNT(DISTINCT cuil_titular) AS personas,
       SUM(cant_benefits) AS beneficios,
       SUM(person_monto) AS montos
FROM _pp
WHERE cuil_titular IS NOT NULL
GROUP BY periodo_mes, nombre_programa, secretaria_origen,
         provincia_titular, sexo_titular, grupo_etario_titular, cant_prestaciones_tc
"""

# ── TC: Re-aggregate temp table → mv_resumen_tc (deduplicate TCs across programs) ──
INSERT_RESUMEN_TC = """
INSERT INTO {table}
WITH tc_agg AS (
    SELECT periodo_mes, provincia_titular, sexo_titular, grupo_etario_titular, cant_prestaciones_tc,
           cuil_titular,
           SUM(cant_benefits) AS cant_benefits,
           SUM(person_monto) AS person_monto
    FROM _pp
    WHERE cuil_titular IS NOT NULL
    GROUP BY periodo_mes, provincia_titular, sexo_titular, grupo_etario_titular, cant_prestaciones_tc, cuil_titular
)
SELECT periodo_mes, provincia_titular, sexo_titular, grupo_etario_titular, cant_prestaciones_tc,
       COUNT(*) AS personas,
       SUM(cant_benefits) AS beneficios,
       SUM(person_monto) AS montos
FROM tc_agg
GROUP BY periodo_mes, provincia_titular, sexo_titular, grupo_etario_titular, cant_prestaciones_tc
"""

# ── TC: Nominal — per-TC detail with aggregated TD info ──
INSERT_NOMINAL_TC = """
INSERT INTO {table}
SELECT periodo_mes, cuil_titular,
       MAX(nombre_titular) AS nombre_titular,
       MAX(apellido_titular) AS apellido_titular,
       MAX(sexo_titular) AS sexo_titular,
       MAX(edad_titular) AS edad_titular,
       MAX(grupo_etario_titular) AS grupo_etario_titular,
       MAX(provincia_titular) AS provincia_titular,
       MAX(departamento_titular) AS departamento_titular,
       COUNT(DISTINCT cuil_td) AS cant_td,
       ARRAY_AGG(DISTINCT cuil_td) AS cuils_td,
       SUM(cant_benefits)::int AS cant_beneficios,
       SUM(person_monto) AS monto_total,
       ARRAY_AGG(DISTINCT program_id) AS program_ids,
       ARRAY_AGG(DISTINCT program_id) FILTER (WHERE estado_beneficio = 'ACTIVO') AS active_program_ids
FROM _pp
WHERE cuil_titular IS NOT NULL
GROUP BY periodo_mes, cuil_titular
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
    cur.execute("SET work_mem = '1GB'")
    cur.execute("SET maintenance_work_mem = '1GB'")
    cur.execute("SET max_parallel_workers_per_gather = 2")
    # Additional planner hints
    cur.execute("SET effective_cache_size = '16GB'")
    cur.execute("SET random_page_cost = '1.1'")
    cur.execute("SET effective_io_concurrency = '0'")  # macOS lacks posix_fadvise()
    cur.execute("SET temp_buffers = '256MB'")


def _process_period(cur, conn, period, cross_table="mv_cross", resumen_table="mv_resumen",
                    cross_tc_table=None, resumen_tc_table=None, nominal_tc_table=None):
    """Process one period: temp table → insert into TD + TC MVs.

    NULLs (non-identified beneficiaries) are included in the main query
    via LEFT JOINs + COALESCE — no separate NULL queries needed.
    """
    period_date = f"{period[:4]}-{period[5:]}-01"
    # Params: period (bpc) + period (pa) + period (bpc_tc) + period_date (LATERAL calc) + period_date (LATERAL calc_tc) + period (WHERE)
    params = [period, period, period, period_date, period_date, period]

    # 1. Single scan → temp table _pp (includes NULL beneficiary_id rows + TC columns)
    cur.execute(TEMP_TABLE_SQL, params)

    # 2. Aggregate for mv_cross (reads temp table only — fast)
    cur.execute(INSERT_CROSS_FROM_PP.format(table=cross_table))
    cross_rows = cur.rowcount

    # 3. Re-aggregate for mv_resumen (reads temp table only — fast)
    cur.execute(INSERT_RESUMEN_FROM_PP.format(table=resumen_table))
    resumen_rows = cur.rowcount

    # 4. TC materialized views (from same _pp temp table)
    cross_tc_rows = resumen_tc_rows = nominal_tc_rows = 0
    if cross_tc_table:
        cur.execute(INSERT_CROSS_TC.format(table=cross_tc_table))
        cross_tc_rows = cur.rowcount
    if resumen_tc_table:
        cur.execute(INSERT_RESUMEN_TC.format(table=resumen_tc_table))
        resumen_tc_rows = cur.rowcount
    if nominal_tc_table:
        cur.execute(INSERT_NOMINAL_TC.format(table=nominal_tc_table))
        nominal_tc_rows = cur.rowcount

    # Commit drops the temp table (ON COMMIT DROP)
    conn.commit()

    return cross_rows, resumen_rows, cross_tc_rows, resumen_tc_rows, nominal_tc_rows


def run():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    cur = conn.cursor()

    t0 = time.time()
    print("=" * 60)
    print("Creando tablas materializadas para RIB Dashboard")
    print(f"DB: {DATABASE_URL.split('@')[-1] if '@' in DATABASE_URL else DATABASE_URL}")
    print("=" * 60)

    # Drop existing tables and legacy MVs
    print("\nLimpiando tablas y MVs existentes...")
    all_table_names = [t[0] for t in TABLES]
    shadow_names = [f"{t}_new" for t in all_table_names]
    for name in all_table_names + shadow_names:
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
    total_cross_tc = 0
    total_resumen_tc = 0
    total_nominal_tc = 0
    for i, p in enumerate(periods, 1):
        t_p = time.time()
        cross_rows, resumen_rows, cross_tc, resumen_tc, nominal_tc = _process_period(
            cur, conn, p,
            cross_tc_table="mv_cross_tc",
            resumen_tc_table="mv_resumen_tc",
            nominal_tc_table="mv_nominal_tc",
        )
        total_cross += cross_rows
        total_resumen += resumen_rows
        total_cross_tc += cross_tc
        total_resumen_tc += resumen_tc
        total_nominal_tc += nominal_tc
        elapsed_p = time.time() - t_p
        bar = "█" * int(i / len(periods) * 30)
        bar += "░" * (30 - len(bar))
        print(f"  {bar} {i}/{len(periods)} │ {p} │ cross:{cross_rows:>7,} res:{resumen_rows:>6,} tc:{cross_tc:>7,} │ {elapsed_p:>5.1f}s")

    print(f"\n  mv_cross: {total_cross:,} rows total")
    print(f"  mv_resumen: {total_resumen:,} rows total")
    print(f"  mv_cross_tc: {total_cross_tc:,} rows total")
    print(f"  mv_resumen_tc: {total_resumen_tc:,} rows total")
    print(f"  mv_nominal_tc: {total_nominal_tc:,} rows total")

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
