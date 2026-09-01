"""
logic/modificacion_logic.py
Lógica de negocio para la Sección — Modificación manual de rutas.

Lee datos desde:
  - `asignaciones` (+ tablas normalizadas) → vehículos y días asignados (guardados en Asignación)
  - `mayoristas_logic.calcular_distribucion_mayoristas` → mayoristas por ruta (orden, peso, coords)
  - `rutas_config` (+ `rutas_config_sucursales`) → sucursales con coordenadas
  - `extraccion`   → pesos de sucursales
  - `vehiculos`    → flota activa
Guarda en:
  - `modificaciones_rutas` (+ `modificacion_rutas`, `modificacion_ruta_sucursales`,
    `modificacion_ruta_mayoristas`) — con logistica_id

No se usan archivos JSON ni las tablas validaciones/reordenamientos.
"""
import json
import math
import time
import urllib.request
import urllib.error
import concurrent.futures
from datetime import datetime

from bson import ObjectId
from bson.errors import InvalidId
from sqlalchemy import select, insert, update, delete, or_, func

from db import get_db, get_table, transaccion
from logic.mayoristas_logic import calcular_distribucion_mayoristas, _integrar_paradas, obtener_mayoristas_guardados

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


def _id_valido(doc_id: str) -> "str | None":
    try:
        return str(ObjectId(doc_id))
    except (InvalidId, TypeError):
        return None


def _obtener_config_general() -> dict:
    try:
        db = get_db()
        tabla = get_table("configuracion")
        fila = db.execute(select(tabla)).mappings().first()
        return dict(fila) if fila else {}
    except Exception:
        return {}


def _doc_key(m: dict) -> str:
    """Clave única de un mayorista: documento si existe, si no str(id_cliente)."""
    return str(m.get("documento") or m.get("id_cliente", ""))


def _override_chofer_nombre(valor) -> str:
    """chofer_overrides[ruta_id] puede ser un dict nuevo {nombre, chofer_id} o un string legado."""
    if isinstance(valor, dict):
        return (valor.get("nombre") or "").strip()
    return (valor or "").strip()


def _override_chofer_id(valor) -> "str | None":
    if isinstance(valor, dict):
        return valor.get("chofer_id")
    return None


def _clave_a_python(clave: str):
    """
    asignaciones_mayoristas_overrides.clave es texto puro (SQL no distingue
    tipos como Mongo). Reconstituye la distinción original documento(str) vs
    id_cliente(int, legado) con el mismo criterio verificado contra datos
    reales: los documento son alfanuméricos (p. ej. "AA1153"), el fallback
    legado por id_cliente es siempre solo dígitos (p. ej. "270").
    """
    return int(clave) if clave.isdigit() else clave


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
            req = urllib.request.Request(url, headers={
                "User-Agent": "ICG-RouteModification/1.0",
                "Accept": "application/json",
            })
            with urllib.request.urlopen(req, timeout=OSRM_TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))

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


def _osrm_llamada_simple(coords: list) -> "dict | None":
    """
    Una llamada OSRM directa (sin alternativas) para los waypoints dados.
    Retorna {distancia_km, traslado_min, geometry} o None si falla.
    """
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
            data = json.loads(resp.read().decode("utf-8"))
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
            data = json.loads(resp.read().decode("utf-8"))
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

    confirmadas: list = list(nativas)

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


# ── Helpers de lectura SQL Server ──────────────────────────────

def _obtener_rutas_db() -> dict:
    """{ruta_id_str: {"_id": ruta_id, "nombre": str}} -- solo `nombre` se consume en el resto del módulo."""
    result = {}
    try:
        db = get_db()
        tabla = get_table("rutas_config")
        for r in db.execute(select(tabla.c.mongo_id, tabla.c.nombre)):
            result[r.mongo_id] = {"_id": r.mongo_id, "nombre": r.nombre}
    except Exception as e:
        print(f"[_obtener_rutas_db] Error: {e}")
    return result


def _obtener_coordenadas_sucursales() -> dict:
    coords = {}
    try:
        db = get_db()
        tabla = get_table("rutas_config_sucursales")
        for suc in db.execute(select(tabla.c.num_tienda, tabla.c.latitud, tabla.c.longitud)):
            nt  = str(suc.num_tienda or "")
            lat = suc.latitud
            lon = suc.longitud
            if nt and lat is not None and lon is not None:
                coords[nt] = {"latitud": float(lat), "longitud": float(lon)}
    except Exception as e:
        print(f"[_obtener_coordenadas_sucursales] Error: {e}")
    return coords


def _obtener_nombres_sucursales() -> dict:
    nombres = {}
    try:
        db = get_db()
        tabla = get_table("rutas_config_sucursales")
        for suc in db.execute(select(tabla)):
            nt = str(suc.num_tienda or "")
            if nt and nt not in nombres:
                nombres[nt] = (
                    suc.nombre_base
                    or suc.nombre_tienda
                    or suc.nombre_pedido
                    or suc.nombre
                    or ""
                )
    except Exception as e:
        print(f"[_obtener_nombres_sucursales] Error: {e}")
    return nombres


def obtener_sucursales_disponibles() -> list:
    sucursales = {}
    try:
        db = get_db()
        tabla = get_table("rutas_config_sucursales")
        for suc in db.execute(select(tabla)):
            nt = str(suc.num_tienda or "")
            if nt and nt not in sucursales:
                sucursales[nt] = {
                    "num_tienda": suc.num_tienda,
                    "nombre": (
                        suc.nombre_base
                        or suc.nombre_tienda
                        or suc.nombre_pedido
                        or suc.nombre
                        or ""
                    ),
                    "latitud":  suc.latitud,
                    "longitud": suc.longitud,
                }
    except Exception as e:
        print(f"[obtener_sucursales_disponibles] Error: {e}")
    return list(sucursales.values())


def obtener_pesos(logistica_id: str) -> dict:
    """Lee los pesos desde `extraccion.datos` (columna JSON) para la logística activa."""
    oid = _id_valido(logistica_id)
    if not oid:
        return {}
    try:
        db  = get_db()
        tabla = get_table("extraccion")
        fila = db.execute(select(tabla.c.datos).where(tabla.c.logistica_id == oid)).mappings().first()
        if not fila or not fila["datos"]:
            return {}
        data  = json.loads(fila["datos"])
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


def _vehiculo_serializar(v) -> dict:
    """Normaliza una fila de la tabla `vehiculos` a los campos usados en la app."""
    cap = v["capacidad_toneladas"] or 0
    return {
        "_id":           v["mongo_id"],
        "placas":        v["placas"] or "",
        # abreviatura es el campo canónico; descripcion como fallback
        "abrev":         v["abreviatura"] or v["descripcion"] or "",
        "descripcion":   v["descripcion"] or "",
        "chofer":        v["chofer"] or "",
        "chofer_id":     v["chofer_id"],
        "capacidad_ton": float(cap),
        "volumen_m3":    float(v["volumen_m3"] or 0),
        "tipo":          v["categoria"] or "",
    }


def obtener_vehiculos() -> list:
    """Devuelve la flota activa de vehículos (sin datos de ocupación)."""
    try:
        db   = get_db()
        tabla = get_table("vehiculos")
        filas = db.execute(
            select(tabla).where(or_(tabla.c.activo == True, tabla.c.activo.is_(None)))  # noqa: E712
        ).mappings().all()
        return [_vehiculo_serializar(v) for v in filas]
    except Exception as e:
        print(f"[obtener_vehiculos modificacion] Error: {e}")
        return []


def obtener_disponibilidad_vehiculos(logistica_id: str) -> list:
    """
    Devuelve la flota activa enriquecida con ocupación por día, leída
    directamente desde `asignaciones_rutas` en SQL Server.

    Campos de ocupación por vehículo:
      · ocupacion   : { dia → { ruta_id, ruta_nombre } }
      · dias_ocupados: [días en que tiene ruta asignada]
      · dias_libres  : [días hábiles L-S sin ruta]
      · pct_semana  : % de días hábiles ocupados (sobre 6 días L-S)
    """
    oid = _id_valido(logistica_id)
    db  = get_db()

    # ── 1. Flota activa indexada por placas ───────────────────
    vehiculos: dict = {}
    try:
        tabla_veh = get_table("vehiculos")
        for v in db.execute(
            select(tabla_veh).where(or_(tabla_veh.c.activo == True, tabla_veh.c.activo.is_(None)))  # noqa: E712
        ).mappings():
            placas = v["placas"] or ""
            if placas:
                vd = _vehiculo_serializar(v)
                vd["ocupacion"] = {}   # { dia: { ruta_id, ruta_nombre } }
                vehiculos[placas] = vd
    except Exception as e:
        print(f"[obtener_disponibilidad_vehiculos] Error al leer vehículos: {e}")

    # ── 2. Ocupación desde asignaciones_rutas ─────────────────
    if oid:
        try:
            tabla_ar = get_table("asignaciones_rutas")
            for r in db.execute(select(tabla_ar).where(tabla_ar.c.logistica_id == oid)):
                placas = r.vehiculo_placas or ""
                if placas and placas in vehiculos:
                    vehiculos[placas]["ocupacion"][r.dia_semana] = {
                        "ruta_id":     r.ruta_key,
                        "ruta_nombre": r.nombre_ruta or r.ruta_key,
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
    Lee desde `asignaciones_rutas`/`asignaciones_sucursales`/`asignaciones_mayoristas`
    el estado guardado en Asignación (equivalente a `asignaciones.detalle_por_dia`
    en el esquema Mongo original: { dia: { ruta_id: {...} } }).

    Combina sucursales (coordenadas de `rutas_config_sucursales`) y mayoristas
    (`mayoristas_logic.calcular_distribucion_mayoristas`), respetando el orden
    guardado.
    """
    oid = _id_valido(logistica_id)
    if not oid:
        return {"status": "error", "mensaje": "logistica_id inválido."}

    cfg          = _obtener_config_general()
    min_descarga = float(cfg.get("min_descarga_por_kg") or MIN_DESCARGA_POR_KG)

    db = get_db()

    # ── 1. ¿Existe una asignación guardada? ───────────────────
    tabla_asig = get_table("asignaciones")
    if not db.execute(select(tabla_asig.c.mongo_id).where(tabla_asig.c.logistica_id == oid)).first():
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

    # chofer_overrides / orden_overrides desde tablas normalizadas
    chofer_overrides: dict = {}
    t_chof_ov = get_table("asignaciones_chofer_overrides")
    for r in db.execute(select(t_chof_ov).where(t_chof_ov.c.logistica_id == oid)):
        chofer_overrides[r.ruta_key] = {"nombre": r.nombre, "chofer_id": r.chofer_id}

    orden_overrides: dict = {}
    t_orden_ov = get_table("asignaciones_orden_overrides")
    for r in db.execute(select(t_orden_ov).where(t_orden_ov.c.logistica_id == oid)):
        orden_overrides.setdefault(r.ruta_key, []).append({"tipo": r.tipo, "key": r.item_key, "orden": r.orden})

    # Chofer por defecto de cada vehículo (tabla `vehiculos`), por placas
    chofer_por_placas: dict = {}
    try:
        tabla_veh = get_table("vehiculos")
        for v in db.execute(select(tabla_veh.c.placas, tabla_veh.c.chofer, tabla_veh.c.chofer_id)):
            plac = v.placas or ""
            if plac:
                chofer_por_placas[plac] = {"nombre": (v.chofer or "").strip(), "chofer_id": v.chofer_id}
    except Exception as e:
        print(f"[obtener_rutas_para_modificar] chofer_por_placas error: {e}")

    # ── 2. Coordenadas y nombres desde rutas_config ───────────
    coords_map = _obtener_coordenadas_sucursales()   # { num_tienda_str: {latitud, longitud} }
    rutas_db   = _obtener_rutas_db()                 # { ruta_id_str: {nombre} }

    # Mapa de coordenadas por sucursal desglosado por rutas_config._id.
    # NOTA: nunca hace match contra los ruta_id de asignaciones (son espacios
    # de ID distintos: "vrpaf_..."/"manual_..." vs mongo_id de rutas_config)
    # -- ya era así en el Mongo original, es un fallback heredado que
    # siempre queda vacío en la práctica. Se preserva tal cual.
    coords_por_ruta: dict = {}
    try:
        tabla_rc  = get_table("rutas_config")
        tabla_rcs = get_table("rutas_config_sucursales")
        sucs_por_rc: dict = {}
        for s in db.execute(select(tabla_rcs.c.ruta_config_id, tabla_rcs.c.num_tienda, tabla_rcs.c.latitud, tabla_rcs.c.longitud)):
            if s.num_tienda is None or s.latitud is None or s.longitud is None:
                continue
            sucs_por_rc.setdefault(s.ruta_config_id, {})[str(s.num_tienda)] = (float(s.latitud), float(s.longitud))
        for r in db.execute(select(tabla_rc.c.mongo_id)):
            cmap = sucs_por_rc.get(r.mongo_id)
            if cmap:
                coords_por_ruta[r.mongo_id] = cmap
    except Exception as e:
        print(f"[obtener_rutas_para_modificar] coords_por_ruta error: {e}")

    # ── 3. Preconstruir sucursales para cálculo de mayoristas ───
    nombres_map = _obtener_nombres_sucursales()   # { num_tienda_str: nombre }

    tabla_ar = get_table("asignaciones_rutas")
    tabla_as = get_table("asignaciones_sucursales")

    filas_rutas = db.execute(select(tabla_ar).where(tabla_ar.c.logistica_id == oid)).mappings().all()
    sucs_por_ruta_key: dict = {}
    for s in db.execute(select(tabla_as).where(tabla_as.c.logistica_id == oid)):
        sucs_por_ruta_key.setdefault(s.ruta_key, []).append(s)

    sucursales_por_ruta: dict = {}
    meta_por_ruta: dict = {}
    procesadas: set = set()

    for det in filas_rutas:
        ruta_id = det["ruta_key"]
        if not ruta_id or ruta_id in procesadas:
            continue
        procesadas.add(ruta_id)

        dia          = det["dia_semana"]
        placas       = det["vehiculo_placas"]        or ""
        veh_abrev    = det["vehiculo_abreviatura"]    or ""
        cap_ton      = det["capacidad_ton"]
        peso_kg      = float(det["peso_total_kg"]     or 0)
        pct          = float(det["porcentaje_utilizacion"] or 0)
        hora_salida  = det["hora_salida"]             or "08:00"
        hora_regreso = det["hora_regreso_estimada"]   or ""
        cumple_h     = det["cumple_horario"]
        if cumple_h is None:
            cumple_h = True
        nombre_r     = det["nombre_ruta"] or rutas_db.get(ruta_id, {}).get("nombre", ruta_id)

        cmap_ruta = coords_por_ruta.get(ruta_id, {})

        sucursales_norm: list = []
        for i, suc in enumerate(sucs_por_ruta_key.get(ruta_id, []), start=1):
            nt = str(suc.num_tienda or "")
            if nt in cmap_ruta:
                lat, lon = cmap_ruta[nt]
            elif nt in coords_map:
                lat = coords_map[nt]["latitud"]
                lon = coords_map[nt]["longitud"]
            else:
                lat, lon = None, None

            peso_suc  = float(suc.peso_kg or 0)
            orden_suc = suc.orden if suc.orden is not None else i
            nombre_raw = suc.nombre or ""
            nombre_suc = nombre_raw if (nombre_raw and nombre_raw != "Sucursal") \
                         else nombres_map.get(nt, "")
            sucursales_norm.append({
                "tipo":         "sucursal",
                "num_tienda":   suc.num_tienda,
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

    _rutas_para_mayoristas = [
        {"_id": rid, "sucursales": sucs, "cap_ton": meta_por_ruta.get(rid, {}).get("cap_ton")}
        for rid, sucs in sucursales_por_ruta.items()
    ]
    dist = (obtener_mayoristas_guardados(logistica_id, _rutas_para_mayoristas)
            or calcular_distribucion_mayoristas(logistica_id, _rutas_para_mayoristas))

    # mayoristas_overrides desde tabla normalizada (clave reconstruida a
    # documento(str)/id_cliente(int) -- ver _clave_a_python)
    overrides: dict = {}
    t_may_ov = get_table("asignaciones_mayoristas_overrides")
    for r in db.execute(select(t_may_ov).where(t_may_ov.c.logistica_id == oid)):
        ov = overrides.setdefault(r.ruta_key, {"excluidos": [], "incluidos": []})
        clave_py = _clave_a_python(r.clave)
        ov["excluidos" if r.tipo_override == "excluido" else "incluidos"].append(clave_py)

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

    # Mayoristas guardados directamente en la ruta (equivalente a
    # det_ruta.get("mayoristas") del esquema Mongo original)
    mayoristas_guardados_por_ruta: dict = {}
    t_am = get_table("asignaciones_mayoristas")
    for m in db.execute(select(t_am).where(t_am.c.logistica_id == oid)):
        mayoristas_guardados_por_ruta.setdefault(m.ruta_key, []).append({
            "id_cliente": m.id_cliente, "documento": m.documento, "nombre": m.nombre,
            "orden": m.orden, "peso_kg": m.peso_kg, "latitud": m.latitud, "longitud": m.longitud,
        })

    # ── 4. Construir lista de rutas ────────────────────────────
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

        mayoristas_norm = []
        ruta_ov = overrides.get(ruta_id, {})
        claves_incluidas = list(ruta_ov.get("incluidos", []) or [])
        if claves_incluidas or ruta_ov.get("excluidos"):
            vistos: set = set()
            for clave in claves_incluidas:
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
        elif ruta_id in mayoristas_guardados_por_ruta:
            mayoristas_norm = [dict(m) for m in mayoristas_guardados_por_ruta[ruta_id]]
        else:
            # Rutas generadas por VRP histórico/afinidad no guardan mayoristas
            # en asignaciones_mayoristas. Usar la distribución calculada
            # (histórico de mayoristas → proximidad geográfica).
            mayoristas_norm = [dict(m) for m in dist.get("mayoristas_por_ruta", {}).get(ruta_id, [])]

        may_orden_map = {
            _doc_key(m): m.get("orden")
            for m in dist.get("mayoristas_por_ruta", {}).get(ruta_id, [])
            if m.get("orden") is not None
        }
        for m in mayoristas_norm:
            dk = _doc_key(m)
            if dk in may_orden_map:
                m["orden"] = may_orden_map[dk]

        orden_ov = orden_overrides.get(ruta_id)
        if orden_ov:
            suc_orden_ov = {str(o.get("key")): o.get("orden") for o in orden_ov if o.get("tipo") == "sucursal"}
            may_orden_ov = {str(o.get("key")): o.get("orden") for o in orden_ov if o.get("tipo") == "mayorista"}
            for s in sucursales_norm:
                nt = str(s.get("num_tienda", ""))
                if nt in suc_orden_ov:
                    s["orden"] = suc_orden_ov[nt]
            for m in mayoristas_norm:
                dk = _doc_key(m)
                if dk in may_orden_ov:
                    m["orden"] = may_orden_ov[dk]

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

        peso_calc = (
            sum(s["peso_kg"] for s in sucursales_norm)
            + sum(m.get("peso_kg", 0) for m in mayoristas_norm)
        )
        if not peso_kg or abs(peso_kg - peso_calc) > 0.1:
            peso_kg = peso_calc

        if cap_ton and cap_ton > 0:
            pct = round((peso_kg / 1000 / cap_ton) * 100, 1)

        con_coords = sum(1 for s in sucursales_norm if s["latitud"] is not None)

        veh_chofer_info  = chofer_por_placas.get(placas, {})
        chofer_default    = veh_chofer_info.get("nombre", "")
        chofer_default_id = veh_chofer_info.get("chofer_id")
        override_val      = chofer_overrides.get(ruta_id)
        override_nombre   = _override_chofer_nombre(override_val)
        chofer_actual     = override_nombre or chofer_default
        chofer_actual_id  = _override_chofer_id(override_val) or (chofer_default_id if not override_nombre else None)

        rutas_normalizadas.append({
            "id":                    ruta_id,
            "nombre":                nombre_r,
            "tipo":                  "asignada",
            "dia":                   dia,
            "vehiculo_placas":       placas,
            "vehiculo_abrev":        veh_abrev,
            "capacidad_ton":         cap_ton,
            "chofer":                chofer_actual,
            "chofer_id":             chofer_actual_id,
            "chofer_default":        chofer_default,
            "chofer_personalizado":  bool(override_nombre),
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

    t_sp = get_table("asignaciones_sucursales_pendientes")
    sucursales_pendientes = [
        {"num_tienda": p.num_tienda, "nombre": p.nombre, "latitud": p.latitud, "longitud": p.longitud, "peso_kg": p.peso_kg}
        for p in db.execute(select(t_sp).where(t_sp.c.logistica_id == oid))
    ]
    t_rc_conf = get_table("asignaciones_rutas_confirmadas")
    rutas_confirmadas = [
        r.ruta_key for r in db.execute(select(t_rc_conf.c.ruta_key).where(t_rc_conf.c.logistica_id == oid))
    ]

    return {
        "status":                "ok",
        "logistica_id":          str(logistica_id),
        "total_rutas":           len(rutas_normalizadas),
        "rutas":                 rutas_normalizadas,
        "mayoristas_disponibles": mayoristas_disponibles,
        "sucursales_pendientes": sucursales_pendientes,
        "rutas_confirmadas":     rutas_confirmadas,
    }


# ── Cálculo de tiempos con caché SQL Server ────────────────────

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
    puntos_evitar: "list | None" = None,
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
    mayoristas: "list | None" = None,
    nombre_ruta: "str | None" = None,
) -> dict:
    oid = _id_valido(logistica_id)
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
    tabla_veh = get_table("vehiculos")
    veh = db.execute(
        select(tabla_veh).where(
            tabla_veh.c.placas == placas,
            or_(tabla_veh.c.activo == True, tabla_veh.c.activo.is_(None)),  # noqa: E712
        )
    ).mappings().first()
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
    abrev = veh["abreviatura"] or veh["descripcion"] or placas
    cap_ton = float(veh["capacidad_toneladas"] or 0)

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
    seen_may: set = set()
    for i, m in enumerate(may_list_raw, start=1):
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

    with transaccion() as conn:
        tabla_asig = get_table("asignaciones")
        fila_asig = conn.execute(select(tabla_asig.c.mongo_id).where(tabla_asig.c.logistica_id == oid)).first()
        if fila_asig:
            asignacion_id = fila_asig.mongo_id
        else:
            asignacion_id = str(ObjectId())
            conn.execute(insert(tabla_asig).values(mongo_id=asignacion_id, logistica_id=oid))

        conn.execute(insert(get_table("asignaciones_rutas")).values(
            asignacion_id=asignacion_id, logistica_id=oid, dia_semana=dia_key, ruta_key=ruta_id,
            nombre_ruta=nombre_ruta, vehiculo_placas=placas, vehiculo_abreviatura=abrev,
            capacidad_ton=cap_ton, peso_total_kg=peso_total_kg, porcentaje_utilizacion=pct,
            hora_salida="08:00", hora_regreso_estimada="", cumple_horario=True,
        ))
        if suc_det:
            conn.execute(insert(get_table("asignaciones_sucursales")), [
                {"asignacion_id": asignacion_id, "logistica_id": oid, "dia_semana": dia_key, "ruta_key": ruta_id, **s}
                for s in suc_det
            ])
        if may_det:
            conn.execute(insert(get_table("asignaciones_mayoristas")), [
                {"asignacion_id": asignacion_id, "logistica_id": oid, "dia_semana": dia_key, "ruta_key": ruta_id, **m}
                for m in may_det
            ])

        t_sp = get_table("asignaciones_sucursales_pendientes")
        if seen:
            conn.execute(delete(t_sp).where(t_sp.c.logistica_id == oid, t_sp.c.num_tienda.in_(seen)))

        pendientes = [
            {"num_tienda": p["num_tienda"], "nombre": p["nombre"], "latitud": p["latitud"], "longitud": p["longitud"], "peso_kg": p["peso_kg"]}
            for p in conn.execute(select(t_sp).where(t_sp.c.logistica_id == oid)).mappings()
        ]

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

    chofer_default    = (veh["chofer"] or "").strip()
    chofer_default_id = veh["chofer_id"]

    ruta_ui = {
        "id":                    ruta_id,
        "nombre":                nombre_ruta,
        "tipo":                  "manual",
        "dia":                   dia_key,
        "vehiculo_placas":       placas,
        "vehiculo_abrev":        abrev,
        "capacidad_ton":         cap_ton,
        "chofer":                chofer_default,
        "chofer_id":             chofer_default_id,
        "chofer_default":        chofer_default,
        "chofer_personalizado":  False,
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
    oid = _id_valido(logistica_id)
    if not oid:
        return {"status": "error", "mensaje": "logistica_id inválido."}

    dia_key = (dia or "").strip().lower()
    if dia_key not in ORDEN_DIAS:
        return {"status": "error", "mensaje": "Día inválido."}

    db = get_db()
    tabla_ar = get_table("asignaciones_rutas")
    existe = db.execute(
        select(tabla_ar.c.asignacion_id).where(
            tabla_ar.c.logistica_id == oid, tabla_ar.c.dia_semana == dia_key, tabla_ar.c.ruta_key == ruta_id
        )
    ).first()
    if not existe:
        return {"status": "error", "mensaje": "Ruta no encontrada."}

    coords_map = _obtener_coordenadas_sucursales()

    with transaccion() as conn:
        t_as = get_table("asignaciones_sucursales")
        sucs_ruta = conn.execute(
            select(t_as).where(t_as.c.logistica_id == oid, t_as.c.dia_semana == dia_key, t_as.c.ruta_key == ruta_id)
        ).mappings().all()

        t_sp = get_table("asignaciones_sucursales_pendientes")
        pendientes_existentes = {
            p.num_tienda for p in conn.execute(select(t_sp.c.num_tienda).where(t_sp.c.logistica_id == oid))
        }

        nuevos_pendientes = []
        for s in sucs_ruta:
            nt = s["num_tienda"]
            if nt is None or nt in pendientes_existentes:
                continue
            coord = coords_map.get(str(nt), {})
            nuevos_pendientes.append({
                "asignacion_id": existe.asignacion_id, "logistica_id": oid, "num_tienda": nt,
                "nombre":     s["nombre"] or f"Sucursal {nt}",
                "latitud":    coord.get("latitud"),
                "longitud":   coord.get("longitud"),
                "peso_kg":    float(s["peso_kg"] or 0),
            })
        if nuevos_pendientes:
            conn.execute(insert(t_sp), nuevos_pendientes)

        conn.execute(delete(t_as).where(t_as.c.logistica_id == oid, t_as.c.dia_semana == dia_key, t_as.c.ruta_key == ruta_id))
        t_am = get_table("asignaciones_mayoristas")
        conn.execute(delete(t_am).where(t_am.c.logistica_id == oid, t_am.c.dia_semana == dia_key, t_am.c.ruta_key == ruta_id))
        conn.execute(delete(tabla_ar).where(tabla_ar.c.logistica_id == oid, tabla_ar.c.dia_semana == dia_key, tabla_ar.c.ruta_key == ruta_id))

        t_rc = get_table("asignaciones_rutas_confirmadas")
        conn.execute(delete(t_rc).where(t_rc.c.logistica_id == oid, t_rc.c.ruta_key == ruta_id))

        pendientes = [
            {"num_tienda": p["num_tienda"], "nombre": p["nombre"], "latitud": p["latitud"], "longitud": p["longitud"], "peso_kg": p["peso_kg"]}
            for p in conn.execute(select(t_sp).where(t_sp.c.logistica_id == oid)).mappings()
        ]

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
    """Persiste el cambio de vehículo en asignaciones_rutas."""
    oid = _id_valido(logistica_id)
    if not oid:
        return {"status": "error", "mensaje": "logistica_id inválido"}
    try:
        db = get_db()
        tabla = get_table("asignaciones_rutas")
        db.execute(
            update(tabla)
            .where(tabla.c.logistica_id == oid, tabla.c.dia_semana == dia, tabla.c.ruta_key == ruta_id)
            .values(
                vehiculo_placas=vehiculo_placas or "",
                vehiculo_abreviatura=vehiculo_abreviatura or "",
                capacidad_ton=capacidad_ton,
            )
        )
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "mensaje": str(e)}


def actualizar_chofer_en_asignacion(
    logistica_id: str, ruta_id: str, dia: str, chofer: str, chofer_id: "str | None" = None,
) -> dict:
    """
    Cambia el chofer asignado a una ruta puntual de un día específico.

    No modifica el chofer por defecto del vehículo (tabla `vehiculos`): se
    guarda como override en `asignaciones_chofer_overrides` (una fila por
    ruta_key), para que sobreviva si se regenera o se vuelve a guardar la
    Asignación. Un chofer vacío elimina el override y la ruta vuelve a usar
    el chofer por defecto del vehículo.
    """
    oid = _id_valido(logistica_id)
    if not oid:
        return {"status": "error", "mensaje": "logistica_id inválido"}
    try:
        db = get_db()
        tabla_asig = get_table("asignaciones")
        fila_asig = db.execute(select(tabla_asig.c.mongo_id).where(tabla_asig.c.logistica_id == oid)).first()
        if not fila_asig:
            return {"status": "error", "mensaje": "No existe una asignación guardada."}
        asignacion_id = fila_asig.mongo_id

        tabla_ar = get_table("asignaciones_rutas")
        existe_ruta = db.execute(
            select(tabla_ar.c.ruta_key).where(
                tabla_ar.c.logistica_id == oid, tabla_ar.c.dia_semana == dia, tabla_ar.c.ruta_key == ruta_id
            )
        ).first()
        if not existe_ruta:
            return {"status": "error", "mensaje": "La ruta no existe en ese día."}

        chofer = (chofer or "").strip()
        t_ov = get_table("asignaciones_chofer_overrides")
        with transaccion() as conn:
            conn.execute(delete(t_ov).where(t_ov.c.logistica_id == oid, t_ov.c.ruta_key == ruta_id))
            if chofer:
                conn.execute(insert(t_ov).values(
                    asignacion_id=asignacion_id, logistica_id=oid, ruta_key=ruta_id,
                    nombre=chofer, chofer_id=chofer_id,
                ))
        return {"status": "ok", "chofer": chofer}
    except Exception as e:
        return {"status": "error", "mensaje": str(e)}


def actualizar_orden_paradas(logistica_id: str, ruta_id: str, dia: str, orden_paradas: list) -> dict:
    """
    Persiste la secuencia exacta de paradas (sucursales + mayoristas
    entrelazados) de una ruta, tal como quedó tras un drag & drop o un
    agregar/quitar parada en el editor de Modificación.

    `orden_paradas` es una lista de
        {"tipo": "sucursal"|"mayorista", "key": <num_tienda o documento/id_cliente>, "orden": int}

    Se guarda en `asignaciones_orden_overrides` (una fila por parada) y
    tiene prioridad sobre el orden geográfico que recalcula
    `calcular_distribucion_mayoristas()` en cada lectura, para que la
    secuencia elegida por el usuario no se pierda al recargar la página.
    """
    oid = _id_valido(logistica_id)
    if not oid:
        return {"status": "error", "mensaje": "logistica_id inválido"}
    if not isinstance(orden_paradas, list):
        return {"status": "error", "mensaje": "Se esperaba una lista de paradas."}
    try:
        limpio = []
        for item in orden_paradas:
            if not isinstance(item, dict):
                continue
            tipo  = item.get("tipo")
            key   = item.get("key")
            orden = item.get("orden")
            if tipo not in ("sucursal", "mayorista") or key is None or orden is None:
                continue
            try:
                limpio.append({"tipo": tipo, "item_key": str(key), "orden": int(orden)})
            except (TypeError, ValueError):
                continue

        with transaccion() as conn:
            tabla_asig = get_table("asignaciones")
            fila_asig = conn.execute(select(tabla_asig.c.mongo_id).where(tabla_asig.c.logistica_id == oid)).first()
            if fila_asig:
                asignacion_id = fila_asig.mongo_id
            else:
                asignacion_id = str(ObjectId())
                conn.execute(insert(tabla_asig).values(mongo_id=asignacion_id, logistica_id=oid))

            t_ov = get_table("asignaciones_orden_overrides")
            conn.execute(delete(t_ov).where(t_ov.c.logistica_id == oid, t_ov.c.ruta_key == ruta_id))
            if limpio:
                conn.execute(insert(t_ov), [
                    {"asignacion_id": asignacion_id, "logistica_id": oid, "ruta_key": ruta_id, **item}
                    for item in limpio
                ])
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
    Mueve una ruta de un día a otro. El vehículo asignado queda disponible
    en el día original y ocupado en el nuevo.
    """
    oid = _id_valido(logistica_id)
    if not oid:
        return {"status": "error", "mensaje": "logistica_id inválido"}

    dia_actual_k = (dia_actual or "").strip().lower()
    dia_nuevo_k  = (dia_nuevo  or "").strip().lower()

    if dia_actual_k not in ORDEN_DIAS or dia_nuevo_k not in ORDEN_DIAS:
        return {"status": "error", "mensaje": "Día inválido"}

    if dia_actual_k == dia_nuevo_k:
        return {"status": "ok"}

    try:
        db = get_db()
        tabla_ar = get_table("asignaciones_rutas")
        existe = db.execute(
            select(tabla_ar.c.ruta_key).where(
                tabla_ar.c.logistica_id == oid, tabla_ar.c.dia_semana == dia_actual_k, tabla_ar.c.ruta_key == ruta_id
            )
        ).first()
        if existe is None:
            return {"status": "error", "mensaje": f"Ruta {ruta_id} no encontrada en {dia_actual_k}"}

        with transaccion() as conn:
            for nombre_tabla in ("asignaciones_rutas", "asignaciones_sucursales", "asignaciones_mayoristas"):
                t = get_table(nombre_tabla)
                conn.execute(
                    update(t)
                    .where(t.c.logistica_id == oid, t.c.dia_semana == dia_actual_k, t.c.ruta_key == ruta_id)
                    .values(dia_semana=dia_nuevo_k)
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
    Elimina una sucursal de la ruta indicada y la registra en
    asignaciones_sucursales_pendientes para que persista entre recargas.
    """
    oid = _id_valido(logistica_id)
    if not oid:
        return {"status": "error", "mensaje": "logistica_id inválido"}
    try:
        db = get_db()
        tabla_asig = get_table("asignaciones")
        fila_asig = db.execute(select(tabla_asig.c.mongo_id).where(tabla_asig.c.logistica_id == oid)).first()
        if not fila_asig:
            return {"status": "error", "mensaje": "No se encontró la asignación"}
        asignacion_id = fila_asig.mongo_id

        tabla_ar = get_table("asignaciones_rutas")
        ruta_det = db.execute(
            select(tabla_ar).where(tabla_ar.c.logistica_id == oid, tabla_ar.c.dia_semana == dia, tabla_ar.c.ruta_key == ruta_id)
        ).mappings().first()
        if ruta_det is None:
            return {"status": "error", "mensaje": f"Ruta {ruta_id} no encontrada en {dia}"}

        nt = int(num_tienda)

        with transaccion() as conn:
            t_as = get_table("asignaciones_sucursales")
            conn.execute(delete(t_as).where(
                t_as.c.logistica_id == oid, t_as.c.dia_semana == dia, t_as.c.ruta_key == ruta_id, t_as.c.num_tienda == nt
            ))

            restantes = conn.execute(
                select(t_as).where(t_as.c.logistica_id == oid, t_as.c.dia_semana == dia, t_as.c.ruta_key == ruta_id)
                .order_by(t_as.c.orden)
            ).mappings().all()
            for i, s in enumerate(restantes, start=1):
                if s["orden"] != i:
                    conn.execute(update(t_as).where(
                        t_as.c.logistica_id == oid, t_as.c.dia_semana == dia, t_as.c.ruta_key == ruta_id, t_as.c.num_tienda == s["num_tienda"]
                    ).values(orden=i))

            peso_total = sum(float(s["peso_kg"] or 0) for s in restantes)
            cap_kg = float(ruta_det["capacidad_ton"] or 0) * 1000
            pct = round(peso_total / cap_kg * 100, 1) if cap_kg > 0 else 0
            conn.execute(update(tabla_ar).where(
                tabla_ar.c.logistica_id == oid, tabla_ar.c.dia_semana == dia, tabla_ar.c.ruta_key == ruta_id
            ).values(peso_total_kg=peso_total, porcentaje_utilizacion=pct))

            t_sp = get_table("asignaciones_sucursales_pendientes")
            conn.execute(delete(t_sp).where(t_sp.c.logistica_id == oid, t_sp.c.num_tienda == nt))
            conn.execute(insert(t_sp).values(
                asignacion_id=asignacion_id, logistica_id=oid, num_tienda=nt, nombre=nombre,
                latitud=latitud, longitud=longitud, peso_kg=float(peso_kg),
            ))
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
    Añade una sucursal a la ruta indicada y la elimina de
    asignaciones_sucursales_pendientes.
    """
    oid = _id_valido(logistica_id)
    if not oid:
        return {"status": "error", "mensaje": "logistica_id inválido"}
    try:
        db = get_db()
        tabla_asig = get_table("asignaciones")
        fila_asig = db.execute(select(tabla_asig.c.mongo_id).where(tabla_asig.c.logistica_id == oid)).first()
        if not fila_asig:
            return {"status": "error", "mensaje": "No se encontró la asignación"}
        asignacion_id = fila_asig.mongo_id

        tabla_ar = get_table("asignaciones_rutas")
        ruta_det = db.execute(
            select(tabla_ar).where(tabla_ar.c.logistica_id == oid, tabla_ar.c.dia_semana == dia, tabla_ar.c.ruta_key == ruta_id)
        ).mappings().first()
        if ruta_det is None:
            return {"status": "error", "mensaje": f"Ruta {ruta_id} no encontrada en {dia}"}

        nt = int(num_tienda)
        # Garantizar nombre correcto: si viene vacío o es el placeholder "Sucursal",
        # resolverlo desde rutas_config (fuente de verdad)
        nombre_final = nombre if (nombre and nombre != "Sucursal") \
                       else _obtener_nombres_sucursales().get(str(nt), "")

        with transaccion() as conn:
            t_as = get_table("asignaciones_sucursales")
            conn.execute(delete(t_as).where(
                t_as.c.logistica_id == oid, t_as.c.dia_semana == dia, t_as.c.ruta_key == ruta_id, t_as.c.num_tienda == nt
            ))
            max_orden = conn.execute(
                select(func.max(t_as.c.orden)).where(t_as.c.logistica_id == oid, t_as.c.dia_semana == dia, t_as.c.ruta_key == ruta_id)
            ).scalar() or 0
            conn.execute(insert(t_as).values(
                asignacion_id=asignacion_id, logistica_id=oid, dia_semana=dia, ruta_key=ruta_id,
                num_tienda=nt, nombre=nombre_final, orden=max_orden + 1, peso_kg=float(peso_kg),
            ))

            peso_total = conn.execute(
                select(func.sum(t_as.c.peso_kg)).where(t_as.c.logistica_id == oid, t_as.c.dia_semana == dia, t_as.c.ruta_key == ruta_id)
            ).scalar() or 0.0
            cap_kg = float(ruta_det["capacidad_ton"] or 0) * 1000
            pct = round(peso_total / cap_kg * 100, 1) if cap_kg > 0 else 0
            conn.execute(update(tabla_ar).where(
                tabla_ar.c.logistica_id == oid, tabla_ar.c.dia_semana == dia, tabla_ar.c.ruta_key == ruta_id
            ).values(peso_total_kg=peso_total, porcentaje_utilizacion=pct))

            t_sp = get_table("asignaciones_sucursales_pendientes")
            conn.execute(delete(t_sp).where(t_sp.c.logistica_id == oid, t_sp.c.num_tienda == nt))
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
    asignaciones_mayoristas_overrides (tipo_override='excluido').
    La clave almacenada es `documento` (str) si está presente; si no, `id_cliente` (int, legacy).
    """
    oid = _id_valido(logistica_id)
    if not oid:
        return {"status": "error", "mensaje": "logistica_id inválido"}
    try:
        db = get_db()
        tabla_asig = get_table("asignaciones")
        fila_asig = db.execute(select(tabla_asig.c.mongo_id).where(tabla_asig.c.logistica_id == oid)).first()
        if not fila_asig:
            return {"status": "error", "mensaje": "No se encontró la asignación"}
        asignacion_id = fila_asig.mongo_id

        clave = documento if documento else str(int(id_cliente))

        t_ov = get_table("asignaciones_mayoristas_overrides")
        with transaccion() as conn:
            conn.execute(delete(t_ov).where(
                t_ov.c.logistica_id == oid, t_ov.c.ruta_key == ruta_id,
                t_ov.c.clave == clave, t_ov.c.tipo_override == "incluido",
            ))
            existe = conn.execute(
                select(t_ov.c.clave).where(
                    t_ov.c.logistica_id == oid, t_ov.c.ruta_key == ruta_id,
                    t_ov.c.clave == clave, t_ov.c.tipo_override == "excluido",
                )
            ).first()
            if not existe:
                conn.execute(insert(t_ov).values(
                    asignacion_id=asignacion_id, logistica_id=oid, ruta_key=ruta_id,
                    tipo_override="excluido", clave=clave,
                ))
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
    oid = _id_valido(logistica_id)
    if not oid:
        return {"status": "error", "mensaje": "logistica_id inválido"}
    try:
        db = get_db()
        tabla_asig = get_table("asignaciones")
        fila_asig = db.execute(select(tabla_asig.c.mongo_id).where(tabla_asig.c.logistica_id == oid)).first()
        if not fila_asig:
            return {"status": "error", "mensaje": "No se encontró la asignación"}
        asignacion_id = fila_asig.mongo_id

        tabla_ar = get_table("asignaciones_rutas")
        ruta_det = db.execute(
            select(tabla_ar).where(tabla_ar.c.logistica_id == oid, tabla_ar.c.dia_semana == dia, tabla_ar.c.ruta_key == ruta_id)
        ).mappings().first()
        if ruta_det is None:
            return {"status": "error", "mensaje": f"Ruta {ruta_id} no encontrada en {dia}"}

        clave = documento if documento else str(int(id_cliente))

        nuevo_peso_kg = float(peso_ruta_actual) + float(peso_kg)
        cap_ton = float(ruta_det["capacidad_ton"] or 0)
        vehiculo_cambiado = False
        nuevo_vehiculo = None

        with transaccion() as conn:
            t_ov = get_table("asignaciones_mayoristas_overrides")
            # Limpiar este documento de cualquier override "incluido" previo en OTRAS rutas
            conn.execute(delete(t_ov).where(
                t_ov.c.logistica_id == oid, t_ov.c.ruta_key != ruta_id,
                t_ov.c.clave == clave, t_ov.c.tipo_override == "incluido",
            ))
            # En esta ruta: quitar de excluidos, añadir a incluidos (si no estaba)
            conn.execute(delete(t_ov).where(
                t_ov.c.logistica_id == oid, t_ov.c.ruta_key == ruta_id,
                t_ov.c.clave == clave, t_ov.c.tipo_override == "excluido",
            ))
            existe_incluido = conn.execute(
                select(t_ov.c.clave).where(
                    t_ov.c.logistica_id == oid, t_ov.c.ruta_key == ruta_id,
                    t_ov.c.clave == clave, t_ov.c.tipo_override == "incluido",
                )
            ).first()
            if not existe_incluido:
                conn.execute(insert(t_ov).values(
                    asignacion_id=asignacion_id, logistica_id=oid, ruta_key=ruta_id,
                    tipo_override="incluido", clave=clave,
                ))

            # Verificar capacidad y buscar vehículo alternativo si hace falta
            if cap_ton > 0 and (nuevo_peso_kg / 1000) > cap_ton:
                t_veh = get_table("vehiculos")
                placas_en_dia = {
                    r.vehiculo_placas for r in conn.execute(
                        select(tabla_ar.c.vehiculo_placas).where(
                            tabla_ar.c.logistica_id == oid, tabla_ar.c.dia_semana == dia, tabla_ar.c.ruta_key != ruta_id
                        )
                    )
                }
                candidatos = sorted(
                    [
                        v for v in conn.execute(
                            select(t_veh).where(or_(t_veh.c.activo == True, t_veh.c.activo.is_(None)))  # noqa: E712
                        ).mappings()
                        if float(v["capacidad_toneladas"] or 0) * 1000 >= nuevo_peso_kg
                        and (v["placas"] or "") not in placas_en_dia
                        and v["placas"] != ruta_det["vehiculo_placas"]
                    ],
                    key=lambda v: float(v["capacidad_toneladas"] or 0),
                )
                if candidatos:
                    mejor = candidatos[0]
                    abrev = mejor["abreviatura"] or mejor["descripcion"] or mejor["placas"] or ""
                    cap_nueva = float(mejor["capacidad_toneladas"] or 0)
                    vehiculo_cambiado = True
                    nuevo_vehiculo = {"placas": mejor["placas"] or "", "abrev": abrev, "capacidad_ton": cap_nueva}

            if vehiculo_cambiado and nuevo_vehiculo:
                conn.execute(update(tabla_ar).where(
                    tabla_ar.c.logistica_id == oid, tabla_ar.c.dia_semana == dia, tabla_ar.c.ruta_key == ruta_id
                ).values(
                    vehiculo_placas=nuevo_vehiculo["placas"],
                    vehiculo_abreviatura=nuevo_vehiculo["abrev"],
                    capacidad_ton=nuevo_vehiculo["capacidad_ton"],
                ))

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
    Persiste el estado de confirmación de rutas en asignaciones_rutas_confirmadas.
    ruta_ids es la lista completa de IDs confirmados en este momento.
    """
    oid = _id_valido(logistica_id)
    if not oid:
        return {"status": "error", "mensaje": "logistica_id inválido"}
    try:
        with transaccion() as conn:
            tabla_asig = get_table("asignaciones")
            fila_asig = conn.execute(select(tabla_asig.c.mongo_id).where(tabla_asig.c.logistica_id == oid)).first()
            if fila_asig:
                asignacion_id = fila_asig.mongo_id
            else:
                asignacion_id = str(ObjectId())
                conn.execute(insert(tabla_asig).values(mongo_id=asignacion_id, logistica_id=oid))

            t_rc = get_table("asignaciones_rutas_confirmadas")
            conn.execute(delete(t_rc).where(t_rc.c.logistica_id == oid))
            filas = [{"asignacion_id": asignacion_id, "logistica_id": oid, "ruta_key": rid} for rid in ruta_ids]
            if filas:
                conn.execute(insert(t_rc), filas)
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "mensaje": str(e)}


# ── Guardar / recuperar modificación ──────────────────────────

def guardar_modificacion(payload: dict, logistica_id: str) -> dict:
    """
    Persiste la modificación en `modificaciones_rutas` (fila base) +
    `modificacion_rutas`/`modificacion_ruta_sucursales`/`modificacion_ruta_mayoristas`
    (una fila por ruta/sucursal/mayorista de payload["rutas_confirmadas"]).

    Reemplazo completo (como el $set atómico de un solo documento en Mongo):
    todo en una sola transacción.
    """
    oid = _id_valido(logistica_id)
    if not oid:
        return {"status": "error", "mensaje": "logistica_id inválido."}

    guardado_en = datetime.now().isoformat()
    fecha_modificacion = payload.get("fecha_modificacion")
    rutas_confirmadas = payload.get("rutas_confirmadas") or []
    if not isinstance(rutas_confirmadas, list):
        rutas_confirmadas = []

    try:
        with transaccion() as conn:
            tabla_base = get_table("modificaciones_rutas")
            fila_base = conn.execute(select(tabla_base.c.mongo_id).where(tabla_base.c.logistica_id == oid)).first()
            if fila_base:
                modificacion_id = fila_base.mongo_id
                conn.execute(update(tabla_base).where(tabla_base.c.mongo_id == modificacion_id).values(
                    fecha_modificacion=fecha_modificacion, guardado_en=guardado_en,
                ))
            else:
                modificacion_id = str(ObjectId())
                conn.execute(insert(tabla_base).values(
                    mongo_id=modificacion_id, logistica_id=oid,
                    fecha_modificacion=fecha_modificacion, guardado_en=guardado_en,
                ))

            t_hdr = get_table("modificacion_rutas")
            t_suc = get_table("modificacion_ruta_sucursales")
            t_may = get_table("modificacion_ruta_mayoristas")

            conn.execute(delete(t_suc).where(t_suc.c.modificacion_id == modificacion_id))
            conn.execute(delete(t_may).where(t_may.c.modificacion_id == modificacion_id))
            conn.execute(delete(t_hdr).where(t_hdr.c.modificacion_id == modificacion_id))

            filas_hdr, filas_suc, filas_may = [], [], []
            for ruta in rutas_confirmadas:
                if not isinstance(ruta, dict):
                    continue
                ruta_key = ruta.get("id")
                if not ruta_key:
                    continue
                filas_hdr.append({
                    "modificacion_id": modificacion_id, "logistica_id": oid, "guardado_en": guardado_en,
                    "ruta_key": ruta_key,
                    "nombre": ruta.get("nombre"), "tipo": ruta.get("tipo"), "dia": ruta.get("dia"),
                    "vehiculo_abrev": ruta.get("vehiculo_abrev"), "vehiculo_placas": ruta.get("vehiculo_placas"),
                    "chofer": ruta.get("chofer"), "chofer_id": ruta.get("chofer_id"),
                    "chofer_personalizado": bool(ruta.get("chofer_personalizado")),
                    "capacidad_ton": ruta.get("capacidad_ton"), "peso_kg": ruta.get("peso_kg"),
                    "peso_ton": ruta.get("peso_ton"), "pct_utilizacion": ruta.get("pct_utilizacion"),
                    "conduccion_min": ruta.get("conduccion_min"), "descarga_min": ruta.get("descarga_min"),
                    "extra_min": ruta.get("extra_min"), "total_min": ruta.get("total_min"),
                    "distancia_km": ruta.get("distancia_km"), "hora_salida": ruta.get("hora_salida"),
                    "hora_regreso": ruta.get("hora_regreso"), "origen_tiempo": ruta.get("origen_tiempo"),
                    "es_personalizada": bool(ruta.get("es_personalizada")),
                    "num_sucursales": ruta.get("num_sucursales"),
                    "geometria_osrm_json": json.dumps(ruta.get("geometria_osrm")) if ruta.get("geometria_osrm") is not None else None,
                    "via_points_json":     json.dumps(ruta.get("via_points"))     if ruta.get("via_points")     is not None else None,
                    "puntos_evitar_json":  json.dumps(ruta.get("puntos_evitar"))  if ruta.get("puntos_evitar")  is not None else None,
                })
                for s in (ruta.get("sucursales") or []):
                    if not isinstance(s, dict):
                        continue
                    filas_suc.append({
                        "modificacion_id": modificacion_id, "ruta_key": ruta_key,
                        "num_tienda": s.get("num_tienda"), "nombre": s.get("nombre"), "orden": s.get("orden"),
                        "peso_kg": s.get("peso_kg"), "descarga_min": s.get("descarga_min"),
                        "latitud": s.get("latitud"), "longitud": s.get("longitud"),
                    })
                for m in (ruta.get("mayoristas") or []):
                    if not isinstance(m, dict):
                        continue
                    filas_may.append({
                        "modificacion_id": modificacion_id, "ruta_key": ruta_key,
                        "id_cliente": m.get("id_cliente"), "documento": m.get("documento"), "nombre": m.get("nombre"),
                        "orden": m.get("orden"), "peso_kg": m.get("peso_kg"),
                        "latitud": m.get("latitud"), "longitud": m.get("longitud"),
                    })

            # t_hdr va fila por fila (no en un solo insert por lotes): cada fila
            # trae geometria_osrm_json/via_points_json (hasta ~100 KB c/u).
            # SQLAlchemy agrupa un insert-por-lotes en una sola sentencia con
            # todos los parámetros juntos — con 25+ rutas eso pasa de varios MB
            # en una sola sentencia, y en esta máquina (6 GB RAM, ~340 MB libres
            # medidos) eso truena SQL Server con error 802 "insufficient memory
            # available in the buffer pool". Uno por uno evita el pico de memoria.
            for fila in filas_hdr:
                conn.execute(insert(t_hdr), fila)
            if filas_suc:
                conn.execute(insert(t_suc), filas_suc)
            if filas_may:
                conn.execute(insert(t_may), filas_may)

        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "mensaje": str(e)}


def obtener_modificacion_previa(logistica_id: str) -> dict:
    """Devuelve la modificación guardada para la logística activa, reconstruyendo
    rutas_confirmadas desde las tablas normalizadas."""
    oid = _id_valido(logistica_id)
    if not oid:
        return {}
    try:
        db = get_db()
        tabla_base = get_table("modificaciones_rutas")
        base = db.execute(select(tabla_base).where(tabla_base.c.logistica_id == oid)).mappings().first()
        if not base:
            return {}

        modificacion_id = base["mongo_id"]
        t_hdr = get_table("modificacion_rutas")
        t_suc = get_table("modificacion_ruta_sucursales")
        t_may = get_table("modificacion_ruta_mayoristas")

        sucs_por_ruta: dict = {}
        for s in db.execute(select(t_suc).where(t_suc.c.modificacion_id == modificacion_id)).mappings():
            sucs_por_ruta.setdefault(s["ruta_key"], []).append({
                "num_tienda": s["num_tienda"], "nombre": s["nombre"], "orden": s["orden"],
                "peso_kg": s["peso_kg"], "descarga_min": s["descarga_min"],
                "latitud": s["latitud"], "longitud": s["longitud"],
            })

        mays_por_ruta: dict = {}
        for m in db.execute(select(t_may).where(t_may.c.modificacion_id == modificacion_id)).mappings():
            mays_por_ruta.setdefault(m["ruta_key"], []).append({
                "id_cliente": m["id_cliente"], "documento": m["documento"], "nombre": m["nombre"],
                "orden": m["orden"], "peso_kg": m["peso_kg"], "latitud": m["latitud"], "longitud": m["longitud"],
            })

        rutas_confirmadas = []
        for r in db.execute(select(t_hdr).where(t_hdr.c.modificacion_id == modificacion_id)).mappings():
            ruta_key = r["ruta_key"]
            rutas_confirmadas.append({
                "id": ruta_key, "nombre": r["nombre"], "tipo": r["tipo"], "dia": r["dia"],
                "vehiculo_abrev": r["vehiculo_abrev"], "vehiculo_placas": r["vehiculo_placas"],
                "chofer": r["chofer"], "chofer_id": r["chofer_id"],
                "chofer_personalizado": bool(r["chofer_personalizado"]),
                "capacidad_ton": r["capacidad_ton"], "peso_kg": r["peso_kg"], "peso_ton": r["peso_ton"],
                "pct_utilizacion": r["pct_utilizacion"], "conduccion_min": r["conduccion_min"],
                "descarga_min": r["descarga_min"], "extra_min": r["extra_min"], "total_min": r["total_min"],
                "distancia_km": r["distancia_km"], "hora_salida": r["hora_salida"], "hora_regreso": r["hora_regreso"],
                "origen_tiempo": r["origen_tiempo"], "es_personalizada": bool(r["es_personalizada"]),
                "num_sucursales": r["num_sucursales"],
                "geometria_osrm": json.loads(r["geometria_osrm_json"]) if r["geometria_osrm_json"] else None,
                "via_points":     json.loads(r["via_points_json"])     if r["via_points_json"]     else None,
                "puntos_evitar":  json.loads(r["puntos_evitar_json"])  if r["puntos_evitar_json"]  else None,
                "sucursales": sucs_por_ruta.get(ruta_key, []),
                "mayoristas": mays_por_ruta.get(ruta_key, []),
            })

        doc = dict(base)
        doc.pop("mongo_id", None)
        doc["logistica_id"] = str(doc["logistica_id"])
        doc["rutas_confirmadas"] = rutas_confirmadas
        return doc
    except Exception:
        return {}
