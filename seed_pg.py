"""
seed_pg.py — Generador de datos masivos para PostgreSQL
Genera 8M beneficiarios, ~200M benefits, pagos correspondientes.

Uso:
    python seed_pg.py              # 8M (default)
    python seed_pg.py --small      # 10K para test rápido
    python seed_pg.py --medium     # 100K
    python seed_pg.py --1m         # 1M registros

Optimizado con numpy vectorizado + COPY para máxima velocidad.
"""
import io
import os
import sys
import time

import numpy as np
import psycopg2
from werkzeug.security import generate_password_hash

from config import load_dotenv
load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL no está seteada. Exportala antes de iniciar:\n"
        "  export DATABASE_URL=postgresql://user:pass@host/rub"
    )

# ──────────────────────────────────────────────
# Datos de referencia
# ──────────────────────────────────────────────

PROVINCIAS = [
    ("Buenos Aires", "06", [("La Matanza","06427"),("Lomas de Zamora","06490"),("Quilmes","06658"),("General Pueyrredón","06357"),("Almirante Brown","06028"),("Moreno","06560"),("Merlo","06539"),("Lanús","06427"),("Florencio Varela","06274"),("Tigre","06840")]),
    ("Córdoba",      "14", [("Capital","14014"),("Río Cuarto","14147"),("San Justo","14175"),("Punilla","14133"),("Colón","14021")]),
    ("Santa Fe",     "82", [("Rosario","82119"),("La Capital","82070"),("Rafaela","82075"),("Caseros","82021"),("General López","82049")]),
    ("Mendoza",      "50", [("Capital","50028"),("Godoy Cruz","50049"),("Luján de Cuyo","50063"),("Guaymallén","50056"),("San Rafael","50098")]),
    ("Tucumán",      "90", [("Capital","90021"),("Yerba Buena","90126"),("Cruz Alta","90028"),("Lules","90063")]),
    ("Salta",        "66", [("Capital","66014"),("Orán","66077"),("San Martín","66091"),("Tartagal","66098")]),
    ("Chaco",        "22", [("San Fernando","22007"),("Comandante Fernández","22042"),("Libertador Gral. San Martín","22063")]),
    ("Corrientes",   "18", [("Capital","18021"),("Goya","18098"),("Mercedes","18112")]),
    ("Misiones",     "54", [("Capital","54028"),("Eldorado","54035"),("Oberá","54063")]),
    ("Jujuy",        "38", [("Dr. Manuel Belgrano","38007"),("Palpalá","38049"),("Ledesma","38042")]),
    ("Entre Ríos",   "30", [("Paraná","30084"),("Concordia","30021"),("Gualeguaychú","30042")]),
    ("Santiago del Estero", "86", [("Capital","86007"),("Banda","86014"),("Robles","86098")]),
    ("San Juan",     "70", [("Capital","70007"),("Rawson","70084"),("Rivadavia","70091")]),
    ("San Luis",     "74", [("La Capital","74014"),("Pedernera","74049")]),
    ("Formosa",      "34", [("Formosa","34007"),("Pilcomayo","34049")]),
    ("Catamarca",    "10", [("Capital","10007"),("Valle Viejo","10098")]),
    ("La Rioja",     "46", [("Capital","46007"),("Chilecito","46021")]),
    ("Neuquén",      "58", [("Confluencia","58014"),("Zapala","58098")]),
    ("Río Negro",    "62", [("General Roca","62042"),("Bariloche","62007")]),
    ("Chubut",       "26", [("Rawson","26042"),("Escalante","26014")]),
    ("Santa Cruz",   "78", [("Güer Aike","78007"),("Deseado","78014")]),
    ("La Pampa",     "42", [("Capital","42007"),("Maracó","42049")]),
    ("Tierra del Fuego", "94", [("Ushuaia","94007"),("Río Grande","94014")]),
]

PROV_WEIGHTS = [38, 8, 8, 4, 4, 3, 3, 2, 3, 2, 3, 2, 2, 1, 1, 1, 1, 1, 2, 1, 1, 1, 0.5]

SECRETARIAS = [
    "Secretaría de Inclusión Social",
    "Secretaría de Desarrollo Humano",
    "Secretaría de Economía Social",
]

PROGRAMAS = [
    ("Asignación Universal por Hijo", 0),
    ("Becas PROGRESAR", 0),
    ("Tarjeta Alimentar", 0),
    ("Pensiones no Contributivas", 1),
    ("SUMAR – Salud", 1),
    ("Hacemos Futuro", 1),
    ("Crédito ARGENTA", 2),
    ("Plan Potenciar Trabajo", 2),
]

NOMBRES_M = ["Carlos","Juan","Diego","Martín","Luis","Pedro","Roberto","Sergio","Mario","Daniel",
             "Miguel","Gustavo","Alejandro","Ricardo","Fernando","Ezequiel","Facundo","Gabriel",
             "Héctor","Ignacio","Pablo","Nicolás","Matías","Tomás","Agustín","Santiago","Ramón",
             "Oscar","Rubén","Eduardo","Marcelo","Leonardo","Adrián","Darío","Emilio","Javier"]
NOMBRES_F = ["María","Ana","Laura","Sandra","Patricia","Claudia","Carolina","Gabriela","Valeria",
             "Marcela","Natalia","Silvana","Andrea","Mónica","Verónica","Lorena","Débora","Alejandra",
             "Viviana","Cecilia","Rosa","Marta","Soledad","Florencia","Julieta","Romina","Daniela",
             "Paola","Karina","Silvia","Norma","Liliana","Teresa","Graciela","Stella","Miriam"]
APELLIDOS = ["García","Rodríguez","González","Fernández","López","Martínez","Sánchez","Pérez",
             "Gómez","Díaz","Hernández","Castro","Romero","Torres","Alvarez","Ruiz","Ramírez",
             "Flores","Acosta","Medina","Suárez","Ramos","Molina","Moreno","Ortega","Silva",
             "Ponce","Vera","Rosa","Reyes","Cabrera","Aguirre","Navarro","Figueroa","Quiroga",
             "Mansilla","Paz","Benítez","Miranda","Ríos","Sosa","Villalba","Vega","Bravo","Luna"]

PERIODOS = ["2025-04","2025-05","2025-06","2025-07","2025-08","2025-09",
            "2025-10","2025-11","2025-12","2026-01","2026-02","2026-03"]

INCOMP_PAIRS = [(0,3),(1,7),(2,5),(6,7)]

RANDOM_SEED = 42


def hash_pw(pw):
    return generate_password_hash(pw)


def _cuil_array(n):
    """Generate n CUILs as numpy string array."""
    prefixes = np.array(["20","23","24","27"])
    nums = np.arange(20000000, 20000000 + n)
    prefix = prefixes[np.arange(n) % 4]
    check = (np.arange(n) % 10).astype(str)
    return np.char.add(np.char.add(prefix, nums.astype(str)), check)


def _copy_buf(cur, table, columns, buf):
    """COPY from StringIO buffer."""
    buf.seek(0)
    cur.copy_expert(
        f"COPY {table}({','.join(columns)}) FROM STDIN WITH (FORMAT csv, NULL '\\N')",
        buf
    )


# ──────────────────────────────────────────────
# Schema
# ──────────────────────────────────────────────

SCHEMA = """
CREATE EXTENSION IF NOT EXISTS pg_trgm;

DROP TABLE IF EXISTS payments CASCADE;
DROP TABLE IF EXISTS benefits CASCADE;
DROP TABLE IF EXISTS incompatibility_rules CASCADE;
DROP TABLE IF EXISTS programs CASCADE;
DROP TABLE IF EXISTS beneficiaries CASCADE;
DROP TABLE IF EXISTS users CASCADE;

CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    nombre TEXT
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
    secretaria_origen TEXT NOT NULL,
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

INDEXES = """
CREATE INDEX idx_benefits_period_state_benid ON benefits(periodo_mes, estado_beneficio, beneficiary_id);
CREATE INDEX idx_benefits_period_state_cuil ON benefits(periodo_mes, estado_beneficio, cuil_raw);
CREATE INDEX idx_benefits_program_period ON benefits(program_id, periodo_mes, estado_beneficio, beneficiary_id);
CREATE INDEX idx_benefits_benid ON benefits(beneficiary_id);
CREATE INDEX idx_benefits_active_benid ON benefits(beneficiary_id, program_id, periodo_mes) WHERE estado_beneficio = 'ACTIVO';
CREATE INDEX idx_ben_provincia ON beneficiaries(provincia);
CREATE INDEX idx_ben_apellido_nombre ON beneficiaries(apellido, nombre);
CREATE INDEX idx_ben_cuil ON beneficiaries(cuil);
CREATE INDEX idx_ben_departamento ON beneficiaries(departamento);
CREATE INDEX idx_ben_sexo ON beneficiaries(sexo);
CREATE INDEX idx_ben_fecha_nacimiento ON beneficiaries(fecha_nacimiento);
CREATE INDEX idx_payments_period ON payments(periodo_mes);
CREATE INDEX idx_payments_compound ON payments(beneficiary_id, program_id, periodo_mes);
CREATE INDEX idx_ben_cuil_trgm ON beneficiaries USING gin(cuil gin_trgm_ops);
"""


def run(num_beneficiaries=8_000_000):
    rng = np.random.default_rng(RANDOM_SEED)

    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    cur = conn.cursor()

    t0 = time.time()
    n = num_beneficiaries
    print(f"🏗️  Generando {n:,} beneficiarios en PostgreSQL...")
    print(f"   URL: {DATABASE_URL.split('@')[-1] if '@' in DATABASE_URL else DATABASE_URL}")
    print(f"   Random seed: {RANDOM_SEED}")

    # ── Schema ──
    print("\n📋 Creando schema...")
    cur.execute(SCHEMA)
    conn.commit()

    print("🗑️  Truncando tablas...")
    cur.execute("TRUNCATE payments, benefits, incompatibility_rules, programs, beneficiaries, users RESTART IDENTITY CASCADE")
    conn.commit()

    # ── Usuarios ──
    cur.execute("INSERT INTO users(email,password_hash,role,nombre) VALUES(%s,%s,%s,%s)",
                ("admin@demo.local", hash_pw("Demo123!"), "admin", "Administrador RUB"))
    cur.execute("INSERT INTO users(email,password_hash,role,nombre) VALUES(%s,%s,%s,%s)",
                ("user@demo.local", hash_pw("Demo123!"), "user", "Analista"))
    conn.commit()
    print("  ✅ Usuarios creados")

    # ── Programas ──
    prog_ids = []
    for nombre, sec_idx in PROGRAMAS:
        cur.execute("INSERT INTO programs(secretaria_origen,nombre_programa) VALUES(%s,%s) RETURNING id",
                    (SECRETARIAS[sec_idx], nombre))
        prog_ids.append(cur.fetchone()[0])
    conn.commit()
    n_progs = len(prog_ids)
    prog_ids_arr = np.array(prog_ids)
    print(f"  ✅ Programas creados: {n_progs}")

    # Base montos por secretaría: [80K, 95K, 60K]
    prog_base_monto = np.array([80000, 80000, 80000, 95000, 95000, 95000, 60000, 60000])

    # ── Incompatibilidades ──
    for a, b in INCOMP_PAIRS:
        cur.execute("""INSERT INTO incompatibility_rules(program_a_id,program_b_id,is_compatible,descripcion)
                       VALUES(%s,%s,0,%s)""",
                    (prog_ids[a], prog_ids[b],
                     f"{PROGRAMAS[a][0]} incompatible con {PROGRAMAS[b][0]}"))
    conn.commit()
    print(f"  ✅ Reglas de incompatibilidad: {len(INCOMP_PAIRS)}")

    # ── Beneficiarios (vectorizado) ──
    print(f"\n👥 Generando {n:,} beneficiarios...")
    t_ben = time.time()

    # Pre-flatten province/department data for vectorized indexing
    prov_names = []
    prov_codes = []
    depto_names = []
    depto_codes = []
    prov_depto_offsets = []  # (start, count) for each province's departments
    flat_deptos = []
    for prov_name, prov_code, deptos in PROVINCIAS:
        start = len(flat_deptos)
        for d_name, d_code in deptos:
            flat_deptos.append((prov_name, prov_code, d_name, d_code))
        prov_depto_offsets.append((start, len(deptos)))

    # Build per-province probability → flat depto probability
    total_w = sum(PROV_WEIGHTS)
    flat_probs = []
    for i, (start, count) in enumerate(prov_depto_offsets):
        p = PROV_WEIGHTS[i] / total_w / count
        flat_probs.extend([p] * count)
    flat_probs = np.array(flat_probs)
    flat_probs /= flat_probs.sum()  # normalize

    depto_idx = rng.choice(len(flat_deptos), size=n, p=flat_probs)

    # Extract province/depto arrays
    flat_prov_names = np.array([fd[0] for fd in flat_deptos])
    flat_prov_codes = np.array([fd[1] for fd in flat_deptos])
    flat_depto_names = np.array([fd[2] for fd in flat_deptos])
    flat_depto_codes = np.array([fd[3] for fd in flat_deptos])

    ben_provincia = flat_prov_names[depto_idx]
    ben_cod_prov = flat_prov_codes[depto_idx]
    ben_depto = flat_depto_names[depto_idx]
    ben_cod_depto = flat_depto_codes[depto_idx]

    # Sexo: M 30%, F 50%, X 10%, NI 10%
    sexo_pool = np.array(["M","M","M","F","F","F","F","F","X","NI"])
    ben_sexo = sexo_pool[rng.integers(0, len(sexo_pool), size=n)]

    # Age groups: niñez 20%, jóvenes 30%, adultos 35%, mayores 15%
    age_groups = rng.choice(4, size=n, p=[0.20, 0.30, 0.35, 0.15])
    age_ranges = [(0, 12), (13, 29), (30, 59), (60, 85)]
    base_date = np.datetime64('2026-03-31')
    years = np.empty(n, dtype=np.int32)
    for g in range(4):
        mask = age_groups == g
        lo, hi = age_ranges[g]
        years[mask] = rng.integers(lo, hi + 1, size=mask.sum())
    months = rng.integers(1, 13, size=n)
    days = rng.integers(1, 29, size=n)
    # Build date strings YYYY-MM-DD
    y_part = (2026 - years).astype(str)
    m_part = np.char.zfill(months.astype(str), 2)
    d_part = np.char.zfill(days.astype(str), 2)
    ben_fecha = np.char.add(np.char.add(np.char.add(np.char.add(y_part, '-'), m_part), '-'), d_part)

    # Names
    all_nombres_m = np.array(NOMBRES_M)
    all_nombres_f = np.array(NOMBRES_F)
    all_nombres_all = np.array(NOMBRES_M + NOMBRES_F)
    all_apellidos = np.array(APELLIDOS)

    ben_nombre = np.empty(n, dtype='U20')
    mask_m = ben_sexo == 'M'
    mask_f = ben_sexo == 'F'
    mask_other = ~mask_m & ~mask_f
    ben_nombre[mask_m] = all_nombres_m[rng.integers(0, len(all_nombres_m), size=mask_m.sum())]
    ben_nombre[mask_f] = all_nombres_f[rng.integers(0, len(all_nombres_f), size=mask_f.sum())]
    ben_nombre[mask_other] = all_nombres_all[rng.integers(0, len(all_nombres_all), size=mask_other.sum())]
    ben_apellido = all_apellidos[rng.integers(0, len(all_apellidos), size=n)]

    # CUILs and CPs
    ben_cuil = _cuil_array(n)
    ben_cp = (1000 + rng.integers(0, 9000, size=n)).astype(str)

    # Write via COPY
    print(f"  Insertando via COPY...", end=" ", flush=True)
    buf = io.StringIO()
    for i in range(n):
        buf.write(f"{ben_cuil[i]},{ben_nombre[i]},{ben_apellido[i]},{ben_sexo[i]},"
                  f"{ben_fecha[i]},{ben_provincia[i]},{ben_cod_prov[i]},"
                  f"{ben_depto[i]},{ben_cod_depto[i]},{ben_cp[i]}\n")
    _copy_buf(cur, "beneficiaries",
              ["cuil","nombre","apellido","sexo","fecha_nacimiento",
               "provincia","codigo_provincia_indec","departamento",
               "codigo_departamento_indec","cp"], buf)
    conn.commit()
    buf.close()

    # Get first beneficiary ID
    cur.execute("SELECT MIN(id) FROM beneficiaries")
    first_ben_id = cur.fetchone()[0]

    print(f"OK ({n:,} rows, {time.time()-t_ben:.0f}s)")

    # ── Benefits + Payments per period (vectorized) ──
    # Pre-compute per-beneficiary program assignments
    # Concentración: 50% 1 prog, 30% 2 progs, 20% 3+ progs
    conc_r = rng.random(n)
    cant_progs = np.where(conc_r < 0.50, 1, np.where(conc_r < 0.80, 2, 3))

    # 5% inválidos (no beneficiary_id)
    num_invalidos = int(n * 0.05)
    is_invalido = np.zeros(n, dtype=bool)
    is_invalido[n - num_invalidos:] = True

    # Force incompatibilities for ~2% of valid beneficiaries
    force_incomp = rng.random(n) < 0.02
    force_incomp[is_invalido] = False

    for periodo in PERIODOS:
        t_per = time.time()
        y, m = int(periodo[:4]), int(periodo[5:])
        print(f"\n📅 Periodo {periodo}...", end=" ", flush=True)

        ben_buf = io.StringIO()
        pay_buf = io.StringIO()
        benefits_count = 0
        payments_count = 0

        # Pre-generate random estado (8% INACTIVO)
        # Max possible benefits: ~1.64 per person on average, but we generate per-person
        # So we batch generate enough random numbers

        for i in range(n):
            ben_id = first_ben_id + i

            if is_invalido[i]:
                prog = prog_ids_arr[rng.integers(0, n_progs)]
                cuil_raw = "\\N" if rng.random() > 0.5 else "00000000000"
                ben_buf.write(f"\\N,{cuil_raw},{prog},{periodo},ACTIVO\n")
                benefits_count += 1
                continue

            cant = int(cant_progs[i])

            if force_incomp[i]:
                pair_idx = rng.integers(0, len(INCOMP_PAIRS))
                a, b = INCOMP_PAIRS[pair_idx]
                progs = [prog_ids[a], prog_ids[b]]
                if cant >= 3:
                    extras = [p for p in prog_ids if p not in progs]
                    progs.append(extras[rng.integers(0, len(extras))])
            else:
                perm = rng.permutation(n_progs)
                progs = prog_ids_arr[perm[:cant]].tolist()

            cuil_str = ben_cuil[i]
            for pid in progs:
                estado = "INACTIVO" if rng.random() < 0.08 else "ACTIVO"
                ben_buf.write(f"{ben_id},{cuil_str},{pid},{periodo},{estado}\n")
                benefits_count += 1

                if estado == "ACTIVO":
                    pidx = pid - prog_ids[0]  # IDs are sequential
                    base = prog_base_monto[pidx]
                    monto = base + rng.integers(-10000, 20001)
                    day = rng.integers(1, 29)
                    pay_buf.write(f"{ben_id},{pid},{y}-{m:02d}-{day:02d},{periodo},{monto}\n")
                    payments_count += 1

            if i > 0 and i % 500_000 == 0:
                # Flush intermediate to avoid huge memory
                _copy_buf(cur, "benefits",
                          ["beneficiary_id","cuil_raw","program_id","periodo_mes","estado_beneficio"],
                          ben_buf)
                _copy_buf(cur, "payments",
                          ["beneficiary_id","program_id","fecha_pago","periodo_mes","monto_prestacion"],
                          pay_buf)
                conn.commit()
                ben_buf.close()
                pay_buf.close()
                ben_buf = io.StringIO()
                pay_buf = io.StringIO()
                elapsed = time.time() - t_per
                rate = (i + 1) / elapsed
                print(f"{i+1:,} [{rate:,.0f}/s]", end=" ", flush=True)

        # Flush remaining
        _copy_buf(cur, "benefits",
                  ["beneficiary_id","cuil_raw","program_id","periodo_mes","estado_beneficio"],
                  ben_buf)
        _copy_buf(cur, "payments",
                  ["beneficiary_id","program_id","fecha_pago","periodo_mes","monto_prestacion"],
                  pay_buf)
        conn.commit()
        ben_buf.close()
        pay_buf.close()

        per_time = time.time() - t_per
        print(f"✅ {benefits_count:,} benefits, {payments_count:,} pagos ({per_time:.0f}s)")

    # ── Índices ──
    print("\n📊 Creando índices...")
    t_idx = time.time()
    for idx_sql in INDEXES.strip().split("\n"):
        idx_sql = idx_sql.strip()
        if idx_sql:
            print(f"  {idx_sql[:60]}...")
            cur.execute(idx_sql)
            conn.commit()
    print(f"  ✅ Índices creados ({time.time()-t_idx:.0f}s)")

    # ── ANALYZE ──
    print("\n🔍 ANALYZE...")
    conn.autocommit = True
    cur.execute("ANALYZE")
    conn.autocommit = False

    # ── Resumen ──
    cur.execute("SELECT COUNT(*) FROM beneficiaries")
    nb = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM benefits")
    nben = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM payments")
    npay = cur.fetchone()[0]
    total_time = time.time() - t0

    print(f"\n{'='*50}")
    print(f"🎉 Seed completado en {total_time/60:.1f} minutos")
    print(f"   Beneficiarios: {nb:,}")
    print(f"   Benefits:      {nben:,}")
    print(f"   Pagos:         {npay:,}")
    print(f"{'='*50}")

    cur.close()
    conn.close()


def schema_only():
    """Crea schema, índices y usuarios demo sin generar beneficiarios."""
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    cur = conn.cursor()

    print("📋 Creando schema...")
    cur.execute(SCHEMA)
    conn.commit()

    print("📊 Creando índices...")
    for idx_sql in INDEXES.strip().split("\n"):
        idx_sql = idx_sql.strip()
        if idx_sql:
            cur.execute(idx_sql)
            conn.commit()

    print("👤 Creando usuarios demo...")
    cur.execute("INSERT INTO users(email,password_hash,role,nombre) VALUES(%s,%s,%s,%s)",
                ("admin@demo.local", hash_pw("Demo123!"), "admin", "Administrador RUB"))
    cur.execute("INSERT INTO users(email,password_hash,role,nombre) VALUES(%s,%s,%s,%s)",
                ("user@demo.local", hash_pw("Demo123!"), "user", "Analista"))
    conn.commit()

    print("✅ Schema inicializado (sin datos de beneficiarios)")
    cur.close()
    conn.close()


if __name__ == "__main__":
    if "--schema-only" in sys.argv:
        schema_only()
    elif "--small" in sys.argv:
        run(num_beneficiaries=10_000)
    elif "--medium" in sys.argv:
        run(num_beneficiaries=100_000)
    elif "--1m" in sys.argv:
        run(num_beneficiaries=1_000_000)
    else:
        run(num_beneficiaries=8_000_000)
