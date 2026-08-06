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


from logic.tiempo_reubicacion import (
    _clave_afinidad_para, _candidatas_con_afinidad, _mejor_candidata, _menos_mala,
)

CFG_AMPLIO = {
    "activo": True, "depot": (18.87, -96.94), "velocidad": 35.0,
    "dias": {
        "martes": {"hora_salida": "07:00", "hora_limite": "20:00"},
        "jueves": {"hora_salida": "07:00", "hora_limite": "20:00"},
    },
}

AFINIDAD = {42: {("F 350_1", "MARTES"): 1, ("F 350_2", "JUEVES"): 2}}


def test_clave_afinidad_para_sucursal_es_su_num_tienda():
    parada = {"num_tienda": 42}
    assert _clave_afinidad_para(parada, "sucursal", {"sucursales": []}, AFINIDAD) == 42


def test_clave_afinidad_para_mayorista_ancla_a_sucursal_cercana_con_afinidad():
    ruta = {"sucursales": [
        {"num_tienda": 42, "latitud": 18.90, "longitud": -96.95},
        {"num_tienda": 99, "latitud": 0.0, "longitud": 0.0},  # sin afinidad, lejos
    ]}
    mayorista = {"id_cliente": 7, "latitud": 18.901, "longitud": -96.951}
    assert _clave_afinidad_para(mayorista, "mayorista", ruta, AFINIDAD) == 42


def test_clave_afinidad_para_mayorista_sin_coords_es_none():
    assert _clave_afinidad_para({"id_cliente": 7}, "mayorista", {"sucursales": []}, AFINIDAD) is None


def test_candidatas_con_afinidad_filtra_por_dia_y_afinidad():
    rutas = [
        {"id": "R_ORIGEN", "dia": "martes",  "vehiculo_abrev": "F 350_1"},
        {"id": "R_MISMO_DIA_AFIN", "dia": "martes", "vehiculo_abrev": "F 350_1"},
        {"id": "R_OTRO_VEH", "dia": "martes", "vehiculo_abrev": "F 350_9"},
        {"id": "R_OTRO_DIA_AFIN", "dia": "jueves", "vehiculo_abrev": "F350_2"},
    ]
    mismo_dia = _candidatas_con_afinidad(42, rutas, AFINIDAD, "R_ORIGEN", True, "martes")
    assert [r["id"] for r in mismo_dia] == ["R_MISMO_DIA_AFIN"]

    otro_dia = _candidatas_con_afinidad(42, rutas, AFINIDAD, "R_ORIGEN", False, "martes")
    assert [r["id"] for r in otro_dia] == ["R_OTRO_DIA_AFIN"]  # 'F350_2' normaliza igual que 'F 350_2'


# El peso de "Vecina" debe IGUALAR peso_kg (no un valor fijo aparte):
# _menos_mala compara vía _simular_insercion -> _recalcular_peso_ruta,
# que sobreescribe peso_kg sumando las paradas reales. Si "Vecina" fuera
# un peso fijo distinto del peso_kg declarado, la simulacion perderia la
# diferencia entre rutas y el test de _menos_mala compararia valores
# identicos sin importar el peso_kg pedido.
def _ruta_destino(id_, dia="martes", peso_kg=0, capacidad_ton=3.5):
    return {
        "id": id_, "dia": dia, "vehiculo_abrev": "F 350_1",
        "capacidad_ton": capacidad_ton, "peso_kg": peso_kg,
        "pct_utilizacion": _pct_utilizacion(peso_kg, capacidad_ton),
        "sucursales": [
            {"num_tienda": 5, "nombre": "Vecina", "orden": 1, "peso_kg": peso_kg,
             "latitud": 18.90, "longitud": -96.95},
        ],
        "mayoristas": [],
    }


def test_mejor_candidata_respeta_umbral_y_tiempo():
    parada = {"num_tienda": 42, "nombre": "Nueva", "peso_kg": 100,
              "latitud": 18.901, "longitud": -96.951}
    llena  = _ruta_destino("LLENA", peso_kg=3300)   # 3400/3500=97% > 85%
    libre  = _ruta_destino("LIBRE", peso_kg=1000)   # 1100/3500=31% <= 85%
    elegida = _mejor_candidata([llena, libre], parada, "sucursal", 100.0,
                               CFG_AMPLIO, None, 85.0)
    assert elegida["id"] == "LIBRE"


def test_mejor_candidata_none_si_ninguna_cumple():
    parada = {"num_tienda": 42, "peso_kg": 100, "latitud": 18.901, "longitud": -96.951}
    llena = _ruta_destino("LLENA", peso_kg=3300)
    assert _mejor_candidata([llena], parada, "sucursal", 100.0, CFG_AMPLIO, None, 85.0) is None


def test_menos_mala_elige_menor_pct_resultante():
    parada = {"num_tienda": 42, "peso_kg": 100, "latitud": 18.901, "longitud": -96.951}
    mas_llena  = _ruta_destino("MAS_LLENA", peso_kg=3300)
    menos_llena = _ruta_destino("MENOS_LLENA", peso_kg=3000)
    elegida = _menos_mala([mas_llena, menos_llena], parada, "sucursal", CFG_AMPLIO, None)
    assert elegida["id"] == "MENOS_LLENA"


def test_menos_mala_none_si_no_hay_candidatas():
    assert _menos_mala([], {}, "sucursal", CFG_AMPLIO, None) is None


from logic.tiempo_reubicacion import resolver_fuera_de_horario


def _cfg_cierre_08_30():
    # Salida 07:00 (420 min), cierre 08:30 (510 min): 90 min de presupuesto.
    # Con estas coordenadas y 60 km/h, alcanza para llegar a (0.5,0.5)
    # DIRECTO desde el depot (~78.6 km, ~78.6 min => llega a 498.6, cabe),
    # pero NO alcanza si antes hay que pasar por (0.05,0.05) + su descarga
    # (42 min): la llegada a (0.5,0.5) cae en ~540.6 min, fuera de horario.
    return {
        "activo": True, "depot": (0.0, 0.0), "velocidad": 60.0,
        "dias": {
            "martes": {"hora_salida": "07:00", "hora_limite": "08:30"},
            "jueves": {"hora_salida": "07:00", "hora_limite": "08:30"},
        },
    }


def test_resolver_fuera_de_horario_mueve_a_ruta_con_afinidad_y_cupo():
    # 42 está lejos del depot. La ruta ORIGEN visita antes una sucursal
    # cercana (con su descarga) y llega tarde a 42. La ruta DESTINO (vacía,
    # mismo día, con afinidad histórica para 42) llega a tiempo yendo directo.
    afinidad = {42: {("DEST", "MARTES"): 1}}
    origen = {
        "id": "ORIGEN", "dia": "martes", "vehiculo_abrev": "ORIGEN",
        "capacidad_ton": 3.5, "peso_kg": 0, "pct_utilizacion": 0.0,
        "sucursales": [
            {"num_tienda": 1, "nombre": "Cercana", "orden": 1, "peso_kg": 100,
             "latitud": 0.05, "longitud": 0.05},
            {"num_tienda": 42, "nombre": "Lejana", "orden": 2, "peso_kg": 100,
             "latitud": 0.5, "longitud": 0.5},
        ],
        "mayoristas": [],
    }
    destino = {
        "id": "DEST", "dia": "martes", "vehiculo_abrev": "DEST",
        "capacidad_ton": 3.5, "peso_kg": 0, "pct_utilizacion": 0.0,
        "sucursales": [], "mayoristas": [],
    }
    rutas = [origen, destino]

    movio = resolver_fuera_de_horario(rutas, _cfg_cierre_08_30(), afinidad,
                                      consultar_osrm_fn=None)

    assert movio is True
    assert [s["num_tienda"] for s in origen["sucursales"]] == [1]
    assert 42 in [s["num_tienda"] for s in destino["sucursales"]]
    assert destino["peso_kg"] == 100.0


def test_resolver_fuera_de_horario_sin_historial_no_mueve_nada():
    ruta = {
        "id": "ORIGEN", "dia": "martes", "vehiculo_abrev": "ORIGEN",
        "capacidad_ton": 3.5, "peso_kg": 0, "pct_utilizacion": 0.0,
        "sucursales": [
            {"num_tienda": 1, "nombre": "Cercana", "orden": 1, "peso_kg": 100,
             "latitud": 0.05, "longitud": 0.05},
            {"num_tienda": 999, "nombre": "Sin historial", "orden": 2, "peso_kg": 100,
             "latitud": 0.5, "longitud": 0.5},
        ],
        "mayoristas": [],
    }
    movio = resolver_fuera_de_horario([ruta], _cfg_cierre_08_30(), {}, consultar_osrm_fn=None)
    assert movio is False
    assert [s["num_tienda"] for s in ruta["sucursales"]] == [1, 999]


def test_resolver_fuera_de_horario_interruptor_apagado_no_hace_nada():
    ruta = {"id": "R1", "dia": "martes", "sucursales": [], "mayoristas": []}
    assert resolver_fuera_de_horario([ruta], {"activo": False}, {}) is False


def test_resolver_fuera_de_horario_mismo_dia_falla_cae_a_otro_dia():
    # SAMEDIA (mismo dia que ORIGEN) tiene afinidad para 42 pero ya esta casi
    # llena (3400/3500=97%): tras sumar los 100kg de 42 quedaria en 100%,
    # por lo que _mejor_candidata la descarta por peso sin llegar a evaluar
    # tiempo. OTRODIA (jueves) tiene afinidad para 42, esta vacia, y llega a
    # tiempo yendo directo desde el depot (mismo calculo que el test anterior:
    # depot->(0.5,0.5) = 78.63 min, llegada 498.63 <= 510).
    afinidad = {42: {("SAMEDIA", "MARTES"): 1, ("OTRODIA", "JUEVES"): 1}}
    origen = {
        "id": "ORIGEN", "dia": "martes", "vehiculo_abrev": "ORIGEN",
        "capacidad_ton": 3.5, "peso_kg": 0, "pct_utilizacion": 0.0,
        "sucursales": [
            {"num_tienda": 1, "nombre": "Cercana", "orden": 1, "peso_kg": 100,
             "latitud": 0.05, "longitud": 0.05},
            {"num_tienda": 42, "nombre": "Lejana", "orden": 2, "peso_kg": 100,
             "latitud": 0.5, "longitud": 0.5},
        ],
        "mayoristas": [],
    }
    samedia = {
        "id": "SAMEDIA", "dia": "martes", "vehiculo_abrev": "SAMEDIA",
        "capacidad_ton": 3.5, "peso_kg": 3400, "pct_utilizacion": 97.1,
        "sucursales": [
            {"num_tienda": 50, "nombre": "Llena", "orden": 1, "peso_kg": 3400,
             "latitud": 0.05, "longitud": 0.05},
        ],
        "mayoristas": [],
    }
    otrodia = {
        "id": "OTRODIA", "dia": "jueves", "vehiculo_abrev": "OTRODIA",
        "capacidad_ton": 3.5, "peso_kg": 0, "pct_utilizacion": 0.0,
        "sucursales": [], "mayoristas": [],
    }
    rutas = [origen, samedia, otrodia]

    movio = resolver_fuera_de_horario(rutas, _cfg_cierre_08_30(), afinidad,
                                      consultar_osrm_fn=None)

    assert movio is True
    assert [s["num_tienda"] for s in origen["sucursales"]] == [1]
    assert [s["num_tienda"] for s in samedia["sucursales"]] == [50]  # sin tocar, descartada por cupo
    assert 42 in [s["num_tienda"] for s in otrodia["sucursales"]]


def test_resolver_fuera_de_horario_procesa_varias_paradas_en_la_misma_ruta():
    # ORIGEN tiene DOS paradas fuera de horario (42 y 43). Cada una tiene
    # afinidad con una ruta destino distinta, vacia, del mismo dia. Deben
    # resolverse una por una (re-evaluando ORIGEN tras cada movimiento), sin
    # detenerse en la primera.
    afinidad = {
        42: {("DEST1", "MARTES"): 1},
        43: {("DEST2", "MARTES"): 1},
    }
    origen = {
        "id": "ORIGEN", "dia": "martes", "vehiculo_abrev": "ORIGEN",
        "capacidad_ton": 3.5, "peso_kg": 0, "pct_utilizacion": 0.0,
        "sucursales": [
            {"num_tienda": 1, "nombre": "Cercana", "orden": 1, "peso_kg": 100,
             "latitud": 0.05, "longitud": 0.05},
            {"num_tienda": 42, "nombre": "Lejana1", "orden": 2, "peso_kg": 100,
             "latitud": 0.5, "longitud": 0.5},
            {"num_tienda": 43, "nombre": "Lejana2", "orden": 3, "peso_kg": 100,
             "latitud": 0.45, "longitud": 0.45},
        ],
        "mayoristas": [],
    }
    dest1 = {
        "id": "DEST1", "dia": "martes", "vehiculo_abrev": "DEST1",
        "capacidad_ton": 3.5, "peso_kg": 0, "pct_utilizacion": 0.0,
        "sucursales": [], "mayoristas": [],
    }
    dest2 = {
        "id": "DEST2", "dia": "martes", "vehiculo_abrev": "DEST2",
        "capacidad_ton": 3.5, "peso_kg": 0, "pct_utilizacion": 0.0,
        "sucursales": [], "mayoristas": [],
    }
    rutas = [origen, dest1, dest2]

    movio = resolver_fuera_de_horario(rutas, _cfg_cierre_08_30(), afinidad,
                                      consultar_osrm_fn=None)

    assert movio is True
    assert [s["num_tienda"] for s in origen["sucursales"]] == [1]
    assert [s["num_tienda"] for s in dest1["sucursales"]] == [42]
    assert [s["num_tienda"] for s in dest2["sucursales"]] == [43]
