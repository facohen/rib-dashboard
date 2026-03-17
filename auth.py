"""
auth.py — Autenticacion y autorizacion RUB Dashboard

Contiene: login/logout, decoradores de acceso, rate limiting, hash de passwords.
Se registra como Blueprint en app.py.
"""
import hashlib
import time
from collections import defaultdict
from functools import wraps

from flask import Blueprint, request, redirect, url_for, session, jsonify, flash, render_template, g
from werkzeug.security import generate_password_hash, check_password_hash

from config import get_connection
import queries

auth_bp = Blueprint("auth", __name__)


# ─── Password hashing ───────────────────────────────────

def hash_password(pw):
    return generate_password_hash(pw)


def _is_legacy_sha256(h):
    """Detecta hash SHA256 legacy (64 hex chars sin separador pbkdf2)."""
    return len(h) == 64 and all(c in "0123456789abcdef" for c in h)


# ─── Decoradores de acceso ───────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            if request.path.startswith("/api/"):
                return jsonify(error="No autorizado"), 401
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            if request.path.startswith("/api/"):
                return jsonify(error="No autorizado"), 401
            return redirect(url_for("auth.login"))
        if session.get("role") != "admin":
            if request.path.startswith("/api/"):
                return jsonify(error="Acceso denegado: se requiere rol admin"), 403
            flash("Acceso denegado.", "error")
            return redirect(url_for("dashboard"))
        return f(*args, **kwargs)
    return decorated


# ─── Rate limiting (in-memory) ───────────────────────────

_login_attempts = defaultdict(list)


def _check_rate_limit(ip, max_attempts=5, window=300):
    now = time.time()
    _login_attempts[ip] = [t for t in _login_attempts[ip] if now - t < window]
    if len(_login_attempts[ip]) >= max_attempts:
        return False
    _login_attempts[ip].append(now)
    return True


# ─── Rutas ───────────────────────────────────────────────

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    error = None
    if request.method == "POST":
        if not _check_rate_limit(request.remote_addr):
            error = "Demasiados intentos. Espera 5 minutos."
            return render_template("login.html", error=error)

        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        if "db" not in g:
            g.db = get_connection()
        conn = g.db

        user = queries.get_user_by_email(conn, email)

        password_ok = False
        if user:
            stored = user["password_hash"]
            if _is_legacy_sha256(stored):
                password_ok = (stored == hashlib.sha256(password.encode()).hexdigest())
                if password_ok:
                    # Migracion transparente a werkzeug hash
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
            session.clear()
            session.permanent = True
            session["user_id"] = user["id"]
            session["email"] = user["email"]
            session["role"] = user["role"]
            session["nombre"] = user["nombre"] or user["email"]
            return redirect(url_for("dashboard"))
        error = "Email o contrasena incorrectos."
    return render_template("login.html", error=error)


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
