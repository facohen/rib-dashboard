"""
seed_pg.py — Generador de datos masivos para PostgreSQL
Genera 8M beneficiarios, ~200M benefits, pagos correspondientes.

Uso:
    DATABASE_URL=postgresql://user:pass@host/rub python seed_pg.py
    DATABASE_URL=postgresql://user:pass@host/rub python seed_pg.py --small   # 10K para test rápido
"""
import os, sys, hashlib, random, time
from datetime import date, timedelta
import psycopg2
from psycopg2.extras import execute_values

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/rub")
BATCH_SIZE = 10_000

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

# Pesos poblacionales aproximados por provincia
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

INCOMP_PAIRS = [(0,3),(1,7),(2,5),(6,7)]  # AUH↔PNC, PROGRESAR↔POTENCIAR, ALIMENTAR↔HACEMOS, ARGENTA↔POTENCIAR


def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()


def cuil(idx):
    prefixes = ["20","23","24","27"]
    return prefixes[idx % 4] + str(20000000 + idx).zfill(8) + str(idx % 10)


def birth_date(group_idx):
    base = date(2026, 3, 31)
    ranges = [(0, 12), (13, 29), (30, 59), (60, 85)]
    lo, hi = ranges[group_idx]
    years = random.randint(lo, hi)
    try:
        d = base.replace(year=base.year - years, month=random.randint(1, 12), day=random.randint(1, 28))
    except ValueError:
        d = base.replace(year=base.year - years)
    return d


# ──────────────────────────────────────────────
# Schema
# ──────────────────────────────────────────────

SCHEMA = """
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
CREATE INDEX idx_ben_provincia ON beneficiaries(provincia);
CREATE INDEX idx_ben_apellido_nombre ON beneficiaries(apellido, nombre);
CREATE INDEX idx_ben_cuil ON beneficiaries(cuil);
CREATE INDEX idx_payments_period ON payments(periodo_mes);
CREATE INDEX idx_payments_benid_period ON payments(beneficiary_id, periodo_mes);
"""


def run(num_beneficiaries=8_000_000):
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    cur = conn.cursor()

    t0 = time.time()
    print(f"🏗️  Generando {num_beneficiaries:,} beneficiarios en PostgreSQL...")
    print(f"   URL: {DATABASE_URL.split('@')[-1] if '@' in DATABASE_URL else DATABASE_URL}")

    # ── Schema ──
    print("\n📋 Creando schema...")
    cur.execute(SCHEMA)
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
    print(f"  ✅ Programas creados: {len(prog_ids)}")

    # ── Incompatibilidades ──
    for a, b in INCOMP_PAIRS:
        cur.execute("""INSERT INTO incompatibility_rules(program_a_id,program_b_id,is_compatible,descripcion)
                       VALUES(%s,%s,0,%s)""",
                    (prog_ids[a], prog_ids[b],
                     f"{PROGRAMAS[a][0]} incompatible con {PROGRAMAS[b][0]}"))
    conn.commit()
    print(f"  ✅ Reglas de incompatibilidad: {len(INCOMP_PAIRS)}")

    # ── Beneficiarios ──
    # Distribución etaria: niñez 20%, jóvenes 30%, adultos 35%, mayores 15%
    group_weights = [0.20, 0.30, 0.35, 0.15]
    sexo_pool = ["M","M","M","F","F","F","F","F","X","NI"]

    # Normalizar pesos provinciales
    total_w = sum(PROV_WEIGHTS)
    prov_probs = [w / total_w for w in PROV_WEIGHTS]

    print(f"\n👥 Insertando {num_beneficiaries:,} beneficiarios...")
    ben_batch = []
    # Track first_id for later reference
    cur.execute("SELECT COALESCE(MAX(id), 0) FROM beneficiaries")
    first_ben_id = cur.fetchone()[0] + 1

    for i in range(num_beneficiaries):
        group = random.choices([0,1,2,3], weights=group_weights)[0]
        sexo = random.choice(sexo_pool)
        nom = random.choice(NOMBRES_M if sexo == "M" else NOMBRES_F if sexo == "F" else NOMBRES_M + NOMBRES_F)
        prov_idx = random.choices(range(len(PROVINCIAS)), weights=prov_probs)[0]
        prov = PROVINCIAS[prov_idx]
        depto = random.choice(prov[2])

        ben_batch.append((
            cuil(i), nom, random.choice(APELLIDOS), sexo,
            birth_date(group),
            prov[0], prov[1], depto[0], depto[1],
            str(1000 + random.randint(0, 8999))
        ))

        if len(ben_batch) >= BATCH_SIZE:
            execute_values(cur, """
                INSERT INTO beneficiaries(cuil,nombre,apellido,sexo,fecha_nacimiento,
                    provincia,codigo_provincia_indec,departamento,codigo_departamento_indec,cp)
                VALUES %s
            """, ben_batch)
            conn.commit()
            done = i + 1
            elapsed = time.time() - t0
            rate = done / elapsed
            eta = (num_beneficiaries - done) / rate if rate > 0 else 0
            print(f"  {done:>10,} / {num_beneficiaries:,}  ({done*100//num_beneficiaries}%)  "
                  f"[{rate:,.0f}/s, ETA {eta/60:.0f}m]", end="\r")
            ben_batch = []

    if ben_batch:
        execute_values(cur, """
            INSERT INTO beneficiaries(cuil,nombre,apellido,sexo,fecha_nacimiento,
                provincia,codigo_provincia_indec,departamento,codigo_departamento_indec,cp)
            VALUES %s
        """, ben_batch)
        conn.commit()

    print(f"\n  ✅ Beneficiarios: {num_beneficiaries:,} ({time.time()-t0:.0f}s)")

    # ── Benefits y pagos por periodo ──
    # Concentración: 50% 1 prest, 30% 2, 15% 3+, 5% inválidos (sin beneficiary_id)
    conc_weights = [0.50, 0.30, 0.15, 0.05]  # 1, 2, 3+, inválido
    num_invalidos = int(num_beneficiaries * 0.05)
    num_validos = num_beneficiaries - num_invalidos

    for periodo in PERIODOS:
        t_per = time.time()
        y, m = int(periodo[:4]), int(periodo[5:])
        print(f"\n📅 Periodo {periodo}...")

        ben_batch_b = []
        pay_batch = []
        benefits_count = 0
        payments_count = 0

        for i in range(num_beneficiaries):
            ben_id = first_ben_id + i

            # 5% inválidos
            if i >= num_validos:
                prog = random.choice(prog_ids)
                ben_batch_b.append((
                    None,
                    None if random.random() > 0.5 else "00000000000",
                    prog, periodo, "ACTIVO"
                ))
                benefits_count += 1
            else:
                # Concentración
                r = random.random()
                if r < 0.50:
                    cant = 1
                elif r < 0.80:
                    cant = 2
                else:
                    cant = 3

                progs_elegidos = random.sample(prog_ids, min(cant, len(prog_ids)))

                # Forzar violaciones de incompatibilidad (~2% de beneficiarios en cada periodo)
                if random.random() < 0.02:
                    pair = random.choice(INCOMP_PAIRS)
                    progs_elegidos = [prog_ids[pair[0]], prog_ids[pair[1]]]
                    if cant >= 3:
                        extra = random.choice([p for p in prog_ids if p not in progs_elegidos])
                        progs_elegidos.append(extra)

                for pid in progs_elegidos:
                    estado = "INACTIVO" if random.random() < 0.08 else "ACTIVO"
                    ben_batch_b.append((ben_id, cuil(i), pid, periodo, estado))
                    benefits_count += 1

                    if estado == "ACTIVO":
                        pidx = prog_ids.index(pid)
                        sec = PROGRAMAS[pidx][1]
                        base = [80000, 95000, 60000][sec]
                        monto = base + random.randint(-10000, 20000)
                        day = random.randint(1, 28)
                        pay_batch.append((
                            ben_id, pid,
                            date(y, m, day),
                            periodo, monto
                        ))
                        payments_count += 1

            # Flush benefits batch
            if len(ben_batch_b) >= BATCH_SIZE:
                execute_values(cur, """
                    INSERT INTO benefits(beneficiary_id,cuil_raw,program_id,periodo_mes,estado_beneficio)
                    VALUES %s
                """, ben_batch_b)
                ben_batch_b = []

            # Flush payments batch
            if len(pay_batch) >= BATCH_SIZE:
                execute_values(cur, """
                    INSERT INTO payments(beneficiary_id,program_id,fecha_pago,periodo_mes,monto_prestacion)
                    VALUES %s
                """, pay_batch)
                pay_batch = []

            if (i + 1) % 500_000 == 0:
                conn.commit()
                elapsed = time.time() - t_per
                rate = (i + 1) / elapsed
                print(f"    {i+1:>10,} / {num_beneficiaries:,}  "
                      f"[{rate:,.0f}/s]", end="\r")

        # Flush remaining
        if ben_batch_b:
            execute_values(cur, """
                INSERT INTO benefits(beneficiary_id,cuil_raw,program_id,periodo_mes,estado_beneficio)
                VALUES %s
            """, ben_batch_b)
        if pay_batch:
            execute_values(cur, """
                INSERT INTO payments(beneficiary_id,program_id,fecha_pago,periodo_mes,monto_prestacion)
                VALUES %s
            """, pay_batch)
        conn.commit()

        per_time = time.time() - t_per
        print(f"  ✅ {periodo}: {benefits_count:,} benefits, {payments_count:,} pagos ({per_time:.0f}s)")

    # ── Índices ──
    print("\n📊 Creando índices (esto puede tardar unos minutos)...")
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
    np = cur.fetchone()[0]
    total_time = time.time() - t0

    print(f"\n{'='*50}")
    print(f"🎉 Seed completado en {total_time/60:.1f} minutos")
    print(f"   Beneficiarios: {nb:,}")
    print(f"   Benefits:      {nben:,}")
    print(f"   Pagos:         {np:,}")
    print(f"{'='*50}")

    cur.close()
    conn.close()


if __name__ == "__main__":
    if "--small" in sys.argv:
        run(num_beneficiaries=10_000)
    elif "--medium" in sys.argv:
        run(num_beneficiaries=100_000)
    else:
        run(num_beneficiaries=8_000_000)
