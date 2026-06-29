from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
from logic.extraccion_logic import procesar_archivos_extraccion, procesar_mayoristas

extraccion_bp = Blueprint('extraccion', __name__)


def _logistica_id() -> str | None:
    return session.get('logistica_id')


# ── Vistas ──────────────────────────────────────────────────────────────────
@extraccion_bp.route('/', methods=['GET'])
def index():
    return render_template('extraccion/index.html')


@extraccion_bp.route('/<slug>', methods=['GET'])
def index_perfil(slug):
    """
    Ruta directa por perfil: /extraccion/<slug>
    Activa la logística correspondiente en la sesión y carga la extracción.
    Si el slug no existe redirige al menú principal.
    """
    from logic.menu_logic import obtener_logistica_por_slug, _slugify

    logistica = obtener_logistica_por_slug(slug)
    if not logistica:
        return redirect(url_for('menu.index'))

    slug_calculado = logistica.get('slug') or _slugify(logistica['nombre'])

    session['logistica_id']     = str(logistica['_id'])
    session['logistica_nombre'] = logistica['nombre']
    session['logistica_slug']   = slug_calculado
    session['logistica_inicio'] = logistica.get('fecha_inicio', '')
    session['logistica_fin']    = logistica.get('fecha_fin', '')

    return render_template('extraccion/index.html')


# ── Procesar archivos ────────────────────────────────────────────────────────
@extraccion_bp.route('/procesar', methods=['POST'])
def procesar():
    archivos = {}

    if 'file_icg'      in request.files and request.files['file_icg'].filename      != '':
        archivos['icg']      = request.files['file_icg']
    if 'file_bimbo'    in request.files and request.files['file_bimbo'].filename    != '':
        archivos['bimbo']    = request.files['file_bimbo']
    if 'file_proalmex' in request.files and request.files['file_proalmex'].filename != '':
        archivos['proalmex'] = request.files['file_proalmex']

    if not archivos:
        return jsonify({'status': 'error', 'mensaje': 'Sube al menos un archivo Excel.'}), 400

    resultado = procesar_archivos_extraccion(archivos)
    return jsonify(resultado)


# ── Obtener datos guardados ──────────────────────────────────────────────────
@extraccion_bp.route('/datos', methods=['GET'])
def obtener_datos():
    """Retorna los datos guardados en MongoDB para la logística activa."""
    lid = _logistica_id()
    if not lid:
        return jsonify({'status': 'error', 'mensaje': 'No hay logística activa.'}), 400

    try:
        from db import get_db
        from bson import ObjectId

        db  = get_db()
        doc = db['extraccion'].find_one({'logistica_id': ObjectId(lid)})
        if not doc:
            return jsonify({
                'status':           'ok',
                'data':             None,
                'desglose':         {},
                'datos_volumen':    None,
                'desglose_volumen': {},
            })

        return jsonify({
            'status':           'ok',
            'data':             doc.get('datos',             {}),
            'desglose':         doc.get('desglose',          {}),
            'datos_volumen':    doc.get('datos_volumen',     {}),
            'desglose_volumen': doc.get('desglose_volumen',  {}),
        })
    except Exception as e:
        return jsonify({'status': 'error', 'mensaje': str(e)}), 500


# ── Guardar en MongoDB ───────────────────────────────────────────────────────
@extraccion_bp.route('/guardar', methods=['POST'])
def guardar():
    """
    Guarda peso + volumen en MongoDB asociados a la logística activa.

    Payload esperado:
      {
        "datos":             { … },   # consolidado por peso
        "desglose":          { … },
        "datos_volumen":     { … },   # consolidado por volumen
        "desglose_volumen":  { … },
      }
    """
    lid = _logistica_id()
    if not lid:
        return jsonify({
            'status':  'error',
            'mensaje': 'No hay logística activa. Selecciona una desde el menú principal.',
        }), 400

    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({'status': 'error', 'mensaje': 'No se recibieron datos para guardar.'}), 400

    # Soporte para formato nuevo y formato legacy (solo datos de peso)
    if 'datos' in payload:
        datos             = payload['datos']
        desglose          = payload.get('desglose', {})
        datos_volumen     = payload.get('datos_volumen', {})
        desglose_volumen  = payload.get('desglose_volumen', {})
    else:
        datos             = payload
        desglose          = {}
        datos_volumen     = {}
        desglose_volumen  = {}

    try:
        from db import get_db
        from bson import ObjectId
        from datetime import datetime

        oid = ObjectId(lid)
        db  = get_db()
        db['extraccion'].update_one(
            {'logistica_id': oid},
            {'$set': {
                'logistica_id':    oid,
                'datos':           datos,
                'desglose':        desglose,
                'datos_volumen':   datos_volumen,
                'desglose_volumen': desglose_volumen,
                'guardado_en':     datetime.now().isoformat(),
            }},
            upsert=True,
        )
        return jsonify({'status': 'ok', 'mensaje': 'Datos guardados en MongoDB.'})
    except Exception as e:
        return jsonify({'status': 'error', 'mensaje': str(e)}), 500



# ══════════════════════════════════════════════════════════════════════════════
# CLIENTES MAYORISTAS
# ══════════════════════════════════════════════════════════════════════════════

@extraccion_bp.route('/procesar-mayoristas', methods=['POST'])
def procesar_mayoristas_endpoint():
    """Procesa el archivo Excel de Clientes Mayoristas y retorna el consolidado."""
    if 'file_mayoristas' not in request.files or request.files['file_mayoristas'].filename == '':
        return jsonify({'status': 'error', 'mensaje': 'Sube el archivo Excel de Mayoristas.'}), 400

    resultado = procesar_mayoristas(request.files['file_mayoristas'])
    return jsonify(resultado)


@extraccion_bp.route('/datos-mayoristas', methods=['GET'])
def obtener_datos_mayoristas():
    """Retorna el consolidado de mayoristas guardado en MongoDB."""
    lid = _logistica_id()
    if not lid:
        return jsonify({'status': 'error', 'mensaje': 'No hay logística activa.'}), 400

    try:
        from db import get_db
        from bson import ObjectId

        db  = get_db()
        doc = db['extraccion'].find_one({'logistica_id': ObjectId(lid)})
        if not doc or 'mayoristas' not in doc:
            return jsonify({'status': 'ok', 'consolidado': None})

        return jsonify({'status': 'ok', 'consolidado': doc.get('mayoristas')})
    except Exception as e:
        return jsonify({'status': 'error', 'mensaje': str(e)}), 500


@extraccion_bp.route('/guardar-mayoristas', methods=['POST'])
def guardar_mayoristas():
    """
    Guarda el consolidado de Clientes Mayoristas en MongoDB asociado a la
    logística activa. El campo 'mayoristas' se escribe dentro del mismo
    documento de extracción que usa el pipeline de Tiendas Lores.

    Payload esperado:
      { "consolidado": [ {codigo, nombre, peso_total_kg}, … ] }
    """
    lid = _logistica_id()
    if not lid:
        return jsonify({
            'status':  'error',
            'mensaje': 'No hay logística activa. Selecciona una desde el menú principal.',
        }), 400

    payload = request.get_json(silent=True)
    if not payload or 'consolidado' not in payload:
        return jsonify({'status': 'error', 'mensaje': 'No se recibieron datos para guardar.'}), 400

    try:
        from db import get_db
        from bson import ObjectId
        from datetime import datetime

        oid = ObjectId(lid)
        db  = get_db()
        db['extraccion'].update_one(
            {'logistica_id': oid},
            {'$set': {
                'logistica_id':       oid,
                'mayoristas':         payload['consolidado'],
                'mayoristas_guardado_en': datetime.now().isoformat(),
            }},
            upsert=True,
        )
        return jsonify({'status': 'ok', 'mensaje': 'Datos de mayoristas guardados.'})
    except Exception as e:
        return jsonify({'status': 'error', 'mensaje': str(e)}), 500


# ── Eliminar fuente y limpiar asignaciones ───────────────────────────────────
@extraccion_bp.route('/eliminar-fuente', methods=['POST'])
def eliminar_fuente():
    """
    Elimina los datos de una fuente específica de la extracción y borra las
    asignaciones VRP vigentes para forzar una regeneración limpia.

    Body JSON:  { "fuente": "mayoristas" | "icg" | "bimbo" | "proalmex" }

    Para Mayoristas elimina el campo del documento de extracción.
    Para Tiendas Lores (icg/bimbo/proalmex) los datos ya quedan vacíos al
    guardar el consolidado sin esa fuente; aquí solo se limpian asignaciones.
    En todos los casos borra asignaciones y reportes VRP de la logística activa.
    """
    lid = _logistica_id()
    if not lid:
        return jsonify({'status': 'error', 'mensaje': 'No hay logística activa.'}), 400

    payload = request.get_json(silent=True)
    fuente  = (payload or {}).get('fuente', '')
    if fuente not in {'mayoristas', 'icg', 'bimbo', 'proalmex'}:
        return jsonify({'status': 'error', 'mensaje': f'Fuente inválida: "{fuente}"'}), 400

    try:
        from db import get_db
        from bson import ObjectId

        oid = ObjectId(lid)
        db  = get_db()

        # Para Mayoristas: eliminar el campo del documento de extracción
        if fuente == 'mayoristas':
            db['extraccion'].update_one(
                {'logistica_id': oid},
                {'$unset': {'mayoristas': '', 'mayoristas_guardado_en': ''}},
            )

        # Limpiar asignaciones y reportes VRP para forzar regeneración
        for coleccion in ('asignaciones', 'vrp_reportes'):
            db[coleccion].delete_one({'logistica_id': oid})

        return jsonify({
            'status':  'ok',
            'mensaje': f'Datos de "{fuente}" eliminados. Regenera las rutas VRP para actualizar las asignaciones.',
        })
    except Exception as e:
        return jsonify({'status': 'error', 'mensaje': str(e)}), 500


# ── Sucursales para carga manual ─────────────────────────────────────────────
@extraccion_bp.route('/sucursales', methods=['GET'])
def obtener_sucursales():
    """Retorna la lista de sucursales (nombre_base + num_tienda) para la carga manual."""
    try:
        from logic.configuracion_logic import listar_sucursales
        sucursales = listar_sucursales()
        return jsonify({'status': 'ok', 'sucursales': sucursales})
    except Exception as e:
        return jsonify({'status': 'error', 'mensaje': str(e)}), 500
