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


from logic.tiempo_reubicacion import _normalizar_veh, _pct_utilizacion, _cabe_por_peso


def test_normalizar_veh_ignora_espacios_y_mayusculas():
    assert _normalizar_veh("F 350_2") == _normalizar_veh("F350_2") == "F350_2"
    assert _normalizar_veh(None) == ""


def test_pct_utilizacion():
    assert _pct_utilizacion(1750, 3.5) == 50.0
    assert _pct_utilizacion(100, 0) == 0.0  # sin capacidad registrada -> 0, no división por cero
    assert _pct_utilizacion(100, None) == 0.0


def test_cabe_por_peso_respeta_umbral():
    ruta = {"peso_kg": 2000, "capacidad_ton": 2.5}  # 80% ya usado
    assert _cabe_por_peso(ruta, 100, 85.0) is True    # 2100/2500=84% <= 85%
    assert _cabe_por_peso(ruta, 200, 85.0) is False   # 2200/2500=88% > 85%


from logic.tiempo_reubicacion import (
    _paradas_ordenadas, _insertar_en_ruta, _quitar_de_ruta, _recalcular_peso_ruta,
)


def _ruta_ejemplo():
    return {
        "id": "R1", "dia": "martes", "vehiculo_abrev": "F 350_1",
        "capacidad_ton": 3.5, "peso_kg": 1000, "pct_utilizacion": 28.6,
        "sucursales": [
            {"num_tienda": 1, "nombre": "A", "orden": 1, "peso_kg": 500,
             "latitud": 18.90, "longitud": -96.95},
            {"num_tienda": 2, "nombre": "B", "orden": 2, "peso_kg": 500,
             "latitud": 18.91, "longitud": -96.95},
        ],
        "mayoristas": [],
    }


def test_paradas_ordenadas_combina_e_intercala_por_orden():
    ruta = _ruta_ejemplo()
    ruta["mayoristas"] = [{"id_cliente": 9, "documento": "BB1", "orden": 3,
                            "peso_kg": 50, "latitud": 18.92, "longitud": -96.95}]
    combinado = _paradas_ordenadas(ruta)
    assert [p.get("_tipo") for p in combinado] == ["sucursal", "sucursal", "mayorista"]


def test_insertar_en_ruta_posicion_geografica_y_reindexa():
    ruta = _ruta_ejemplo()
    nueva_sucursal = {"num_tienda": 3, "nombre": "C", "peso_kg": 300,
                       "latitud": 18.902, "longitud": -96.95}  # mas cerca de A(1) que de B(2), sin ambiguedad de float
    _insertar_en_ruta(ruta, nueva_sucursal, "sucursal")
    ordenes = [(s["num_tienda"], s["orden"]) for s in ruta["sucursales"]]
    assert ordenes == [(1, 1), (3, 2), (2, 3)]


def test_quitar_de_ruta_reindexa_lo_restante():
    ruta = _ruta_ejemplo()
    _quitar_de_ruta(ruta, {"num_tienda": 1}, "sucursal")
    assert [s["num_tienda"] for s in ruta["sucursales"]] == [2]
    assert ruta["sucursales"][0]["orden"] == 1


def test_recalcular_peso_ruta_suma_sucursales_y_mayoristas():
    ruta = _ruta_ejemplo()
    ruta["mayoristas"] = [{"id_cliente": 9, "documento": "BB1", "orden": 3,
                            "peso_kg": 50, "latitud": 18.92, "longitud": -96.95}]
    _recalcular_peso_ruta(ruta)
    assert ruta["peso_kg"] == 1050.0
    assert ruta["pct_utilizacion"] == _pct_utilizacion(1050.0, 3.5)
