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
import concurrent.futures
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


def _doc_key(m: dict) -> str:
    """Clave única de un mayorista: documento si existe, si no str(id_cliente)."""
    return str(m.get("documento") or m.get("id_cliente", ""))


def _override_key_matches(m: dict, key) -> bool:
    """True si el mayorista coincide con la clave de override.
    Las claves string se comparan contra documento; las int contra id_cliente (legacy).
    """
    if isinstance(key, int):
        return int(m.get("id_cliente", -1)) == key
    return str(m.get("documento") or m.get("id_cliente", "")) == str(key)


def _aplicar_overrides_mayoristas(dist: dict, overrides: dict, sucursales_por_ruta: dict) -> dict:
    """
    Aplica los overrides manuales al resultado de calcular_distribucion_mayoristas.
    overrides = { ruta_id: { excluidos: [doc_str | id_cl_int,...], incluidos: [...] } }
    Las claves string se comparan por documento; las int (legacy) por id_cliente.
    """
    if not overrides:
        return dist

    mayoristas_por_ruta = {k: list(v) for k, v in dist.get("mayoristas_por_ruta", {}).items()}
    todos = (
        dist.get("todos_mayoristas", [])
        + dist.get("sin_asignar", [])
        + dist.get("sin_coords", [])
    )
    # Índice dual: por documento (str) y por id_cliente (int, legacy)
    todos_may_doc   = {str(m.get("documento") or ""): m for m in todos if m.get("documento")}
    todos_may_index = {int(m.get("id_cliente", 0)): m for m in todos}

    rutas_afectadas: set = set()

    for ruta_id, ov in overrides.items():
        excluidos = ov.get("excluidos", [])
        incluidos = ov.get("incluidos", [])

        if excluidos:
            antes = mayoristas_por_ruta.get(ruta_id, [])
            despues = [m for m in antes
                       if not any(_override_key_matches(m, k) for k in excluidos)]
            if len(despues) != len(antes):
                mayoristas_por_ruta[ruta_id] = despues
                rutas_afectadas.add(ruta_id)

        if incluidos:
            for other_rid in list(mayoristas_por_ruta.keys()):
                if other_rid == ruta_id:
                    continue
                prev = mayoristas_por_ruta[other_rid]
                nuevos = [m for m in prev
                          if not any(_override_key_matches(m, k) for k in incluidos)]
                if len(nuevos) != len(prev):
                    mayoristas_por_ruta[other_rid] = nuevos
                    rutas_afectadas.add(other_rid)
            existing_keys = {_doc_key(m) for m in mayoristas_por_ruta.get(ruta_id, [])}
            for key in incluidos:
                ref = todos_may_doc.get(str(key)) if isinstance(key, str) else todos_may_index.get(int(key))
                if ref and _doc_key(ref) not in existing_keys:
                    mayoristas_por_ruta.setdefault(ruta_id, []).append(dict(ref))
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
            orden_may[_doc_key(p)] = p.get("orden")
        for m in mays:
            dk = _doc_key(m)
            if dk in orden_may:
                m["orden"] = orden_may[dk]

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


def _osrm_llamada_simple(coords: list) -> dict | None:
    """
    Una llamada OSRM directa (sin alternativas) para los waypoints dados.
    Retorna {distancia_km, traslado_min, geometry} o None si falla.
    """
    import json as _json
    if len(coords) < 2:
        return None
    waypoints = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in coords)
    url = f"{OSRM_BASE_URL}/{waypoints}?overview=full&geometries=geojson"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "ICG-RouteModification/1.0",
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=OSRM_TIMEOUT) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
        if data.get("code") != "Ok" or not data.get("routes"):
            return None
        ruta = data["routes"][0]
        return {
            "distancia_km": round(ruta.get("distance", 0) / 1000, 2),
            "traslado_min": round(ruta.get("duration", 0) / 60, 1),
            "geometry":     ruta.get("geometry", {}).get("coordinates", []),
        }
    except Exception:
        return None


def _osrm_llamada_alternativas(coords: list) -> list:
    """
    Llamada OSRM con alternatives=2: devuelve hasta 3 rutas que el propio
    algoritmo de OSRM garantiza que usan segmentos de calle distintos.
    Retorna lista de dicts (puede estar vacía si OSRM no encuentra alternativas).
    """
    import json as _json
    if len(coords) < 2:
        return []
    waypoints = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in coords)
    url = f"{OSRM_BASE_URL}/{waypoints}?overview=full&geometries=geojson&alternatives=2"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "ICG-RouteModification/1.0",
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=OSRM_TIMEOUT) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
        if data.get("code") != "Ok" or not data.get("routes"):
            return []
        return [{
            "distancia_km": round(r.get("distance", 0) / 1000, 2),
            "traslado_min": round(r.get("duration", 0) / 60, 1),
            "geometry":     r.get("geometry", {}).get("coordinates", []),
        } for r in data["routes"]]
    except Exception:
        return []


def _geom_muestra(geom: list, n: int = 5) -> list:
    """Extrae n puntos (lat, lon) distribuidos uniformemente en una geometría GeoJSON."""
    if not geom or n < 2:
        return []
    total = len(geom)
    indices = [int(i * (total - 1) / (n - 1)) for i in range(n)]
    return [(geom[i][1], geom[i][0]) for i in indices]


def _divergencia_geom(geom_a: list, geom_b: list) -> float:
    """
    Distancia mínima (km) entre puntos homólogos de dos geometrías.
    Un valor bajo indica rutas muy similares; uno alto, rutas que divergen.
    """
    ma = _geom_muestra(geom_a, 5)
    mb = _geom_muestra(geom_b, 5)
    if not ma or not mb:
        return 0.0
    return min(_haversine_km(a[0], a[1], b[0], b[1]) for a, b in zip(ma, mb))


def _insertar_via(coords: list, lado: int, offset_deg: float) -> list:
    """
    Inserta un via-point perpendicular al eje principal del recorrido
    en el segmento más largo, a `offset_deg` grados del eje.
    lado=+1 derecha, lado=-1 izquierda.
    """
    if len(coords) < 2:
        return coords
    dir_lat = coords[-1][0] - coords[0][0]
    dir_lon = coords[-1][1] - coords[0][1]
    length  = math.sqrt(dir_lat ** 2 + dir_lon ** 2)
    if length > 1e-6:
        ux = (-dir_lon / length) * offset_deg * lado
        uy = ( dir_lat / length) * offset_deg * lado
    else:
        ux = offset_deg * lado
        uy = 0.0
    best_i = max(
        range(len(coords) - 1),
        key=lambda i: _haversine_km(
            coords[i][0], coords[i][1], coords[i + 1][0], coords[i + 1][1]
        ),
    )
    mid_lat = (coords[best_i][0] + coords[best_i + 1][0]) / 2 + ux
    mid_lon = (coords[best_i][1] + coords[best_i + 1][1]) / 2 + uy
    return coords[: best_i + 1] + [(mid_lat, mid_lon)] + coords[best_i + 1 :]


def consultar_osrm_alternativas(coords: list, velocidad_kmh: float = 35.0) -> list:
    """
    Genera 3 rutas genuinamente distintas con enfoque de dos fases:

    Fase 1 — OSRM alternatives=2
        Una llamada donde el algoritmo interno de OSRM penaliza segmentos
        compartidos y devuelve hasta 3 rutas con maxima divergencia posible.
        Si devuelve 3 rutas, se usan directamente sin procesar mas.

    Fase 2 — Multi-busqueda con validacion geometrica
        Si faltan rutas, lanza hasta 8 candidatos en paralelo con via-points
        a 4 distancias distintas (0.15 a 0.60 grados) en ambos lados del eje.
        Cada candidato que OSRM acepta es evaluado por su divergencia real
        respecto a las rutas ya confirmadas (distancia entre puntos homologos).
        Se selecciona greedily el candidato con mayor separacion minima.

    Fase 3 — Fallback haversine
        Solo si la red vial no ofrece ninguna alternativa real.
    """
    if len(coords) < 2:
        return []

    fb           = _fallback_haversine(coords, velocidad_kmh)
    geom_directa = [[lon, lat] for lat, lon in coords]

    # ── Fase 1: OSRM nativo ──────────────────────────────────────────────
    nativas = _osrm_llamada_alternativas(coords)
    if len(nativas) >= 3:
        return nativas[:3]

    confirmadas: list[dict] = list(nativas)

    # ── Fase 2: multi-busqueda cuando faltan rutas ───────────────────────
    faltan = 3 - len(confirmadas)
    if faltan > 0:
        lats = [c[0] for c in coords]
        lons = [c[1] for c in coords]
        span = max(max(lats) - min(lats), max(lons) - min(lons))

        offsets = [
            max(0.12, span * 0.35),
            max(0.22, span * 0.60),
            max(0.35, span * 0.90),
            max(0.50, span * 1.20),
        ]
        candidatos_coords = [
            _insertar_via(coords, lado, off)
            for off in offsets
            for lado in (+1, -1)
        ]

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            futuros    = [ex.submit(_osrm_llamada_simple, vc) for vc in candidatos_coords]
            candidatos = [f.result() for f in futuros]

        candidatos = [c for c in candidatos if c and c.get("geometry")]

        _UMBRAL_KM = 0.8
        while len(confirmadas) < 3 and candidatos:
            mejor_idx   = -1
            mejor_score = -1.0
            for i, cand in enumerate(candidatos):
                if not confirmadas:
                    score = 999.0
                else:
                    score = min(
                        _divergencia_geom(cand["geometry"], conf["geometry"])
                        for conf in confirmadas
                    )
                if score > mejor_score:
                    mejor_score = score
                    mejor_idx   = i
            if mejor_idx == -1 or mejor_score < _UMBRAL_KM:
                break
            confirmadas.append(candidatos.pop(mejor_idx))

    # ── Fase 3: fallback ─────────────────────────────────────────────────
    base_d = confirmadas[0]["distancia_km"] if confirmadas else fb["distancia_km"]
    base_t = confirmadas[0]["traslado_min"] if confirmadas else fb["traslado_min"]
    factores = [(1.0, 1.0), (1.12, 1.15), (1.25, 1.28)]
    while len(confirmadas) < 3:
        fd, ft = factores[len(confirmadas)]
        confirmadas.append({
            "distancia_km": round(base_d * fd, 2),
            "traslado_min": round(base_t * ft, 1),
            "geometry":     geom_directa,
        })

    return confirmadas[:3]


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
        docs = list(db["vehiculos"].find({"activo": {"$ne": False}}))
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
        for v in db["vehiculos"].find({"activo": {"$ne": False}}):
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
        dist_vacia = calcular_distribucion_mayoristas(logistica_id)
        return {
            "status":                "ok",
            "logistica_id":          str(logistica_id),
            "total_rutas":           0,
            "rutas":                 [],
            "mayoristas_disponibles": dist_vacia.get("todos_mayoristas", []),
            "sucursales_pendientes": [],
            "rutas_confirmadas":     [],
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
            {"_id": rid, "sucursales": sucs, "cap_ton": meta_por_ruta.get(rid, {}).get("cap_ton")}
            for rid, sucs in sucursales_por_ruta.items()
        ],
    )
    overrides = doc_asig.get("mayoristas_overrides", {})
    orden_sucursales: dict = dist.get("orden_sucursales", {})
    mayoristas_disponibles: list = dist.get("todos_mayoristas", [])

    todos_may_index: dict = {}   # int(id_cliente) → m  (legacy)
    todos_may_doc:   dict = {}   # str(documento)   → m  (primary)
    for m in (dist.get("todos_mayoristas", []) + dist.get("sin_coords", []) + dist.get("sin_asignar", [])):
        try:
            id_cl = int(m.get("id_cliente", 0))
        except (TypeError, ValueError):
            continue
        if id_cl:
            todos_may_index[id_cl] = dict(m)
        doc = str(m.get("documento") or "")
        if doc:
            todos_may_doc[doc] = dict(m)

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

        det_ruta = None
        for dia, rutas_del_dia in detalle_por_dia.items():
            if isinstance(rutas_del_dia, dict) and ruta_id in rutas_del_dia:
                det_ruta = rutas_del_dia.get(ruta_id) or {}
                break

        mayoristas_norm = []
        ruta_ov = overrides.get(ruta_id, {}) if isinstance(overrides, dict) else {}
        claves_incluidas = list(ruta_ov.get("incluidos", []) or [])
        if claves_incluidas or ruta_ov.get("excluidos"):
            vistos: set = set()
            for clave in claves_incluidas:
                # Clave puede ser str(documento) o int(id_cliente) legacy
                if isinstance(clave, str):
                    may = todos_may_doc.get(clave)
                else:
                    try:
                        may = todos_may_index.get(int(clave))
                    except (TypeError, ValueError):
                        may = None
                if may:
                    dk = _doc_key(may)
                    if dk not in vistos:
                        vistos.add(dk)
                        mayoristas_norm.append(dict(may))
        elif det_ruta and isinstance(det_ruta.get("mayoristas"), list):
            mayoristas_norm = [dict(m) for m in det_ruta.get("mayoristas", []) if isinstance(m, dict)]
        else:
            # Rutas generadas por VRP histórico/afinidad no guardan "mayoristas" en
            # detalle_por_dia (historico_logic.py solo persiste sucursales). Usar la
            # distribución calculada (histórico de mayoristas → proximidad geográfica)
            # para que se muestren con la secuencia correcta.
            mayoristas_norm = [dict(m) for m in dist.get("mayoristas_por_ruta", {}).get(ruta_id, [])]

        # Aplicar orden entrelazado calculado geográficamente desde mayoristas_por_ruta
        may_orden_map = {
            _doc_key(m): m.get("orden")
            for m in dist.get("mayoristas_por_ruta", {}).get(ruta_id, [])
            if m.get("orden") is not None
        }
        for m in mayoristas_norm:
            dk = _doc_key(m)
            if dk in may_orden_map:
                m["orden"] = may_orden_map[dk]

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


def calcular_alternativas_subruta(paradas: list, pesos: dict, hora_salida: str = "08:00") -> list:
    """
    Devuelve siempre exactamente 3 alternativas de ruta con tiempos calculados.
    Cada elemento: {distancia_km, traslado_min, descarga_min, total_min, hora_regreso, geometry, origen}.
    Ruta 0 = directa (recomendada), Ruta 1 = vía norte, Ruta 2 = vía sur.
    """
    cfg          = _obtener_config_general()
    matriz_lat   = MATRIZ_LAT_DEFAULT
    matriz_lon   = MATRIZ_LON_DEFAULT
    min_descarga = float(cfg.get("min_descarga_por_kg") or MIN_DESCARGA_POR_KG)
    velocidad    = float(cfg.get("velocidad_kmh") or 35.0)

    coords = [(matriz_lat, matriz_lon)]
    for p in paradas:
        lat = p.get("latitud")
        lon = p.get("longitud")
        if lat is not None and lon is not None:
            coords.append((float(lat), float(lon)))

    if len(coords) < 2:
        return []

    coords.append((matriz_lat, matriz_lon))

    def _peso_descarga(p: dict) -> float:
        if p.get("tipo") == "mayorista" or (p.get("id_cliente") is not None and p.get("num_tienda") is None):
            return float(p.get("peso_kg") or 0)
        return float(pesos.get(str(p.get("num_tienda", "")), p.get("peso_kg") or 0) or 0)

    descarga = sum(
        min(_peso_descarga(p) * min_descarga, MAX_DESCARGA_POR_SUCURSAL)
        for p in paradas
    )

    try:
        h_s, m_s = hora_salida.split(":")
        hora_salida_min = int(h_s) * 60 + int(m_s)
    except Exception:
        hora_salida_min = 8 * 60

    def _hora_regreso(traslado_min):
        total = traslado_min + descarga + HORAS_EXTRA_RUTA_MIN
        reg   = hora_salida_min + total
        h_r   = int(reg // 60)
        m_r   = int(round(reg % 60))
        if m_r >= 60:
            h_r += 1; m_r -= 60
        return f"{h_r:02d}:{m_r:02d}", round(total, 1)

    # Siempre devuelve exactamente 3 rutas (paralelas + fallback)
    alternativas = consultar_osrm_alternativas(coords, velocidad)

    resultado = []
    for alt in alternativas:
        hr, total = _hora_regreso(alt["traslado_min"])
        resultado.append({
            "distancia_km": alt["distancia_km"],
            "traslado_min": round(alt["traslado_min"], 1),
            "descarga_min": round(descarga, 1),
            "total_min":    total,
            "hora_regreso": hr,
            "geometry":     alt["geometry"],
            "origen":       "osrm",
        })

    return resultado


def _posicion_via_optima(waypoints: list, via: tuple) -> int:
    """Devuelve el índice tras el cual insertar un via-point minimizando el desvío total."""
    via_lat, via_lon = via
    best_i    = 0
    best_cost = float("inf")
    for i in range(len(waypoints) - 1):
        a, b  = waypoints[i], waypoints[i + 1]
        costo = (_haversine_km(a[0], a[1], via_lat, via_lon)
                 + _haversine_km(via_lat, via_lon, b[0], b[1]))
        if costo < best_cost:
            best_cost = costo
            best_i    = i
    return best_i


def calcular_ruta_personalizada(
    paradas: list,
    via_points: list,
    pesos: dict,
    hora_salida: str = "08:00",
    puntos_evitar: list | None = None,
) -> dict:
    """
    Calcula una ruta OSRM con via-points (paso obligatorio) y zonas a evitar.

    via_points    → puntos que la ruta DEBE recorrer; se insertan en su posición óptima.
    puntos_evitar → zonas que la ruta debe evitar; si el trayecto base pasa dentro
                    de 300 m de una zona, se intenta desviar añadiendo un waypoint
                    perpendicular que aleja la ruta de esa área.
    """
    cfg          = _obtener_config_general()
    matriz_lat   = MATRIZ_LAT_DEFAULT
    matriz_lon   = MATRIZ_LON_DEFAULT
    min_descarga = float(cfg.get("min_descarga_por_kg") or MIN_DESCARGA_POR_KG)
    velocidad    = float(cfg.get("velocidad_kmh") or 35.0)
    puntos_evitar = puntos_evitar or []

    # Construir secuencia base: matriz → paradas → matriz
    coords_base: list = [(matriz_lat, matriz_lon)]
    for p in paradas:
        lat = p.get("latitud")
        lon = p.get("longitud")
        if lat is not None and lon is not None:
            coords_base.append((float(lat), float(lon)))

    if len(coords_base) < 2:
        return {}

    coords_base.append((matriz_lat, matriz_lon))

    # Insertar via-points en su posición óptima (paso obligatorio)
    coords: list = list(coords_base)
    for vp in via_points:
        lat = vp.get("lat")
        lon = vp.get("lon")
        if lat is None or lon is None:
            continue
        idx    = _posicion_via_optima(coords, (float(lat), float(lon)))
        coords = coords[: idx + 1] + [(float(lat), float(lon))] + coords[idx + 1 :]

    # Intentar desviar si algún segmento pasa por una zona a evitar
    RADIO_EVITAR_KM = 0.3  # 300 m
    for pe in puntos_evitar:
        pe_lat = pe.get("lat")
        pe_lon = pe.get("lon")
        if pe_lat is None or pe_lon is None:
            continue
        pe_lat, pe_lon = float(pe_lat), float(pe_lon)
        # Buscar el segmento más cercano a la zona a evitar
        seg_idx = None
        seg_dist = float("inf")
        for i in range(len(coords) - 1):
            a, b = coords[i], coords[i + 1]
            mid_lat = (a[0] + b[0]) / 2
            mid_lon = (a[1] + b[1]) / 2
            d = _haversine_km(mid_lat, mid_lon, pe_lat, pe_lon)
            if d < seg_dist:
                seg_dist = d
                seg_idx  = i
        if seg_dist > RADIO_EVITAR_KM or seg_idx is None:
            continue
        # Calcular waypoint de desvío: mover perpendicularmente 0.5 km desde la zona
        a, b   = coords[seg_idx], coords[seg_idx + 1]
        dx     = b[0] - a[0]
        dy     = b[1] - a[1]
        length = math.sqrt(dx * dx + dy * dy) or 1e-9
        # perpendicular unitario
        perp_lat = -dy / length
        perp_lon =  dx / length
        # desplazar 0.5 km en grados (aprox. 1/111 por km en latitud)
        offset = 0.005
        desvio_lat = pe_lat + perp_lat * offset
        desvio_lon = pe_lon + perp_lon * offset
        coords = coords[: seg_idx + 1] + [(desvio_lat, desvio_lon)] + coords[seg_idx + 1 :]

    # Calcular descarga acumulada
    def _peso_p(p):
        if p.get("tipo") == "mayorista" or (p.get("id_cliente") is not None and p.get("num_tienda") is None):
            return float(p.get("peso_kg") or 0)
        return float(pesos.get(str(p.get("num_tienda", "")), p.get("peso_kg") or 0) or 0)

    descarga = sum(
        min(_peso_p(p) * min_descarga, MAX_DESCARGA_POR_SUCURSAL)
        for p in paradas
    )

    try:
        h_s, m_s = hora_salida.split(":")
        hora_salida_min = int(h_s) * 60 + int(m_s)
    except Exception:
        hora_salida_min = 8 * 60

    # Llamada OSRM con la secuencia completa
    resultado = _osrm_llamada_simple(coords) or _fallback_haversine(coords_base, velocidad)

    traslado = resultado.get("traslado_min", 0.0)
    total    = traslado + descarga + HORAS_EXTRA_RUTA_MIN
    reg      = hora_salida_min + total
    h_r, m_r = int(reg // 60), int(round(reg % 60))
    if m_r >= 60:
        h_r += 1; m_r -= 60

    return {
        "distancia_km": resultado.get("distancia_km", 0),
        "traslado_min": round(traslado, 1),
        "descarga_min": round(descarga, 1),
        "total_min":    round(total, 1),
        "hora_regreso": f"{h_r:02d}:{m_r:02d}",
        "geometry":     resultado.get("geometry", []),
        "origen":       "osrm_personalizada",
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
    mayoristas: list | None = None,
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

    sucursales = sucursales if isinstance(sucursales, list) else []
    may_list_raw = mayoristas if isinstance(mayoristas, list) else []

    if not sucursales and not may_list_raw:
        return {"status": "error", "mensaje": "Se requiere al menos una sucursal o un mayorista."}

    db = get_db()
    doc_asig = db["asignaciones"].find_one({"logistica_id": oid})
    if not doc_asig:
        doc_asig = {"logistica_id": oid, "detalle_por_dia": {}, "sucursales_pendientes": [], "rutas_confirmadas": []}

    veh = db["vehiculos"].find_one({"placas": placas, "activo": {"$ne": False}})
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

    may_det = []
    may_list = may_list_raw
    seen_may: set = set()
    for i, m in enumerate(may_list, start=1):
        if not isinstance(m, dict):
            continue
        id_cl = m.get("id_cliente")
        try:
            id_cl_int = int(id_cl)
        except (TypeError, ValueError):
            continue
        dk = _doc_key(m)  # deduplicate per document, not per client
        if dk in seen_may:
            continue
        seen_may.add(dk)
        peso_may = float(m.get("peso_kg") or 0)
        peso_total_kg += peso_may
        may_det.append({
            "id_cliente": id_cl_int,
            "documento":  m.get("documento") or "",
            "nombre":     m.get("nombre") or f"Mayorista {id_cl_int}",
            "orden":      len(suc_det) + i,
            "peso_kg":    peso_may,
            "latitud":    m.get("latitud"),
            "longitud":   m.get("longitud"),
        })

    pct = round((peso_total_kg / 1000 / cap_ton) * 100, 1) if cap_ton > 0 else 0
    nombre_ruta = (nombre_ruta or "").strip() or f"{abrev} — {dia_key.capitalize()}"

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
        "mayoristas":             may_det,
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
        upsert=True,
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
        "mayoristas":            may_det,
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


def cambiar_dia_ruta(
    logistica_id: str,
    ruta_id: str,
    dia_actual: str,
    dia_nuevo: str,
) -> dict:
    """
    Mueve una ruta de un día a otro dentro de asignaciones.detalle_por_dia.
    El vehículo asignado queda disponible en el día original y ocupado en el nuevo.
    """
    oid = _parse_oid(logistica_id)
    if not oid:
        return {"status": "error", "mensaje": "logistica_id inválido"}

    dia_actual_k = (dia_actual or "").strip().lower()
    dia_nuevo_k  = (dia_nuevo  or "").strip().lower()

    if dia_actual_k not in ORDEN_DIAS or dia_nuevo_k not in ORDEN_DIAS:
        return {"status": "error", "mensaje": "Día inválido"}

    if dia_actual_k == dia_nuevo_k:
        return {"status": "ok"}

    try:
        db  = get_db()
        doc = db["asignaciones"].find_one({"logistica_id": oid})
        if not doc:
            return {"status": "error", "mensaje": "No se encontró la asignación"}

        detalle  = doc.get("detalle_por_dia", {})
        ruta_det = detalle.get(dia_actual_k, {}).get(ruta_id)
        if ruta_det is None:
            return {"status": "error", "mensaje": f"Ruta {ruta_id} no encontrada en {dia_actual_k}"}

        db["asignaciones"].update_one(
            {"logistica_id": oid},
            {
                "$set":   {f"detalle_por_dia.{dia_nuevo_k}.{ruta_id}": ruta_det},
                "$unset": {f"detalle_por_dia.{dia_actual_k}.{ruta_id}": ""},
            },
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
    documento: str = "",
) -> dict:
    """
    Persiste el retiro de un documento de mayorista de una ruta en
    asignaciones.mayoristas_overrides[ruta_id].excluidos.
    La clave almacenada es `documento` (str) si está presente; si no, `id_cliente` (int, legacy).
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
        # Clave primaria: documento (str) o id_cliente (int) como fallback
        clave = documento if documento else int(id_cliente)

        if clave not in ruta_ov.get("excluidos", []):
            ruta_ov.setdefault("excluidos", []).append(clave)
        ruta_ov["incluidos"] = [x for x in ruta_ov.get("incluidos", []) if x != clave]
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
    documento: str = "",
) -> dict:
    """
    Persiste la incorporación de un documento de mayorista a una ruta mediante override.
    La clave almacenada es `documento` (str) si está presente; si no, `id_cliente` (int, legacy).
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

        # Clave primaria: documento (str) o id_cliente (int) como fallback
        clave = documento if documento else int(id_cliente)
        overrides = doc.get("mayoristas_overrides", {})

        # Limpiar este documento de cualquier override previo en OTRAS rutas
        for rid, ov in overrides.items():
            if rid != ruta_id:
                ov["incluidos"] = [x for x in ov.get("incluidos", []) if x != clave]

        # En esta ruta: quitar de excluidos y añadir a incluidos
        ruta_ov = overrides.setdefault(ruta_id, {"excluidos": [], "incluidos": []})
        ruta_ov["excluidos"] = [x for x in ruta_ov.get("excluidos", []) if x != clave]
        if clave not in ruta_ov.get("incluidos", []):
            ruta_ov.setdefault("incluidos", []).append(clave)

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
                    v for v in db["vehiculos"].find({"activo": {"$ne": False}})
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
