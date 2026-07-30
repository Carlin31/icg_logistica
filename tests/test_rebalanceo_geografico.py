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
