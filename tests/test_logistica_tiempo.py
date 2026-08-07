"""Pruebas del modelo de tiempo de entrega (descarga por parada + llegadas)."""
from logic.logistica_tiempo import tiempo_descarga_min, evaluar_llegadas


def test_descarga_clamp_sucursal():
    assert tiempo_descarga_min(0, es_mayorista=False) == 38.0        # piso
    assert tiempo_descarga_min(100000, es_mayorista=False) == 90.0   # techo
    assert tiempo_descarga_min(600, es_mayorista=False) == 62.0      # 38 + 600*0.04


def test_descarga_clamp_mayorista():
    assert tiempo_descarga_min(0, es_mayorista=True) == 38.0         # piso
    assert tiempo_descarga_min(100000, es_mayorista=True) == 90.0    # techo


def test_llegadas_acumuladas():
    # salida 07:00 = 420 min; cierre 20:00 = 1200 min. 3 paradas peso 0 (descarga=piso 60).
    paradas = [{"nombre": "A", "peso_kg": 0}, {"nombre": "B", "peso_kg": 0}, {"nombre": "C", "peso_kg": 0}]
    tramos = [30, 30, 30, 30]  # el último (regreso) se ignora; descarga(peso 0)=piso 38
    out = evaluar_llegadas(paradas, tramos, 420, 1200)
    assert out[0]["hora_llegada_min"] == 450   # 420 + 30
    assert out[1]["hora_llegada_min"] == 518   # 450 + 38(desc A) + 30
    assert out[2]["hora_llegada_min"] == 586   # 518 + 38(desc B) + 30
    assert all(p["entregable_por_tiempo"] for p in out)


def test_deteccion_no_entregable():
    paradas = [{"peso_kg": 0}, {"peso_kg": 0}]
    tramos = [30, 60]
    out = evaluar_llegadas(paradas, tramos, 420, 500)   # cierre temprano 500
    assert out[0]["entregable_por_tiempo"] is True      # llega 450 <= 500
    assert out[1]["entregable_por_tiempo"] is False     # llega 570 > 500


def test_determinismo():
    paradas = [{"peso_kg": 100}, {"peso_kg": 200, "es_mayorista": True}]
    tramos = [10, 20, 30]
    assert evaluar_llegadas(paradas, tramos, 420, 1200) == evaluar_llegadas(paradas, tramos, 420, 1200)


from logic.logistica_tiempo import hhmm_a_min, evaluar_ruta_por_tiempo


def test_hhmm_a_min():
    assert hhmm_a_min("07:00") == 420
    assert hhmm_a_min("20:00") == 1200
    assert hhmm_a_min("basura", default=99) == 99


def test_evaluar_ruta_por_tiempo_marca_tarde():
    depot = (18.87, -96.95)
    # dos paradas muy cercanas (traslado ~minutos); descarga(peso 0)=40 domina.
    paradas = [{"latitud": 18.88, "longitud": -96.95, "peso_kg": 0},
               {"latitud": 18.89, "longitud": -96.95, "peso_kg": 0}]
    out = evaluar_ruta_por_tiempo(paradas, depot, 420, 450, velocidad_kmh=35)
    assert out[0]["entregable_por_tiempo"] is True    # llega ~422 <= 450
    assert out[1]["entregable_por_tiempo"] is False   # ~422 + 40(desc) + viaje > 450


def test_velocidad_por_tramo_calibrada_dos_regimenes():
    """
    La velocidad haversine-equivalente no es constante: medida contra OSRM sobre
    875 tramos reales da ~55.5 km/h en tramos largos (depot→clúster) y ~37.8 en
    cortos. Una sola constante de 35 infla los largos ~2.5 h y dispara
    violaciones de tiempo falsas.
    """
    from logic.logistica_tiempo import velocidad_para_km
    assert velocidad_para_km(200) == 55.5      # largo
    assert velocidad_para_km(10) == 37.8       # corto
    assert velocidad_para_km(50) == 37.8       # el umbral pertenece a "corto"
    assert velocidad_para_km(51) == 55.5


def test_evaluar_ruta_usa_velocidad_por_tramo_si_se_pide():
    from logic.logistica_tiempo import evaluar_ruta_por_tiempo
    depot = (18.87, -96.95)
    # una parada a ~200 km: con 35 km/h son ~343 min; con el régimen largo ~216
    paradas = [{"latitud": 18.44, "longitud": -95.21, "peso_kg": 0}]
    fijo = evaluar_ruta_por_tiempo(paradas, depot, 420, 1200, velocidad_kmh=35.0)
    calib = evaluar_ruta_por_tiempo(paradas, depot, 420, 1200, por_tramo=True)
    assert calib[0]["hora_llegada_min"] < fijo[0]["hora_llegada_min"] - 60


def test_evaluar_ruta_sin_coords_no_rompe():
    depot = (18.87, -96.95)
    paradas = [{"peso_kg": 100}, {"latitud": 18.88, "longitud": -96.95, "peso_kg": 0}]
    out = evaluar_ruta_por_tiempo(paradas, depot, 420, 1200, velocidad_kmh=35)
    assert len(out) == 2
    assert all("entregable_por_tiempo" in p for p in out)
