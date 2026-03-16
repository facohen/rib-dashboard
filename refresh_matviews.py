"""
refresh_matviews.py — Refresca las vistas materializadas (mv_cross + mv_cobertura).

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

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost/rub")
APP_URL = os.environ.get("APP_URL", "http://localhost:5000")


def run():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    cur = conn.cursor()

    t0 = time.time()
    print("=" * 60)
    print("Refrescando vistas materializadas")
    print(f"DB: {DATABASE_URL.split('@')[-1] if '@' in DATABASE_URL else DATABASE_URL}")
    print("=" * 60)

    # Refresh all MVs using the stored function
    print("\nRefrescando MVs (CONCURRENTLY)...", flush=True)
    t_mv = time.time()
    cur.execute("SELECT refresh_all_matviews()")
    conn.commit()
    print(f"  OK ({time.time() - t_mv:.1f}s)")

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
    mvs = ["mv_cross", "mv_cobertura", "periods", "provincias_lookup"]
    for mv in mvs:
        cur.execute(f"ANALYZE {mv}")
    print("OK")

    # Row counts
    print("\nRow counts:")
    for mv in mvs:
        cur.execute(f"SELECT COUNT(*) FROM {mv}")
        cnt = cur.fetchone()[0]
        print(f"  {mv}: {cnt:,}")

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
