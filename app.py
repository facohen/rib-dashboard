"""
RUB Dashboard – Flask + PostgreSQL
Correr con: DATABASE_URL=postgresql://... python app.py
Produccion: gunicorn -w 4 app:app --bind 0.0.0.0:5000
"""
import hashlib
import os
from decimal import Decimal

from flask import (Flask, render_template, request, redirect, url_for,
                   session, jsonify, g)
from flask.json.provider import DefaultJSONProvider
from flask_caching import Cache
from flask_compress import Compress
from flask_wtf.csrf import CSRFProtect

from config import get_connection, put_connection
from auth import auth_bp, login_required, admin_required
from chatbot import chatbot_bp, check_ollama
import queries


class CustomJSONProvider(DefaultJSONProvider):
    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        return super().default(o)


app = Flask(__name__)
app.json_provider_class = CustomJSONProvider
app.json = CustomJSONProvider(app)
Compress(app)

# ── Secret key ──
_secret = os.environ.get("FLASK_SECRET_KEY")
if not _secret:
    _secret = os.urandom(32).hex()
    print("[WARN] FLASK_SECRET_KEY no seteada — key efimera")
app.secret_key = _secret

# ── Session ──
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = 3600

# ── CSRF + Blueprints ──
csrf = CSRFProtect(app)
app.register_blueprint(auth_bp)
app.register_blueprint(chatbot_bp)

# ── Cache ──
CACHE_DIR = os.path.join(os.path.dirname(__file__), ".cache")
cache_config = {"CACHE_TYPE": "FileSystemCache", "CACHE_DIR": CACHE_DIR,
                "CACHE_DEFAULT_TIMEOUT": 3600}
cache = Cache(app, config=cache_config)


# ── Security headers ──
@app.after_request
def set_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://unpkg.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://unpkg.com; "
        "font-src https://fonts.gstatic.com; "
        "img-src 'self' data: https://*.tile.openstreetmap.org; "
        "connect-src 'self'")
    return response


# ── DB helpers ──
def get_db():
    if "db" not in g:
        g.db = get_connection()
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop("db", None)
    if db:
        put_connection(db)


# ── Pages ──

@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("auth.login"))

@app.route("/dashboard")
@login_required
def dashboard():
    periodos = queries.get_periods(get_db())
    return render_template("dashboard.html",
                           periodos=periodos,
                           default_period=periodos[0] if periodos else "2026-03",
                           role=session.get("role"),
                           nombre=session.get("nombre"))

@app.route("/dashboard/nominal")
@admin_required
def nominal():
    conn = get_db()
    periodos = queries.get_periods(conn)
    return render_template("nominal.html",
                           periodos=periodos,
                           default_period=periodos[0] if periodos else "2026-03",
                           programs=queries.get_programs(conn),
                           provincias=queries.get_provincias(conn),
                           role=session.get("role"),
                           nombre=session.get("nombre"))


# ── API: Indicators ──

_FILTER_KEYS = ("secretaria", "sexo", "programa", "provincia", "departamento",
                "grupo_etario", "cant_prestaciones")

def _filters():
    f = {k: v for k in _FILTER_KEYS if (v := request.args.get(k, "").strip())}
    return f or None

def _cache_key():
    return "rub:" + hashlib.sha256(request.full_path.encode()).hexdigest()[:16]

# Tabla declarativa: (ruta, query_fn, usa_metric)
_INDICATOR_ROUTES = [
    ("summary",        queries.get_summary,         False),
    ("by-secretaria",  queries.get_by_secretaria,   True),
    ("by-provincia",   queries.get_by_provincia,     True),
    ("by-departamento",queries.get_by_departamento,  False),
    ("by-programa",    queries.get_by_programa,      True),
    ("by-sexo",        queries.get_by_sexo,          True),
    ("by-grupo-etario",queries.get_by_grupo_etario,  True),
    ("evolucion",      queries.get_evolucion,         True),
]

def _make_indicator_view(query_fn, has_metric, is_evolucion=False):
    @login_required
    @cache.cached(key_prefix=_cache_key)
    def view():
        if is_evolucion:
            args = [get_db(), _filters()]
        else:
            args = [get_db(), request.args.get("period", "2026-03"), _filters()]
        if has_metric:
            args.append(request.args.get("metric", "personas"))
        return jsonify(query_fn(*args))
    return view

for _name, _fn, _metric in _INDICATOR_ROUTES:
    _is_evo = _name == "evolucion"
    _view = _make_indicator_view(_fn, _metric, _is_evo)
    _view.__name__ = f"api_{_name.replace('-', '_')}"
    app.add_url_rule(f"/api/indicators/{_name}", view_func=_view)

@app.route("/api/admin/clear-cache", methods=["POST"])
@admin_required
def clear_cache():
    cache.clear()
    return jsonify({"ok": True})


# ── API: Nominal (admin only) ──

@app.route("/api/nominal/beneficiaries")
@admin_required
def api_nominal_list():
    period = request.args.get("period", "2026-03")
    filters = {k: request.args.get(k, "").strip() if k != "estado" else request.args.get(k, "ACTIVO")
               for k in ("cuil", "provincia", "programa", "sexo", "estado")}
    page = int(request.args.get("page", 1))
    page_size = int(request.args.get("pageSize", 20))
    return jsonify(queries.get_nominal_list(get_db(), period, filters, page, page_size))

@app.route("/api/nominal/beneficiaries/<int:bid>")
@admin_required
def api_nominal_detail(bid):
    period = request.args.get("period", "2026-03")
    detail = queries.get_nominal_detail(get_db(), bid, period)
    if not detail:
        return jsonify(error="No encontrado"), 404
    return jsonify(detail)


# ── Health ──

@app.route("/api/health/api")
@csrf.exempt
def health_api():
    checks = {}
    try:
        conn = get_db()
        conn.cursor().execute("SELECT 1")
        checks["db"] = "ok"
    except Exception:
        checks["db"] = "error"
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public' AND table_name IN ('mv_cross','mv_resumen')")
        checks["matviews"] = f"{cur.fetchone()[0]}/2"
    except Exception:
        checks["matviews"] = "error"
    checks["cache"] = cache_config.get("CACHE_TYPE", "unknown")
    checks["status"] = "ok" if checks["db"] == "ok" else "degraded"
    return jsonify(checks)

@app.route("/api/health/ml")
@csrf.exempt
def health_ml():
    return jsonify(check_ollama())


# ── Main ──

if __name__ == "__main__":
    print("\n>> RUB Dashboard: http://localhost:5000")
    print("   admin@demo.local / Demo123!\n")
    app.run(debug=os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true"), port=5000)
