"""Pruebas del modelo de tiempo de entrega (descarga por parada + llegadas)."""
from logic.logistica_tiempo import tiempo_descarga_min, evaluar_llegadas


def test_descarga_clamp_sucursal():
    assert tiempo_descarga_min(0, es_mayorista=False) == 40.0        # piso
    assert tiempo_descarga_min(100000, es_mayorista=False) == 90.0   # techo
    assert tiempo_descarga_min(600, es_mayorista=False) == 70.0      # 40 + 600*0.05


def test_descarga_clamp_mayorista():
    assert tiempo_descarga_min(0, es_mayorista=True) == 40.0         # piso
    assert tiempo_descarga_min(100000, es_mayorista=True) == 90.0    # techo


def test_llegadas_acumuladas():
    # salida 07:00 = 420 min; cierre 20:00 = 1200 min. 3 paradas peso 0 (descarga=piso 60).
    paradas = [{"nombre": "A", "peso_kg": 0}, {"nombre": "B", "peso_kg": 0}, {"nombre": "C", "peso_kg": 0}]
    tramos = [30, 30, 30, 30]  # el último (regreso) se ignora; descarga(peso 0)=piso 40
    out = evaluar_llegadas(paradas, tramos, 420, 1200)
    assert out[0]["hora_llegada_min"] == 450   # 420 + 30
    assert out[1]["hora_llegada_min"] == 520   # 450 + 40(desc A) + 30
    assert out[2]["hora_llegada_min"] == 590   # 520 + 40(desc B) + 30
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
