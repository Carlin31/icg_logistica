"""
router/menu_router.py
Blueprint Flask para el Menú Principal — Gestión de Logísticas.

Renombrado desde logistica_router.py para unificar la nomenclatura bajo
el prefijo "menu_". El blueprint se registra en app.py con url_prefix="/".

Endpoints:
  GET    /                        → Menú principal
  GET    /api/listar              → Lista todas las logísticas
  POST   /api/crear               → Crea una nueva logística
  POST   /api/activar/<id>        → Activa logística → guarda en sesión
  GET    /api/activa              → Info de la logística activa en sesión
  DELETE /api/eliminar/<id>       → Elimina una logística y todos sus datos
  POST   /api/completar/<id>      → Marca logística como completada
  POST   /salir                   → Desactiva logística (limpia sesión)
"""
from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
from logic.menu_logic import (
    listar_logisticas,
    obtener_logistica,
    crear_logistica,
    eliminar_logistica,
    activar_logistica,
    marcar_completada,
)

menu_bp = Blueprint("menu", __name__)


# ── Helpers ───────────────────────────────────────────────────────

def _logistica_activa_id() -> "str | None":
    return session.get("logistica_id")


def _set_logistica_sesion(info: dict):
    session["logistica_id"]     = info["id"]
    session["logistica_nombre"] = info["nombre"]
    session["logistica_slug"]   = info.get("slug", "")
    session["logistica_inicio"] = info["fecha_inicio"]
    session["logistica_fin"]    = info["fecha_fin"]


def _clear_logistica_sesion():
    for key in ("logistica_id", "logistica_nombre", "logistica_slug",
                "logistica_inicio", "logistica_fin"):
        session.pop(key, None)


# ── Vistas ────────────────────────────────────────────────────────

@menu_bp.route("/", methods=["GET"])
def index():
    return render_template("menu/index.html")


# ── API JSON ──────────────────────────────────────────────────────

@menu_bp.route("/api/listar", methods=["GET"])
def api_listar():
    try:
        return jsonify({"status": "ok", "logisticas": listar_logisticas()})
    except Exception as e:
        return jsonify({"status": "error", "mensaje": str(e)}), 500


@menu_bp.route("/api/crear", methods=["POST"])
def api_crear():
    datos = request.get_json(silent=True) or {}
    fecha_inicio = datos.get("fecha_inicio", "").strip()
    fecha_fin    = datos.get("fecha_fin", "").strip()

    if not fecha_inicio or not fecha_fin:
        return jsonify({"status": "error", "mensaje": "Se requieren fecha_inicio y fecha_fin."}), 400

    resultado = crear_logistica(fecha_inicio, fecha_fin)
    code = 201 if resultado.get("status") == "ok" else 400
    return jsonify(resultado), code


@menu_bp.route("/api/activar/<logistica_id>", methods=["POST"])
def api_activar(logistica_id: str):
    """Activa la logística: valida que exista y guarda el ID en sesión."""
    resultado = activar_logistica(logistica_id)
    if resultado.get("status") != "ok":
        return jsonify(resultado), 404

    _set_logistica_sesion(resultado)
    return jsonify(resultado)


@menu_bp.route("/api/activa", methods=["GET"])
def api_activa():
    lid = _logistica_activa_id()
    if not lid:
        return jsonify({"status": "sin_logistica"})
    return jsonify({
        "status":  "ok",
        "id":      lid,
        "nombre":  session.get("logistica_nombre"),
        "slug":    session.get("logistica_slug", ""),
        "inicio":  session.get("logistica_inicio"),
        "fin":     session.get("logistica_fin"),
    })


@menu_bp.route("/api/eliminar/<logistica_id>", methods=["DELETE"])
def api_eliminar(logistica_id: str):
    if _logistica_activa_id() == logistica_id:
        _clear_logistica_sesion()
    resultado = eliminar_logistica(logistica_id)
    code = 200 if resultado.get("status") == "ok" else 404
    return jsonify(resultado), code


@menu_bp.route("/api/completar/<logistica_id>", methods=["POST"])
def api_completar(logistica_id: str):
    resultado = marcar_completada(logistica_id)
    code = 200 if resultado.get("status") == "ok" else 404
    return jsonify(resultado), code


@menu_bp.route("/salir", methods=["POST"])
def salir():
    _clear_logistica_sesion()
    return redirect(url_for("menu.index"))