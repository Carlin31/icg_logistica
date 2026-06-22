"""
logic/vrp_afinidad/aprendizaje.py
Ajuste incremental de la matriz de afinidad por correcciones manuales del operador.
"""
from collections import defaultdict


def registrar_correccion(
    afinidad: dict,
    sucursal_movida,
    ruta_origen: list,
    ruta_destino: list,
    factor: float = 1.0,
) -> dict:
    """
    Ajusta la afinidad cuando el operador mueve una sucursal de una ruta a otra.

    - Incrementa afinidad entre sucursal_movida y cada miembro de ruta_destino.
    - Decrementa afinidad entre sucursal_movida y cada miembro de ruta_origen.
    - Nunca baja de 0 (la afinidad no puede ser negativa).

    Parámetros
    ----------
    afinidad        : dict de afinidad RAW (no normalizado) — se modifica in-place.
    sucursal_movida : id del nodo que se movió.
    ruta_origen     : lista de ids de nodos que quedan en la ruta origen.
    ruta_destino    : lista de ids de nodos en la ruta destino (sin incluir sucursal_movida).
    factor          : magnitud del ajuste (default 1.0).

    Retorna
    -------
    El mismo dict afinidad modificado.
    """
    s = sucursal_movida

    for dest_nodo in ruta_destino:
        if dest_nodo == s:
            continue
        par = (min(s, dest_nodo), max(s, dest_nodo))
        afinidad[par] = afinidad.get(par, 0.0) + factor

    for orig_nodo in ruta_origen:
        if orig_nodo == s:
            continue
        par = (min(s, orig_nodo), max(s, orig_nodo))
        nuevo = afinidad.get(par, 0.0) - factor
        afinidad[par] = max(0.0, nuevo)  # no negativo

    return afinidad


def aplicar_decaimiento(afinidad: dict, factor_decay: float = 0.95) -> dict:
    """
    Aplica decaimiento multiplicativo a toda la matriz de afinidad.
    Las entradas que caen por debajo de 1e-6 se eliminan para mantener el dict compacto.

    Parámetros
    ----------
    afinidad     : dict {(i,j): float} — modificado in-place.
    factor_decay : multiplicador (0 < factor_decay < 1).

    Retorna
    -------
    El mismo dict modificado.
    """
    umbral = 1e-6
    claves_a_borrar = []
    for par in afinidad:
        afinidad[par] *= factor_decay
        if afinidad[par] < umbral:
            claves_a_borrar.append(par)
    for par in claves_a_borrar:
        del afinidad[par]
    return afinidad


def renormalizar(afinidad_raw: dict) -> dict:
    """
    Renormaliza a [0,1] después de correcciones o decaimiento.
    Devuelve un nuevo dict (no modifica afinidad_raw).
    """
    if not afinidad_raw:
        return {}
    max_val = max(afinidad_raw.values())
    if max_val == 0:
        return {par: 0.0 for par in afinidad_raw}
    return {par: v / max_val for par, v in afinidad_raw.items()}
