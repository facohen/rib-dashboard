"""
seed_sintetico.py — Genera datos sinteticos para testing/desarrollo.

Crea schema + genera beneficiarios, benefits y pagos con numpy vectorizado.

Uso:
    python seed_sintetico.py              # 8M (default)
    python seed_sintetico.py --small      # 10K
    python seed_sintetico.py --medium     # 100K
    python seed_sintetico.py --1m         # 1M
"""
import io
import sys
import time

import numpy as np

from db_schema import get_conn, create_schema, create_indexes, create_users, DATABASE_URL

RANDOM_SEED = 42

# ─── Datos de referencia ─────────────────────────────────

PROVINCIAS = [
    ("Buenos Aires", "06", [("La Matanza","06427"),("Lomas de Zamora","06490"),("Quilmes","06658"),("General Pueyrredon","06357"),("Almirante Brown","06028"),("Moreno","06560"),("Merlo","06539"),("Lanus","06427"),("Florencio Varela","06274"),("Tigre","06840")]),
    ("Cordoba",      "14", [("Capital","14014"),("Rio Cuarto","14147"),("San Justo","14175"),("Punilla","14133"),("Colon","14021")]),
    ("Santa Fe",     "82", [("Rosario","82119"),("La Capital","82070"),("Rafaela","82075"),("Caseros","82021"),("General Lopez","82049")]),
    ("Mendoza",      "50", [("Capital","50028"),("Godoy Cruz","50049"),("Lujan de Cuyo","50063"),("Guaymallen","50056"),("San Rafael","50098")]),
    ("Tucuman",      "90", [("Capital","90021"),("Yerba Buena","90126"),("Cruz Alta","90028"),("Lules","90063")]),
    ("Salta",        "66", [("Capital","66014"),("Oran","66077"),("San Martin","66091"),("Tartagal","66098")]),
    ("Chaco",        "22", [("San Fernando","22007"),("Comandante Fernandez","22042"),("Libertador Gral. San Martin","22063")]),
    ("Corrientes",   "18", [("Capital","18021"),("Goya","18098"),("Mercedes","18112")]),
    ("Misiones",     "54", [("Capital","54028"),("Eldorado","54035"),("Obera","54063")]),
    ("Jujuy",        "38", [("Dr. Manuel Belgrano","38007"),("Palpala","38049"),("Ledesma","38042")]),
    ("Entre Rios",   "30", [("Parana","30084"),("Concordia","30021"),("Gualeguaychu","30042")]),
    ("Santiago del Estero", "86", [("Capital","86007"),("Banda","86014"),("Robles","86098")]),
    ("San Juan",     "70", [("Capital","70007"),("Rawson","70084"),("Rivadavia","70091")]),
    ("San Luis",     "74", [("La Capital","74014"),("Pedernera","74049")]),
    ("Formosa",      "34", [("Formosa","34007"),("Pilcomayo","34049")]),
    ("Catamarca",    "10", [("Capital","10007"),("Valle Viejo","10098")]),
    ("La Rioja",     "46", [("Capital","46007"),("Chilecito","46021")]),
    ("Neuquen",      "58", [("Confluencia","58014"),("Zapala","58098")]),
    ("Rio Negro",    "62", [("General Roca","62042"),("Bariloche","62007")]),
    ("Chubut",       "26", [("Rawson","26042"),("Escalante","26014")]),
    ("Santa Cruz",   "78", [("Guer Aike","78007"),("Deseado","78014")]),
    ("La Pampa",     "42", [("Capital","42007"),("Maraco","42049")]),
    ("Tierra del Fuego", "94", [("Ushuaia","94007"),("Rio Grande","94014")]),
]
PROV_WEIGHTS = [38, 8, 8, 4, 4, 3, 3, 2, 3, 2, 3, 2, 2, 1, 1, 1, 1, 1, 2, 1, 1, 1, 0.5]

SECRETARIAS = [
    "Secretaria de Inclusion Social",
    "Secretaria de Desarrollo Humano",
    "Secretaria de Economia Social",
]
PROGRAMAS = [
    ("Asignacion Universal por Hijo", 0),
    ("Becas PROGRESAR", 0),
    ("Tarjeta Alimentar", 0),
    ("Pensiones no Contributivas", 1),
    ("SUMAR - Salud", 1),
    ("Hacemos Futuro", 1),
    ("Credito ARGENTA", 2),
    ("Plan Potenciar Trabajo", 2),
]

NOMBRES_M = ["Carlos","Juan","Diego","Martin","Luis","Pedro","Roberto","Sergio","Mario","Daniel",
             "Miguel","Gustavo","Alejandro","Ricardo","Fernando","Ezequiel","Facundo","Gabriel",
             "Hector","Ignacio","Pablo","Nicolas","Matias","Tomas","Agustin","Santiago","Ramon",
             "Oscar","Ruben","Eduardo","Marcelo","Leonardo","Adrian","Dario","Emilio","Javier"]
NOMBRES_F = ["Maria","Ana","Laura","Sandra","Patricia","Claudia","Carolina","Gabriela","Valeria",
             "Marcela","Natalia","Silvana","Andrea","Monica","Veronica","Lorena","Debora","Alejandra",
             "Viviana","Cecilia","Rosa","Marta","Soledad","Florencia","Julieta","Romina","Daniela",
             "Paola","Karina","Silvia","Norma","Liliana","Teresa","Graciela","Stella","Miriam"]
APELLIDOS = ["Garcia","Rodriguez","Gonzalez","Fernandez","Lopez","Martinez","Sanchez","Perez",
             "Gomez","Diaz","Hernandez","Castro","Romero","Torres","Alvarez","Ruiz","Ramirez",
             "Flores","Acosta","Medina","Suarez","Ramos","Molina","Moreno","Ortega","Silva",
             "Ponce","Vera","Rosa","Reyes","Cabrera","Aguirre","Navarro","Figueroa","Quiroga",
             "Mansilla","Paz","Benitez","Miranda","Rios","Sosa","Villalba","Vega","Bravo","Luna"]

PERIODOS = ["2025-04","2025-05","2025-06","2025-07","2025-08","2025-09",
            "2025-10","2025-11","2025-12","2026-01","2026-02","2026-03"]
INCOMP_PAIRS = [(0,3),(1,7),(2,5),(6,7)]

# Monto base por programa (indice en PROGRAMAS)
PROG_BASE_MONTO = np.array([80000, 80000, 80000, 95000, 95000, 95000, 60000, 60000])


# ─── Helpers ─────────────────────────────────────────────

def _cuil_array(n):
    prefixes = np.array(["20","23","24","27"])
    nums = np.arange(20000000, 20000000 + n)
    prefix = prefixes[np.arange(n) % 4]
    check = (np.arange(n) % 10).astype(str)
    return np.char.add(np.char.add(prefix, nums.astype(str)), check)


def _copy_buf(cur, table, columns, buf):
    buf.seek(0)
    cur.copy_expert(
        f"COPY {table}({','.join(columns)}) FROM STDIN WITH (FORMAT csv, NULL '\\N')", buf)


def _build_csv_rows(arrays, n):
    """Concatena arrays numpy en lineas CSV."""
    row = arrays[0]
    for arr in arrays[1:]:
        row = np.char.add(np.char.add(row, ","), arr)
    return np.char.add(row, "\n")


# ─── Generacion ──────────────────────────────────────────

def _create_reference_data(conn):
    """Crea secretarias, programas e incompatibilidades. Retorna prog_ids."""
    cur = conn.cursor()

    sec_ids = []
    for sec in SECRETARIAS:
        cur.execute("INSERT INTO secretarias(nombre) VALUES(%s) RETURNING id", (sec,))
        sec_ids.append(cur.fetchone()[0])
    conn.commit()

    prog_ids = []
    for nombre, sec_idx in PROGRAMAS:
        cur.execute("INSERT INTO programs(secretaria_id, nombre_programa) VALUES(%s,%s) RETURNING id",
                    (sec_ids[sec_idx], nombre))
        prog_ids.append(cur.fetchone()[0])
    conn.commit()

    for a, b in INCOMP_PAIRS:
        cur.execute("INSERT INTO incompatibility_rules(program_a_id, program_b_id, is_compatible, descripcion) VALUES(%s,%s,0,%s)",
                    (prog_ids[a], prog_ids[b], f"{PROGRAMAS[a][0]} incompatible con {PROGRAMAS[b][0]}"))
    conn.commit()
    cur.close()

    print(f"  {len(SECRETARIAS)} secretarias, {len(prog_ids)} programas, {len(INCOMP_PAIRS)} reglas incomp.")
    return np.array(prog_ids)


def _generate_beneficiaries(conn, cur, n, rng):
    """Genera beneficiarios vectorizado con numpy. Retorna (cuils, first_ben_id)."""
    t0 = time.time()

    # Provincia
    flat_deptos = []
    for prov_name, prov_code, deptos in PROVINCIAS:
        for d_name, d_code in deptos:
            flat_deptos.append((prov_name, prov_code, d_name, d_code))

    total_w = sum(PROV_WEIGHTS)
    flat_probs = []
    offset = 0
    for i, (_, _, deptos) in enumerate(PROVINCIAS):
        p = PROV_WEIGHTS[i] / total_w / len(deptos)
        flat_probs.extend([p] * len(deptos))
    flat_probs = np.array(flat_probs)
    flat_probs /= flat_probs.sum()

    depto_idx = rng.choice(len(flat_deptos), size=n, p=flat_probs)
    fd_prov = np.array([fd[0] for fd in flat_deptos])
    fd_cprov = np.array([fd[1] for fd in flat_deptos])
    fd_depto = np.array([fd[2] for fd in flat_deptos])
    fd_cdepto = np.array([fd[3] for fd in flat_deptos])

    # Sexo
    sexo_pool = np.array(["M","M","M","F","F","F","F","F","X","NI"])
    ben_sexo = sexo_pool[rng.integers(0, len(sexo_pool), size=n)]

    # Edad
    age_groups = rng.choice(4, size=n, p=[0.20, 0.30, 0.35, 0.15])
    age_ranges = [(0, 12), (13, 29), (30, 59), (60, 85)]
    years = np.empty(n, dtype=np.int32)
    for g in range(4):
        mask = age_groups == g
        lo, hi = age_ranges[g]
        years[mask] = rng.integers(lo, hi + 1, size=mask.sum())

    y_part = (2026 - years).astype(str)
    m_part = np.char.zfill(rng.integers(1, 13, size=n).astype(str), 2)
    d_part = np.char.zfill(rng.integers(1, 29, size=n).astype(str), 2)
    ben_fecha = np.char.add(np.char.add(np.char.add(np.char.add(y_part, '-'), m_part), '-'), d_part)

    # Nombres
    arr_m, arr_f, arr_all = np.array(NOMBRES_M), np.array(NOMBRES_F), np.array(NOMBRES_M + NOMBRES_F)
    arr_ape = np.array(APELLIDOS)
    ben_nombre = np.empty(n, dtype='U20')
    mm, mf = ben_sexo == 'M', ben_sexo == 'F'
    ben_nombre[mm] = arr_m[rng.integers(0, len(arr_m), size=mm.sum())]
    ben_nombre[mf] = arr_f[rng.integers(0, len(arr_f), size=mf.sum())]
    mo = ~mm & ~mf
    ben_nombre[mo] = arr_all[rng.integers(0, len(arr_all), size=mo.sum())]
    ben_apellido = arr_ape[rng.integers(0, len(arr_ape), size=n)]

    ben_cuil = _cuil_array(n)
    ben_cp = (1000 + rng.integers(0, 9000, size=n)).astype(str)

    # COPY
    rows = _build_csv_rows([
        ben_cuil, ben_nombre, ben_apellido, ben_sexo, ben_fecha,
        fd_prov[depto_idx], fd_cprov[depto_idx], fd_depto[depto_idx],
        fd_cdepto[depto_idx], ben_cp], n)

    buf = io.StringIO()
    buf.write(''.join(rows))
    _copy_buf(cur, "beneficiaries",
              ["cuil","nombre","apellido","sexo","fecha_nacimiento",
               "provincia","codigo_provincia_indec","cp"], buf)
    conn.commit()
    buf.close()
    del rows

    cur.execute("SELECT MIN(id) FROM beneficiaries")
    first_id = cur.fetchone()[0]
    print(f"  {n:,} beneficiarios ({time.time()-t0:.0f}s)")
    return ben_cuil, first_id


def _generate_period(conn, cur, rng, periodo, n, ben_cuil, first_ben_id,
                     prog_ids, cant_progs, is_invalido, force_incomp):
    """Genera benefits + payments para un periodo."""
    t0 = time.time()
    n_progs = len(prog_ids)
    y, m = int(periodo[:4]), int(periodo[5:])
    periodo_prefix = f",{periodo},"
    date_prefix = f"{y}-{m:02d}-"

    b_parts, p_parts = [], []
    ben_cols = ["beneficiary_id","cuil_raw","program_id","periodo_mes","estado_beneficio"]
    pay_cols = ["beneficiary_id","program_id","fecha_pago","periodo_mes","monto_prestacion"]

    # Invalidos (5%)
    inv_idx = np.where(is_invalido)[0]
    if len(inv_idx) > 0:
        inv_progs = prog_ids[rng.integers(0, n_progs, size=len(inv_idx))]
        inv_cuils = np.where(rng.random(len(inv_idx)) > 0.5, "\\N", "00000000000")
        lines = np.char.add(np.char.add(np.char.add(np.char.add(
            "\\N,", inv_cuils), ","), inv_progs.astype(str)), periodo_prefix)
        b_parts.append(np.char.add(lines, "ACTIVO\n"))

    # Normal por cantidad de programas
    normal_mask = ~is_invalido & ~force_incomp
    for cant in [1, 2, 3]:
        g_idx = np.where(normal_mask & (cant_progs == cant))[0]
        if len(g_idx) == 0:
            continue
        gn = len(g_idx)
        g_bids = (first_ben_id + g_idx).astype(str)
        g_cuils = ben_cuil[g_idx]

        selected = rng.random((gn, n_progs)).argsort(axis=1)[:, :cant]
        prog_matrix = prog_ids[selected]
        estados = np.where(rng.random((gn, cant)) >= 0.08, "ACTIVO", "INACTIVO")

        flat_bids = np.repeat(g_bids, cant)
        flat_cuils = np.repeat(g_cuils, cant)
        flat_progs = prog_matrix.ravel().astype(str)
        flat_estados = estados.ravel()

        lines = _build_csv_rows([flat_bids, flat_cuils, flat_progs,
                                  np.full(len(flat_bids), periodo), flat_estados], len(flat_bids))
        b_parts.append(lines)

        # Payments para ACTIVO
        activo = estados.ravel() == "ACTIVO"
        na = activo.sum()
        if na > 0:
            a_pidx = prog_matrix.ravel()[activo] - prog_ids[0]
            montos = (PROG_BASE_MONTO[a_pidx] + rng.integers(-10000, 20001, size=na)).astype(str)
            days = np.char.zfill(rng.integers(1, 29, size=na).astype(str), 2)
            dates = np.char.add(date_prefix, days)
            plines = _build_csv_rows([flat_bids[activo], flat_progs[activo],
                                       dates, np.full(na, periodo), montos], na)
            p_parts.append(plines)

    # Incompatibles (~2%)
    incomp_idx = np.where(~is_invalido & force_incomp)[0]
    incomp_a = np.array([prog_ids[a] for a, _ in INCOMP_PAIRS])
    incomp_b = np.array([prog_ids[b] for _, b in INCOMP_PAIRS])

    if len(incomp_idx) > 0:
        ic_bids = (first_ben_id + incomp_idx).astype(str)
        ic_cuils = ben_cuil[incomp_idx]
        pair_sel = rng.integers(0, len(INCOMP_PAIRS), size=len(incomp_idx))

        for progs_col in [incomp_a[pair_sel], incomp_b[pair_sel]]:
            estados = np.where(rng.random(len(incomp_idx)) >= 0.08, "ACTIVO", "INACTIVO")
            lines = _build_csv_rows([ic_bids, ic_cuils, progs_col.astype(str),
                                      np.full(len(incomp_idx), periodo), estados], len(incomp_idx))
            b_parts.append(lines)

            act = estados == "ACTIVO"
            na = act.sum()
            if na > 0:
                pidx = progs_col[act] - prog_ids[0]
                montos = (PROG_BASE_MONTO[pidx] + rng.integers(-10000, 20001, size=na)).astype(str)
                days = np.char.zfill(rng.integers(1, 29, size=na).astype(str), 2)
                plines = _build_csv_rows([ic_bids[act], progs_col[act].astype(str),
                                           np.char.add(date_prefix, days),
                                           np.full(na, periodo), montos], na)
                p_parts.append(plines)

    # COPY
    all_ben = np.concatenate(b_parts)
    buf = io.StringIO()
    buf.write(''.join(all_ben))
    _copy_buf(cur, "benefits", ben_cols, buf)
    buf.close()

    payments_count = 0
    if p_parts:
        all_pay = np.concatenate(p_parts)
        payments_count = len(all_pay)
        buf = io.StringIO()
        buf.write(''.join(all_pay))
        _copy_buf(cur, "payments", pay_cols, buf)
        buf.close()

    conn.commit()
    print(f"  {periodo}: {len(all_ben):,} benefits, {payments_count:,} pagos ({time.time()-t0:.0f}s)")


# ─── Main ────────────────────────────────────────────────

def run(num_beneficiaries=8_000_000):
    rng = np.random.default_rng(RANDOM_SEED)
    n = num_beneficiaries

    conn = get_conn()
    cur = conn.cursor()

    t0 = time.time()
    db_display = DATABASE_URL.split('@')[-1] if '@' in DATABASE_URL else DATABASE_URL
    print(f"{'='*50}")
    print(f"  RIB -- Seed sintetico ({n:,} beneficiarios)")
    print(f"  DB: {db_display}")
    print(f"{'='*50}")

    # Schema
    create_schema(conn)
    create_users(conn)

    # Datos de referencia
    prog_ids = _create_reference_data(conn)

    # Beneficiarios
    ben_cuil, first_ben_id = _generate_beneficiaries(conn, cur, n, rng)

    # Pre-compute concentracion y flags
    conc_r = rng.random(n)
    cant_progs = np.where(conc_r < 0.50, 1, np.where(conc_r < 0.80, 2, 3))
    is_invalido = np.zeros(n, dtype=bool)
    is_invalido[n - int(n * 0.05):] = True
    force_incomp = (rng.random(n) < 0.02) & ~is_invalido

    # Benefits + payments por periodo
    for periodo in PERIODOS:
        _generate_period(conn, cur, rng, periodo, n, ben_cuil, first_ben_id,
                         prog_ids, cant_progs, is_invalido, force_incomp)

    # Indices
    print("\n  Creando indices...")
    create_indexes(conn)

    # ANALYZE
    conn.autocommit = True
    cur.execute("ANALYZE")
    conn.autocommit = False

    # Resumen
    for tbl in ["beneficiaries", "benefits", "payments"]:
        cur.execute(f"SELECT COUNT(*) FROM {tbl}")
        print(f"  {tbl}: {cur.fetchone()[0]:,}")

    elapsed = time.time() - t0
    print(f"\n{'='*50}")
    print(f"  Completado en {elapsed/60:.1f} minutos")
    print(f"{'='*50}")
    cur.close()
    conn.close()


if __name__ == "__main__":
    sizes = {"--small": 10_000, "--medium": 100_000, "--1m": 1_000_000}
    n = next((v for k, v in sizes.items() if k in sys.argv), 8_000_000)
    run(n)
