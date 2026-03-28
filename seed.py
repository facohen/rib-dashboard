"""
seed.py — Datos demo para RIB Dashboard
"""
import sqlite3, hashlib, random
from datetime import date, timedelta

DB_PATH = "rib.db"

PROVINCIAS = [
    ("Buenos Aires", "06", [("La Matanza","06427"),("Lomas de Zamora","06490"),("Quilmes","06658"),("General Pueyrredón","06357"),("Almirante Brown","06028")]),
    ("Córdoba",      "14", [("Capital","14014"),("Río Cuarto","14147"),("San Justo","14175")]),
    ("Santa Fe",     "82", [("Rosario","82119"),("La Capital","82070"),("Rafaela","82075")]),
    ("Mendoza",      "50", [("Capital","50028"),("Godoy Cruz","50049"),("Luján de Cuyo","50063")]),
    ("Tucumán",      "90", [("Capital","90021"),("Yerba Buena","90126")]),
    ("Salta",        "66", [("Capital","66014"),("Orán","66077")]),
    ("Chaco",        "22", [("San Fernando","22007"),("Comandante Fernández","22042")]),
    ("Corrientes",   "18", [("Capital","18021"),("Goya","18098")]),
    ("Misiones",     "54", [("Capital","54028"),("Eldorado","54035")]),
    ("Jujuy",        "38", [("Dr. Manuel Belgrano","38007"),("Palpalá","38049")]),
]

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

NOMBRES_M = ["Carlos","Juan","Diego","Martín","Luis","Pedro","Roberto","Sergio","Mario","Daniel","Miguel","Gustavo","Alejandro","Ricardo","Fernando","Ezequiel","Facundo","Gabriel","Héctor","Ignacio"]
NOMBRES_F = ["María","Ana","Laura","Sandra","Patricia","Claudia","Carolina","Gabriela","Valeria","Marcela","Natalia","Silvana","Andrea","Mónica","Verónica","Lorena","Débora","Alejandra","Viviana","Cecilia"]
APELLIDOS = ["García","Rodríguez","González","Fernández","López","Martínez","Sánchez","Pérez","Gómez","Díaz","Hernández","Castro","Romero","Torres","Alvarez","Ruiz","Ramírez","Flores","Acosta","Medina","Suárez","Ramos","Molina","Moreno","Ortega","Silva","Ponce","Vera","Rosa","Reyes"]
SEXOS_PONDERADOS = ["M","M","M","F","F","F","F","F","X","NI"]
PERIODOS = ["2026-01","2026-02","2026-03"]

def hash_pw(pw): return hashlib.sha256(pw.encode()).hexdigest()
def rnd(a,b): return random.randint(a,b)
def pick(lst): return random.choice(lst)

def birth_from_group(group):
    today = date(2026,3,31)
    ranges = [(0,12),(13,29),(30,59),(60,85)]
    lo,hi = ranges[group]
    years = rnd(lo,hi)
    d = today.replace(year=today.year-years)
    try: d = d.replace(month=rnd(1,12), day=rnd(1,28))
    except: pass
    return d.isoformat()

def cuil(idx):
    prefixes = ["20","23","24","27"]
    return prefixes[idx%4] + str(20000000+idx).zfill(8) + "0"

def run():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Limpiar
    for t in ["payments","benefits","incompatibility_rules","programs","beneficiaries","users"]:
        c.execute(f"DELETE FROM {t}")
    conn.commit()

    # Usuarios
    c.execute("INSERT INTO users(email,password_hash,role,nombre) VALUES(?,?,?,?)",
              ("admin@demo.local", hash_pw("Demo123!"), "admin", "Administrador RIB"))
    c.execute("INSERT INTO users(email,password_hash,role,nombre) VALUES(?,?,?,?)",
              ("user@demo.local", hash_pw("Demo123!"), "user", "Analista"))
    conn.commit()
    print("  ✅ Usuarios creados")

    # Programas
    prog_ids = []
    for nombre, sec_idx in PROGRAMAS:
        c.execute("INSERT INTO programs(secretaria_origen,nombre_programa) VALUES(?,?)",
                  (SECRETARIAS[sec_idx], nombre))
        prog_ids.append(c.lastrowid)
    conn.commit()
    print("  ✅ Programas creados:", len(prog_ids))

    # Reglas incompatibilidad: AUH↔PNC, PROGRESAR↔POTENCIAR, ALIMENTAR↔HACEMOS, ARGENTA↔POTENCIAR
    incomp_pairs = [(0,3),(1,7),(2,5),(6,7)]
    for a,b in incomp_pairs:
        c.execute("""INSERT INTO incompatibility_rules(program_a_id,program_b_id,is_compatible,descripcion)
                     VALUES(?,?,0,?)""",
                  (prog_ids[a], prog_ids[b],
                   f"{PROGRAMAS[a][0]} incompatible con {PROGRAMAS[b][0]}"))
    conn.commit()
    print("  ✅ Reglas de incompatibilidad: 4")

    # Beneficiarios (300)
    # Distribución etaria: niñez 20%, jóvenes 30%, adultos 35%, mayores 15%
    grupos = [0]*60 + [1]*90 + [2]*105 + [3]*45
    random.shuffle(grupos)
    ben_ids = []
    for i in range(300):
        g = grupos[i]
        sexo = pick(SEXOS_PONDERADOS)
        nom = pick(NOMBRES_M if sexo=="M" else NOMBRES_F if sexo=="F" else NOMBRES_M+NOMBRES_F)
        prov = PROVINCIAS[i % len(PROVINCIAS)]
        depto = pick(prov[2])
        c.execute("""INSERT INTO beneficiaries(cuil,nombre,apellido,sexo,fecha_nacimiento,
                     provincia,codigo_provincia_indec,departamento,codigo_departamento_indec,cp)
                     VALUES(?,?,?,?,?,?,?,?,?,?)""",
                  (cuil(i), nom, pick(APELLIDOS), sexo, birth_from_group(g),
                   prov[0], prov[1], depto[0], depto[1], str(1000+rnd(0,8999))))
        ben_ids.append(c.lastrowid)
    conn.commit()
    print("  ✅ Beneficiarios creados: 300")

    # Benefits y pagos por periodo
    for periodo in PERIODOS:
        y, m = int(periodo[:4]), int(periodo[5:])
        # ~5% registros inválidos (últimos 15)
        for i, bid in enumerate(ben_ids):
            invalido = i >= 285  # ~5%
            if invalido:
                prog = pick(prog_ids)
                c.execute("""INSERT INTO benefits(beneficiary_id,cuil_raw,program_id,periodo_mes,estado_beneficio)
                             VALUES(?,?,?,?,?)""",
                          (None, None if random.random()>0.5 else "00000000000",
                           prog, periodo, "ACTIVO"))
                continue

            # concentración: 50%→1 prest, 30%→2, 15%→3+
            if i < 150: cant = 1
            elif i < 240: cant = 2
            else: cant = 3

            progs_elegidos = random.sample(prog_ids, min(cant, len(prog_ids)))

            # Forzar violaciones en 2026-03 (beneficiarios 220..239)
            if periodo == "2026-03" and 220 <= i < 240:
                progs_elegidos = [prog_ids[0], prog_ids[3]]  # AUH + PNC → incompatible
                if cant >= 3: progs_elegidos.append(prog_ids[1])

            for pid in progs_elegidos:
                estado = "INACTIVO" if random.random() < 0.08 else "ACTIVO"
                c.execute("""INSERT INTO benefits(beneficiary_id,cuil_raw,program_id,periodo_mes,estado_beneficio)
                             VALUES(?,?,?,?,?)""",
                          (bid, cuil(i), pid, periodo, estado))
                if estado == "ACTIVO":
                    # Monto según secretaría
                    sec = c.execute("SELECT secretaria_origen FROM programs WHERE id=?", (pid,)).fetchone()[0]
                    base = 80000 if "Inclusión" in sec else 95000 if "Desarrollo" in sec else 60000
                    monto = base + rnd(-10000, 20000)
                    day = rnd(1,28)
                    c.execute("""INSERT INTO payments(beneficiary_id,program_id,fecha_pago,periodo_mes,monto_prestacion)
                                 VALUES(?,?,?,?,?)""",
                              (bid, pid, f"{y}-{m:02d}-{day:02d}", periodo, monto))

        conn.commit()
        print(f"  ✅ Periodo {periodo} procesado")

    # Resumen
    nb = conn.execute("SELECT COUNT(*) FROM benefits").fetchone()[0]
    np = conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0]
    print(f"\n  📊 Benefits: {nb} | Pagos: {np}")
    conn.close()
    print("\n🎉 Seed completado!")

if __name__ == "__main__":
    run()
