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

    # ── 3. Mayoristas desde distribucion_mayoristas ────────────
    # Los campos canónicos de paradas_integradas son:
    #   id_cliente, nombre_base|nombre, latitud, longitud, desvio_m
    # Los pesos vienen de extraccion.mayoristas; la distribución no los guarda.
    mayoristas_por_ruta: dict = {}
    try:
        dist = db["distribucion_mayoristas"].find_one({"_key": "ultimo"})
        if dist:
            for ruta_dist in dist.get("rutas", []):
                rid  = str(ruta_dist.get("_id", ""))
                mays = []
                for p in ruta_dist.get("paradas_integradas", []):
                    if p.get("tipo") != "mayorista":
                        continue
                    # La colección usa 'latitud'/'longitud' (no 'lat'/'lon')
                    lat = p.get("latitud")
                    lon = p.get("longitud")
                    mays.append({
                        "tipo":       "mayorista",
                        "id_cliente": p.get("id_cliente"),
                        "nombre":     p.get("nombre_base") or p.get("nombre", ""),
                        "peso_kg":    float(p.get("peso_kg", 0)),
                        "latitud":    float(lat) if lat is not None else None,
                        "longitud":   float(lon) if lon is not None else None,
                        "orden":      p.get("orden"),
                    })
                if mays:
                    mayoristas_por_ruta[rid] = mays
    except Exception as e:
        print(f"[obtener_rutas_para_modificar] mayoristas error: {e}")

    # ── 3b. Enriquecer y filtrar mayoristas según pedidos en extracción ────
    # distribucion_mayoristas no almacena pesos individuales; el peso real de
    # cada mayorista vive en extraccion[logistica_id].mayoristas[].peso_total_kg.
    # Regla: solo se incluyen mayoristas con pedido real (peso_total_kg > 0).
    try:
        pesos_may_ext: dict = {}   # id_cliente_int → peso_kg
        ext_doc = db["extraccion"].find_one({"logistica_id": oid})
        if ext_doc:
            for m in ext_doc.get("mayoristas", []):
                id_cl = m.get("codigo") or m.get("id_cliente")
                try:
                    peso = float(m.get("peso_total_kg", 0) or 0)
                    if peso > 0:
                        pesos_may_ext[int(id_cl)] = peso
                except (TypeError, ValueError):
                    pass
        for rid, mays in list(mayoristas_por_ruta.items()):
            filtrados = []
            for m in mays:
                id_cl = m.get("id_cliente")
                try:
                    id_int = int(str(id_cl).split(".")[0])
                except (TypeError, ValueError):
                    continue
                peso = pesos_may_ext.get(id_int, 0.0)
                if peso <= 0:
                    continue
                m["peso_kg"] = peso
                filtrados.append(m)
            if filtrados:
                mayoristas_por_ruta[rid] = filtrados
            else:
                mayoristas_por_ruta.pop(rid, None)
    except Exception as e:
        print(f"[obtener_rutas_para_modificar] pesos_may error: {e}")

    # ── 4. Construir lista de rutas desde detalle_por_dia ─────
    # Mapa de nombres canónicos desde rutas_config (fuente de verdad)
    nombres_map = _obtener_nombres_sucursales()   # { num_tienda_str: nombre }

    # detalle_por_dia[dia] es un dict  { ruta_id: { campos... } }
    rutas_normalizadas: list = []
    procesadas: set          = set()

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

            mayoristas_norm = mayoristas_por_ruta.get(ruta_id, [])

            # Calcular peso si vino en cero
            if not peso_kg:
                peso_kg = (
                    sum(s["peso_kg"] for s in sucursales_norm)
                    + sum(m["peso_kg"] for m in mayoristas_norm)
                )

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

    # Descarga acumulada: cada sucursal tiene su propio tope de 120 min
    descarga = sum(
        min(pesos.get(str(p.get("num_tienda", "")), 0.0) * min_descarga,
            MAX_DESCARGA_POR_SUCURSAL)
        for p in paradas
        if p.get("tipo") == "sucursal" or p.get("num_tienda")
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
