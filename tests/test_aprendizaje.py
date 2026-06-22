"""
tests/test_aprendizaje.py
Pruebas del módulo de aprendizaje por correcciones manuales.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from logic.vrp_afinidad.aprendizaje import (
    registrar_correccion,
    aplicar_decaimiento,
    renormalizar,
)
from logic.vrp_afinidad.afinidad import construir_afinidad
from logic.vrp_afinidad.clarke_wright import generar_rutas
from tests.datos_sinteticos import (
    generar_historiales, pedidos_normal, COORDS, DEPOSITO, CAPACIDADES,
)


@pytest.fixture
def afinidad_raw():
    historiales = generar_historiales(n=4)
    res = construir_afinidad(historiales)
    return dict(res["afinidad_raw"])  # copia mutable


def test_correccion_incrementa_afinidad_destino(afinidad_raw):
    """Mover nodo 1 hacia ruta con nodo 11 incrementa afinidad(1, 11)."""
    par = (1, 11)
    antes = afinidad_raw.get(par, 0.0)
    registrar_correccion(
        afinidad=afinidad_raw,
        sucursal_movida=1,
        ruta_origen=[2, 3, 4],
        ruta_destino=[11, 12, 13],
        factor=1.0,
    )
    despues = afinidad_raw.get(par, 0.0)
    assert despues > antes, f"Afinidad no incrementó: {antes} → {despues}"


def test_correccion_decrementa_afinidad_origen(afinidad_raw):
    """Mover nodo 1 fuera de la ruta con nodo 2 decrementa afinidad(1, 2)."""
    par = (1, 2)
    antes = afinidad_raw.get(par, 0.0)
    registrar_correccion(
        afinidad=afinidad_raw,
        sucursal_movida=1,
        ruta_origen=[2, 3, 4],
        ruta_destino=[11, 12, 13],
        factor=1.0,
    )
    despues = afinidad_raw.get(par, 0.0)
    assert despues < antes, f"Afinidad no decrementó: {antes} → {despues}"


def test_afinidad_no_negativa(afinidad_raw):
    """Ningún valor de afinidad puede ser negativo tras múltiples correcciones."""
    for _ in range(20):
        registrar_correccion(
            afinidad=afinidad_raw,
            sucursal_movida=1,
            ruta_origen=[2, 3, 4],
            ruta_destino=[11, 12, 13],
            factor=5.0,
        )
    for par, val in afinidad_raw.items():
        assert val >= 0.0, f"Afinidad negativa en {par}: {val}"


def test_decaimiento_reduce_valores(afinidad_raw):
    """El decaimiento debe reducir todos los valores."""
    copia_antes = dict(afinidad_raw)
    aplicar_decaimiento(afinidad_raw, factor_decay=0.80)
    for par, val_antes in copia_antes.items():
        val_despues = afinidad_raw.get(par, 0.0)
        assert val_despues <= val_antes, f"Decaimiento no redujo {par}"


def test_decaimiento_elimina_entradas_minimas():
    """Entradas muy pequeñas deben eliminarse después del decaimiento."""
    af = {(1, 2): 1e-7, (3, 4): 100.0}
    aplicar_decaimiento(af, factor_decay=0.5)
    assert (1, 2) not in af, "Entrada mínima no fue eliminada"
    assert (3, 4) in af


def test_renormalizar_rango_0_1():
    """renormalizar debe retornar valores en [0,1]."""
    af = {(1, 2): 50.0, (3, 4): 100.0, (5, 6): 25.0}
    norm = renormalizar(af)
    for par, val in norm.items():
        assert 0.0 <= val <= 1.0
    assert norm[(3, 4)] == pytest.approx(1.0)


def test_correccion_afecta_siguiente_generacion():
    """
    Tras una corrección que acerca nodo 21 al cluster B (nodos 11-20),
    la afinidad raw(11, 21) debe ser mayor que antes.
    """
    historiales = generar_historiales(n=4)
    res = construir_afinidad(historiales)
    af_raw = dict(res["afinidad_raw"])

    par = (min(11, 21), max(11, 21))
    antes = af_raw.get(par, 0.0)

    # Simulamos que el operador movió nodo 21 de la ruta C a la ruta B
    registrar_correccion(
        afinidad=af_raw,
        sucursal_movida=21,
        ruta_origen=[22, 23, 24],   # ruta C
        ruta_destino=[11, 12, 13],  # ruta B
        factor=2.0,
    )
    despues = af_raw.get(par, 0.0)
    assert despues > antes, "La corrección no mejoró la afinidad 11-21"

    # Al renormalizar y usar en la siguiente generación, el par debe tener
    # afinidad positiva
    af_norm = renormalizar(af_raw)
    assert af_norm.get(par, 0.0) > 0.0


def test_factor_escala_correccion():
    """Un factor mayor debe producir un cambio mayor en la afinidad."""
    af1 = {(1, 11): 10.0}
    af2 = {(1, 11): 10.0}

    registrar_correccion(af1, 1, [2], [11], factor=1.0)
    registrar_correccion(af2, 1, [2], [11], factor=3.0)

    assert af2[(1, 11)] > af1[(1, 11)], "Factor mayor debe producir afinidad mayor"
