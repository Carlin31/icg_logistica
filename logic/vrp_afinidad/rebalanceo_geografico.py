"""
logic/vrp_afinidad/rebalanceo_geografico.py

Rebalanceo geográfico de rutas por búsqueda local. La cercanía entre
sucursales gana sobre el patrón histórico: reacomoda sucursales entre rutas
del MISMO día para minimizar la dispersión al centroide, respetando peso y
volumen del vehículo y sin cambiar el número de rutas ni el día de cada
sucursal.

Módulo puro: sin BD, sin OSRM. Entrada/salida son estructuras en memoria.
"""
import math
from collections import defaultdict

_EPS = 1e-9


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _coords_validas(sids: list, coords: dict) -> list:
    return [coords[s] for s in sids if s in coords]


def _centroide(pts: list) -> tuple:
    n = len(pts)
    return (sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n)


def _costo_ruta(sids: list, coords: dict) -> float:
    """Dispersión = suma de distancias de cada sucursal al centroide de la ruta.
    Sucursales sin coordenadas se ignoran en el cálculo. <2 puntos -> 0."""
    pts = _coords_validas(sids, coords)
    if len(pts) < 2:
        return 0.0
    clat, clon = _centroide(pts)
    return sum(_haversine(lat, lon, clat, clon) for lat, lon in pts)


def _peso_sids(sids: list, pesos: dict) -> float:
    return sum(pesos.get(s, 0) for s in sids)


def _vol_sids(sids: list, volumenes: dict) -> float:
    return sum(volumenes.get(s, 0.0) for s in sids)


def _cabe(sids: list, veh: str, pesos: dict, volumenes: dict,
          cap_peso: dict, cap_vol: dict) -> bool:
    """True si el conjunto `sids` no excede ni el peso ni el volumen del
    vehículo `veh`. Un vehículo sin capacidad registrada no impone límite."""
    return (_peso_sids(sids, pesos) <= cap_peso.get(veh, float("inf"))
            and _vol_sids(sids, volumenes) <= cap_vol.get(veh, float("inf")))
