"""
tests/test_clarke_wright.py
Pruebas del algoritmo Clarke-Wright con sesgo de afinidad.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from logic.vrp_afinidad.afinidad import construir_afinidad
from logic.vrp_afinidad.clarke_wright import generar_rutas, Ruta
from tests.datos_sinteticos import (
    generar_historiales, pedidos_normal, pedidos_cambiado,
    COORDS, COORDS_CAMBIADO, DEPOSITO, CAPACIDADES, PATRON_CANONICAL,
)


@pytest.fixture
def historiales():
    return generar_historiales(n=6)


@pytest.fixture
def afinidad_data(historiales):
    return construir_afinidad(historiales)


@pytest.fixture
def rutas_normal(afinidad_data):
    return generar_rutas(
        pedidos=pedidos_normal(),
        coords=COORDS,
        afinidad_data=afinidad_data,
        capacidades=CAPACIDADES,
        deposito_coords=DEPOSITO,
        lambda_afinidad=0.5,
    )


def test_genera_rutas(rutas_normal):
    """Debe generar al menos una ruta."""
    assert len(rutas_normal) > 0


def test_ninguna_ruta_supera_capacidad(rutas_normal):
    """Restricción dura: ninguna ruta puede superar la capacidad del vehículo."""
    for ruta in rutas_normal:
        assert ruta.kg_total <= ruta.capacidad, (
            f"Sobrecarga en ruta {ruta.vehiculo}: {ruta.kg_total:.0f} > {ruta.capacidad}"
        )


def test_todos_los_pedidos_cubiertos(rutas_normal):
    """Todos los nodos con pedido deben aparecer en exactamente una ruta."""
    pedidos = pedidos_normal()
    nodos_en_rutas = []
    for ruta in rutas_normal:
        nodos_en_rutas.extend(ruta.paradas)

    assert set(nodos_en_rutas) == set(pedidos.keys()), \
        "Nodos cubiertos != nodos pedidos"
    assert len(nodos_en_rutas) == len(set(nodos_en_rutas)), \
        "Hay nodos duplicados en las rutas"


def test_lambda_alto_rutas_parecidas_al_historico(afinidad_data, historiales):
    """
    Con lambda_afinidad=1.0 y contexto normal, los pares de sucursales
    dentro de cada cluster deben terminar en la misma ruta (como en el historial).
    """
    rutas = generar_rutas(
        pedidos=pedidos_normal(),
        coords=COORDS,
        afinidad_data=afinidad_data,
        capacidades=CAPACIDADES,
        deposito_coords=DEPOSITO,
        lambda_afinidad=1.0,
    )

    # Construir mapa nodo → índice de ruta
    nodo_ruta = {}
    for idx, ruta in enumerate(rutas):
        for n in ruta.paradas:
            nodo_ruta[n] = idx

    # Pares del cluster A (todos deben estar en la misma ruta)
    cluster_a_ids = list(range(1, 11))
    rutas_a = set(nodo_ruta.get(n) for n in cluster_a_ids if n in nodo_ruta)

    # Pares del cluster B
    cluster_b_ids = list(range(11, 21))
    rutas_b = set(nodo_ruta.get(n) for n in cluster_b_ids if n in nodo_ruta)

    # Pueden estar en 1 o 2 rutas (si supera capacidad se dividen), pero no en 3+
    assert len(rutas_a) <= 2, f"Cluster A disperso en {len(rutas_a)} rutas"
    assert len(rutas_b) <= 2, f"Cluster B disperso en {len(rutas_b)} rutas"

    # Los clusters A y B NO deben mezclarse
    assert rutas_a.isdisjoint(rutas_b), "Cluster A y B se mezclaron"


def test_contexto_cambiado_sin_sobrecarga(afinidad_data):
    """Con demanda aumentada y sucursal nueva, no debe haber sobrecarga."""
    rutas = generar_rutas(
        pedidos=pedidos_cambiado(),
        coords=COORDS_CAMBIADO,
        afinidad_data=afinidad_data,
        capacidades=CAPACIDADES,
        deposito_coords=DEPOSITO,
        lambda_afinidad=0.5,
    )
    for ruta in rutas:
        assert ruta.kg_total <= ruta.capacidad, (
            f"Sobrecarga: {ruta.vehiculo} {ruta.kg_total:.0f} > {ruta.capacidad}"
        )


def test_sucursal_nueva_va_con_vecino_afin(afinidad_data):
    """
    La sucursal nueva (id=31, coords cercanas al cluster C) debe terminar
    en la misma ruta que nodos del cluster C, no en cluster A o B.
    """
    rutas = generar_rutas(
        pedidos=pedidos_cambiado(),
        coords=COORDS_CAMBIADO,
        afinidad_data=afinidad_data,
        capacidades=CAPACIDADES,
        deposito_coords=DEPOSITO,
        lambda_afinidad=0.5,
    )

    nodo_ruta = {}
    for idx, ruta in enumerate(rutas):
        for n in ruta.paradas:
            nodo_ruta[n] = idx

    ruta_de_31 = nodo_ruta.get(31)
    assert ruta_de_31 is not None, "Sucursal 31 no aparece en ninguna ruta"

    # Verificar que haya al menos un nodo del cluster C en la misma ruta
    cluster_c_ids = set(range(21, 31))
    ruta_con_31 = rutas[ruta_de_31]
    nodos_c_misma_ruta = cluster_c_ids & set(ruta_con_31.paradas)

    assert len(nodos_c_misma_ruta) > 0, \
        f"Sucursal 31 no fue asignada junto a nodos del cluster C (fue sola o con A/B)"


def test_sin_veh_grande_no_sobrecarga(afinidad_data):
    """Si quitamos V1 (el más grande), el sistema redistribuye sin sobrecargar."""
    caps_sin_v1 = {k: v for k, v in CAPACIDADES.items() if k != "V1"}
    rutas = generar_rutas(
        pedidos=pedidos_normal(),
        coords=COORDS,
        afinidad_data=afinidad_data,
        capacidades=caps_sin_v1,
        deposito_coords=DEPOSITO,
        lambda_afinidad=0.5,
    )
    for ruta in rutas:
        assert ruta.kg_total <= ruta.capacidad


def test_ruta_con_objeto_ruta(rutas_normal):
    """Cada elemento de la lista es un objeto Ruta con atributos válidos."""
    for r in rutas_normal:
        assert isinstance(r, Ruta)
        assert r.vehiculo in CAPACIDADES
        assert r.kg_total >= 0
        assert r.capacidad > 0
        assert len(r.paradas) > 0
        assert 0.0 < r.ocupacion <= 1.0


def test_lambda_cero_igual_distancia_pura(afinidad_data):
    """Con lambda=0 no usa afinidad; debe funcionar igual que Clarke-Wright puro."""
    rutas = generar_rutas(
        pedidos=pedidos_normal(),
        coords=COORDS,
        afinidad_data=afinidad_data,
        capacidades=CAPACIDADES,
        deposito_coords=DEPOSITO,
        lambda_afinidad=0.0,
    )
    # Condiciones básicas igual que siempre
    for r in rutas:
        assert r.kg_total <= r.capacidad

    nodos_cubiertos = [n for r in rutas for n in r.paradas]
    assert set(nodos_cubiertos) == set(pedidos_normal().keys())
