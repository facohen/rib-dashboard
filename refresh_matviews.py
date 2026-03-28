"""
refresh_matviews.py — Refresca las tablas materializadas (mv_cross + mv_resumen).

Usa patrón blue/green: crea shadow tables, indexa, swap atómico (~1ms downtime).

Ejecutar después de cada carga mensual de datos:
    DATABASE_URL=postgresql://user:pass@host/rib_dev python refresh_matviews.py

También actualiza las tablas lookup (periods, provincias_lookup)
y limpia el cache de Flask si hay un servidor corriendo.
"""
import os
import sys
import time
import psycopg2
import requests

from config import load_dotenv
load_dotenv()

from create_matviews import TABLES, _apply_tuning, _process_period

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL no está seteada. Exportala antes de iniciar:\n"
        "  export DATABASE_URL=postgresql://user:pass@host/rib_dev"
    )
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

    # Session tuning
    _apply_tuning(cur)

    # Get available periods
    cur.execute("SELECT DISTINCT periodo_mes FROM benefits ORDER BY 1")
    periods = [r[0] for r in cur.fetchall()]
    print(f"\nPeríodos encontrados: {len(periods)}")

    # Step 1: Create empty UNLOGGED shadow tables
    print(f"\n{'─' * 60}")
    print("Creando shadow tables...")
    print(f"{'─' * 60}")
    for name, create_ddl, _, _ in TABLES:
        shadow = f"{name}_new"
        cur.execute(f"DROP TABLE IF EXISTS {shadow}")
        shadow_ddl = create_ddl.replace(f"TABLE {name}", f"TABLE {shadow}", 1)
        cur.execute(shadow_ddl)
        print(f"  {shadow} creada")
    conn.commit()

    # Step 2: Process periods — shared temp table feeds both shadow MVs
    print(f"\n{'─' * 60}")
    print("Procesando periodos (temp table compartida → shadow tables)")
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
            cross_table="mv_cross_new",
            resumen_table="mv_resumen_new",
            cross_tc_table="mv_cross_tc_new",
            resumen_tc_table="mv_resumen_tc_new",
            nominal_tc_table="mv_nominal_tc_new",
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

    print(f"\n  mv_cross_new: {total_cross:,} rows total")
    print(f"  mv_resumen_new: {total_resumen:,} rows total")
    print(f"  mv_cross_tc_new: {total_cross_tc:,} rows total")
    print(f"  mv_resumen_tc_new: {total_resumen_tc:,} rows total")
    print(f"  mv_nominal_tc_new: {total_nominal_tc:,} rows total")

    # SET LOGGED + indexes on shadow tables
    for name, _, _, indexes in TABLES:
        shadow = f"{name}_new"
        print(f"\n  SET LOGGED {shadow}...", end=" ", flush=True)
        cur.execute(f"ALTER TABLE {shadow} SET LOGGED")
        conn.commit()
        print("OK")

        if isinstance(indexes, str):
            indexes = [indexes]
        for idx_i, idx_sql in enumerate(indexes, 1):
            print(f"  Índice {idx_i}/{len(indexes)} en {shadow}...", end=" ", flush=True)
            cur.execute(idx_sql.replace(f" {name}(", f" {shadow}(").replace(f" {name} ", f" {shadow} "))
            print("OK")
        conn.commit()

    # Step 3: Atomic swap (~1ms, single transaction)
    print(f"\n{'─' * 60}")
    print("Swap atómico...", end=" ", flush=True)
    t_swap = time.time()
    swap_sql = ""
    for name, _, _, _ in TABLES:
        swap_sql += f"""
        DROP TABLE IF EXISTS {name}_old;
        ALTER TABLE IF EXISTS {name} RENAME TO {name}_old;
        ALTER TABLE {name}_new RENAME TO {name};
        DROP TABLE IF EXISTS {name}_old;
        """
    cur.execute(swap_sql)
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
    analyze_tables = [t[0] for t in TABLES] + ["periods", "provincias_lookup"]
    conn.autocommit = True
    for name in analyze_tables:
        print(f"  {name}...", end=" ", flush=True)
        cur.execute(f"ANALYZE {name}")
        print("OK")

    # Row counts
    print(f"\n{'─' * 60}")
    print("Resultado")
    print(f"{'─' * 60}")
    for name in analyze_tables:
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
