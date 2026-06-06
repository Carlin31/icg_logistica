"""
logic/asignacion_logic.py
Lógica de negocio para la Sección 3 — Asignación de Rutas.

Cambios v5:
  - CAP-2 corregido: util_max_default = 100 % (era 120 %).
  - CAP-3 eliminado: se suprime el fallback util_max+30 % (umbral 150 %). Cualquier
    vehículo con pct > util_max queda descartado directamente; no existe banda de
    tolerancia adicional.
  - OPE-2 corregido: si la ruta tiene requisito volumétrico (vol_ruta > 0) y el
    vehículo no tiene vol_m3 definido, se aplica ESTRATEGIA_VOLUMEN_NULO
    (BLOQUEAR | CONSERVADOR), configurable desde MongoDB.
  - MAY-1 implementado: exclusividad de mayorista por ruta y día. Un mayorista
    confirmado en una ruta no puede ser candidato en otra ruta del mismo día.
  - MAY-2 implementado: pasada de optimización independiente con mayoristas tras
    la asignación principal. Agrega/quita mayoristas para acercar la ocupación
    al 100 %; si la sobrecarga residual no puede resolverse quitando mayoristas,
    escala a un vehículo de mayor capacidad o libera la ruta (OPE-7).
  - ORD-2 respetado: el orden ascendente de la fase principal no se altera. La
    fase MAY-2 usa orden descendente exclusivamente en su propia iteración.
    - PLAN-1 implementado: ordenamiento de sucursales replicando la lógica de
        planificación de app_rutas.py:
            · Si la secuencia de visita es única, se respeta.
            · Si hay secuencias faltantes/duplicadas, se ordena por nearest-neighbor
                iniciando por la sucursal más al norte.

Cambios v4 (conservados):
  - _normalizar_dia(): normaliza nombres de día (minúsculas + sin tildes).
  - _dias_candidatos(): respeta dia_sugerido/dia_programado sin mover rutas.
  - Fallback sin vehículo: conserva el día configurado de la ruta.
"""
import math
import time
import unicodedata
import urllib.request
import urllib.error
from datetime import datetime
from bson import ObjectId
from bson.errors import InvalidId

from db import get_db
from logic.mayoristas_logic import calcular_distribucion_mayoristas

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

DIAS_ORDEN = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]

# Rango óptimo por defecto (se sobreescribe con la config de MongoDB)
# CAP-1 / CAP-2: util_max = 100 % (corregido; era 120 %).
# CAP-3 eliminado: ya no existe umbral fallback de util_max+30 % (150 %).
UTIL_MIN_DEFAULT = 80   # %
UTIL_MAX_DEFAULT = 100  # %  ← corregido de 120 a 100

# OPE-2: estrategia cuando el vehículo no tiene vol_m3 definido y la ruta
# tiene requisito volumétrico. Configurable desde MongoDB.
#   "BLOQUEAR"    → descarta el vehículo hasta que se cargue el dato de volumen.
#   "CONSERVADOR" → estima vol_m3 = capacidad_ton × FACTOR_VOLUMEN_POR_DEFECTO.
ESTRATEGIA_VOLUMEN_NULO_DEFAULT   = "BLOQUEAR"
FACTOR_VOLUMEN_POR_DEFECTO_DEFAULT = 2.5   # m³ por tonelada de capacidad

# MAY-2: radio máximo para considerar mayoristas candidatos (configurable).
RADIO_MAYORISTAS_KM_DEFAULT = 50.0

# ══════════════════════════════════════════════════════════════════════
# FASE 3 — REACOMODAMIENTO (Sección 5)
#
# Algoritmo de asignación de vehículos a rutas:
#   1. Ordenar rutas por total_kg ascendente (más ligera primero).
#   2. Ordenar vehículos por capacidad_kg ascendente (más pequeño primero).
#   3. Cada vehículo se usa como máximo una vez por día.
#   4. Para cada ruta: asignar el vehículo más pequeño donde
#      route_kg ≤ vehicle_capacity_kg × (1 + VRP_CAP_TOLERANCE).
#   5. Si no hay vehículo elegible → ruta sin vehículo.
#
#   VRP_CAP_TOLERANCE = 0.30 → rango válido 70%–130%
# ══════════════════════════════════════════════════════════════════════
VRP_CAP_TOLERANCE = 0.30   # ±30 % → rango válido 70%–130%


# ── Helpers genéricos ──────────────────────────────────────────

def _normalizar_dia(s: str) -> str:
    """
    Normaliza el nombre de un día a minúsculas sin tildes.

    Necesario porque creacion_rutas almacena dia_sugerido con mayúscula inicial
    y tildes ('Miércoles', 'Jueves'…), mientras que asignacion_logic trabaja con
    claves sin tildes ('miercoles', 'jueves'…).

    Sin esta normalización, 'miercoles' in 'miércoles' es False en Python,
    provocando que las rutas de esos días nunca se emparejaran.
    """
    s = (s or "").lower().strip()
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )


def _serialize(doc: dict) -> dict:
    doc = dict(doc)
    if "_id" in doc and isinstance(doc["_id"], ObjectId):
        doc["_id"] = str(doc["_id"])
    return doc


def _parse_oid(doc_id: str) -> "ObjectId | None":
    try:
        return ObjectId(doc_id)
    except (InvalidId, TypeError):
        return None


def _parse_hhmm(s: str) -> "int | None":
    if not s:
        return None
    try:
        h, m = s.split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return None


def _capacidad_efectiva_ton(cap) -> float:
    """
    Regla especial: unidades de 3.5 t se consideran con tolerancia
    hasta 4.0 t para marcar sobrecarga.
    """
    try:
        c = float(cap or 0)
    except (TypeError, ValueError):
        return 0.0
    if c <= 0:
        return 0.0
    return 4.0 if abs(c - 3.5) < 0.05 else c


def _pct_utilizacion(peso_ton: float, cap_ton) -> float:
    cap_eff = _capacidad_efectiva_ton(cap_ton)
    return (peso_ton / cap_eff) * 100 if cap_eff > 0 else 0.0


def _minutos_a_hhmm(min_total: float) -> str:
    h = int(min_total // 60)
    m = int(round(min_total % 60))
    return f"{h:02d}:{m:02d}"


def _obtener_config_general() -> dict:
    try:
        db  = get_db()
        cfg = db["configuracion"].find_one({"_tipo": {"$exists": False}}) or {}
        return cfg
    except Exception:
        return {}


def _leer_rango_utilizacion() -> tuple:
    """
    Lee util_min y util_max desde la configuración general.
    Retorna (util_min_pct, util_max_pct) como floats (ej. 80.0, 100.0).
    """
    try:
        cfg      = _obtener_config_general()
        util_min = float(cfg.get("utilizacion_min") or UTIL_MIN_DEFAULT)
        util_max = float(cfg.get("utilizacion_max") or UTIL_MAX_DEFAULT)
        return util_min, util_max
    except Exception:
        return float(UTIL_MIN_DEFAULT), float(UTIL_MAX_DEFAULT)


def _leer_config_volumen() -> tuple:
    """
    Lee los parámetros de estrategia volumétrica desde la configuración general.

    Retorna (estrategia, factor_vol, radio_km) donde:
      · estrategia  — "BLOQUEAR" | "CONSERVADOR"  (OPE-2)
      · factor_vol  — m³ por tonelada de capacidad, usado cuando CONSERVADOR
      · radio_km    — radio máximo para candidatos mayoristas  (MAY-2)
    """
    try:
        cfg        = _obtener_config_general()
        estrategia = (
            cfg.get("estrategia_volumen_nulo") or ESTRATEGIA_VOLUMEN_NULO_DEFAULT
        ).upper().strip()
        factor_vol = float(
            cfg.get("factor_volumen_por_defecto") or FACTOR_VOLUMEN_POR_DEFECTO_DEFAULT
        )
        radio_km   = float(
            cfg.get("radio_mayoristas_km") or RADIO_MAYORISTAS_KM_DEFAULT
        )
        return estrategia, factor_vol, radio_km
    except Exception:
        return ESTRATEGIA_VOLUMEN_NULO_DEFAULT, float(FACTOR_VOLUMEN_POR_DEFECTO_DEFAULT), float(RADIO_MAYORISTAS_KM_DEFAULT)


def _leer_config_reacomodamiento() -> dict:
    """
    Lee los parámetros de la Fase 3 (reacomodamiento) desde MongoDB.
    Retorna { cap_tolerance } donde cap_tolerance define el rango válido:
    (1 - cap_tolerance)*100 % a (1 + cap_tolerance)*100 %.
    """
    try:
        cfg = _obtener_config_general()
        return {
            "cap_tolerance": float(cfg.get("vrp_cap_tolerance", VRP_CAP_TOLERANCE)),
        }
    except Exception:
        return {"cap_tolerance": VRP_CAP_TOLERANCE}


def _es_vol_compatible(vehiculo: dict, vol_ruta: float,
                        estrategia: str, factor_vol: float) -> bool:
    """
    OPE-2 — Restricción volumétrica corregida.

    Reglas:
      1. Si vol_ruta <= 0 → sin requisito volumétrico → siempre compatible.
      2. Si el vehículo tiene vol_m3 definido → vol_ruta < vol_m3 (estricto).
      3. Si el vehículo NO tiene vol_m3:
         · BLOQUEAR    → incompatible (no asignar hasta que se cargue el dato).
         · CONSERVADOR → estimar vol_m3 = capacidad_ton × factor_vol y aplicar.
    """
    if vol_ruta <= 0:
        return True

    vol_v = vehiculo.get("volumen_m3") or 0
    if vol_v > 0:
        return vol_ruta < vol_v

    # vol_m3 no definido
    if estrategia == "CONSERVADOR":
        cap_ton   = vehiculo.get("capacidad_toneladas") or 0
        vol_estim = cap_ton * factor_vol if cap_ton > 0 else 0.0
        return vol_ruta < vol_estim

    # BLOQUEAR (default): descartar el vehículo
    return False


# ═══════════════════════════════════════════════════════════════
# OSRM — Caché en MongoDB
# ═══════════════════════════════════════════════════════════════

def _cache_key(coords: list) -> str:
    return ";".join(f"{lat:.5f},{lon:.5f}" for lat, lon in coords)


def _cargar_cache(clave: str) -> "dict | None":
    try:
        db  = get_db()
        doc = db["cache_osrm"].find_one({"clave": clave, "tipo": "tiempos"})
        return doc["resultado"] if doc else None
    except Exception:
        return None


def _guardar_cache(clave: str, resultado: dict) -> None:
    try:
        db = get_db()
        db["cache_osrm"].update_one(
            {"clave": clave, "tipo": "tiempos"},
            {"$set": {"resultado": resultado, "actualizado_en": datetime.now().isoformat()}},
            upsert=True,
        )
    except Exception as e:
        print(f"[OSRM cache] Error al guardar: {e}")


def consultar_osrm(coords: list) -> dict:
    if len(coords) < 2:
        return {"distancia_km": 0.0, "traslado_min": 0.0, "origen": "osrm"}

    import json as _json
    waypoints    = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in coords)
    url          = f"{OSRM_BASE_URL}/{waypoints}?overview=false"
    ultimo_error = None

    for intento in range(OSRM_MAX_RETRIES):
        try:
            if intento > 0:
                time.sleep(OSRM_RETRY_DELAY)
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "ICG-RouteAssignment/1.0", "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=OSRM_TIMEOUT) as resp:
                data = _json.loads(resp.read().decode("utf-8"))

            if data.get("code") != "Ok" or not data.get("routes"):
                ultimo_error = f"OSRM code={data.get('code', '?')}"
                continue

            ruta = data["routes"][0]
            return {
                "distancia_km": round(ruta.get("distance", 0.0) / 1000, 2),
                "traslado_min": round(ruta.get("duration", 0.0) / 60, 1),
                "origen": "osrm",
            }
        except urllib.error.HTTPError as e:
            ultimo_error = f"HTTP {e.code}"
            if e.code == 429:
                time.sleep(OSRM_RETRY_DELAY * 2)
        except Exception as e:
            ultimo_error = str(e)

    return {"error": ultimo_error or "Agotados reintentos OSRM", "origen": "osrm_error"}


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a    = (math.sin(dlat / 2) ** 2
            + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
            * math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _to_float(valor):
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def _extraer_secuencia_sucursal(sucursal: dict) -> "int | None":
    for key in ("secuencia_visita", "orden", "secuencia", "seq"):
        valor = sucursal.get(key)
        if valor is None:
            continue
        try:
            return int(float(valor))
        except (TypeError, ValueError):
            continue
    return None


def _ordenar_sucursales_planificacion(sucursales: list) -> list:
    """
    Replica el criterio de app_rutas.py para ordenar paradas de sucursales:
      1) Si la secuencia de visita es única y completa, se respeta.
      2) Si no, aplica nearest-neighbor iniciando por la sucursal más al norte.
    """
    if not isinstance(sucursales, list) or len(sucursales) <= 1:
        return sucursales or []

    secuencias = [_extraer_secuencia_sucursal(s) for s in sucursales]
    if all(seq is not None for seq in secuencias) and len(set(secuencias)) == len(secuencias):
        return sorted(sucursales, key=lambda s: _extraer_secuencia_sucursal(s) or 999999)

    con_coords = []
    sin_coords = []
    for idx, s in enumerate(sucursales):
        lat = _to_float(s.get("latitud"))
        lon = _to_float(s.get("longitud"))
        if lat is None or lon is None:
            sin_coords.append((idx, s))
            continue
        con_coords.append((idx, s, lat, lon))

    if len(con_coords) <= 1:
        return list(sucursales)

    pendientes = list(con_coords)
    actual = max(pendientes, key=lambda t: (t[2], -t[0]))
    ordenadas = [actual]
    pendientes.remove(actual)

    while pendientes:
        _, _, lat_a, lon_a = ordenadas[-1]
        prox = min(
            pendientes,
            key=lambda t: (_haversine_km(lat_a, lon_a, t[2], t[3]), t[0]),
        )
        ordenadas.append(prox)
        pendientes.remove(prox)

    resultado = [t[1] for t in ordenadas]
    if sin_coords:
        resultado.extend(s for _idx, s in sorted(sin_coords, key=lambda t: t[0]))
    return resultado


def _fallback_haversine(coords: list, velocidad_kmh: float = 35.0) -> dict:
    FACTOR_VIA = 1.35
    distancia_recta = sum(
        _haversine_km(coords[i][0], coords[i][1], coords[i+1][0], coords[i+1][1])
        for i in range(len(coords) - 1)
    )
    distancia_vial = distancia_recta * FACTOR_VIA
    return {
        "distancia_km": round(distancia_vial, 2),
        "traslado_min": round((distancia_vial / velocidad_kmh) * 60, 1),
        "origen": "haversine_fallback",
    }


# ── Cálculo de tiempos ─────────────────────────────────────────

def calcular_tiempos_ruta(
    ruta: dict,
    pesos: dict,
    usar_cache: bool = True,
    paradas: list | None = None,
) -> dict:
    if paradas is None:
        sucursales = _ordenar_sucursales_planificacion(ruta.get("sucursales", []))
        mayoristas = ruta.get("mayoristas") or []
        if mayoristas:
            paradas = [dict(s, tipo="sucursal") for s in sucursales]
            paradas += [dict(m, tipo="mayorista") for m in mayoristas]
            paradas.sort(key=lambda p: p.get("orden") if p.get("orden") is not None else 9999)
        else:
            paradas = [dict(s, tipo="sucursal") for s in sucursales]
    VACIO = {
        "traslado_min": 0.0, "descarga_min": 0.0, "extra_min": HORAS_EXTRA_RUTA_MIN,
        "total_min": 0.0, "distancia_km": 0.0, "origen_tiempo": "sin_coordenadas",
    }
    if not paradas:
        return VACIO

    cfg          = _obtener_config_general()
    matriz_lat   = MATRIZ_LAT_DEFAULT
    matriz_lon   = MATRIZ_LON_DEFAULT
    min_descarga = float(cfg.get("min_descarga_por_kg") or MIN_DESCARGA_POR_KG)
    velocidad    = float(cfg.get("velocidad_kmh")       or 35.0)

    coords = [(matriz_lat, matriz_lon)]
    for p in paradas:
        lat = p.get("latitud") if p.get("latitud") is not None else p.get("lat")
        lon = p.get("longitud") if p.get("longitud") is not None else p.get("lon")
        if lat is not None and lon is not None:
            coords.append((float(lat), float(lon)))

    if len(coords) < 2:
        return VACIO

    coords.append((matriz_lat, matriz_lon))
    clave = _cache_key(coords)
    resultado_traslado = _cargar_cache(clave) if usar_cache else None

    if resultado_traslado is None:
        resultado_traslado = consultar_osrm(coords)
        if "error" in resultado_traslado:
            resultado_traslado = _fallback_haversine(coords, velocidad)
        if resultado_traslado.get("origen") == "osrm":
            _guardar_cache(clave, resultado_traslado)

    # Descarga acumulada: cada sucursal tiene su propio tope de 120 min
    def _peso_descarga(p: dict) -> float:
        if p.get("tipo") == "mayorista" or (p.get("id_cliente") is not None and p.get("num_tienda") is None):
            return float(p.get("peso_kg") or 0)
        return float(pesos.get(str(p.get("num_tienda", "")), p.get("peso_kg") or 0) or 0)

    tiempo_descarga = sum(
        min(_peso_descarga(p) * min_descarga, MAX_DESCARGA_POR_SUCURSAL)
        for p in paradas
    )
    traslado_min = resultado_traslado.get("traslado_min", 0.0)
    distancia_km = resultado_traslado.get("distancia_km", 0.0)
    total        = traslado_min + tiempo_descarga + HORAS_EXTRA_RUTA_MIN

    return {
        "traslado_min":  round(traslado_min, 1),
        "descarga_min":  round(tiempo_descarga, 1),
        "extra_min":     HORAS_EXTRA_RUTA_MIN,
        "total_min":     round(total, 1),
        "distancia_km":  round(distancia_km, 2),
        "origen_tiempo": resultado_traslado.get("origen", "desconocido"),
    }


def calcular_tiempos_multiples_rutas(rutas: list, pesos: dict, logistica_id: str | None = None) -> dict:
    resultados = {}
    paradas_map: dict = {}
    if logistica_id:
        try:
            dist = calcular_distribucion_mayoristas(logistica_id, rutas)
            paradas_map = dist.get("paradas_integradas", {})
        except Exception:
            paradas_map = {}

    for ruta in rutas:
        ruta_id = str(ruta.get("_id", "") or ruta.get("id", ""))
        try:
            paradas = paradas_map.get(ruta_id)
            resultados[ruta_id] = calcular_tiempos_ruta(ruta, pesos, paradas=paradas)
        except Exception:
            resultados[ruta_id] = {
                "traslado_min": 0.0, "descarga_min": 0.0, "extra_min": HORAS_EXTRA_RUTA_MIN,
                "total_min": 0.0, "distancia_km": 0.0, "origen_tiempo": "error",
            }
    return resultados


# ═══════════════════════════════════════════════════════════════
# Funciones de dominio
# ═══════════════════════════════════════════════════════════════

def obtener_rutas() -> list:
    db = get_db()
    rutas = []
    for r in db["rutas_config"].find({}):
        ruta = _serialize(r)
        sucursales = ruta.get("sucursales") or []
        if isinstance(sucursales, list) and sucursales:
            ruta["sucursales"] = _ordenar_sucursales_planificacion(sucursales)
        rutas.append(ruta)
    return rutas


def obtener_vehiculos() -> list:
    """Devuelve únicamente los vehículos activos."""
    try:
        db = get_db()
        return [_serialize(v) for v in db["vehiculos"].find({"activo": True})]
    except Exception:
        return []


def obtener_pesos(logistica_id: str) -> dict:
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
        for nombre_sucursal, valores in data.items():
            id_suc  = valores.get("id_sucursal")
            peso_kg = valores.get("total_kg", 0)
            if id_suc is not None:
                pesos[str(id_suc)] = float(peso_kg)
        return pesos
    except Exception as e:
        print(f"[obtener_pesos] Error: {e}")
        return {}


def obtener_volumenes(logistica_id: str) -> dict:
    """Devuelve { id_sucursal: total_m3 } desde la extracción activa."""
    oid = _parse_oid(logistica_id)
    if not oid:
        return {}
    try:
        db  = get_db()
        doc = db["extraccion"].find_one({"logistica_id": oid})
        if not doc:
            return {}
        datos_vol = doc.get("datos_volumen", {})
        volumenes = {}
        for _, valores in datos_vol.items():
            id_suc = valores.get("id_sucursal")
            vol_m3 = valores.get("total_m3", 0)
            if id_suc is not None:
                volumenes[str(id_suc)] = float(vol_m3)
        return volumenes
    except Exception as e:
        print(f"[obtener_volumenes] Error: {e}")
        return {}


def calcular_peso_ruta(ruta: dict, pesos: dict) -> float:
    return sum(
        pesos.get(str(s.get("num_tienda", "")), 0.0)
        for s in ruta.get("sucursales", [])
    )


def calcular_volumen_ruta(ruta: dict, volumenes: dict) -> float:
    return sum(
        volumenes.get(str(s.get("num_tienda", "")), 0.0)
        for s in ruta.get("sucursales", [])
    )


def _score_vehiculo(peso_ton: float, vehiculo: dict,
                    util_min: float = UTIL_MIN_DEFAULT,
                    util_max: float = UTIL_MAX_DEFAULT) -> float:
    """
    Puntaje de idoneidad del vehículo para una carga dada.
    Menor puntaje = mejor vehículo.

    • Dentro del rango [util_min, util_max] → penalización = |pct - 100|
      (se prefiere el que se acerque más al 100 %).
    • Fuera del rango → penalización alta: 100 + distancia al borde del rango.
      Esto garantiza que siempre se prefiera cualquier vehículo dentro del rango
      antes que uno fuera, pero aún permite comparar los candidatos fuera del
      rango entre sí (fallback).
    """
    cap = _capacidad_efectiva_ton(vehiculo.get("capacidad_toneladas", 0))
    if cap <= 0:
        return float("inf")
    pct = (peso_ton / cap) * 100
    if util_min <= pct <= util_max:
        return abs(pct - 100)
    if pct < util_min:
        return 100 + (util_min - pct)
    return 100 + (pct - util_max)


def sugerir_vehiculo(peso_kg: float, vehiculos: list,
                     placas_ocupadas: "set | None" = None) -> "dict | None":
    if placas_ocupadas is None:
        placas_ocupadas = set()
    util_min, util_max = _leer_rango_utilizacion()
    peso_ton   = peso_kg / 1000
    candidatos = [
        v for v in vehiculos
        if v.get("placas") not in placas_ocupadas
        and (v.get("capacidad_toneladas") or 0) > 0
    ]
    if not candidatos:
        return None
    return min(candidatos, key=lambda v: _score_vehiculo(peso_ton, v, util_min, util_max))


def calcular_porcentaje_capacidad(peso_kg: float, vehiculo: "dict | None") -> float:
    if not vehiculo:
        return 0.0
    cap = _capacidad_efectiva_ton(vehiculo.get("capacidad_toneladas", 0))
    return round((peso_kg / 1000 / cap) * 100, 1) if cap > 0 else 0.0


# ═══════════════════════════════════════════════════════════════
# ASIGNACIÓN OPTIMIZADA — MÁXIMA PROXIMIDAD AL 100 %
# ═══════════════════════════════════════════════════════════════

def generar_asignacion_optimizada(payload: dict, logistica_id: str) -> dict:
    """
    Fase 1 — Asignación principal (BLOQUES 1–5):

      1. Lee util_min / util_max desde la configuración general de MongoDB
         (por defecto 80 % / 100 %; CAP-1 / CAP-2).
      2. Filtra solo las rutas seleccionadas (no entregadas / no excluidas).
      3. Ordena las rutas de menor a mayor peso (kg) — ORD-1.
      4. Ordena los vehículos de menor a mayor capacidad (ton) — ORD-2.
      5. Para cada ruta (de la más liviana a la más pesada):
           a. Obtiene los vehículos libres en el día candidato (OPE-6).
           b. Descarta vehículos que excederían util_max (CAP-2). No existe
              fallback de util_max+30 % — CAP-3 ha sido eliminado.
           c. Aplica restricción volumétrica con ESTRATEGIA_VOLUMEN_NULO (OPE-2).
           d. Prioriza vehículos dentro del rango [util_min, util_max] (SCO-1).
           e. Si ninguno queda dentro del rango, selecciona el menos penalizado
              fuera del rango (SCO-2/SCO-3) — solo vehículos subutilizados.
           f. Si ningún vehículo está disponible → placas=null (OPE-7).

    Fase 2 — Optimización con mayoristas (BLOQUE 7 — MAY-2):
      Ejecutada después de la fase principal. Agrega/quita mayoristas para
      acercar la ocupación al 100 %. Escalado a vehículo mayor si necesario.
      Orden: descendente de capacidad (exclusivo de esta fase).

    Retorna el mapa { ruta_id → { dia, placas, pct, peso_kg, en_rango, … } }.
    """
    rutas_input   = payload.get("rutas", [])
    vehiculos_in  = payload.get("vehiculos", [])
    pesos         = payload.get("pesos", {})
    volumenes     = payload.get("volumenes", {})
    config_dias   = payload.get("config_dias", {})
    ids_excluidos = set(payload.get("ids_excluidos", []))

    # ── Rango óptimo y config volumétrica desde MongoDB ─────────
    util_min, util_max                    = _leer_rango_utilizacion()
    estrategia_vol, factor_vol, radio_km  = _leer_config_volumen()

    # ── Días habilitados (orden canónico) ────────────────────────
    dias_hab = [d for d in DIAS_ORDEN if config_dias.get(d, {}).get("habilitado")]
    if not dias_hab:
        dias_hab = ["lunes", "martes", "miercoles", "jueves", "viernes"]

    # ── Filtrar rutas seleccionadas ──────────────────────────────
    rutas = [r for r in rutas_input if str(r.get("_id", "")) not in ids_excluidos]

    # ── Datos de mayoristas (cercanía + extracción) ─────────────────
    # Se cargan antes del cálculo de pesos para incluir el peso de los
    # mayoristas ya distribuidos en el peso total de cada ruta (Fase 1).
    # Esto garantiza que el vehículo asignado conozca la carga real completa.
    mayoristas_por_ruta, todos_mayoristas = _cargar_datos_mayoristas(logistica_id, rutas)

    # ── Vehículos disponibles ordenados de menor a mayor capacidad
    # Solo vehículos activos (activo=True o sin campo, por compatibilidad)
    vehiculos = sorted(
        [v for v in vehiculos_in
         if (v.get("capacidad_toneladas") or 0) > 0
         and v.get("activo", True)],
        key=lambda v: v.get("capacidad_toneladas", 0),
    )

    if not vehiculos:
        return {"status": "error", "mensaje": "No hay vehículos configurados."}

    # ── Calcular peso de cada ruta (sucursales + mayoristas) y ordenar ──
    # _peso_kg     → peso total usado para seleccionar vehículo y ordenar rutas.
    # _peso_suc_kg → solo sucursales (se almacena en asig["peso_kg"] para el frontend).
    # _peso_may_kg → solo mayoristas distribuidos según distribucion_mayoristas.
    rutas_con_peso = []
    for ruta in rutas:
        ruta_id     = str(ruta.get("_id", ""))
        peso_suc_kg = calcular_peso_ruta(ruta, pesos)
        peso_may_kg = sum(m["peso_kg"] for m in mayoristas_por_ruta.get(ruta_id, []))
        rutas_con_peso.append({
            **ruta,
            "_peso_kg":     peso_suc_kg + peso_may_kg,
            "_peso_suc_kg": peso_suc_kg,
            "_peso_may_kg": peso_may_kg,
        })
    rutas_con_peso.sort(key=lambda r: r["_peso_kg"])

    # ── Estado por día: vehículos usados ────────────────────────
    estado_dias: dict = {
        d: {"vehiculos_usados": set(), "rutas": []} for d in dias_hab
    }

    asignaciones: dict = {}   # ruta_id → {dia, placas, pct, peso_kg, en_rango}

    def _dias_candidatos(ruta: dict) -> list:
        """
                Respeta el día configurado en la ruta (dia_sugerido).

                Cómo funciona la carga de rutas:
          DIAS_VALIDOS = {'Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes'}
          El campo dia_sugerido se almacena con mayúscula inicial y tildes.

        Por eso se usa _normalizar_dia() antes de comparar contra las claves
        internas ('lunes', 'miercoles', …), que están en minúsculas sin tildes.

        Regla:
          - Si la ruta tiene un día configurado (dia_sugerido) y ese día está
            habilitado → retorna SOLO ese día. El sistema no mueve la ruta a
            otro día para optimizar carga ni por ningún otro motivo.
          - Si el día configurado no está habilitado, o la ruta no tiene día
            configurado → retorna todos los días habilitados por carga mínima
            (este caso es raro y solo ocurre en rutas sin dia_sugerido).
        """
        # Prioridad 1: dia_sugerido (configurado en Creación de Rutas)
        dia_sug_norm  = _normalizar_dia(ruta.get("dia_sugerido") or "")
        # Prioridad 2: dia_programado (puede venir de una asignación previa)
        dia_prog_norm = _normalizar_dia(ruta.get("dia_programado") or "")

        dia_configurado = None
        for candidato_norm in [dia_sug_norm, dia_prog_norm]:
            if not candidato_norm:
                continue
            for d in dias_hab:
                if _normalizar_dia(d) == candidato_norm:
                    dia_configurado = d
                    break
            if dia_configurado:
                break

        if dia_configurado:
            # El día configurado está habilitado → respetar sin excepciones
            return [dia_configurado]

        # Sin día configurado o día no habilitado → todos los habilitados por carga
        return sorted(dias_hab, key=lambda d: len(estado_dias[d]["rutas"]))

    def _asignar_vehiculo(peso_ton: float, dia: str, volumen_m3_ruta: float = 0.0) -> "dict | None":
        """
        Selecciona el mejor vehículo libre para el día dado.

        Restricciones aplicadas (CAP-2, CAP-3, OPE-2, OPE-6):
          · Solo vehículos no usados ese día.
          · capacidad_toneladas > 0  (CAP-4).
          · pct = peso_ton / cap × 100  ≤  util_max  (CAP-2).
            Cualquier vehículo que supere util_max queda descartado directamente.
            No existe banda de tolerancia adicional (CAP-3 eliminado).
          · Restricción volumétrica según ESTRATEGIA_VOLUMEN_NULO  (OPE-2).

        Selección:
          1. Dentro del rango [util_min, util_max] → min |pct - 100|  (SCO-1/SCO-3).
          2. Fuera del rango pero ≤ util_max → fallback por score  (SCO-2).
             Solo puede quedar aquí un vehículo subutilizado (pct < util_min),
             porque los sobrecargados ya fueron descartados en el filtro.
        """
        usados = estado_dias[dia]["vehiculos_usados"]
        libres = [
            v for v in vehiculos
            if v.get("placas") not in usados
            and (v.get("capacidad_toneladas") or 0) > 0
            # CAP-2: descartar si superaría util_max (sin tolerancia adicional)
            and _pct_utilizacion(peso_ton, v.get("capacidad_toneladas")) <= util_max
            # OPE-2: restricción volumétrica con estrategia configurable
            and _es_vol_compatible(v, volumen_m3_ruta, estrategia_vol, factor_vol)
        ]

        if not libres:
            return None

        # SCO-1/SCO-3: candidatos dentro del rango óptimo → preferir el más cercano al 100 %
        en_rango = [
            v for v in libres
            if util_min <= _pct_utilizacion(peso_ton, v.get("capacidad_toneladas")) <= util_max
        ]

        if en_rango:
            return min(en_rango, key=lambda v: abs(_pct_utilizacion(peso_ton, v.get("capacidad_toneladas")) - 100))

        # SCO-2: fallback — ninguno en rango, pero todos ya cumplen pct ≤ util_max
        # (los sobrecargados fueron descartados arriba; aquí solo quedan subutilizados)
        return min(libres, key=lambda v: _score_vehiculo(peso_ton, v, util_min, util_max))

    # ── Proceso de asignación ────────────────────────────────────
    for ruta in rutas_con_peso:
        ruta_id     = str(ruta.get("_id", ""))
        peso_suc_kg = ruta["_peso_suc_kg"]
        peso_may_kg = ruta["_peso_may_kg"]
        peso_kg     = ruta["_peso_kg"]      # total = suc + mayoristas
        peso_ton    = peso_kg / 1000
        volumen_m3  = calcular_volumen_ruta(ruta, volumenes)

        asignado = None
        for dia in _dias_candidatos(ruta):
            vehiculo = _asignar_vehiculo(peso_ton, dia, volumen_m3)
            if vehiculo:
                asignado = {"dia": dia, "vehiculo": vehiculo}
                break

        if asignado:
            dia     = asignado["dia"]
            veh     = asignado["vehiculo"]
            placas  = veh.get("placas")
            cap     = veh.get("capacidad_toneladas", 1)
            pct     = round(_pct_utilizacion(peso_ton, cap), 1)
            cap_vol = veh.get("volumen_m3") or 0
            pct_vol = round((volumen_m3 / cap_vol) * 100, 1) if cap_vol > 0 else None

            asignaciones[ruta_id] = {
                "dia":                dia,
                "placas":             placas,
                "pct":                pct,
                # peso_kg almacena solo sucursales (compatibilidad frontend);
                # el peso total (suc+mayoristas) se refleja en pct.
                "peso_kg":            round(peso_suc_kg, 1),
                "peso_kg_mayoristas": round(peso_may_kg, 1),
                "en_rango":           util_min <= pct <= util_max,
                "volumen_m3":         round(volumen_m3, 6),
                "pct_vol":            pct_vol,
                "cumple_vol":         True,
            }
            estado_dias[dia]["vehiculos_usados"].add(placas)
            estado_dias[dia]["rutas"].append(ruta_id)
        else:
            # Sin vehículo disponible: conservar el día configurado de la ruta.
            candidatos = _dias_candidatos(ruta)
            dia = candidatos[0] if candidatos else min(
                dias_hab, key=lambda d: len(estado_dias[d]["rutas"]))
            asignaciones[ruta_id] = {
                "dia":                dia,
                "placas":             None,
                "pct":                0.0,
                "peso_kg":            round(peso_suc_kg, 1),
                "peso_kg_mayoristas": round(peso_may_kg, 1),
                "en_rango":           False,
                "volumen_m3":         round(volumen_m3, 6),
                "pct_vol":            None,
                "cumple_vol":         False,
            }
            estado_dias[dia]["rutas"].append(ruta_id)

    # ── Fase 2: optimización con mayoristas (MAY-2) ──────────────
    # Los datos de mayoristas ya están cargados; se pasan directamente para
    # evitar una segunda consulta a MongoDB.
    rutas_index = {str(r.get("_id", "")): r for r in rutas_con_peso}
    _ejecutar_optimizacion_mayoristas(
        asignaciones        = asignaciones,
        estado_dias         = estado_dias,
        vehiculos_raw       = vehiculos,
        mayoristas_por_ruta = mayoristas_por_ruta,
        todos_mayoristas    = todos_mayoristas,
        rutas_index         = rutas_index,
        util_min            = util_min,
        util_max            = util_max,
        estrategia_vol      = estrategia_vol,
        factor_vol          = factor_vol,
        radio_km            = radio_km,
    )

    # ── Fase 3: Reacomodamiento Lógico ──────────────────────────
    cfg_r = _leer_config_reacomodamiento()
    resultado_reacomo = ejecutar_reacomodamiento(
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
        cfg_r          = cfg_r,
    )

    # ── Resumen por día ──────────────────────────────────────────
    resumen = {
        d: {
            "num_rutas":        len(e["rutas"]),
            "vehiculos_usados": list(e["vehiculos_usados"]),
        }
        for d, e in estado_dias.items()
    }

    # Conteo de rutas fuera del rango óptimo (asignadas pero con pct incorrecto)
    fuera_rango = sum(
        1 for v in asignaciones.values()
        if v["placas"] and not v.get("en_rango", True)
    )

    return {
        "status":        "ok",
        "asignaciones":  asignaciones,
        "resumen_dias":  resumen,
        "total_rutas":   len(asignaciones),
        "sin_vehiculo":  sum(1 for v in asignaciones.values() if not v["placas"]),
        "fuera_rango":   fuera_rango,
        "util_min":      util_min,
        "util_max":      util_max,
        "reacomodamiento": resultado_reacomo,
    }


# ═══════════════════════════════════════════════════════════════
# BLOQUE 6-7 — Mayoristas: helpers y optimización MAY-1 / MAY-2
# ═══════════════════════════════════════════════════════════════

def _cargar_datos_mayoristas(logistica_id: str, rutas: list | None = None) -> tuple:
    """
    Carga los datos de mayoristas necesarios para las fases 1 y 2 de la asignación.

    Fuente primaria: ``distribucion_mayoristas`` (documento con ``_key='ultimo'``).
    Contiene la distribución ya calculada: qué mayoristas van en qué ruta y con
    qué coordenadas.  Se complementa con ``extraccion.mayoristas`` para obtener
    los pesos reales del ciclo logístico activo.

    Estructura del documento distribucion_mayoristas:
      · rutas[].paradas_integradas[] (tipo='mayorista')
            _id, id_cliente, nombre_base, latitud, longitud, desvio_m, …
      · sin_asignar[]   → mayoristas con coords pero rutas llenas al distribuir
            _id, id_cliente, nombre, poblacion, motivo   (sin coordenadas)
      · sin_coords[]    → mayoristas sin coordenadas en el catálogo

    Para los ``sin_asignar`` se consulta ``clientes_mayoristas`` solo por sus
    ``id_cliente`` (lectura mínima) para obtener sus coordenadas y poder
    considerarlos candidatos en el paso de adición (MAY-2).

    Returns:
        (mayoristas_por_ruta, todos_mayoristas)

        · mayoristas_por_ruta : {ruta_id_str: [{id_cliente, nombre, peso_kg, lat, lon}]}
        · todos_mayoristas    : lista unificada de todos los mayoristas con coords
                                (asignados a rutas + sin_asignar con coords).
                                Usada para identificar candidatos al agregar (MAY-2).
    """
    try:
        dist = calcular_distribucion_mayoristas(logistica_id, rutas)
        return dist.get("mayoristas_por_ruta", {}), dist.get("todos_mayoristas", [])
    except Exception as e:
        print(f"[_cargar_datos_mayoristas] Error al calcular cercania: {e}")
        return {}, []


def _ejecutar_optimizacion_mayoristas(
    asignaciones:        dict,
    estado_dias:         dict,
    vehiculos_raw:       list,
    mayoristas_por_ruta: dict,
    todos_mayoristas:    list,
    rutas_index:         dict,
    util_min:            float,
    util_max:            float,
    estrategia_vol:      str,
    factor_vol:          float,
    radio_km:            float,
) -> None:
    """
    Fase 2 — Optimización de llenado con mayoristas  (BLOQUE 7 — MAY-2).

    Opera sobre ``asignaciones`` y ``estado_dias`` IN-PLACE.

    Los datos de mayoristas ya vienen precargados desde ``generar_asignacion_optimizada``
    (``mayoristas_por_ruta`` y ``todos_mayoristas``) para evitar una segunda consulta
    a MongoDB. En la Fase 1 esos datos se usaron para incluir el peso de los mayoristas
    en la selección de vehículos; aquí se usan para afinar la distribución.

    Pasos (especificación BLOQUE 7):
      3. Si pct ∈ [CAP-1, CAP-2] → confirmar mayoristas y no modificar.
      4. Si pct < CAP-1 → agregar mayoristas cercanos (≤ radio_km) no usados
         ese día, por distancia ascendente. Elegir la combinación más cercana
         al 100 % sin superar CAP-2.
      5. Si pct > CAP-2 → quitar mayoristas (mayor peso primero) hasta rango.
         MAY-1: no quitar si el mayorista es necesario para el cumplimiento.
      6. Si sigue > CAP-2 tras quitar todo lo permitido → escalar a vehículo
         de mayor capacidad (OPE-1, OPE-6, OPE-2, rango [CAP-1,CAP-2]).
         Si no existe → placas=null, en_rango=false (OPE-7).

    Orden: descendente de capacidad del vehículo asignado (MAY-2 paso 9).
    No altera el orden ascendente de la fase principal (ORD-1/ORD-2).

    MAY-1: ``mayoristas_usados[día]`` se construye durante esta pasada.
    Un mayorista confirmado no puede candidatearse en otra ruta del mismo día.
    """
    if not todos_mayoristas and not mayoristas_por_ruta:
        return   # No hay datos de mayoristas → nada que optimizar

    # Índice rápido: id_cliente → datos del mayorista
    may_index: dict = {m["id_cliente"]: m for m in todos_mayoristas}

    # MAY-1: rastreo de mayoristas ya confirmados en cada día
    mayoristas_usados: dict = {dia: set() for dia in estado_dias}

    # Índice de vehículos por placas (incluye todos los de la flota activa)
    veh_por_placas: dict = {v.get("placas"): v for v in vehiculos_raw}

    # ── Ordenar rutas asignadas: desc por capacidad del vehículo ─
    rutas_con_vehiculo = [
        (ruta_id, asig)
        for ruta_id, asig in asignaciones.items()
        if asig.get("placas")
    ]
    rutas_con_vehiculo.sort(
        key=lambda x: (veh_por_placas.get(x[1]["placas"]) or {}).get("capacidad_toneladas", 0),
        reverse=True,
    )

    for ruta_id, asig in rutas_con_vehiculo:
        dia    = asig["dia"]
        placas = asig["placas"]
        veh    = veh_por_placas.get(placas)
        if not veh:
            continue
        cap_ton = _capacidad_efectiva_ton(veh.get("capacidad_toneladas", 0))
        if cap_ton <= 0:
            continue

        # Peso base de sucursales (ya calculado en fase 1)
        peso_suc_kg = asig["peso_kg"]

        # Mayoristas actualmente asignados a esta ruta (de distribucion_mayoristas)
        may_actuales: list = list(mayoristas_por_ruta.get(ruta_id, []))

        # IDs de mayoristas ya en la ruta (para evitar duplicados)
        ids_actuales = {m["id_cliente"] for m in may_actuales}

        # Peso total incluyendo mayoristas actuales
        peso_may_actual = sum(m["peso_kg"] for m in may_actuales)
        peso_total_ton  = (peso_suc_kg + peso_may_actual) / 1000
        pct_actual      = _pct_utilizacion(peso_total_ton, cap_ton)

        # ── MAY-2 paso 3: ya está en rango ──────────────────────
        if util_min <= pct_actual <= util_max:
            for m in may_actuales:
                mayoristas_usados[dia].add(m["id_cliente"])
            asig["pct"]               = round(pct_actual, 1)
            asig["en_rango"]          = True
            asig["peso_kg_mayoristas"] = round(peso_may_actual, 1)
            continue

        # ── MAY-2 paso 4: subutilizado — agregar mayoristas ──────
        if pct_actual < util_min:
            sucursales = (rutas_index.get(ruta_id) or {}).get("sucursales", [])

            candidatos: list = []
            for m in todos_mayoristas:
                if m["id_cliente"] in mayoristas_usados[dia]:
                    continue
                if m["id_cliente"] in ids_actuales:
                    continue
                if m["lat"] is None or m["lon"] is None:
                    continue
                if m["peso_kg"] <= 0:
                    continue
                # Distancia mínima al conjunto de sucursales de la ruta
                dist_min = min(
                    (
                        _haversine_km(m["lat"], m["lon"],
                                      float(s["latitud"]), float(s["longitud"]))
                        for s in sucursales
                        if s.get("latitud") is not None and s.get("longitud") is not None
                    ),
                    default=float("inf"),
                )
                if dist_min <= radio_km:
                    candidatos.append((dist_min, m))

            candidatos.sort(key=lambda x: x[0])

            # Selección greedy: agregar en orden de distancia,
            # manteniendo el resultado más cercano al 100 % sin superar util_max.
            may_trabajo  = list(may_actuales)
            mejor_combo  = list(may_actuales)
            mejor_pct    = pct_actual

            for _dist, m in candidatos:
                peso_test = (peso_suc_kg + sum(x["peso_kg"] for x in may_trabajo) + m["peso_kg"]) / 1000
                pct_test  = _pct_utilizacion(peso_test, cap_ton)
                if pct_test > util_max:
                    continue   # No agregar si excedería util_max (CAP-2)
                may_trabajo.append(m)
                if abs(pct_test - 100) < abs(mejor_pct - 100):
                    mejor_pct   = pct_test
                    mejor_combo = list(may_trabajo)

            # Aplicar mejor combinación
            peso_may_final = sum(m["peso_kg"] for m in mejor_combo)
            pct_final      = _pct_utilizacion((peso_suc_kg + peso_may_final) / 1000, cap_ton)

            for m in mejor_combo:
                mayoristas_usados[dia].add(m["id_cliente"])
            mayoristas_por_ruta[ruta_id] = mejor_combo
            asig["pct"]                = round(pct_final, 1)
            asig["en_rango"]           = util_min <= pct_final <= util_max
            asig["peso_kg_mayoristas"] = round(peso_may_final, 1)
            continue

        # ── MAY-2 paso 5: sobrecargado — quitar mayoristas ───────
        # (pct_actual > util_max)
        # Ordenar los mayoristas actuales: mayor peso primero
        may_a_quitar = sorted(may_actuales, key=lambda m: m["peso_kg"], reverse=True)
        may_restantes = list(may_actuales)

        for m in may_a_quitar:
            # MAY-1 check: si quitar este mayorista dejaría la ruta por DEBAJO de
            # util_min, es "necesario para el cumplimiento del rango". No quitarlo.
            peso_sin_este = (peso_suc_kg + sum(
                x["peso_kg"] for x in may_restantes if x["id_cliente"] != m["id_cliente"]
            )) / 1000
            pct_sin_este = (peso_sin_este / cap_ton) * 100

            # Si sin él la ruta quedaría en rango o sigue sobre el mínimo → quitar
            # Si sin él la ruta caería bajo util_min → es necesario, no quitar
            may_restantes = [x for x in may_restantes if x["id_cliente"] != m["id_cliente"]]

            pct_tras_quita = _pct_utilizacion((peso_suc_kg + sum(x["peso_kg"] for x in may_restantes)) / 1000, cap_ton)
            if pct_tras_quita <= util_max:
                break   # Ya dentro del rango (o bajo util_min, pero sin sobrecarga)

        peso_may_final = sum(m["peso_kg"] for m in may_restantes)
        pct_final      = _pct_utilizacion((peso_suc_kg + peso_may_final) / 1000, cap_ton)

        if pct_final <= util_max:
            # Éxito: sobrecarga resuelta quitando mayoristas
            for m in may_restantes:
                mayoristas_usados[dia].add(m["id_cliente"])
            mayoristas_por_ruta[ruta_id] = may_restantes
            asig["pct"]                = round(pct_final, 1)
            asig["en_rango"]           = util_min <= pct_final <= util_max
            asig["peso_kg_mayoristas"] = round(peso_may_final, 1)
            continue

        # ── MAY-2 paso 6: escalado a vehículo de mayor capacidad ─
        # Tras quitar todos los mayoristas permitidos, la carga base sigue
        # superando util_max. Buscar un vehículo alternativo de mayor capacidad.
        peso_base_ton  = peso_suc_kg / 1000
        vol_m3_ruta    = asig.get("volumen_m3", 0.0)

        vehiculos_candidatos = [
            v for v in vehiculos_raw
            if v.get("activo", True)
            and (v.get("capacidad_toneladas") or 0) > cap_ton   # mayor capacidad
            and v.get("placas") not in estado_dias[dia]["vehiculos_usados"]
            and v.get("placas") != placas
            and _es_vol_compatible(v, vol_m3_ruta, estrategia_vol, factor_vol)
        ]

        # Seleccionar el vehículo de menor capacidad que aún ponga la ruta en rango
        vehiculo_alt = None
        for v in sorted(vehiculos_candidatos, key=lambda x: x.get("capacidad_toneladas", 0)):
            cap_v   = _capacidad_efectiva_ton(v.get("capacidad_toneladas", 0))
            pct_v   = _pct_utilizacion(peso_base_ton, cap_v) if cap_v > 0 else 999.0
            if util_min <= pct_v <= util_max:
                vehiculo_alt = v
                break

        if vehiculo_alt:
            new_placas = vehiculo_alt.get("placas")
            new_cap    = _capacidad_efectiva_ton(vehiculo_alt.get("capacidad_toneladas"))
            pct_nuevo  = round(_pct_utilizacion(peso_base_ton, new_cap), 1)

            # Liberar vehículo anterior; asignar el nuevo
            estado_dias[dia]["vehiculos_usados"].discard(placas)
            estado_dias[dia]["vehiculos_usados"].add(new_placas)

            mayoristas_por_ruta[ruta_id] = []
            asig["placas"]             = new_placas
            asig["pct"]                = pct_nuevo
            asig["en_rango"]           = util_min <= pct_nuevo <= util_max
            asig["peso_kg_mayoristas"] = 0.0
            # Mayoristas eliminados quedan disponibles para otras rutas
        else:
            # OPE-7: no existe vehículo que resuelva la sobrecarga → sin cobertura
            estado_dias[dia]["vehiculos_usados"].discard(placas)
            asig["placas"]             = None
            asig["pct"]                = 0.0
            asig["en_rango"]           = False
            asig["peso_kg_mayoristas"] = 0.0
            mayoristas_por_ruta[ruta_id] = []


# ═══════════════════════════════════════════════════════════════
# FASE 3 — REACOMODAMIENTO (assign_vehicles_to_routes)
# ═══════════════════════════════════════════════════════════════



def ejecutar_reacomodamiento(
    asignaciones:    dict,
    estado_dias:     dict,
    vehiculos_raw:   list,
    rutas_index:     dict,
    pesos:           dict,
    volumenes:       dict,
    config_dias:     dict,
    util_min:        float,
    util_max:        float,
    estrategia_vol:  str,
    factor_vol:      float,
    cfg_r:           "dict | None" = None,
) -> dict:
    """
    Fase 3 — Reacomodamiento (assign_vehicles_to_routes).

    Opera sobre ``asignaciones`` y ``estado_dias`` IN-PLACE.

    Algoritmo por día:
      1. Ordena rutas por total_kg ascendente (más ligera primero).
      2. Ordena vehículos por capacidad_kg ascendente (más pequeño primero).
      3. Cada vehículo se usa como máximo una vez por día.
      4. Para cada ruta, asigna el vehículo más pequeño donde:
           route_kg ≤ vehicle_capacity_kg × (1 + cap_tolerance)
      5. Si ningún vehículo es elegible → ruta sin vehículo.

    Métricas de salida por ruta:
      · utilization_pct   = (total_kg / capacity_kg) × 100
      · within_tolerance  = |utilization_pct/100 − 1.0| ≤ cap_tolerance
      · overloaded        = utilization_pct > (1 + cap_tolerance) × 100
    """
    if cfg_r is None:
        cfg_r = _leer_config_reacomodamiento()

    cap_tolerance = float(cfg_r.get("cap_tolerance", VRP_CAP_TOLERANCE))
    max_factor    = 1.0 + cap_tolerance    # 1.30 con tolerancia 0.30
    max_util_pct  = max_factor * 100       # 130.0

    # Vehículos ordenados ascendente por capacidad (desempate por placas)
    vehiculos_sorted = sorted(
        [v for v in vehiculos_raw if (v.get("capacidad_toneladas") or 0) > 0],
        key=lambda v: (v.get("capacidad_toneladas", 0), v.get("placas") or ""),
    )

    detalle:         dict = {}
    cambios:         list = []
    n_en_rango       = 0
    n_sobrecargadas  = 0
    n_sin_vehiculo   = 0
    n_reasignadas    = 0

    for dia, estado in estado_dias.items():
        rutas_del_dia = list(estado.get("rutas", []))
        if not rutas_del_dia:
            continue

        # Construir resumen de rutas con total_kg (sucursales + mayoristas)
        route_summary = []
        for ruta_id in rutas_del_dia:
            asig     = asignaciones.get(ruta_id, {})
            peso_suc = float(asig.get("peso_kg", 0.0))
            peso_may = float(asig.get("peso_kg_mayoristas", 0.0))
            route_summary.append({
                "ruta_id":    ruta_id,
                "total_kg":   peso_suc + peso_may,
                "old_placas": asig.get("placas"),
            })

        # Regla 1: ordenar rutas por total_kg ascendente
        route_summary.sort(key=lambda r: r["total_kg"])

        # Regla 2–5: asignar vehículo más pequeño elegible
        usados_dia: set = set()

        for route in route_summary:
            ruta_id  = route["ruta_id"]
            total_kg = route["total_kg"]
            asig     = asignaciones.get(ruta_id, {})

            # Elegibles: no usados hoy, capacidad > 0,
            # y route_kg ≤ vehicle_capacity_kg × max_factor
            eligible = [
                v for v in vehiculos_sorted
                if v.get("placas") not in usados_dia
                and (v.get("capacidad_toneladas") or 0) > 0
                and total_kg <= (_capacidad_efectiva_ton(v["capacidad_toneladas"]) * 1000.0) * max_factor
            ]

            if not eligible:
                old_placas = asig.get("placas")
                if old_placas is not None:
                    n_reasignadas += 1
                    cambios.append({
                        "ruta_id": ruta_id,
                        "before":  old_placas,
                        "after":   None,
                        "razon":   "sin_vehiculo_elegible",
                    })
                asig["placas"]           = None
                asig["pct"]              = 0.0
                asig["en_rango"]         = False
                asig["vrp_applied"]      = True
                n_sin_vehiculo += 1
                detalle[ruta_id] = {
                    "vehicle_id":       None,
                    "utilization_pct":  0.0,
                    "within_tolerance": False,
                    "overloaded":       False,
                }
                continue

            # Seleccionar el más pequeño elegible (mejor ajuste sin desperdiciar)
            best_v     = eligible[0]
            placas_new = best_v.get("placas")
            cap_kg     = _capacidad_efectiva_ton(best_v.get("capacidad_toneladas", 1)) * 1000.0
            util_pct   = round((total_kg / cap_kg) * 100, 1)
            within_tol = abs(util_pct / 100.0 - 1.0) <= cap_tolerance
            overloaded = util_pct > max_util_pct

            usados_dia.add(placas_new)

            old_placas = asig.get("placas")
            if old_placas != placas_new:
                n_reasignadas += 1
                cambios.append({
                    "ruta_id": ruta_id,
                    "before":  old_placas,
                    "after":   placas_new,
                    "razon":   "vrp_reasignacion",
                })

            asig["placas"]       = placas_new
            asig["pct"]          = util_pct
            asig["en_rango"]     = within_tol
            asig["vrp_applied"]  = True

            if within_tol:
                n_en_rango += 1
            if overloaded:
                n_sobrecargadas += 1

            detalle[ruta_id] = {
                "vehicle_id":       placas_new,
                "driver":           best_v.get("conductor") or best_v.get("nombre", ""),
                "capacity_kg":      round(cap_kg, 1),
                "total_kg":         round(total_kg, 1),
                "utilization_pct":  util_pct,
                "within_tolerance": within_tol,
                "overloaded":       overloaded,
            }

        estado["vehiculos_usados"] = usados_dia

    return {
        "aplicado":        True,
        "config":          {"cap_tolerance": cap_tolerance},
        "n_reasignadas":   n_reasignadas,
        "n_en_rango":      n_en_rango,
        "n_sobrecargadas": n_sobrecargadas,
        "n_sin_vehiculo":  n_sin_vehiculo,
        "cambios":         cambios,
        "detalle":         detalle,
    }


def obtener_config_reacomodamiento() -> dict:
    """Expone la config de reacomodamiento para el endpoint de configuración."""
    return _leer_config_reacomodamiento()


# ── Config de días (por logística) ────────────────────────────

def obtener_config_dias(logistica_id: str = None) -> dict:
    """
    Devuelve la configuración de días de operación con dos capas:

      1. BASE: la config global guardada en la colección 'configuracion'
         (sección "Configuración de días de operación" del menú Configuración).
      2. OVERRIDE: si existe una config específica para la logística activa
         (guardada con el modal ⚙ Días dentro de Asignación), sus valores
         sobreescriben los de la base día a día.

    Así la asignación siempre respeta lo que el usuario definió en Configuración,
    y solo se desvía si el usuario lo ajustó manualmente dentro de la logística.
    """
    # ── 1. Cargar config global como base ────────────────────────
    base: dict = {}
    try:
        cfg_global = _obtener_config_general()
        base = dict(cfg_global.get("config_dias") or {})
    except Exception:
        pass

    # ── 2. Cargar config por logística y aplicar como override ──
    if logistica_id:
        oid = _parse_oid(logistica_id)
        if oid:
            try:
                db  = get_db()
                doc = db["config_dias"].find_one({"logistica_id": oid})
                config_logistica = (doc.get("config_dias") or {}) if doc else {}
                if config_logistica:
                    base = {**base, **config_logistica}
            except Exception:
                pass

    return base


def guardar_config_dias(datos: dict, logistica_id: str = None) -> dict:
    if not logistica_id:
        return {"status": "error", "mensaje": "Se requiere logistica_id."}
    oid = _parse_oid(logistica_id)
    if not oid:
        return {"status": "error", "mensaje": "logistica_id inválido."}
    try:
        db = get_db()
        db["config_dias"].update_one(
            {"logistica_id": oid},
            {"$set": {
                "logistica_id":  oid,
                "config_dias":   datos,
                "actualizado_en": datetime.now().isoformat(),
            }},
            upsert=True,
        )
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "mensaje": str(e)}


# ── Guardar / recuperar asignación ────────────────────────────

def asignar_rutas(datos: dict, logistica_id: str = None) -> dict:
    """Punto de entrada legacy — delega en guardar_asignacion."""
    return guardar_asignacion(datos, logistica_id)


def guardar_asignacion(payload: dict, logistica_id: str = None) -> dict:
    if not logistica_id:
        return {"status": "error", "mensaje": "Se requiere logistica_id activo."}
    oid = _parse_oid(logistica_id)
    if not oid:
        return {"status": "error", "mensaje": "logistica_id inválido."}

    payload["guardado_en"] = datetime.now().isoformat()

    try:
        config_dias      = obtener_config_dias(logistica_id)
        dias_habilitados = [d for d, cfg in config_dias.items() if cfg.get("habilitado")]
    except Exception:
        dias_habilitados = []

    asig_por_dia = payload.get("asignaciones_por_dia", {})
    dias_prog    = payload.get("dias_programados", {})

    def siguiente_dia_habilitado(dia_actual: str) -> str:
        idx = DIAS_ORDEN.index(dia_actual) if dia_actual in DIAS_ORDEN else 0
        for i in range(1, 8):
            candidato = DIAS_ORDEN[(idx + i) % 7]
            if candidato in dias_habilitados:
                return candidato
        return dia_actual

    reprogramadas = {}
    for ruta_id, dia_actual in dias_prog.items():
        placas = asig_por_dia.get(dia_actual, {}).get(ruta_id, "")
        if not placas:
            sig_dia = siguiente_dia_habilitado(dia_actual)
            if sig_dia != dia_actual:
                reprogramadas[ruta_id] = {"de": dia_actual, "a": sig_dia}

    payload["reprogramadas"] = reprogramadas

    try:
        db = get_db()
        db["asignaciones"].update_one(
            {"logistica_id": oid},
            {"$set": {"logistica_id": oid, **payload}},
            upsert=True,
        )
        return {"status": "ok", "reprogramadas": reprogramadas}
    except Exception as e:
        return {"status": "error", "mensaje": str(e)}


def obtener_mayoristas_por_ruta(logistica_id: str) -> dict:
    """
    Devuelve mayoristas asignados por cercanía y el orden sugerido de sucursales.

    Retorna:
      {
        "mayoristas": {ruta_id: [..] },
        "orden_sucursales": {ruta_id: {num_tienda: orden}}
      }
    """
    try:
        dist = calcular_distribucion_mayoristas(logistica_id)
        return {
            "mayoristas": dist.get("mayoristas_por_ruta", {}),
            "orden_sucursales": dist.get("orden_sucursales", {}),
        }
    except Exception as e:
        print(f"[obtener_mayoristas_por_ruta] Error: {e}")
        return {"mayoristas": {}, "orden_sucursales": {}}


def obtener_geometria_ruta(ruta_id: str, logistica_id: str) -> dict:
    """
    Devuelve la geometría de una ruta para visualización en mapa.

    Proceso:
      1. Busca la ruta en distribucion_mayoristas (paradas_integradas, incluye
         mayoristas en su posición óptima dentro del recorrido).
      2. Si no está, recurre a rutas_config con solo sucursales.
      3. Consulta OSRM con overview=full&geometries=geojson para obtener la
         polilínea real. Cachea el resultado en cache_osrm (tipo='geometria').
      4. Si OSRM falla, devuelve los puntos como polilínea recta (fallback).

    Returns:
        {
          paradas: [{tipo, nombre, lat, lon, orden, peso_kg?, desvio_m?}],
          polyline: [[lat, lon], ...],   # coordenadas para Leaflet (lat primero)
          distancia_km: float,
          duracion_min: float,
          origen: "osrm" | "fallback_lineal" | "sin_datos",
        }
    """
    import json as _json

    cfg        = _obtener_config_general()
    matriz_lat = MATRIZ_LAT_DEFAULT
    matriz_lon = MATRIZ_LON_DEFAULT
    velocidad  = float(cfg.get("velocidad_kmh") or 35.0)

    # ── Pesos de mayoristas del ciclo activo ─────────────────────
    # Regla: solo mayoristas con pedido (peso_total_kg > 0).
    oid = _parse_oid(logistica_id)
    pesos_may: dict = {}
    try:
        db  = get_db()
        ext = db["extraccion"].find_one({"logistica_id": oid})
        if ext:
            for m in ext.get("mayoristas", []):
                codigo = m.get("codigo")
                if codigo is not None:
                    try:
                        peso = float(m.get("peso_total_kg", 0) or 0)
                        if peso > 0:
                            pesos_may[int(str(codigo).split(".")[0])] = peso
                    except (ValueError, TypeError):
                        pass
    except Exception:
        pass

    # ── Paradas ordenadas (cercanía) ────────────────────────────
    paradas: list = []
    try:
        dist = calcular_distribucion_mayoristas(logistica_id)
        base = dist.get("paradas_integradas", {}).get(ruta_id, [])
        for p in base:
            lat = p.get("latitud")
            lon = p.get("longitud")
            if lat is None or lon is None:
                continue
            tipo = p.get("tipo", "sucursal")
            nombre = p.get("nombre_base") or p.get("nombre", "")
            peso = float(p.get("peso_kg") or 0)
            paradas.append({
                "tipo":     tipo,
                "nombre":   nombre,
                "lat":      float(lat),
                "lon":      float(lon),
                "orden":    p.get("orden"),
                "peso_kg":  peso,
                "desvio_m": p.get("desvio_m"),
                "num_tienda": p.get("num_tienda"),
                "id_cliente": p.get("id_cliente"),
            })
    except Exception as e:
        print(f"[obtener_geometria_ruta] Error leyendo distribución: {e}")

    # Fallback: solo sucursales desde rutas_config
    if not paradas:
        try:
            db      = get_db()
            oid_r   = _parse_oid(ruta_id)
            ruta_doc = db["rutas_config"].find_one({"_id": oid_r})
            if ruta_doc:
                sucursales = _ordenar_sucursales_planificacion(ruta_doc.get("sucursales", []))
                for s in sucursales:
                    lat = s.get("latitud")
                    lon = s.get("longitud")
                    if lat is None or lon is None:
                        continue
                    paradas.append({
                        "tipo":   "sucursal",
                        "nombre": s.get("nombre_base", ""),
                        "lat":    float(lat),
                        "lon":    float(lon),
                        "orden":  s.get("orden"),
                    })
        except Exception as e:
            print(f"[obtener_geometria_ruta] Error leyendo rutas_config: {e}")

    if not paradas:
        return {
            "paradas":      [],
            "polyline":     [],
            "distancia_km": 0.0,
            "duracion_min": 0.0,
            "origen":       "sin_datos",
        }

    # ── Waypoints: depósito → paradas → depósito ─────────────────
    coords = [(matriz_lat, matriz_lon)]
    for p in paradas:
        coords.append((p["lat"], p["lon"]))
    coords.append((matriz_lat, matriz_lon))

    # ── Cache de geometría ───────────────────────────────────────
    clave = _cache_key(coords)
    polyline:     list  = []
    distancia_km: float = 0.0
    duracion_min: float = 0.0
    origen:       str   = "sin_datos"

    cached = None
    try:
        db  = get_db()
        doc = db["cache_osrm"].find_one({"clave": clave, "tipo": "geometria"})
        if doc:
            cached = doc["resultado"]
    except Exception:
        pass

    if cached and cached.get("polyline"):
        polyline     = cached.get("polyline", [])
        distancia_km = cached.get("distancia_km", 0.0)
        duracion_min = cached.get("duracion_min", 0.0)
        origen       = cached.get("origen", "osrm")
    else:
        waypoints_str = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in coords)
        url           = f"{OSRM_BASE_URL}/{waypoints_str}?overview=full&geometries=geojson"
        ultimo_error  = None

        for intento in range(OSRM_MAX_RETRIES):
            try:
                if intento > 0:
                    time.sleep(OSRM_RETRY_DELAY)
                req = urllib.request.Request(
                    url,
                    headers={"User-Agent": "ICG-RouteAssignment/1.0", "Accept": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=OSRM_TIMEOUT) as resp:
                    data = _json.loads(resp.read().decode("utf-8"))

                if data.get("code") == "Ok" and data.get("routes"):
                    route = data["routes"][0]
                    geom  = route.get("geometry", {})
                    polyline     = [[c[1], c[0]] for c in geom.get("coordinates", [])]
                    distancia_km = round(route.get("distance", 0.0) / 1000, 2)
                    duracion_min = round(route.get("duration", 0.0) / 60, 1)
                    origen       = "osrm"
                    break
                else:
                    ultimo_error = f"OSRM code={data.get('code')}"
            except urllib.error.HTTPError as e:
                ultimo_error = f"HTTP {e.code}"
                if e.code == 429:
                    time.sleep(OSRM_RETRY_DELAY * 2)
            except Exception as e:
                ultimo_error = str(e)
        else:
            print(f"[obtener_geometria_ruta] OSRM agotó reintentos ({ultimo_error}); usando fallback lineal")
            fb           = _fallback_haversine(coords, velocidad)
            polyline     = [[lat, lon] for lat, lon in coords]
            distancia_km = fb["distancia_km"]
            duracion_min = fb["traslado_min"]
            origen       = "fallback_lineal"

        # Solo guardar en caché si OSRM respondió correctamente
        if origen == "osrm" and polyline:
            try:
                db = get_db()
                db["cache_osrm"].update_one(
                    {"clave": clave, "tipo": "geometria"},
                    {"$set": {
                        "resultado": {
                            "polyline":     polyline,
                            "distancia_km": distancia_km,
                            "duracion_min": duracion_min,
                            "origen":       origen,
                        },
                        "actualizado_en": datetime.now().isoformat(),
                    }},
                    upsert=True,
                )
            except Exception:
                pass

    # Agregar depósito al inicio de la lista de paradas (para los marcadores)
    paradas_con_depot = [{
        "tipo":   "depot",
        "nombre": "Depósito (salida/regreso)",
        "lat":    matriz_lat,
        "lon":    matriz_lon,
        "orden":  0,
    }] + paradas

    return {
        "paradas":      paradas_con_depot,
        "polyline":     polyline,
        "distancia_km": distancia_km,
        "duracion_min": duracion_min,
        "origen":       origen,
    }


def obtener_asignaciones_previas(logistica_id: str = None) -> dict:
    if not logistica_id:
        return {}
    oid = _parse_oid(logistica_id)
    if not oid:
        return {}
    try:
        db  = get_db()
        doc = db["asignaciones"].find_one({"logistica_id": oid})
        if not doc:
            return {}
        doc.pop("_id", None)
        doc.pop("logistica_id", None)
        return {k: str(v) if isinstance(v, ObjectId) else v for k, v in doc.items()}
    except Exception:
        return {}