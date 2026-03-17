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

    # Boost memory for heavy aggregation queries
    cur.execute("SET work_mem = '1GB'")
    cur.execute("SET maintenance_work_mem = '2GB'")
    cur.execute("SET max_parallel_workers_per_gather = 8")

    # Get available periods
    cur.execute("SELECT DISTINCT periodo_mes FROM benefits ORDER BY 1")
    periods = [r[0] for r in cur.fetchall()]
    print(f"\nPeríodos encontrados: {len(periods)}")

    # Step 1: Create shadow tables period by period (doesn't block readers)
    for tbl_idx, (name, create_ddl, insert_sql, indexes) in enumerate(TABLES, 1):
        t_tbl = time.time()
        shadow = f"{name}_new"
        print(f"\n{'─' * 60}")
        print(f"[{tbl_idx}/{len(TABLES)}] Creando shadow {shadow}...")
        print(f"{'─' * 60}")

        # Create empty UNLOGGED shadow table
        cur.execute(f"DROP TABLE IF EXISTS {shadow}")
        shadow_ddl = create_ddl.replace(f"TABLE {name}", f"TABLE {shadow}", 1)
        cur.execute(shadow_ddl)
        conn.commit()

        # Insert period by period
        shadow_insert = insert_sql.replace(f"INTO {name}", f"INTO {shadow}", 1)
        n_params = shadow_insert.count('%s')
        total_rows = 0
        for i, p in enumerate(periods, 1):
            t_p = time.time()
            cur.execute(shadow_insert, [p] * n_params)
            conn.commit()
            rows = cur.rowcount
            total_rows += rows
            elapsed_p = time.time() - t_p
            bar = "█" * int(i / len(periods) * 30)
            bar += "░" * (30 - len(bar))
            print(f"  {bar} {i}/{len(periods)} │ {p} │ {rows:>8,} rows │ {elapsed_p:>5.1f}s")

        # Convert to logged and create indexes on shadow
        print(f"  SET LOGGED...", end=" ", flush=True)
        cur.execute(f"ALTER TABLE {shadow} SET LOGGED")
        conn.commit()
        print("OK")

        if isinstance(indexes, str):
            indexes = [indexes]
        for idx_i, idx_sql in enumerate(indexes, 1):
            print(f"  Índice {idx_i}/{len(indexes)}...", end=" ", flush=True)
            cur.execute(idx_sql.replace(f" {name}(", f" {shadow}(").replace(f" {name} ", f" {shadow} "))
            print("OK")
        conn.commit()

        elapsed = time.time() - t_tbl
        print(f"  ✓ {shadow}: {total_rows:,} rows en {elapsed:.1f}s")

    # Step 3: Atomic swap (~1ms, single transaction)
    print(f"\n{'─' * 60}")
    print("Swap atómico...", end=" ", flush=True)
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
    print(f"{'─' * 60}")
    print("ANALYZE")
    print(f"{'─' * 60}")
    conn.autocommit = True
    for name in ["mv_cross", "mv_resumen", "periods", "provincias_lookup"]:
        print(f"  {name}...", end=" ", flush=True)
        cur.execute(f"ANALYZE {name}")
        print("OK")

    # Row counts
    print(f"\n{'─' * 60}")
    print("Resultado")
    print(f"{'─' * 60}")
    for name in ["mv_cross", "mv_resumen", "periods", "provincias_lookup"]:
        cur.execute(f"SELECT COUNT(*) FROM {name}")
        cnt = cur.fetchone()[0]
        print(f"  {name}: {cnt:,} rows")

    total_time = time.time() - t0
    mins = int(total_time // 60)
    secs = total_time % 60
    print(f"\n{'=' * 60}")
    print(f"Refresh completado en {mins}m {secs:.0f}s")
    print(f"{'=' * 60}")

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
