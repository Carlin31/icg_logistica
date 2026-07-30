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

# Interruptor: True = modelo real (descarga piso+peso×tasa; deadline por llegada).
# False = el resto del sistema sigue con su cálculo anterior (este módulo no se usa).
TIEMPO_ENTREGA_ESTRICTO = True

# Descarga por parada (min): clamp(piso + peso_kg × TASA, piso, techo)
DESCARGA_PISO_SUCURSAL   = 60.0
DESCARGA_TECHO_SUCURSAL  = 120.0
DESCARGA_PISO_MAYORISTA  = 90.0
DESCARGA_TECHO_MAYORISTA = 120.0
TASA_DESCARGA_MIN_POR_KG = 0.05   # con piso 60, ~1200 kg alcanza el techo


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
