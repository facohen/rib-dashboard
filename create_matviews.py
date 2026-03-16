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
        """
        CREATE TABLE mv_cross AS
        WITH
        benef_prog_count AS (
            SELECT beneficiary_id, periodo_mes,
                   COUNT(DISTINCT program_id) AS cant_prog
            FROM benefits
            WHERE estado_beneficio = 'ACTIVO' AND beneficiary_id IS NOT NULL
            GROUP BY beneficiary_id, periodo_mes
        ),
        payment_agg AS (
            SELECT beneficiary_id, program_id, periodo_mes,
                   SUM(monto_prestacion) AS total_monto
            FROM payments
            GROUP BY beneficiary_id, program_id, periodo_mes
        ),
        benefit_enriched AS (
            SELECT b.periodo_mes, p.nombre_programa, p.secretaria_origen,
                   ben.provincia, ben.sexo,
                   CASE
                     WHEN ben.fecha_nacimiento IS NULL THEN 'Sin dato'
                     WHEN edad <= 4  THEN '0-4 años'
                     WHEN edad <= 12 THEN '5-12 años'
                     WHEN edad <= 17 THEN '13-17 años'
                     WHEN edad <= 29 THEN '18-29 años'
                     WHEN edad <= 59 THEN '30-59 años'
                     ELSE '60+ años'
                   END AS grupo_etario,
                   CASE
                       WHEN bpc.cant_prog = 1 THEN '1'
                       WHEN bpc.cant_prog = 2 THEN '2'
                       ELSE '3+'
                   END AS cant_prestaciones,
                   b.beneficiary_id,
                   pa.total_monto
            FROM benefits b
            JOIN programs p ON b.program_id = p.id
            JOIN beneficiaries ben ON b.beneficiary_id = ben.id
            JOIN benef_prog_count bpc
                 ON bpc.beneficiary_id = b.beneficiary_id
                 AND bpc.periodo_mes = b.periodo_mes
            LEFT JOIN payment_agg pa ON pa.beneficiary_id = b.beneficiary_id
                AND pa.program_id = b.program_id AND pa.periodo_mes = b.periodo_mes
            CROSS JOIN LATERAL (
                SELECT EXTRACT(YEAR FROM AGE(
                    (SUBSTRING(b.periodo_mes FROM 1 FOR 4) || '-' ||
                     SUBSTRING(b.periodo_mes FROM 6 FOR 2) || '-01')::date,
                    ben.fecha_nacimiento
                ))::int AS edad
            ) calc
            WHERE b.estado_beneficio = 'ACTIVO'
        ),
        per_person AS (
            SELECT periodo_mes, nombre_programa, secretaria_origen,
                   provincia, sexo, grupo_etario, cant_prestaciones,
                   beneficiary_id,
                   COUNT(*) AS cant_benefits,
                   COALESCE(SUM(total_monto), 0) AS person_monto
            FROM benefit_enriched
            GROUP BY periodo_mes, nombre_programa, secretaria_origen,
                     provincia, sexo, grupo_etario, cant_prestaciones, beneficiary_id
        )
        SELECT periodo_mes, nombre_programa, secretaria_origen,
               provincia, sexo, grupo_etario, cant_prestaciones,
               COUNT(*) AS personas,
               SUM(cant_benefits) AS beneficios,
               SUM(person_monto) AS montos
        FROM per_person
        GROUP BY periodo_mes, nombre_programa, secretaria_origen,
                 provincia, sexo, grupo_etario, cant_prestaciones
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
        """
        CREATE TABLE mv_resumen AS
        WITH
        benef_prog_count AS (
            SELECT beneficiary_id, periodo_mes,
                   COUNT(DISTINCT program_id) AS cant_prog
            FROM benefits
            WHERE estado_beneficio = 'ACTIVO' AND beneficiary_id IS NOT NULL
            GROUP BY beneficiary_id, periodo_mes
        ),
        payment_agg AS (
            SELECT beneficiary_id, program_id, periodo_mes,
                   SUM(monto_prestacion) AS total_monto
            FROM payments
            GROUP BY beneficiary_id, program_id, periodo_mes
        ),
        benefit_enriched AS (
            SELECT b.periodo_mes, p.nombre_programa, p.secretaria_origen,
                   ben.provincia, ben.sexo,
                   CASE
                     WHEN ben.fecha_nacimiento IS NULL THEN 'Sin dato'
                     WHEN edad <= 4  THEN '0-4 años'
                     WHEN edad <= 12 THEN '5-12 años'
                     WHEN edad <= 17 THEN '13-17 años'
                     WHEN edad <= 29 THEN '18-29 años'
                     WHEN edad <= 59 THEN '30-59 años'
                     ELSE '60+ años'
                   END AS grupo_etario,
                   CASE
                       WHEN bpc.cant_prog = 1 THEN '1'
                       WHEN bpc.cant_prog = 2 THEN '2'
                       ELSE '3+'
                   END AS cant_prestaciones,
                   b.beneficiary_id,
                   pa.total_monto
            FROM benefits b
            JOIN programs p ON b.program_id = p.id
            JOIN beneficiaries ben ON b.beneficiary_id = ben.id
            JOIN benef_prog_count bpc
                 ON bpc.beneficiary_id = b.beneficiary_id
                 AND bpc.periodo_mes = b.periodo_mes
            LEFT JOIN payment_agg pa ON pa.beneficiary_id = b.beneficiary_id
                AND pa.program_id = b.program_id AND pa.periodo_mes = b.periodo_mes
            CROSS JOIN LATERAL (
                SELECT EXTRACT(YEAR FROM AGE(
                    (SUBSTRING(b.periodo_mes FROM 1 FOR 4) || '-' ||
                     SUBSTRING(b.periodo_mes FROM 6 FOR 2) || '-01')::date,
                    ben.fecha_nacimiento
                ))::int AS edad
            ) calc
            WHERE b.estado_beneficio = 'ACTIVO'
        ),
        per_person AS (
            SELECT periodo_mes, nombre_programa, secretaria_origen,
                   provincia, sexo, grupo_etario, cant_prestaciones,
                   beneficiary_id,
                   COUNT(*) AS cant_benefits,
                   COALESCE(SUM(total_monto), 0) AS person_monto
            FROM benefit_enriched
            GROUP BY periodo_mes, nombre_programa, secretaria_origen,
                     provincia, sexo, grupo_etario, cant_prestaciones, beneficiary_id
        ),
        per_person_total AS (
            SELECT periodo_mes, provincia, sexo, grupo_etario, cant_prestaciones,
                   beneficiary_id,
                   SUM(cant_benefits) AS cant_benefits,
                   SUM(person_monto) AS person_monto
            FROM per_person
            GROUP BY periodo_mes, provincia, sexo, grupo_etario, cant_prestaciones, beneficiary_id
        )
        SELECT periodo_mes, provincia, sexo, grupo_etario, cant_prestaciones,
               COUNT(*) AS personas,
               SUM(cant_benefits) AS beneficios,
               SUM(person_monto) AS montos
        FROM per_person_total
        GROUP BY periodo_mes, provincia, sexo, grupo_etario, cant_prestaciones
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
    # Drop physical tables first (current + shadow) — must come before MV drops
    # because mv_cross may exist as either a table or a materialized view
    for name in ["mv_cross", "mv_resumen", "mv_cross_new", "mv_resumen_new"]:
        cur.execute(f"DROP TABLE IF EXISTS {name} CASCADE")
    # Drop legacy materialized views (from before the table-based approach)
    legacy_mvs = [
        "mv_summary", "mv_pagos_summary", "mv_concentracion",
        "mv_incompatibilidades", "mv_by_programa", "mv_by_secretaria",
        "mv_by_provincia", "mv_by_sexo", "mv_by_grupo_etario",
        "mv_evolucion", "mv_cobertura", "mv_cross",
    ]
    for name in legacy_mvs:
        cur.execute(f"DROP MATERIALIZED VIEW IF EXISTS {name} CASCADE")
    # Drop stored function if exists
    cur.execute("DROP FUNCTION IF EXISTS refresh_all_matviews()")
    conn.commit()

    # Create each table
    for name, create_sql, indexes in TABLES:
        t_mv = time.time()
        print(f"\nCreando {name}...", end=" ", flush=True)
        cur.execute(create_sql)
        conn.commit()

        # Create indexes
        if isinstance(indexes, str):
            indexes = [indexes]
        for idx_sql in indexes:
            cur.execute(idx_sql)
        conn.commit()

        # Count rows
        cur.execute(f"SELECT COUNT(*) FROM {name}")
        row_count = cur.fetchone()[0]
        elapsed = time.time() - t_mv
        print(f"OK ({row_count:,} rows, {elapsed:.1f}s)")

    # Create lookup tables
    print("\nCreando tabla periods...", end=" ", flush=True)
    cur.execute(PERIODS_TABLE)
    conn.commit()
    print("OK")

    print("Creando tabla provincias_lookup...", end=" ", flush=True)
    cur.execute(PROVINCIAS_TABLE)
    conn.commit()
    print("OK")

    # ANALYZE tables
    print("\nANALYZE en tablas...")
    conn.autocommit = True
    for name, _, _ in TABLES:
        cur.execute(f"ANALYZE {name}")
    cur.execute("ANALYZE periods")
    cur.execute("ANALYZE provincias_lookup")
    conn.autocommit = False

    total_time = time.time() - t0
    print(f"\n{'=' * 60}")
    print(f"Tablas materializadas creadas en {total_time:.1f}s")
    print(f"{'=' * 60}")

    cur.close()
    conn.close()


if __name__ == "__main__":
    run()
