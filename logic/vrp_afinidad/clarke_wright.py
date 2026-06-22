"""
logic/vrp_afinidad/clarke_wright.py
Algoritmo Clarke-Wright Savings con sesgo de afinidad histórica.
"""
from math import radians, sin, cos, sqrt, atan2
from dataclasses import dataclass, field
from typing import Any
from collections import defaultdict


# ── Haversine ─────────────────────────────────────────────────────────────────

def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dl = radians(lat2 - lat1)
    dlo = radians(lon2 - lon1)
    a = sin(dl / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlo / 2) ** 2
    return 2 * R * atan2(sqrt(a), sqrt(1 - a))


# ── Estructura de ruta ─────────────────────────────────────────────────────────

@dataclass
class Ruta:
    paradas: list = field(default_factory=list)   # ids de nodos en orden de visita
    kg_total: float = 0.0
    vehiculo: str = ""
    capacidad: int = 0
    dia: str = ""

    @property
    def ocupacion(self) -> float:
        return self.kg_total / self.capacidad if self.capacidad > 0 else 0.0


# ── Nearest-neighbor desde depósito ───────────────────────────────────────────

def _ordenar_nn(nodos: list, coords: dict, deposito: tuple) -> list:
    if not nodos:
        return []
    restantes = list(nodos)
    orden = []
    pos_actual = deposito
    while restantes:
        lat_a, lon_a = pos_actual
        cerca = min(restantes, key=lambda n: haversine(lat_a, lon_a, coords[n][0], coords[n][1]))
        orden.append(cerca)
        pos_actual = coords[cerca]
        restantes.remove(cerca)
    return orden


def _ordenar_historico(
    nodos: list,
    coords: dict,
    deposito: tuple,
    orden_par: dict,
    lambda_hist: float = 0.4,
) -> list:
    """
    Ordena las paradas priorizando la secuencia histórica de visitas.

    En cada paso elige el siguiente nodo con:
        score = (1 + lambda_hist * continuidad) / distancia
    donde continuidad es qué tanto el nodo actual tiende a preceder al candidato
    según orden_par. Si no hay datos históricos para la ruta cae en nearest-neighbor.

    orden_par: {(min(a,b), max(a,b)): float en [-1,1]}
        +1  → el de id menor visita primero
        -1  → el de id mayor visita primero
    """
    if len(nodos) <= 1:
        return list(nodos)

    tiene_hist = any(
        orden_par.get((min(a, b), max(a, b)), 0.0) != 0.0
        for i, a in enumerate(nodos)
        for b in nodos[i + 1:]
    )
    if not tiene_hist:
        return _ordenar_nn(nodos, coords, deposito)

    restantes = list(nodos)
    orden: list = []
    pos_actual = deposito

    while restantes:
        lat_a, lon_a = pos_actual
        ultimo = orden[-1] if orden else None

        def _score(c, _ultimo=ultimo, _lat=lat_a, _lon=lon_a):
            dist = haversine(_lat, _lon, coords[c][0], coords[c][1])
            if dist < 0.001:
                dist = 0.001
            if _ultimo is not None:
                par = (min(_ultimo, c), max(_ultimo, c))
                val = orden_par.get(par, 0.0)
                # val > 0 significa que min(ultimo, c) visita primero.
                # queremos saber si ultimo precede a c → buena continuación.
                continuidad = val if _ultimo < c else -val
                bonus = lambda_hist * max(0.0, continuidad)
            else:
                bonus = 0.0
            return (1.0 + bonus) / dist

        prox = max(restantes, key=_score)
        orden.append(prox)
        restantes.remove(prox)
        pos_actual = coords[prox]

    return orden


# ── Asignación de vehículo óptimo ─────────────────────────────────────────────

def _asignar_vehiculo(
    kg: float,
    capacidades: dict,
    flota_usada: dict,
    dia: str,
    nodos_ruta: list | None = None,
    pref_vehiculo_dia: dict | None = None,
) -> tuple[str, int]:
    """
    Asigna vehículo con preferencia histórica:
      1. Determina por mayoría de votos qué vehículo cubrió históricamente los
         nodos de esta ruta. Si ese vehículo tiene capacidad suficiente y está
         disponible ese día, se asigna directamente.
      2. Si no hay preferencia histórica o el vehículo no está disponible/no cabe,
         elige el más pequeño que cubra kg sin superarlo (smallest-fit).
      3. Si ninguno cabe, toma el de mayor capacidad disponible.
    """
    usados_hoy: set = flota_usada.get(dia, set()) if dia else set()

    # 1. Preferencia histórica
    if nodos_ruta and pref_vehiculo_dia:
        votos: dict = defaultdict(int)
        for nodo in nodos_ruta:
            pref = pref_vehiculo_dia.get(nodo)
            if pref:
                votos[pref[0]] += 1   # pref = (vehiculo_abrev, dia)
        if votos:
            veh_hist = max(votos, key=votos.get)
            cap_hist = capacidades.get(veh_hist, 0)
            if cap_hist >= kg and veh_hist not in usados_hoy:
                return veh_hist, cap_hist

    # 2. Smallest-fit respetando disponibilidad
    candidatos = [
        (abrev, cap) for abrev, cap in capacidades.items()
        if cap >= kg and abrev not in usados_hoy
    ]
    if not candidatos:
        # 3. Ninguno disponible o ninguno cabe: mayor capacidad disponible
        candidatos = sorted(
            ((a, c) for a, c in capacidades.items() if a not in usados_hoy),
            key=lambda x: -x[1],
        ) or sorted(capacidades.items(), key=lambda x: -x[1])
    else:
        candidatos = sorted(candidatos, key=lambda x: x[1])
    return candidatos[0]


# ── Clarke-Wright principal ────────────────────────────────────────────────────

def generar_rutas(
    pedidos: dict,
    coords: dict,
    afinidad_data: dict,
    capacidades: dict,
    deposito_coords: tuple,
    lambda_afinidad: float = 0.5,
    ocupacion_objetivo: float = 0.92,
    restricciones: dict | None = None,
) -> list[Ruta]:
    """
    Genera rutas usando Clarke-Wright Savings modificado con sesgo de afinidad.

    Parámetros
    ----------
    pedidos         : {id_nodo: kg}
    coords          : {id_nodo: (lat, lon)}
    afinidad_data   : resultado de construir_afinidad()
    capacidades     : {abrev_vehiculo: kg_capacidad}
    deposito_coords : (lat, lon)
    lambda_afinidad : peso del término de afinidad en el savings (0=pura distancia, 1=pura afinidad)
    ocupacion_objetivo : umbral mínimo de ocupación antes de cerrar una ruta
    restricciones   : {id_nodo: {'vehiculos_permitidos': list, 'debe_ir_solo': bool}}

    Retorna
    -------
    list[Ruta]
    """
    if not pedidos or not capacidades:
        return []

    afinidad_norm = afinidad_data.get("afinidad", {})
    restricciones = restricciones or {}

    nodos = [n for n in pedidos if n in coords and pedidos[n] > 0]
    if not nodos:
        return []

    cap_max = max(capacidades.values()) if capacidades else 3500

    # Distancias desde depósito a cada nodo
    dep_lat, dep_lon = deposito_coords
    dist_dep = {n: haversine(dep_lat, dep_lon, coords[n][0], coords[n][1]) for n in nodos}

    # Factor de escala: pone afinidad en la misma magnitud que las distancias (km)
    if dist_dep:
        escala_dist = sum(dist_dep.values()) / len(dist_dep)
    else:
        escala_dist = 50.0

    def _afinidad(i, j) -> float:
        par = (min(i, j), max(i, j))
        return afinidad_norm.get(par, 0.0)

    def _savings(i, j) -> float:
        d = dist_dep[i] + dist_dep[j] - haversine(coords[i][0], coords[i][1], coords[j][0], coords[j][1])
        a = lambda_afinidad * _afinidad(i, j) * escala_dist
        return d + a

    # Nodos que deben ir solos
    solos = {n for n, r in restricciones.items() if r.get("debe_ir_solo")}

    # Inicializar: cada nodo en su propia ruta
    # ruta_de[n] = id de ruta
    ruta_id_ctr = 0
    rutas_abiertas: dict[int, list] = {}  # rid → lista de nodos
    ruta_de: dict[Any, int] = {}

    for n in nodos:
        rutas_abiertas[ruta_id_ctr] = [n]
        ruta_de[n] = ruta_id_ctr
        ruta_id_ctr += 1

    # Calcular todos los savings y ordenar descendente
    pares = []
    for idx_i in range(len(nodos)):
        for idx_j in range(idx_i + 1, len(nodos)):
            i, j = nodos[idx_i], nodos[idx_j]
            if i in solos or j in solos:
                continue
            s = _savings(i, j)
            pares.append((s, i, j))

    pares.sort(key=lambda x: -x[0])

    def _kg_ruta(rid: int) -> float:
        return sum(pedidos.get(n, 0) for n in rutas_abiertas[rid])

    def _cabeza(rid: int):
        """Primer nodo de una ruta (extremo izquierdo)."""
        return rutas_abiertas[rid][0]

    def _cola(rid: int):
        """Último nodo de una ruta (extremo derecho)."""
        return rutas_abiertas[rid][-1]

    # Fusión Clarke-Wright
    for _, i, j in pares:
        rid_i = ruta_de.get(i)
        rid_j = ruta_de.get(j)
        if rid_i is None or rid_j is None or rid_i == rid_j:
            continue

        # Solo fusionar si i es extremo de su ruta y j es extremo de la suya
        r_i = rutas_abiertas[rid_i]
        r_j = rutas_abiertas[rid_j]

        i_es_cabeza = (r_i[0] == i)
        i_es_cola   = (r_i[-1] == i)
        j_es_cabeza = (r_j[0] == j)
        j_es_cola   = (r_j[-1] == j)

        if not ((i_es_cabeza or i_es_cola) and (j_es_cabeza or j_es_cola)):
            continue

        kg_fusionada = _kg_ruta(rid_i) + _kg_ruta(rid_j)
        if kg_fusionada > cap_max:
            continue

        # Verificar restricciones de vehículos compatibles
        perm_i = set(restricciones.get(i, {}).get("vehiculos_permitidos") or capacidades.keys())
        perm_j = set(restricciones.get(j, {}).get("vehiculos_permitidos") or capacidades.keys())
        vehs_compat = set(v for v in capacidades if capacidades[v] >= kg_fusionada)
        if not (perm_i & perm_j & vehs_compat):
            continue

        # Construir ruta fusionada respetando la orientación de extremos
        if i_es_cola and j_es_cabeza:
            nueva = r_i + r_j
        elif i_es_cabeza and j_es_cola:
            nueva = r_j + r_i
        elif i_es_cola and j_es_cola:
            nueva = r_i + list(reversed(r_j))
        else:  # i cabeza, j cabeza
            nueva = list(reversed(r_i)) + r_j

        # Usar el id de rid_i para la ruta fusionada
        rutas_abiertas[rid_i] = nueva
        del rutas_abiertas[rid_j]
        for n in r_j:
            ruta_de[n] = rid_i

    # Construir objetos Ruta finales
    orden_par        = afinidad_data.get("orden_par", {})
    pref_vehiculo_dia = afinidad_data.get("pref_vehiculo_dia", {})
    resultado: list[Ruta] = []
    for rid, nodos_ruta in rutas_abiertas.items():
        if not nodos_ruta:
            continue
        kg = sum(pedidos.get(n, 0) for n in nodos_ruta)
        veh, cap = _asignar_vehiculo(kg, capacidades, {}, "", nodos_ruta, pref_vehiculo_dia)
        paradas_ord = _ordenar_historico(nodos_ruta, coords, deposito_coords, orden_par)
        ruta = Ruta(
            paradas=paradas_ord,
            kg_total=kg,
            vehiculo=veh,
            capacidad=cap,
        )
        resultado.append(ruta)

    # Rutas de nodos solos
    for n in nodos:
        if n in solos:
            kg = pedidos.get(n, 0)
            veh, cap = _asignar_vehiculo(kg, capacidades, {}, "", [n], pref_vehiculo_dia)
            resultado.append(Ruta(paradas=[n], kg_total=kg, vehiculo=veh, capacidad=cap))

    return resultado
