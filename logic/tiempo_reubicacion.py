"""
logic/tiempo_reubicacion.py

Fase B — tiempo de entrega: reubica automáticamente las paradas que Fase A
marca FUERA DE HORARIO hacia otra ruta con afinidad histórica real, cupo
(≤85 % de utilización) y tiempo. Fase A solo detecta; Fase B mueve.

Continúa docs/superpowers/specs/2026-08-06-tiempo-entrega-faseB-design.md.

Módulo mayormente puro: no importa OSRM ni BD directamente. Quien llama
inyecta `consultar_osrm_fn` (típicamente `logic.asignacion_logic.consultar_osrm`)
para poder evaluar con datos reales cuando hay red, con haversine como
respaldo — mismo criterio que ya usa Fase A. La persistencia (guardar en
`modificaciones_rutas`) queda a cargo de quien llama a
`resolver_fuera_de_horario`, no de este módulo.
"""
import math

from logic.logistica_tiempo import evaluar_llegadas, evaluar_ruta_por_tiempo, hhmm_a_min

UMBRAL_PCT_DESTINO = 85.0
# Salvaguarda anti-bucle: tope de movimientos por ruta origen en una sola
# resolución (una ruta real rara vez tiene más de un puñado de paradas
# FUERA DE HORARIO).
MAX_MOVIMIENTOS_POR_RUTA = 20


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _normalizar_veh(s) -> str:
    """Mayúsculas y sin espacios, para comparar nombres de vehículo entre el
    histórico y la ruta actual sin caer en el bug ya confirmado del proyecto
    ('F350_2' != 'F 350_2', ver MIGRACION_STATUS.md)."""
    return str(s or "").strip().upper().replace(" ", "")


def evaluar_ruta_completa(paradas: list, dia: str, cfg_tiempo: dict,
                          consultar_osrm_fn=None) -> list:
    """
    Evalúa la hora de llegada a cada parada de una ruta YA ORDENADA, contra
    el horario configurado de `dia`. Intenta tramos reales vía
    `consultar_osrm_fn` primero; si no hay función, falla, o no trae
    'tramos_min', usa haversine (evaluar_ruta_por_tiempo) — mismo criterio
    que usaba `pdf_logic._tabla_vehiculo` en Fase A, ahora factorizado para
    que Fase B decida con el mismo criterio que termina impreso.

    paradas: en orden, dicts con latitud/longitud/peso_kg y _tipo
             ('sucursal'|'mayorista') o es_mayorista (bool).
    cfg_tiempo: {'depot': (lat,lon), 'velocidad': kmh,
                 'dias': {dia: {'hora_salida': 'HH:MM', 'hora_limite': 'HH:MM'}}}.
    Retorna copias de `paradas` con 'hora_llegada_min' y
    'entregable_por_tiempo' (ver logistica_tiempo.evaluar_llegadas).
    """
    dcfg  = cfg_tiempo.get("dias", {}).get(dia, {})
    h_sal = hhmm_a_min(dcfg.get("hora_salida"), 420)
    h_lim = hhmm_a_min(dcfg.get("hora_limite"), 1080)
    depot = cfg_tiempo.get("depot")

    paradas_t = [{
        "latitud": p.get("latitud"), "longitud": p.get("longitud"),
        "peso_kg": p.get("peso_kg", 0),
        "es_mayorista": p.get("es_mayorista", p.get("_tipo") == "mayorista"),
    } for p in paradas]

    tramos = None
    if consultar_osrm_fn is not None:
        try:
            pts, prev = [depot], depot
            for p in paradas:
                la, lo = p.get("latitud"), p.get("longitud")
                if la is not None and lo is not None:
                    prev = (float(la), float(lo))
                pts.append(prev)
            pts.append(depot)
            r = consultar_osrm_fn(pts)
            if "error" not in r and r.get("tramos_min"):
                tramos = r["tramos_min"]
        except Exception:
            tramos = None

    return (evaluar_llegadas(paradas_t, tramos, h_sal, h_lim) if tramos
            else evaluar_ruta_por_tiempo(paradas_t, depot, h_sal, h_lim,
                                         cfg_tiempo.get("velocidad", 35.0)))
