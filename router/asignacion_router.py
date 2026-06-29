"""
router/asignacion_router.py
Blueprint Flask para la Sección 3 — Asignación de Rutas.
"""
from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
from logic.historico_logic import generar_rutas_vrp_afinidad, obtener_reporte_vrp, resumen_historial, sugerir_vehiculos_optimos
from logic.asignacion_logic import (
    obtener_rutas,
    obtener_vehiculos,
    obtener_pesos,
    obtener_volumenes,
    obtener_config_dias,
    guardar_config_dias,
    guardar_asignacion,
    obtener_asignaciones_previas,
    calcular_tiempos_ruta,
    calcular_tiempos_multiples_rutas,
    consultar_osrm,
    generar_asignacion_optimizada,
    obtener_mayoristas_por_ruta,
    obtener_geometria_ruta,
    ejecutar_reacomodamiento,
)

asignacion_bp = Blueprint("asignacion", __name__)


def _json_o_400():
    datos = request.get_json(silent=True)
    if datos is None:
        return None, (jsonify({"status": "error", "mensaje": "Cuerpo JSON inválido o Content-Type incorrecto"}), 400)
    return datos, None


def _logistica_id() -> "str | None":
    return session.get("logistica_id")


def _requiere_logistica():
    lid = _logistica_id()
    if not lid:
        return None, (
            jsonify({"status": "error", "mensaje": "No hay logística activa. Selecciona una desde el menú."}),
            400,
        )
    return lid, None


# ── Página principal ───────────────────────────────────────────
@asignacion_bp.route("/", methods=["GET"])
def index():
    return render_template("asignacion/index.html")


@asignacion_bp.route("/<slug>", methods=["GET"])
def index_perfil(slug):
    """Ruta directa por perfil: /asignacion/<slug>. Activa el perfil y carga la sección."""
    from logic.menu_logic import obtener_logistica_por_slug, _slugify
    logistica = obtener_logistica_por_slug(slug)
    if not logistica:
        return redirect(url_for("menu.index"))
    slug_calc = logistica.get("slug") or _slugify(logistica["nombre"])
    session["logistica_id"]     = str(logistica["_id"])
    session["logistica_nombre"] = logistica["nombre"]
    session["logistica_slug"]   = slug_calc
    session["logistica_inicio"] = logistica.get("fecha_inicio", "")
    session["logistica_fin"]    = logistica.get("fecha_fin", "")
    return render_template("asignacion/index.html")


# ── Rutas de MongoDB ───────────────────────────────────────────
@asignacion_bp.route("/rutas", methods=["GET"])
def get_rutas():
    try:
        return jsonify(obtener_rutas())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Vehículos ──────────────────────────────────────────────────
@asignacion_bp.route("/vehiculos", methods=["GET"])
def get_vehiculos():
    try:
        return jsonify(obtener_vehiculos())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Pesos del consolidado ──────────────────────────────────────
@asignacion_bp.route("/pesos", methods=["GET"])
def get_pesos():
    lid, err = _requiere_logistica()
    if err:
        return err
    try:
        return jsonify(obtener_pesos(lid))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Volúmenes del consolidado ──────────────────────────────────
@asignacion_bp.route("/volumenes", methods=["GET"])
def get_volumenes():
    lid, err = _requiere_logistica()
    if err:
        return err
    try:
        return jsonify(obtener_volumenes(lid))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Cálculo de tiempos ─────────────────────────────────────────
@asignacion_bp.route("/calcular-tiempos", methods=["POST"])
def post_calcular_tiempos():
    lid, err = _requiere_logistica()
    if err:
        return err

    datos, err2 = _json_o_400()
    if err2:
        return err2

    rutas = datos.get("rutas")
    if not isinstance(rutas, list):
        return jsonify({"status": "error", "mensaje": "Se esperaba { rutas: [...] }"}), 400

    pesos = datos.get("pesos") or obtener_pesos(lid)
    try:
        return jsonify(calcular_tiempos_multiples_rutas(rutas, pesos, lid))
    except Exception as e:
        return jsonify({"status": "error", "mensaje": str(e)}), 500


@asignacion_bp.route("/calcular-tiempos/<ruta_id>", methods=["GET"])
def get_calcular_tiempos_ruta(ruta_id: str):
    lid, err = _requiere_logistica()
    if err:
        return err
    try:
        rutas = obtener_rutas()
        ruta  = next((r for r in rutas if str(r.get("_id")) == ruta_id), None)
        if not ruta:
            return jsonify({"error": "Ruta no encontrada"}), 404
        pesos     = obtener_pesos(lid)
        # Recalcular paradas integradas por cercanía para incluir mayoristas
        from logic.mayoristas_logic import calcular_distribucion_mayoristas
        dist = calcular_distribucion_mayoristas(lid, [ruta])
        paradas = dist.get("paradas_integradas", {}).get(ruta_id)
        resultado = calcular_tiempos_ruta(ruta, pesos, paradas=paradas)
        return jsonify(resultado)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Configuración de días ──────────────────────────────────────
@asignacion_bp.route("/config-dias", methods=["GET"])
def get_config_dias():
    lid = _logistica_id()
    return jsonify(obtener_config_dias(lid))


@asignacion_bp.route("/config-dias", methods=["POST"])
def post_config_dias():
    lid, err = _requiere_logistica()
    if err:
        return err
    datos, err2 = _json_o_400()
    if err2:
        return err2
    return jsonify(guardar_config_dias(datos, lid))


# ── Asignaciones previas ───────────────────────────────────────
@asignacion_bp.route("/asignaciones", methods=["GET"])
def get_asignaciones():
    lid = _logistica_id()
    return jsonify(obtener_asignaciones_previas(lid))


# ── Guardar asignación ─────────────────────────────────────────
@asignacion_bp.route("/guardar", methods=["POST"])
def post_guardar():
    lid, err = _requiere_logistica()
    if err:
        return err
    datos, err2 = _json_o_400()
    if err2:
        return err2
    resultado = guardar_asignacion(datos, lid)
    code = 200 if resultado.get("status") == "ok" else 500
    return jsonify(resultado), code


# ── NUEVA: Generar asignación optimizada ──────────────────────
@asignacion_bp.route("/generar-asignacion", methods=["POST"])
def post_generar_asignacion():
    """
    Recibe:
      {
        rutas:         [...],          // rutas seleccionadas
        vehiculos:     [...],          // flota disponible
        pesos:         { id: kg },     // pesos de la extracción
        config_dias:   { lunes: {...}, ... },
        ids_excluidos: ["id1", ...],   // entregadas / deseleccionadas
      }
    Retorna:
      {
        status: "ok",
        asignaciones: { ruta_id: { dia, placas, pct, peso_kg } },
        resumen_dias: { ... },
        total_rutas:  N,
        sin_vehiculo: M,
      }
    """
    lid, err = _requiere_logistica()
    if err:
        return err

    datos, err2 = _json_o_400()
    if err2:
        return err2

    try:
        resultado = generar_asignacion_optimizada(datos, lid)
        code = 200 if resultado.get("status") == "ok" else 422
        return jsonify(resultado), code
    except Exception as e:
        return jsonify({"status": "error", "mensaje": str(e)}), 500


# ── Mayoristas por ruta ───────────────────────────────────────
@asignacion_bp.route("/mayoristas-por-ruta", methods=["GET"])
def get_mayoristas_por_ruta():
    lid, err = _requiere_logistica()
    if err:
        return err
    try:
        return jsonify(obtener_mayoristas_por_ruta(lid))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Geometría de ruta (OSRM) ──────────────────────────────────
@asignacion_bp.route("/geometria-ruta/<ruta_id>", methods=["GET"])
def get_geometria_ruta(ruta_id: str):
    lid, err = _requiere_logistica()
    if err:
        return err
    try:
        return jsonify(obtener_geometria_ruta(ruta_id, lid))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Generar nombre de ruta con LLM ────────────────────────────
@asignacion_bp.route("/generar-nombre-ruta", methods=["POST"])
def post_generar_nombre_ruta():
    """
    Genera un nombre de ruta inteligente usando Groq + reglas locales.

    Body JSON:
        paradas           : list de {tipo, documento, orden}
        nombres_sucursales: list[str]

    Respuesta:
        {"nombre": "BB2822/35_ROYAN_3 VALLES"}
    """
    from logic.groq_logic import generar_nombre_ruta
    datos, err = _json_o_400()
    if err:
        return err
    try:
        nombre = generar_nombre_ruta(
            paradas=datos.get("paradas", []),
            nombres_sucursales=datos.get("nombres_sucursales", []),
        )
        return jsonify({"nombre": nombre})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Ejecutar reacomodamiento standalone ───────────────────────
@asignacion_bp.route("/reacomodamiento", methods=["POST"])
def post_reacomodamiento():
    """
    Ejecuta la Fase 3 (Reacomodamiento Lógico) sobre una asignación
    ya generada.  Útil para re-optimizar sin regenerar desde cero.

    Recibe:
      {
        asignaciones:  { ruta_id: { dia, placas, pct, peso_kg, ... } },
        rutas:         [...],
        vehiculos:     [...],
        pesos:         { id: kg },
        volumenes:     { id: m3 },
        config_dias:   { lunes: {...}, ... },
        util_min:      float,
        util_max:      float,
      }

    Retorna el mismo objeto asignaciones con los campos adicionales de
    reacomodamiento (tipo_ruta, cumple_tiempo, hora_llegada, …).
    """
    lid, err = _requiere_logistica()
    if err:
        return err

    datos, err2 = _json_o_400()
    if err2:
        return err2

    try:
        from logic.asignacion_logic import (
            _leer_config_volumen,
            DIAS_ORDEN,
        )

        asignaciones = datos.get("asignaciones", {})
        rutas_raw    = datos.get("rutas", [])
        vehiculos    = datos.get("vehiculos", [])
        pesos        = datos.get("pesos", {})
        volumenes    = datos.get("volumenes", {})
        config_dias  = datos.get("config_dias", {})
        util_min     = float(datos.get("util_min", 80))
        util_max     = float(datos.get("util_max", 100))

        estrategia_vol, factor_vol, _ = _leer_config_volumen()

        # CAP-4: sin tolerancia configurable. ejecutar_reacomodamiento() aplica
        # el tope fijo de 3.9 t para vehículos de 3.5-4 t y 100% para el resto.

        # Reconstruir estado_dias desde las asignaciones
        estado_dias = {}
        for ruta_id, asig in asignaciones.items():
            dia = asig.get("dia", "lunes")
            if dia not in estado_dias:
                estado_dias[dia] = {"vehiculos_usados": set(), "rutas": []}
            estado_dias[dia]["rutas"].append(ruta_id)
            if asig.get("placas"):
                estado_dias[dia]["vehiculos_usados"].add(asig["placas"])

        rutas_index = {str(r.get("_id", "")): r for r in rutas_raw}

        resultado = ejecutar_reacomodamiento(
            asignaciones   = asignaciones,
            estado_dias    = estado_dias,
            vehiculos_raw  = vehiculos,
            rutas_index    = rutas_index,
            pesos          = pesos,
            volumenes      = volumenes,
            config_dias    = config_dias,
            util_min       = util_min,
            util_max       = util_max,
            estrategia_vol = estrategia_vol,
            factor_vol     = factor_vol,
        )

        return jsonify({
            "status":          "ok",
            "asignaciones":    asignaciones,
            "reacomodamiento": resultado,
        })
    except Exception as e:
        return jsonify({"status": "error", "mensaje": str(e)}), 500


# ── VRP Híbrido desde historial ───────────────────────────────

@asignacion_bp.route("/generar-vrp-historico", methods=["POST"])
def post_generar_vrp_historico():
    """
    Genera rutas VRP híbridas usando el historial cargado + datos de extracción.
    No requiere cuerpo JSON — usa la logística activa de la sesión.

    Retorna:
      {
        status: "ok",
        total_rutas:      N,
        total_sucursales: M,
        n_historicos:     K,
        reporte:          [...],
        consolidaciones:  [...],
      }
    """
    lid, err = _requiere_logistica()
    if err:
        return err
    try:
        resultado = generar_rutas_vrp_afinidad(lid)
        code      = 200 if resultado.get("status") == "ok" else 422
        return jsonify(resultado), code
    except Exception as e:
        return jsonify({"status": "error", "mensaje": str(e)}), 500


@asignacion_bp.route("/reporte-vrp", methods=["GET"])
def get_reporte_vrp():
    """Devuelve el último reporte VRP generado para la logística activa."""
    lid = _logistica_id()
    if not lid:
        return jsonify({})
    try:
        return jsonify(obtener_reporte_vrp(lid))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@asignacion_bp.route("/sugerir-vehiculos", methods=["POST"])
def post_sugerir_vehiculos():
    """
    Sugiere el vehículo más rentable para cada ruta usando histórico + capacidad.

    Recibe:
      { "rutas": [{ "id": str, "dia": str, "peso_kg": float, "sucursales": [int] }] }

    Retorna:
      { route_id: { "placas": str, "abrev": str, "fuente": "historico"|"capacidad"|"ninguno", "pct": float } }
    """
    datos, err = _json_o_400()
    if err:
        return err
    routes_info = datos.get("rutas", [])
    if not isinstance(routes_info, list):
        return jsonify({"status": "error", "mensaje": "Se esperaba { rutas: [...] }"}), 400
    try:
        resultado = sugerir_vehiculos_optimos(routes_info)
        return jsonify({"status": "ok", "sugerencias": resultado})
    except Exception as e:
        return jsonify({"status": "error", "mensaje": str(e)}), 500


@asignacion_bp.route("/historial-disponible", methods=["GET"])
def get_historial_disponible():
    """Verifica si hay historial cargado y devuelve un resumen."""
    try:
        return jsonify(resumen_historial())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

