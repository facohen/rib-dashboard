"""
queries.py — Todas las queries SQL (PostgreSQL)
Único lugar para cambiar lógica de consultas.

Estrategia de performance:
- Todo el dashboard → queries sobre mv_cross (~100-130K filas, <10ms)
- Cobertura/identificación → mv_cobertura (12 filas)
- Nominal → queries optimizadas con edad en SQL
"""
import calendar
from datetime import date

from config import query, query_one


def _corte_date(period):
    y, m = int(period[:4]), int(period[5:7])
    last = calendar.monthrange(y, m)[1]
    return f"{y}-{m:02d}-{last}"


# ──────────────────────────────────────────────
# Cross-filter helpers for mv_cross
# ──────────────────────────────────────────────

# Map filter keys to mv_cross columns
_CROSS_FILTER_MAP = {
    "secretaria": "secretaria_origen",
    "programa": "nombre_programa",
    "provincia": "provincia",
    "sexo": "sexo",
    "grupo_etario": "grupo_etario",
}

# Map grupo_etario filter values from the frontend to groups in mv_cross
_GRUPO_ETARIO_MAP = {
    "0-4 años": "0-4 años",
    "5-12 años": "5-12 años",
    "13-17 años": "13-17 años",
    "18-29 años": "18-29 años",
    "30-59 años": "30-59 años",
    "60+ años": "60+ años",
    # Also handle the old filter keys
    "Niñez (0-12)": ["0-4 años", "5-12 años"],
    "Jóvenes (13-29)": ["13-17 años", "18-29 años"],
    "Adultos (30-59)": ["30-59 años"],
    "Mayores (60+)": ["60+ años"],
}


def _has_filters(filters):
    """Check if there are any active cross-chart filters."""
    return filters and any(filters.get(k) for k in _CROSS_FILTER_MAP)


def _cross_where(filters, exclude=None):
    """Build WHERE clauses and params for mv_cross from filters."""
    where = []
    params = []
    if not filters:
        return where, params

    for fkey, col in _CROSS_FILTER_MAP.items():
        if fkey == exclude:
            continue
        val = filters.get(fkey)
        if not val:
            continue

        if fkey == "grupo_etario":
            mapped = _GRUPO_ETARIO_MAP.get(val)
            if mapped is None:
                continue
            if isinstance(mapped, list):
                placeholders = ",".join(["%s"] * len(mapped))
                where.append(f"{col} IN ({placeholders})")
                params.extend(mapped)
            else:
                where.append(f"{col} = %s")
                params.append(mapped)
        else:
            where.append(f"{col} = %s")
            params.append(val)

    return where, params


_MV_COL = {
    "personas": "personas",
    "beneficios": "beneficios",
    "montos": "montos",
    "monto_persona": "CASE WHEN personas>0 THEN montos/personas ELSE 0 END",
    "monto_beneficio": "CASE WHEN beneficios>0 THEN montos/beneficios ELSE 0 END",
}

_METRIC_COL = {
    "personas": "SUM(personas)",
    "beneficios": "SUM(beneficios)",
    "montos": "SUM(montos)",
    "monto_persona": "CASE WHEN SUM(personas)>0 THEN SUM(montos)/SUM(personas) ELSE 0 END",
    "monto_beneficio": "CASE WHEN SUM(beneficios)>0 THEN SUM(montos)/SUM(beneficios) ELSE 0 END",
}


def _cross_query(conn, period, filters, group_col, metric="personas",
                 exclude_filter=None, extra_cols=""):
    """Generic query on mv_cross with filters, grouped by group_col."""
    sel = _METRIC_COL.get(metric, "SUM(personas)")
    where = ["periodo_mes = %s"]
    params = [period]

    fw, fp = _cross_where(filters, exclude=exclude_filter)
    where.extend(fw)
    params.extend(fp)

    where_sql = " AND ".join(where)
    extra = f", {extra_cols}" if extra_cols else ""
    group_extra = f", {extra_cols}" if extra_cols else ""

    rows = query(conn, f"""
        SELECT {group_col}{extra}, {sel} AS total
        FROM mv_cross
        WHERE {where_sql}
        GROUP BY {group_col}{group_extra}
        ORDER BY total DESC
    """, params)
    return rows


# ──────────────────────────────────────────────
# Auth
# ──────────────────────────────────────────────

def get_user_by_email(conn, email):
    return query_one(conn, "SELECT * FROM users WHERE email=%s", (email,))


# ──────────────────────────────────────────────
# Lookups (periodos, programas, provincias)
# Uses lookup tables instead of scanning huge tables.
# ──────────────────────────────────────────────

def get_periods(conn):
    try:
        rows = query(conn, "SELECT periodo_mes FROM periods ORDER BY periodo_mes DESC")
    except Exception:
        conn.rollback()
        rows = query(conn, "SELECT DISTINCT periodo_mes FROM benefits ORDER BY periodo_mes DESC")
    return [r["periodo_mes"] for r in rows]


def get_programs(conn):
    return query(conn, "SELECT id, nombre_programa FROM programs ORDER BY nombre_programa")


def get_provincias(conn):
    try:
        return query(conn, "SELECT provincia FROM provincias_lookup ORDER BY provincia")
    except Exception:
        conn.rollback()
        return query(conn, "SELECT DISTINCT provincia FROM beneficiaries ORDER BY provincia")


# ──────────────────────────────────────────────
# Dashboard indicators — all from mv_cross + mv_cobertura
# ──────────────────────────────────────────────

def get_summary(conn, period, filters=None):
    # Aggregates from mv_cross
    where = ["periodo_mes = %s"]
    params = [period]
    fw, fp = _cross_where(filters)
    where.extend(fw)
    params.extend(fp)
    where_sql = " AND ".join(where)

    row = query_one(conn, f"""
        SELECT SUM(personas) AS total_benef,
               SUM(beneficios) AS total_prest,
               SUM(montos) AS total_monto,
               COUNT(DISTINCT nombre_programa) AS cant_programas
        FROM mv_cross WHERE {where_sql}
    """, params)

    total_benef = int(row["total_benef"] or 0) if row else 0
    total_prest = int(row["total_prest"] or 0) if row else 0
    monto_total = float(row["total_monto"] or 0) if row else 0
    cant_programas = int(row["cant_programas"] or 0) if row else 0

    if total_benef == 0:
        return _empty_summary(period)

    # Concentración from mv_cross (GROUP BY cant_prestaciones)
    conc_rows = query(conn, f"""
        SELECT cant_prestaciones, SUM(personas) AS total
        FROM mv_cross WHERE {where_sql}
        GROUP BY cant_prestaciones
    """, params)
    con1 = con2 = con3 = 0
    for r in conc_rows:
        cp = r["cant_prestaciones"]
        t = int(r["total"] or 0)
        if cp == "1":
            con1 = t
        elif cp == "2":
            con2 = t
        elif cp == "3+":
            con3 = t

    # Cobertura + identificación from mv_cobertura (not affected by demographic filters)
    cob = query_one(conn,
        "SELECT * FROM mv_cobertura WHERE periodo_mes = %s", (period,))
    cobertura = cob["cobertura"] if cob else 0
    tasa_no_ident = round(
        (cob["no_identificados"] / cob["total_prest"] * 100), 2
    ) if cob and cob["total_prest"] else 0

    # Incompatibilidades — direct query (small, fast with indexes)
    incomp = query_one(conn, """
        SELECT COUNT(DISTINCT b1.beneficiary_id) AS cant_incompatibles
        FROM incompatibility_rules ir
        JOIN benefits b1 ON b1.program_id = ir.program_a_id
            AND b1.estado_beneficio = 'ACTIVO' AND b1.periodo_mes = %s
        JOIN benefits b2 ON b2.beneficiary_id = b1.beneficiary_id
            AND b2.program_id = ir.program_b_id
            AND b2.periodo_mes = b1.periodo_mes
            AND b2.estado_beneficio = 'ACTIVO'
        WHERE ir.is_compatible = 0
    """, (period,))
    incomp_count = incomp["cant_incompatibles"] if incomp else 0

    prom_prest = round(total_prest / total_benef, 2) if total_benef else 0
    prom_monto = round(monto_total / total_benef) if total_benef else 0

    return {
        "period": period,
        "cobertura": cobertura,
        "promedioPrestaciones": prom_prest,
        "promedioMontoPorBenef": prom_monto,
        "montoTotal": round(monto_total),
        "tasaNoIdentificados": tasa_no_ident,
        "casosIncompatibilidad": incomp_count,
        "cantidadProgramas": cant_programas,
        "concentracion": {"conUna": con1, "conDos": con2, "conTresMas": con3},
    }


def _empty_summary(period):
    return {
        "period": period,
        "cobertura": 0,
        "promedioPrestaciones": 0,
        "promedioMontoPorBenef": 0,
        "montoTotal": 0,
        "tasaNoIdentificados": 0,
        "casosIncompatibilidad": 0,
        "cantidadProgramas": 0,
        "concentracion": {"conUna": 0, "conDos": 0, "conTresMas": 0},
    }


def get_by_secretaria(conn, period, filters=None, metric="personas"):
    rows = _cross_query(conn, period, filters, "secretaria_origen",
                        metric=metric, exclude_filter="secretaria")
    return [{"secretaria": r["secretaria_origen"], "total": r["total"]} for r in rows]


def get_by_provincia(conn, period, filters=None, metric="personas"):
    return _cross_query(conn, period, filters, "provincia",
                        metric=metric, exclude_filter="provincia")


def get_by_departamento(conn, period, filters=None):
    # Departamento is not in mv_cross — use raw query with indexes
    corte = _corte_date(period)
    fj, fw, fp = _apply_filters_raw(filters, corte, has_ben=True, has_prog=False)
    extra = _extra_where(fw)
    rows = query(conn, f"""
        SELECT ben.departamento, ben.provincia, COUNT(DISTINCT b.beneficiary_id) AS total
        FROM benefits b JOIN beneficiaries ben ON b.beneficiary_id=ben.id {fj}
        WHERE b.periodo_mes=%s AND b.estado_beneficio='ACTIVO'
        {extra}
        GROUP BY ben.departamento, ben.provincia ORDER BY total DESC LIMIT 10
    """, [period] + fp)
    return [{**r, "label": f"{r['departamento']}, {r['provincia']}"} for r in rows]


def get_by_programa(conn, period, filters=None, metric="personas"):
    rows = _cross_query(conn, period, filters, "nombre_programa",
                        metric=metric, exclude_filter="programa")
    return [{"programa": r["nombre_programa"], "total": r["total"]} for r in rows]


def get_by_sexo(conn, period, filters=None, metric="personas"):
    sel = _METRIC_COL.get(metric, "SUM(personas)")
    where = ["periodo_mes = %s"]
    params = [period]
    fw, fp = _cross_where(filters, exclude="sexo")
    where.extend(fw)
    params.extend(fp)
    where_sql = " AND ".join(where)

    return query(conn, f"""
        SELECT sexo, sexo_label AS label, {sel} AS total
        FROM mv_cross
        WHERE {where_sql}
        GROUP BY sexo, sexo_label
        ORDER BY total DESC
    """, params)


def get_by_grupo_etario(conn, period, filters=None, metric="personas"):
    sel = _METRIC_COL.get(metric, "SUM(personas)")
    where = ["periodo_mes = %s"]
    params = [period]
    fw, fp = _cross_where(filters, exclude="grupo_etario")
    where.extend(fw)
    params.extend(fp)
    where_sql = " AND ".join(where)

    return query(conn, f"""
        SELECT grupo_etario AS grupo, {sel} AS total
        FROM mv_cross
        WHERE {where_sql}
        GROUP BY grupo_etario
        ORDER BY
            CASE grupo_etario
                WHEN '0-4 años' THEN 1
                WHEN '5-12 años' THEN 2
                WHEN '13-17 años' THEN 3
                WHEN '18-29 años' THEN 4
                WHEN '30-59 años' THEN 5
                WHEN '60+ años' THEN 6
            END
    """, params)


def get_evolucion(conn, filters=None, metric="montos"):
    sel = _METRIC_COL.get(metric, "SUM(personas)")
    where = []
    params = []
    # Exclude grupo_etario from evolution filters
    f = {k: v for k, v in filters.items() if k != "grupo_etario"} if filters else {}
    fw, fp = _cross_where(f)
    where.extend(fw)
    params.extend(fp)
    where_sql = (" AND " + " AND ".join(where)) if where else ""

    return query(conn, f"""
        SELECT periodo_mes, {sel} AS total
        FROM mv_cross
        WHERE 1=1 {where_sql}
        GROUP BY periodo_mes
        ORDER BY periodo_mes ASC
    """, params)


# ──────────────────────────────────────────────
# Raw filter helper (for departamento fallback)
# ──────────────────────────────────────────────

_GRUPO_RANGES = {
    "Niñez (0-12)": (0, 13),
    "Jóvenes (13-29)": (13, 30),
    "Adultos (30-59)": (30, 60),
    "Mayores (60+)": (60, 999),
}

def _extra_where(fw):
    """Join filter WHERE parts with AND prefix, or empty string if none."""
    return (" AND " + " AND ".join(fw)) if fw else ""


def _apply_filters_raw(filters, corte=None, has_ben=False, has_prog=False):
    """Build extra JOINs, WHERE clauses and params from cross-chart filters.
    For use with raw benefits table queries (departamento).
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
        val = filters["grupo_etario"]
        rng = _GRUPO_RANGES.get(val)
        if rng:
            lo, hi = rng
            where.append("EXTRACT(YEAR FROM AGE(%s::date, ben.fecha_nacimiento)) >= %s")
            params.extend([corte, lo])
            where.append("EXTRACT(YEAR FROM AGE(%s::date, ben.fecha_nacimiento)) < %s")
            params.extend([corte, hi])

    join_sql = " ".join(joins)
    return join_sql, where, params


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

    # Count query
    count_row = query_one(conn, f"""
        SELECT COUNT(DISTINCT ben.id) AS t
        FROM benefits b JOIN beneficiaries ben ON b.beneficiary_id=ben.id
        WHERE {where_sql}
    """, params)
    total = count_row["t"] if count_row else 0

    # Corte date for age calculation in SQL
    corte = _corte_date(period)

    # Main query with age computed in SQL and cant_prestaciones respecting estado filter
    cnt_where = "b2.periodo_mes=%s"
    cnt_params = [period]
    if estado:
        cnt_where += " AND b2.estado_beneficio=%s"
        cnt_params = [period, estado]

    rows = query(conn, f"""
        SELECT ben.id, ben.cuil, ben.nombre, ben.apellido, ben.sexo,
               ben.fecha_nacimiento, ben.provincia, ben.departamento,
               EXTRACT(YEAR FROM AGE(%s::date, ben.fecha_nacimiento))::int AS edad,
               COALESCE(cnt.cant, 0) AS cant_prestaciones
        FROM benefits b
        JOIN beneficiaries ben ON b.beneficiary_id=ben.id
        LEFT JOIN (
            SELECT b2.beneficiary_id, COUNT(*) AS cant
            FROM benefits b2
            WHERE {cnt_where}
            GROUP BY b2.beneficiary_id
        ) cnt ON cnt.beneficiary_id=ben.id
        WHERE {where_sql}
        GROUP BY ben.id, ben.cuil, ben.nombre, ben.apellido, ben.sexo,
                 ben.fecha_nacimiento, ben.provincia, ben.departamento, cnt.cant
        ORDER BY ben.apellido, ben.nombre
        LIMIT %s OFFSET %s
    """, [corte] + cnt_params + params + [page_size, offset])

    items = []
    for r in rows:
        items.append({
            "id": r["id"], "cuil": r["cuil"],
            "nombre": r["nombre"], "apellido": r["apellido"],
            "sexo": r["sexo"], "edad": r["edad"],
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

    corte = _corte_date(period)
    fn = ben["fecha_nacimiento"]
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
