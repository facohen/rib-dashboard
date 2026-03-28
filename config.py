"""
config.py — Conexión PostgreSQL con connection pooling + helpers
Único lugar para cambiar conexión o mappings de tablas/campos.

PostgreSQL tuning recomendado (para 32GB RAM compartidos, SSD):
  shared_buffers = 4GB
  work_mem = 32MB             # per-sort per-query per-conn; usar SET LOCAL para queries pesadas
  maintenance_work_mem = 1GB  # para MV refresh e index builds
  effective_cache_size = 16GB
  random_page_cost = 1.1
"""
import os
import threading
import psycopg2
import psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool

def load_dotenv():
    """Load .env file if present (so DATABASE_URL works without manual export)."""
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.isfile(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL no está seteada. Exportala antes de iniciar:\n"
        "  export DATABASE_URL=postgresql://user:pass@host/rib_dev"
    )

# Agregar sslmode=prefer si no viene en el connection string
if "sslmode" not in DATABASE_URL:
    DATABASE_URL += ("&" if "?" in DATABASE_URL else "?") + "sslmode=prefer"

# Connection pool: min 2, max 10 connections (per Gunicorn worker process)
_pool = None
_pool_lock = threading.Lock()


def _get_pool():
    global _pool
    if _pool is None or _pool.closed:
        with _pool_lock:
            if _pool is None or _pool.closed:
                _pool = ThreadedConnectionPool(2, 10, DATABASE_URL)
    return _pool


def get_connection():
    conn = _get_pool().getconn()
    try:
        conn.rollback()
    except Exception:
        pass
    conn.autocommit = False
    return conn


def put_connection(conn):
    """Return connection to pool instead of closing it."""
    try:
        conn.rollback()
        _get_pool().putconn(conn)
    except Exception:
        try:
            conn.close()
        except Exception:
            pass


def close_pool():
    global _pool
    if _pool and not _pool.closed:
        _pool.closeall()
        _pool = None


def query(conn, sql, params=()):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def query_one(conn, sql, params=()):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def execute(conn, sql, params=()):
    with conn.cursor() as cur:
        cur.execute(sql, params)
    conn.commit()
