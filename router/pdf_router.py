"""
router/pdf_router.py
Blueprint Flask para la generación de reportes PDF y la autorización de
rutas hacia el portal del Conductor.

Endpoints:
  GET  /                      → Vista de la sección (pdf/index.html)
  POST /generar                → Genera y descarga el reporte PDF de la logística activa
  POST /generar-excel          → Convierte el PDF generado (enviado por el navegador) a Excel
  GET  /estado-autorizacion    → Estado de autorización de la logística activa
  POST /autorizar               → Autoriza TODAS las rutas de la logística activa
  GET  /entregas-resumen        → # de entregas registradas (para advertir antes de cancelar)
  POST /cancelar-autorizacion   → Retira la autorización (borra entregas asociadas)
"""
from io import BytesIO

from flask import Blueprint, render_template, request, send_file, jsonify, session, redirect, url_for
from logic.pdf_logic import generar_pdf, SnapshotDesactualizado
from logic.excel_logic import pdf_a_excel
from logic.conductor_logic import (
    obtener_estado_autorizacion,
    autorizar_rutas,
    cancelar_autorizacion,
    contar_entregas_logistica,
)

pdf_bp = Blueprint('pdf', __name__)

MAX_PDF_BYTES = 10 * 1024 * 1024   # el PDF real pesa ~15 KB; 10 MB es holgura de sobra
MIMETYPE_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


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
    session['logistica_id']     = str(logistica['_id'])
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
    except SnapshotDesactualizado as e:
        # 409: no es una falla, es un conflicto de estado que el usuario
        # resuelve recargando Modificación y volviendo a guardar.
        return jsonify({"status": "error", "mensaje": str(e)}), 409
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


@pdf_bp.route('/generar-excel', methods=['POST'])
def generar_excel():
    """
    Convierte a Excel el PDF que el navegador ya generó y tiene en la vista
    previa (campo multipart `pdf`). El Excel sale del mismo PDF que ve el
    usuario; no se consulta la BD ni se guarda nada en el servidor.
    """
    logistica = _logistica_activa()
    if not logistica:
        return jsonify({"status": "error", "mensaje": "No hay ninguna logística activa."}), 400

    archivo = request.files.get("pdf")
    if archivo is None:
        return jsonify({"status": "error", "mensaje": "No se recibió el PDF."}), 400

    contenido = archivo.read(MAX_PDF_BYTES + 1)
    if len(contenido) > MAX_PDF_BYTES:
        return jsonify({"status": "error", "mensaje": "El PDF es demasiado grande."}), 400
    if not contenido.startswith(b"%PDF"):
        return jsonify({"status": "error", "mensaje": "El archivo recibido no es un PDF."}), 400

    try:
        xlsx = pdf_a_excel(contenido)
    except ValueError as e:
        return jsonify({"status": "error", "mensaje": str(e)}), 422
    except Exception as e:
        return jsonify({"status": "error", "mensaje": f"Error al generar el Excel: {e}"}), 500

    return send_file(
        BytesIO(xlsx),
        as_attachment=True,
        download_name=f"{logistica['nombre'].replace(' ', '_')}.xlsx",
        mimetype=MIMETYPE_XLSX,
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
