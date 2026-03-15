"""
queries.py — Todas las queries SQL (PostgreSQL)
Único lugar para cambiar lógica de consultas.

Estrategia de performance:
- Sin filtros → lectura directa de vistas materializadas (<10ms)
- Con filtros → query sobre mv_cross (~43K filas, <2s)
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

_GRUPO_RANGES = {
    "Niñez (0-12)": (0, 13),
    "Jóvenes (13-29)": (13, 30),
    "Adultos (30-59)": (30, 60),
    "Mayores (60+)": (60, 999),
}

# Map filter keys to mv_cross columns
_CROSS_FILTER_MAP = {
    "secretaria": "secretaria_origen",
    "programa": "nombre_programa",
    "provincia": "provincia",
    "sexo": "sexo",
    "grupo_etario": "grupo_etario",
}

# Map grupo_etario filter values from the frontend (labels from the chart)
# to groups in mv_cross
_GRUPO_ETARIO_MAP = {
    "0-4 años": "0-4 años",
    "5-12 años": "5-12 años",
    "13-17 años": "13-17 años",
    "18-29 años": "18-29 años",
    "30-59 años": "30-59 años",
    "60+ años": "60+ años",
    # Also handle the old filter keys from _GRUPO_RANGES
    "Niñez (0-12)": ["0-4 años", "5-12 años"],
    "Jóvenes (13-29)": ["13-17 años", "18-29 años"],
    "Adultos (30-59)": ["30-59 años"],
    "Mayores (60+)": ["60+ años"],
}


def _has_filters(filters):
    """Check if there are any active cross-chart filters."""
    return filters and any(filters.get(k) for k in _CROSS_FILTER_MAP)


_mvs_available = None

def _check_mvs(conn):
    """Check once if materialized views are available."""
    global _mvs_available
    if _mvs_available is None:
        row = query_one(conn,
            "SELECT 1 FROM pg_matviews WHERE matviewname = 'mv_summary'")
        _mvs_available = row is not None
    return _mvs_available


def _cross_where(filters, exclude=None):
    """Build WHERE clauses and params for mv_cross from filters.

    Returns (where_parts, params) — caller adds 'AND '.join(where_parts).
    """
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


def _extra_where(fw):
    """Join filter WHERE parts with AND prefix, or empty string if none."""
    return (" AND " + " AND ".join(fw)) if fw else ""


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
# Dashboard indicators
# ──────────────────────────────────────────────

def get_summary(conn, period, filters=None):
    if not _check_mvs(conn):
        return _get_summary_raw(conn, period, filters)

    if not _has_filters(filters):
        # Fast path: read from MVs (<10ms)
        summary = query_one(conn,
            "SELECT * FROM mv_summary WHERE periodo_mes = %s", (period,))
        pagos = query_one(conn,
            "SELECT * FROM mv_pagos_summary WHERE periodo_mes = %s", (period,))
        conc = query_one(conn,
            "SELECT * FROM mv_concentracion WHERE periodo_mes = %s", (period,))
        incomp = query_one(conn,
            "SELECT * FROM mv_incompatibilidades WHERE periodo_mes = %s", (period,))

        if not summary:
            return _empty_summary(period)

        total_benef = summary["total_benef"] or 0
        total_prest = summary["total_prest"] or 0
        cobertura = summary["cobertura"] or 0
        no_ident = summary["no_identificados"] or 0
        total_act = total_prest
        monto_total = float(pagos["total_monto"]) if pagos else 0
        incomp_count = incomp["cant_incompatibles"] if incomp else 0
        cant_programas = summary["cant_programas"] or 0

        con1 = int(conc["con_una"] or 0) if conc else 0
        con2 = int(conc["con_dos"] or 0) if conc else 0
        con3 = int(conc["con_tres_mas"] or 0) if conc else 0

        prom_prest = round(total_prest / total_benef, 2) if total_benef else 0
        prom_monto = round(monto_total / total_benef) if total_benef else 0
        tasa_no_ident = round((no_ident / total_act * 100), 2) if total_act else 0

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

    # Filtered path: query mv_cross
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

    # Concentration: fall back to raw benefits table (mv_cross aggregates can't compute per-beneficiary counts)
    corte = _corte_date(period)
    fj_bp, fw_bp, fp_bp = _apply_filters_raw(filters, corte)
    extra_where_bp = _extra_where(fw_bp)

    conc_row = query_one(conn, f"""
        SELECT
            SUM(CASE WHEN cnt = 1 THEN 1 ELSE 0 END) AS con_una,
            SUM(CASE WHEN cnt = 2 THEN 1 ELSE 0 END) AS con_dos,
            SUM(CASE WHEN cnt >= 3 THEN 1 ELSE 0 END) AS con_tres_mas
        FROM (
            SELECT b.beneficiary_id, COUNT(*) AS cnt
            FROM benefits b {fj_bp}
            WHERE b.periodo_mes=%s AND b.estado_beneficio='ACTIVO' AND b.beneficiary_id IS NOT NULL
            {extra_where_bp}
            GROUP BY b.beneficiary_id
        ) sub
    """, [period] + fp_bp)

    con1 = int(conc_row["con_una"] or 0) if conc_row else 0
    con2 = int(conc_row["con_dos"] or 0) if conc_row else 0
    con3 = int(conc_row["con_tres_mas"] or 0) if conc_row else 0

    # Cobertura from mv_cross doesn't have cuil info — use summary MV
    summary = query_one(conn,
        "SELECT cobertura, no_identificados, total_prest FROM mv_summary WHERE periodo_mes = %s",
        (period,))
    cobertura = summary["cobertura"] if summary else 0
    tasa_no_ident = round(
        (summary["no_identificados"] / summary["total_prest"] * 100), 2
    ) if summary and summary["total_prest"] else 0

    # Incompatibilidades — use MV (not affected by demographic filters meaningfully)
    incomp = query_one(conn,
        "SELECT cant_incompatibles FROM mv_incompatibilidades WHERE periodo_mes = %s",
        (period,))
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
    if not _check_mvs(conn):
        return _get_by_secretaria_raw(conn, period, filters, metric)

    if not _has_filters(filters):
        col = _MV_COL.get(metric, "personas")
        return query(conn, f"""
            SELECT secretaria, {col} AS total
            FROM mv_by_secretaria
            WHERE periodo_mes = %s
            ORDER BY total DESC
        """, (period,))

    rows = _cross_query(conn, period, filters, "secretaria_origen",
                        metric=metric, exclude_filter="secretaria")
    return [{"secretaria": r["secretaria_origen"], "total": r["total"]} for r in rows]


def get_by_provincia(conn, period, filters=None, metric="personas"):
    if not _check_mvs(conn):
        return _get_by_provincia_raw(conn, period, filters, metric)

    if not _has_filters(filters):
        col = _MV_COL.get(metric, "personas")
        return query(conn, f"""
            SELECT provincia, {col} AS total
            FROM mv_by_provincia
            WHERE periodo_mes = %s
            ORDER BY total DESC
        """, (period,))

    rows = _cross_query(conn, period, filters, "provincia",
                        metric=metric, exclude_filter="provincia")
    return rows


def get_by_departamento(conn, period, filters=None):
    # Departamento is not in mv_cross — use raw query but with indexes
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
    if not _check_mvs(conn):
        return _get_by_programa_raw(conn, period, filters, metric)

    if not _has_filters(filters):
        col = _MV_COL.get(metric, "personas")
        return query(conn, f"""
            SELECT programa, {col} AS total
            FROM mv_by_programa
            WHERE periodo_mes = %s
            ORDER BY total DESC
        """, (period,))

    rows = _cross_query(conn, period, filters, "nombre_programa",
                        metric=metric, exclude_filter="programa")
    return [{"programa": r["nombre_programa"], "total": r["total"]} for r in rows]


def get_by_sexo(conn, period, filters=None, metric="personas"):
    if not _check_mvs(conn):
        return _get_by_sexo_raw(conn, period, filters, metric)

    if not _has_filters(filters):
        col = _MV_COL.get(metric, "personas")
        return query(conn, f"""
            SELECT sexo, label, {col} AS total
            FROM mv_by_sexo
            WHERE periodo_mes = %s
            ORDER BY total DESC
        """, (period,))

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
    if not _check_mvs(conn):
        return _get_by_grupo_etario_raw(conn, period, filters, metric)

    if not _has_filters(filters):
        col = _MV_COL.get(metric, "personas")
        return query(conn, f"""
            SELECT grupo, {col} AS total
            FROM mv_by_grupo_etario
            WHERE periodo_mes = %s
            ORDER BY
                CASE grupo
                    WHEN '0-4 años' THEN 1
                    WHEN '5-12 años' THEN 2
                    WHEN '13-17 años' THEN 3
                    WHEN '18-29 años' THEN 4
                    WHEN '30-59 años' THEN 5
                    WHEN '60+ años' THEN 6
                END
        """, (period,))

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
    if not _check_mvs(conn):
        return _get_evolucion_raw(conn, filters, metric)

    if not _has_filters(filters):
        col = _MV_COL.get(metric, "personas")
        return query(conn, f"""
            SELECT periodo_mes, {col} AS total
            FROM mv_evolucion
            ORDER BY periodo_mes ASC
        """)

    # Filtered: aggregate mv_cross across periods
    sel = _METRIC_COL.get(metric, "SUM(personas)")
    where = []
    params = []
    # Exclude grupo_etario from evolution filters (no corte per period)
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
# Raw filter helper (for concentration + departamento fallback)
# ──────────────────────────────────────────────

def _apply_filters_raw(filters, corte=None, has_ben=False, has_prog=False):
    """Build extra JOINs, WHERE clauses and params from cross-chart filters.
    For use with raw benefits table queries.
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
        # Map grupo_etario to age ranges
        val = filters["grupo_etario"]
        mapped = _GRUPO_ETARIO_MAP.get(val)
        if mapped:
            # Use AGE-based filter on raw table
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
# Raw fallback queries (when MVs don't exist)
# ──────────────────────────────────────────────

def _metric_expr(metric):
    pay_join = "LEFT JOIN payments pay ON pay.beneficiary_id=b.beneficiary_id AND pay.program_id=b.program_id AND pay.periodo_mes=b.periodo_mes"
    if metric == "montos":
        return (f"COALESCE(SUM(pay.monto_prestacion),0)", pay_join)
    if metric == "beneficios":
        return ("COUNT(*)", "")
    if metric == "monto_persona":
        return (f"CASE WHEN COUNT(DISTINCT b.beneficiary_id)>0 THEN COALESCE(SUM(pay.monto_prestacion),0)/COUNT(DISTINCT b.beneficiary_id) ELSE 0 END", pay_join)
    if metric == "monto_beneficio":
        return (f"CASE WHEN COUNT(*)>0 THEN COALESCE(SUM(pay.monto_prestacion),0)/COUNT(*) ELSE 0 END", pay_join)
    # default: personas
    return ("COUNT(DISTINCT b.beneficiary_id)", "")


def _get_summary_raw(conn, period, filters=None):
    corte = _corte_date(period)
    fj_bp, fw_bp, fp_bp = _apply_filters_raw(filters, corte)
    extra_where_bp = _extra_where(fw_bp)

    row = query_one(conn, f"""
        SELECT COUNT(DISTINCT b.cuil_raw) as total
        FROM benefits b {fj_bp}
        WHERE b.periodo_mes=%s AND b.estado_beneficio='ACTIVO'
          AND b.cuil_raw IS NOT NULL AND LENGTH(b.cuil_raw)=11 {extra_where_bp}
    """, [period] + fp_bp)
    cobertura = row["total"] if row else 0

    row2 = query_one(conn, f"""
        SELECT COUNT(DISTINCT b.beneficiary_id) as total
        FROM benefits b {fj_bp}
        WHERE b.periodo_mes=%s AND b.estado_beneficio='ACTIVO' AND b.beneficiary_id IS NOT NULL
        {extra_where_bp}
    """, [period] + fp_bp)
    total_benef = row2["total"] if row2 else 0

    row3 = query_one(conn, f"""
        SELECT COUNT(*) as total
        FROM benefits b {fj_bp}
        WHERE b.periodo_mes=%s AND b.estado_beneficio='ACTIVO' AND b.beneficiary_id IS NOT NULL
        {extra_where_bp}
    """, [period] + fp_bp)
    total_prest = row3["total"] if row3 else 0
    prom_prestaciones = round(total_prest / total_benef, 2) if total_benef else 0

    if filters:
        pay_join = f"JOIN benefits b ON pay.beneficiary_id=b.beneficiary_id AND pay.program_id=b.program_id AND pay.periodo_mes=b.periodo_mes {fj_bp}"
        row4 = query_one(conn, f"""
            SELECT COALESCE(SUM(pay.monto_prestacion),0) as monto_total
            FROM payments pay {pay_join}
            WHERE pay.periodo_mes=%s AND b.estado_beneficio='ACTIVO' {extra_where_bp}
        """, [period] + fp_bp)
    else:
        row4 = query_one(conn, "SELECT COALESCE(SUM(monto_prestacion),0) as monto_total FROM payments WHERE periodo_mes=%s", (period,))
    monto_total = row4["monto_total"] if row4 else 0
    prom_monto = round(float(monto_total) / total_benef) if total_benef else 0

    row5 = query_one(conn, "SELECT COUNT(*) as t FROM benefits WHERE periodo_mes=%s AND estado_beneficio='ACTIVO'", (period,))
    row6 = query_one(conn, "SELECT COUNT(*) as t FROM benefits WHERE periodo_mes=%s AND estado_beneficio='ACTIVO' AND (beneficiary_id IS NULL OR cuil_raw IS NULL)", (period,))
    total_act = row5["t"] if row5 else 0
    no_ident = row6["t"] if row6 else 0
    tasa_no_ident = round((no_ident / total_act * 100), 2) if total_act else 0

    incomp_row = query_one(conn, """
        SELECT COUNT(DISTINCT b1.beneficiary_id) as total
        FROM incompatibility_rules ir
        JOIN benefits b1 ON b1.program_id = ir.program_a_id AND b1.periodo_mes = %s AND b1.estado_beneficio = 'ACTIVO' AND b1.beneficiary_id IS NOT NULL
        JOIN benefits b2 ON b2.program_id = ir.program_b_id AND b2.beneficiary_id = b1.beneficiary_id AND b2.periodo_mes = %s AND b2.estado_beneficio = 'ACTIVO'
        WHERE ir.is_compatible = 0
    """, (period, period))
    incomp = incomp_row["total"] if incomp_row else 0

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
    con1 = int(conc_row["con_una"] or 0) if conc_row else 0
    con2 = int(conc_row["con_dos"] or 0) if conc_row else 0
    con3 = int(conc_row["con_tres_mas"] or 0) if conc_row else 0

    prog_row = query_one(conn, f"""
        SELECT COUNT(DISTINCT b.program_id) as total
        FROM benefits b {fj_bp}
        WHERE b.periodo_mes=%s AND b.estado_beneficio='ACTIVO' AND b.beneficiary_id IS NOT NULL
        {extra_where_bp}
    """, [period] + fp_bp)
    cant_programas = prog_row["total"] if prog_row else 0

    return {
        "period": period,
        "cobertura": cobertura,
        "promedioPrestaciones": prom_prestaciones,
        "promedioMontoPorBenef": prom_monto,
        "montoTotal": round(float(monto_total)),
        "tasaNoIdentificados": tasa_no_ident,
        "casosIncompatibilidad": incomp,
        "cantidadProgramas": cant_programas,
        "concentracion": {"conUna": con1, "conDos": con2, "conTresMas": con3},
    }


def _get_by_dimension_raw(conn, period, filters, metric, *,
                          select_expr, group_expr, base_join,
                          has_ben=False, has_prog=False,
                          extra_condition=""):
    """Generic raw fallback for all get_by_* dimension queries."""
    corte = _corte_date(period)
    sel, pay_join = _metric_expr(metric)
    fj, fw, fp = _apply_filters_raw(filters, corte, has_ben=has_ben, has_prog=has_prog)
    extra = _extra_where(fw)
    return query(conn, f"""
        SELECT {select_expr}, {sel} as total
        FROM benefits b {base_join} {pay_join} {fj}
        WHERE b.periodo_mes=%s AND b.estado_beneficio='ACTIVO' {extra_condition}
        {extra}
        GROUP BY {group_expr} ORDER BY total DESC
    """, [period] + fp)


def _get_by_secretaria_raw(conn, period, filters=None, metric="personas"):
    return _get_by_dimension_raw(conn, period, filters, metric,
        select_expr="p.secretaria_origen as secretaria",
        group_expr="p.secretaria_origen",
        base_join="JOIN programs p ON b.program_id=p.id",
        has_prog=True, extra_condition="AND b.cuil_raw IS NOT NULL")


def _get_by_provincia_raw(conn, period, filters=None, metric="personas"):
    return _get_by_dimension_raw(conn, period, filters, metric,
        select_expr="ben.provincia",
        group_expr="ben.provincia",
        base_join="JOIN beneficiaries ben ON b.beneficiary_id=ben.id",
        has_ben=True)


def _get_by_programa_raw(conn, period, filters=None, metric="personas"):
    return _get_by_dimension_raw(conn, period, filters, metric,
        select_expr="p.nombre_programa as programa",
        group_expr="p.nombre_programa",
        base_join="JOIN programs p ON b.program_id=p.id",
        has_prog=True, extra_condition="AND b.beneficiary_id IS NOT NULL")


def _get_by_sexo_raw(conn, period, filters=None, metric="personas"):
    return _get_by_dimension_raw(conn, period, filters, metric,
        select_expr="ben.sexo, CASE ben.sexo WHEN 'M' THEN 'Masculino' WHEN 'F' THEN 'Femenino' WHEN 'X' THEN 'No binario' ELSE 'No informado' END as label",
        group_expr="ben.sexo",
        base_join="JOIN beneficiaries ben ON b.beneficiary_id=ben.id",
        has_ben=True)


def _get_by_grupo_etario_raw(conn, period, filters=None, metric="personas"):
    corte = _corte_date(period)
    fj, fw, fp = _apply_filters_raw(filters, corte, has_ben=True, has_prog=False)
    extra = _extra_where(fw)
    # Compute age in a CTE, then aggregate
    return query(conn, f"""
        WITH aged AS (
            SELECT b.beneficiary_id,
                   EXTRACT(YEAR FROM AGE(%s::date, ben.fecha_nacimiento))::int AS age
            FROM benefits b
            JOIN beneficiaries ben ON b.beneficiary_id=ben.id {fj}
            WHERE b.periodo_mes=%s AND b.estado_beneficio='ACTIVO'
            {extra}
        )
        SELECT
          CASE
            WHEN age <= 4 THEN '0-4 años'
            WHEN age <= 12 THEN '5-12 años'
            WHEN age <= 17 THEN '13-17 años'
            WHEN age <= 29 THEN '18-29 años'
            WHEN age <= 59 THEN '30-59 años'
            ELSE '60+ años'
          END AS grupo,
          COUNT(DISTINCT beneficiary_id) AS total
        FROM aged
        GROUP BY 1
        ORDER BY MIN(age)
    """, [corte, period] + fp)


def _get_evolucion_raw(conn, filters=None, metric="montos"):
    f = {k: v for k, v in filters.items() if k != "grupo_etario"} if filters else {}
    fj, fw, fp = _apply_filters_raw(f, None)
    extra = _extra_where(fw)
    sel, pay_join = _metric_expr(metric)

    if not pay_join:
        # personas or beneficios — no payments join needed
        if not f:
            return query(conn, f"""
                SELECT periodo_mes, {sel} as total
                FROM benefits b
                WHERE estado_beneficio='ACTIVO' AND beneficiary_id IS NOT NULL
                GROUP BY periodo_mes ORDER BY periodo_mes ASC
            """)
        return query(conn, f"""
            SELECT b.periodo_mes, {sel} as total
            FROM benefits b {fj}
            WHERE b.estado_beneficio='ACTIVO' AND b.beneficiary_id IS NOT NULL {extra}
            GROUP BY b.periodo_mes ORDER BY b.periodo_mes ASC
        """, fp)

    # montos, monto_persona, monto_beneficio — need payments join
    if not f:
        return query(conn, f"""
            SELECT b.periodo_mes, {sel} as total
            FROM benefits b {pay_join}
            WHERE b.estado_beneficio='ACTIVO'
            GROUP BY b.periodo_mes ORDER BY b.periodo_mes ASC
        """)
    return query(conn, f"""
        SELECT b.periodo_mes, {sel} as total
        FROM benefits b {pay_join} {fj}
        WHERE b.estado_beneficio='ACTIVO' {extra}
        GROUP BY b.periodo_mes ORDER BY b.periodo_mes ASC
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
    # Age computed in Python as fallback (single row, negligible cost)
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
