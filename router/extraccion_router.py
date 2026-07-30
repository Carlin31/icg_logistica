import json
from datetime import datetime

from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for

from bson import ObjectId
from bson.errors import InvalidId
from sqlalchemy import select, insert, update, delete

from logic.extraccion_logic import procesar_archivos_extraccion, procesar_mayoristas

extraccion_bp = Blueprint('extraccion', __name__)


def _logistica_id() -> "str | None":
    return session.get('logistica_id')


def _id_valido(doc_id: str) -> "str | None":
    try:
        return str(ObjectId(doc_id))
    except (InvalidId, TypeError):
        return None


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


# ── Helpers de desglose (extraccion_desglose) ────────────────────────────────

def _id_sucursal_int(valor) -> "int | None":
    """
    Coacciona id_sucursal a int para la columna INT de extraccion_desglose.

    Las sucursales del pedido que no cruzan con el catálogo llegan con
    id_sucursal == 'N/A' (marcador de "sin match", ver calculadora.py). Ese
    valor —o cualquier otro no numérico— no cabe en una columna INT de SQL
    Server (falla con "Conversion failed ... to data type int"), así que aquí
    se normaliza a NULL. El marcador textual se conserva íntegro en el JSON
    `datos`/`datos_volumen`, que es la fuente que consumen pesos/volúmenes.
    """
    if valor is None:
        return None
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def _desglose_a_filas(extraccion_id: str, logistica_id: str, tipo_medida: str, desglose: dict) -> list:
    """
    Aplana desglose[marca][nombre_sucursal] = {id_sucursal, kg|m3} en filas
    listas para insertar en extraccion_desglose.
    """
    campo_valor = 'kg' if tipo_medida == 'kg' else 'm3'
    filas = []
    for marca, por_sucursal in (desglose or {}).items():
        if not isinstance(por_sucursal, dict):
            continue
        for nombre_sucursal, valores in por_sucursal.items():
            if not isinstance(valores, dict):
                continue
            filas.append({
                'extraccion_id':    extraccion_id,
                'logistica_id':     logistica_id,
                'tipo_medida':      tipo_medida,
                'marca':            marca,
                'nombre_sucursal':  nombre_sucursal,
                'id_sucursal':      _id_sucursal_int(valores.get('id_sucursal')),
                'valor':            valores.get(campo_valor),
            })
    return filas


def _filas_a_desglose(filas: list, campo_valor: str) -> dict:
    """Inverso de _desglose_a_filas: reconstruye desglose[marca][nombre_sucursal] = {id_sucursal, kg|m3}."""
    desglose: dict = {}
    for f in filas:
        desglose.setdefault(f.marca, {})[f.nombre_sucursal] = {
            'id_sucursal': f.id_sucursal,
            campo_valor:   f.valor,
        }
    return desglose


# ── Obtener datos guardados ──────────────────────────────────────────────────
@extraccion_bp.route('/datos', methods=['GET'])
def obtener_datos():
    """Retorna los datos guardados en SQL Server para la logística activa."""
    lid = _logistica_id()
    if not lid:
        return jsonify({'status': 'error', 'mensaje': 'No hay logística activa.'}), 400

    oid = _id_valido(lid)
    if oid is None:
        return jsonify({'status': 'error', 'mensaje': 'Logística inválida.'}), 400

    try:
        from db import get_db, get_table

        db      = get_db()
        tabla   = get_table('extraccion')
        doc = db.execute(select(tabla).where(tabla.c.logistica_id == oid)).mappings().first()
        if not doc:
            return jsonify({
                'status':           'ok',
                'data':             None,
                'desglose':         {},
                'datos_volumen':    None,
                'desglose_volumen': {},
            })

        t_desglose = get_table('extraccion_desglose')
        filas_kg = db.execute(select(t_desglose).where(t_desglose.c.logistica_id == oid, t_desglose.c.tipo_medida == 'kg')).all()
        filas_m3 = db.execute(select(t_desglose).where(t_desglose.c.logistica_id == oid, t_desglose.c.tipo_medida == 'm3')).all()

        return jsonify({
            'status':           'ok',
            'data':             json.loads(doc['datos']) if doc['datos'] else {},
            'desglose':         _filas_a_desglose(filas_kg, 'kg'),
            'datos_volumen':    json.loads(doc['datos_volumen']) if doc['datos_volumen'] else {},
            'desglose_volumen': _filas_a_desglose(filas_m3, 'm3'),
        })
    except Exception as e:
        return jsonify({'status': 'error', 'mensaje': str(e)}), 500


# ── Guardar en SQL Server ───────────────────────────────────────────────────
@extraccion_bp.route('/guardar', methods=['POST'])
def guardar():
    """
    Guarda peso + volumen en SQL Server asociados a la logística activa.

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
        from db import get_table, transaccion

        oid   = _id_valido(lid)
        if oid is None:
            return jsonify({'status': 'error', 'mensaje': 'Logística inválida.'}), 400

        tabla      = get_table('extraccion')
        t_desglose = get_table('extraccion_desglose')

        # Todo el guardado (fila de extraccion + reemplazo de su desglose) es
        # una sola unidad atómica — en Mongo era un único $set de un mismo
        # documento; aquí son varias sentencias sobre dos tablas, así que
        # necesitan una transacción real (ver db.transaccion()).
        with transaccion() as conn:
            existente = conn.execute(select(tabla.c.mongo_id).where(tabla.c.logistica_id == oid)).first()
            valores = {
                'logistica_id':  oid,
                'datos':         json.dumps(datos, ensure_ascii=False),
                'datos_volumen': json.dumps(datos_volumen, ensure_ascii=False),
                'guardado_en':   datetime.now().isoformat(),
            }
            if existente:
                extraccion_id = existente.mongo_id
                conn.execute(update(tabla).where(tabla.c.mongo_id == extraccion_id).values(**valores))
            else:
                extraccion_id = str(ObjectId())
                conn.execute(insert(tabla).values(mongo_id=extraccion_id, **valores))

            conn.execute(delete(t_desglose).where(t_desglose.c.logistica_id == oid))
            filas = (
                _desglose_a_filas(extraccion_id, oid, 'kg', desglose)
                + _desglose_a_filas(extraccion_id, oid, 'm3', desglose_volumen)
            )
            if filas:
                conn.execute(insert(t_desglose), filas)

        return jsonify({'status': 'ok', 'mensaje': 'Datos guardados en SQL Server.'})
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
    """Retorna el consolidado de mayoristas guardado en SQL Server."""
    lid = _logistica_id()
    if not lid:
        return jsonify({'status': 'error', 'mensaje': 'No hay logística activa.'}), 400

    try:
        from db import get_db, get_table

        oid = _id_valido(lid)
        if oid is None:
            return jsonify({'status': 'error', 'mensaje': 'Logística inválida.'}), 400

        db    = get_db()
        tabla = get_table('extraccion')
        doc = db.execute(select(tabla.c.mayoristas).where(tabla.c.logistica_id == oid)).mappings().first()
        if not doc or doc['mayoristas'] is None:
            return jsonify({'status': 'ok', 'consolidado': None})

        return jsonify({'status': 'ok', 'consolidado': json.loads(doc['mayoristas'])})
    except Exception as e:
        return jsonify({'status': 'error', 'mensaje': str(e)}), 500


@extraccion_bp.route('/guardar-mayoristas', methods=['POST'])
def guardar_mayoristas():
    """
    Guarda el consolidado de Clientes Mayoristas en SQL Server asociado a la
    logística activa. El campo 'mayoristas' se escribe dentro de la misma
    fila de extracción que usa el pipeline de Tiendas Lores.

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
        from db import get_db, get_table

        oid = _id_valido(lid)
        if oid is None:
            return jsonify({'status': 'error', 'mensaje': 'Logística inválida.'}), 400

        db    = get_db()
        tabla = get_table('extraccion')
        valores = {
            'mayoristas':             json.dumps(payload['consolidado'], ensure_ascii=False),
            'mayoristas_guardado_en': datetime.now().isoformat(),
        }
        existente = db.execute(select(tabla.c.mongo_id).where(tabla.c.logistica_id == oid)).first()
        if existente:
            db.execute(update(tabla).where(tabla.c.mongo_id == existente.mongo_id).values(**valores))
        else:
            db.execute(insert(tabla).values(mongo_id=str(ObjectId()), logistica_id=oid, **valores))

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
    En todos los casos borra asignaciones y reportes VRP de la logística activa
    (base + todas sus tablas normalizadas hijas).
    """
    lid = _logistica_id()
    if not lid:
        return jsonify({'status': 'error', 'mensaje': 'No hay logística activa.'}), 400

    payload = request.get_json(silent=True)
    fuente  = (payload or {}).get('fuente', '')
    if fuente not in {'mayoristas', 'icg', 'bimbo', 'proalmex'}:
        return jsonify({'status': 'error', 'mensaje': f'Fuente inválida: "{fuente}"'}), 400

    try:
        from db import get_db, get_table
        from logic.menu_logic import eliminar_datos_asignacion, eliminar_datos_vrp_reportes

        oid = _id_valido(lid)
        if oid is None:
            return jsonify({'status': 'error', 'mensaje': 'Logística inválida.'}), 400

        db = get_db()

        # Para Mayoristas: eliminar el campo del documento de extracción
        if fuente == 'mayoristas':
            tabla = get_table('extraccion')
            db.execute(
                update(tabla).where(tabla.c.logistica_id == oid)
                .values(mayoristas=None, mayoristas_guardado_en=None)
            )

        # Limpiar asignaciones y reportes VRP (+ tablas normalizadas) para forzar regeneración
        eliminar_datos_asignacion(oid)
        eliminar_datos_vrp_reportes(oid)

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
