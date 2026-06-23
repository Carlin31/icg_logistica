"""
router/modificacion_router.py
Blueprint Flask para la Sección 6 — Modificación manual de rutas.
Pasa logistica_id a todas las funciones de lógica.
"""
from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, Response
from logic.historico_logic import exportar_csv_rutas, guardar_en_historico
from logic.modificacion_logic import (
    MATRIZ_LAT_DEFAULT, MATRIZ_LON_DEFAULT,
    obtener_rutas_para_modificar,
    obtener_sucursales_disponibles,
    obtener_pesos,
    obtener_vehiculos,
    obtener_disponibilidad_vehiculos,
    calcular_tiempos_subruta,
    calcular_alternativas_subruta,
    calcular_ruta_personalizada,
    calcular_tiempos_lote,
    guardar_modificacion,
    obtener_modificacion_previa,
    actualizar_vehiculo_en_asignacion,
    actualizar_chofer_en_asignacion,
    actualizar_orden_paradas,
    quitar_sucursal_de_asignacion,
    agregar_sucursal_a_asignacion,
    actualizar_rutas_confirmadas,
    crear_ruta_manual,
    eliminar_ruta_manual,
    quitar_mayorista_de_ruta,
    agregar_mayorista_a_ruta,
    cambiar_dia_ruta,
)

modificacion_bp = Blueprint("modificacion", __name__)


def _json_o_400():
    datos = request.get_json(silent=True)
    if datos is None:
        return None, (jsonify({"status": "error", "mensaje": "Cuerpo JSON inválido"}), 400)
    return datos, None


def _logistica_id() -> str | None:
    return session.get("logistica_id")


def _requiere_logistica():
    lid = _logistica_id()
    if not lid:
        return None, (
            jsonify({"status": "error", "mensaje": "No hay logística activa. Selecciona una desde el menú."}),
            400,
        )
    return lid, None


@modificacion_bp.route("/", methods=["GET"])
def index():
    return render_template("modificacion/index.html")


@modificacion_bp.route("/<slug>", methods=["GET"])
def index_perfil(slug):
    """Ruta directa por perfil: /modificacion/<slug>. Activa el perfil y carga la sección."""
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
    return render_template("modificacion/index.html")


@modificacion_bp.route("/rutas", methods=["GET"])
def get_rutas():
    lid, err = _requiere_logistica()
    if err:
        return err
    try:
        return jsonify(obtener_rutas_para_modificar(lid))
    except Exception as e:
        return jsonify({"status": "error", "mensaje": str(e)}), 500


@modificacion_bp.route("/sucursales", methods=["GET"])
def get_sucursales():
    try:
        return jsonify(obtener_sucursales_disponibles())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@modificacion_bp.route("/vehiculos", methods=["GET"])
def get_vehiculos():
    """
    Devuelve la flota activa con disponibilidad por día calculada desde MongoDB.
    Requiere logística activa para leer la ocupación; sin ella devuelve la flota
    sin datos de ocupación.
    """
    lid = _logistica_id()
    try:
        if lid:
            return jsonify(obtener_disponibilidad_vehiculos(lid))
        return jsonify(obtener_vehiculos())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@modificacion_bp.route("/pesos", methods=["GET"])
def get_pesos():
    lid, err = _requiere_logistica()
    if err:
        return err
    try:
        return jsonify(obtener_pesos(lid))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@modificacion_bp.route("/horarios-config", methods=["GET"])
def get_horarios_config():
    from logic.configuracion_logic import obtener_configuracion
    try:
        cfg = obtener_configuracion()
        return jsonify(cfg.get("config_dias") or {})
    except Exception as e:
        return jsonify({}), 200


@modificacion_bp.route("/actualizar-vehiculo", methods=["POST"])
def post_actualizar_vehiculo():
    lid, err = _requiere_logistica()
    if err:
        return err
    datos, err2 = _json_o_400()
    if err2:
        return err2
    try:
        resultado = actualizar_vehiculo_en_asignacion(
            lid,
            datos.get("ruta_id"),
            datos.get("dia"),
            datos.get("vehiculo_placas"),
            datos.get("vehiculo_abreviatura"),
            datos.get("capacidad_ton"),
        )
        code = 200 if resultado.get("status") == "ok" else 500
        return jsonify(resultado), code
    except Exception as e:
        return jsonify({"status": "error", "mensaje": str(e)}), 500


@modificacion_bp.route("/actualizar-chofer", methods=["POST"])
def post_actualizar_chofer():
    """Cambia el chofer de una ruta puntual (día + ruta), sin afectar el chofer por defecto del vehículo."""
    lid, err = _requiere_logistica()
    if err:
        return err
    datos, err2 = _json_o_400()
    if err2:
        return err2
    try:
        resultado = actualizar_chofer_en_asignacion(
            lid,
            datos.get("ruta_id"),
            datos.get("dia"),
            datos.get("chofer", ""),
            datos.get("chofer_id"),
        )
        code = 200 if resultado.get("status") == "ok" else 500
        return jsonify(resultado), code
    except Exception as e:
        return jsonify({"status": "error", "mensaje": str(e)}), 500


@modificacion_bp.route("/actualizar-orden", methods=["POST"])
def post_actualizar_orden():
    """Persiste la secuencia exacta de paradas de una ruta (drag & drop / agregar / quitar)."""
    lid, err = _requiere_logistica()
    if err:
        return err
    datos, err2 = _json_o_400()
    if err2:
        return err2
    try:
        resultado = actualizar_orden_paradas(
            lid,
            datos.get("ruta_id"),
            datos.get("dia"),
            datos.get("orden_paradas") or [],
        )
        code = 200 if resultado.get("status") == "ok" else 500
        return jsonify(resultado), code
    except Exception as e:
        return jsonify({"status": "error", "mensaje": str(e)}), 500


@modificacion_bp.route("/recalcular-tiempos", methods=["POST"])
def post_recalcular():
    lid, err = _requiere_logistica()
    if err:
        return err
    datos, err2 = _json_o_400()
    if err2:
        return err2

    # Acepta tanto "paradas" (sucursales+mayoristas) como "sucursales" (legado)
    paradas = datos.get("paradas") or datos.get("sucursales")
    if not isinstance(paradas, list):
        return jsonify({"status": "error", "mensaje": "Se esperaba { paradas: [...] }"}), 400

    hora_salida = datos.get("hora_salida") or "08:00"
    pesos = obtener_pesos(lid)
    try:
        return jsonify(calcular_tiempos_subruta(paradas, pesos, hora_salida))
    except Exception as e:
        return jsonify({"status": "error", "mensaje": str(e)}), 500


@modificacion_bp.route("/alternativas-ruta", methods=["POST"])
def post_alternativas_ruta():
    lid, err = _requiere_logistica()
    if err:
        return err
    datos, err2 = _json_o_400()
    if err2:
        return err2

    paradas = datos.get("paradas") or datos.get("sucursales")
    if not isinstance(paradas, list):
        return jsonify({"status": "error", "mensaje": "Se esperaba { paradas: [...] }"}), 400

    hora_salida = datos.get("hora_salida") or "08:00"
    pesos = obtener_pesos(lid)
    try:
        alternativas = calcular_alternativas_subruta(paradas, pesos, hora_salida)
        return jsonify({
            "status": "ok",
            "alternativas": alternativas,
            "matriz": [MATRIZ_LAT_DEFAULT, MATRIZ_LON_DEFAULT],
        })
    except Exception as e:
        return jsonify({"status": "error", "mensaje": str(e)}), 500


@modificacion_bp.route("/ruta-personalizada", methods=["POST"])
def post_ruta_personalizada():
    lid, err = _requiere_logistica()
    if err:
        return err
    datos, err2 = _json_o_400()
    if err2:
        return err2

    paradas        = datos.get("paradas") or datos.get("sucursales")
    via_points     = datos.get("via_points") or []
    puntos_evitar  = datos.get("puntos_evitar") or []
    if not isinstance(paradas, list):
        return jsonify({"status": "error", "mensaje": "Se esperaba { paradas: [...] }"}), 400

    hora_salida = datos.get("hora_salida") or "08:00"
    pesos = obtener_pesos(lid)
    try:
        ruta = calcular_ruta_personalizada(paradas, via_points, pesos, hora_salida, puntos_evitar)
        return jsonify({"status": "ok", "ruta": ruta})
    except Exception as e:
        return jsonify({"status": "error", "mensaje": str(e)}), 500


@modificacion_bp.route("/calcular-lote", methods=["POST"])
def post_calcular_lote():
    lid, err = _requiere_logistica()
    if err:
        return err
    datos, err2 = _json_o_400()
    if err2:
        return err2

    rutas = datos.get("rutas")
    if not isinstance(rutas, list):
        return jsonify({"status": "error", "mensaje": "Se esperaba { rutas: [...] }"}), 400

    pesos = obtener_pesos(lid)
    try:
        resultados = calcular_tiempos_lote(rutas, pesos)
        return jsonify({"status": "ok", "resultados": resultados})
    except Exception as e:
        return jsonify({"status": "error", "mensaje": str(e)}), 500


@modificacion_bp.route("/guardar", methods=["POST"])
def post_guardar():
    lid, err = _requiere_logistica()
    if err:
        return err
    datos, err2 = _json_o_400()
    if err2:
        return err2
    resultado = guardar_modificacion(datos, lid)
    code = 200 if resultado.get("status") == "ok" else 500
    return jsonify(resultado), code


@modificacion_bp.route("/previa", methods=["GET"])
def get_previa():
    lid, err = _requiere_logistica()
    if err:
        return err
    try:
        return jsonify(obtener_modificacion_previa(lid))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@modificacion_bp.route("/rutas-confirmadas", methods=["POST"])
def post_rutas_confirmadas():
    """
    Persiste el estado de confirmación de rutas.
    Body: { ruta_ids: ["id1", "id2", ...] }
    """
    lid, err = _requiere_logistica()
    if err:
        return err
    datos, err2 = _json_o_400()
    if err2:
        return err2
    ruta_ids = datos.get("ruta_ids", [])
    try:
        resultado = actualizar_rutas_confirmadas(lid, ruta_ids)
        return jsonify(resultado)
    except Exception as e:
        return jsonify({"status": "error", "mensaje": str(e)}), 500


@modificacion_bp.route("/crear-ruta", methods=["POST"])
def post_crear_ruta():
    """
    Crea una ruta manual dentro de asignaciones.detalle_por_dia.
    Body: { dia, vehiculo_placas, sucursales: [{num_tienda, nombre}] }
    """
    lid, err = _requiere_logistica()
    if err:
        return err
    datos, err2 = _json_o_400()
    if err2:
        return err2

    dia = datos.get("dia", "")
    vehiculo_placas = datos.get("vehiculo_placas", "")
    sucursales = datos.get("sucursales", [])
    mayoristas = datos.get("mayoristas", [])
    nombre = datos.get("nombre_ruta") or datos.get("nombre")

    try:
        resultado = crear_ruta_manual(lid, dia, vehiculo_placas, sucursales, mayoristas, nombre)
        code = 200 if resultado.get("status") == "ok" else 400
        return jsonify(resultado), code
    except Exception as e:
        return jsonify({"status": "error", "mensaje": str(e)}), 500


@modificacion_bp.route("/eliminar-ruta", methods=["POST"])
def post_eliminar_ruta():
    """
    Elimina una ruta manual y mueve sus sucursales a pendientes.
    Body: { ruta_id, dia }
    """
    lid, err = _requiere_logistica()
    if err:
        return err
    datos, err2 = _json_o_400()
    if err2:
        return err2

    ruta_id = datos.get("ruta_id", "")
    dia = datos.get("dia", "")
    try:
        resultado = eliminar_ruta_manual(lid, ruta_id, dia)
        code = 200 if resultado.get("status") == "ok" else 400
        return jsonify(resultado), code
    except Exception as e:
        return jsonify({"status": "error", "mensaje": str(e)}), 500


@modificacion_bp.route("/cambiar-dia", methods=["POST"])
def post_cambiar_dia():
    """
    Mueve una ruta de un día a otro en asignaciones.detalle_por_dia.
    Body: { ruta_id, dia_actual, dia_nuevo }
    """
    lid, err = _requiere_logistica()
    if err:
        return err
    datos, err2 = _json_o_400()
    if err2:
        return err2
    try:
        resultado = cambiar_dia_ruta(
            lid,
            datos.get("ruta_id", ""),
            datos.get("dia_actual", ""),
            datos.get("dia_nuevo", ""),
        )
        code = 200 if resultado.get("status") == "ok" else 400
        return jsonify(resultado), code
    except Exception as e:
        return jsonify({"status": "error", "mensaje": str(e)}), 500


@modificacion_bp.route("/quitar-sucursal", methods=["POST"])
def post_quitar_sucursal():
    """
    Quita una sucursal de una ruta en asignaciones y la registra como pendiente.
    Body: { ruta_id, dia, num_tienda, nombre, latitud, longitud, peso_kg }
    """
    lid, err = _requiere_logistica()
    if err:
        return err
    datos, err2 = _json_o_400()
    if err2:
        return err2
    try:
        resultado = quitar_sucursal_de_asignacion(
            logistica_id = lid,
            ruta_id      = datos.get("ruta_id", ""),
            dia          = datos.get("dia", ""),
            num_tienda   = int(datos.get("num_tienda", 0)),
            nombre       = datos.get("nombre", ""),
            latitud      = datos.get("latitud"),
            longitud     = datos.get("longitud"),
            peso_kg      = float(datos.get("peso_kg") or 0),
        )
        code = 200 if resultado.get("status") == "ok" else 500
        return jsonify(resultado), code
    except Exception as e:
        return jsonify({"status": "error", "mensaje": str(e)}), 500


@modificacion_bp.route("/agregar-sucursal", methods=["POST"])
def post_agregar_sucursal():
    """
    Añade una sucursal a una ruta en asignaciones y la elimina de pendientes.
    Body: { ruta_id, dia, num_tienda, nombre, latitud, longitud, peso_kg }
    """
    lid, err = _requiere_logistica()
    if err:
        return err
    datos, err2 = _json_o_400()
    if err2:
        return err2
    try:
        resultado = agregar_sucursal_a_asignacion(
            logistica_id = lid,
            ruta_id      = datos.get("ruta_id", ""),
            dia          = datos.get("dia", ""),
            num_tienda   = int(datos.get("num_tienda", 0)),
            nombre       = datos.get("nombre", ""),
            latitud      = datos.get("latitud"),
            longitud     = datos.get("longitud"),
            peso_kg      = float(datos.get("peso_kg") or 0),
        )
        code = 200 if resultado.get("status") == "ok" else 500
        return jsonify(resultado), code
    except Exception as e:
        return jsonify({"status": "error", "mensaje": str(e)}), 500


@modificacion_bp.route("/quitar-mayorista", methods=["POST"])
def post_quitar_mayorista():
    """
    Registra el retiro de un mayorista de una ruta en mayoristas_overrides.
    Body: { ruta_id, id_cliente }
    """
    lid, err = _requiere_logistica()
    if err:
        return err
    datos, err2 = _json_o_400()
    if err2:
        return err2
    ruta_id    = datos.get("ruta_id", "")
    id_cliente = datos.get("id_cliente")
    documento  = datos.get("documento", "")
    if not ruta_id or id_cliente is None:
        return jsonify({"status": "error", "mensaje": "Se requiere ruta_id e id_cliente"}), 400
    try:
        resultado = quitar_mayorista_de_ruta(lid, ruta_id, int(id_cliente), documento=str(documento))
        code = 200 if resultado.get("status") == "ok" else 500
        return jsonify(resultado), code
    except Exception as e:
        return jsonify({"status": "error", "mensaje": str(e)}), 500


@modificacion_bp.route("/agregar-mayorista", methods=["POST"])
def post_agregar_mayorista():
    """
    Incorpora un mayorista a una ruta mediante override y verifica capacidad del vehículo.
    Body: { ruta_id, dia, id_cliente, nombre, latitud, longitud, peso_kg, peso_ruta_actual }
    """
    lid, err = _requiere_logistica()
    if err:
        return err
    datos, err2 = _json_o_400()
    if err2:
        return err2
    ruta_id          = datos.get("ruta_id", "")
    dia              = datos.get("dia", "")
    id_cliente       = datos.get("id_cliente")
    if not ruta_id or not dia or id_cliente is None:
        return jsonify({"status": "error", "mensaje": "Se requiere ruta_id, dia e id_cliente"}), 400
    try:
        resultado = agregar_mayorista_a_ruta(
            logistica_id     = lid,
            ruta_id          = ruta_id,
            dia              = dia,
            id_cliente       = int(id_cliente),
            nombre           = datos.get("nombre", ""),
            latitud          = datos.get("latitud"),
            longitud         = datos.get("longitud"),
            peso_kg          = float(datos.get("peso_kg") or 0),
            peso_ruta_actual = float(datos.get("peso_ruta_actual") or 0),
            documento        = str(datos.get("documento") or ""),
        )
        code = 200 if resultado.get("status") == "ok" else 500
        return jsonify(resultado), code
    except Exception as e:
        return jsonify({"status": "error", "mensaje": str(e)}), 500


@modificacion_bp.route("/exportar-csv", methods=["POST"])
def post_exportar_csv():
    """
    Exporta las rutas modificadas en formato CSV histórico.

    Espera JSON:
      { rutas: [{id_sucursal, vehiculo, dia_semana, secuencia_visita, kg_entrega}] }

    Retorna: archivo CSV para descarga.
    """
    lid, err = _requiere_logistica()
    if err:
        return err
    datos, err2 = _json_o_400()
    if err2:
        return err2

    rutas = datos.get("rutas", [])
    if not rutas:
        return jsonify({"status": "error", "mensaje": "No se recibieron rutas"}), 400

    try:
        csv_content = exportar_csv_rutas(rutas)
        return Response(
            csv_content,
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=rutas_historico.csv"},
        )
    except Exception as e:
        return jsonify({"status": "error", "mensaje": str(e)}), 500


@modificacion_bp.route("/guardar-historico", methods=["POST"])
def post_guardar_historico():
    """
    Guarda las rutas confirmadas como un nuevo historial en MongoDB.

    Espera JSON:
      {
        nombre: str,          // nombre del historial (ej. "Logística Mar 10-14 2026")
        rutas:  [{num_tienda, vehiculo, dia_semana, secuencia_visita, kg_entrega}]
      }
    """
    lid, err = _requiere_logistica()
    if err:
        return err
    datos, err2 = _json_o_400()
    if err2:
        return err2

    nombre = datos.get("nombre", "Rutas confirmadas")
    rutas  = datos.get("rutas", [])
    if not rutas:
        return jsonify({"status": "error", "mensaje": "No se recibieron rutas"}), 400

    try:
        resultado = guardar_en_historico(lid, nombre, rutas)
        code      = 200 if resultado.get("status") == "ok" else 500
        return jsonify(resultado), code
    except Exception as e:
        return jsonify({"status": "error", "mensaje": str(e)}), 500