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


from logic.vrp_afinidad.rebalanceo_geografico import rebalancear_por_geografia


def _sids(grupo):
    return sorted(m["sid"] for m in grupo)


# Cluster V1 compacto en (18.90,-96.95); outlier 99 lejos, cerca de V2.
COORDS = {
    1:  (18.90, -96.95),
    2:  (18.91, -96.95),
    99: (18.30, -96.10),   # lejos de V1, cerca de V2
    3:  (18.31, -96.11),
}


def test_outlier_se_mueve_a_ruta_cercana():
    groups = {
        ("V1", "LUNES"): [{"sid": 1, "seq": 1}, {"sid": 2, "seq": 2}, {"sid": 99, "seq": 3}],
        ("V2", "LUNES"): [{"sid": 3, "seq": 1}],
    }
    pesos = {1: 100, 2: 100, 99: 100, 3: 100}
    vols  = {1: 1.0, 2: 1.0, 99: 1.0, 3: 1.0}
    out = rebalancear_por_geografia(
        groups, COORDS, pesos, vols, {"V1": 10000, "V2": 10000}, {"V1": 999, "V2": 999})
    assert 99 in _sids(out[("V2", "LUNES")])
    assert 99 not in _sids(out[("V1", "LUNES")])


def test_no_mueve_si_excede_peso():
    groups = {
        ("V1", "LUNES"): [{"sid": 1, "seq": 1}, {"sid": 2, "seq": 2}, {"sid": 99, "seq": 3}],
        ("V2", "LUNES"): [{"sid": 3, "seq": 1}],
    }
    # 99 pesa 200: no cabe en V2 (cap 150) ni por reubicación ni por intercambio
    # (un intercambio dejaría a 99 solo en V2, y 200>150 igual). Con 99 a 100kg
    # como en el resto de las pruebas, un intercambio 99<->3 sí sería viable
    # (V2 quedaría con 1 solo elemento de 100kg) y mejoraría la geografía —
    # desviación deliberada del plan para que la prueba verifique el bloqueo
    # de capacidad también frente al intercambio, no solo a la reubicación.
    pesos = {1: 100, 2: 100, 99: 200, 3: 100}
    vols  = {1: 1.0, 2: 1.0, 99: 1.0, 3: 1.0}
    out = rebalancear_por_geografia(
        groups, COORDS, pesos, vols, {"V1": 10000, "V2": 150}, {"V1": 999, "V2": 999})
    assert 99 in _sids(out[("V1", "LUNES")])


def test_no_mueve_si_excede_volumen():
    groups = {
        ("V1", "LUNES"): [{"sid": 1, "seq": 1}, {"sid": 2, "seq": 2}, {"sid": 99, "seq": 3}],
        ("V2", "LUNES"): [{"sid": 3, "seq": 1}],
    }
    # 99 ocupa 3.0 m3: no cabe en V2 (cap 1.5) ni por reubicación ni por
    # intercambio (mismo razonamiento que en test_no_mueve_si_excede_peso).
    pesos = {1: 100, 2: 100, 99: 100, 3: 100}
    vols  = {1: 1.0, 2: 1.0, 99: 3.0, 3: 1.0}
    out = rebalancear_por_geografia(
        groups, COORDS, pesos, vols, {"V1": 10000, "V2": 10000}, {"V1": 999, "V2": 1.5})
    assert 99 in _sids(out[("V1", "LUNES")])


def test_intercambio_cuando_ambas_llenas():
    # A y B llenas (200 c/u). Un swap 1-a-1 mantiene el peso y compacta.
    coords = {
        11: (0.0, 0.0), 12: (0.0, 10.0),     # A: un punto lejos
        21: (0.0, 10.1), 22: (0.0, 0.1),     # B: un punto lejos (cruzado)
    }
    groups = {
        ("A", "LUNES"): [{"sid": 11, "seq": 1}, {"sid": 12, "seq": 2}],
        ("B", "LUNES"): [{"sid": 21, "seq": 1}, {"sid": 22, "seq": 2}],
    }
    pesos = {11: 100, 12: 100, 21: 100, 22: 100}
    vols  = {11: 1.0, 12: 1.0, 21: 1.0, 22: 1.0}
    out = rebalancear_por_geografia(
        groups, coords, pesos, vols, {"A": 200, "B": 200}, {"A": 999, "B": 999})
    a = _sids(out[("A", "LUNES")])
    b = _sids(out[("B", "LUNES")])
    # Tras el swap, cada ruta agrupa los dos puntos mutuamente cercanos:
    # {11,22} (cerca de lat/lon 0) y {12,21} (cerca de lat/lon 10). El problema
    # es simétrico (ambos swaps cruzados cuestan exactamente lo mismo), así que
    # cuál de las dos rutas (A o B) termina con cada par es un empate legítimo
    # que depende del orden de iteración interno — desviación deliberada del
    # plan: se verifica el particionado, no la etiqueta de ruta específica.
    assert {frozenset(a), frozenset(b)} == {frozenset([11, 22]), frozenset([12, 21])}


def test_sucursal_sin_coords_se_queda_fija():
    groups = {
        ("V1", "LUNES"): [{"sid": 1, "seq": 1}, {"sid": 2, "seq": 2}, {"sid": 77, "seq": 3}],
        ("V2", "LUNES"): [{"sid": 3, "seq": 1}],
    }
    # 77 no tiene coordenadas
    coords = {1: (18.90, -96.95), 2: (18.91, -96.95), 3: (18.31, -96.11)}
    pesos = {1: 100, 2: 100, 77: 100, 3: 100}
    vols  = {1: 1.0, 2: 1.0, 77: 1.0, 3: 1.0}
    out = rebalancear_por_geografia(
        groups, coords, pesos, vols, {"V1": 10000, "V2": 10000}, {"V1": 999, "V2": 999})
    assert 77 in _sids(out[("V1", "LUNES")])


def test_invariantes_y_determinismo():
    groups = {
        ("V1", "LUNES"): [{"sid": 1, "seq": 1}, {"sid": 2, "seq": 2}, {"sid": 99, "seq": 3}],
        ("V2", "LUNES"): [{"sid": 3, "seq": 1}],
    }
    pesos = {1: 100, 2: 100, 99: 100, 3: 100}
    vols  = {1: 1.0, 2: 1.0, 99: 1.0, 3: 1.0}
    caps_p, caps_v = {"V1": 10000, "V2": 10000}, {"V1": 999, "V2": 999}
    out1 = rebalancear_por_geografia(groups, COORDS, pesos, vols, caps_p, caps_v)
    out2 = rebalancear_por_geografia(out1, COORDS, pesos, vols, caps_p, caps_v)
    # Mismo número de rutas y mismos días
    assert set(out1.keys()) == {("V1", "LUNES"), ("V2", "LUNES")}
    # Mismo conjunto total de sucursales (nada se pierde ni duplica)
    todas = sorted(m["sid"] for g in out1.values() for m in g)
    assert todas == [1, 2, 3, 99]
    # Ninguna ruta queda vacía
    assert all(len(g) >= 1 for g in out1.values())
    # Idempotente: correr de nuevo no cambia nada
    assert {k: _sids(v) for k, v in out2.items()} == {k: _sids(v) for k, v in out1.items()}
