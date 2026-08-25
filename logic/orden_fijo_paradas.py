"""
logic/orden_fijo_paradas.py

Orden de visita fijo por regla nombrada. Cuando TODAS las sucursales de una
ruta pertenecen a la misma regla, ese orden gana sobre el histórico y la
geografía (ver ordenar_paradas_por_historico en vrp_logic.py). Si la ruta
mezcla sucursales de la regla con otras que no son parte de ella, no se
aplica ningún pin -- la ruta sigue el camino normal sin cambios.
"""
from sqlalchemy import select

from db import get_table


def obtener_orden_fijo(db) -> dict:
    """
    Lee orden_fijo_paradas y arma {num_tienda: (nombre_regla, posicion)}.

    Se llama UNA sola vez por corrida de generar_rutas_vrp_afinidad (antes
    del bucle de rutas), no por ruta -- es una tabla chica de referencia.
    """
    t = get_table("orden_fijo_paradas")
    filas = db.execute(select(t.c.num_tienda, t.c.nombre_regla, t.c.posicion)).mappings().all()
    return {f["num_tienda"]: (f["nombre_regla"], f["posicion"]) for f in filas}


def aplicar_orden_fijo(miembros: list, orden_fijo: dict):
    """
    Devuelve las sids de `miembros` ordenadas por su posición fija, o None
    si la ruta no aplica: falta alguna sucursal en `orden_fijo`, o mezcla
    sucursales de más de una regla.

    miembros: [{"sid": id, ...}, ...] -- solo se usa la clave "sid".
    """
    if not miembros or not orden_fijo:
        return None

    entradas = []
    reglas = set()
    for m in miembros:
        sid = m["sid"]
        if sid not in orden_fijo:
            return None
        nombre_regla, posicion = orden_fijo[sid]
        reglas.add(nombre_regla)
        entradas.append((posicion, sid))

    if len(reglas) != 1:
        return None

    entradas.sort(key=lambda e: e[0])
    return [sid for _, sid in entradas]
