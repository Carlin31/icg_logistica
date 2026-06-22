"""
tests/test_afinidad.py
Pruebas de la matriz de afinidad.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from logic.vrp_afinidad.afinidad import construir_afinidad
from tests.datos_sinteticos import (
    generar_historiales, CLUSTER_A, CLUSTER_B, CLUSTER_C
)


@pytest.fixture
def historiales():
    return generar_historiales(n=6)


def test_afinidad_mayor_en_misma_ruta(historiales):
    """Pares que siempre comparten ruta deben tener afinidad alta."""
    res = construir_afinidad(historiales)
    af = res["afinidad"]

    # Par dentro del cluster A (siempre V1-LUNES)
    par_aa = (min(1, 2), max(1, 2))
    # Par entre cluster A y B (nunca comparten ruta)
    par_ab = (min(1, 11), max(1, 11))

    assert par_aa in af, "Par del mismo cluster debe tener afinidad"
    assert af[par_aa] > af.get(par_ab, 0), "Mismo cluster > diferente cluster"


def test_afinidad_normalizada_entre_0_y_1(historiales):
    """Todos los valores deben estar en [0,1]."""
    res = construir_afinidad(historiales)
    for par, val in res["afinidad"].items():
        assert 0.0 <= val <= 1.0, f"Afinidad fuera de rango en {par}: {val}"


def test_afinidad_simetrica(historiales):
    """La afinidad debe ser simétrica: (i,j) == (j,i) por construcción."""
    res = construir_afinidad(historiales)
    for (i, j), val in res["afinidad"].items():
        assert i <= j, f"Par no canónico encontrado: ({i},{j})"
    # No hay duplicados (i,j) y (j,i) — están todos en forma min,max
    claves = list(res["afinidad"].keys())
    assert len(claves) == len(set(claves))


def test_recencia_pesa_mas(historiales):
    """
    Un par que aparece solo en el historial más reciente debe superar
    a un par que aparece solo en el más antiguo.
    """
    # Crear dos historiales: uno con par (1,2), otro (más reciente) con par (3,4)
    hist_antiguo = {
        "nombre": "antiguo",
        "confirmada": False,
        "fecha_inicio": "2023-01-01",
        "cargado_en": "2023-01-01T00:00:00",
        "filas": [
            {"id_sucursal": 1, "vehiculo": "VX", "dia_semana": "LUNES",
             "secuencia_visita": 1, "kg_entrega": 300},
            {"id_sucursal": 2, "vehiculo": "VX", "dia_semana": "LUNES",
             "secuencia_visita": 2, "kg_entrega": 300},
        ],
    }
    hist_reciente = {
        "nombre": "reciente",
        "confirmada": False,
        "fecha_inicio": "2025-06-01",
        "cargado_en": "2025-06-01T00:00:00",
        "filas": [
            {"id_sucursal": 3, "vehiculo": "VX", "dia_semana": "LUNES",
             "secuencia_visita": 1, "kg_entrega": 300},
            {"id_sucursal": 4, "vehiculo": "VX", "dia_semana": "LUNES",
             "secuencia_visita": 2, "kg_entrega": 300},
        ],
    }
    res = construir_afinidad([hist_antiguo, hist_reciente])
    af_raw = res["afinidad_raw"]

    par_antiguo  = (1, 2)
    par_reciente = (3, 4)

    assert par_antiguo  in af_raw, "Par antiguo debe aparecer en afinidad_raw"
    assert par_reciente in af_raw, "Par reciente debe aparecer en afinidad_raw"
    assert af_raw[par_reciente] > af_raw[par_antiguo], \
        f"Par reciente ({af_raw[par_reciente]}) debe superar al antiguo ({af_raw[par_antiguo]})"


def test_kg_promedio_calculado(historiales):
    """kg_promedio debe existir para todas las sucursales vistas."""
    res = construir_afinidad(historiales)
    for sid in list(CLUSTER_A.keys()) + list(CLUSTER_B.keys()) + list(CLUSTER_C.keys()):
        assert sid in res["kg_promedio"], f"Falta kg_promedio para {sid}"
        assert res["kg_promedio"][sid] > 0


def test_pref_vehiculo_dia_coherente(historiales):
    """La preferencia debe apuntar al vehículo/día correcto del patrón canónico."""
    from tests.datos_sinteticos import PATRON_CANONICAL
    res = construir_afinidad(historiales)
    pref = res["pref_vehiculo_dia"]

    for sid, (veh_esp, dia_esp) in PATRON_CANONICAL.items():
        if sid in pref:
            veh_pred, dia_pred = pref[sid]
            assert veh_pred == veh_esp, f"Sucursal {sid}: vehículo {veh_pred} ≠ {veh_esp}"
            assert dia_pred == dia_esp,  f"Sucursal {sid}: día {dia_pred} ≠ {dia_esp}"


def test_historiales_vacios():
    """Con historial vacío, la función no falla y devuelve estructura vacía."""
    res = construir_afinidad([])
    assert res["afinidad"] == {}
    assert res["kg_promedio"] == {}


def test_historial_sin_filas():
    """Historial con filas vacías no debe lanzar excepción."""
    hist = {"nombre": "vacio", "confirmada": False, "fecha_inicio": "2024-01-01", "filas": []}
    res = construir_afinidad([hist])
    assert res["afinidad"] == {}


def test_clusters_separados_baja_afinidad(historiales):
    """Nodos de clusters distintos deben tener afinidad 0 o muy baja."""
    res = construir_afinidad(historiales)
    af = res["afinidad"]
    # Nodo del cluster A con nodo del cluster B
    par_cruzado = (min(1, 11), max(1, 11))
    val = af.get(par_cruzado, 0.0)
    assert val == 0.0, f"Afinidad entre clusters distintos debería ser 0, fue {val}"
