"""
RIB Dashboard – Flask + PostgreSQL
Correr con: DATABASE_URL=postgresql://... python app.py
Produccion: gunicorn -w 4 app:app --bind 0.0.0.0:5000
"""
import os
from decimal import Decimal

from flask import (Flask, render_template, redirect, url_for,
                   session, jsonify, g)
from flask.json.provider import DefaultJSONProvider
from flask_caching import Cache
from flask_compress import Compress
from flask_wtf.csrf import CSRFProtect

from config import get_connection, put_connection
from auth import auth_bp, login_required, admin_required
from chatbot import chatbot_bp, check_ollama
from endpoints_td import td_bp, init_td
from endpoints_tc import tc_bp, init_tc
from queries_helpers import get_periods, get_programs, get_provincias


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
app.register_blueprint(td_bp)
app.register_blueprint(tc_bp)

# ── Cache ──
CACHE_DIR = os.path.join(os.path.dirname(__file__), ".cache")
cache_config = {"CACHE_TYPE": "FileSystemCache", "CACHE_DIR": CACHE_DIR,
                "CACHE_DEFAULT_TIMEOUT": 3600}
cache = Cache(app, config=cache_config)

init_td(cache)
init_tc(cache)


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
    periodos = get_periods(get_db())
    return render_template("dashboard.html",
                           periodos=periodos,
                           default_period=periodos[0] if periodos else "2026-03",
                           role=session.get("role"),
                           nombre=session.get("nombre"))

@app.route("/dashboard/nominal")
@admin_required
def nominal():
    conn = get_db()
    periodos = get_periods(conn)
    return render_template("nominal.html",
                           periodos=periodos,
                           default_period=periodos[0] if periodos else "2026-03",
                           programs=get_programs(conn),
                           provincias=get_provincias(conn),
                           role=session.get("role"),
                           nombre=session.get("nombre"))


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
        cur.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public' AND table_name IN ('mv_cross','mv_resumen','mv_cross_tc','mv_resumen_tc','mv_nominal_tc')")
        checks["matviews"] = f"{cur.fetchone()[0]}/5"
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
    print("\n>> RIB Dashboard: http://localhost:5000")
    print("   admin@demo.local / Demo123!\n")
    app.run(debug=os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true"), port=5000)
