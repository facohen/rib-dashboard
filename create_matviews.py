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
# Table definitions (physical tables, not materialized views)
# ──────────────────────────────────────────────

TABLES = [
    # ── mv_cross (multidimensional per-program, ~130-180K rows) ──
    (
        "mv_cross",
        # DDL: create empty UNLOGGED table
        """
        CREATE UNLOGGED TABLE mv_cross (
            periodo_mes TEXT, nombre_programa TEXT, secretaria_origen TEXT,
            provincia TEXT, sexo TEXT, grupo_etario TEXT, cant_prestaciones TEXT,
            personas BIGINT, beneficios BIGINT, montos NUMERIC(14,2)
        )
        """,
        # INSERT for one period (%s x4: bpc, payment subquery, main WHERE, UNION ALL)
        """
        INSERT INTO mv_cross
        WITH base AS (
            SELECT b.beneficiary_id, b.periodo_mes,
                   p.nombre_programa, p.secretaria_origen,
                   ben.provincia, ben.sexo,
                   CASE
                     WHEN ben.fecha_nacimiento IS NULL THEN 'Sin dato'
                     WHEN calc.edad <= 4  THEN '0-4 años'
                     WHEN calc.edad <= 12 THEN '5-12 años'
                     WHEN calc.edad <= 17 THEN '13-17 años'
                     WHEN calc.edad <= 29 THEN '18-29 años'
                     WHEN calc.edad <= 59 THEN '30-59 años'
                     ELSE '60+ años'
                   END AS grupo_etario,
                   bpc.cant_prog,
                   COALESCE(pa.total_monto, 0) AS monto
            FROM benefits b
            JOIN programs p ON b.program_id = p.id
            JOIN beneficiaries ben ON b.beneficiary_id = ben.id
            JOIN (
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
                SELECT EXTRACT(YEAR FROM AGE(
                    (SUBSTRING(b.periodo_mes FROM 1 FOR 4) || '-' ||
                     SUBSTRING(b.periodo_mes FROM 6 FOR 2) || '-01')::date,
                    ben.fecha_nacimiento
                ))::int AS edad
            ) calc
            WHERE b.periodo_mes = %s AND b.beneficiary_id IS NOT NULL
        ),
        per_person AS (
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
        )
        SELECT periodo_mes, nombre_programa, secretaria_origen,
               provincia, sexo, grupo_etario, cant_prestaciones,
               COUNT(*) AS personas,
               SUM(cant_benefits) AS beneficios,
               SUM(person_monto) AS montos
        FROM per_person
        GROUP BY periodo_mes, nombre_programa, secretaria_origen,
                 provincia, sexo, grupo_etario, cant_prestaciones
        UNION ALL
        SELECT b.periodo_mes, p.nombre_programa, p.secretaria_origen,
               'Sin dato' AS provincia, 'Sin dato' AS sexo,
               'Sin dato' AS grupo_etario, '1' AS cant_prestaciones,
               COUNT(*) AS personas, COUNT(*) AS beneficios,
               CAST(0 AS NUMERIC(14,2)) AS montos
        FROM benefits b
        JOIN programs p ON b.program_id = p.id
        WHERE b.beneficiary_id IS NULL AND b.periodo_mes = %s
        GROUP BY b.periodo_mes, p.nombre_programa, p.secretaria_origen
        """,
        [
            "CREATE UNIQUE INDEX ON mv_cross(periodo_mes, nombre_programa, secretaria_origen, provincia, sexo, grupo_etario, cant_prestaciones)",
            "CREATE INDEX ON mv_cross(periodo_mes)",
            "CREATE INDEX ON mv_cross(periodo_mes, nombre_programa)",
            "CREATE INDEX ON mv_cross(periodo_mes, provincia)",
            "CREATE INDEX ON mv_cross(periodo_mes, secretaria_origen)",
            "CREATE INDEX ON mv_cross(periodo_mes, cant_prestaciones)",
        ],
    ),

    # ── mv_resumen (deduplicated persons, ~23K rows) ──
    (
        "mv_resumen",
        # DDL: create empty UNLOGGED table
        """
        CREATE UNLOGGED TABLE mv_resumen (
            periodo_mes TEXT, provincia TEXT, sexo TEXT,
            grupo_etario TEXT, cant_prestaciones TEXT,
            personas BIGINT, beneficios BIGINT, montos NUMERIC(14,2)
        )
        """,
        # INSERT for one period (%s x4: bpc, payment subquery, main WHERE, UNION ALL)
        """
        INSERT INTO mv_resumen
        WITH base AS (
            SELECT b.beneficiary_id, b.periodo_mes,
                   ben.provincia, ben.sexo,
                   CASE
                     WHEN ben.fecha_nacimiento IS NULL THEN 'Sin dato'
                     WHEN calc.edad <= 4  THEN '0-4 años'
                     WHEN calc.edad <= 12 THEN '5-12 años'
                     WHEN calc.edad <= 17 THEN '13-17 años'
                     WHEN calc.edad <= 29 THEN '18-29 años'
                     WHEN calc.edad <= 59 THEN '30-59 años'
                     ELSE '60+ años'
                   END AS grupo_etario,
                   bpc.cant_prog,
                   COALESCE(pa.total_monto, 0) AS monto
            FROM benefits b
            JOIN programs p ON b.program_id = p.id
            JOIN beneficiaries ben ON b.beneficiary_id = ben.id
            JOIN (
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
                SELECT EXTRACT(YEAR FROM AGE(
                    (SUBSTRING(b.periodo_mes FROM 1 FOR 4) || '-' ||
                     SUBSTRING(b.periodo_mes FROM 6 FOR 2) || '-01')::date,
                    ben.fecha_nacimiento
                ))::int AS edad
            ) calc
            WHERE b.periodo_mes = %s AND b.beneficiary_id IS NOT NULL
        ),
        per_person AS (
            SELECT periodo_mes, provincia, sexo, grupo_etario,
                   CASE WHEN cant_prog = 1 THEN '1' WHEN cant_prog = 2 THEN '2' ELSE '3+' END AS cant_prestaciones,
                   beneficiary_id,
                   COUNT(*) AS cant_benefits,
                   SUM(monto) AS person_monto
            FROM base
            GROUP BY periodo_mes, provincia, sexo, grupo_etario,
                     CASE WHEN cant_prog = 1 THEN '1' WHEN cant_prog = 2 THEN '2' ELSE '3+' END,
                     beneficiary_id
        )
        SELECT periodo_mes, provincia, sexo, grupo_etario, cant_prestaciones,
               COUNT(*) AS personas,
               SUM(cant_benefits) AS beneficios,
               SUM(person_monto) AS montos
        FROM per_person
        GROUP BY periodo_mes, provincia, sexo, grupo_etario, cant_prestaciones
        UNION ALL
        SELECT b.periodo_mes,
               'Sin dato' AS provincia, 'Sin dato' AS sexo,
               'Sin dato' AS grupo_etario, '1' AS cant_prestaciones,
               COUNT(*) AS personas, COUNT(*) AS beneficios,
               CAST(0 AS NUMERIC(14,2)) AS montos
        FROM benefits b
        WHERE b.beneficiary_id IS NULL AND b.periodo_mes = %s
        GROUP BY b.periodo_mes
        """,
        [
            "CREATE UNIQUE INDEX ON mv_resumen(periodo_mes, provincia, sexo, grupo_etario, cant_prestaciones)",
            "CREATE INDEX ON mv_resumen(periodo_mes)",
        ],
    ),
]

# ── Periods lookup table ──
PERIODS_TABLE = """
CREATE TABLE IF NOT EXISTS periods (periodo_mes TEXT PRIMARY KEY);
INSERT INTO periods SELECT DISTINCT periodo_mes FROM benefits ON CONFLICT DO NOTHING;
"""

PROVINCIAS_TABLE = """
CREATE TABLE IF NOT EXISTS provincias_lookup (provincia TEXT PRIMARY KEY);
INSERT INTO provincias_lookup SELECT DISTINCT provincia FROM beneficiaries ON CONFLICT DO NOTHING;
"""


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

    # Boost memory for heavy aggregation queries (default 4MB spills to disk)
    cur.execute("SET work_mem = '1GB'")
    cur.execute("SET maintenance_work_mem = '2GB'")
    # Use more parallel workers for large scans/aggregations
    cur.execute("SET max_parallel_workers_per_gather = 8")

    # Get available periods
    cur.execute("SELECT DISTINCT periodo_mes FROM benefits ORDER BY 1")
    periods = [r[0] for r in cur.fetchall()]
    print(f"\nPeríodos encontrados: {len(periods)}")

    # Create each table, inserting period by period
    for tbl_idx, (name, create_ddl, insert_sql, indexes) in enumerate(TABLES, 1):
        t_mv = time.time()
        print(f"\n{'─' * 60}")
        print(f"[{tbl_idx}/{len(TABLES)}] Creando {name}...")
        print(f"{'─' * 60}")

        # Create empty UNLOGGED table
        cur.execute(create_ddl)
        conn.commit()

        # Insert period by period (reduces memory pressure)
        total_rows = 0
        n_params = insert_sql.count('%s')
        for i, p in enumerate(periods, 1):
            t_p = time.time()
            cur.execute(insert_sql, [p] * n_params)
            conn.commit()
            rows = cur.rowcount
            total_rows += rows
            elapsed_p = time.time() - t_p
            bar = "█" * int(i / len(periods) * 30)
            bar += "░" * (30 - len(bar))
            print(f"  {bar} {i}/{len(periods)} │ {p} │ {rows:>8,} rows │ {elapsed_p:>5.1f}s")

        # Convert to logged table and create indexes
        print(f"  SET LOGGED...", end=" ", flush=True)
        cur.execute(f"ALTER TABLE {name} SET LOGGED")
        conn.commit()
        print("OK")

        if isinstance(indexes, str):
            indexes = [indexes]
        for idx_i, idx_sql in enumerate(indexes, 1):
            idx_name = idx_sql.split("ON")[0].strip().replace("CREATE UNIQUE INDEX", "UNIQUE IDX").replace("CREATE INDEX", "IDX")
            print(f"  Índice {idx_i}/{len(indexes)}...", end=" ", flush=True)
            cur.execute(idx_sql)
            print("OK")
        conn.commit()

        elapsed = time.time() - t_mv
        print(f"  ✓ {name}: {total_rows:,} rows en {elapsed:.1f}s")

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
