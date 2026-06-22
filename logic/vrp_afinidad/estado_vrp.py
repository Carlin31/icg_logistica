"""
logic/vrp_afinidad/estado_vrp.py
Clasificación del estado de cada ruta según ocupación e historial.
"""
from .clarke_wright import Ruta

# Umbrales (mismos que vrp_logic.py para coherencia)
_DEV_NORMAL   = 0.30
_DEV_ESPECIAL = 0.50
_CAP_PEQUEÑO  = 1300


VRP_STATUS_LABEL = {
    "NORMAL":     "Normal (≤30%)",
    "EDGE_CASE":  "Caso especial (30–50%)",
    "CRITICO":    "Crítico (>50%)",
    "SOBRECARGA": "Sobrecarga",
    "SIN_HIST":   "Sin historial",
    "OK_PEQUEÑO": "Camión pequeño (cap. ok)",
}


def calcular_estado(ruta: Ruta, route_stats_historico: dict | None = None) -> str:
    """
    Clasifica el estado de una ruta.

    Parámetros
    ----------
    ruta                  : Ruta generada por generar_rutas()
    route_stats_historico : {(vehiculo, dia): {'kg_avg': float, ...}}
                            puede ser None si no hay historial de esa ruta.

    Retorna
    -------
    str — uno de NORMAL | EDGE_CASE | CRITICO | SOBRECARGA | OK_PEQUEÑO | SIN_HIST
    """
    if ruta.kg_total > ruta.capacidad:
        return "SOBRECARGA"

    if ruta.capacidad <= _CAP_PEQUEÑO:
        return "OK_PEQUEÑO"

    hist_avg = None
    if route_stats_historico:
        clave = (ruta.vehiculo, ruta.dia)
        stats = route_stats_historico.get(clave, {})
        hist_avg = stats.get("kg_avg")

    if hist_avg is None or hist_avg == 0:
        return "SIN_HIST"

    dev = abs(ruta.kg_total - hist_avg) / hist_avg
    if dev > _DEV_ESPECIAL:
        return "CRITICO"
    if dev > _DEV_NORMAL:
        return "EDGE_CASE"
    return "NORMAL"


def porcentaje_ocupacion(ruta: Ruta) -> float:
    return round(ruta.ocupacion * 100, 1)
