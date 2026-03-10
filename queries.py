"""
queries.py — Todas las queries SQL (PostgreSQL)
Único lugar para cambiar lógica de consultas.
"""
import calendar
from datetime import date

from config import query, query_one


def _corte_date(period):
    y, m = int(period[:4]), int(period[5:7])
    last = calendar.monthrange(y, m)[1]
    return f"{y}-{m:02d}-{last}"


# ──────────────────────────────────────────────
# Cross-chart filter helper
# ──────────────────────────────────────────────

_GRUPO_RANGES = {
    "Niñez (0-12)": (0, 13),
    "Jóvenes (13-29)": (13, 30),
    "Adultos (30-59)": (30, 60),
    "Mayores (60+)": (60, 999),
}


def _apply_filters(filters, corte=None, has_ben=False, has_prog=False):
    """Build extra JOINs, WHERE clauses and params from cross-chart filters.

    Returns (join_sql, where_parts, params).
    """
    if not filters:
        return "", [], []

    joins = []
    where = []
    params = []

    need_ben = any(filters.get(k) for k in ("sexo", "provincia", "departamento", "grupo_etario"))
    need_prog = any(filters.get(k) for k in ("secretaria", "programa"))

    if need_ben and not has_ben:
        joins.append("JOIN beneficiaries ben ON b.beneficiary_id=ben.id")
    if need_prog and not has_prog:
        joins.append("JOIN programs p ON b.program_id=p.id")

    if filters.get("secretaria"):
        where.append("p.secretaria_origen=%s")
        params.append(filters["secretaria"])
    if filters.get("sexo"):
        where.append("ben.sexo=%s")
        params.append(filters["sexo"])
    if filters.get("programa"):
        where.append("p.nombre_programa=%s")
        params.append(filters["programa"])
    if filters.get("provincia"):
        where.append("ben.provincia=%s")
        params.append(filters["provincia"])
    if filters.get("departamento"):
        where.append("ben.departamento=%s")
        params.append(filters["departamento"])
    if filters.get("grupo_etario") and corte:
        rng = _GRUPO_RANGES.get(filters["grupo_etario"])
        if rng:
            lo, hi = rng
            where.append("EXTRACT(YEAR FROM AGE(%s::date, ben.fecha_nacimiento)) >= %s")
            params.extend([corte, lo])
            where.append("EXTRACT(YEAR FROM AGE(%s::date, ben.fecha_nacimiento)) < %s")
            params.extend([corte, hi])

    join_sql = " ".join(joins)
    return join_sql, where, params


# ──────────────────────────────────────────────
# Auth
# ──────────────────────────────────────────────

def get_user_by_email(conn, email):
    return query_one(conn, "SELECT * FROM users WHERE email=%s", (email,))


# ──────────────────────────────────────────────
# Lookups (periodos, programas, provincias)
# ──────────────────────────────────────────────

def get_periods(conn):
    rows = query(conn, "SELECT DISTINCT periodo_mes FROM benefits ORDER BY periodo_mes DESC")
    return [r["periodo_mes"] for r in rows]


def get_programs(conn):
    return query(conn, "SELECT id, nombre_programa FROM programs ORDER BY nombre_programa")


def get_provincias(conn):
    return query(conn, "SELECT DISTINCT provincia FROM beneficiaries ORDER BY provincia")


# ──────────────────────────────────────────────
# Dashboard indicators
# ──────────────────────────────────────────────

def get_summary(conn, period, filters=None):
    corte = _corte_date(period)

    # Base filter parts for benefits-only queries
    fj_bp, fw_bp, fp_bp = _apply_filters(filters, corte, has_ben=False, has_prog=False)
    # For queries that already have ben join
    _, fw_ben, fp_ben = _apply_filters(filters, corte, has_ben=True, has_prog=False)
    fj_ben_prog, fw_ben_nojoin, fp_ben_nojoin = _apply_filters(filters, corte, has_ben=False, has_prog=False)

    # Build extra WHERE for benefits-only queries (need JOINs)
    extra_where_bp = (" AND " + " AND ".join(fw_bp)) if fw_bp else ""
    extra_where_ben = (" AND " + " AND ".join(fw_ben)) if fw_ben else ""

    # I-01 Cobertura
    row = query_one(conn, f"""
        SELECT COUNT(DISTINCT b.cuil_raw) as total
        FROM benefits b {fj_bp}
        WHERE b.periodo_mes=%s AND b.estado_beneficio='ACTIVO'
          AND b.cuil_raw IS NOT NULL AND LENGTH(b.cuil_raw)=11
          {extra_where_bp}
    """, [period] + fp_bp)
    cobertura = row["total"] if row else 0

    # Total beneficiarios identificados
    row2 = query_one(conn, f"""
        SELECT COUNT(DISTINCT b.beneficiary_id) as total
        FROM benefits b {fj_bp}
        WHERE b.periodo_mes=%s AND b.estado_beneficio='ACTIVO' AND b.beneficiary_id IS NOT NULL
        {extra_where_bp}
    """, [period] + fp_bp)
    total_benef = row2["total"] if row2 else 0

    # Total prestaciones activas
    row3 = query_one(conn, f"""
        SELECT COUNT(*) as total
        FROM benefits b {fj_bp}
        WHERE b.periodo_mes=%s AND b.estado_beneficio='ACTIVO' AND b.beneficiary_id IS NOT NULL
        {extra_where_bp}
    """, [period] + fp_bp)
    total_prest = row3["total"] if row3 else 0

    prom_prestaciones = round(total_prest / total_benef, 2) if total_benef else 0

    # I-09 I-10 Pagos
    # payments needs join through benefits for filters
    if filters:
        pay_join = f"JOIN benefits b ON pay.beneficiary_id=b.beneficiary_id AND pay.program_id=b.program_id AND pay.periodo_mes=b.periodo_mes {fj_bp}"
        row4 = query_one(conn, f"""
            SELECT COALESCE(SUM(pay.monto_prestacion),0) as monto_total, COUNT(*) as cant
            FROM payments pay {pay_join}
            WHERE pay.periodo_mes=%s AND b.estado_beneficio='ACTIVO'
            {extra_where_bp}
        """, [period] + fp_bp)
    else:
        row4 = query_one(conn, """
            SELECT COALESCE(SUM(monto_prestacion),0) as monto_total, COUNT(*) as cant
            FROM payments WHERE periodo_mes=%s
        """, (period,))
    monto_total = row4["monto_total"] if row4 else 0
    prom_monto = round(monto_total / total_benef) if total_benef else 0

    # I-14 Tasa no identificados (not filtered — shows overall data quality)
    row5 = query_one(conn,
        "SELECT COUNT(*) as t FROM benefits WHERE periodo_mes=%s AND estado_beneficio='ACTIVO'",
        (period,))
    row6 = query_one(conn, """
        SELECT COUNT(*) as t FROM benefits
        WHERE periodo_mes=%s AND estado_beneficio='ACTIVO'
          AND (beneficiary_id IS NULL OR cuil_raw IS NULL)
    """, (period,))
    total_act = row5["t"] if row5 else 0
    no_ident = row6["t"] if row6 else 0
    tasa_no_ident = round((no_ident / total_act * 100), 2) if total_act else 0

    # I-15 Incompatibilidades
    if filters:
        # Need to filter b1 with the cross-filters
        fj_b1, fw_b1, fp_b1 = _apply_filters(filters, corte, has_ben=False, has_prog=False)
        # Replace 'ben.' with 'ben1.' and 'p.' with 'p1.' for b1 context
        fj_b1 = fj_b1.replace("beneficiaries ben", "beneficiaries ben1").replace("b.beneficiary_id=ben.id", "b1.beneficiary_id=ben1.id").replace("programs p", "programs p1").replace("b.program_id=p.id", "b1.program_id=p1.id")
        fw_b1_sql = [w.replace("ben.", "ben1.").replace("p.", "p1.") for w in fw_b1]
        extra_incomp = (" AND " + " AND ".join(fw_b1_sql)) if fw_b1_sql else ""
        incomp_row = query_one(conn, f"""
            SELECT COUNT(DISTINCT b1.beneficiary_id) as total
            FROM incompatibility_rules ir
            JOIN benefits b1 ON b1.program_id = ir.program_a_id
                AND b1.periodo_mes = %s AND b1.estado_beneficio = 'ACTIVO'
                AND b1.beneficiary_id IS NOT NULL
            {fj_b1}
            JOIN benefits b2 ON b2.program_id = ir.program_b_id
                AND b2.beneficiary_id = b1.beneficiary_id
                AND b2.periodo_mes = %s AND b2.estado_beneficio = 'ACTIVO'
            WHERE ir.is_compatible = 0 {extra_incomp}
        """, [period, period] + fp_b1)
    else:
        incomp_row = query_one(conn, """
            SELECT COUNT(DISTINCT b1.beneficiary_id) as total
            FROM incompatibility_rules ir
            JOIN benefits b1 ON b1.program_id = ir.program_a_id
                AND b1.periodo_mes = %s AND b1.estado_beneficio = 'ACTIVO'
                AND b1.beneficiary_id IS NOT NULL
            JOIN benefits b2 ON b2.program_id = ir.program_b_id
                AND b2.beneficiary_id = b1.beneficiary_id
                AND b2.periodo_mes = %s AND b2.estado_beneficio = 'ACTIVO'
            WHERE ir.is_compatible = 0
        """, (period, period))
    incomp = incomp_row["total"] if incomp_row else 0

    # I-05/06/07 Concentración
    conc_row = query_one(conn, f"""
        SELECT
            SUM(CASE WHEN cnt = 1 THEN 1 ELSE 0 END) as con_una,
            SUM(CASE WHEN cnt = 2 THEN 1 ELSE 0 END) as con_dos,
            SUM(CASE WHEN cnt >= 3 THEN 1 ELSE 0 END) as con_tres_mas
        FROM (
            SELECT b.beneficiary_id, COUNT(*) as cnt
            FROM benefits b {fj_bp}
            WHERE b.periodo_mes=%s AND b.estado_beneficio='ACTIVO' AND b.beneficiary_id IS NOT NULL
            {extra_where_bp}
            GROUP BY b.beneficiary_id
        ) sub
    """, [period] + fp_bp)
    con1 = (conc_row["con_una"] or 0) if conc_row else 0
    con2 = (conc_row["con_dos"] or 0) if conc_row else 0
    con3 = (conc_row["con_tres_mas"] or 0) if conc_row else 0

    return {
        "period": period,
        "cobertura": cobertura,
        "promedioPrestaciones": prom_prestaciones,
        "promedioMontoPorBenef": prom_monto,
        "montoTotal": round(monto_total),
        "tasaNoIdentificados": tasa_no_ident,
        "casosIncompatibilidad": incomp,
        "concentracion": {"conUna": con1, "conDos": con2, "conTresMas": con3},
    }


def get_by_secretaria(conn, period, filters=None):
    corte = _corte_date(period)
    fj, fw, fp = _apply_filters(filters, corte, has_ben=False, has_prog=True)
    extra = (" AND " + " AND ".join(fw)) if fw else ""
    return query(conn, f"""
        SELECT p.secretaria_origen as secretaria, COUNT(DISTINCT b.cuil_raw) as total
        FROM benefits b JOIN programs p ON b.program_id=p.id {fj}
        WHERE b.periodo_mes=%s AND b.estado_beneficio='ACTIVO' AND b.cuil_raw IS NOT NULL
        {extra}
        GROUP BY p.secretaria_origen ORDER BY total DESC
    """, [period] + fp)


def get_by_provincia(conn, period, filters=None):
    corte = _corte_date(period)
    fj, fw, fp = _apply_filters(filters, corte, has_ben=True, has_prog=False)
    extra = (" AND " + " AND ".join(fw)) if fw else ""
    return query(conn, f"""
        SELECT ben.provincia, COUNT(DISTINCT b.beneficiary_id) as total
        FROM benefits b JOIN beneficiaries ben ON b.beneficiary_id=ben.id {fj}
        WHERE b.periodo_mes=%s AND b.estado_beneficio='ACTIVO'
        {extra}
        GROUP BY ben.provincia ORDER BY total DESC
    """, [period] + fp)


def get_by_departamento(conn, period, filters=None):
    corte = _corte_date(period)
    fj, fw, fp = _apply_filters(filters, corte, has_ben=True, has_prog=False)
    extra = (" AND " + " AND ".join(fw)) if fw else ""
    rows = query(conn, f"""
        SELECT ben.departamento, ben.provincia, COUNT(DISTINCT b.beneficiary_id) as total
        FROM benefits b JOIN beneficiaries ben ON b.beneficiary_id=ben.id {fj}
        WHERE b.periodo_mes=%s AND b.estado_beneficio='ACTIVO'
        {extra}
        GROUP BY ben.departamento, ben.provincia ORDER BY total DESC LIMIT 10
    """, [period] + fp)
    return [{**r, "label": f"{r['departamento']}, {r['provincia']}"} for r in rows]


def get_by_programa(conn, period, filters=None):
    corte = _corte_date(period)
    fj, fw, fp = _apply_filters(filters, corte, has_ben=False, has_prog=True)
    extra = (" AND " + " AND ".join(fw)) if fw else ""
    return query(conn, f"""
        SELECT p.nombre_programa as programa, COUNT(DISTINCT b.beneficiary_id) as total
        FROM benefits b JOIN programs p ON b.program_id=p.id {fj}
        WHERE b.periodo_mes=%s AND b.estado_beneficio='ACTIVO' AND b.beneficiary_id IS NOT NULL
        {extra}
        GROUP BY p.nombre_programa ORDER BY total DESC
    """, [period] + fp)


def get_by_sexo(conn, period, filters=None):
    corte = _corte_date(period)
    fj, fw, fp = _apply_filters(filters, corte, has_ben=True, has_prog=False)
    extra = (" AND " + " AND ".join(fw)) if fw else ""
    return query(conn, f"""
        SELECT ben.sexo,
               CASE ben.sexo WHEN 'M' THEN 'Masculino' WHEN 'F' THEN 'Femenino'
                             WHEN 'X' THEN 'No binario' ELSE 'No informado' END as label,
               COUNT(DISTINCT ben.id) as total
        FROM benefits b JOIN beneficiaries ben ON b.beneficiary_id=ben.id {fj}
        WHERE b.periodo_mes=%s AND b.estado_beneficio='ACTIVO'
        {extra}
        GROUP BY ben.sexo ORDER BY total DESC
    """, [period] + fp)


def get_by_grupo_etario(conn, period, filters=None):
    corte = _corte_date(period)
    fj, fw, fp = _apply_filters(filters, corte, has_ben=True, has_prog=False)
    extra = (" AND " + " AND ".join(fw)) if fw else ""
    return query(conn, f"""
        SELECT
          CASE
            WHEN EXTRACT(YEAR FROM AGE(%s::date, ben.fecha_nacimiento)) < 13 THEN 'Niñez (0-12)'
            WHEN EXTRACT(YEAR FROM AGE(%s::date, ben.fecha_nacimiento)) < 30 THEN 'Jóvenes (13-29)'
            WHEN EXTRACT(YEAR FROM AGE(%s::date, ben.fecha_nacimiento)) < 60 THEN 'Adultos (30-59)'
            ELSE 'Mayores (60+)'
          END as grupo,
          COUNT(DISTINCT ben.id) as total
        FROM benefits b JOIN beneficiaries ben ON b.beneficiary_id=ben.id {fj}
        WHERE b.periodo_mes=%s AND b.estado_beneficio='ACTIVO'
        {extra}
        GROUP BY 1 ORDER BY MIN(ben.fecha_nacimiento) DESC
    """, [corte, corte, corte, period] + fp)


def get_evolucion(conn, filters=None):
    if not filters:
        return query(conn, """
            SELECT periodo_mes, COALESCE(SUM(monto_prestacion),0) as total
            FROM payments
            GROUP BY periodo_mes
            ORDER BY periodo_mes ASC
        """)
    # With filters, join through benefits to ben/programs
    corte = None  # evolucion spans all periods, skip grupo_etario filter
    f = {k: v for k, v in filters.items() if k != "grupo_etario"}
    fj, fw, fp = _apply_filters(f, corte, has_ben=False, has_prog=False)
    extra = (" AND " + " AND ".join(fw)) if fw else ""
    return query(conn, f"""
        SELECT pay.periodo_mes, COALESCE(SUM(pay.monto_prestacion),0) as total
        FROM payments pay
        JOIN benefits b ON pay.beneficiary_id=b.beneficiary_id
            AND pay.program_id=b.program_id AND pay.periodo_mes=b.periodo_mes
        {fj}
        WHERE b.estado_beneficio='ACTIVO' {extra}
        GROUP BY pay.periodo_mes
        ORDER BY pay.periodo_mes ASC
    """, fp)


# ──────────────────────────────────────────────
# Nominal
# ──────────────────────────────────────────────

def get_nominal_list(conn, period, filters, page, page_size):
    cuil = filters.get("cuil", "")
    provincia = filters.get("provincia", "")
    programa_id = filters.get("programa", "")
    sexo = filters.get("sexo", "")
    estado = filters.get("estado", "ACTIVO")
    offset = (page - 1) * page_size

    where = ["b.periodo_mes=%s"]
    params = [period]
    if estado:
        where.append("b.estado_beneficio=%s"); params.append(estado)
    if provincia:
        where.append("ben.provincia=%s"); params.append(provincia)
    if sexo:
        where.append("ben.sexo=%s"); params.append(sexo)
    if cuil:
        where.append("ben.cuil LIKE %s"); params.append(f"{cuil}%")
    if programa_id:
        where.append("b.program_id=%s"); params.append(programa_id)

    where_sql = " AND ".join(where)

    count_row = query_one(conn, f"""
        SELECT COUNT(DISTINCT ben.id) as t
        FROM benefits b JOIN beneficiaries ben ON b.beneficiary_id=ben.id
        WHERE {where_sql}
    """, params)
    total = count_row["t"] if count_row else 0

    rows = query(conn, f"""
        SELECT ben.id, ben.cuil, ben.nombre, ben.apellido, ben.sexo,
               ben.fecha_nacimiento, ben.provincia, ben.departamento,
               COALESCE(cnt.cant, 0) as cant_prestaciones
        FROM benefits b
        JOIN beneficiaries ben ON b.beneficiary_id=ben.id
        LEFT JOIN (
            SELECT beneficiary_id, COUNT(*) as cant
            FROM benefits
            WHERE periodo_mes=%s AND estado_beneficio='ACTIVO'
            GROUP BY beneficiary_id
        ) cnt ON cnt.beneficiary_id=ben.id
        WHERE {where_sql}
        GROUP BY ben.id, ben.cuil, ben.nombre, ben.apellido, ben.sexo,
                 ben.fecha_nacimiento, ben.provincia, ben.departamento, cnt.cant
        ORDER BY ben.apellido, ben.nombre
        LIMIT %s OFFSET %s
    """, [period] + params + [page_size, offset])

    corte = _corte_date(period)
    items = []
    for r in rows:
        fn = r["fecha_nacimiento"]
        try:
            edad = int((date.fromisoformat(corte) - fn).days / 365.25)
        except Exception:
            try:
                edad = int((date.fromisoformat(corte) - date.fromisoformat(str(fn)[:10])).days / 365.25)
            except Exception:
                edad = None
        items.append({
            "id": r["id"], "cuil": r["cuil"],
            "nombre": r["nombre"], "apellido": r["apellido"],
            "sexo": r["sexo"], "edad": edad,
            "provincia": r["provincia"], "departamento": r["departamento"],
            "cantPrestaciones": r["cant_prestaciones"],
        })
    return {"items": items, "total": total, "page": page, "pageSize": page_size}


def get_nominal_detail(conn, bid, period):
    ben = query_one(conn, "SELECT * FROM beneficiaries WHERE id=%s", (bid,))
    if not ben:
        return None

    prestaciones = query(conn, """
        SELECT b.estado_beneficio, b.periodo_mes, p.nombre_programa, p.secretaria_origen
        FROM benefits b JOIN programs p ON b.program_id=p.id
        WHERE b.beneficiary_id=%s AND b.periodo_mes=%s ORDER BY p.nombre_programa
    """, (bid, period))

    pagos = query(conn, """
        SELECT pay.fecha_pago, pay.monto_prestacion, p.nombre_programa
        FROM payments pay JOIN programs p ON pay.program_id=p.id
        WHERE pay.beneficiary_id=%s AND pay.periodo_mes=%s
        ORDER BY pay.fecha_pago
    """, (bid, period))

    fn = ben["fecha_nacimiento"]
    corte = _corte_date(period)
    try:
        edad = int((date.fromisoformat(corte) - fn).days / 365.25)
    except Exception:
        try:
            edad = int((date.fromisoformat(corte) - date.fromisoformat(str(fn)[:10])).days / 365.25)
        except Exception:
            edad = None

    return {
        "id": ben["id"], "cuil": ben["cuil"],
        "nombre": ben["nombre"], "apellido": ben["apellido"],
        "sexo": ben["sexo"], "edad": edad,
        "fecha_nacimiento": str(fn)[:10],
        "provincia": ben["provincia"], "departamento": ben["departamento"],
        "cp": ben["cp"],
        "prestaciones": [dict(r) for r in prestaciones],
        "pagos": [dict(r) for r in pagos],
    }
