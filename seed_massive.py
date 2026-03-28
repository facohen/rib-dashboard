"""
seed_massive.py — Seed masivo: ~8M beneficiarios, millones de benefits por programa
Usa COPY para inserción ultra-rápida en PostgreSQL.
"""
import hashlib
import random
import io
import time
from datetime import date

from config import get_connection

# ── Configuración ──
TOTAL_BENEFICIARIOS = 8_000_000
PERIODOS = ["2026-01", "2026-02", "2026-03"]
BATCH_SIZE = 500_000  # filas por COPY batch

PROVINCIAS = [
    ("Buenos Aires", "06", [("La Matanza","06427"),("Lomas de Zamora","06490"),("Quilmes","06658"),("General Pueyrredón","06357"),("Almirante Brown","06028"),("Moreno","06560"),("Merlo","06539"),("Florencio Varela","06274"),("Tigre","06756"),("Lanús","06434")]),
    ("Córdoba",      "14", [("Capital","14014"),("Río Cuarto","14147"),("San Justo","14175"),("Punilla","14140"),("Colón","14021")]),
    ("Santa Fe",     "82", [("Rosario","82119"),("La Capital","82070"),("Rafaela","82075"),("Caseros","82021"),("General López","82049")]),
    ("Mendoza",      "50", [("Capital","50028"),("Godoy Cruz","50049"),("Luján de Cuyo","50063"),("Guaymallén","50056")]),
    ("Tucumán",      "90", [("Capital","90021"),("Yerba Buena","90126"),("Cruz Alta","90028")]),
    ("Salta",        "66", [("Capital","66014"),("Orán","66077"),("San Martín","66098")]),
    ("Chaco",        "22", [("San Fernando","22007"),("Comandante Fernández","22042"),("Libertad","22056")]),
    ("Corrientes",   "18", [("Capital","18021"),("Goya","18098"),("Mercedes","18119")]),
    ("Misiones",     "54", [("Capital","54028"),("Eldorado","54035"),("Oberá","54063")]),
    ("Jujuy",        "38", [("Dr. Manuel Belgrano","38007"),("Palpalá","38049"),("San Pedro","38063")]),
    ("Entre Ríos",   "30", [("Paraná","30084"),("Concordia","30021"),("Gualeguaychú","30056")]),
    ("Santiago del Estero", "86", [("Capital","86021"),("Banda","86007"),("Río Hondo","86098")]),
    ("Formosa",      "34", [("Formosa","34007"),("Pilcomayo","34042"),("Laishí","34021")]),
    ("Catamarca",    "10", [("Capital","10007"),("Valle Viejo","10098"),("Fray Mamerto Esquiú","10028")]),
    ("La Rioja",     "46", [("Capital","46021"),("Chilecito","46014"),("Arauco","46007")]),
    ("San Juan",     "70", [("Rawson","70077"),("Capital","70014"),("Chimbas","70021")]),
    ("San Luis",     "74", [("La Capital","74028"),("Pedernera","74042")]),
    ("Neuquén",      "58", [("Confluencia","58014"),("Zapala","58098")]),
    ("Río Negro",    "62", [("General Roca","62042"),("Bariloche","62007")]),
    ("Chubut",       "26", [("Rawson","26042"),("Escalante","26014")]),
    ("Santa Cruz",   "78", [("Güer Aike","78007"),("Deseado","78014")]),
    ("Tierra del Fuego", "94", [("Ushuaia","94015"),("Río Grande","94008")]),
]

# Pesos de población por provincia (aprox real, Buenos Aires domina)
PROV_WEIGHTS = [38, 8, 8, 5, 4, 3, 3, 3, 3, 2, 3, 2, 2, 1, 1, 2, 1, 1, 2, 1, 1, 1]

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

NOMBRES_M = ["Carlos","Juan","Diego","Martín","Luis","Pedro","Roberto","Sergio","Mario","Daniel","Miguel","Gustavo","Alejandro","Ricardo","Fernando","Ezequiel","Facundo","Gabriel","Héctor","Ignacio","Pablo","Ramón","Jorge","Oscar","Julio","Eduardo","Rubén","José","Alberto","Raúl"]
NOMBRES_F = ["María","Ana","Laura","Sandra","Patricia","Claudia","Carolina","Gabriela","Valeria","Marcela","Natalia","Silvana","Andrea","Mónica","Verónica","Lorena","Débora","Alejandra","Viviana","Cecilia","Romina","Lucía","Soledad","Florencia","Mariana","Rosa","Carmen","Julia","Liliana","Estela"]
APELLIDOS = ["García","Rodríguez","González","Fernández","López","Martínez","Sánchez","Pérez","Gómez","Díaz","Hernández","Castro","Romero","Torres","Alvarez","Ruiz","Ramírez","Flores","Acosta","Medina","Suárez","Ramos","Molina","Moreno","Ortega","Silva","Ponce","Vera","Rosa","Reyes","Aguirre","Cabrera","Córdoba","Domínguez","Espinoza","Figueroa","Guzmán","Herrera","Ibáñez","Jiménez"]

SEXOS = ["M","M","M","F","F","F","F","F","X","NI"]

def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

def cuil_gen(idx):
    prefixes = ["20","23","24","27"]
    return prefixes[idx % 4] + str(20000000 + idx).zfill(8) + str(idx % 10)

def random_date(min_year, max_year):
    y = random.randint(min_year, max_year)
    m = random.randint(1, 12)
    d = random.randint(1, 28)
    return f"{y}-{m:02d}-{d:02d}"

def birth_for_group(group):
    """0=niñez, 1=jóvenes, 2=adultos, 3=mayores"""
    if group == 0: return random_date(2014, 2026)
    if group == 1: return random_date(1997, 2013)
    if group == 2: return random_date(1967, 1996)
    return random_date(1941, 1966)

def pick_provincia_weighted():
    return random.choices(PROVINCIAS, weights=PROV_WEIGHTS, k=1)[0]


def run():
    t0 = time.time()
    conn = get_connection()
    cur = conn.cursor()

    print("🗑️  Limpiando tablas...")
    for t in ["payments", "benefits", "incompatibility_rules", "programs", "beneficiaries", "users"]:
        cur.execute(f"TRUNCATE {t} CASCADE")
    # Reset sequences
    for t in ["users", "programs", "beneficiaries", "benefits", "payments", "incompatibility_rules"]:
        cur.execute(f"SELECT setval(pg_get_serial_sequence('{t}', 'id'), 1, false)")
    conn.commit()

    # ── Usuarios ──
    cur.execute("INSERT INTO users(email,password_hash,role,nombre) VALUES(%s,%s,%s,%s)",
                ("admin@demo.local", hash_pw("Demo123!"), "admin", "Administrador RIB"))
    cur.execute("INSERT INTO users(email,password_hash,role,nombre) VALUES(%s,%s,%s,%s)",
                ("user@demo.local", hash_pw("Demo123!"), "user", "Analista"))
    conn.commit()
    print("  ✅ Usuarios: 2")

    # ── Programas ──
    prog_ids = []
    for nombre, sec_idx in PROGRAMAS:
        cur.execute("INSERT INTO programs(secretaria_origen,nombre_programa) VALUES(%s,%s) RETURNING id",
                    (SECRETARIAS[sec_idx], nombre))
        prog_ids.append(cur.fetchone()[0])
    conn.commit()
    print(f"  ✅ Programas: {len(prog_ids)}")

    # Montos base por programa
    MONTO_BASE = {
        prog_ids[0]: 80000,   # AUH
        prog_ids[1]: 65000,   # PROGRESAR
        prog_ids[2]: 70000,   # Alimentar
        prog_ids[3]: 120000,  # PNC
        prog_ids[4]: 45000,   # SUMAR
        prog_ids[5]: 90000,   # Hacemos Futuro
        prog_ids[6]: 55000,   # ARGENTA
        prog_ids[7]: 95000,   # Potenciar
    }

    # ── Incompatibilidades ──
    incomp_pairs = [(0,3),(1,7),(2,5),(6,7)]
    for a, b in incomp_pairs:
        cur.execute("""INSERT INTO incompatibility_rules(program_a_id,program_b_id,is_compatible,descripcion)
                       VALUES(%s,%s,0,%s)""",
                    (prog_ids[a], prog_ids[b],
                     f"{PROGRAMAS[a][0]} incompatible con {PROGRAMAS[b][0]}"))
    conn.commit()
    print("  ✅ Reglas incompatibilidad: 4")

    # ── Beneficiarios (8M) con COPY ──
    print(f"\n📊 Generando {TOTAL_BENEFICIARIOS:,} beneficiarios...")
    # Distribución etaria: niñez 15%, jóvenes 25%, adultos 40%, mayores 20%
    group_weights = [15, 25, 40, 20]

    # Pre-generate provincia assignments
    all_nombres_m = NOMBRES_M
    all_nombres_f = NOMBRES_F

    generated = 0
    while generated < TOTAL_BENEFICIARIOS:
        batch = min(BATCH_SIZE, TOTAL_BENEFICIARIOS - generated)
        buf = io.StringIO()

        for i in range(batch):
            idx = generated + i
            group = random.choices([0,1,2,3], weights=group_weights, k=1)[0]
            sexo = random.choice(SEXOS)
            if sexo == "M":
                nom = random.choice(all_nombres_m)
            elif sexo == "F":
                nom = random.choice(all_nombres_f)
            else:
                nom = random.choice(all_nombres_m + all_nombres_f)
            ape = random.choice(APELLIDOS)
            prov = pick_provincia_weighted()
            depto = random.choice(prov[2])
            birth = birth_for_group(group)
            c = cuil_gen(idx)
            cp = str(1000 + random.randint(0, 8999))

            # TSV: cuil, nombre, apellido, sexo, fecha_nacimiento, provincia, cod_prov, depto, cod_depto, cp
            buf.write(f"{c}\t{nom}\t{ape}\t{sexo}\t{birth}\t{prov[0]}\t{prov[1]}\t{depto[0]}\t{depto[1]}\t{cp}\n")

        buf.seek(0)
        cur.copy_from(buf, 'beneficiaries',
                      columns=('cuil','nombre','apellido','sexo','fecha_nacimiento',
                               'provincia','codigo_provincia_indec','departamento',
                               'codigo_departamento_indec','cp'))
        conn.commit()
        generated += batch
        elapsed = time.time() - t0
        print(f"  ... {generated:>10,} beneficiarios ({elapsed:.0f}s)")

    print(f"  ✅ Beneficiarios: {TOTAL_BENEFICIARIOS:,}")

    # ── Benefits y Payments ──
    # Cada beneficiario recibe 1-3 programas por período
    # Distribución: 50% → 1 programa, 30% → 2, 15% → 3, 5% → 4+
    # Esto da ~1.75 benefits/beneficiario/período ≈ 14M benefits/período ≈ 42M total
    # Y por programa ~1.75M benefits/período

    print(f"\n📊 Generando benefits y payments para {len(PERIODOS)} períodos...")

    for periodo in PERIODOS:
        y, m = int(periodo[:4]), int(periodo[5:])
        print(f"\n  Período {periodo}:")

        ben_buf = io.StringIO()
        pay_buf = io.StringIO()
        ben_count = 0
        pay_count = 0

        # Process in batches to control memory
        for batch_start in range(1, TOTAL_BENEFICIARIOS + 1, BATCH_SIZE):
            batch_end = min(batch_start + BATCH_SIZE, TOTAL_BENEFICIARIOS + 1)

            for bid in range(batch_start, batch_end):
                # ~5% sin identificar
                if random.random() < 0.05:
                    prog = random.choice(prog_ids)
                    raw_cuil = "" if random.random() > 0.5 else "00000000000"
                    ben_buf.write(f"\\N\t{raw_cuil}\t{prog}\t{periodo}\tACTIVO\n")
                    ben_count += 1
                    continue

                # Cantidad de programas
                r = random.random()
                if r < 0.50:
                    cant = 1
                elif r < 0.80:
                    cant = 2
                elif r < 0.95:
                    cant = 3
                else:
                    cant = 4

                progs = random.sample(prog_ids, min(cant, len(prog_ids)))

                # Forzar incompatibilidades en último período (~2% de los de 2+ programas)
                if periodo == PERIODOS[-1] and cant >= 2 and random.random() < 0.02:
                    progs = [prog_ids[0], prog_ids[3]]  # AUH + PNC
                    if cant >= 3:
                        progs.append(random.choice([prog_ids[1], prog_ids[4]]))

                c = cuil_gen(bid - 1)
                for pid in progs:
                    estado = "INACTIVO" if random.random() < 0.06 else "ACTIVO"
                    ben_buf.write(f"{bid}\t{c}\t{pid}\t{periodo}\t{estado}\n")
                    ben_count += 1

                    if estado == "ACTIVO":
                        base = MONTO_BASE[pid]
                        monto = base + random.randint(-15000, 25000)
                        day = random.randint(1, 28)
                        fecha = f"{y}-{m:02d}-{day:02d}"
                        pay_buf.write(f"{bid}\t{pid}\t{fecha}\t{periodo}\t{monto}\n")
                        pay_count += 1

            # Flush benefits batch
            ben_buf.seek(0)
            cur.copy_from(ben_buf, 'benefits',
                          columns=('beneficiary_id','cuil_raw','program_id','periodo_mes','estado_beneficio'),
                          null='\\N')
            conn.commit()
            ben_buf.close()
            ben_buf = io.StringIO()

            # Flush payments batch
            pay_buf.seek(0)
            cur.copy_from(pay_buf, 'payments',
                          columns=('beneficiary_id','program_id','fecha_pago','periodo_mes','monto_prestacion'))
            conn.commit()
            pay_buf.close()
            pay_buf = io.StringIO()

            elapsed = time.time() - t0
            done = batch_end - 1
            print(f"    ... procesados {done:>10,} / {TOTAL_BENEFICIARIOS:,} beneficiarios ({elapsed:.0f}s)")

        ben_buf.close()
        pay_buf.close()
        print(f"  ✅ {periodo}: {ben_count:,} benefits, {pay_count:,} payments")

    # ── Índices para performance ──
    print("\n🔧 Creando índices...")
    indices = [
        "CREATE INDEX IF NOT EXISTS idx_benefits_periodo ON benefits(periodo_mes)",
        "CREATE INDEX IF NOT EXISTS idx_benefits_bid ON benefits(beneficiary_id)",
        "CREATE INDEX IF NOT EXISTS idx_benefits_pid ON benefits(program_id)",
        "CREATE INDEX IF NOT EXISTS idx_benefits_estado ON benefits(estado_beneficio)",
        "CREATE INDEX IF NOT EXISTS idx_benefits_periodo_estado ON benefits(periodo_mes, estado_beneficio)",
        "CREATE INDEX IF NOT EXISTS idx_benefits_bid_periodo ON benefits(beneficiary_id, periodo_mes)",
        "CREATE INDEX IF NOT EXISTS idx_payments_periodo ON payments(periodo_mes)",
        "CREATE INDEX IF NOT EXISTS idx_payments_bid ON payments(beneficiary_id)",
        "CREATE INDEX IF NOT EXISTS idx_ben_provincia ON beneficiaries(provincia)",
        "CREATE INDEX IF NOT EXISTS idx_ben_sexo ON beneficiaries(sexo)",
    ]
    for ddl in indices:
        name = ddl.split("idx_")[1].split(" ")[0]
        cur.execute(ddl)
        conn.commit()
        print(f"  ✅ {name}")

    # ── ANALYZE ──
    print("\n🔧 ANALYZE...")
    cur.execute("ANALYZE beneficiaries")
    cur.execute("ANALYZE benefits")
    cur.execute("ANALYZE payments")
    conn.commit()

    # ── Resumen ──
    print("\n" + "="*50)
    for t in ["beneficiaries", "benefits", "payments"]:
        cur.execute(f"SELECT COUNT(*) FROM {t}")
        print(f"  {t}: {cur.fetchone()[0]:,}")

    # Por programa
    cur.execute("""
        SELECT p.nombre_programa, COUNT(*) as total
        FROM benefits b JOIN programs p ON b.program_id=p.id
        WHERE b.periodo_mes='2026-03' AND b.estado_beneficio='ACTIVO'
        GROUP BY p.nombre_programa ORDER BY total DESC
    """)
    print("\n  Benefits activos por programa (2026-03):")
    for row in cur.fetchall():
        print(f"    {row[0]}: {row[1]:,}")

    conn.close()
    elapsed = time.time() - t0
    print(f"\n🎉 Seed masivo completado en {elapsed:.0f} segundos")


if __name__ == "__main__":
    run()
