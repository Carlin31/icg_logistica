"""
router/usuarios_router.py
Blueprint Flask para la Gestión de Usuarios — exclusivo para administradores.
El control de acceso real (solo admin) se aplica en app.py (_requerir_login),
igual que la separación de roles Logística/Conductor.
"""
from flask import Blueprint, render_template, request, jsonify, session

from logic.auth_logic import (
    listar_usuarios,
    obtener_usuario,
    crear_usuario_admin,
    editar_usuario,
    eliminar_usuario,
)

usuarios_bp = Blueprint("usuarios", __name__)


def _json_o_400():
    datos = request.get_json(silent=True)
    if datos is None:
        return None, (jsonify({"status": "error", "mensaje": "Cuerpo JSON inválido"}), 400)
    return datos, None


def _respuesta(resultado: dict):
    code = 200 if resultado.get("status") == "ok" else 400
    return jsonify(resultado), code


@usuarios_bp.route("/", methods=["GET"])
def index():
    return render_template("usuarios/index.html")


@usuarios_bp.route("/api/usuarios", methods=["GET"])
def get_usuarios():
    return jsonify(listar_usuarios())


@usuarios_bp.route("/api/usuarios/<usuario_id>", methods=["GET"])
def get_usuario(usuario_id):
    doc = obtener_usuario(usuario_id)
    return jsonify(doc) if doc else (jsonify({"error": "No encontrado"}), 404)


@usuarios_bp.route("/api/usuarios", methods=["POST"])
def post_usuario():
    datos, err = _json_o_400()
    if err:
        return err
    resultado = crear_usuario_admin(
        datos.get("username", ""),
        datos.get("password", ""),
        datos.get("nombre", ""),
        bool(datos.get("es_admin", False)),
    )
    code = 201 if resultado.get("status") == "ok" else 400
    return jsonify(resultado), code


@usuarios_bp.route("/api/usuarios/<usuario_id>", methods=["PUT"])
def put_usuario(usuario_id):
    datos, err = _json_o_400()
    if err:
        return err
    return _respuesta(editar_usuario(usuario_id, datos, session.get("usuario_id")))


@usuarios_bp.route("/api/usuarios/<usuario_id>", methods=["DELETE"])
def delete_usuario(usuario_id):
    return _respuesta(eliminar_usuario(usuario_id, session.get("usuario_id")))
