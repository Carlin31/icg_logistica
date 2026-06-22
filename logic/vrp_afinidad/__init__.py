"""
logic/vrp_afinidad — Motor de ruteo por afinidad histórica + Clarke-Wright.
Módulo independiente; no toca el flujo de producción existente.
"""
from .afinidad import construir_afinidad
from .clarke_wright import generar_rutas, Ruta
from .estado_vrp import calcular_estado
from .aprendizaje import registrar_correccion, aplicar_decaimiento

__all__ = [
    "construir_afinidad",
    "generar_rutas",
    "Ruta",
    "calcular_estado",
    "registrar_correccion",
    "aplicar_decaimiento",
]
