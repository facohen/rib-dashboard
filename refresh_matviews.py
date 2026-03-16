"""
refresh_matviews.py — Refresca las tablas materializadas (mv_cross + mv_resumen).

Usa patrón blue/green: crea shadow tables, indexa, swap atómico (~1ms downtime).

Ejecutar después de cada carga mensual de datos:
    DATABASE_URL=postgresql://user:pass@host/rub python refresh_matviews.py

También actualiza las tablas lookup (periods, provincias_lookup)
y limpia el cache de Flask si hay un servidor corriendo.
"""
import os
import sys
import time
import psycopg2
import requests

from create_matviews import TABLES

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost/rub")
APP_URL = os.environ.get("APP_URL", "http://localhost:5000")


def run():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    cur = conn.cursor()

    t0 = time.time()
    print("=" * 60)
    print("Refrescando tablas materializadas (blue/green)")
    print(f"DB: {DATABASE_URL.split('@')[-1] if '@' in DATABASE_URL else DATABASE_URL}")
    print("=" * 60)

    # Boost work_mem for heavy aggregation queries
    cur.execute("SET work_mem = '256MB'")
    cur.execute("SET maintenance_work_mem = '512MB'")

    # Step 1: Create shadow tables (long operation, doesn't block readers)
    for name, create_sql, indexes in TABLES:
        t_tbl = time.time()
        print(f"\nCreando shadow {name}_new...", end=" ", flush=True)
        cur.execute(f"DROP TABLE IF EXISTS {name}_new")
        new_sql = create_sql.replace(f"CREATE TABLE {name}", f"CREATE TABLE {name}_new", 1)
        cur.execute(new_sql)
        conn.commit()

        # Step 2: Create indexes on shadow (separate txn)
        if isinstance(indexes, str):
            indexes = [indexes]
        for idx_sql in indexes:
            # Replace table name in index definitions
            cur.execute(idx_sql.replace(f" {name}(", f" {name}_new(").replace(f" {name} ", f" {name}_new "))
        conn.commit()

        cur.execute(f"SELECT COUNT(*) FROM {name}_new")
        row_count = cur.fetchone()[0]
        elapsed = time.time() - t_tbl
        print(f"OK ({row_count:,} rows, {elapsed:.1f}s)")

    # Step 3: Atomic swap (~1ms, single transaction)
    print("\nSwap atómico...", end=" ", flush=True)
    t_swap = time.time()
    cur.execute("""
        DROP TABLE IF EXISTS mv_cross_old;
        ALTER TABLE mv_cross RENAME TO mv_cross_old;
        ALTER TABLE mv_cross_new RENAME TO mv_cross;
        DROP TABLE IF EXISTS mv_cross_old;
        DROP TABLE IF EXISTS mv_resumen_old;
        ALTER TABLE mv_resumen RENAME TO mv_resumen_old;
        ALTER TABLE mv_resumen_new RENAME TO mv_resumen;
        DROP TABLE IF EXISTS mv_resumen_old;
    """)
    conn.commit()
    print(f"OK ({time.time() - t_swap:.3f}s)")

    # Update lookup tables
    print("Actualizando tabla periods...", end=" ", flush=True)
    cur.execute("TRUNCATE periods")
    cur.execute("INSERT INTO periods SELECT DISTINCT periodo_mes FROM benefits")
    conn.commit()
    print("OK")

    print("Actualizando tabla provincias_lookup...", end=" ", flush=True)
    cur.execute("TRUNCATE provincias_lookup")
    cur.execute("INSERT INTO provincias_lookup SELECT DISTINCT provincia FROM beneficiaries")
    conn.commit()
    print("OK")

    # ANALYZE
    print("ANALYZE...", end=" ", flush=True)
    conn.autocommit = True
    for name in ["mv_cross", "mv_resumen", "periods", "provincias_lookup"]:
        cur.execute(f"ANALYZE {name}")
    print("OK")

    # Row counts
    print("\nRow counts:")
    for name in ["mv_cross", "mv_resumen", "periods", "provincias_lookup"]:
        cur.execute(f"SELECT COUNT(*) FROM {name}")
        cnt = cur.fetchone()[0]
        print(f"  {name}: {cnt:,}")

    total_time = time.time() - t0
    print(f"\nRefresh completado en {total_time:.1f}s")

    cur.close()
    conn.close()

    # Clear Flask cache
    print("\nLimpiando cache de Flask...", end=" ", flush=True)
    try:
        r = requests.post(f"{APP_URL}/api/admin/clear-cache",
                          cookies={"session": "admin"}, timeout=5)
        if r.status_code == 200:
            print("OK")
        else:
            print(f"HTTP {r.status_code} (el cache se limpiara por TTL)")
    except Exception:
        print("servidor no disponible (el cache se limpiara por TTL)")

    print("=" * 60)


if __name__ == "__main__":
    run()
