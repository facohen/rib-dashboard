"""
create_matviews.py — Crea mv_cross + mv_cobertura para el dashboard.

Se ejecuta DESPUÉS de seed_pg.py (o después de cargar datos reales).

Pipeline de datos:
    python seed_pg.py          # 1. Crear schema + cargar datos
    python create_matviews.py  # 2. Crear + popular vistas materializadas
    python refresh_matviews.py # 3. (mensual) Refrescar MVs después de carga de datos

Uso:
    DATABASE_URL=postgresql://user:pass@host/rub python create_matviews.py
"""
import os
import sys
import time
import psycopg2

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost/rub")

# ──────────────────────────────────────────────
# Materialized View definitions
# ──────────────────────────────────────────────

MATVIEWS = [
    # ── mv_cross (multidimensional con cant_prestaciones, ~100-130K rows) ──
    (
        "mv_cross",
        """
        CREATE MATERIALIZED VIEW mv_cross AS
        WITH benef_prog_count AS (
            SELECT beneficiary_id, periodo_mes,
                   COUNT(DISTINCT program_id) AS cant_prog
            FROM benefits
            WHERE estado_beneficio = 'ACTIVO' AND beneficiary_id IS NOT NULL
            GROUP BY beneficiary_id, periodo_mes
        ),
        base AS (
            SELECT b.periodo_mes, p.nombre_programa, p.secretaria_origen,
                   ben.provincia, ben.sexo,
                   CASE
                     WHEN EXTRACT(YEAR FROM AGE(
                         (SUBSTRING(b.periodo_mes FROM 1 FOR 4) || '-' ||
                          SUBSTRING(b.periodo_mes FROM 6 FOR 2) || '-01')::date,
                         ben.fecha_nacimiento
                     )) <= 4  THEN '0-4 años'
                     WHEN EXTRACT(YEAR FROM AGE(
                         (SUBSTRING(b.periodo_mes FROM 1 FOR 4) || '-' ||
                          SUBSTRING(b.periodo_mes FROM 6 FOR 2) || '-01')::date,
                         ben.fecha_nacimiento
                     )) <= 12 THEN '5-12 años'
                     WHEN EXTRACT(YEAR FROM AGE(
                         (SUBSTRING(b.periodo_mes FROM 1 FOR 4) || '-' ||
                          SUBSTRING(b.periodo_mes FROM 6 FOR 2) || '-01')::date,
                         ben.fecha_nacimiento
                     )) <= 17 THEN '13-17 años'
                     WHEN EXTRACT(YEAR FROM AGE(
                         (SUBSTRING(b.periodo_mes FROM 1 FOR 4) || '-' ||
                          SUBSTRING(b.periodo_mes FROM 6 FOR 2) || '-01')::date,
                         ben.fecha_nacimiento
                     )) <= 29 THEN '18-29 años'
                     WHEN EXTRACT(YEAR FROM AGE(
                         (SUBSTRING(b.periodo_mes FROM 1 FOR 4) || '-' ||
                          SUBSTRING(b.periodo_mes FROM 6 FOR 2) || '-01')::date,
                         ben.fecha_nacimiento
                     )) <= 59 THEN '30-59 años'
                     ELSE '60+ años'
                   END AS grupo_etario,
                   CASE ben.sexo WHEN 'M' THEN 'Masculino' WHEN 'F' THEN 'Femenino'
                                 WHEN 'X' THEN 'No binario' ELSE 'No informado' END AS sexo_label,
                   CASE
                       WHEN bpc.cant_prog = 1 THEN '1'
                       WHEN bpc.cant_prog = 2 THEN '2'
                       ELSE '3+'
                   END AS cant_prestaciones,
                   b.beneficiary_id,
                   pay.monto_prestacion
            FROM benefits b
            JOIN programs p ON b.program_id = p.id
            JOIN beneficiaries ben ON b.beneficiary_id = ben.id
            JOIN benef_prog_count bpc
                 ON bpc.beneficiary_id = b.beneficiary_id
                 AND bpc.periodo_mes = b.periodo_mes
            LEFT JOIN payments pay ON pay.beneficiary_id = b.beneficiary_id
                AND pay.program_id = b.program_id AND pay.periodo_mes = b.periodo_mes
            WHERE b.estado_beneficio = 'ACTIVO'
        )
        SELECT periodo_mes, nombre_programa, secretaria_origen,
               provincia, sexo, grupo_etario, sexo_label, cant_prestaciones,
               COUNT(DISTINCT beneficiary_id) AS personas,
               COUNT(*) AS beneficios,
               COALESCE(SUM(monto_prestacion), 0) AS montos
        FROM base
        GROUP BY periodo_mes, nombre_programa, secretaria_origen,
                 provincia, sexo, grupo_etario, sexo_label, cant_prestaciones
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

    # ── mv_cobertura (12 rows — one per period) ──
    (
        "mv_cobertura",
        """
        CREATE MATERIALIZED VIEW mv_cobertura AS
        SELECT b.periodo_mes,
               COUNT(DISTINCT b.beneficiary_id) FILTER (WHERE b.beneficiary_id IS NOT NULL) AS identificados,
               COUNT(*) FILTER (WHERE b.beneficiary_id IS NULL OR b.cuil_raw IS NULL) AS no_identificados,
               COUNT(*) AS total_prest,
               COUNT(DISTINCT b.program_id) AS cant_programas,
               COUNT(DISTINCT b.cuil_raw) FILTER (WHERE b.cuil_raw IS NOT NULL AND LENGTH(b.cuil_raw) = 11) AS cobertura
        FROM benefits b
        WHERE b.estado_beneficio = 'ACTIVO'
        GROUP BY b.periodo_mes
        """,
        "CREATE UNIQUE INDEX ON mv_cobertura(periodo_mes)",
    ),
]

# ── Refresh function (PostgreSQL stored procedure) ──
REFRESH_FUNCTION = """
CREATE OR REPLACE FUNCTION refresh_all_matviews() RETURNS void AS $$
BEGIN
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_cross;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_cobertura;
END;
$$ LANGUAGE plpgsql;
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


def run():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    cur = conn.cursor()

    t0 = time.time()
    print("=" * 60)
    print("Creando vistas materializadas para RUB Dashboard")
    print(f"DB: {DATABASE_URL.split('@')[-1] if '@' in DATABASE_URL else DATABASE_URL}")
    print("=" * 60)

    # Drop existing MVs first (including old ones from previous architecture)
    print("\nLimpiando MVs existentes...")
    old_mvs = [
        "mv_summary", "mv_pagos_summary", "mv_concentracion",
        "mv_incompatibilidades", "mv_by_programa", "mv_by_secretaria",
        "mv_by_provincia", "mv_by_sexo", "mv_by_grupo_etario",
        "mv_evolucion", "mv_cross", "mv_cobertura",
    ]
    for name in reversed(old_mvs):
        cur.execute(f"DROP MATERIALIZED VIEW IF EXISTS {name} CASCADE")
    conn.commit()

    # Create each MV
    for name, create_sql, indexes in MATVIEWS:
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

    # Create refresh function
    print("\nCreando funcion refresh_all_matviews()...", end=" ", flush=True)
    cur.execute(REFRESH_FUNCTION)
    conn.commit()
    print("OK")

    # Create lookup tables
    print("Creando tabla periods...", end=" ", flush=True)
    cur.execute(PERIODS_TABLE)
    conn.commit()
    print("OK")

    print("Creando tabla provincias_lookup...", end=" ", flush=True)
    cur.execute(PROVINCIAS_TABLE)
    conn.commit()
    print("OK")

    # ANALYZE MVs
    print("\nANALYZE en MVs...")
    conn.autocommit = True
    for name, _, _ in MATVIEWS:
        cur.execute(f"ANALYZE {name}")
    cur.execute("ANALYZE periods")
    cur.execute("ANALYZE provincias_lookup")
    conn.autocommit = False

    total_time = time.time() - t0
    print(f"\n{'=' * 60}")
    print(f"Vistas materializadas creadas en {total_time:.1f}s")
    print(f"{'=' * 60}")

    cur.close()
    conn.close()


if __name__ == "__main__":
    run()
