"""
RUB Dashboard – Flask + PostgreSQL
Correr con: DATABASE_URL=postgresql://... python app.py
"""
import hashlib
import json as json_mod
import os
from decimal import Decimal
from functools import wraps
import requests as http_requests
from flask import (Flask, render_template, request, redirect, url_for,
                   session, jsonify, g, flash, Response)
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
# Ollama / Chatbot config
# ──────────────────────────────────────────────

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
CHATBOT_MODEL = os.environ.get("CHATBOT_MODEL", "qwen2.5-coder:7b")

SQL_GEN_PROMPT = """Solo SELECT PostgreSQL. Solo la query, sin explicacion, sin backticks. LIMIT 100. Si no podes, responde NO_SQL.

TABLAS:
beneficiaries(id,cuil,nombre,apellido,sexo,fecha_nacimiento,provincia,departamento)
programs(id,secretaria_origen,nombre_programa)
benefits(id,beneficiary_id,program_id,periodo_mes,estado_beneficio)
payments(id,beneficiary_id,program_id,periodo_mes,monto_prestacion)
incompatibility_rules(id,program_a_id,program_b_id,is_compatible,descripcion)

REGLAS: estado_beneficio='ACTIVO', periodo_mes='2026-03', contar=COUNT(DISTINCT b.beneficiary_id)

EJ:
total: SELECT COUNT(DISTINCT beneficiary_id) FROM benefits WHERE estado_beneficio='ACTIVO' AND periodo_mes='2026-03'
x_prog: SELECT p.nombre_programa,COUNT(DISTINCT b.beneficiary_id) n FROM benefits b JOIN programs p ON p.id=b.program_id WHERE b.estado_beneficio='ACTIVO' GROUP BY 1 ORDER BY n DESC LIMIT 100
x_prov: SELECT ben.provincia,COUNT(DISTINCT b.beneficiary_id) n FROM benefits b JOIN beneficiaries ben ON ben.id=b.beneficiary_id WHERE b.estado_beneficio='ACTIVO' GROUP BY 1 ORDER BY n DESC LIMIT 100
montos: SELECT p.nombre_programa,SUM(pay.monto_prestacion) FROM payments pay JOIN programs p ON p.id=pay.program_id WHERE pay.periodo_mes='2026-03' GROUP BY 1 ORDER BY 2 DESC LIMIT 100
multi: SELECT COUNT(*) FROM (SELECT beneficiary_id FROM benefits WHERE estado_beneficio='ACTIVO' AND periodo_mes='2026-03' GROUP BY 1 HAVING COUNT(DISTINCT program_id)>1) s
"""

ANSWER_PROMPT = """Responde en espanol usando SOLO los datos recibidos. No inventes datos.
Formato markdown: tablas con | y --- para datos tabulares, **negrita** para numeros clave.
Montos en pesos con separador de miles. Se breve."""

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
    print(f"[CHATBOT] {time.strftime('%H:%M:%S')} Nueva consulta")
    print(f"[CHATBOT] Pregunta: {user_msg}")
    print(f"[CHATBOT] Modelo: {CHATBOT_MODEL}")
    print(f"[CHATBOT] Ollama URL: {OLLAMA_URL}")
    print(f"[CHATBOT] Historial: {len(history)} mensajes previos")
    print(f"[CHATBOT] Prompt sistema: {len(SQL_GEN_PROMPT)} chars")
    print(f"[CHATBOT] Fase 1: Generando SQL...")

    # Fase 1: Generar SQL
    sql_messages = [
        {"role": "system", "content": SQL_GEN_PROMPT},
        {"role": "user", "content": user_msg}
    ]
    payload = {"model": CHATBOT_MODEL, "messages": sql_messages, "stream": False,
                "options": {"num_predict": 200, "temperature": 0}}
    print(f"[CHATBOT]   POST {OLLAMA_URL}/api/chat (stream=False, timeout=120)")
    print(f"[CHATBOT]   Payload size: {len(json_mod.dumps(payload))} bytes")
    try:
        resp = http_requests.post(f"{OLLAMA_URL}/api/chat",
            json=payload, timeout=120)
        print(f"[CHATBOT]   HTTP {resp.status_code} en {time.time()-t0:.1f}s")
        resp.raise_for_status()
        raw_resp = resp.json()
        sql_query = raw_resp["message"]["content"].strip()
        t1 = time.time()
        # Metricas de Ollama
        total_dur = raw_resp.get("total_duration", 0) / 1e9
        load_dur = raw_resp.get("load_duration", 0) / 1e9
        prompt_eval_count = raw_resp.get("prompt_eval_count", 0)
        prompt_eval_dur = raw_resp.get("prompt_eval_duration", 0) / 1e9
        eval_count = raw_resp.get("eval_count", 0)
        eval_dur = raw_resp.get("eval_duration", 0) / 1e9
        tps = eval_count / eval_dur if eval_dur > 0 else 0
        prompt_tps = prompt_eval_count / prompt_eval_dur if prompt_eval_dur > 0 else 0
        print(f"[CHATBOT] Fase 1 COMPLETADA en {t1-t0:.1f}s")
        print(f"[CHATBOT]   Ollama total: {total_dur:.1f}s")
        print(f"[CHATBOT]   Model load: {load_dur:.1f}s")
        print(f"[CHATBOT]   Prompt eval: {prompt_eval_count} tokens en {prompt_eval_dur:.1f}s ({prompt_tps:.1f} tok/s)")
        print(f"[CHATBOT]   Generation: {eval_count} tokens en {eval_dur:.1f}s ({tps:.1f} tok/s)")
        print(f"[CHATBOT]   SQL raw ({len(sql_query)} chars): {sql_query[:500]}")
    except http_requests.ConnectionError as e:
        print(f"[CHATBOT] ERROR CONEXION: {e}")
        return jsonify(error="No se pudo conectar con Ollama. Verifica que este ejecutandose."), 502
    except http_requests.Timeout as e:
        elapsed = time.time() - t0
        print(f"[CHATBOT] ERROR TIMEOUT despues de {elapsed:.1f}s: {e}")
        return jsonify(error=f"Ollama tardo demasiado ({elapsed:.0f}s). El modelo puede ser muy pesado para CPU."), 504
    except Exception as e:
        elapsed = time.time() - t0
        print(f"[CHATBOT] ERROR {type(e).__name__} despues de {elapsed:.1f}s: {e}")
        return jsonify(error=f"Error con Ollama: {e}"), 502

    # Si el modelo no genero SQL valido, responder sin datos
    sql_clean = sql_query.strip().strip("`").strip()
    if sql_clean.startswith("```"):
        sql_clean = sql_clean.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    if sql_clean.upper().startswith("NO_SQL") or not sql_clean.upper().lstrip().startswith("SELECT"):
        print(f"[CHATBOT] No es SQL valido -> respondiendo sin datos")
        print(f"[CHATBOT]   Contenido: {sql_query[:200]}")
        return _stream_answer(user_msg, history, None, None)

    print(f"[CHATBOT] SQL limpio: {sql_clean[:300]}")

    # Ejecutar SQL (solo SELECT)
    print(f"[CHATBOT] Fase 2: Ejecutando SQL en PostgreSQL...")
    sql_result = None
    sql_error = None
    try:
        conn = get_db()
        cur = conn.cursor()
        t_sql = time.time()
        cur.execute(sql_clean)
        columns = [desc[0] for desc in cur.description] if cur.description else []
        rows = cur.fetchmany(100)
        sql_result = {"columns": columns,
                      "rows": [dict(zip(columns, row)) for row in rows],
                      "row_count": len(rows)}
        t2 = time.time()
        print(f"[CHATBOT] Fase 2 COMPLETADA en {t2-t_sql:.3f}s")
        print(f"[CHATBOT]   Resultado: {len(rows)} filas, {len(columns)} columnas")
        if columns:
            print(f"[CHATBOT]   Columnas: {columns}")
        if rows:
            print(f"[CHATBOT]   Primera fila: {dict(zip(columns, rows[0]))}")
        if len(rows) >= 100:
            print(f"[CHATBOT]   WARN: resultado truncado a 100 filas")
    except Exception as e:
        sql_error = str(e)
        t2 = time.time()
        print(f"[CHATBOT] Fase 2 SQL ERROR: {sql_error}")
        try:
            conn.rollback()
        except Exception:
            pass

    total_so_far = time.time() - t0
    print(f"[CHATBOT] Fase 3: Streaming respuesta... (tiempo acumulado: {total_so_far:.1f}s)")
    return _stream_answer(user_msg, history, sql_clean, sql_result, sql_error)


def _stream_answer(user_msg, history, sql_query, sql_result, sql_error=None):
    import time
    context_parts = []
    if sql_result and sql_result["rows"]:
        context_parts.append(f"\nDATOS (resultado de la query SQL):\n"
                             f"{json_mod.dumps(sql_result, ensure_ascii=False, default=str)}")
    elif sql_error:
        context_parts.append(f"\nError ejecutando la query SQL: {sql_error}")

    full_user_msg = user_msg
    if context_parts:
        full_user_msg += "\n".join(context_parts)

    messages = [{"role": "system", "content": ANSWER_PROMPT}]
    for h in history[-10:]:
        messages.append({"role": h.get("role", "user"), "content": h.get("content", "")})
    messages.append({"role": "user", "content": full_user_msg})

    payload_size = len(json_mod.dumps(messages, ensure_ascii=False, default=str))
    print(f"[CHATBOT-STREAM] Preparando respuesta")
    print(f"[CHATBOT-STREAM]   Mensajes: {len(messages)} ({payload_size} bytes)")
    print(f"[CHATBOT-STREAM]   SQL incluido: {'si' if sql_query else 'no'}")
    print(f"[CHATBOT-STREAM]   Datos incluidos: {'si' if sql_result and sql_result['rows'] else 'no'}")
    print(f"[CHATBOT-STREAM]   Error SQL: {sql_error if sql_error else 'ninguno'}")

    def generate():
        yield json_mod.dumps({"type": "meta",
                               "sql": sql_query or "",
                               "sql_error": sql_error or "",
                               "row_count": sql_result["row_count"] if sql_result else 0}) + "\n"
        t_stream = time.time()
        token_count = 0
        try:
            print(f"[CHATBOT-STREAM] POST {OLLAMA_URL}/api/chat (stream=True, timeout=120)")
            resp = http_requests.post(f"{OLLAMA_URL}/api/chat",
                json={"model": CHATBOT_MODEL, "messages": messages, "stream": True,
                      "options": {"num_predict": 500, "temperature": 0}},
                stream=True, timeout=180)
            print(f"[CHATBOT-STREAM] HTTP {resp.status_code}")
            resp.raise_for_status()
            first_token_time = None
            for line in resp.iter_lines():
                if line:
                    chunk = json_mod.loads(line)
                    token = chunk.get("message", {}).get("content", "")
                    done = chunk.get("done", False)
                    if token and first_token_time is None:
                        first_token_time = time.time()
                        print(f"[CHATBOT-STREAM] Primer token en {first_token_time - t_stream:.1f}s")
                    token_count += 1
                    yield json_mod.dumps({"type": "token", "content": token, "done": done}) + "\n"
                    if done:
                        elapsed = time.time() - t_stream
                        # Ollama final stats
                        eval_count = chunk.get("eval_count", token_count)
                        eval_dur = chunk.get("eval_duration", 0) / 1e9
                        tps = eval_count / eval_dur if eval_dur > 0 else 0
                        print(f"[CHATBOT-STREAM] COMPLETADO en {elapsed:.1f}s")
                        print(f"[CHATBOT-STREAM]   Tokens: {eval_count} ({tps:.1f} tok/s)")
                        print(f"{'='*60}")
        except http_requests.ConnectionError as e:
            print(f"[CHATBOT-STREAM] ERROR CONEXION: {e}")
            yield json_mod.dumps({"type": "error",
                                   "content": "No se pudo conectar con Ollama"}) + "\n"
        except http_requests.Timeout as e:
            print(f"[CHATBOT-STREAM] ERROR TIMEOUT: {e}")
            yield json_mod.dumps({"type": "error",
                                   "content": "Ollama tardo demasiado generando la respuesta"}) + "\n"
        except Exception as e:
            print(f"[CHATBOT-STREAM] ERROR {type(e).__name__}: {e}")
            yield json_mod.dumps({"type": "error", "content": str(e)}) + "\n"

    return Response(generate(), mimetype="application/x-ndjson")

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
