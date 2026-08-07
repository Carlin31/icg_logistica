"""
logic/logistica_tiempo.py

Modelo de tiempo de entrega (Fase A). Puro: sin BD ni OSRM.

Regla de negocio: la hora límite del día es el CIERRE de las tiendas; hay que
LLEGAR a cada parada antes de esa hora. Cada parada consume un tiempo de
trámites+descarga proporcional al peso, con piso y techo.

- `tiempo_descarga_min(peso, es_mayorista)` → minutos de descarga de una parada.
- `evaluar_llegadas(paradas, tramos_min, salida, cierre)` → anexa a cada parada
  su hora de llegada acumulada y si es entregable a tiempo.
"""
import math


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def hhmm_a_min(hhmm, default: int = 0) -> int:
    """'HH:MM' → minutos desde 00:00. Devuelve `default` si no parsea."""
    try:
        h, m = str(hhmm).split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError, TypeError):
        return default


# Interruptor: True = modelo real (descarga piso+peso×tasa; deadline por llegada).
# False = el resto del sistema sigue con su cálculo anterior (este módulo no se usa).
TIEMPO_ENTREGA_ESTRICTO = True

# Descarga por parada (min): clamp(piso + peso_kg × TASA, piso, techo)
# Calibrado para un promedio ~50 min/parada (así una ruta agrupada alcanza a
# entregar ~13 paradas dentro del horario). Ajustar si cambia la operación.
DESCARGA_PISO_SUCURSAL   = 38.0
DESCARGA_TECHO_SUCURSAL  = 90.0
DESCARGA_PISO_MAYORISTA  = 38.0
DESCARGA_TECHO_MAYORISTA = 90.0
TASA_DESCARGA_MIN_POR_KG = 0.04   # promedio ~48 min/parada


def tiempo_descarga_min(peso_kg, es_mayorista: bool = False) -> float:
    """Minutos de trámites+descarga de una parada, acotados por tipo."""
    try:
        peso = max(float(peso_kg or 0), 0.0)
    except (TypeError, ValueError):
        peso = 0.0
    if es_mayorista:
        piso, techo = DESCARGA_PISO_MAYORISTA, DESCARGA_TECHO_MAYORISTA
    else:
        piso, techo = DESCARGA_PISO_SUCURSAL, DESCARGA_TECHO_SUCURSAL
    return min(max(piso + peso * TASA_DESCARGA_MIN_POR_KG, piso), techo)


def evaluar_llegadas(paradas: list, tramos_min: list,
                     hora_salida_min: float, hora_limite_min: float) -> list:
    """
    Calcula la hora de llegada acumulada a cada parada y si se alcanza antes del
    cierre.

    paradas         : lista EN ORDEN de dicts con al menos {peso_kg, es_mayorista?}
    tramos_min      : duración de cada tramo [matriz→p1, p1→p2, …]; se usan los
                      primeros len(paradas) (el regreso, si viene, se ignora)
    hora_salida_min : minutos desde 00:00 de la salida (p. ej. 07:00 = 420)
    hora_limite_min : minutos del cierre (p. ej. 20:00 = 1200)

    Retorna copias de las paradas con 'hora_llegada_min' y
    'entregable_por_tiempo' añadidos. Determinista.
    """
    resultado: list = []
    t = float(hora_salida_min)
    for i, p in enumerate(paradas):
        # viaje hacia esta parada
        t += float(tramos_min[i]) if i < len(tramos_min) else 0.0
        nuevo = dict(p)
        nuevo["hora_llegada_min"] = round(t, 1)
        nuevo["entregable_por_tiempo"] = t <= hora_limite_min
        resultado.append(nuevo)
        # tiempo consumido en esta parada antes de salir a la siguiente
        t += tiempo_descarga_min(p.get("peso_kg", 0), p.get("es_mayorista", False))
    return resultado


# Velocidad haversine-equivalente (km en línea recta ÷ hora de viaje real),
# calibrada contra OSRM sobre 875 tramos de las 9 semanas confirmadas. NO es
# constante: los tramos largos (matriz → clúster) son mucho más "directos" que
# el zigzag entre sucursales cercanas. Una sola constante de 35 km/h inflaba el
# tramo matriz→Los Tuxtlas en ~2.5 h y disparaba violaciones de tiempo falsas.
VELOCIDAD_TRAMO_LARGO_KMH = 55.5   # mediana de 249 tramos > 50 km
VELOCIDAD_TRAMO_CORTO_KMH = 37.8   # mediana de 626 tramos ≤ 50 km
UMBRAL_TRAMO_LARGO_KM = 50.0


def velocidad_para_km(km: float) -> float:
    """Velocidad haversine-equivalente según la longitud del tramo."""
    return (VELOCIDAD_TRAMO_LARGO_KMH if float(km) > UMBRAL_TRAMO_LARGO_KM
            else VELOCIDAD_TRAMO_CORTO_KMH)


def _latlon(p: dict):
    lat = p.get("latitud", p.get("lat"))
    lon = p.get("longitud", p.get("lon"))
    if lat is None or lon is None:
        return None
    return (float(lat), float(lon))


def evaluar_ruta_por_tiempo(paradas: list, depot: tuple, hora_salida_min: float,
                            hora_limite_min: float, velocidad_kmh: float = 35.0,
                            por_tramo: bool = False) -> list:
    """
    Evalúa las llegadas de una ruta calculando el traslado por HAVERSINE
    (rápido, sin OSRM: en rutas agrupadas el tiempo lo domina la descarga).

    paradas   : en orden, con lat/lon (latitud/longitud o lat/lon), peso_kg,
                es_mayorista.
    depot     : (lat, lon) de la matriz.
    por_tramo : True = velocidad calibrada según la longitud de cada tramo
                (ver `velocidad_para_km`); False = `velocidad_kmh` para todos,
                comportamiento anterior.
    Retorna evaluar_llegadas(...) sobre esta ruta.
    """
    vel = velocidad_kmh if (velocidad_kmh and velocidad_kmh > 0) else 35.0
    tramos: list = []
    prev = depot
    for p in paradas:
        ll = _latlon(p)
        if ll is None or prev is None:
            tramos.append(0.0)   # sin coords: no se puede medir el tramo
        else:
            km = _haversine_km(prev[0], prev[1], ll[0], ll[1])
            v = velocidad_para_km(km) if por_tramo else vel
            tramos.append(km / v * 60.0)
            prev = ll
    return evaluar_llegadas(paradas, tramos, hora_salida_min, hora_limite_min)
