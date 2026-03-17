"""
chatbot.py — Chatbot con IA (Ollama) para consultas SQL al RUB.

Blueprint Flask. Genera SQL via LLM, ejecuta read-only, formatea resultado.
"""
import json as json_mod
import os
import re
import time
from decimal import Decimal

import requests as http_requests
from flask import Blueprint, request, jsonify, render_template, session, Response

from auth import login_required

chatbot_bp = Blueprint("chatbot", __name__)

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

_SQL_ALLOWED_TABLES = {"beneficiaries", "benefits", "payments", "programs",
                       "incompatibility_rules", "secretarias"}


# ─── Rutas ───────────────────────────────────────────────

@chatbot_bp.route("/dashboard/chatbot")
@login_required
def chatbot_page():
    return render_template("chatbot.html",
                           role=session.get("role"),
                           nombre=session.get("nombre"))


@chatbot_bp.route("/api/chatbot/status")
@login_required
def api_status():
    try:
        resp = http_requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        resp.raise_for_status()
        models = [m["name"] for m in resp.json().get("models", [])]
        available = any(CHATBOT_MODEL.split(":")[0] in m for m in models)
        return jsonify(online=True, model=CHATBOT_MODEL,
                       model_available=available, available_models=models)
    except Exception:
        return jsonify(online=False, model=CHATBOT_MODEL,
                       model_available=False, available_models=[])


@chatbot_bp.route("/api/chatbot/ask", methods=["POST"])
@login_required
def api_ask():
    data = request.get_json()
    user_msg = data.get("message", "").strip()
    if not user_msg:
        return jsonify(error="Mensaje vacio"), 400

    t0 = time.time()
    messages = [{"role": "system", "content": SQL_GEN_PROMPT},
                {"role": "user", "content": user_msg}]

    # Fase 1: Generar SQL
    sql, err = _call_ollama(messages)
    if sql is None and err:
        return jsonify(error=err), 502
    if sql is None:
        return _response("Soy un modelo de IA para contestar preguntas sobre el RUB.", "", "", 0)

    # Fase 2: Ejecutar (con 1 retry)
    result, err = _execute_readonly(sql)
    if err:
        retry_msgs = messages + [
            {"role": "assistant", "content": sql},
            {"role": "user", "content": f"Esa query dio error: {err}\nCorregila. Solo la query SQL corregida."}
        ]
        sql2, _ = _call_ollama(retry_msgs)
        if sql2:
            result2, err2 = _execute_readonly(sql2)
            if not err2:
                sql, result, err = sql2, result2, err2

    # Fase 3: Formatear
    if err:
        answer = f"Error al consultar los datos: {err}"
    elif not result or not result["rows"]:
        answer = "No se encontraron datos para esa consulta."
    else:
        answer = _format_result(result)

    return _response(answer, sql or "", err or "",
                     result["row_count"] if result else 0)


# ─── Helpers ─────────────────────────────────────────────

def _call_ollama(messages):
    """Llama a Ollama para generar SQL. Retorna (sql, error)."""
    payload = {"model": CHATBOT_MODEL, "messages": messages, "stream": False,
               "options": {"num_predict": 300, "temperature": 0}}
    try:
        resp = http_requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=120)
        resp.raise_for_status()
        raw = resp.json()["message"]["content"].strip()

        # Limpiar backticks/markdown
        sql = raw.strip().strip("`").strip()
        if sql.startswith("```"):
            sql = sql.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        if sql.upper().startswith("NO_SQL") or not sql.upper().lstrip().startswith("SELECT"):
            return None, None
        return sql, None

    except http_requests.ConnectionError:
        return None, "No se pudo conectar con Ollama."
    except http_requests.Timeout:
        return None, "Ollama tardo demasiado (timeout 120s)."
    except Exception as e:
        return None, f"Error con Ollama: {e}"


def _execute_readonly(sql):
    """Ejecuta SQL read-only con allowlist de tablas."""
    referenced = set(re.findall(r'(?:FROM|JOIN)\s+(\w+)', sql, re.IGNORECASE))
    forbidden = referenced - _SQL_ALLOWED_TABLES
    if forbidden:
        return None, f"Tablas no permitidas: {', '.join(forbidden)}"

    if not re.search(r'\bLIMIT\b', sql, re.IGNORECASE):
        sql = sql.rstrip(";").strip() + " LIMIT 100"

    try:
        from flask import g
        from config import get_connection
        if "db" not in g:
            g.db = get_connection()
        conn = g.db
        cur = conn.cursor()
        cur.execute("SET TRANSACTION READ ONLY")
        cur.execute(sql)
        columns = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(100)
        conn.commit()
        return {"columns": columns,
                "rows": [dict(zip(columns, r)) for r in rows],
                "row_count": len(rows)}, None
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        return None, str(e)


def _format_result(result):
    """Formatea resultado SQL como markdown."""
    columns, rows = result["columns"], result["rows"]

    # Maskear CUIL (PII)
    for col in [c for c in columns if c.lower() in ("cuil", "cuil_raw")]:
        for row in rows:
            if row.get(col):
                s = str(row[col])
                row[col] = "*" * (len(s) - 4) + s[-4:] if len(s) >= 4 else s

    # 1 fila, 1 columna
    if len(rows) == 1 and len(columns) == 1:
        return f"**{_fmt(rows[0][columns[0]])}**"

    # 1 fila, pocas columnas
    if len(rows) == 1 and len(columns) <= 4:
        return " | ".join(f"**{c}**: {_fmt(rows[0][c])}" for c in columns)

    # Tabla markdown
    lines = ["| " + " | ".join(columns) + " |",
             "| " + " | ".join("---" for _ in columns) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(_fmt(row[c]) for c in columns) + " |")
    return "\n".join(lines)


def _fmt(val):
    if val is None:
        return "-"
    if isinstance(val, (int, float, Decimal)):
        if isinstance(val, float) and val == int(val):
            val = int(val)
        if isinstance(val, int) and abs(val) >= 1000:
            return f"{val:,.0f}".replace(",", ".")
        if isinstance(val, (float, Decimal)) and abs(val) >= 1000:
            return f"{val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return str(val)


def _response(answer, sql_query, sql_error, row_count):
    """NDJSON stream compatible con frontend."""
    def gen():
        yield json_mod.dumps({"type": "meta", "sql": sql_query,
                               "sql_error": sql_error, "row_count": row_count}) + "\n"
        yield json_mod.dumps({"type": "token", "content": answer, "done": False}) + "\n"
        yield json_mod.dumps({"type": "token", "content": "", "done": True}) + "\n"
    return Response(gen(), mimetype="application/x-ndjson")


# ─── Health ──────────────────────────────────────────────

def check_ollama():
    """Para health endpoint en app.py."""
    try:
        r = http_requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        models = [m["name"] for m in r.json().get("models", [])]
        return {"ollama": "ok", "models": models, "target_model": CHATBOT_MODEL,
                "model_loaded": any(CHATBOT_MODEL in m for m in models),
                "status": "ok" if any(CHATBOT_MODEL in m for m in models) else "degraded"}
    except Exception:
        return {"ollama": "unreachable", "model_loaded": False, "status": "degraded"}
