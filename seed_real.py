"""
seed_real.py — Inicializa schema para carga de datos reales.

Crea tablas, indices, usuarios demo. No genera datos sinteticos.
Despues de ejecutar, usar ingest.py opcion 3 para cargar CSVs.

Uso:
    python seed_real.py
"""
from db_schema import get_conn, init_schema


def run():
    print("=" * 50)
    print("  RIB -- Inicializacion para datos reales")
    print("=" * 50)
    conn = get_conn()
    init_schema(conn)
    conn.close()
    print("\n  Listo. Usar ingest.py opcion 3 para cargar CSVs.")
    print("=" * 50)


if __name__ == "__main__":
    run()
