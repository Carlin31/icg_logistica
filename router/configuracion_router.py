"""
router/configuracion_router.py
Blueprint Flask para la Sección 0 — Configuración del Sistema.
"""
from flask import Blueprint, render_template, request, jsonify, Response, stream_with_context
from logic.historico_logic import (
    listar_rutas_historicas,
    cargar_csv_historico,
    eliminar_historico,
    resumen_historial,
    obtener_geometrias_historico,
    stream_geometrias_historico,
)
from logic.configuracion_logic import (
    obtener_configuracion, guardar_configuracion,
    listar_productos,  obtener_producto,  agregar_producto,  editar_producto,  eliminar_producto,
    listar_productos_proalmex, obtener_producto_proalmex, agregar_producto_proalmex,
    editar_producto_proalmex, eliminar_producto_proalmex,
    listar_productos_bimbo, obtener_producto_bimbo, agregar_producto_bimbo,
    editar_producto_bimbo, eliminar_producto_bimbo,
    listar_sucursales, obtener_sucursal, agregar_sucursal, editar_sucursal, eliminar_sucursal,
    listar_vehiculos,  obtener_vehiculo,  agregar_vehiculo,  editar_vehiculo,  eliminar_vehiculo,
    toggle_activo_vehiculo, actualizar_chofer_vehiculo,
    listar_clientes_mayoristas, obtener_cliente_mayorista, agregar_cliente_mayorista,
    editar_cliente_mayorista, eliminar_cliente_mayorista, toggle_activo_cliente_mayorista,
    listar_choferes, obtener_chofer, agregar_chofer, eliminar_chofer,
)
from logic.auth_logic import generar_acceso_chofer

configuracion_bp = Blueprint("configuracion", __name__)


def _json_o_400():
    """Parsea el JSON del request; devuelve (datos, None) o (None, respuesta_error)."""
    datos = request.get_json(silent=True)
    if datos is None:
        return None, (jsonify({"status": "error", "mensaje": "Cuerpo JSON inválido o Content-Type incorrecto"}), 400)
    return datos, None


def _respuesta(resultado: dict):
    code = 200 if resultado.get("status") == "ok" else 400
    return jsonify(resultado), code


# ── Configuración general ──────────────────────────────────────
@configuracion_bp.route("/", methods=["GET"])
def index():
    return render_template("configuracion/index.html")


@configuracion_bp.route("/config-general", methods=["GET"])
def get_config_general():
    """Devuelve la configuración general del sistema (para poblar el formulario vía AJAX)."""
    config = obtener_configuracion()
    return jsonify(config)


@configuracion_bp.route("/guardar", methods=["POST"])
def guardar():
    datos, err = _json_o_400()
    if err:
        return err
    return jsonify(guardar_configuracion(datos))


# ── Productos ──────────────────────────────────────────────────
@configuracion_bp.route("/productos", methods=["GET"])
def get_productos():
    return jsonify(listar_productos(
        request.args.get("nombre", ""),
        request.args.get("fecha",  ""),
    ))


@configuracion_bp.route("/productos/<producto_id>", methods=["GET"])
def get_producto(producto_id):
    doc = obtener_producto(producto_id)
    return jsonify(doc) if doc else (jsonify({"error": "No encontrado"}), 404)


@configuracion_bp.route("/productos", methods=["POST"])
def post_producto():
    datos, err = _json_o_400()
    if err:
        return err
    resultado = agregar_producto(datos)
    code = 201 if resultado.get("status") == "ok" else 400
    return jsonify(resultado), code


@configuracion_bp.route("/productos/<producto_id>", methods=["PUT"])
def put_producto(producto_id):
    datos, err = _json_o_400()
    if err:
        return err
    return _respuesta(editar_producto(producto_id, datos))


@configuracion_bp.route("/productos/<producto_id>", methods=["DELETE"])
def delete_producto(producto_id):
    return _respuesta(eliminar_producto(producto_id))


@configuracion_bp.route("/productos-proalmex", methods=["GET"])
def get_productos_proalmex():
    return jsonify(listar_productos_proalmex(
        request.args.get("nombre", ""),
        request.args.get("fecha",  ""),
    ))


@configuracion_bp.route("/productos-proalmex/<producto_id>", methods=["GET"])
def get_producto_proalmex(producto_id):
    doc = obtener_producto_proalmex(producto_id)
    return jsonify(doc) if doc else (jsonify({"error": "No encontrado"}), 404)


@configuracion_bp.route("/productos-proalmex", methods=["POST"])
def post_producto_proalmex():
    datos, err = _json_o_400()
    if err:
        return err
    resultado = agregar_producto_proalmex(datos)
    code = 201 if resultado.get("status") == "ok" else 400
    return jsonify(resultado), code


@configuracion_bp.route("/productos-proalmex/<producto_id>", methods=["PUT"])
def put_producto_proalmex(producto_id):
    datos, err = _json_o_400()
    if err:
        return err
    return _respuesta(editar_producto_proalmex(producto_id, datos))


@configuracion_bp.route("/productos-proalmex/<producto_id>", methods=["DELETE"])
def delete_producto_proalmex(producto_id):
    return _respuesta(eliminar_producto_proalmex(producto_id))


# ── Productos Bimbo ───────────────────────────────────────────
@configuracion_bp.route("/productos-bimbo", methods=["GET"])
def get_productos_bimbo():
    return jsonify(listar_productos_bimbo(
        request.args.get("nombre", ""),
        request.args.get("fecha",  ""),
    ))


@configuracion_bp.route("/productos-bimbo/<producto_id>", methods=["GET"])
def get_producto_bimbo(producto_id):
    doc = obtener_producto_bimbo(producto_id)
    return jsonify(doc) if doc else (jsonify({"error": "No encontrado"}), 404)


@configuracion_bp.route("/productos-bimbo", methods=["POST"])
def post_producto_bimbo():
    datos, err = _json_o_400()
    if err:
        return err
    resultado = agregar_producto_bimbo(datos)
    code = 201 if resultado.get("status") == "ok" else 400
    return jsonify(resultado), code


@configuracion_bp.route("/productos-bimbo/<producto_id>", methods=["PUT"])
def put_producto_bimbo(producto_id):
    datos, err = _json_o_400()
    if err:
        return err
    return _respuesta(editar_producto_bimbo(producto_id, datos))


@configuracion_bp.route("/productos-bimbo/<producto_id>", methods=["DELETE"])
def delete_producto_bimbo(producto_id):
    return _respuesta(eliminar_producto_bimbo(producto_id))


# ── Sucursales ─────────────────────────────────────────────────
@configuracion_bp.route("/sucursales", methods=["GET"])
def get_sucursales():
    return jsonify(listar_sucursales(
        request.args.get("nombre", ""),
        request.args.get("fecha",  ""),
    ))


@configuracion_bp.route("/sucursales/<sucursal_id>", methods=["GET"])
def get_sucursal(sucursal_id):
    doc = obtener_sucursal(sucursal_id)
    return jsonify(doc) if doc else (jsonify({"error": "No encontrado"}), 404)


@configuracion_bp.route("/sucursales", methods=["POST"])
def post_sucursal():
    datos, err = _json_o_400()
    if err:
        return err
    return jsonify(agregar_sucursal(datos)), 201


@configuracion_bp.route("/sucursales/<sucursal_id>", methods=["PUT"])
def put_sucursal(sucursal_id):
    datos, err = _json_o_400()
    if err:
        return err
    return _respuesta(editar_sucursal(sucursal_id, datos))


@configuracion_bp.route("/sucursales/<sucursal_id>", methods=["DELETE"])
def delete_sucursal(sucursal_id):
    return _respuesta(eliminar_sucursal(sucursal_id))


# ── Vehículos ──────────────────────────────────────────────────
@configuracion_bp.route("/vehiculos", methods=["GET"])
def get_vehiculos():
    return jsonify(listar_vehiculos(
        request.args.get("nombre", ""),
        request.args.get("fecha",  ""),
    ))


@configuracion_bp.route("/vehiculos/<vehiculo_id>", methods=["GET"])
def get_vehiculo(vehiculo_id):
    doc = obtener_vehiculo(vehiculo_id)
    return jsonify(doc) if doc else (jsonify({"error": "No encontrado"}), 404)


@configuracion_bp.route("/vehiculos", methods=["POST"])
def post_vehiculo():
    datos, err = _json_o_400()
    if err:
        return err
    return jsonify(agregar_vehiculo(datos)), 201


@configuracion_bp.route("/vehiculos/<vehiculo_id>", methods=["PUT"])
def put_vehiculo(vehiculo_id):
    datos, err = _json_o_400()
    if err:
        return err
    return _respuesta(editar_vehiculo(vehiculo_id, datos))


@configuracion_bp.route("/vehiculos/<vehiculo_id>", methods=["DELETE"])
def delete_vehiculo(vehiculo_id):
    return _respuesta(eliminar_vehiculo(vehiculo_id))


@configuracion_bp.route("/vehiculos/<vehiculo_id>/activo", methods=["PUT"])
def put_vehiculo_activo(vehiculo_id):
    return _respuesta(toggle_activo_vehiculo(vehiculo_id))


@configuracion_bp.route("/vehiculos/<vehiculo_id>/chofer", methods=["PUT"])
def put_vehiculo_chofer(vehiculo_id):
    datos, err = _json_o_400()
    if err:
        return err
    return _respuesta(actualizar_chofer_vehiculo(vehiculo_id, datos.get("chofer", ""), datos.get("chofer_id")))


# ── Choferes ─────────────────────────────────────────────────────
@configuracion_bp.route("/choferes", methods=["GET"])
def get_choferes():
    return jsonify(listar_choferes(request.args.get("nombre", "")))


@configuracion_bp.route("/choferes/<chofer_id>", methods=["GET"])
def get_chofer(chofer_id):
    doc = obtener_chofer(chofer_id)
    return jsonify(doc) if doc else (jsonify({"error": "No encontrado"}), 404)


@configuracion_bp.route("/choferes", methods=["POST"])
def post_chofer():
    datos, err = _json_o_400()
    if err:
        return err
    resultado = agregar_chofer(datos)
    code = 201 if resultado.get("status") == "ok" else 400
    return jsonify(resultado), code


@configuracion_bp.route("/choferes/<chofer_id>", methods=["DELETE"])
def delete_chofer(chofer_id):
    return _respuesta(eliminar_chofer(chofer_id))


@configuracion_bp.route("/choferes/<chofer_id>/generar-acceso", methods=["POST"])
def post_generar_acceso_chofer(chofer_id):
    """Genera username + contraseña para que el chofer pueda usar el portal del Conductor."""
    resultado = generar_acceso_chofer(chofer_id)
    code = 201 if resultado.get("status") == "ok" else 400
    return jsonify(resultado), code


# ── Clientes Mayoristas ────────────────────────────────────────
@configuracion_bp.route("/clientes-mayoristas", methods=["GET"])
def get_clientes_mayoristas():
    return jsonify(listar_clientes_mayoristas(
        request.args.get("nombre", ""),
        request.args.get("fecha",  ""),
    ))


@configuracion_bp.route("/clientes-mayoristas/<cliente_id>", methods=["GET"])
def get_cliente_mayorista(cliente_id):
    doc = obtener_cliente_mayorista(cliente_id)
    return jsonify(doc) if doc else (jsonify({"error": "No encontrado"}), 404)


@configuracion_bp.route("/clientes-mayoristas", methods=["POST"])
def post_cliente_mayorista():
    datos, err = _json_o_400()
    if err:
        return err
    return jsonify(agregar_cliente_mayorista(datos)), 201


@configuracion_bp.route("/clientes-mayoristas/<cliente_id>", methods=["PUT"])
def put_cliente_mayorista(cliente_id):
    datos, err = _json_o_400()
    if err:
        return err
    return _respuesta(editar_cliente_mayorista(cliente_id, datos))


@configuracion_bp.route("/clientes-mayoristas/<cliente_id>", methods=["DELETE"])
def delete_cliente_mayorista(cliente_id):
    return _respuesta(eliminar_cliente_mayorista(cliente_id))


@configuracion_bp.route("/clientes-mayoristas/<cliente_id>/activo", methods=["PUT"])
def put_cliente_mayorista_activo(cliente_id):
    return _respuesta(toggle_activo_cliente_mayorista(cliente_id))


# ── Rutas Históricas ───────────────────────────────────────────

@configuracion_bp.route("/rutas-historicas", methods=["GET"])
def get_rutas_historicas():
    """Lista todos los historiales de rutas cargados."""
    return jsonify(listar_rutas_historicas())


@configuracion_bp.route("/rutas-historicas/resumen", methods=["GET"])
def get_resumen_historico():
    """Estadísticas rápidas del historial (sin cargar filas)."""
    return jsonify(resumen_historial())


@configuracion_bp.route("/rutas-historicas/cargar", methods=["POST"])
def post_cargar_historico():
    """
    Sube un CSV de rutas históricas.
    Espera multipart/form-data con campo 'archivo' (file) y 'nombre' (text).
    """
    archivo = request.files.get("archivo")
    if not archivo:
        return jsonify({"status": "error", "mensaje": "No se recibió ningún archivo"}), 400

    nombre = request.form.get("nombre", "") or archivo.filename or "Historial"
    csv_bytes = archivo.read()
    resultado = cargar_csv_historico(csv_bytes, nombre)
    code = 201 if resultado.get("status") == "ok" else 400
    return jsonify(resultado), code


@configuracion_bp.route("/rutas-historicas/<hist_id>", methods=["DELETE"])
def delete_historico(hist_id):
    """Elimina un historial de rutas por su ID."""
    return jsonify(eliminar_historico(hist_id))


@configuracion_bp.route("/rutas-historicas/<hist_id>/geometrias", methods=["GET"])
def get_geometrias_historico(hist_id):
    """Geometrías OSRM (con caché) para visualizar un historial en el mapa."""
    return jsonify(obtener_geometrias_historico(hist_id))


@configuracion_bp.route("/rutas-historicas/<hist_id>/geometrias-stream", methods=["GET"])
def stream_geometrias_historico_endpoint(hist_id):
    """SSE: emite progreso ruta por ruta mientras calcula geometrías OSRM."""
    return Response(
        stream_with_context(stream_geometrias_historico(hist_id)),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )