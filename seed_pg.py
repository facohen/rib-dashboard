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
        "  export DATABASE_URL=postgresql://user:pass@host/rib_dev"
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


def _cuil_array(n, start=20_000_000):
    """Generate n CUILs as numpy string array."""
    prefixes = np.array(["20","23","24","27"])
    nums = np.arange(start, start + n)
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
    estado_beneficio TEXT NOT NULL DEFAULT 'ACTIVO',
    cuil_titular TEXT,
    nombre_titular TEXT,
    apellido_titular TEXT,
    sexo_titular TEXT,
    fecha_nacimiento_titular DATE,
    provincia_titular TEXT
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
CREATE INDEX idx_benefits_periodo_benid_progid ON benefits(periodo_mes, beneficiary_id, program_id);
CREATE INDEX idx_benefits_period_state_benid ON benefits(periodo_mes, estado_beneficio, beneficiary_id);
CREATE INDEX idx_benefits_benid ON benefits(beneficiary_id);
CREATE INDEX idx_ben_apellido_nombre ON beneficiaries(apellido, nombre);
CREATE INDEX idx_ben_cuil ON beneficiaries(cuil);
CREATE INDEX idx_payments_covering ON payments(periodo_mes, beneficiary_id, program_id) INCLUDE (monto_prestacion);
CREATE INDEX idx_ben_cuil_trgm ON beneficiaries USING gin(cuil gin_trgm_ops);
CREATE INDEX idx_benefits_periodo_cuil_titular ON benefits(periodo_mes, cuil_titular) INCLUDE (program_id, beneficiary_id);
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
    cur.execute("TRUNCATE payments, benefits, incompatibility_rules, programs, secretarias, beneficiaries, users RESTART IDENTITY CASCADE")
    conn.commit()

    # ── Usuarios ──
    cur.execute("INSERT INTO users(email,password_hash,role,nombre) VALUES(%s,%s,%s,%s)",
                ("admin@demo.local", hash_pw("Demo123!"), "admin", "Administrador RIB"))
    cur.execute("INSERT INTO users(email,password_hash,role,nombre) VALUES(%s,%s,%s,%s)",
                ("user@demo.local", hash_pw("Demo123!"), "user", "Analista"))
    conn.commit()
    print("  ✅ Usuarios creados")

    # ── Secretarías ──
    sec_ids = []
    for sec_nombre in SECRETARIAS:
        cur.execute("INSERT INTO secretarias(nombre) VALUES(%s) RETURNING id", (sec_nombre,))
        sec_ids.append(cur.fetchone()[0])
    conn.commit()
    print(f"  ✅ Secretarías creadas: {len(sec_ids)}")

    # ── Programas ──
    prog_ids = []
    for nombre, sec_idx in PROGRAMAS:
        cur.execute("INSERT INTO programs(secretaria_id,nombre_programa) VALUES(%s,%s) RETURNING id",
                    (sec_ids[sec_idx], nombre))
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

    # Pre-flatten province data for vectorized indexing
    # (departments kept in PROVINCIAS for weighted distribution but not stored in DB)
    prov_depto_offsets = []  # (start, count) for each province's departments
    flat_entries = []
    for prov_name, prov_code, deptos in PROVINCIAS:
        start = len(flat_entries)
        for _ in deptos:
            flat_entries.append((prov_name, prov_code))
        prov_depto_offsets.append((start, len(deptos)))

    # Build per-province probability → flat probability (one entry per department for weighting)
    total_w = sum(PROV_WEIGHTS)
    flat_probs = []
    for i, (start, count) in enumerate(prov_depto_offsets):
        p = PROV_WEIGHTS[i] / total_w / count
        flat_probs.extend([p] * count)
    flat_probs = np.array(flat_probs)
    flat_probs /= flat_probs.sum()  # normalize

    entry_idx = rng.choice(len(flat_entries), size=n, p=flat_probs)

    # Extract province arrays
    flat_prov_names = np.array([e[0] for e in flat_entries])
    flat_prov_codes = np.array([e[1] for e in flat_entries])

    ben_provincia = flat_prov_names[entry_idx]
    ben_cod_prov = flat_prov_codes[entry_idx]

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

    # Write via COPY (vectorized string build)
    print(f"  Insertando via COPY...", end=" ", flush=True)
    sep = np.full(n, ',')
    nl = np.full(n, '\n')
    row = np.char.add(ben_cuil, sep)
    row = np.char.add(row, ben_nombre)
    row = np.char.add(row, sep)
    row = np.char.add(row, ben_apellido)
    row = np.char.add(row, sep)
    row = np.char.add(row, ben_sexo)
    row = np.char.add(row, sep)
    row = np.char.add(row, ben_fecha)
    row = np.char.add(row, sep)
    row = np.char.add(row, ben_provincia)
    row = np.char.add(row, sep)
    row = np.char.add(row, ben_cod_prov)
    row = np.char.add(row, sep)
    row = np.char.add(row, ben_cp)
    row = np.char.add(row, nl)
    buf = io.StringIO()
    buf.write(''.join(row))
    _copy_buf(cur, "beneficiaries",
              ["cuil","nombre","apellido","sexo","fecha_nacimiento",
               "provincia","codigo_provincia_indec","cp"], buf)
    conn.commit()
    buf.close()
    del row  # free memory

    # Get first beneficiary ID
    cur.execute("SELECT MIN(id) FROM beneficiaries")
    first_ben_id = cur.fetchone()[0]

    print(f"OK ({n:,} rows, {time.time()-t_ben:.0f}s)")

    # ── TC (Titular de Cobro) pool + family map ──
    # AUH (prog 0) and Alimentar (prog 2) have external TCs for minors
    TC_PROGRAMS = {prog_ids[0], prog_ids[2]}
    n_tc = n // 5  # ~20% of beneficiaries get external TCs
    tc_cuil = _cuil_array(n_tc, start=40_000_000)
    tc_nombre = all_nombres_f[rng.integers(0, len(all_nombres_f), size=n_tc)]
    tc_apellido = all_apellidos[rng.integers(0, len(all_apellidos), size=n_tc)]
    tc_sexo = np.full(n_tc, "F", dtype='U2')
    tc_years_age = rng.integers(25, 56, size=n_tc)
    tc_y_part = (2026 - tc_years_age).astype(str)
    tc_m_part = np.char.zfill(rng.integers(1, 13, size=n_tc).astype(str), 2)
    tc_d_part = np.char.zfill(rng.integers(1, 29, size=n_tc).astype(str), 2)
    tc_fecha = np.char.add(np.char.add(np.char.add(np.char.add(tc_y_part, '-'), tc_m_part), '-'), tc_d_part)
    # TC province: reuse same distribution
    tc_entry_idx = rng.choice(len(flat_entries), size=n_tc, p=flat_probs)
    tc_provincia = flat_prov_names[tc_entry_idx]

    # Family map: assign minors to TC pool (pre-computed once, reused across periods)
    minor_mask = years < 18
    minor_indices = np.where(minor_mask)[0]
    group_sizes = rng.integers(1, 5, size=n_tc)
    tc_expanded = np.repeat(np.arange(n_tc), group_sizes)
    n_assigned = min(len(minor_indices), len(tc_expanded))
    tc_assignment = np.full(n, -1, dtype=np.int64)  # -1 = no external TC (self-reference)
    tc_assignment[minor_indices[:n_assigned]] = tc_expanded[:n_assigned]
    print(f"  TC pool: {n_tc:,} titulares de cobro, {n_assigned:,} menores asignados")

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

    # Pre-compute incomp pair arrays for vectorized lookup
    incomp_a = np.array([prog_ids[a] for a, b in INCOMP_PAIRS])
    incomp_b = np.array([prog_ids[b] for a, b in INCOMP_PAIRS])

    # Pre-compute indices for each group
    inv_idx = np.where(is_invalido)[0]
    normal_mask = (~is_invalido) & (~force_incomp)
    incomp_idx = np.where((~is_invalido) & force_incomp)[0]
    group_indices = {}
    for c in [1, 2, 3]:
        idx = np.where(normal_mask & (cant_progs == c))[0]
        if len(idx) > 0:
            group_indices[c] = idx

    ben_cols = ["beneficiary_id","cuil_raw","program_id","periodo_mes","estado_beneficio",
                "cuil_titular","nombre_titular","apellido_titular","sexo_titular",
                "fecha_nacimiento_titular","provincia_titular"]
    pay_cols = ["beneficiary_id","program_id","fecha_pago","periodo_mes","monto_prestacion"]

    def _tc_suffix(idx_arr, progs_int, cuils, n_rows):
        """Build TC CSV suffix for benefit lines: ,cuil_tc,nombre_tc,...,provincia_tc"""
        tc_idx = tc_assignment[idx_arr]
        has_tc = tc_idx >= 0
        is_tc_prog = np.isin(progs_int, list(TC_PROGRAMS))
        use_ext = has_tc & is_tc_prog
        si = tc_idx.clip(0)
        s = np.char.add(",", np.where(use_ext, tc_cuil[si], cuils))
        s = np.char.add(s, ",")
        s = np.char.add(s, np.where(use_ext, tc_nombre[si], ben_nombre[idx_arr]))
        s = np.char.add(s, ",")
        s = np.char.add(s, np.where(use_ext, tc_apellido[si], ben_apellido[idx_arr]))
        s = np.char.add(s, ",")
        s = np.char.add(s, np.where(use_ext, tc_sexo[si], ben_sexo[idx_arr]))
        s = np.char.add(s, ",")
        s = np.char.add(s, np.where(use_ext, tc_fecha[si], ben_fecha[idx_arr]))
        s = np.char.add(s, ",")
        s = np.char.add(s, np.where(use_ext, tc_provincia[si], ben_provincia[idx_arr]))
        return s

    for periodo in PERIODOS:
        t_per = time.time()
        y, m = int(periodo[:4]), int(periodo[5:])
        periodo_prefix = f",{periodo},"
        date_prefix = f"{y}-{m:02d}-"
        print(f"\n📅 Periodo {periodo}...", end=" ", flush=True)

        b_parts = []  # list of string arrays for benefits
        p_parts = []  # list of string arrays for payments

        # ── Invalidos (5%) — TC = cuil_raw for cuil_titular, \N for other TC fields ──
        n_inv = len(inv_idx)
        if n_inv > 0:
            inv_progs = prog_ids_arr[rng.integers(0, n_progs, size=n_inv)]
            inv_null = rng.random(n_inv) > 0.5
            inv_cuils = np.where(inv_null, "\\N", "00000000000")
            # Build: \N,cuil,prog,periodo,ACTIVO,cuil_tc,\N,\N,\N,\N,\N,\N
            lines = np.char.add("\\N,", inv_cuils)
            lines = np.char.add(lines, ",")
            lines = np.char.add(lines, inv_progs.astype(str))
            lines = np.char.add(lines, periodo_prefix)
            lines = np.char.add(lines, "ACTIVO,")
            lines = np.char.add(lines, inv_cuils)
            lines = np.char.add(lines, ",\\N,\\N,\\N,\\N,\\N\n")
            b_parts.append(lines)

        # ── Normal valid by cant (vectorized per group) ──
        for cant, g_idx in group_indices.items():
            group_n = len(g_idx)
            g_ben_ids = (first_ben_id + g_idx).astype(str)
            g_cuils = ben_cuil[g_idx]

            # Program selection: argsort trick on random matrix
            rand_matrix = rng.random((group_n, n_progs))
            selected = rand_matrix.argsort(axis=1)[:, :cant]
            prog_matrix = prog_ids_arr[selected]  # (group_n, cant)

            # Estado: 8% INACTIVO
            estado_rand = rng.random((group_n, cant))
            is_activo = estado_rand >= 0.08
            estado_strs = np.where(is_activo, "ACTIVO", "INACTIVO")

            # Flatten: each beneficiary produces `cant` benefit rows
            flat_bids = np.repeat(g_ben_ids, cant)
            flat_cuils = np.repeat(g_cuils, cant)
            flat_progs = prog_matrix.ravel()
            flat_progs_str = flat_progs.astype(str)
            flat_estados = estado_strs.ravel()

            # TC columns: per-program assignment (repeat g_idx for flattened rows)
            flat_g_idx = np.repeat(g_idx, cant)
            tc_suf = _tc_suffix(flat_g_idx, flat_progs, flat_cuils, len(flat_cuils))

            # Build benefit lines: bid,cuil,prog,periodo,estado,tc_cuil,tc_nombre,...,tc_depto
            lines = np.char.add(flat_bids, ",")
            lines = np.char.add(lines, flat_cuils)
            lines = np.char.add(lines, ",")
            lines = np.char.add(lines, flat_progs_str)
            lines = np.char.add(lines, periodo_prefix)
            lines = np.char.add(lines, flat_estados)
            lines = np.char.add(lines, tc_suf)
            lines = np.char.add(lines, "\n")
            b_parts.append(lines)

            # Payments for ACTIVO only
            activo_flat = is_activo.ravel()
            n_activo = activo_flat.sum()
            if n_activo > 0:
                a_bids = flat_bids[activo_flat]
                a_progs_int = prog_matrix.ravel()[activo_flat]
                a_progs_str = flat_progs_str[activo_flat]
                a_prog_idx = a_progs_int - prog_ids_arr[0]
                montos = (prog_base_monto[a_prog_idx] + rng.integers(-10000, 20001, size=n_activo)).astype(str)
                days = np.char.zfill(rng.integers(1, 29, size=n_activo).astype(str), 2)
                # Build: bid,prog,YYYY-MM-DD,periodo,monto
                plines = np.char.add(a_bids, ",")
                plines = np.char.add(plines, a_progs_str)
                plines = np.char.add(plines, ",")
                plines = np.char.add(plines, date_prefix)
                plines = np.char.add(plines, days)
                plines = np.char.add(plines, periodo_prefix)
                plines = np.char.add(plines, montos)
                plines = np.char.add(plines, "\n")
                p_parts.append(plines)

        # ── Force incomp (~2% of valid) ──
        n_ic = len(incomp_idx)
        if n_ic > 0:
            ic_ben_ids = (first_ben_id + incomp_idx).astype(str)
            ic_cuils = ben_cuil[incomp_idx]
            ic_cant = cant_progs[incomp_idx]

            # Pick random incompatible pairs
            pair_sel = rng.integers(0, len(INCOMP_PAIRS), size=n_ic)
            prog_a = incomp_a[pair_sel]
            prog_b = incomp_b[pair_sel]

            # Generate benefits for program A and B
            for progs_col in [prog_a, prog_b]:
                estados = np.where(rng.random(n_ic) >= 0.08, "ACTIVO", "INACTIVO")
                progs_str = progs_col.astype(str)
                tc_suf = _tc_suffix(incomp_idx, progs_col, ic_cuils, n_ic)
                lines = np.char.add(ic_ben_ids, ",")
                lines = np.char.add(lines, ic_cuils)
                lines = np.char.add(lines, ",")
                lines = np.char.add(lines, progs_str)
                lines = np.char.add(lines, periodo_prefix)
                lines = np.char.add(lines, estados)
                lines = np.char.add(lines, tc_suf)
                lines = np.char.add(lines, "\n")
                b_parts.append(lines)

                act = estados == "ACTIVO"
                na = act.sum()
                if na > 0:
                    pidx = progs_col[act] - prog_ids_arr[0]
                    montos = (prog_base_monto[pidx] + rng.integers(-10000, 20001, size=na)).astype(str)
                    days = np.char.zfill(rng.integers(1, 29, size=na).astype(str), 2)
                    plines = np.char.add(ic_ben_ids[act], ",")
                    plines = np.char.add(plines, progs_str[act])
                    plines = np.char.add(plines, ",")
                    plines = np.char.add(plines, date_prefix)
                    plines = np.char.add(plines, days)
                    plines = np.char.add(plines, periodo_prefix)
                    plines = np.char.add(plines, montos)
                    plines = np.char.add(plines, "\n")
                    p_parts.append(plines)

            # Extra program for cant>=3
            extra_mask = ic_cant >= 3
            n_extra = extra_mask.sum()
            if n_extra > 0:
                # Pick random program avoiding the pair (vectorized: use argsort, exclude first 2)
                e_pair_a = prog_a[extra_mask]
                e_pair_b = prog_b[extra_mask]
                # Random from remaining 6 programs
                e_rand = rng.random((n_extra, n_progs))
                # Set high value for pair programs to exclude them
                for j in range(n_extra):
                    e_rand[j, e_pair_a[j] - prog_ids_arr[0]] = 2.0
                    e_rand[j, e_pair_b[j] - prog_ids_arr[0]] = 2.0
                e_progs = prog_ids_arr[e_rand.argsort(axis=1)[:, 0]]
                e_progs_str = e_progs.astype(str)
                e_bids = ic_ben_ids[extra_mask]
                e_cuils = ic_cuils[extra_mask]

                estados = np.where(rng.random(n_extra) >= 0.08, "ACTIVO", "INACTIVO")
                e_incomp_idx = incomp_idx[extra_mask]
                tc_suf = _tc_suffix(e_incomp_idx, e_progs, e_cuils, n_extra)
                lines = np.char.add(e_bids, ",")
                lines = np.char.add(lines, e_cuils)
                lines = np.char.add(lines, ",")
                lines = np.char.add(lines, e_progs_str)
                lines = np.char.add(lines, periodo_prefix)
                lines = np.char.add(lines, estados)
                lines = np.char.add(lines, tc_suf)
                lines = np.char.add(lines, "\n")
                b_parts.append(lines)

                act = estados == "ACTIVO"
                na = act.sum()
                if na > 0:
                    pidx = e_progs[act] - prog_ids_arr[0]
                    montos = (prog_base_monto[pidx] + rng.integers(-10000, 20001, size=na)).astype(str)
                    days = np.char.zfill(rng.integers(1, 29, size=na).astype(str), 2)
                    plines = np.char.add(e_bids[act], ",")
                    plines = np.char.add(plines, e_progs_str[act])
                    plines = np.char.add(plines, ",")
                    plines = np.char.add(plines, date_prefix)
                    plines = np.char.add(plines, days)
                    plines = np.char.add(plines, periodo_prefix)
                    plines = np.char.add(plines, montos)
                    plines = np.char.add(plines, "\n")
                    p_parts.append(plines)

        # ── Concatenate and COPY ──
        all_ben = np.concatenate(b_parts)
        benefits_count = len(all_ben)
        ben_buf = io.StringIO()
        ben_buf.write(''.join(all_ben))
        _copy_buf(cur, "benefits", ben_cols, ben_buf)
        ben_buf.close()
        del all_ben

        if p_parts:
            all_pay = np.concatenate(p_parts)
            payments_count = len(all_pay)
            pay_buf = io.StringIO()
            pay_buf.write(''.join(all_pay))
            _copy_buf(cur, "payments", pay_cols, pay_buf)
            pay_buf.close()
            del all_pay
        else:
            payments_count = 0

        conn.commit()
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
                ("admin@demo.local", hash_pw("Demo123!"), "admin", "Administrador RIB"))
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
