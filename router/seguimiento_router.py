"""
router/seguimiento_router.py
Blueprint Flask para la Sección — Seguimiento de entregas en tiempo real.

Solo tiene sentido (y solo está habilitada en el menú) una vez que la
logística activa fue autorizada desde la sección PDF.

Endpoints:
  GET /              → Vista de la sección (seguimiento/index.html)
  GET /<slug>        → Activa el perfil por slug y carga la sección
  GET /datos         → JSON con el avance de cada ruta de la logística activa
  GET /detalle/<id>  → JSON con el historial de paradas de una ruta
  GET /auditoria     → JSON con el log de auditoría de la logística activa
"""
from flask import Blueprint, render_template, jsonify, session, redirect, url_for
from logic.conductor_logic import obtener_seguimiento_logistica, obtener_historial_entregas_ruta
from logic.auditoria_logic import listar_auditoria
from bson import ObjectId
from bson.errors import InvalidId

seguimiento_bp = Blueprint("seguimiento", __name__)


def _logistica_id() -> str | None:
    return session.get("logistica_id")


@seguimiento_bp.route("/", methods=["GET"])
def index():
    return render_template("seguimiento/index.html")


@seguimiento_bp.route("/<slug>", methods=["GET"])
def index_perfil(slug):
    """Ruta directa por perfil: /seguimiento/<slug>. Activa el perfil y carga la sección."""
    from logic.menu_logic import obtener_logistica_por_slug, _slugify
    logistica = obtener_logistica_por_slug(slug)
    if not logistica:
        return redirect(url_for("menu.index"))
    slug_calc = logistica.get("slug") or _slugify(logistica["nombre"])
    session["logistica_id"]     = logistica["_id"]
    session["logistica_nombre"] = logistica["nombre"]
    session["logistica_slug"]   = slug_calc
    session["logistica_inicio"] = logistica.get("fecha_inicio", "")
    session["logistica_fin"]    = logistica.get("fecha_fin", "")
    return render_template("seguimiento/index.html")


@seguimiento_bp.route("/datos", methods=["GET"])
def get_datos():
    lid = _logistica_id()
    if not lid:
        return jsonify({"status": "error", "mensaje": "No hay ninguna logística activa."}), 400
    try:
        return jsonify(obtener_seguimiento_logistica(lid))
    except Exception as e:
        return jsonify({"status": "error", "mensaje": str(e)}), 500


@seguimiento_bp.route("/detalle/<ruta_id>", methods=["GET"])
def get_detalle_ruta(ruta_id):
    """Historial completo de paradas de una ruta (para el panel de detalle en Seguimiento)."""
    lid = _logistica_id()
    if not lid:
        return jsonify({"status": "error", "mensaje": "No hay ninguna logística activa."}), 400
    try:
        return jsonify(obtener_historial_entregas_ruta(ruta_id))
    except Exception as e:
        return jsonify({"status": "error", "mensaje": str(e)}), 500


@seguimiento_bp.route("/auditoria", methods=["GET"])
def get_auditoria():
    """Log de auditoría de autorizaciones y acciones de entrega de la logística activa."""
    lid = _logistica_id()
    if not lid:
        return jsonify({"status": "error", "mensaje": "No hay ninguna logística activa."}), 400
    try:
        oid = ObjectId(lid)
        eventos = listar_auditoria(oid, limite=300)
        return jsonify({"status": "ok", "eventos": eventos})
    except (InvalidId, Exception) as e:
        return jsonify({"status": "error", "mensaje": str(e)}), 500
