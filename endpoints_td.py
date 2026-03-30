"""
endpoints_td.py — Flask Blueprint para endpoints de Titulares de Derecho (TD).

Rutas: /api/indicators/*, /api/nominal/*, /api/admin/clear-cache
Para TC ver endpoints_tc.py.
"""
import hashlib

from flask import Blueprint, request, jsonify, g
from config import get_connection
from auth import login_required, admin_required
import queries_td

td_bp = Blueprint("td", __name__, url_prefix="/api")

_cache = None


def init_td(cache_instance):
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
    return "rib:" + hashlib.sha256(request.full_path.encode()).hexdigest()[:16]


# ── Indicator routes (declarative table) ──

_INDICATOR_ROUTES = [
    ("summary",         queries_td.get_summary,          False),
    ("by-secretaria",   queries_td.get_by_secretaria,    True),
    ("by-provincia",    queries_td.get_by_provincia,     True),
    ("by-programa",     queries_td.get_by_programa,      True),
    ("by-sexo",         queries_td.get_by_sexo,          True),
    ("by-grupo-etario", queries_td.get_by_grupo_etario,  True),
    ("evolucion",       queries_td.get_evolucion,        True),
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
    _view.__name__ = f"api_{_name.replace('-', '_')}"
    td_bp.add_url_rule(f"/indicators/{_name}", view_func=_view)


@td_bp.route("/indicators/provincia-detail")
@login_required
def api_provincia_detail():
    provincia = request.args.get("provincia", "").strip()
    if not provincia:
        return jsonify(error="provincia required"), 400
    period = request.args.get("period", "2026-03")
    return jsonify(queries_td.get_provincia_detail(_get_db(), period, provincia, _filters()))


@td_bp.route("/admin/clear-cache", methods=["POST"])
@admin_required
def clear_cache():
    if _cache:
        _cache.clear()
    return jsonify({"ok": True})


# ── Nominal (admin only) ──

@td_bp.route("/nominal/beneficiaries")
@admin_required
def api_nominal_list():
    period = request.args.get("period", "2026-03")
    filters = {k: request.args.get(k, "").strip() if k != "estado" else request.args.get(k, "ACTIVO")
               for k in ("cuil", "provincia", "programa", "sexo", "grupo_etario", "cant_prestaciones", "estado")}
    page = int(request.args.get("page", 1))
    page_size = int(request.args.get("pageSize", 20))
    return jsonify(queries_td.get_nominal_list(_get_db(), period, filters, page, page_size))


@td_bp.route("/nominal/beneficiaries/<bid>")
@admin_required
def api_nominal_detail(bid):
    try:
        bid = int(bid)
    except (ValueError, TypeError):
        return jsonify(error="ID inválido"), 400
    period = request.args.get("period", "2026-03")
    detail = queries_td.get_nominal_detail(_get_db(), bid, period)
    if not detail:
        return jsonify(error="No encontrado"), 404
    return jsonify(detail)
