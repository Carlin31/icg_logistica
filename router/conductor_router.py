"""
router/conductor_router.py
Blueprint Flask para el portal del Conductor.
Usa session["chofer_id"] y session["usuario_nombre"] (seteados en el login).

Rutas de página (sirven el shell HTML — móvil/tablet o escritorio según
dispositivo — con el estado inicial embebido para restaurar dónde se quedó
el conductor). La autorización real NUNCA se valida aquí: ocurre siempre en
las rutas /api/* (vía conductor_logic), así que un slug/día/ruta inventado
o manipulado a mano en la URL simplemente no devuelve datos.
  GET /                                  → lista general
  GET /<logistica_slug>                  → logística enfocada
  GET /<logistica_slug>/<dia>            → + día expandido
  GET /<logistica_slug>/<dia>/<ruta_id>  → + ruta abierta

Rutas API (JSON):
  GET  /api/rutas
  GET  /api/ruta/<ruta_id>
  POST /api/marcar-entrega
"""
import re
from flask import Blueprint, render_template, request, jsonify, session

from logic.conductor_logic import (
    obtener_rutas_chofer,
    obtener_detalle_ruta_chofer,
    marcar_entrega,
)

conductor_bp = Blueprint("conductor", __name__)

_MOBIL_UA_RE = re.compile(r"Mobi|Android|iPhone|iPod|iPad|Tablet|BlackBerry|Windows Phone|Opera Mini|IEMobile", re.I)


def _es_movil(user_agent: str) -> bool:
    return bool(_MOBIL_UA_RE.search(user_agent or ""))


def _vista_actual() -> str:
    """'movil' o 'escritorio', según ?vista=, la preferencia guardada en sesión, o el User-Agent."""
    vista_qs = request.args.get("vista")
    if vista_qs in ("movil", "escritorio"):
        session["conductor_vista"] = vista_qs
        return vista_qs
    vista_sesion = session.get("conductor_vista")
    if vista_sesion in ("movil", "escritorio"):
        return vista_sesion
    return "movil" if _es_movil(request.headers.get("User-Agent", "")) else "escritorio"


def _json_o_400():
    datos = request.get_json(silent=True)
    if datos is None:
        return None, (jsonify({"status": "error", "mensaje": "Cuerpo JSON inválido"}), 400)
    return datos, None


def _requiere_chofer():
    chofer_id = session.get("chofer_id")
    if not chofer_id:
        return None, (jsonify({"status": "error", "mensaje": "Sesión de conductor inválida"}), 400)
    return chofer_id, None


def _render_shell(logistica_slug=None, dia=None, ruta_id=None):
    template = "conductor/index.html" if _vista_actual() == "movil" else "conductor/desktop.html"
    estado_inicial = {"logistica_slug": logistica_slug, "dia": dia, "ruta_id": ruta_id}
    return render_template(template, estado_inicial=estado_inicial)


@conductor_bp.route("/", defaults={"logistica_slug": None, "dia": None, "ruta_id": None}, methods=["GET"])
@conductor_bp.route("/<logistica_slug>", defaults={"dia": None, "ruta_id": None}, methods=["GET"])
@conductor_bp.route("/<logistica_slug>/<dia>", defaults={"ruta_id": None}, methods=["GET"])
@conductor_bp.route("/<logistica_slug>/<dia>/<ruta_id>", methods=["GET"])
def index(logistica_slug, dia, ruta_id):
    return _render_shell(logistica_slug, dia, ruta_id)


@conductor_bp.route("/api/rutas", methods=["GET"])
def get_rutas():
    chofer_id, err = _requiere_chofer()
    if err:
        return err
    try:
        return jsonify(obtener_rutas_chofer(chofer_id, session.get("usuario_nombre", "")))
    except Exception as e:
        return jsonify({"status": "error", "mensaje": str(e)}), 500


@conductor_bp.route("/api/ruta/<ruta_id>", methods=["GET"])
def get_ruta(ruta_id):
    chofer_id, err = _requiere_chofer()
    if err:
        return err
    try:
        resultado = obtener_detalle_ruta_chofer(chofer_id, session.get("usuario_nombre", ""), ruta_id)
        code = 200 if resultado.get("status") == "ok" else 403
        return jsonify(resultado), code
    except Exception as e:
        return jsonify({"status": "error", "mensaje": str(e)}), 500


@conductor_bp.route("/api/marcar-entrega", methods=["POST"])
def post_marcar_entrega():
    chofer_id, err = _requiere_chofer()
    if err:
        return err
    datos, err2 = _json_o_400()
    if err2:
        return err2
    try:
        resultado = marcar_entrega(
            session.get("usuario_id"),
            chofer_id,
            session.get("usuario_nombre", ""),
            datos.get("ruta_id"),
            datos.get("parada_tipo"),
            datos.get("parada_key"),
            datos.get("estado", "entregado"),
            datos.get("observacion", ""),
        )
        code = 200 if resultado.get("status") == "ok" else 403
        return jsonify(resultado), code
    except Exception as e:
        return jsonify({"status": "error", "mensaje": str(e)}), 500
