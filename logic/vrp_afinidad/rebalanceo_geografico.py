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


def rebalancear_por_geografia(groups: dict, coords: dict, pesos: dict,
                              volumenes: dict, cap_peso: dict, cap_vol: dict,
                              max_iter: int = 500) -> dict:
    """
    Reacomoda sucursales entre rutas del MISMO día para minimizar la dispersión
    geográfica, respetando peso y volumen. No crea/elimina rutas ni cambia días.

    groups     : {(vehiculo, dia): [{"sid": int, "seq": int}, ...]}
    coords     : {sid: (lat, lon)}
    pesos      : {sid: kg}
    volumenes  : {sid: m3}
    cap_peso   : {vehiculo: kg}
    cap_vol    : {vehiculo: m3}

    Retorna un `groups` nuevo con la misma forma.
    """
    # sids por ruta (copia mutable) + metadatos para reconstruir
    rutas = {k: [m["sid"] for m in v] for k, v in groups.items()}
    seq_original: dict = {}
    ruta_original: dict = {}
    for k, v in groups.items():
        for m in v:
            seq_original[m["sid"]] = m.get("seq", 999)
            ruta_original[m["sid"]] = k

    por_dia: dict = defaultdict(list)
    for k in rutas:
        por_dia[k[1]].append(k)
    for dia in por_dia:
        por_dia[dia].sort()  # orden estable -> determinismo

    def costo_par(a, b) -> float:
        return _costo_ruta(rutas[a], coords) + _costo_ruta(rutas[b], coords)

    for _ in range(max_iter):
        mejor_delta = -_EPS
        mejor_mov = None  # ("reloc", a, b, sid) | ("swap", a, b, sa, sb)

        for dia in sorted(por_dia):
            keys = por_dia[dia]
            if len(keys) < 2:
                continue

            # REUBICAR: mover un sid de a -> b
            for a in keys:
                if len(rutas[a]) <= 1:
                    continue  # no vaciar una ruta (conserva el número de rutas)
                for sid in list(rutas[a]):
                    if sid not in coords:
                        continue
                    for b in keys:
                        if b == a:
                            continue
                        destino = rutas[b] + [sid]
                        if not _cabe(destino, b[0], pesos, volumenes, cap_peso, cap_vol):
                            continue
                        antes = costo_par(a, b)
                        origen = [s for s in rutas[a] if s != sid]
                        despues = _costo_ruta(origen, coords) + _costo_ruta(destino, coords)
                        delta = despues - antes
                        if delta < mejor_delta:
                            mejor_delta = delta
                            mejor_mov = ("reloc", a, b, sid, None)

            # INTERCAMBIAR: sa de a <-> sb de b
            for ia in range(len(keys)):
                for ib in range(ia + 1, len(keys)):
                    a, b = keys[ia], keys[ib]
                    for sa in list(rutas[a]):
                        if sa not in coords:
                            continue
                        for sb in list(rutas[b]):
                            if sb not in coords:
                                continue
                            nueva_a = [s for s in rutas[a] if s != sa] + [sb]
                            nueva_b = [s for s in rutas[b] if s != sb] + [sa]
                            if not _cabe(nueva_a, a[0], pesos, volumenes, cap_peso, cap_vol):
                                continue
                            if not _cabe(nueva_b, b[0], pesos, volumenes, cap_peso, cap_vol):
                                continue
                            antes = costo_par(a, b)
                            despues = _costo_ruta(nueva_a, coords) + _costo_ruta(nueva_b, coords)
                            delta = despues - antes
                            if delta < mejor_delta:
                                mejor_delta = delta
                                mejor_mov = ("swap", a, b, sa, sb)

        if mejor_mov is None:
            break

        if mejor_mov[0] == "reloc":
            _, a, b, sid, _ = mejor_mov
            rutas[a].remove(sid)
            rutas[b].append(sid)
        else:
            _, a, b, sa, sb = mejor_mov
            rutas[a].remove(sa); rutas[a].append(sb)
            rutas[b].remove(sb); rutas[b].append(sa)

    # Reconstruir groups. A las sucursales que cambiaron de ruta se les pone
    # seq=999 (no tienen orden histórico válido en su nueva ruta; el
    # re-secuenciado posterior las coloca por proximidad).
    nuevo: dict = {}
    for k, sids in rutas.items():
        miembros = []
        for s in sids:
            seq = seq_original.get(s, 999) if ruta_original.get(s) == k else 999
            miembros.append({"sid": s, "seq": seq})
        nuevo[k] = miembros
    return nuevo
