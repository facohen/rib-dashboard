"""
RUB Dashboard – Flask + PostgreSQL
Correr con: DATABASE_URL=postgresql://... python app.py
"""
import hashlib
from decimal import Decimal
from functools import wraps
from flask import (Flask, render_template, request, redirect, url_for,
                   session, jsonify, g, flash)
from flask.json.provider import DefaultJSONProvider
from config import get_connection
import queries


class CustomJSONProvider(DefaultJSONProvider):
    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        return super().default(o)


app = Flask(__name__)
app.json_provider_class = CustomJSONProvider
app.json = CustomJSONProvider(app)
app.secret_key = "rub-dashboard-secret-2026-change-in-prod"

# ──────────────────────────────────────────────
# DB helpers
# ──────────────────────────────────────────────

def get_db():
    if "db" not in g:
        g.db = get_connection()
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop("db", None)
    if db:
        db.close()

# ──────────────────────────────────────────────
# Auth helpers
# ──────────────────────────────────────────────

def hash_password(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            if request.path.startswith("/api/"):
                return jsonify(error="No autorizado"), 401
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            if request.path.startswith("/api/"):
                return jsonify(error="No autorizado"), 401
            return redirect(url_for("login"))
        if session.get("role") != "admin":
            if request.path.startswith("/api/"):
                return jsonify(error="Acceso denegado: se requiere rol admin"), 403
            flash("Acceso denegado.", "error")
            return redirect(url_for("dashboard"))
        return f(*args, **kwargs)
    return decorated

# ──────────────────────────────────────────────
# Pages / Auth
# ──────────────────────────────────────────────

@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        user = queries.get_user_by_email(get_db(), email)
        if user and user["password_hash"] == hash_password(password):
            session.permanent = True
            session["user_id"] = user["id"]
            session["email"] = user["email"]
            session["role"] = user["role"]
            session["nombre"] = user["nombre"] or user["email"]
            return redirect(url_for("dashboard"))
        error = "Email o contraseña incorrectos."
    return render_template("login.html", error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/dashboard")
@login_required
def dashboard():
    periodos = queries.get_periods(get_db())
    default_period = periodos[0] if periodos else "2026-03"
    return render_template("dashboard.html",
                           periodos=periodos,
                           default_period=default_period,
                           role=session.get("role"),
                           nombre=session.get("nombre"))

@app.route("/dashboard/nominal")
@admin_required
def nominal():
    conn = get_db()
    periodos = queries.get_periods(conn)
    default_period = periodos[0] if periodos else "2026-03"
    programs = queries.get_programs(conn)
    provincias = queries.get_provincias(conn)
    return render_template("nominal.html",
                           periodos=periodos,
                           default_period=default_period,
                           programs=programs,
                           provincias=provincias,
                           role=session.get("role"),
                           nombre=session.get("nombre"))

# ──────────────────────────────────────────────
# API: Indicators (with cross-chart filtering)
# ──────────────────────────────────────────────

_FILTER_KEYS = ("secretaria", "sexo", "programa", "provincia", "departamento", "grupo_etario")

def _get_chart_filters():
    """Extract cross-chart filter params from query string."""
    f = {}
    for k in _FILTER_KEYS:
        v = request.args.get(k, "").strip()
        if v:
            f[k] = v
    return f or None

@app.route("/api/indicators/summary")
@login_required
def api_summary():
    period = request.args.get("period", "2026-03")
    return jsonify(queries.get_summary(get_db(), period, _get_chart_filters()))

@app.route("/api/indicators/by-secretaria")
@login_required
def api_by_secretaria():
    period = request.args.get("period", "2026-03")
    return jsonify(queries.get_by_secretaria(get_db(), period, _get_chart_filters()))

@app.route("/api/indicators/by-provincia")
@login_required
def api_by_provincia():
    period = request.args.get("period", "2026-03")
    return jsonify(queries.get_by_provincia(get_db(), period, _get_chart_filters()))

@app.route("/api/indicators/by-departamento")
@login_required
def api_by_departamento():
    period = request.args.get("period", "2026-03")
    return jsonify(queries.get_by_departamento(get_db(), period, _get_chart_filters()))

@app.route("/api/indicators/by-programa")
@login_required
def api_by_programa():
    period = request.args.get("period", "2026-03")
    return jsonify(queries.get_by_programa(get_db(), period, _get_chart_filters()))

@app.route("/api/indicators/by-sexo")
@login_required
def api_by_sexo():
    period = request.args.get("period", "2026-03")
    return jsonify(queries.get_by_sexo(get_db(), period, _get_chart_filters()))

@app.route("/api/indicators/by-grupo-etario")
@login_required
def api_by_grupo_etario():
    period = request.args.get("period", "2026-03")
    return jsonify(queries.get_by_grupo_etario(get_db(), period, _get_chart_filters()))

@app.route("/api/indicators/evolucion")
@login_required
def api_evolucion():
    return jsonify(queries.get_evolucion(get_db(), _get_chart_filters()))

# ──────────────────────────────────────────────
# API: Nominal (admin only)
# ──────────────────────────────────────────────

@app.route("/api/nominal/beneficiaries")
@admin_required
def api_nominal_list():
    period = request.args.get("period", "2026-03")
    filters = {
        "cuil": request.args.get("cuil", "").strip(),
        "provincia": request.args.get("provincia", ""),
        "programa": request.args.get("programa", ""),
        "sexo": request.args.get("sexo", ""),
        "estado": request.args.get("estado", "ACTIVO"),
    }
    page = int(request.args.get("page", 1))
    page_size = int(request.args.get("pageSize", 20))
    result = queries.get_nominal_list(get_db(), period, filters, page, page_size)
    return jsonify(result)

@app.route("/api/nominal/beneficiaries/<int:bid>")
@admin_required
def api_nominal_detail(bid):
    period = request.args.get("period", "2026-03")
    detail = queries.get_nominal_detail(get_db(), bid, period)
    if not detail:
        return jsonify(error="No encontrado"), 404
    return jsonify(detail)

# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

if __name__ == "__main__":
    print("\n🚀 RUB Dashboard corriendo en http://localhost:5000")
    print("   Requiere DATABASE_URL con PostgreSQL")
    print("   admin@demo.local / Demo123!")
    print("   user@demo.local  / Demo123!\n")
    app.run(debug=True, port=5000)
