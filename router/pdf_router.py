"""
router/pdf_router.py
Blueprint Flask para la generación de reportes PDF y la autorización de
rutas hacia el portal del Conductor.

Endpoints:
  GET  /                      → Vista de la sección (pdf/index.html)
  POST /generar                → Genera y descarga el reporte PDF de la logística activa
  GET  /estado-autorizacion    → Estado de autorización de la logística activa
  POST /autorizar               → Autoriza TODAS las rutas de la logística activa
  GET  /entregas-resumen        → # de entregas registradas (para advertir antes de cancelar)
  POST /cancelar-autorizacion   → Retira la autorización (borra entregas asociadas)
"""
from flask import Blueprint, render_template, send_file, jsonify, session, redirect, url_for
from logic.pdf_logic import generar_pdf
from logic.conductor_logic import (
    obtener_estado_autorizacion,
    autorizar_rutas,
    cancelar_autorizacion,
    contar_entregas_logistica,
)

pdf_bp = Blueprint('pdf', __name__)


def _logistica_activa() -> dict | None:
    """Devuelve los datos de la logística activa desde la sesión, o None."""
    lid = session.get("logistica_id")
    if not lid:
        return None
    return {
        "id":           lid,
        "nombre":       session.get("logistica_nombre", "Logística"),
        "fecha_inicio": session.get("logistica_inicio", ""),
        "fecha_fin":    session.get("logistica_fin", ""),
    }


@pdf_bp.route('/', methods=['GET'])
def index():
    return render_template('pdf/index.html')


@pdf_bp.route('/<slug>', methods=['GET'])
def index_perfil(slug):
    """Ruta directa por perfil: /pdf/<slug>. Activa el perfil y carga la sección."""
    from logic.menu_logic import obtener_logistica_por_slug, _slugify
    logistica = obtener_logistica_por_slug(slug)
    if not logistica:
        return redirect(url_for('menu.index'))
    slug_calc = logistica.get('slug') or _slugify(logistica['nombre'])
    session['logistica_id']     = logistica['_id']
    session['logistica_nombre'] = logistica['nombre']
    session['logistica_slug']   = slug_calc
    session['logistica_inicio'] = logistica.get('fecha_inicio', '')
    session['logistica_fin']    = logistica.get('fecha_fin', '')
    return render_template('pdf/index.html')


@pdf_bp.route('/generar', methods=['POST'])
def generar():
    """
    Genera el reporte PDF de pesos. Generar el PDF NO autoriza las rutas
    automáticamente — eso requiere presionar "Autorizar" por separado.
    """
    logistica = _logistica_activa()
    if not logistica:
        return jsonify({
            "status":  "error",
            "mensaje": "No hay ninguna logística activa. "
                       "Selecciona una desde el menú principal antes de generar el reporte.",
        }), 400

    try:
        ruta_archivo = generar_pdf(logistica)
    except FileNotFoundError as e:
        return jsonify({"status": "error", "mensaje": str(e)}), 404
    except Exception as e:
        return jsonify({"status": "error", "mensaje": f"Error al generar el PDF: {e}"}), 500

    nombre_descarga = f"{logistica['nombre'].replace(' ', '_')}.pdf"
    return send_file(
        ruta_archivo,
        as_attachment=True,
        download_name=nombre_descarga,
        mimetype="application/pdf",
    )


@pdf_bp.route('/estado-autorizacion', methods=['GET'])
def get_estado_autorizacion():
    logistica = _logistica_activa()
    if not logistica:
        return jsonify({"status": "error", "mensaje": "No hay ninguna logística activa."}), 400
    return jsonify(obtener_estado_autorizacion(logistica["id"]))


@pdf_bp.route('/autorizar', methods=['POST'])
def post_autorizar():
    logistica = _logistica_activa()
    if not logistica:
        return jsonify({"status": "error", "mensaje": "No hay ninguna logística activa."}), 400
    resultado = autorizar_rutas(logistica["id"], session.get("usuario_id"), session.get("usuario_nombre", ""))
    code = 200 if resultado.get("status") == "ok" else 400
    return jsonify(resultado), code


@pdf_bp.route('/entregas-resumen', methods=['GET'])
def get_entregas_resumen():
    logistica = _logistica_activa()
    if not logistica:
        return jsonify({"status": "error", "mensaje": "No hay ninguna logística activa."}), 400
    return jsonify({"status": "ok", "entregas": contar_entregas_logistica(logistica["id"])})


@pdf_bp.route('/cancelar-autorizacion', methods=['POST'])
def post_cancelar_autorizacion():
    logistica = _logistica_activa()
    if not logistica:
        return jsonify({"status": "error", "mensaje": "No hay ninguna logística activa."}), 400
    resultado = cancelar_autorizacion(logistica["id"], session.get("usuario_id"), session.get("usuario_nombre", ""))
    code = 200 if resultado.get("status") == "ok" else 400
    return jsonify(resultado), code
