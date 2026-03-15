"""
create_matviews.py — Crea y popula vistas materializadas para el dashboard.

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
    # ── mv_summary (12 rows — one per period) ──
    (
        "mv_summary",
        """
        CREATE MATERIALIZED VIEW mv_summary AS
        SELECT
            b.periodo_mes,
            COUNT(DISTINCT b.beneficiary_id) FILTER (WHERE b.beneficiary_id IS NOT NULL) AS total_benef,
            COUNT(DISTINCT CASE WHEN b.cuil_raw IS NOT NULL AND LENGTH(b.cuil_raw) = 11
                                THEN b.cuil_raw END) AS cobertura,
            COUNT(*) AS total_prest,
            COUNT(DISTINCT b.program_id) AS cant_programas,
            COUNT(*) FILTER (WHERE b.beneficiary_id IS NULL OR b.cuil_raw IS NULL) AS no_identificados,
            COUNT(*) FILTER (WHERE b.beneficiary_id IS NOT NULL) AS identificados
        FROM benefits b
        WHERE b.estado_beneficio = 'ACTIVO'
        GROUP BY b.periodo_mes
        """,
        "CREATE UNIQUE INDEX ON mv_summary(periodo_mes)",
    ),

    # ── mv_pagos_summary (12 rows) ──
    (
        "mv_pagos_summary",
        """
        CREATE MATERIALIZED VIEW mv_pagos_summary AS
        SELECT b.periodo_mes,
               COALESCE(SUM(pay.monto_prestacion), 0) AS total_monto,
               COUNT(DISTINCT pay.beneficiary_id) AS benef_con_pago
        FROM benefits b
        LEFT JOIN payments pay ON pay.beneficiary_id = b.beneficiary_id
            AND pay.program_id = b.program_id AND pay.periodo_mes = b.periodo_mes
        WHERE b.estado_beneficio = 'ACTIVO'
        GROUP BY b.periodo_mes
        """,
        "CREATE UNIQUE INDEX ON mv_pagos_summary(periodo_mes)",
    ),

    # ── mv_concentracion (12 rows) ──
    (
        "mv_concentracion",
        """
        CREATE MATERIALIZED VIEW mv_concentracion AS
        SELECT periodo_mes,
            SUM(CASE WHEN cnt = 1 THEN 1 ELSE 0 END) AS con_una,
            SUM(CASE WHEN cnt = 2 THEN 1 ELSE 0 END) AS con_dos,
            SUM(CASE WHEN cnt >= 3 THEN 1 ELSE 0 END) AS con_tres_mas
        FROM (
            SELECT beneficiary_id, periodo_mes, COUNT(DISTINCT program_id) AS cnt
            FROM benefits
            WHERE estado_beneficio = 'ACTIVO' AND beneficiary_id IS NOT NULL
            GROUP BY beneficiary_id, periodo_mes
        ) sub
        GROUP BY periodo_mes
        """,
        "CREATE UNIQUE INDEX ON mv_concentracion(periodo_mes)",
    ),

    # ── mv_incompatibilidades (12 rows) ──
    (
        "mv_incompatibilidades",
        """
        CREATE MATERIALIZED VIEW mv_incompatibilidades AS
        SELECT b1.periodo_mes,
               COUNT(DISTINCT b1.beneficiary_id) AS cant_incompatibles
        FROM incompatibility_rules ir
        JOIN benefits b1 ON b1.program_id = ir.program_a_id
            AND b1.estado_beneficio = 'ACTIVO'
        JOIN benefits b2 ON b2.beneficiary_id = b1.beneficiary_id
            AND b2.program_id = ir.program_b_id
            AND b2.periodo_mes = b1.periodo_mes
            AND b2.estado_beneficio = 'ACTIVO'
        WHERE ir.is_compatible = 0
        GROUP BY b1.periodo_mes
        """,
        "CREATE UNIQUE INDEX ON mv_incompatibilidades(periodo_mes)",
    ),

    # ── mv_by_programa (12 × ~10 = ~120 rows) ──
    (
        "mv_by_programa",
        """
        CREATE MATERIALIZED VIEW mv_by_programa AS
        SELECT b.periodo_mes, p.nombre_programa AS programa,
               COUNT(DISTINCT b.beneficiary_id) AS personas,
               COUNT(*) AS beneficios,
               COALESCE(SUM(pay.monto_prestacion), 0) AS montos
        FROM benefits b
        JOIN programs p ON b.program_id = p.id
        LEFT JOIN payments pay ON pay.beneficiary_id = b.beneficiary_id
            AND pay.program_id = b.program_id AND pay.periodo_mes = b.periodo_mes
        WHERE b.estado_beneficio = 'ACTIVO' AND b.beneficiary_id IS NOT NULL
        GROUP BY b.periodo_mes, p.nombre_programa
        """,
        "CREATE UNIQUE INDEX ON mv_by_programa(periodo_mes, programa)",
    ),

    # ── mv_by_secretaria (12 × ~3 = ~36 rows) ──
    (
        "mv_by_secretaria",
        """
        CREATE MATERIALIZED VIEW mv_by_secretaria AS
        SELECT b.periodo_mes, p.secretaria_origen AS secretaria,
               COUNT(DISTINCT b.beneficiary_id) AS personas,
               COUNT(*) AS beneficios,
               COALESCE(SUM(pay.monto_prestacion), 0) AS montos
        FROM benefits b
        JOIN programs p ON b.program_id = p.id
        LEFT JOIN payments pay ON pay.beneficiary_id = b.beneficiary_id
            AND pay.program_id = b.program_id AND pay.periodo_mes = b.periodo_mes
        WHERE b.estado_beneficio = 'ACTIVO' AND b.cuil_raw IS NOT NULL
        GROUP BY b.periodo_mes, p.secretaria_origen
        """,
        "CREATE UNIQUE INDEX ON mv_by_secretaria(periodo_mes, secretaria)",
    ),

    # ── mv_by_provincia (12 × ~24 = ~288 rows) ──
    (
        "mv_by_provincia",
        """
        CREATE MATERIALIZED VIEW mv_by_provincia AS
        SELECT b.periodo_mes, ben.provincia,
               COUNT(DISTINCT b.beneficiary_id) AS personas,
               COUNT(*) AS beneficios,
               COALESCE(SUM(pay.monto_prestacion), 0) AS montos
        FROM benefits b
        JOIN beneficiaries ben ON b.beneficiary_id = ben.id
        LEFT JOIN payments pay ON pay.beneficiary_id = b.beneficiary_id
            AND pay.program_id = b.program_id AND pay.periodo_mes = b.periodo_mes
        WHERE b.estado_beneficio = 'ACTIVO'
        GROUP BY b.periodo_mes, ben.provincia
        """,
        "CREATE UNIQUE INDEX ON mv_by_provincia(periodo_mes, provincia)",
    ),

    # ── mv_by_sexo (12 × ~4 = ~48 rows) ──
    (
        "mv_by_sexo",
        """
        CREATE MATERIALIZED VIEW mv_by_sexo AS
        SELECT b.periodo_mes, ben.sexo,
               CASE ben.sexo WHEN 'M' THEN 'Masculino' WHEN 'F' THEN 'Femenino'
                             WHEN 'X' THEN 'No binario' ELSE 'No informado' END AS label,
               COUNT(DISTINCT b.beneficiary_id) AS personas,
               COUNT(*) AS beneficios,
               COALESCE(SUM(pay.monto_prestacion), 0) AS montos
        FROM benefits b
        JOIN beneficiaries ben ON b.beneficiary_id = ben.id
        LEFT JOIN payments pay ON pay.beneficiary_id = b.beneficiary_id
            AND pay.program_id = b.program_id AND pay.periodo_mes = b.periodo_mes
        WHERE b.estado_beneficio = 'ACTIVO'
        GROUP BY b.periodo_mes, ben.sexo
        """,
        "CREATE UNIQUE INDEX ON mv_by_sexo(periodo_mes, sexo)",
    ),

    # ── mv_by_grupo_etario (12 × 6 = ~72 rows) ──
    (
        "mv_by_grupo_etario",
        """
        CREATE MATERIALIZED VIEW mv_by_grupo_etario AS
        WITH aged AS (
            SELECT b.periodo_mes, b.beneficiary_id,
                   EXTRACT(YEAR FROM AGE(
                       (SUBSTRING(b.periodo_mes FROM 1 FOR 4) || '-' ||
                        SUBSTRING(b.periodo_mes FROM 6 FOR 2) || '-01')::date,
                       ben.fecha_nacimiento
                   ))::int AS age,
                   pay.monto_prestacion AS monto
            FROM benefits b
            JOIN beneficiaries ben ON b.beneficiary_id = ben.id
            LEFT JOIN payments pay ON pay.beneficiary_id = b.beneficiary_id
                AND pay.program_id = b.program_id AND pay.periodo_mes = b.periodo_mes
            WHERE b.estado_beneficio = 'ACTIVO'
        )
        SELECT periodo_mes,
               CASE
                 WHEN age <= 4  THEN '0-4 años'
                 WHEN age <= 12 THEN '5-12 años'
                 WHEN age <= 17 THEN '13-17 años'
                 WHEN age <= 29 THEN '18-29 años'
                 WHEN age <= 59 THEN '30-59 años'
                 ELSE '60+ años'
               END AS grupo,
               COUNT(DISTINCT beneficiary_id) AS personas,
               COUNT(*) AS beneficios,
               COALESCE(SUM(monto), 0) AS montos
        FROM aged
        GROUP BY periodo_mes,
                 CASE
                   WHEN age <= 4  THEN '0-4 años'
                   WHEN age <= 12 THEN '5-12 años'
                   WHEN age <= 17 THEN '13-17 años'
                   WHEN age <= 29 THEN '18-29 años'
                   WHEN age <= 59 THEN '30-59 años'
                   ELSE '60+ años'
                 END
        """,
        "CREATE UNIQUE INDEX ON mv_by_grupo_etario(periodo_mes, grupo)",
    ),

    # ── mv_evolucion (12 rows) ──
    (
        "mv_evolucion",
        """
        CREATE MATERIALIZED VIEW mv_evolucion AS
        SELECT b.periodo_mes,
               COUNT(DISTINCT b.beneficiary_id) AS personas,
               COUNT(*) AS beneficios,
               COALESCE(SUM(pay.monto_prestacion), 0) AS montos
        FROM benefits b
        LEFT JOIN payments pay ON pay.beneficiary_id = b.beneficiary_id
            AND pay.program_id = b.program_id AND pay.periodo_mes = b.periodo_mes
        WHERE b.estado_beneficio = 'ACTIVO'
        GROUP BY b.periodo_mes
        """,
        "CREATE UNIQUE INDEX ON mv_evolucion(periodo_mes)",
    ),

    # ── mv_cross (multidimensional, max ~43,200 rows) ──
    (
        "mv_cross",
        """
        CREATE MATERIALIZED VIEW mv_cross AS
        WITH base AS (
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
                   b.beneficiary_id,
                   pay.monto_prestacion
            FROM benefits b
            JOIN programs p ON b.program_id = p.id
            JOIN beneficiaries ben ON b.beneficiary_id = ben.id
            LEFT JOIN payments pay ON pay.beneficiary_id = b.beneficiary_id
                AND pay.program_id = b.program_id AND pay.periodo_mes = b.periodo_mes
            WHERE b.estado_beneficio = 'ACTIVO'
        )
        SELECT periodo_mes, nombre_programa, secretaria_origen,
               provincia, sexo, grupo_etario, sexo_label,
               COUNT(DISTINCT beneficiary_id) AS personas,
               COUNT(*) AS beneficios,
               COALESCE(SUM(monto_prestacion), 0) AS montos
        FROM base
        GROUP BY periodo_mes, nombre_programa, secretaria_origen,
                 provincia, sexo, grupo_etario, sexo_label
        """,
        [
            "CREATE UNIQUE INDEX ON mv_cross(periodo_mes, nombre_programa, secretaria_origen, provincia, sexo, grupo_etario)",
            "CREATE INDEX ON mv_cross(periodo_mes)",
            "CREATE INDEX ON mv_cross(periodo_mes, nombre_programa)",
            "CREATE INDEX ON mv_cross(periodo_mes, provincia)",
            "CREATE INDEX ON mv_cross(periodo_mes, secretaria_origen)",
        ],
    ),
]

# ── Refresh function (PostgreSQL stored procedure) ──
REFRESH_FUNCTION = """
CREATE OR REPLACE FUNCTION refresh_all_matviews() RETURNS void AS $$
BEGIN
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_summary;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_pagos_summary;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_concentracion;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_incompatibilidades;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_by_programa;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_by_secretaria;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_by_provincia;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_by_sexo;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_by_grupo_etario;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_evolucion;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_cross;
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

    # Drop existing MVs first
    print("\nLimpiando MVs existentes...")
    mv_names = [mv[0] for mv in MATVIEWS]
    for name in reversed(mv_names):
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
