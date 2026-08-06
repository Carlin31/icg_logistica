from logic.tiempo_reubicacion import evaluar_ruta_completa


CFG = {
    "activo": True,
    "depot": (18.87, -96.94),
    "velocidad": 35.0,
    "dias": {"martes": {"hora_salida": "07:00", "hora_limite": "20:00"}},
}


def test_evaluar_ruta_completa_usa_osrm_cuando_disponible():
    paradas = [
        {"latitud": 18.90, "longitud": -96.95, "peso_kg": 100, "_tipo": "sucursal"},
        {"latitud": 18.91, "longitud": -96.95, "peso_kg": 100, "_tipo": "sucursal"},
    ]

    def osrm_fake(pts):
        return {"tramos_min": [10.0, 10.0, 10.0]}

    out = evaluar_ruta_completa(paradas, "martes", CFG, osrm_fake)
    assert out[0]["hora_llegada_min"] == 430.0  # 07:00 (420) + 10
    assert out[0]["entregable_por_tiempo"] is True


def test_evaluar_ruta_completa_respaldo_haversine_si_osrm_falla():
    paradas = [{"latitud": 18.90, "longitud": -96.95, "peso_kg": 100, "_tipo": "sucursal"}]

    def osrm_roto(pts):
        raise RuntimeError("sin red")

    out = evaluar_ruta_completa(paradas, "martes", CFG, osrm_roto)
    assert out[0]["entregable_por_tiempo"] is True


def test_evaluar_ruta_completa_sin_funcion_osrm_usa_haversine():
    paradas = [{"latitud": 18.90, "longitud": -96.95, "peso_kg": 100, "_tipo": "sucursal"}]
    out = evaluar_ruta_completa(paradas, "martes", CFG, None)
    assert out[0]["entregable_por_tiempo"] is True


def test_evaluar_ruta_completa_detecta_fuera_de_horario():
    # Cierre a las 08:00 (480 min); con tramos de 100 min entre paradas, la
    # segunda llega bien pasado el cierre.
    cfg = {
        "activo": True, "depot": (18.87, -96.94), "velocidad": 35.0,
        "dias": {"martes": {"hora_salida": "07:00", "hora_limite": "08:00"}},
    }
    paradas = [
        {"latitud": 18.90, "longitud": -96.95, "peso_kg": 100, "_tipo": "sucursal"},
        {"latitud": 19.90, "longitud": -97.95, "peso_kg": 100, "_tipo": "sucursal"},
    ]
    out = evaluar_ruta_completa(paradas, "martes", cfg, None)
    assert out[1]["entregable_por_tiempo"] is False


import logic.historico_logic as historico_logic


def test_afinidad_historica_por_sucursal_compone_helpers(monkeypatch):
    historiales_falsos = [{"filas": [
        {"id_sucursal": 42, "vehiculo": "F 350_1", "dia_semana": "martes", "secuencia_visita": 1},
        {"id_sucursal": 42, "vehiculo": "F 350_1", "dia_semana": "martes", "secuencia_visita": 3},
    ]}]
    monkeypatch.setattr(historico_logic, "_historiales_crudos_sucursales", lambda: historiales_falsos)
    out = historico_logic.afinidad_historica_por_sucursal()
    assert out[42][("F 350_1", "MARTES")] == 2  # mediana de 1 y 3
