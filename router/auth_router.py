"""
router/auth_router.py
Blueprint Flask para autenticación de usuarios.

Endpoints:
  GET       /auth/seleccionar → Elegir tipo de acceso (Logística / Conductor)
  GET/POST  /auth/login       → Formulario e inicio de sesión (?tipo=logistica|conductor)
  POST/GET  /auth/logout      → Cierre de sesión
"""
from flask import Blueprint, render_template, request, redirect, url_for, session, jsonify

from logic.auth_logic import autenticar, obtener_zoom, guardar_zoom, ROL_LOGISTICA, ROL_CONDUCTOR

auth_bp = Blueprint("auth", __name__)


def _destino_segun_rol(rol: str) -> str:
    return url_for("conductor.index") if rol == ROL_CONDUCTOR else url_for("menu.index")


@auth_bp.route("/seleccionar", methods=["GET"])
def seleccionar():
    # Esta página es precisamente para elegir/cambiar el tipo de acceso, así
    # que siempre se muestra — aunque ya haya una sesión activa de otro tipo.
    # No redirigir aquí evita el bug de "entro como conductor y me manda
    # directo a mi sesión de Logística que seguía abierta en el navegador".
    return render_template("auth/seleccionar.html")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    tipo = request.args.get("tipo") or request.form.get("tipo") or ROL_LOGISTICA
    if tipo not in (ROL_LOGISTICA, ROL_CONDUCTOR):
        tipo = ROL_LOGISTICA

    if request.method == "GET":
        # Solo se omite el formulario si la sesión activa YA es del mismo
        # tipo solicitado. Si se pide explícitamente el otro tipo (?tipo=...),
        # siempre se muestra el login para ese tipo, en vez de redirigir a la
        # sesión que ya estaba abierta — cada tipo de acceso debe iniciarse
        # con sus propias credenciales, nunca heredar la sesión del otro.
        if session.get("usuario_id") and session.get("rol") == tipo:
            return redirect(_destino_segun_rol(tipo))
        return render_template("auth/login.html", tipo=tipo)

    datos    = request.get_json(silent=True) if request.is_json else request.form
    datos    = datos or {}
    username = (datos.get("username") or "").strip()
    password = datos.get("password") or ""

    resultado = autenticar(username, password)
    if resultado["status"] == "ok" and resultado["usuario"]["rol"] != tipo:
        # Evita que una credencial de Logística entre como Conductor (o viceversa).
        resultado = {"status": "error", "mensaje": "Usuario o contraseña incorrectos"}

    if resultado["status"] != "ok":
        if request.is_json:
            return jsonify(resultado), 401
        return render_template("auth/login.html", error=resultado["mensaje"], username=username, tipo=tipo), 401

    usuario = resultado["usuario"]
    session.clear()
    session.permanent        = True
    session["usuario_id"]     = usuario["id"]
    session["usuario_nombre"] = usuario["nombre"]
    session["usuario_admin"]  = usuario["es_admin"]
    session["rol"]            = usuario["rol"]
    session["chofer_id"]      = usuario["chofer_id"]

    destino = _destino_segun_rol(usuario["rol"])
    if request.is_json:
        return jsonify({"status": "ok", "redirect": destino})
    siguiente = request.args.get("next") or destino
    return redirect(siguiente)


@auth_bp.route("/logout", methods=["GET", "POST"])
def logout():
    session.clear()
    return redirect(url_for("auth.seleccionar"))


@auth_bp.route("/zoom", methods=["GET"])
def get_zoom():
    usuario_id = session.get("usuario_id")
    if not usuario_id:
        return jsonify({"zoom": 100})
    return jsonify({"zoom": obtener_zoom(usuario_id)})


@auth_bp.route("/zoom", methods=["POST"])
def post_zoom():
    usuario_id = session.get("usuario_id")
    if not usuario_id:
        return jsonify({"status": "error", "mensaje": "Sesión no iniciada"}), 401
    datos = request.get_json(silent=True) or {}
    resultado = guardar_zoom(usuario_id, datos.get("zoom"))
    code = 200 if resultado.get("status") == "ok" else 400
    return jsonify(resultado), code
