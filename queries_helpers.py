"""
queries_helpers.py — Infraestructura compartida para queries TD y TC.

Contiene: filtros de cross-chart, routing MV (resumen vs cross),
métricas, query genérica, lookups y auth.
Usado por queries_td.py y queries_tc.py.
"""
import calendar

from config import query, query_one


# ──────────────────────────────────────────────
# Date helpers
# ──────────────────────────────────────────────

def corte_date(period):
    y, m = int(period[:4]), int(period[5:7])
    last = calendar.monthrange(y, m)[1]
    return f"{y}-{m:02d}-{last}"


# ──────────────────────────────────────────────
# Cross-filter helpers for mv_cross / mv_resumen
# ──────────────────────────────────────────────

CROSS_FILTER_MAP = {
    "secretaria": "secretaria_origen",
    "programa": "nombre_programa",
    "provincia": "provincia",
    "sexo": "sexo",
    "grupo_etario": "grupo_etario",
    "cant_prestaciones": "cant_prestaciones",
}

GRUPO_ETARIO_MAP = {
    "0-4 años": "0-4 años",
    "5-12 años": "5-12 años",
    "13-17 años": "13-17 años",
    "18-29 años": "18-29 años",
    "30-59 años": "30-59 años",
    "60+ años": "60+ años",
    "Niñez (0-12)": ["0-4 años", "5-12 años"],
    "Jóvenes (13-29)": ["13-17 años", "18-29 años"],
    "Adultos (30-59)": ["30-59 años"],
    "Mayores (60+)": ["60+ años"],
}


def has_filters(filters):
    """Check if there are any active cross-chart filters."""
    return filters and any(filters.get(k) for k in CROSS_FILTER_MAP)


def cross_where(filters, exclude=None):
    """Build WHERE clauses and params from filters."""
    where = []
    params = []
    if not filters:
        return where, params

    for fkey, col in CROSS_FILTER_MAP.items():
        if fkey == exclude:
            continue
        val = filters.get(fkey)
        if not val:
            continue

        if fkey == "grupo_etario":
            mapped = GRUPO_ETARIO_MAP.get(val)
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


def use_resumen(filters, group_col):
    """Decide si usar mv_resumen (dedup) o mv_cross (por programa)."""
    if group_col in ("nombre_programa", "secretaria_origen"):
        return False
    if filters and (filters.get("programa") or filters.get("secretaria")):
        return False
    return True


MV_COL = {
    "personas": "personas",
    "beneficios": "beneficios",
    "montos": "montos",
    "monto_persona": "CASE WHEN personas>0 THEN montos/personas ELSE 0 END",
    "monto_beneficio": "CASE WHEN beneficios>0 THEN montos/beneficios ELSE 0 END",
}

METRIC_COL = {
    "personas": "SUM(personas)",
    "beneficios": "SUM(beneficios)",
    "montos": "SUM(montos)",
    "monto_persona": "CASE WHEN SUM(personas)>0 THEN SUM(montos)/SUM(personas) ELSE 0 END",
    "monto_beneficio": "CASE WHEN SUM(beneficios)>0 THEN SUM(montos)/SUM(beneficios) ELSE 0 END",
}


def cross_query(conn, period, filters, group_col, metric="personas",
                exclude_filter=None, extra_cols="",
                table=None, cross_table="mv_cross", resumen_table="mv_resumen"):
    """Generic query on mv_cross/mv_resumen with filters, grouped by group_col."""
    if table is None:
        table = resumen_table if use_resumen(filters, group_col) else cross_table
    sel = METRIC_COL.get(metric, "SUM(personas)")
    where = ["periodo_mes = %s"]
    params = [period]

    fw, fp = cross_where(filters, exclude=exclude_filter)
    where.extend(fw)
    params.extend(fp)

    where_sql = " AND ".join(where)
    extra = f", {extra_cols}" if extra_cols else ""
    group_extra = f", {extra_cols}" if extra_cols else ""

    rows = query(conn, f"""
        SELECT {group_col}{extra}, {sel} AS total
        FROM {table}
        WHERE {where_sql}
        GROUP BY {group_col}{group_extra}
        ORDER BY total DESC
    """, params)
    return rows


def empty_summary(period):
    return {
        "period": period,
        "cobertura": 0,
        "prestacionesActivas": 0,
        "promedioPrestaciones": 0,
        "promedioMontoPorBenef": 0,
        "montoTotal": 0,
        "tasaNoIdentificados": 0,
        "casosIncompatibilidad": 0,
        "cantidadProgramas": 0,
        "concentracion": {"conUna": 0, "conDos": 0, "conTresMas": 0},
    }


# ──────────────────────────────────────────────
# Auth
# ──────────────────────────────────────────────

def get_user_by_email(conn, email):
    return query_one(conn, "SELECT * FROM users WHERE email=%s", (email,))


# ──────────────────────────────────────────────
# Lookups (periodos, programas, provincias)
# ──────────────────────────────────────────────

def get_periods(conn):
    rows = query(conn, "SELECT periodo_mes FROM periods ORDER BY periodo_mes DESC")
    return [r["periodo_mes"] for r in rows]


def get_programs(conn):
    return query(conn, "SELECT id, nombre_programa FROM programs ORDER BY nombre_programa")


def get_provincias(conn):
    return query(conn, "SELECT provincia FROM provincias_lookup ORDER BY provincia")
