"""
router/auth_router.py
Blueprint Flask para autenticación de usuarios.

Endpoints:
  GET/POST  /auth/login   → Formulario e inicio de sesión
  POST/GET  /auth/logout  → Cierre de sesión
"""
from flask import Blueprint, render_template, request, redirect, url_for, session, jsonify

from logic.auth_logic import autenticar

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        if session.get("usuario_id"):
            return redirect(url_for("menu.index"))
        return render_template("auth/login.html")

    datos    = request.get_json(silent=True) if request.is_json else request.form
    datos    = datos or {}
    username = (datos.get("username") or "").strip()
    password = datos.get("password") or ""

    resultado = autenticar(username, password)
    if resultado["status"] != "ok":
        if request.is_json:
            return jsonify(resultado), 401
        return render_template("auth/login.html", error=resultado["mensaje"], username=username), 401

    usuario = resultado["usuario"]
    session.clear()
    session.permanent       = True
    session["usuario_id"]     = usuario["id"]
    session["usuario_nombre"] = usuario["nombre"]
    session["usuario_admin"]  = usuario["es_admin"]

    if request.is_json:
        return jsonify({"status": "ok"})
    siguiente = request.args.get("next") or url_for("menu.index")
    return redirect(siguiente)


@auth_bp.route("/logout", methods=["GET", "POST"])
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
