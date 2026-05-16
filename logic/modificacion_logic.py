"""
logic/modificacion_logic.py
Lógica de negocio para la Sección — Modificación manual de rutas.

Lee datos desde:
  - `asignaciones`           → vehículos y días asignados (guardados en Asignación)
  - `distribucion_mayoristas`→ mayoristas por ruta (orden, peso, coords)
  - `rutas_config`           → sucursales con coordenadas
  - `extraccion`             → pesos de sucursales
  - `vehiculos`              → flota activa
Guarda en:
  - `modificaciones_rutas`   (con logistica_id)

No se usan archivos JSON ni las colecciones validaciones/reordenamientos.
"""
import math
import time
import urllib.request
import urllib.error
from datetime import datetime
from bson import ObjectId
from bson.errors import InvalidId

from db import get_db
from logic.mayoristas_logic import calcular_distribucion_mayoristas, _integrar_paradas

# ── Constantes ────────────────────────────────────────────────
MIN_DESCARGA_POR_KG        = 0.1
MAX_DESCARGA_POR_SUCURSAL  = 120   # tope individual por sucursal (min)
HORAS_EXTRA_RUTA_MIN       = 120   # 2 h adicionales al total de la ruta
MATRIZ_LAT_DEFAULT   = 18.87329315661368
MATRIZ_LON_DEFAULT   = -96.9491574270346

OSRM_BASE_URL    = "https://router.project-osrm.org/route/v1/driving"
OSRM_TIMEOUT     = 20
OSRM_MAX_RETRIES = 3
OSRM_RETRY_DELAY = 1.5

ORDEN_DIAS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]


def _parse_oid(doc_id: str) -> ObjectId | None:
    try:
        return ObjectId(doc_id)
    except (InvalidId, TypeError):
        return None


def _serialize(doc: dict) -> dict:
    doc = dict(doc)
    if "_id" in doc and isinstance(doc["_id"], ObjectId):
        doc["_id"] = str(doc["_id"])
    return doc


def _obtener_config_general() -> dict:
    try:
        db = get_db()
        return db["configuracion"].find_one({"_tipo": {"$exists": False}}) or {}
    except Exception:
        return {}


def _aplicar_overrides_mayoristas(dist: dict, overrides: dict, sucursales_por_ruta: dict) -> dict:
    """
    Aplica los overrides manuales al resultado de calcular_distribucion_mayoristas.
    overrides = { ruta_id: { excluidos: [id_cl,...], incluidos: [id_cl,...] } }
    """
    if not overrides:
        return dist

    mayoristas_por_ruta = {k: list(v) for k, v in dist.get("mayoristas_por_ruta", {}).items()}
    todos = (
        dist.get("todos_mayoristas", [])
        + dist.get("sin_asignar", [])
        + dist.get("sin_coords", [])
    )
    todos_may_index = {int(m.get("id_cliente", 0)): m for m in todos}

    rutas_afectadas: set = set()

    for ruta_id, ov in overrides.items():
        excluidos = {int(x) for x in ov.get("excluidos", [])}
        incluidos = {int(x) for x in ov.get("incluidos", [])}

        if excluidos:
            antes = mayoristas_por_ruta.get(ruta_id, [])
            despues = [m for m in antes if int(m.get("id_cliente", 0)) not in excluidos]
            if len(despues) != len(antes):
                mayoristas_por_ruta[ruta_id] = despues
                rutas_afectadas.add(ruta_id)

        if incluidos:
            for other_rid in list(mayoristas_por_ruta.keys()):
                if other_rid == ruta_id:
                    continue
                prev = mayoristas_por_ruta[other_rid]
                nuevos = [m for m in prev if int(m.get("id_cliente", 0)) not in incluidos]
                if len(nuevos) != len(prev):
                    mayoristas_por_ruta[other_rid] = nuevos
                    rutas_afectadas.add(other_rid)
            existing_ids = {int(m.get("id_cliente", 0)) for m in mayoristas_por_ruta.get(ruta_id, [])}
            for id_cl in incluidos:
                if id_cl not in existing_ids and id_cl in todos_may_index:
                    mayoristas_por_ruta.setdefault(ruta_id, []).append(dict(todos_may_index[id_cl]))
                    rutas_afectadas.add(ruta_id)

    paradas_integradas = dict(dist.get("paradas_integradas", {}))
    orden_sucursales = dict(dist.get("orden_sucursales", {}))

    for ruta_id in rutas_afectadas:
        sucs_raw = sucursales_por_ruta.get(ruta_id, [])
        sucs = sorted(sucs_raw, key=lambda s: int(s.get("orden") or 9999))
        mays = mayoristas_por_ruta.get(ruta_id, [])
        paradas = _integrar_paradas(sucs, mays)
        paradas_integradas[ruta_id] = paradas

        orden_map: dict = {}
        for p in paradas:
            if p.get("tipo") != "sucursal":
                continue
            nt = p.get("num_tienda")
            if nt is not None:
                orden_map[str(nt)] = p.get("orden")
        orden_sucursales[ruta_id] = orden_map

        orden_may: dict = {}
        for p in paradas:
            if p.get("tipo") != "mayorista":
                continue
            id_cl = p.get("id_cliente")
            if id_cl is not None:
                orden_may[int(id_cl)] = p.get("orden")
        for m in mays:
            id_cl = m.get("id_cliente")
            if id_cl is not None and int(id_cl) in orden_may:
                m["orden"] = orden_may[int(id_cl)]

    dist = dict(dist)
    dist["mayoristas_por_ruta"] = mayoristas_por_ruta
    dist["paradas_integradas"] = paradas_integradas
    dist["orden_sucursales"] = orden_sucursales
    return dist




# ── OSRM con reintentos y geometría ───────────────────────────

def consultar_osrm_con_reintentos(coords: list) -> dict:
    if len(coords) < 2:
        return {"distancia_km": 0.0, "traslado_min": 0.0, "origen": "osrm", "geometry": []}

    waypoints = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in coords)
    url = f"{OSRM_BASE_URL}/{waypoints}?overview=full&geometries=geojson"
    ultimo_error = None

    for intento in range(OSRM_MAX_RETRIES):
        try:
            if intento > 0:
                time.sleep(OSRM_RETRY_DELAY)
            import json as _json
            req = urllib.request.Request(url, headers={
                "User-Agent": "ICG-RouteModification/1.0",
                "Accept": "application/json",
            })
            with urllib.request.urlopen(req, timeout=OSRM_TIMEOUT) as resp:
                data = _json.loads(resp.read().decode("utf-8"))

            if data.get("code") != "Ok" or not data.get("routes"):
                ultimo_error = f"OSRM: {data.get('code', '?')}"
                continue

            ruta     = data["routes"][0]
            geometry = ruta.get("geometry", {}).get("coordinates", [])
            return {
                "distancia_km": round(ruta.get("distance", 0) / 1000, 2),
                "traslado_min": round(ruta.get("duration", 0) / 60, 1),
                "origen":       "osrm",
                "geometry":     geometry,
            }
        except urllib.error.HTTPError as e:
            ultimo_error = f"HTTP {e.code}"
            if e.code == 429:
                time.sleep(OSRM_RETRY_DELAY * 2)
        except Exception as e:
            ultimo_error = str(e)

    return {"error": ultimo_error or "Agotados reintentos", "origen": "osrm_error"}


def _haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _fallback_haversine(coords: list, velocidad_kmh: float = 35.0) -> dict:
    dist = sum(
        _haversine_km(coords[i][0], coords[i][1], coords[i+1][0], coords[i+1][1])
        for i in range(len(coords) - 1)
    )
    dist_via = dist * 1.35
    return {
        "distancia_km": round(dist_via, 2),
        "traslado_min": round((dist_via / velocidad_kmh) * 60, 1),
        "origen": "haversine_fallback",
        "geometry": [],
    }


# ── Helpers de lectura MongoDB ─────────────────────────────────

def _obtener_rutas_db() -> dict:
    result = {}
    try:
        db = get_db()
        for ruta in db["rutas_config"].find({}):
            ruta_s = _serialize(ruta)
            result[ruta_s["_id"]] = ruta_s
    except Exception as e:
        print(f"[_obtener_rutas_db] Error: {e}")
    return result


def _obtener_coordenadas_sucursales() -> dict:
    coords = {}
    try:
        db = get_db()
        for ruta in db["rutas_config"].find({}):
            for suc in ruta.get("sucursales", []):
                nt  = str(suc.get("num_tienda", ""))
                lat = suc.get("latitud")
                lon = suc.get("longitud")
                if nt and lat is not None and lon is not None:
                    coords[nt] = {"latitud": float(lat), "longitud": float(lon)}
    except Exception as e:
        print(f"[_obtener_coordenadas_sucursales] Error: {e}")
    return coords


def _obtener_nombres_sucursales() -> dict:
    nombres = {}
    try:
        db = get_db()
        for ruta in db["rutas_config"].find({}):
            for suc in ruta.get("sucursales", []):
                nt = str(suc.get("num_tienda", ""))
                if nt and nt not in nombres:
                    nombres[nt] = (
                        suc.get("nombre_base")
                        or suc.get("nombre_tienda")
                        or suc.get("nombre_pedido")
                        or suc.get("nombre")
                        or ""
                    )
    except Exception as e:
        print(f"[_obtener_nombres_sucursales] Error: {e}")
    return nombres


def obtener_sucursales_disponibles() -> list:
    sucursales = {}
    try:
        db = get_db()
        for ruta in db["rutas_config"].find({}):
            for suc in ruta.get("sucursales", []):
                nt = str(suc.get("num_tienda", ""))
                if nt and nt not in sucursales:
                    sucursales[nt] = {
                        "num_tienda": suc.get("num_tienda"),
                        "nombre": (
                            suc.get("nombre_base")
                            or suc.get("nombre_tienda")
                            or suc.get("nombre_pedido")
                            or suc.get("nombre", "")
                        ),
                        "latitud":  suc.get("latitud"),
                        "longitud": suc.get("longitud"),
                    }
    except Exception as e:
        print(f"[obtener_sucursales_disponibles] Error: {e}")
    return list(sucursales.values())


def obtener_pesos(logistica_id: str) -> dict:
    """Lee los pesos desde la colección `extraccion` para la logística activa."""
    oid = _parse_oid(logistica_id)
    if not oid:
        return {}
    try:
        db  = get_db()
        doc = db["extraccion"].find_one({"logistica_id": oid})
        if not doc:
            return {}
        data  = doc.get("datos", {})
        pesos = {}
        for nombre, valores in data.items():
            id_suc = valores.get("id_sucursal")
            peso   = valores.get("total_kg", 0)
            if id_suc is not None:
                pesos[str(id_suc)] = float(peso)
        return pesos
    except Exception as e:
        print(f"[obtener_pesos modificacion] Error: {e}")
        return {}


def _vehiculo_serializar(v: dict) -> dict:
    """Normaliza un documento de la colección `vehiculos` a los campos usados en la app."""
    cap = v.get("capacidad_toneladas") or 0
    return {
        "_id":           str(v["_id"]),
        "placas":        v.get("placas", ""),
        # abreviatura es el campo canónico; descripcion como fallback
        "abrev":         v.get("abreviatura", "") or v.get("descripcion", ""),
        "descripcion":   v.get("descripcion", ""),
        "chofer":        v.get("chofer", "") or "",
        "capacidad_ton": float(cap),
        "volumen_m3":    float(v.get("volumen_m3") or 0),
        "tipo":          v.get("categoria", "") or "",
    }


def obtener_vehiculos() -> list:
    """Devuelve la flota activa de vehículos (sin datos de ocupación)."""
    try:
        db   = get_db()
        docs = list(db["vehiculos"].find({"activo": True}))
        return [_vehiculo_serializar(v) for v in docs]
    except Exception as e:
        print(f"[obtener_vehiculos modificacion] Error: {e}")
        return []


def obtener_disponibilidad_vehiculos(logistica_id: str) -> list:
    """
    Devuelve la flota activa enriquecida con ocupación por día, leída
    directamente desde `asignaciones.detalle_por_dia` en MongoDB.

    Campos de ocupación por vehículo:
      · ocupacion   : { dia → { ruta_id, ruta_nombre } }
      · dias_ocupados: [días en que tiene ruta asignada]
      · dias_libres  : [días hábiles L-S sin ruta]
      · pct_semana  : % de días hábiles ocupados (sobre 6 días L-S)
    """
    oid = _parse_oid(logistica_id)
    db  = get_db()

    # ── 1. Flota activa indexada por placas ───────────────────
    vehiculos: dict = {}
    try:
        for v in db["vehiculos"].find({"activo": True}):
            placas = v.get("placas", "")
            if placas:
                vd = _vehiculo_serializar(v)
                vd["ocupacion"] = {}   # { dia: { ruta_id, ruta_nombre } }
                vehiculos[placas] = vd
    except Exception as e:
        print(f"[obtener_disponibilidad_vehiculos] Error al leer vehículos: {e}")

    # ── 2. Ocupación desde asignaciones ───────────────────────
    if oid:
        try:
            doc_asig = db["asignaciones"].find_one({"logistica_id": oid})
            if doc_asig:
                detalle_por_dia = doc_asig.get("detalle_por_dia", {})
                for dia, rutas_del_dia in detalle_por_dia.items():
                    if not isinstance(rutas_del_dia, dict):
                        continue
                    for ruta_id, det in rutas_del_dia.items():
                        placas = det.get("vehiculo_placas") or ""
                        if placas and placas in vehiculos:
                            vehiculos[placas]["ocupacion"][dia] = {
                                "ruta_id":     ruta_id,
                                "ruta_nombre": det.get("nombre_ruta") or ruta_id,
                            }
        except Exception as e:
            print(f"[obtener_disponibilidad_vehiculos] Error al leer asignaciones: {e}")

    # ── 3. Calcular métricas por vehículo ─────────────────────
    dias_habiles = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado"]
    resultado: list = []
    for v in vehiculos.values():
        ocu        = v["ocupacion"]
        dias_ocu   = [d for d in ORDEN_DIAS if d in ocu]
        dias_libres = [d for d in dias_habiles if d not in ocu]
        ocupados_habiles = sum(1 for d in dias_habiles if d in ocu)
        pct_semana = round(ocupados_habiles / len(dias_habiles) * 100, 1)
        resultado.append({
            **v,
            "dias_ocupados": dias_ocu,
            "dias_libres":   dias_libres,
            "pct_semana":    pct_semana,
        })

    # Ordenar: primero los que tienen más días libres (más disponibles)
    resultado.sort(key=lambda x: (x["pct_semana"], x["abrev"]))
    return resultado


# ═══════════════════════════════════════════════════════════════
# Carga y normalización de rutas desde asignaciones
# ═══════════════════════════════════════════════════════════════

def obtener_rutas_para_modificar(logistica_id: str) -> dict:
    """
    Lee desde la colección `asignaciones` el estado guardado en Asignación.

    Estructura de asignaciones.detalle_por_dia (guardado desde asignacion.js):
        { dia: { ruta_id: { nombre_ruta, vehiculo_placas, vehiculo_abreviatura,
                            capacidad_ton, peso_total_kg, porcentaje_utilizacion,
                            hora_salida, hora_regreso_estimada, cumple_horario,
                            sucursales: [{num_tienda, nombre, orden, peso_kg, ...}]
                            (sin latitud/longitud — se toman de rutas_config)
                          } } }

    Combina sucursales (coordenadas de rutas_config) y mayoristas
    (distribucion_mayoristas), respetando el orden original de MongoDB.
    """
    oid = _parse_oid(logistica_id)
    if not oid:
        return {"status": "error", "mensaje": "logistica_id inválido."}

    cfg          = _obtener_config_general()
    min_descarga = float(cfg.get("min_descarga_por_kg") or MIN_DESCARGA_POR_KG)

    db = get_db()

    # ── 1. Leer documento de asignación ───────────────────────
    doc_asig = db["asignaciones"].find_one({"logistica_id": oid})
    if not doc_asig:
        return {
            "status":  "error",
            "mensaje": "No se encontró asignación guardada para esta logística. "
                       "Completa y guarda la sección de Asignación primero.",
        }

    # detalle_por_dia = { dia: { ruta_id: { ... } } }  ← dict anidado, NO lista
    detalle_por_dia = doc_asig.get("detalle_por_dia", {})

    # ── 2. Datos de rutas y coordenadas desde rutas_config ────
    coords_map = _obtener_coordenadas_sucursales()   # { num_tienda_str: {latitud, longitud} }
    rutas_db   = _obtener_rutas_db()                 # { ruta_id_str: doc }

    # Mapa de coordenadas por sucursal desglosado por ruta (más preciso que coords_map)
    # { ruta_id_str: { num_tienda_str: (lat, lon) } }
    coords_por_ruta: dict = {}
    try:
        db_tmp = get_db()
        for rdoc in db_tmp["rutas_config"].find({}, {"_id": 1, "sucursales": 1}):
            rid_key = str(rdoc["_id"])
            cmap: dict = {}
            for suc in rdoc.get("sucursales", []):
                nt  = str(suc.get("num_tienda", ""))
                lat = suc.get("latitud")
                lon = suc.get("longitud")
                if nt and lat is not None and lon is not None:
                    cmap[nt] = (float(lat), float(lon))
            if cmap:
                coords_por_ruta[rid_key] = cmap
    except Exception as e:
        print(f"[obtener_rutas_para_modificar] coords_por_ruta error: {e}")

    # ── 3. Preconstruir sucursales para cálculo de mayoristas ───
    # Mapa de nombres canónicos desde rutas_config (fuente de verdad)
    nombres_map = _obtener_nombres_sucursales()   # { num_tienda_str: nombre }

    sucursales_por_ruta: dict = {}
    meta_por_ruta: dict = {}
    procesadas: set = set()

    for dia, rutas_del_dia in detalle_por_dia.items():
        if not isinstance(rutas_del_dia, dict):
            continue

        for ruta_id, det in rutas_del_dia.items():
            if not ruta_id or ruta_id in procesadas:
                continue
            procesadas.add(ruta_id)

            # Campos del detalle (nombres exactos guardados por asignacion.js)
            placas       = det.get("vehiculo_placas")        or ""
            veh_abrev    = det.get("vehiculo_abreviatura")   or ""
            cap_ton      = det.get("capacidad_ton")
            peso_kg      = float(det.get("peso_total_kg")    or 0)
            pct          = float(det.get("porcentaje_utilizacion") or 0)
            hora_salida  = det.get("hora_salida")            or "08:00"
            hora_regreso = det.get("hora_regreso_estimada")  or ""
            cumple_h     = det.get("cumple_horario")
            if cumple_h is None:
                cumple_h = True
            nombre_r     = (
                det.get("nombre_ruta")
                or rutas_db.get(ruta_id, {}).get("nombre", ruta_id)
            )

            # Sucursales: guardadas sin coordenadas → agregar desde rutas_config
            sucursales_guardadas = det.get("sucursales") or []
            cmap_ruta            = coords_por_ruta.get(ruta_id, {})

            sucursales_norm: list = []
            for i, suc in enumerate(sucursales_guardadas):
                nt    = str(suc.get("num_tienda", ""))
                # Buscar coordenadas: 1° en el mapa de la ruta, 2° en el mapa global
                if nt in cmap_ruta:
                    lat, lon = cmap_ruta[nt]
                elif nt in coords_map:
                    lat = coords_map[nt]["latitud"]
                    lon = coords_map[nt]["longitud"]
                else:
                    lat, lon = None, None

                peso_suc  = float(suc.get("peso_kg", 0))
                orden_suc = suc.get("orden", i + 1)
                nombre_raw = suc.get("nombre") or ""
                nombre_suc = nombre_raw if (nombre_raw and nombre_raw != "Sucursal") \
                             else nombres_map.get(nt, "")
                sucursales_norm.append({
                    "tipo":         "sucursal",
                    "num_tienda":   suc.get("num_tienda"),
                    "nombre":       nombre_suc,
                    "orden":        orden_suc,
                    "peso_kg":      peso_suc,
                    "descarga_min": round(min(peso_suc * min_descarga, MAX_DESCARGA_POR_SUCURSAL), 1),
                    "latitud":      float(lat) if lat is not None else None,
                    "longitud":     float(lon) if lon is not None else None,
                })

            sucursales_por_ruta[ruta_id] = sucursales_norm
            meta_por_ruta[ruta_id] = {
                "dia":          dia,
                "placas":       placas,
                "veh_abrev":    veh_abrev,
                "cap_ton":      cap_ton,
                "peso_kg":      peso_kg,
                "pct":          pct,
                "hora_salida":  hora_salida,
                "hora_regreso": hora_regreso,
                "cumple_h":     cumple_h,
                "nombre_r":     nombre_r,
            }

    dist = calcular_distribucion_mayoristas(
        logistica_id,
        [
            {"_id": rid, "sucursales": sucs}
            for rid, sucs in sucursales_por_ruta.items()
        ],
    )
    overrides = doc_asig.get("mayoristas_overrides", {})
    if overrides:
        dist = _aplicar_overrides_mayoristas(dist, overrides, sucursales_por_ruta)
    mayoristas_por_ruta: dict = dist.get("mayoristas_por_ruta", {})
    orden_sucursales: dict = dist.get("orden_sucursales", {})
    mayoristas_disponibles: list = dist.get("todos_mayoristas", [])

    # ── 4. Construir lista de rutas desde detalle_por_dia ─────
    rutas_normalizadas: list = []

    for ruta_id, meta in meta_por_ruta.items():
        sucursales_norm = sucursales_por_ruta.get(ruta_id, [])
        orden_map = orden_sucursales.get(ruta_id, {})
        for s in sucursales_norm:
            nt = s.get("num_tienda")
            if nt is None:
                continue
            orden = orden_map.get(str(nt))
            if orden is not None:
                s["orden"] = orden

        mayoristas_norm = mayoristas_por_ruta.get(ruta_id, [])

        meta = meta_por_ruta.get(ruta_id, {})
        placas       = meta.get("placas", "")
        veh_abrev    = meta.get("veh_abrev", "")
        cap_ton      = meta.get("cap_ton")
        peso_kg      = float(meta.get("peso_kg") or 0)
        pct          = float(meta.get("pct") or 0)
        hora_salida  = meta.get("hora_salida", "08:00")
        hora_regreso = meta.get("hora_regreso", "")
        cumple_h     = meta.get("cumple_h", True)
        nombre_r     = meta.get("nombre_r", ruta_id)
        dia          = meta.get("dia", "")

        # Calcular peso si vino en cero o si faltan mayoristas
        peso_calc = (
            sum(s["peso_kg"] for s in sucursales_norm)
            + sum(m.get("peso_kg", 0) for m in mayoristas_norm)
        )
        if not peso_kg or abs(peso_kg - peso_calc) > 0.1:
            peso_kg = peso_calc

        if cap_ton and cap_ton > 0:
            pct = round((peso_kg / 1000 / cap_ton) * 100, 1)

        con_coords = sum(1 for s in sucursales_norm if s["latitud"] is not None)

        rutas_normalizadas.append({
            "id":                    ruta_id,
            "nombre":                nombre_r,
            "tipo":                  "asignada",
            "dia":                   dia,
            "vehiculo_placas":       placas,
            "vehiculo_abrev":        veh_abrev,
            "capacidad_ton":         cap_ton,
            "peso_kg":               peso_kg,
            "pct_utilizacion":       pct,
            "cumple_peso":           True,
            "hora_salida":           hora_salida,
            "hora_regreso":          hora_regreso,
            "cumple_horario":        cumple_h,
            "conduccion_min":        0,
            "descarga_min":          0,
            "extra_min":             HORAS_EXTRA_RUTA_MIN,
            "total_min":             0,
            "distancia_km":          0,
            "origen_tiempo":         "pendiente",
            "num_sucursales":        len(sucursales_norm),
            "sucursales_con_coords": con_coords,
            "sucursales":            sucursales_norm,
            "mayoristas":            mayoristas_norm,
        })

    if not rutas_normalizadas:
        return {
            "status":  "error",
            "mensaje": "La asignación existe pero no contiene rutas programadas. "
                       "Genera la asignación automática o asigna días manualmente y guarda.",
        }

    rutas_normalizadas.sort(key=lambda r: (
        ORDEN_DIAS.index(r["dia"].lower()) if r.get("dia") and r["dia"].lower() in ORDEN_DIAS else 99,
        r["nombre"],
    ))

    sucursales_pendientes = doc_asig.get("sucursales_pendientes", [])
    rutas_confirmadas     = doc_asig.get("rutas_confirmadas", [])

    return {
        "status":                "ok",
        "logistica_id":          str(logistica_id),
        "total_rutas":           len(rutas_normalizadas),
        "rutas":                 rutas_normalizadas,
        "mayoristas_disponibles": mayoristas_disponibles,
        "sucursales_pendientes": sucursales_pendientes,
        "rutas_confirmadas":     rutas_confirmadas,
    }


# ── Cálculo de tiempos con caché MongoDB ──────────────────────

def calcular_tiempos_subruta(paradas: list, pesos: dict, hora_salida: str = "08:00") -> dict:
    """
    Calcula tiempos OSRM reales para una secuencia de paradas
    (sucursales y/o mayoristas). Siempre consulta la API directamente.
    """
    cfg          = _obtener_config_general()
    matriz_lat   = MATRIZ_LAT_DEFAULT
    matriz_lon   = MATRIZ_LON_DEFAULT
    min_descarga = float(cfg.get("min_descarga_por_kg") or MIN_DESCARGA_POR_KG)
    velocidad    = float(cfg.get("velocidad_kmh")       or 35.0)

    coords = [(matriz_lat, matriz_lon)]
    for p in paradas:
        lat = p.get("latitud")
        lon = p.get("longitud")
        if lat is not None and lon is not None:
            coords.append((float(lat), float(lon)))

    if len(coords) < 2:
        return {
            "traslado_min": 0, "descarga_min": 0, "extra_min": HORAS_EXTRA_RUTA_MIN,
            "total_min": 0, "distancia_km": 0,
            "origen_tiempo": "sin_coordenadas", "geometry": [],
            "matriz": [matriz_lat, matriz_lon], "hora_regreso": "—",
        }

    coords.append((matriz_lat, matriz_lon))
    resultado = consultar_osrm_con_reintentos(coords)
    if "error" in resultado:
        resultado = _fallback_haversine(coords, velocidad)

    def _peso_descarga(p: dict) -> float:
        if p.get("tipo") == "mayorista" or (p.get("id_cliente") is not None and p.get("num_tienda") is None):
            return float(p.get("peso_kg") or 0)
        return float(pesos.get(str(p.get("num_tienda", "")), p.get("peso_kg") or 0) or 0)

    descarga = sum(
        min(_peso_descarga(p) * min_descarga, MAX_DESCARGA_POR_SUCURSAL)
        for p in paradas
    )
    traslado = resultado.get("traslado_min", 0)
    total    = traslado + descarga + HORAS_EXTRA_RUTA_MIN

    try:
        h_s, m_s = hora_salida.split(":")
        hora_salida_min = int(h_s) * 60 + int(m_s)
    except Exception:
        hora_salida_min = 8 * 60
    regreso_min = hora_salida_min + total
    h_reg = int(regreso_min // 60)
    m_reg = int(round(regreso_min % 60))
    if m_reg >= 60:
        h_reg += 1
        m_reg -= 60
    hora_regreso = f"{h_reg:02d}:{m_reg:02d}"

    return {
        "traslado_min":  round(traslado, 1),
        "descarga_min":  round(descarga, 1),
        "extra_min":     HORAS_EXTRA_RUTA_MIN,
        "total_min":     round(total, 1),
        "distancia_km":  resultado.get("distancia_km", 0),
        "origen_tiempo": resultado.get("origen", "desconocido"),
        "geometry":      resultado.get("geometry", []),
        "hora_regreso":  hora_regreso,
        "matriz":        [matriz_lat, matriz_lon],
    }


def calcular_tiempos_lote(rutas: list, pesos: dict, delay: float = 1.2) -> dict:
    """Calcula tiempos OSRM para múltiples rutas con delay entre llamadas."""
    resultados = {}
    cfg        = _obtener_config_general()
    matriz_lat = MATRIZ_LAT_DEFAULT
    matriz_lon = MATRIZ_LON_DEFAULT

    for ruta in rutas:
        ruta_id = ruta.get("id", "")
        # Combinar sucursales y mayoristas para el cálculo OSRM
        paradas = list(ruta.get("sucursales", [])) + list(ruta.get("mayoristas", []))

        if not paradas:
            resultados[ruta_id] = {
                "traslado_min": 0, "descarga_min": 0, "extra_min": HORAS_EXTRA_RUTA_MIN,
                "total_min": 0, "distancia_km": 0,
                "origen_tiempo": "sin_sucursales", "geometry": [],
                "hora_regreso": "—", "matriz": [matriz_lat, matriz_lon],
            }
            continue

        coords = [(matriz_lat, matriz_lon)]
        for p in paradas:
            lat = p.get("latitud")
            lon = p.get("longitud")
            if lat is not None and lon is not None:
                coords.append((float(lat), float(lon)))
        coords.append((matriz_lat, matriz_lon))

        if resultados:
            time.sleep(delay)

        resultados[ruta_id] = calcular_tiempos_subruta(paradas, pesos)

    return resultados


# ── Crear / eliminar rutas manuales ──────────────────────────

def crear_ruta_manual(
    logistica_id: str,
    dia: str,
    vehiculo_placas: str,
    sucursales: list,
    nombre_ruta: str | None = None,
) -> dict:
    oid = _parse_oid(logistica_id)
    if not oid:
        return {"status": "error", "mensaje": "logistica_id inválido."}

    dia_key = (dia or "").strip().lower()
    if dia_key not in ORDEN_DIAS:
        return {"status": "error", "mensaje": "Día inválido."}

    placas = (vehiculo_placas or "").strip()
    if not placas:
        return {"status": "error", "mensaje": "Se requiere vehículo."}

    if not isinstance(sucursales, list) or not sucursales:
        return {"status": "error", "mensaje": "Se requiere al menos una sucursal."}

    db = get_db()
    doc_asig = db["asignaciones"].find_one({"logistica_id": oid})
    if not doc_asig:
        return {
            "status": "error",
            "mensaje": "No se encontró asignación guardada para esta logística.",
        }

    veh = db["vehiculos"].find_one({"placas": placas, "activo": True})
    if not veh:
        return {"status": "error", "mensaje": "Vehículo no encontrado o inactivo."}

    nombres = _obtener_nombres_sucursales()
    pesos_map = obtener_pesos(logistica_id)

    suc_norm = []
    seen = set()
    for s in sucursales:
        if isinstance(s, dict):
            nt = s.get("num_tienda") or s.get("id_sucursal") or s.get("num")
            nombre = s.get("nombre") or ""
        else:
            nt = s
            nombre = ""
        try:
            nt_int = int(nt)
        except (TypeError, ValueError):
            continue
        if nt_int in seen:
            continue
        seen.add(nt_int)
        if not nombre:
            nombre = nombres.get(str(nt_int), "")
        suc_norm.append({
            "num_tienda": nt_int,
            "nombre": nombre or f"Sucursal {nt_int}",
        })

    if not suc_norm:
        return {"status": "error", "mensaje": "No se pudieron validar las sucursales."}

    ruta_id = f"manual_{ObjectId()}"
    abrev = veh.get("abreviatura") or veh.get("descripcion") or placas
    cap_ton = float(veh.get("capacidad_toneladas") or 0)

    cfg = _obtener_config_general()
    min_descarga = float(cfg.get("min_descarga_por_kg") or MIN_DESCARGA_POR_KG)

    suc_det = []
    peso_total_kg = 0.0
    for i, s in enumerate(suc_norm, start=1):
        peso_kg = float(pesos_map.get(str(s["num_tienda"]), 0))
        peso_total_kg += peso_kg
        suc_det.append({
            "num_tienda": s["num_tienda"],
            "nombre": s["nombre"],
            "orden": i,
            "peso_kg": peso_kg,
            "descarga_min": round(min(peso_kg * min_descarga, MAX_DESCARGA_POR_SUCURSAL), 1),
        })

    pct = round((peso_total_kg / 1000 / cap_ton) * 100, 1) if cap_ton > 0 else 0
    nombre_ruta = (nombre_ruta or "").strip() or f"{abrev} — {dia_key.capitalize()} (manual)"

    detalle_por_dia = doc_asig.get("detalle_por_dia", {})
    if dia_key not in detalle_por_dia:
        detalle_por_dia[dia_key] = {}

    detalle_por_dia[dia_key][ruta_id] = {
        "nombre_ruta":            nombre_ruta,
        "vehiculo_placas":        placas,
        "vehiculo_abreviatura":   abrev,
        "capacidad_ton":          cap_ton,
        "peso_total_kg":          peso_total_kg,
        "porcentaje_utilizacion": pct,
        "hora_salida":            "08:00",
        "hora_regreso_estimada":  "",
        "cumple_horario":         True,
        "sucursales":             suc_det,
    }

    pendientes = [
        p for p in doc_asig.get("sucursales_pendientes", [])
        if int(p.get("num_tienda", -1)) not in seen
    ]

    db["asignaciones"].update_one(
        {"logistica_id": oid},
        {"$set": {
            "detalle_por_dia": detalle_por_dia,
            "sucursales_pendientes": pendientes,
        }},
    )

    coords_map = _obtener_coordenadas_sucursales()
    con_coords = 0
    suc_ui = []
    for s in suc_det:
        nt_str = str(s["num_tienda"])
        coord = coords_map.get(nt_str, {})
        lat = coord.get("latitud")
        lon = coord.get("longitud")
        if lat is not None and lon is not None:
            con_coords += 1
        suc_ui.append({
            "tipo":         "sucursal",
            "num_tienda":   s["num_tienda"],
            "nombre":       s["nombre"],
            "orden":        s["orden"],
            "peso_kg":      s["peso_kg"],
            "descarga_min": s["descarga_min"],
            "latitud":      float(lat) if lat is not None else None,
            "longitud":     float(lon) if lon is not None else None,
        })

    ruta_ui = {
        "id":                    ruta_id,
        "nombre":                nombre_ruta,
        "tipo":                  "manual",
        "dia":                   dia_key,
        "vehiculo_placas":       placas,
        "vehiculo_abrev":        abrev,
        "capacidad_ton":         cap_ton,
        "peso_kg":               peso_total_kg,
        "pct_utilizacion":       pct,
        "cumple_peso":           True,
        "hora_salida":           "08:00",
        "hora_regreso":          "",
        "cumple_horario":        True,
        "conduccion_min":        0,
        "descarga_min":          0,
        "extra_min":             HORAS_EXTRA_RUTA_MIN,
        "total_min":             0,
        "distancia_km":          0,
        "origen_tiempo":         "pendiente",
        "num_sucursales":        len(suc_ui),
        "sucursales_con_coords": con_coords,
        "sucursales":            suc_ui,
        "mayoristas":            [],
    }

    return {
        "status": "ok",
        "ruta": ruta_ui,
        "sucursales_pendientes": pendientes,
    }


def eliminar_ruta_manual(logistica_id: str, ruta_id: str, dia: str) -> dict:
    oid = _parse_oid(logistica_id)
    if not oid:
        return {"status": "error", "mensaje": "logistica_id inválido."}

    dia_key = (dia or "").strip().lower()
    if dia_key not in ORDEN_DIAS:
        return {"status": "error", "mensaje": "Día inválido."}

    db = get_db()
    doc_asig = db["asignaciones"].find_one({"logistica_id": oid})
    if not doc_asig:
        return {"status": "error", "mensaje": "No se encontró la asignación."}

    detalle_por_dia = doc_asig.get("detalle_por_dia", {})
    ruta_det = detalle_por_dia.get(dia_key, {}).get(ruta_id)
    if not ruta_det:
        return {"status": "error", "mensaje": "Ruta no encontrada."}

    coords_map = _obtener_coordenadas_sucursales()
    pendientes_map = {
        int(p.get("num_tienda", -1)): p
        for p in doc_asig.get("sucursales_pendientes", [])
        if p.get("num_tienda") is not None
    }

    for s in ruta_det.get("sucursales", []):
        try:
            nt = int(s.get("num_tienda"))
        except (TypeError, ValueError):
            continue
        if nt in pendientes_map:
            continue
        coord = coords_map.get(str(nt), {})
        pendientes_map[nt] = {
            "num_tienda": nt,
            "nombre":     s.get("nombre") or f"Sucursal {nt}",
            "latitud":    coord.get("latitud"),
            "longitud":   coord.get("longitud"),
            "peso_kg":    float(s.get("peso_kg") or 0),
        }

    if dia_key in detalle_por_dia and ruta_id in detalle_por_dia[dia_key]:
        detalle_por_dia[dia_key].pop(ruta_id, None)
        if not detalle_por_dia[dia_key]:
            detalle_por_dia.pop(dia_key, None)

    rutas_confirmadas = [
        rid for rid in doc_asig.get("rutas_confirmadas", [])
        if rid != ruta_id
    ]

    pendientes = list(pendientes_map.values())

    db["asignaciones"].update_one(
        {"logistica_id": oid},
        {"$set": {
            "detalle_por_dia": detalle_por_dia,
            "sucursales_pendientes": pendientes,
            "rutas_confirmadas": rutas_confirmadas,
        }},
    )

    return {
        "status": "ok",
        "sucursales_pendientes": pendientes,
    }


# ── Operaciones atómicas sobre asignaciones ───────────────────

def actualizar_vehiculo_en_asignacion(
    logistica_id: str,
    ruta_id: str,
    dia: str,
    vehiculo_placas: str,
    vehiculo_abreviatura: str,
    capacidad_ton,
) -> dict:
    """Persiste el cambio de vehículo en asignaciones.detalle_por_dia."""
    oid = _parse_oid(logistica_id)
    if not oid:
        return {"status": "error", "mensaje": "logistica_id inválido"}
    try:
        db = get_db()
        db["asignaciones"].update_one(
            {"logistica_id": oid},
            {"$set": {
                f"detalle_por_dia.{dia}.{ruta_id}.vehiculo_placas":      vehiculo_placas or "",
                f"detalle_por_dia.{dia}.{ruta_id}.vehiculo_abreviatura": vehiculo_abreviatura or "",
                f"detalle_por_dia.{dia}.{ruta_id}.capacidad_ton":        capacidad_ton,
            }},
        )
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "mensaje": str(e)}


def quitar_sucursal_de_asignacion(
    logistica_id: str,
    ruta_id: str,
    dia: str,
    num_tienda: int,
    nombre: str,
    latitud,
    longitud,
    peso_kg: float,
) -> dict:
    """
    Elimina una sucursal de la ruta indicada dentro de asignaciones.detalle_por_dia
    y la registra en asignaciones.sucursales_pendientes para que persista entre recargas.
    """
    oid = _parse_oid(logistica_id)
    if not oid:
        return {"status": "error", "mensaje": "logistica_id inválido"}
    try:
        db  = get_db()
        doc = db["asignaciones"].find_one({"logistica_id": oid})
        if not doc:
            return {"status": "error", "mensaje": "No se encontró la asignación"}

        detalle  = doc.get("detalle_por_dia", {})
        ruta_det = detalle.get(dia, {}).get(ruta_id)
        if ruta_det is None:
            return {"status": "error", "mensaje": f"Ruta {ruta_id} no encontrada en {dia}"}

        # Quitar la sucursal de la lista
        nt = int(num_tienda)
        ruta_det["sucursales"] = [
            s for s in ruta_det.get("sucursales", [])
            if int(s.get("num_tienda", -1)) != nt
        ]
        # Re-numerar orden
        for i, s in enumerate(ruta_det["sucursales"]):
            s["orden"] = i + 1

        # Recalcular peso y utilización
        peso_total = sum(float(s.get("peso_kg") or 0) for s in ruta_det["sucursales"])
        ruta_det["peso_total_kg"] = peso_total
        cap_kg = float(ruta_det.get("capacidad_ton") or 0) * 1000
        ruta_det["porcentaje_utilizacion"] = round(peso_total / cap_kg * 100, 1) if cap_kg > 0 else 0

        # Añadir a pendientes (sin duplicar)
        pendientes = [
            p for p in doc.get("sucursales_pendientes", [])
            if int(p.get("num_tienda", -1)) != nt
        ]
        pendientes.append({
            "num_tienda": nt,
            "nombre":     nombre,
            "latitud":    latitud,
            "longitud":   longitud,
            "peso_kg":    float(peso_kg),
        })

        db["asignaciones"].update_one(
            {"logistica_id": oid},
            {"$set": {
                f"detalle_por_dia.{dia}.{ruta_id}": ruta_det,
                "sucursales_pendientes":             pendientes,
            }},
        )
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "mensaje": str(e)}


def agregar_sucursal_a_asignacion(
    logistica_id: str,
    ruta_id: str,
    dia: str,
    num_tienda: int,
    nombre: str,
    latitud,
    longitud,
    peso_kg: float,
) -> dict:
    """
    Añade una sucursal a la ruta indicada dentro de asignaciones.detalle_por_dia
    y la elimina de asignaciones.sucursales_pendientes.
    """
    oid = _parse_oid(logistica_id)
    if not oid:
        return {"status": "error", "mensaje": "logistica_id inválido"}
    try:
        db  = get_db()
        doc = db["asignaciones"].find_one({"logistica_id": oid})
        if not doc:
            return {"status": "error", "mensaje": "No se encontró la asignación"}

        detalle  = doc.get("detalle_por_dia", {})
        ruta_det = detalle.get(dia, {}).get(ruta_id)
        if ruta_det is None:
            return {"status": "error", "mensaje": f"Ruta {ruta_id} no encontrada en {dia}"}

        nt         = int(num_tienda)
        # Garantizar nombre correcto: si viene vacío o es el placeholder "Sucursal",
        # resolverlo desde rutas_config (fuente de verdad)
        nombre_final = nombre if (nombre and nombre != "Sucursal") \
                       else _obtener_nombres_sucursales().get(str(nt), "")
        sucursales = [s for s in ruta_det.get("sucursales", [])
                      if int(s.get("num_tienda", -1)) != nt]
        max_orden  = max((int(s.get("orden") or 0) for s in sucursales), default=0)
        sucursales.append({
            "num_tienda": nt,
            "nombre":     nombre_final,
            "orden":      max_orden + 1,
            "peso_kg":    float(peso_kg),
        })
        ruta_det["sucursales"] = sucursales

        peso_total = sum(float(s.get("peso_kg") or 0) for s in sucursales)
        ruta_det["peso_total_kg"] = peso_total
        cap_kg = float(ruta_det.get("capacidad_ton") or 0) * 1000
        ruta_det["porcentaje_utilizacion"] = round(peso_total / cap_kg * 100, 1) if cap_kg > 0 else 0

        pendientes = [
            p for p in doc.get("sucursales_pendientes", [])
            if int(p.get("num_tienda", -1)) != nt
        ]

        db["asignaciones"].update_one(
            {"logistica_id": oid},
            {"$set": {
                f"detalle_por_dia.{dia}.{ruta_id}": ruta_det,
                "sucursales_pendientes":             pendientes,
            }},
        )
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "mensaje": str(e)}


# ── Mayoristas: quitar / agregar con overrides ────────────────

def quitar_mayorista_de_ruta(
    logistica_id: str,
    ruta_id: str,
    id_cliente: int,
) -> dict:
    """
    Persiste el retiro de un mayorista de una ruta añadiéndolo a
    asignaciones.mayoristas_overrides[ruta_id].excluidos.
    """
    oid = _parse_oid(logistica_id)
    if not oid:
        return {"status": "error", "mensaje": "logistica_id inválido"}
    try:
        db = get_db()
        doc = db["asignaciones"].find_one({"logistica_id": oid})
        if not doc:
            return {"status": "error", "mensaje": "No se encontró la asignación"}

        overrides = doc.get("mayoristas_overrides", {})
        ruta_ov = overrides.get(ruta_id, {"excluidos": [], "incluidos": []})
        id_cl = int(id_cliente)

        if id_cl not in ruta_ov.get("excluidos", []):
            ruta_ov.setdefault("excluidos", []).append(id_cl)
        ruta_ov["incluidos"] = [x for x in ruta_ov.get("incluidos", []) if x != id_cl]
        overrides[ruta_id] = ruta_ov

        db["asignaciones"].update_one(
            {"logistica_id": oid},
            {"$set": {"mayoristas_overrides": overrides}},
        )
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "mensaje": str(e)}


def agregar_mayorista_a_ruta(
    logistica_id: str,
    ruta_id: str,
    dia: str,
    id_cliente: int,
    nombre: str,
    latitud,
    longitud,
    peso_kg: float,
    peso_ruta_actual: float,
) -> dict:
    """
    Persiste la incorporación de un mayorista a una ruta mediante override.
    Si el nuevo peso total supera la capacidad del vehículo, busca el vehículo
    más pequeño disponible y lo asigna automáticamente.
    """
    oid = _parse_oid(logistica_id)
    if not oid:
        return {"status": "error", "mensaje": "logistica_id inválido"}
    try:
        db = get_db()
        doc = db["asignaciones"].find_one({"logistica_id": oid})
        if not doc:
            return {"status": "error", "mensaje": "No se encontró la asignación"}

        detalle = doc.get("detalle_por_dia", {})
        ruta_det = detalle.get(dia, {}).get(ruta_id)
        if ruta_det is None:
            return {"status": "error", "mensaje": f"Ruta {ruta_id} no encontrada en {dia}"}

        id_cl = int(id_cliente)
        overrides = doc.get("mayoristas_overrides", {})

        # Limpiar este mayorista de cualquier override previo en OTRAS rutas
        for rid, ov in overrides.items():
            if rid != ruta_id:
                ov["incluidos"] = [x for x in ov.get("incluidos", []) if x != id_cl]

        # En esta ruta: quitar de excluidos y añadir a incluidos
        ruta_ov = overrides.setdefault(ruta_id, {"excluidos": [], "incluidos": []})
        ruta_ov["excluidos"] = [x for x in ruta_ov.get("excluidos", []) if x != id_cl]
        if id_cl not in ruta_ov.get("incluidos", []):
            ruta_ov.setdefault("incluidos", []).append(id_cl)

        # Verificar capacidad y buscar vehículo alternativo si hace falta
        nuevo_peso_kg = float(peso_ruta_actual) + float(peso_kg)
        cap_ton = float(ruta_det.get("capacidad_ton") or 0)
        vehiculo_cambiado = False
        nuevo_vehiculo = None

        if cap_ton > 0 and (nuevo_peso_kg / 1000) > cap_ton:
            placas_en_dia = {
                v.get("vehiculo_placas", "")
                for rid2, v in detalle.get(dia, {}).items()
                if rid2 != ruta_id
            }
            candidatos = sorted(
                [
                    v for v in db["vehiculos"].find({"activo": True})
                    if float(v.get("capacidad_toneladas") or 0) * 1000 >= nuevo_peso_kg
                    and (v.get("placas") or "") not in placas_en_dia
                    and v.get("placas") != ruta_det.get("vehiculo_placas")
                ],
                key=lambda v: float(v.get("capacidad_toneladas") or 0),
            )
            if candidatos:
                mejor = candidatos[0]
                abrev = mejor.get("abreviatura") or mejor.get("descripcion") or mejor.get("placas", "")
                cap_nueva = float(mejor.get("capacidad_toneladas") or 0)
                vehiculo_cambiado = True
                nuevo_vehiculo = {
                    "placas":        mejor.get("placas", ""),
                    "abrev":         abrev,
                    "capacidad_ton": cap_nueva,
                }

        update_set: dict = {"mayoristas_overrides": overrides}
        if vehiculo_cambiado and nuevo_vehiculo:
            update_set[f"detalle_por_dia.{dia}.{ruta_id}.vehiculo_placas"]      = nuevo_vehiculo["placas"]
            update_set[f"detalle_por_dia.{dia}.{ruta_id}.vehiculo_abreviatura"] = nuevo_vehiculo["abrev"]
            update_set[f"detalle_por_dia.{dia}.{ruta_id}.capacidad_ton"]        = nuevo_vehiculo["capacidad_ton"]

        db["asignaciones"].update_one(
            {"logistica_id": oid},
            {"$set": update_set},
        )
        return {
            "status":           "ok",
            "vehiculo_cambiado": vehiculo_cambiado,
            "nuevo_vehiculo":    nuevo_vehiculo,
        }
    except Exception as e:
        return {"status": "error", "mensaje": str(e)}


# ── Confirmaciones de rutas ───────────────────────────────────

def actualizar_rutas_confirmadas(logistica_id: str, ruta_ids: list) -> dict:
    """
    Persiste el estado de confirmación de rutas en asignaciones.rutas_confirmadas.
    ruta_ids es la lista completa de IDs confirmados en este momento.
    """
    oid = _parse_oid(logistica_id)
    if not oid:
        return {"status": "error", "mensaje": "logistica_id inválido"}
    try:
        db = get_db()
        db["asignaciones"].update_one(
            {"logistica_id": oid},
            {"$set": {"rutas_confirmadas": list(ruta_ids)}},
            upsert=True,
        )
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "mensaje": str(e)}


# ── Guardar / recuperar modificación ──────────────────────────

def guardar_modificacion(payload: dict, logistica_id: str) -> dict:
    """Persiste la modificación en la colección `modificaciones_rutas`."""
    oid = _parse_oid(logistica_id)
    if not oid:
        return {"status": "error", "mensaje": "logistica_id inválido."}

    payload["guardado_en"]  = datetime.now().isoformat()
    payload["logistica_id"] = oid

    try:
        db = get_db()
        db["modificaciones_rutas"].update_one(
            {"logistica_id": oid},
            {"$set": payload},
            upsert=True,
        )
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "mensaje": str(e)}


def obtener_modificacion_previa(logistica_id: str) -> dict:
    """Devuelve la modificación guardada para la logística activa."""
    oid = _parse_oid(logistica_id)
    if not oid:
        return {}
    try:
        db  = get_db()
        doc = db["modificaciones_rutas"].find_one({"logistica_id": oid})
        if not doc:
            return {}
        doc.pop("_id", None)
        doc["logistica_id"] = str(doc["logistica_id"])
        return doc
    except Exception:
        return {}
