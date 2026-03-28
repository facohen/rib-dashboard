"""
queries_tc.py — Queries para Titulares de Cobro (TC).

Consultas contra mv_cross_tc, mv_resumen_tc, mv_nominal_tc.
Para TD ver queries_td.py.
"""
from config import query, query_one
from queries_helpers import (
    corte_date as _corte_date,
    METRIC_COL as _METRIC_COL,
    cross_where as _cross_where,
    use_resumen as _use_resumen,
    cross_query as _cross_query,
    empty_summary as _empty_summary,
)

_CROSS = "mv_cross_tc"
_RESUMEN = "mv_resumen_tc"
_NOMINAL = "mv_nominal_tc"


# ──────────────────────────────────────────────
# Dashboard indicators — all from mv_cross_tc + mv_resumen_tc
# ──────────────────────────────────────────────

def get_summary_tc(conn, period, filters=None):
    """/* queries_tc.get_summary_tc */"""
    has_prog_filter = filters and (filters.get("programa") or filters.get("secretaria"))
    agg_table = _CROSS if has_prog_filter else _RESUMEN

    where = ["periodo_mes = %s"]
    params = [period]
    fw, fp = _cross_where(filters)
    where.extend(fw)
    params.extend(fp)
    where_sql = " AND ".join(where)

    row = query_one(conn, f"""
        /* queries_tc.get_summary_tc — main */
        SELECT SUM(personas) AS total_benef,
               SUM(beneficios) AS total_prest,
               SUM(montos) AS total_monto
        FROM {agg_table} WHERE {where_sql}
    """, params)

    total_benef = int(row["total_benef"] or 0) if row else 0
    total_prest = int(row["total_prest"] or 0) if row else 0
    monto_total = float(row["total_monto"] or 0) if row else 0

    prog_row = query_one(conn, f"""
        /* queries_tc.get_summary_tc — programas */
        SELECT COUNT(DISTINCT nombre_programa) AS cant_programas,
               SUM(personas) AS prestaciones_activas
        FROM {_CROSS} WHERE {where_sql}
    """, params)
    cant_programas = int(prog_row["cant_programas"] or 0) if prog_row else 0
    prestaciones_activas = int(prog_row["prestaciones_activas"] or 0) if prog_row else 0

    if total_benef == 0:
        return _empty_summary(period)

    conc_rows = query(conn, f"""
        /* queries_tc.get_summary_tc — concentracion */
        SELECT cant_prestaciones, SUM(personas) AS total
        FROM {agg_table} WHERE {where_sql}
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

    prom_prest = round(total_prest / total_benef, 2) if total_benef else 0
    prom_monto = round(monto_total / total_benef) if total_benef else 0

    return {
        "period": period,
        "cobertura": total_benef,
        "prestacionesActivas": prestaciones_activas,
        "promedioPrestaciones": prom_prest,
        "promedioMontoPorBenef": prom_monto,
        "montoTotal": round(monto_total),
        "tasaNoIdentificados": 0,
        "casosIncompatibilidad": 0,
        "cantidadProgramas": cant_programas,
        "concentracion": {"conUna": con1, "conDos": con2, "conTresMas": con3},
    }


def get_by_secretaria_tc(conn, period, filters=None, metric="personas"):
    rows = _cross_query(conn, period, filters, "secretaria_origen",
                        metric=metric, exclude_filter="secretaria",
                        cross_table=_CROSS, resumen_table=_RESUMEN)
    return [{"secretaria": r["secretaria_origen"], "total": r["total"]} for r in rows]


def get_by_provincia_tc(conn, period, filters=None, metric="personas"):
    return _cross_query(conn, period, filters, "provincia",
                        metric=metric, exclude_filter="provincia",
                        cross_table=_CROSS, resumen_table=_RESUMEN)


def get_by_programa_tc(conn, period, filters=None, metric="personas"):
    rows = _cross_query(conn, period, filters, "nombre_programa",
                        metric=metric, exclude_filter="programa",
                        cross_table=_CROSS, resumen_table=_RESUMEN)
    return [{"programa": r["nombre_programa"], "total": r["total"]} for r in rows]


def get_by_sexo_tc(conn, period, filters=None, metric="personas"):
    sel = _METRIC_COL.get(metric, "SUM(personas)")
    where = ["periodo_mes = %s"]
    params = [period]
    fw, fp = _cross_where(filters, exclude="sexo")
    where.extend(fw)
    params.extend(fp)
    where_sql = " AND ".join(where)

    table = _RESUMEN if _use_resumen(filters, "sexo") else _CROSS

    return query(conn, f"""
        /* queries_tc.get_by_sexo_tc */
        SELECT sexo,
               CASE sexo WHEN 'M' THEN 'Masculino' WHEN 'F' THEN 'Femenino'
                         WHEN 'X' THEN 'No binario' ELSE 'No informado' END AS label,
               {sel} AS total
        FROM {table}
        WHERE {where_sql}
        GROUP BY sexo
        ORDER BY total DESC
    """, params)


def get_by_grupo_etario_tc(conn, period, filters=None, metric="personas"):
    sel = _METRIC_COL.get(metric, "SUM(personas)")
    where = ["periodo_mes = %s"]
    params = [period]
    fw, fp = _cross_where(filters, exclude="grupo_etario")
    where.extend(fw)
    params.extend(fp)
    where_sql = " AND ".join(where)

    table = _RESUMEN if _use_resumen(filters, "grupo_etario") else _CROSS

    return query(conn, f"""
        /* queries_tc.get_by_grupo_etario_tc */
        SELECT grupo_etario AS grupo, {sel} AS total
        FROM {table}
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


def get_evolucion_tc(conn, filters=None, metric="montos"):
    sel = _METRIC_COL.get(metric, "SUM(personas)")
    where = []
    params = []
    f = {k: v for k, v in filters.items() if k != "grupo_etario"} if filters else {}

    table = _RESUMEN if _use_resumen(f, "periodo_mes") else _CROSS

    fw, fp = _cross_where(f)
    where.extend(fw)
    params.extend(fp)
    where_sql = (" AND " + " AND ".join(where)) if where else ""

    return query(conn, f"""
        /* queries_tc.get_evolucion_tc */
        SELECT periodo_mes, {sel} AS total
        FROM {table}
        WHERE 1=1 {where_sql}
        GROUP BY periodo_mes
        ORDER BY periodo_mes ASC
    """, params)


# ──────────────────────────────────────────────
# Provincia detail (rich map tooltip) — TC perspective
# ──────────────────────────────────────────────

def get_provincia_detail_tc(conn, period, provincia, filters=None):
    where_base = ["periodo_mes = %s", "provincia = %s"]
    params_base = [period, provincia]
    fw, fp = _cross_where(filters)

    has_prog_filter = filters and (filters.get("programa") or filters.get("secretaria"))
    resumen_table = _CROSS if has_prog_filter else _RESUMEN
    where_r = where_base + fw
    where_r_sql = " AND ".join(where_r)
    params_r = params_base + fp

    totals = query_one(conn, f"""
        /* queries_tc.get_provincia_detail_tc — totals */
        SELECT SUM(personas) AS personas,
               SUM(beneficios) AS beneficios,
               SUM(montos) AS montos
        FROM {resumen_table} WHERE {where_r_sql}
    """, params_r)

    personas = int(totals["personas"] or 0) if totals else 0
    beneficios = int(totals["beneficios"] or 0) if totals else 0
    montos = float(totals["montos"] or 0) if totals else 0

    where_c = where_base + fw
    where_c_sql = " AND ".join(where_c)
    params_c = params_base + fp

    cross_totals = query_one(conn, f"""
        /* queries_tc.get_provincia_detail_tc — prestaciones */
        SELECT SUM(personas) AS prestaciones
        FROM {_CROSS} WHERE {where_c_sql}
    """, params_c)
    prestaciones = int(cross_totals["prestaciones"] or 0) if cross_totals else 0
    prom_prest = round(prestaciones / personas, 2) if personas else 0

    progs = query(conn, f"""
        /* queries_tc.get_provincia_detail_tc — programas */
        SELECT nombre_programa,
               SUM(personas) AS personas,
               SUM(beneficios) AS beneficios,
               SUM(montos) AS montos
        FROM {_CROSS} WHERE {where_c_sql}
        GROUP BY nombre_programa
        ORDER BY SUM(personas) DESC
        LIMIT 5
    """, params_c)

    programas = []
    for p in progs:
        pp = int(p["personas"] or 0)
        pb = int(p["beneficios"] or 0)
        programas.append({
            "nombre": p["nombre_programa"],
            "personas": pp,
            "beneficios": pb,
            "montos": float(p["montos"] or 0),
        })

    return {
        "provincia": provincia,
        "personas": personas,
        "prestaciones": prestaciones,
        "beneficios": beneficios,
        "montos": round(montos),
        "promPrestaciones": prom_prest,
        "programas": programas,
    }


# ──────────────────────────────────────────────
# Nominal TC
# ──────────────────────────────────────────────

def get_nominal_tc_list(conn, period, filters, page, page_size):
    """Lista paginada de Titulares de Cobro desde mv_nominal_tc."""
    offset = (page - 1) * page_size

    cuil = filters.get("cuil", "")
    provincia = filters.get("provincia", "")
    sexo = filters.get("sexo", "")
    programa_id = filters.get("programa", "")
    estado = filters.get("estado", "ACTIVO")
    grupo_etario = filters.get("grupo_etario", "")

    where = ["n.periodo_mes=%s"]
    params = [period]
    if provincia:
        where.append("n.provincia_titular=%s"); params.append(provincia)
    if sexo:
        where.append("n.sexo_titular=%s"); params.append(sexo)
    if cuil:
        where.append("n.cuil_titular LIKE %s"); params.append(f"{cuil}%")
    if grupo_etario:
        if grupo_etario == "60+":
            where.append("n.edad_titular >= 60")
        else:
            parts = grupo_etario.split("-")
            where.append("n.edad_titular >= %s AND n.edad_titular <= %s")
            params.extend([int(parts[0]), int(parts[1])])

    if programa_id and estado == "ACTIVO":
        where.append("n.active_program_ids @> ARRAY[%s]::int[]"); params.append(int(programa_id))
    elif programa_id and estado == "INACTIVO":
        where.append("n.program_ids @> ARRAY[%s]::int[]"); params.append(int(programa_id))
        where.append("NOT (n.active_program_ids @> ARRAY[%s]::int[])"); params.append(int(programa_id))
    elif programa_id:
        where.append("n.program_ids @> ARRAY[%s]::int[]"); params.append(int(programa_id))
    elif estado == "ACTIVO":
        where.append("array_length(n.active_program_ids, 1) > 0")
    elif estado == "INACTIVO":
        where.append("array_length(n.program_ids, 1) > array_length(COALESCE(n.active_program_ids, '{}'), 1)")

    where_sql = " AND ".join(where)

    has_user_filters = any([provincia, sexo, cuil, grupo_etario, programa_id])
    if has_user_filters:
        count_row = query_one(conn, f"""
            /* queries_tc.get_nominal_tc_list — count */
            SELECT COUNT(*) AS total FROM {_NOMINAL} n WHERE {where_sql}
        """, params)
        total = int(count_row["total"]) if count_row else 0
    else:
        count_row = query_one(conn, f"""
            SELECT SUM(personas) AS total FROM {_RESUMEN} WHERE periodo_mes = %s
        """, [period])
        total = int(count_row["total"]) if count_row else 0

    rows = query(conn, f"""
        /* queries_tc.get_nominal_tc_list — data */
        SELECT n.cuil_titular, n.nombre_titular, n.apellido_titular,
               n.sexo_titular, n.edad_titular,
               n.provincia_titular, n.departamento_titular,
               n.cant_td, n.cant_beneficios, n.monto_total
        FROM {_NOMINAL} n
        WHERE {where_sql}
        ORDER BY n.apellido_titular, n.nombre_titular
        LIMIT %s OFFSET %s
    """, params + [page_size, offset])

    items = [{
        "cuil": r["cuil_titular"],
        "nombre": r["nombre_titular"], "apellido": r["apellido_titular"],
        "sexo": r["sexo_titular"], "edad": r["edad_titular"],
        "provincia": r["provincia_titular"],
        "departamento": r["departamento_titular"],
        "cantTD": r["cant_td"],
        "cantBeneficios": r["cant_beneficios"],
        "montoTotal": float(r["monto_total"]) if r["monto_total"] else 0,
    } for r in rows]
    return {"items": items, "total": total, "page": page, "pageSize": page_size}


def get_nominal_tc_detail(conn, cuil_titular, period):
    """Detalle de un TC: datos propios + lista de TDs a cargo."""
    tc = query_one(conn, f"""
        /* queries_tc.get_nominal_tc_detail — tc */
        SELECT * FROM {_NOMINAL}
        WHERE cuil_titular=%s AND periodo_mes=%s
    """, (cuil_titular, period))
    if not tc:
        return None

    # Expand cuils_td array → fetch each TD's info + their benefits
    cuils_td = tc["cuils_td"] or []
    titulares_derecho = []
    for cuil_td in cuils_td:
        ben = query_one(conn, """
            SELECT id, cuil, nombre, apellido, sexo, fecha_nacimiento,
                   provincia, departamento
            FROM beneficiaries WHERE cuil=%s
        """, (cuil_td,))
        if not ben:
            continue

        corte = _corte_date(period)
        fn = ben["fecha_nacimiento"]
        try:
            from datetime import date
            edad = int((date.fromisoformat(corte) - fn).days / 365.25)
        except Exception:
            edad = None

        prestaciones = query(conn, """
            SELECT p.nombre_programa, b.estado_beneficio
            FROM benefits b
            JOIN programs p ON b.program_id=p.id
            WHERE b.beneficiary_id=%s AND b.periodo_mes=%s
            ORDER BY p.nombre_programa
        """, (ben["id"], period))

        titulares_derecho.append({
            "cuil": ben["cuil"],
            "nombre": ben["nombre"], "apellido": ben["apellido"],
            "sexo": ben["sexo"], "edad": edad,
            "prestaciones": [dict(r) for r in prestaciones],
        })

    return {
        "cuil": tc["cuil_titular"],
        "nombre": tc["nombre_titular"], "apellido": tc["apellido_titular"],
        "sexo": tc["sexo_titular"], "edad": tc["edad_titular"],
        "provincia": tc["provincia_titular"],
        "departamento": tc["departamento_titular"],
        "cantTD": tc["cant_td"],
        "cantBeneficios": tc["cant_beneficios"],
        "montoTotal": float(tc["monto_total"]) if tc["monto_total"] else 0,
        "titulares_derecho": titulares_derecho,
    }
