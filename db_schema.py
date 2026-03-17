"""
db_schema.py — Schema DDL, indices y usuarios demo para RUB Dashboard.

Modulo compartido por seed_sintetico.py y seed_real.py.
"""
import os
import psycopg2
from werkzeug.security import generate_password_hash

from config import load_dotenv
load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL no seteada. export DATABASE_URL=postgresql://user:pass@host/rub")


SCHEMA = """
CREATE EXTENSION IF NOT EXISTS pg_trgm;

DROP TABLE IF EXISTS payments CASCADE;
DROP TABLE IF EXISTS benefits CASCADE;
DROP TABLE IF EXISTS incompatibility_rules CASCADE;
DROP TABLE IF EXISTS programs CASCADE;
DROP TABLE IF EXISTS secretarias CASCADE;
DROP TABLE IF EXISTS beneficiaries CASCADE;
DROP TABLE IF EXISTS users CASCADE;

CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    nombre TEXT
);

CREATE TABLE secretarias (
    id SERIAL PRIMARY KEY,
    nombre TEXT UNIQUE NOT NULL
);

CREATE TABLE beneficiaries (
    id SERIAL PRIMARY KEY,
    cuil TEXT UNIQUE NOT NULL,
    nombre TEXT NOT NULL,
    apellido TEXT NOT NULL,
    sexo TEXT NOT NULL,
    fecha_nacimiento DATE NOT NULL,
    provincia TEXT NOT NULL,
    codigo_provincia_indec TEXT NOT NULL,
    departamento TEXT NOT NULL,
    codigo_departamento_indec TEXT NOT NULL,
    cp TEXT
);

CREATE TABLE programs (
    id SERIAL PRIMARY KEY,
    secretaria_id INTEGER NOT NULL REFERENCES secretarias(id),
    nombre_programa TEXT NOT NULL
);

CREATE TABLE benefits (
    id SERIAL PRIMARY KEY,
    beneficiary_id INTEGER REFERENCES beneficiaries(id),
    cuil_raw TEXT,
    program_id INTEGER NOT NULL REFERENCES programs(id),
    periodo_mes TEXT NOT NULL,
    estado_beneficio TEXT NOT NULL DEFAULT 'ACTIVO'
);

CREATE TABLE payments (
    id SERIAL PRIMARY KEY,
    beneficiary_id INTEGER NOT NULL REFERENCES beneficiaries(id),
    program_id INTEGER NOT NULL REFERENCES programs(id),
    fecha_pago DATE NOT NULL,
    periodo_mes TEXT NOT NULL,
    monto_prestacion NUMERIC(12,2) NOT NULL
);

CREATE TABLE incompatibility_rules (
    id SERIAL PRIMARY KEY,
    program_a_id INTEGER NOT NULL REFERENCES programs(id),
    program_b_id INTEGER NOT NULL REFERENCES programs(id),
    is_compatible INTEGER NOT NULL DEFAULT 0,
    descripcion TEXT
);
"""

INDEXES = [
    "CREATE INDEX idx_benefits_periodo_benid_progid ON benefits(periodo_mes, beneficiary_id, program_id)",
    "CREATE INDEX idx_benefits_period_state_benid ON benefits(periodo_mes, estado_beneficio, beneficiary_id)",
    "CREATE INDEX idx_benefits_benid ON benefits(beneficiary_id)",
    "CREATE INDEX idx_ben_apellido_nombre ON beneficiaries(apellido, nombre)",
    "CREATE INDEX idx_ben_cuil ON beneficiaries(cuil)",
    "CREATE INDEX idx_payments_covering ON payments(periodo_mes, beneficiary_id, program_id) INCLUDE (monto_prestacion)",
    "CREATE INDEX idx_ben_cuil_trgm ON beneficiaries USING gin(cuil gin_trgm_ops)",
]

DEMO_USERS = [
    ("admin@demo.local", "Demo123!", "admin", "Administrador RUB"),
    ("user@demo.local",  "Demo123!", "user",  "Analista"),
]


def get_conn():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    return conn


def create_schema(conn):
    """Crea todas las tablas (DROP + CREATE)."""
    cur = conn.cursor()
    cur.execute(SCHEMA)
    conn.commit()
    cur.close()
    print("  Schema creado")


def create_indexes(conn):
    """Crea indices de performance."""
    cur = conn.cursor()
    for idx in INDEXES:
        cur.execute(idx)
        conn.commit()
    cur.close()
    print(f"  {len(INDEXES)} indices creados")


def create_users(conn):
    """Crea usuarios demo."""
    cur = conn.cursor()
    for email, pw, role, nombre in DEMO_USERS:
        cur.execute("INSERT INTO users(email, password_hash, role, nombre) VALUES(%s,%s,%s,%s)",
                    (email, generate_password_hash(pw), role, nombre))
    conn.commit()
    cur.close()
    print(f"  {len(DEMO_USERS)} usuarios creados")


def init_schema(conn=None):
    """Setup completo: schema + indices + usuarios."""
    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    create_schema(conn)
    create_indexes(conn)
    create_users(conn)
    if own_conn:
        conn.close()
    print("  Schema inicializado")
