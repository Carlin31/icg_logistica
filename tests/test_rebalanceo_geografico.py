from logic.vrp_afinidad.rebalanceo_geografico import (
    _haversine, _costo_ruta,
)


def test_costo_ruta_un_punto_es_cero():
    # Una sola sucursal no tiene dispersión.
    coords = {1: (18.90, -96.95)}
    assert _costo_ruta([1], coords) == 0.0


def test_costo_ruta_crece_con_dispersion():
    coords = {1: (18.90, -96.95), 2: (18.91, -96.95), 99: (18.30, -96.10)}
    compacto = _costo_ruta([1, 2], coords)
    disperso = _costo_ruta([1, 2, 99], coords)
    assert disperso > compacto > 0


from logic.vrp_afinidad.rebalanceo_geografico import _cabe


def test_cabe_respeta_peso_y_volumen():
    pesos = {1: 100, 2: 100}
    vols  = {1: 1.0, 2: 1.0}
    # cabe: peso 200<=200 y vol 2.0<=3.0
    assert _cabe([1, 2], "V", pesos, vols, {"V": 200}, {"V": 3.0}) is True
    # no cabe por peso: 200>150
    assert _cabe([1, 2], "V", pesos, vols, {"V": 150}, {"V": 3.0}) is False
    # no cabe por volumen: 2.0>1.5
    assert _cabe([1, 2], "V", pesos, vols, {"V": 200}, {"V": 1.5}) is False


def test_cabe_sin_capacidad_definida_no_bloquea():
    # Vehículo sin capacidad registrada -> no impone límite (inf).
    assert _cabe([1], "DESCONOCIDO", {1: 999999}, {1: 999999}, {}, {}) is True
