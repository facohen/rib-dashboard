"""
endpoints_tc.py — Flask Blueprint para endpoints de Titulares de Cobro (TC).

Rutas: /api/tc/indicators/*, /api/tc/nominal/titulares/*
Para TD ver endpoints_td.py.
"""
import hashlib

from flask import Blueprint, request, jsonify, g
from config import get_connection
from auth import login_required, admin_required
import queries_tc

tc_bp = Blueprint("tc", __name__, url_prefix="/api/tc")

_cache = None


def init_tc(cache_instance):
    global _cache
    _cache = cache_instance


# ── Helpers ──

def _get_db():
    if "db" not in g:
        g.db = get_connection()
    return g.db


_FILTER_KEYS = ("secretaria", "sexo", "programa", "provincia",
                "grupo_etario", "cant_prestaciones")


def _filters():
    f = {k: v for k in _FILTER_KEYS if (v := request.args.get(k, "").strip())}
    return f or None


def _cache_key():
    return "tc:" + hashlib.sha256(request.full_path.encode()).hexdigest()[:16]


# ── Indicator routes (declarative table) ──

_INDICATOR_ROUTES = [
    ("summary",         queries_tc.get_summary_tc,          False),
    ("by-secretaria",   queries_tc.get_by_secretaria_tc,    True),
    ("by-provincia",    queries_tc.get_by_provincia_tc,     True),
    ("by-programa",     queries_tc.get_by_programa_tc,      True),
    ("by-sexo",         queries_tc.get_by_sexo_tc,          True),
    ("by-grupo-etario", queries_tc.get_by_grupo_etario_tc,  True),
    ("evolucion",       queries_tc.get_evolucion_tc,        True),
]


def _make_indicator_view(query_fn, has_metric, is_evolucion=False):
    @login_required
    def view():
        if is_evolucion:
            args = [_get_db(), _filters()]
        else:
            args = [_get_db(), request.args.get("period", "2026-03"), _filters()]
        if has_metric:
            args.append(request.args.get("metric", "personas"))
        result = query_fn(*args)
        response = jsonify(result)
        if _cache:
            _cache.set(_cache_key(), response)
        return response
    return view


for _name, _fn, _metric in _INDICATOR_ROUTES:
    _is_evo = _name == "evolucion"
    _view = _make_indicator_view(_fn, _metric, _is_evo)
    _view.__name__ = f"tc_api_{_name.replace('-', '_')}"
    tc_bp.add_url_rule(f"/indicators/{_name}", view_func=_view)


@tc_bp.route("/indicators/provincia-detail")
@login_required
def tc_api_provincia_detail():
    provincia = request.args.get("provincia", "").strip()
    if not provincia:
        return jsonify(error="provincia required"), 400
    period = request.args.get("period", "2026-03")
    return jsonify(queries_tc.get_provincia_detail_tc(_get_db(), period, provincia, _filters()))


# ── Nominal TC (admin only) ──

@tc_bp.route("/nominal/titulares")
@admin_required
def tc_api_nominal_list():
    period = request.args.get("period", "2026-03")
    filters = {k: request.args.get(k, "").strip() if k != "estado" else request.args.get(k, "ACTIVO")
               for k in ("cuil", "provincia", "programa", "sexo", "grupo_etario", "estado")}
    page = int(request.args.get("page", 1))
    page_size = int(request.args.get("pageSize", 20))
    return jsonify(queries_tc.get_nominal_tc_list(_get_db(), period, filters, page, page_size))


@tc_bp.route("/nominal/titulares/<cuil>")
@admin_required
def tc_api_nominal_detail(cuil):
    period = request.args.get("period", "2026-03")
    detail = queries_tc.get_nominal_tc_detail(_get_db(), cuil, period)
    if not detail:
        return jsonify(error="No encontrado"), 404
    return jsonify(detail)
