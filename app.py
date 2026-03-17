"""
RUB Dashboard – Flask + PostgreSQL
Correr con: DATABASE_URL=postgresql://... python app.py
Producción: gunicorn -w 4 app:app --bind 0.0.0.0:5000
"""
import hashlib
import json as json_mod
import os
import re
import time
from collections import defaultdict
from decimal import Decimal
from functools import wraps
import requests as http_requests
from flask import (Flask, render_template, request, redirect, url_for,
                   session, jsonify, g, flash, Response)
from flask.json.provider import DefaultJSONProvider
from flask_caching import Cache
from flask_wtf.csrf import CSRFProtect
from werkzeug.security import generate_password_hash, check_password_hash
from config import get_connection, put_connection
import queries


class CustomJSONProvider(DefaultJSONProvider):
    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        return super().default(o)


app = Flask(__name__)
app.json_provider_class = CustomJSONProvider
app.json = CustomJSONProvider(app)

# ── Secret key: from env or ephemeral random ──
_secret = os.environ.get("FLASK_SECRET_KEY")
if not _secret:
    _secret = os.urandom(32).hex()
    print("[WARN] FLASK_SECRET_KEY no seteada — usando key efímera (sessions no sobreviven restarts)")
app.secret_key = _secret

# ── Session cookie hardening ──
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = 3600  # 1 hora
# SESSION_COOKIE_SECURE = True  # TODO: activar cuando haya TLS

# ── CSRF protection ──
csrf = CSRFProtect(app)

# ──────────────────────────────────────────────
# Cache: FileSystemCache (shared across Gunicorn workers)
# ──────────────────────────────────────────────
CACHE_DIR = os.path.join(os.path.dirname(__file__), ".cache")
cache_config = {
    "CACHE_TYPE": "FileSystemCache",
    "CACHE_DIR": CACHE_DIR,
    "CACHE_DEFAULT_TIMEOUT": 3600,
}
cache = Cache(app, config=cache_config)


# ──────────────────────────────────────────────
# Security headers
# ──────────────────────────────────────────────

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
        "connect-src 'self'"
    )
    return response


# ──────────────────────────────────────────────
# Ollama / Chatbot config
# ──────────────────────────────────────────────

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
CHATBOT_MODEL = os.environ.get("CHATBOT_MODEL", "qwen2.5-coder:7b")

SQL_GEN_PROMPT = """Generador SQL para PostgreSQL. Responde UNICAMENTE con una query SELECT. Sin explicacion, sin backticks, sin markdown. LIMIT 100 siempre. Si no podes generar SQL, responde exactamente: NO_SQL

TABLAS:
beneficiaries(id,cuil,nombre,apellido,sexo,fecha_nacimiento,provincia,departamento)
secretarias(id,nombre)
programs(id,secretaria_id REFERENCES secretarias(id),nombre_programa)
benefits(id,beneficiary_id,program_id,periodo_mes,estado_beneficio)
payments(id,beneficiary_id,program_id,periodo_mes,monto_prestacion)
incompatibility_rules(id,program_a_id,program_b_id,is_compatible,descripcion)

REGLAS: estado_beneficio='ACTIVO', periodo_mes='2026-03', contar=COUNT(DISTINCT b.beneficiary_id)

EJ:
total: SELECT COUNT(DISTINCT beneficiary_id) FROM benefits WHERE estado_beneficio='ACTIVO' AND periodo_mes='2026-03'
x_prog: SELECT p.nombre_programa,COUNT(DISTINCT b.beneficiary_id) n FROM benefits b JOIN programs p ON p.id=b.program_id WHERE b.estado_beneficio='ACTIVO' AND b.periodo_mes='2026-03' GROUP BY 1 ORDER BY n DESC LIMIT 100
x_prov: SELECT ben.provincia,COUNT(DISTINCT b.beneficiary_id) n FROM benefits b JOIN beneficiaries ben ON ben.id=b.beneficiary_id WHERE b.estado_beneficio='ACTIVO' AND b.periodo_mes='2026-03' GROUP BY 1 ORDER BY n DESC LIMIT 100
montos: SELECT p.nombre_programa,SUM(pay.monto_prestacion) FROM payments pay JOIN programs p ON p.id=pay.program_id WHERE pay.periodo_mes='2026-03' GROUP BY 1 ORDER BY 2 DESC LIMIT 100
multi: SELECT COUNT(*) FROM (SELECT beneficiary_id FROM benefits WHERE estado_beneficio='ACTIVO' AND periodo_mes='2026-03' GROUP BY 1 HAVING COUNT(DISTINCT program_id)>1) s
crosstab: SELECT ben.provincia,SUM(CASE WHEN ben.sexo='F' THEN 1 ELSE 0 END) femenino,SUM(CASE WHEN ben.sexo='M' THEN 1 ELSE 0 END) masculino,SUM(CASE WHEN ben.sexo='X' THEN 1 ELSE 0 END) no_binario,COUNT(DISTINCT b.beneficiary_id) total FROM benefits b JOIN beneficiaries ben ON ben.id=b.beneficiary_id WHERE b.estado_beneficio='ACTIVO' AND b.periodo_mes='2026-03' GROUP BY 1 ORDER BY total DESC LIMIT 10

IMPORTANTE: Cuando pidan desglose por sexo, programa u otra dimension dentro de un ranking (ej "top provincias por sexo"), usa SUM(CASE WHEN ... THEN 1 ELSE 0 END) para pivotar columnas. NO uses GROUP BY con dos dimensiones + LIMIT porque trunca combinaciones.
"""

NO_SQL_RESPONSE = "Soy un modelo de IA para contestar preguntas sobre el RUB."
NO_DATA_RESPONSE = "No se encontraron datos para esa consulta."

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
        put_connection(db)

# ──────────────────────────────────────────────
# Auth helpers
# ──────────────────────────────────────────────

def hash_password(pw):
    return generate_password_hash(pw)

def _is_legacy_sha256(h):
    """Detecta hash SHA256 legacy (64 hex chars sin separador pbkdf2)."""
    return len(h) == 64 and all(c in "0123456789abcdef" for c in h)

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

# ── Rate limiting (in-memory) ──
_login_attempts = defaultdict(list)

def _check_rate_limit(ip, max_attempts=5, window=300):
    now = time.time()
    _login_attempts[ip] = [t for t in _login_attempts[ip] if now - t < window]
    if len(_login_attempts[ip]) >= max_attempts:
        return False
    _login_attempts[ip].append(now)
    return True

@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    error = None
    if request.method == "POST":
        if not _check_rate_limit(request.remote_addr):
            error = "Demasiados intentos. Esperá 5 minutos."
            return render_template("login.html", error=error)

        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        conn = get_db()
        user = queries.get_user_by_email(conn, email)

        # Verificar password (soporta legacy SHA256 y werkzeug pbkdf2)
        password_ok = False
        if user:
            stored = user["password_hash"]
            if _is_legacy_sha256(stored):
                # Legacy: SHA256 sin salt
                password_ok = (stored == hashlib.sha256(password.encode()).hexdigest())
                if password_ok:
                    # Migración transparente a werkzeug hash
                    new_hash = generate_password_hash(password)
                    try:
                        cur = conn.cursor()
                        cur.execute("UPDATE users SET password_hash = %s WHERE id = %s",
                                    (new_hash, user["id"]))
                        conn.commit()
                    except Exception:
                        conn.rollback()
            else:
                password_ok = check_password_hash(stored, password)

        if password_ok:
            session.clear()  # Fix session fixation
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
# Chatbot con IA
# ──────────────────────────────────────────────

@app.route("/dashboard/chatbot")
@login_required
def chatbot():
    return render_template("chatbot.html",
                           role=session.get("role"),
                           nombre=session.get("nombre"))

@app.route("/api/chatbot/status")
@login_required
def api_chatbot_status():
    print(f"\n[CHATBOT-STATUS] Verificando Ollama en {OLLAMA_URL}...")
    try:
        resp = http_requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        resp.raise_for_status()
        data = resp.json()
        models = [m["name"] for m in data.get("models", [])]
        model_available = any(CHATBOT_MODEL.split(":")[0] in m for m in models)
        print(f"[CHATBOT-STATUS] Ollama ONLINE")
        print(f"[CHATBOT-STATUS] Modelos disponibles: {models}")
        print(f"[CHATBOT-STATUS] Modelo configurado: {CHATBOT_MODEL} -> {'DISPONIBLE' if model_available else 'NO DISPONIBLE'}")
        return jsonify(online=True, model=CHATBOT_MODEL,
                       model_available=model_available, available_models=models)
    except http_requests.ConnectionError as e:
        print(f"[CHATBOT-STATUS] Ollama OFFLINE - no se pudo conectar: {e}")
        return jsonify(online=False, model=CHATBOT_MODEL,
                       model_available=False, available_models=[])
    except Exception as e:
        print(f"[CHATBOT-STATUS] Error inesperado: {type(e).__name__}: {e}")
        return jsonify(online=False, model=CHATBOT_MODEL,
                       model_available=False, available_models=[])

@app.route("/api/chatbot/ask", methods=["POST"])
@login_required
def api_chatbot_ask():
    data = request.get_json()
    user_msg = data.get("message", "").strip()
    history = data.get("history", [])

    if not user_msg:
        return jsonify(error="Mensaje vacio"), 400

    import time
    t0 = time.time()
    print(f"\n{'='*60}")
    print(f"[CHATBOT] {time.strftime('%H:%M:%S')} Nueva consulta: {user_msg}")

    # ── Fase 1: Generar SQL (sin historial, modelo chico se confunde) ──
    sql_messages = [
        {"role": "system", "content": SQL_GEN_PROMPT},
        {"role": "user", "content": user_msg}
    ]

    sql_clean, sql_error = _call_ollama_sql(sql_messages, t0)
    if sql_clean is None and sql_error:
        return jsonify(error=sql_error), 502

    # Si no genero SQL valido, responder mensaje fijo
    if sql_clean is None:
        print(f"[CHATBOT] No es SQL -> respuesta fija")
        return _direct_response(NO_SQL_RESPONSE, "", "", 0)

    # ── Fase 2: Ejecutar SQL (read-only) ──
    sql_result, sql_error = _execute_sql_readonly(sql_clean)

    # ── Fase 2b: Retry si hubo error SQL (1 intento) ──
    if sql_error:
        print(f"[CHATBOT] SQL error, reintentando con feedback...")
        retry_messages = sql_messages + [
            {"role": "assistant", "content": sql_clean},
            {"role": "user", "content": f"Esa query dio error: {sql_error}\nCorregila. Solo la query SQL corregida, nada mas."}
        ]
        sql_clean_retry, retry_err = _call_ollama_sql(retry_messages, time.time())
        if sql_clean_retry:
            print(f"[CHATBOT] Retry SQL: {sql_clean_retry[:200]}")
            sql_result, sql_error = _execute_sql_readonly(sql_clean_retry)
            if not sql_error:
                sql_clean = sql_clean_retry

    # ── Fase 3: Formatear respuesta en Python (sin segunda llamada a Ollama) ──
    elapsed = time.time() - t0
    print(f"[CHATBOT] Formateando respuesta ({elapsed:.1f}s acumulado)")

    if sql_error:
        answer = f"Error al consultar los datos: {sql_error}"
    elif not sql_result or not sql_result["rows"]:
        answer = NO_DATA_RESPONSE
    else:
        answer = _format_sql_result(user_msg, sql_result)

    print(f"[CHATBOT] COMPLETADO en {time.time()-t0:.1f}s")
    print(f"{'='*60}")
    return _direct_response(answer, sql_clean or "", sql_error or "",
                            sql_result["row_count"] if sql_result else 0)


def _call_ollama_sql(messages, t0):
    """Llama a Ollama para generar SQL. Retorna (sql_clean, error)."""
    import time
    payload = {"model": CHATBOT_MODEL, "messages": messages, "stream": False,
               "options": {"num_predict": 300, "temperature": 0}}
    try:
        resp = http_requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=120)
        resp.raise_for_status()
        raw_resp = resp.json()
        sql_raw = raw_resp["message"]["content"].strip()

        eval_count = raw_resp.get("eval_count", 0)
        eval_dur = raw_resp.get("eval_duration", 0) / 1e9
        tps = eval_count / eval_dur if eval_dur > 0 else 0
        print(f"[CHATBOT] SQL generado en {time.time()-t0:.1f}s ({eval_count} tok, {tps:.1f} tok/s)")
        print(f"[CHATBOT]   Raw: {sql_raw[:300]}")

        # Limpiar backticks/markdown
        sql_clean = sql_raw.strip().strip("`").strip()
        if sql_clean.startswith("```"):
            sql_clean = sql_clean.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        if sql_clean.upper().startswith("NO_SQL") or not sql_clean.upper().lstrip().startswith("SELECT"):
            return None, None  # No es SQL, no es error

        return sql_clean, None

    except http_requests.ConnectionError:
        return None, "No se pudo conectar con Ollama. Verifica que este ejecutandose."
    except http_requests.Timeout:
        return None, f"Ollama tardo demasiado (timeout 120s)."
    except Exception as e:
        return None, f"Error con Ollama: {e}"


_SQL_ALLOWED_TABLES = {"beneficiaries", "benefits", "payments", "programs",
                       "incompatibility_rules", "secretarias"}

def _execute_sql_readonly(sql_clean):
    """Ejecuta SQL en modo read-only con allowlist de tablas. Retorna (result_dict, error_str)."""
    # Validar tablas referenciadas contra allowlist
    referenced = set(re.findall(r'(?:FROM|JOIN)\s+(\w+)', sql_clean, re.IGNORECASE))
    forbidden = referenced - _SQL_ALLOWED_TABLES
    if forbidden:
        return None, f"Tablas no permitidas: {', '.join(forbidden)}"

    # Agregar LIMIT si no tiene
    if not re.search(r'\bLIMIT\b', sql_clean, re.IGNORECASE):
        sql_clean = sql_clean.rstrip(";").strip() + " LIMIT 100"

    print(f"[CHATBOT] Ejecutando SQL: {sql_clean[:200]}")
    t_sql = time.time()
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SET TRANSACTION READ ONLY")
        cur.execute(sql_clean)
        columns = [desc[0] for desc in cur.description] if cur.description else []
        rows = cur.fetchmany(100)
        result = {"columns": columns,
                  "rows": [dict(zip(columns, row)) for row in rows],
                  "row_count": len(rows)}
        conn.commit()
        print(f"[CHATBOT] SQL OK en {time.time()-t_sql:.3f}s: {len(rows)} filas")
        return result, None
    except Exception as e:
        err = str(e)
        print(f"[CHATBOT] SQL ERROR: {err}")
        try:
            conn.rollback()
        except Exception:
            pass
        return None, err


def _mask_cuil(val):
    """Maskea CUIL para proteger PII: 20-12345678-9 → *******8-9"""
    s = str(val)
    if len(s) >= 4:
        return "*" * (len(s) - 4) + s[-4:]
    return s

def _format_sql_result(user_msg, sql_result):
    """Formatea el resultado SQL como markdown (sin LLM)."""
    columns = sql_result["columns"]
    rows = sql_result["rows"]

    # Maskear columnas CUIL para proteger PII
    cuil_cols = {c for c in columns if c.lower() in ("cuil", "cuil_raw")}
    if cuil_cols:
        for row in rows:
            for col in cuil_cols:
                if row.get(col):
                    row[col] = _mask_cuil(row[col])

    # Caso simple: 1 fila, 1 columna (ej: COUNT)
    if len(rows) == 1 and len(columns) == 1:
        val = rows[0][columns[0]]
        return f"**{_fmt_value(val)}**"

    # Caso simple: 1 fila, pocas columnas
    if len(rows) == 1 and len(columns) <= 4:
        parts = []
        for col in columns:
            parts.append(f"**{col}**: {_fmt_value(rows[0][col])}")
        return " | ".join(parts)

    # Tabla markdown
    header = "| " + " | ".join(columns) + " |"
    separator = "| " + " | ".join("---" for _ in columns) + " |"
    lines = [header, separator]
    for row in rows:
        cells = []
        for col in columns:
            cells.append(_fmt_value(row[col]))
        lines.append("| " + " | ".join(cells) + " |")

    return "\n".join(lines)


def _fmt_value(val):
    """Formatea un valor para display."""
    if val is None:
        return "-"
    if isinstance(val, (int, float, Decimal)):
        if isinstance(val, float) and val == int(val):
            val = int(val)
        if isinstance(val, (int,)) and abs(val) >= 1000:
            return f"{val:,.0f}".replace(",", ".")
        if isinstance(val, (float, Decimal)) and abs(val) >= 1000:
            return f"{val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return str(val)
    return str(val)


def _direct_response(answer, sql_query, sql_error, row_count):
    """Retorna respuesta directa como stream NDJSON (compatible con frontend)."""
    def generate():
        yield json_mod.dumps({"type": "meta",
                               "sql": sql_query,
                               "sql_error": sql_error,
                               "row_count": row_count}) + "\n"
        # Enviar toda la respuesta como tokens para compatibilidad con el frontend
        yield json_mod.dumps({"type": "token", "content": answer, "done": False}) + "\n"
        yield json_mod.dumps({"type": "token", "content": "", "done": True}) + "\n"

    return Response(generate(), mimetype="application/x-ndjson")

# ──────────────────────────────────────────────
# API: Indicators (with cross-chart filtering)
# ──────────────────────────────────────────────

_FILTER_KEYS = ("secretaria", "sexo", "programa", "provincia", "departamento", "grupo_etario", "cant_prestaciones")

def _get_chart_filters():
    """Extract cross-chart filter params from query string."""
    f = {}
    for k in _FILTER_KEYS:
        v = request.args.get(k, "").strip()
        if v:
            f[k] = v
    return f or None

def _cache_key():
    """Cache key: hashed to avoid path traversal and namespace collisions."""
    return "rub:" + hashlib.sha256(request.full_path.encode()).hexdigest()[:16]

@app.route("/api/indicators/summary")
@login_required
@cache.cached(key_prefix=_cache_key)
def api_summary():
    period = request.args.get("period", "2026-03")
    return jsonify(queries.get_summary(get_db(), period, _get_chart_filters()))

@app.route("/api/indicators/by-secretaria")
@login_required
@cache.cached(key_prefix=_cache_key)
def api_by_secretaria():
    period = request.args.get("period", "2026-03")
    metric = request.args.get("metric", "personas")
    return jsonify(queries.get_by_secretaria(get_db(), period, _get_chart_filters(), metric))

@app.route("/api/indicators/by-provincia")
@login_required
@cache.cached(key_prefix=_cache_key)
def api_by_provincia():
    period = request.args.get("period", "2026-03")
    metric = request.args.get("metric", "personas")
    return jsonify(queries.get_by_provincia(get_db(), period, _get_chart_filters(), metric))

@app.route("/api/indicators/by-departamento")
@login_required
@cache.cached(key_prefix=_cache_key)
def api_by_departamento():
    period = request.args.get("period", "2026-03")
    return jsonify(queries.get_by_departamento(get_db(), period, _get_chart_filters()))

@app.route("/api/indicators/by-programa")
@login_required
@cache.cached(key_prefix=_cache_key)
def api_by_programa():
    period = request.args.get("period", "2026-03")
    metric = request.args.get("metric", "personas")
    return jsonify(queries.get_by_programa(get_db(), period, _get_chart_filters(), metric))

@app.route("/api/indicators/by-sexo")
@login_required
@cache.cached(key_prefix=_cache_key)
def api_by_sexo():
    period = request.args.get("period", "2026-03")
    metric = request.args.get("metric", "personas")
    return jsonify(queries.get_by_sexo(get_db(), period, _get_chart_filters(), metric))

@app.route("/api/indicators/by-grupo-etario")
@login_required
@cache.cached(key_prefix=_cache_key)
def api_by_grupo_etario():
    period = request.args.get("period", "2026-03")
    metric = request.args.get("metric", "personas")
    return jsonify(queries.get_by_grupo_etario(get_db(), period, _get_chart_filters(), metric))

@app.route("/api/indicators/evolucion")
@login_required
@cache.cached(key_prefix=_cache_key)
def api_evolucion():
    metric = request.args.get("metric", "personas")
    return jsonify(queries.get_evolucion(get_db(), _get_chart_filters(), metric))

@app.route("/api/admin/clear-cache", methods=["POST"])
@admin_required
def clear_cache():
    cache.clear()
    return jsonify({"ok": True})

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
# Health endpoints (no auth — para monitoring)
# ──────────────────────────────────────────────

@app.route("/api/health/api")
@csrf.exempt
def health_api():
    """Check: DB connection, tables, MVs, cache type."""
    checks = {}
    try:
        conn = get_db()
        conn.cursor().execute("SELECT 1")
        checks["db"] = "ok"
    except Exception:
        checks["db"] = "error"
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public' AND table_name IN ('mv_cross', 'mv_resumen')")
        mv_count = cur.fetchone()[0]
        checks["matviews"] = f"{mv_count}/2"
    except Exception:
        checks["matviews"] = "error"
    checks["cache"] = cache_config.get("CACHE_TYPE", "unknown")
    checks["status"] = "ok" if checks["db"] == "ok" else "degraded"
    return jsonify(checks)


@app.route("/api/health/ml")
@csrf.exempt
def health_ml():
    """Check: Ollama reachable, model loaded."""
    checks = {}
    try:
        r = http_requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        models = [m["name"] for m in r.json().get("models", [])]
        checks["ollama"] = "ok"
        checks["models"] = models
        checks["target_model"] = CHATBOT_MODEL
        checks["model_loaded"] = any(CHATBOT_MODEL in m for m in models)
    except Exception:
        checks["ollama"] = "unreachable"
        checks["model_loaded"] = False
    checks["status"] = "ok" if checks.get("model_loaded") else "degraded"
    return jsonify(checks)


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

if __name__ == "__main__":
    print("\n>> RUB Dashboard corriendo en http://localhost:5000")
    print("   Requiere DATABASE_URL con PostgreSQL")
    print("   admin@demo.local / Demo123!")
    print("   user@demo.local  / Demo123!\n")
    app.run(debug=os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true"), port=5000)
