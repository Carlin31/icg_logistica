"""
router/pdf_router.py
Blueprint Flask para la generación de reportes PDF.

Endpoints:
  GET  /           → Vista de la sección (pdf/index.html)
  POST /generar    → Genera y descarga el reporte PDF de pesos de la logística activa
"""
from flask import Blueprint, render_template, request, send_file, jsonify, session, redirect, url_for
from logic.pdf_logic import generar_pdf

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
    Genera el reporte PDF de pesos usando:
      - Datos de la logística activa en sesión (nombre, fechas).
      - data/modificacion_rutas.json para las rutas y pesos.
    Descarga el archivo generado directamente.
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

    nombre_descarga = f"reporte_pesos_{logistica['nombre'].replace(' ', '_')}.pdf"
    return send_file(
        ruta_archivo,
        as_attachment=True,
        download_name=nombre_descarga,
        mimetype="application/pdf",
    )