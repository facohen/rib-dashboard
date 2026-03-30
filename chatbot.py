"""
chatbot.py — Chatbot con IA (Ollama) para consultas SQL al RIB.

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

SQL_GEN_PROMPT = """Genera SELECT PostgreSQL. Solo SQL, sin explicacion, sin backticks. LIMIT 100. Si no aplica: NO_SQL
Nunca devolver cuil, nombre o apellido. Si piden datos de una persona especifica: NO_SQL

BASE: RIB (Registro Unico de Beneficiarios) — programas sociales argentinos.
-- Persona: ser humano, 1 fila en beneficiaries. Tiene sexo ('F','M','X'), fecha_nacimiento DATE, provincia.
-- Beneficiario: una persona dentro de un programa. Maria en 3 programas = 1 persona, 3 beneficiarios.
-- Beneficio/prestacion: cada fila de benefits (persona+programa+mes). Contar beneficios = COUNT(*).
-- Contar personas unicas = COUNT(DISTINCT beneficiary_id). NO es lo mismo que contar beneficios.
-- Monto: plata que cobra. SUM(monto_prestacion) o SUM(montos). No confundir con cantidad de personas.

TABLAS MATERIALIZADAS (preferir siempre, son rapidas):
mv_resumen(periodo_mes, provincia, sexo, grupo_etario, cant_prestaciones, personas BIGINT, beneficios BIGINT, montos NUMERIC)
-- Personas unicas. SUM(personas) = total personas, SUM(beneficios) = total beneficios, SUM(montos) = total plata.
-- grupo_etario: '0-4 años','5-12 años','13-17 años','18-29 años','30-59 años','60+ años'
-- cant_prestaciones: '1','2','3+' (cuantos programas tiene la persona)
-- NO tiene programa ni secretaria. Usar para totales generales.

mv_cross(periodo_mes, nombre_programa, secretaria_origen, provincia, sexo, grupo_etario, cant_prestaciones, personas, beneficios, montos)
-- Mismas metricas pero desglosada por programa y secretaria. Usar cuando pregunten por programa.

TABLAS RAW (usar solo si las MVs no alcanzan, ej: edad exacta):
beneficiaries(id, cuil, nombre, apellido, sexo CHAR(1), fecha_nacimiento DATE, provincia)
-- edad exacta: EXTRACT(YEAR FROM AGE(fecha_nacimiento))
programs(id, secretaria_id, nombre_programa)
secretarias(id, nombre)
benefits(id, beneficiary_id, program_id, periodo_mes TEXT, estado_beneficio TEXT)
payments(id, beneficiary_id, program_id, periodo_mes, monto_prestacion NUMERIC)
incompatibility_rules(id, program_a_id, program_b_id, is_compatible BOOLEAN, descripcion)

DEFAULTS salvo que pidan otra cosa: periodo_mes='2026-03'. En tablas raw: estado_beneficio='ACTIVO'.

EJ:
total_personas: SELECT SUM(personas) FROM mv_resumen WHERE periodo_mes='2026-03'
total_montos: SELECT SUM(montos) FROM mv_resumen WHERE periodo_mes='2026-03'
x_prog: SELECT nombre_programa,SUM(personas) n FROM mv_cross WHERE periodo_mes='2026-03' GROUP BY 1 ORDER BY n DESC LIMIT 100
x_prov: SELECT provincia,SUM(personas) n FROM mv_resumen WHERE periodo_mes='2026-03' GROUP BY 1 ORDER BY n DESC LIMIT 100
x_sexo: SELECT sexo,SUM(personas) n FROM mv_resumen WHERE periodo_mes='2026-03' GROUP BY 1 ORDER BY n DESC
x_edad: SELECT grupo_etario,SUM(personas) n FROM mv_resumen WHERE periodo_mes='2026-03' GROUP BY 1 ORDER BY n DESC
montos_prog: SELECT nombre_programa,SUM(montos) total FROM mv_cross WHERE periodo_mes='2026-03' GROUP BY 1 ORDER BY total DESC LIMIT 100
prov_sexo: SELECT provincia,SUM(CASE WHEN sexo='F' THEN personas ELSE 0 END) femenino,SUM(CASE WHEN sexo='M' THEN personas ELSE 0 END) masculino,SUM(CASE WHEN sexo='X' THEN personas ELSE 0 END) no_binario,SUM(personas) total FROM mv_resumen WHERE periodo_mes='2026-03' GROUP BY 1 ORDER BY total DESC LIMIT 100
mayores60: SELECT SUM(personas) FROM mv_resumen WHERE periodo_mes='2026-03' AND grupo_etario='60+ años'
menores5: SELECT SUM(personas) FROM mv_resumen WHERE periodo_mes='2026-03' AND grupo_etario='0-4 años'
edad>100: SELECT COUNT(DISTINCT b.beneficiary_id) FROM benefits b JOIN beneficiaries ben ON ben.id=b.beneficiary_id WHERE b.estado_beneficio='ACTIVO' AND b.periodo_mes='2026-03' AND EXTRACT(YEAR FROM AGE(ben.fecha_nacimiento))>100
edad<2: SELECT COUNT(DISTINCT b.beneficiary_id) FROM benefits b JOIN beneficiaries ben ON ben.id=b.beneficiary_id WHERE b.estado_beneficio='ACTIVO' AND b.periodo_mes='2026-03' AND EXTRACT(YEAR FROM AGE(ben.fecha_nacimiento))<2
multi: SELECT SUM(personas) FROM mv_resumen WHERE periodo_mes='2026-03' AND cant_prestaciones IN ('2','3+')
concentracion: SELECT cant_prestaciones,SUM(personas) n FROM mv_resumen WHERE periodo_mes='2026-03' GROUP BY 1 ORDER BY 1
benef_prov: SELECT provincia,SUM(beneficios) n FROM mv_resumen WHERE periodo_mes='2026-03' GROUP BY 1 ORDER BY n DESC LIMIT 100
top5_benef_prov: SELECT provincia,SUM(beneficios) n FROM mv_resumen WHERE periodo_mes='2026-03' GROUP BY 1 ORDER BY n DESC LIMIT 5
x_secretaria: SELECT secretaria_origen,SUM(personas) n FROM mv_cross WHERE periodo_mes='2026-03' GROUP BY 1 ORDER BY n DESC
benef_secretaria: SELECT secretaria_origen,SUM(beneficios) n FROM mv_cross WHERE periodo_mes='2026-03' GROUP BY 1 ORDER BY n DESC
otro_mes: SELECT nombre_programa,SUM(personas) n FROM mv_cross WHERE periodo_mes='2025-06' GROUP BY 1 ORDER BY n DESC LIMIT 100
montos_mes: SELECT provincia,SUM(montos) total FROM mv_resumen WHERE periodo_mes='2025-12' GROUP BY 1 ORDER BY total DESC LIMIT 100
"""

_SQL_ALLOWED_TABLES = {"beneficiaries", "benefits", "payments", "programs",
                       "incompatibility_rules", "secretarias",
                       "mv_cross", "mv_resumen"}

_NOMINAL_COLUMNS = re.compile(
    r'\b(cuil|nombre|apellido)\b', re.IGNORECASE
)


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
        return _response("Soy un modelo de IA para contestar preguntas sobre el RIB.", "", "", 0)

    # Fase 2: Ejecutar (con 1 retry)
    result, err = _execute_readonly(sql)
    if err == "NOMINAL":
        return _response("No puedo responder con datos nominales (nombres, apellidos, CUIL). "
                         "Puedo ayudarte con totales, promedios y distribuciones.", sql, "", 0)
    if err:
        retry_msgs = messages + [
            {"role": "assistant", "content": sql},
            {"role": "user", "content": f"Esa query dio error: {err}\nCorregila. Solo la query SQL corregida."}
        ]
        sql2, _ = _call_ollama(retry_msgs)
        if sql2:
            result2, err2 = _execute_readonly(sql2)
            if err2 == "NOMINAL":
                return _response("No puedo responder con datos nominales (nombres, apellidos, CUIL). "
                                 "Puedo ayudarte con totales, promedios y distribuciones.", sql2, "", 0)
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
    """Ejecuta SQL read-only. Solo SELECT permitido."""
    # Hard block: solo un SELECT, sin multi-statement
    stripped = sql.strip().rstrip(";").strip()
    if ";" in stripped:
        return None, "Solo se permite una consulta."
    if not stripped.upper().startswith("SELECT"):
        return None, "Solo se permiten consultas SELECT."
    if re.search(r'\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|COPY|EXECUTE|SET|COMMIT|ROLLBACK|BEGIN)\b',
                 stripped, re.IGNORECASE):
        return None, "Solo se permiten consultas SELECT."

    # Bloquear SELECT que devuelva datos nominales (PII)
    select_part = re.split(r'\bFROM\b', sql, maxsplit=1, flags=re.IGNORECASE)[0]
    if _NOMINAL_COLUMNS.search(select_part):
        return None, "NOMINAL"

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
    """Formato AR estilo Power BI: <1M completo, >=1M escala M/MM."""
    if val is None:
        return "-"
    if not isinstance(val, (int, float, Decimal)):
        return str(val)
    n = float(val)
    abs_n = abs(n)
    if abs_n >= 1e9:
        return f"{n/1e9:,.2f}MM".replace(",", "X").replace(".", ",").replace("X", ".")
    if abs_n >= 1e6:
        return f"{n/1e6:,.2f}M".replace(",", "X").replace(".", ",").replace("X", ".")
    # < 1M: número completo con separador de miles
    if isinstance(val, (float, Decimal)) and n != int(n):
        return f"{n:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{int(n):,}".replace(",", ".")


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
