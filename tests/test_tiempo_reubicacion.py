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


from logic.tiempo_reubicacion import _grupo_para

CFG_AMPLIO = {
    "activo": True, "depot": (18.87, -96.94), "velocidad": 35.0,
    "dias": {
        "martes": {"hora_salida": "07:00", "hora_limite": "20:00"},
        "jueves": {"hora_salida": "07:00", "hora_limite": "20:00"},
    },
}

GRUPO_42 = {"grupo": 1, "rigidez": "FLEXIBLE", "sucursales": [42],
            "unidades_afines": "F 350_1:1 | F 350_2:2", "dias_admisibles": ["MARTES", "JUEVES"],
            "dia_preferido": "JUEVES"}
INDICE_GRUPOS = {42: GRUPO_42}


def test_grupo_para_sucursal_es_su_grupo_directo():
    parada = {"num_tienda": 42}
    assert _grupo_para(parada, "sucursal", {"sucursales": []}, INDICE_GRUPOS) is GRUPO_42


def test_grupo_para_sucursal_sin_grupo_es_none():
    parada = {"num_tienda": 999}
    assert _grupo_para(parada, "sucursal", {"sucursales": []}, INDICE_GRUPOS) is None


def test_grupo_para_mayorista_ancla_a_sucursal_cercana_con_grupo():
    ruta = {"sucursales": [
        {"num_tienda": 42, "latitud": 18.90, "longitud": -96.95},
        {"num_tienda": 99, "latitud": 0.0, "longitud": 0.0},  # sin grupo, lejos
    ]}
    mayorista = {"id_cliente": 7, "latitud": 18.901, "longitud": -96.951}
    assert _grupo_para(mayorista, "mayorista", ruta, INDICE_GRUPOS) is GRUPO_42


def test_grupo_para_mayorista_sin_coords_es_none():
    assert _grupo_para({"id_cliente": 7}, "mayorista", {"sucursales": []}, INDICE_GRUPOS) is None


from logic.tiempo_reubicacion import _rutas_candidatas_por_grupo


def test_rutas_candidatas_por_grupo_ordena_por_frecuencia_descendente():
    grupo = {"unidades_afines": "T 23:3 | K 16:2 | T 25:1", "dias_admisibles": ["MARTES"]}
    rutas = [
        {"id": "R_ORIGEN", "dia": "martes", "vehiculo_abrev": "T 23"},
        {"id": "R_K16", "dia": "martes", "vehiculo_abrev": "K 16"},
        {"id": "R_T25", "dia": "martes", "vehiculo_abrev": "T 25"},
        {"id": "R_SIN_AFINIDAD", "dia": "martes", "vehiculo_abrev": "T 99"},
    ]
    candidatas = _rutas_candidatas_por_grupo(grupo, rutas, "R_ORIGEN")
    assert [r["id"] for r in candidatas] == ["R_K16", "R_T25"]


def test_rutas_candidatas_por_grupo_incluye_mismo_vehiculo_en_otro_dia_admisible():
    # Solo se excluye la ruta de origen EXACTA (mismo vehiculo Y mismo dia).
    # El mismo vehiculo en OTRO dia admisible (T 23 jueves) SI es candidata
    # valida -- un grupo flexible puede operar varios dias, y forzar un
    # vehiculo distinto solo por coincidir en vehiculo con el origen
    # tiraria al vehiculo dominante por una coincidencia de calendario.
    grupo = {"unidades_afines": "T 23:5", "dias_admisibles": ["MARTES", "JUEVES"]}
    rutas = [
        {"id": "R_ORIGEN", "dia": "martes", "vehiculo_abrev": "T 23"},
        {"id": "R_T23_JUEVES", "dia": "jueves", "vehiculo_abrev": "T 23"},
    ]
    candidatas = _rutas_candidatas_por_grupo(grupo, rutas, "R_ORIGEN")
    assert [r["id"] for r in candidatas] == ["R_T23_JUEVES"]


def test_rutas_candidatas_por_grupo_excluye_solo_la_ruta_de_origen_exacta():
    # Mismo vehiculo Y mismo dia que origen (o sea, la ruta origen misma,
    # aunque aparezca de nuevo en la lista por error) nunca se auto-elige.
    grupo = {"unidades_afines": "T 23:5", "dias_admisibles": ["MARTES"]}
    rutas = [{"id": "R_ORIGEN", "dia": "martes", "vehiculo_abrev": "T 23"}]
    assert _rutas_candidatas_por_grupo(grupo, rutas, "R_ORIGEN") == []


def test_rutas_candidatas_por_grupo_dia_admisible_en_orden_preferido_primero():
    grupo = {"unidades_afines": "F 350_1:7", "dias_admisibles": ["MARTES", "JUEVES"]}
    rutas = [
        {"id": "R_ORIGEN", "dia": "martes", "vehiculo_abrev": "OTRO"},
        {"id": "R_F350_1_JUEVES", "dia": "jueves", "vehiculo_abrev": "F 350_1"},
        {"id": "R_F350_1_MARTES", "dia": "martes", "vehiculo_abrev": "F 350_1"},
    ]
    candidatas = _rutas_candidatas_por_grupo(grupo, rutas, "R_ORIGEN")
    assert [r["id"] for r in candidatas] == ["R_F350_1_MARTES", "R_F350_1_JUEVES"]


def test_rutas_candidatas_por_grupo_sin_ruta_real_para_ese_vehiculo_dia():
    grupo = {"unidades_afines": "T 20:1", "dias_admisibles": ["VIERNES"]}
    rutas = [{"id": "R_ORIGEN", "dia": "lunes", "vehiculo_abrev": "T 23"}]
    assert _rutas_candidatas_por_grupo(grupo, rutas, "R_ORIGEN") == []


from logic.tiempo_reubicacion import _mejor_candidata_grupo, _menos_mala_grupo


# El peso de "Vecina" debe IGUALAR peso_kg (no un valor fijo aparte):
# _menos_mala_grupo compara vía _simular_insercion_conjunto -> _recalcular_peso_ruta,
# que sobreescribe peso_kg sumando las paradas reales.
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


def test_mejor_candidata_grupo_respeta_umbral_y_tiempo():
    conjunto = [{"num_tienda": 42, "nombre": "Nueva", "peso_kg": 100,
                 "latitud": 18.901, "longitud": -96.951}]
    llena  = _ruta_destino("LLENA", peso_kg=3300)   # 3400/3500=97% > 85%
    libre  = _ruta_destino("LIBRE", peso_kg=1000)   # 1100/3500=31% <= 85%
    elegida = _mejor_candidata_grupo([llena, libre], conjunto, "sucursal", 100.0,
                                     CFG_AMPLIO, None, 85.0)
    assert elegida["id"] == "LIBRE"


def test_mejor_candidata_grupo_none_si_ninguna_cumple():
    conjunto = [{"num_tienda": 42, "peso_kg": 100, "latitud": 18.901, "longitud": -96.951}]
    llena = _ruta_destino("LLENA", peso_kg=3300)
    assert _mejor_candidata_grupo([llena], conjunto, "sucursal", 100.0, CFG_AMPLIO, None, 85.0) is None


def test_mejor_candidata_grupo_evalua_el_peso_total_del_conjunto():
    # Dos paradas de 500 kg cada una (grupo rigido): 1000 kg extra.
    conjunto = [
        {"num_tienda": 42, "peso_kg": 500, "latitud": 18.901, "longitud": -96.951},
        {"num_tienda": 43, "peso_kg": 500, "latitud": 18.902, "longitud": -96.952},
    ]
    # 1000 + 1000 = 2000/3500 = 57% <= 85% -> cabe.
    libre = _ruta_destino("LIBRE", peso_kg=1000)
    elegida = _mejor_candidata_grupo([libre], conjunto, "sucursal", 1000.0, CFG_AMPLIO, None, 85.0)
    assert elegida["id"] == "LIBRE"
    # 3000 + 1000 = 4000/3500 = 114% > 85% -> no cabe con las dos.
    llena = _ruta_destino("LLENA", peso_kg=3000)
    assert _mejor_candidata_grupo([llena], conjunto, "sucursal", 1000.0, CFG_AMPLIO, None, 85.0) is None


def test_menos_mala_grupo_elige_menor_pct_resultante():
    conjunto = [{"num_tienda": 42, "peso_kg": 100, "latitud": 18.901, "longitud": -96.951}]
    mas_llena  = _ruta_destino("MAS_LLENA", peso_kg=3300)
    menos_llena = _ruta_destino("MENOS_LLENA", peso_kg=3000)
    elegida = _menos_mala_grupo([mas_llena, menos_llena], conjunto, "sucursal", CFG_AMPLIO, None)
    assert elegida["id"] == "MENOS_LLENA"


def test_menos_mala_grupo_none_si_no_hay_candidatas():
    assert _menos_mala_grupo([], [{}], "sucursal", CFG_AMPLIO, None) is None


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
            "lunes":  {"hora_salida": "07:00", "hora_limite": "08:30"},
        },
    }


def _ruta_vacia(id_, dia, vehiculo):
    return {
        "id": id_, "dia": dia, "vehiculo_abrev": vehiculo,
        "capacidad_ton": 3.5, "peso_kg": 0, "pct_utilizacion": 0.0,
        "sucursales": [], "mayoristas": [],
    }


def test_resolver_fuera_de_horario_mueve_a_ruta_con_grupo_y_cupo(monkeypatch):
    import logic.tiempo_reubicacion as tr
    monkeypatch.setattr(tr, "TIEMPO_REUBICACION_ACTIVA", True)
    # 42 está lejos del depot. La ruta ORIGEN visita antes una sucursal
    # cercana (con su descarga) y llega tarde a 42. La ruta DESTINO (vacía,
    # mismo día, con el vehiculo dominante del grupo de 42) llega a tiempo
    # yendo directo.
    grupos = [{"grupo": 1, "rigidez": "FLEXIBLE", "sucursales": [42],
               "unidades_afines": "DEST:5", "dias_admisibles": ["MARTES"],
               "dia_preferido": "MARTES"}]
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
    destino = _ruta_vacia("DEST", "martes", "DEST")
    rutas = [origen, destino]

    movio = resolver_fuera_de_horario(rutas, _cfg_cierre_08_30(), grupos, consultar_osrm_fn=None)

    assert movio is True
    assert [s["num_tienda"] for s in origen["sucursales"]] == [1]
    assert 42 in [s["num_tienda"] for s in destino["sucursales"]]
    assert destino["peso_kg"] == 100.0


def test_resolver_fuera_de_horario_sin_grupo_no_mueve_nada():
    ruta = {
        "id": "ORIGEN", "dia": "martes", "vehiculo_abrev": "ORIGEN",
        "capacidad_ton": 3.5, "peso_kg": 0, "pct_utilizacion": 0.0,
        "sucursales": [
            {"num_tienda": 1, "nombre": "Cercana", "orden": 1, "peso_kg": 100,
             "latitud": 0.05, "longitud": 0.05},
            {"num_tienda": 999, "nombre": "Sin grupo", "orden": 2, "peso_kg": 100,
             "latitud": 0.5, "longitud": 0.5},
        ],
        "mayoristas": [],
    }
    movio = resolver_fuera_de_horario([ruta], _cfg_cierre_08_30(), [], consultar_osrm_fn=None)
    assert movio is False
    assert [s["num_tienda"] for s in ruta["sucursales"]] == [1, 999]


def test_resolver_fuera_de_horario_interruptor_apagado_no_hace_nada():
    ruta = {"id": "R1", "dia": "martes", "sucursales": [], "mayoristas": []}
    assert resolver_fuera_de_horario([ruta], {"activo": False}, []) is False


def test_resolver_fuera_de_horario_dominante_se_prueba_antes_que_minoritario(monkeypatch):
    import logic.tiempo_reubicacion as tr
    monkeypatch.setattr(tr, "TIEMPO_REUBICACION_ACTIVA", True)
    # MINORITARIO (1 semana de historial) esta vacia y cabria perfecto.
    # DOMINANTE (7 semanas) tambien cabe -- debe elegirse DOMINANTE primero
    # aunque ambas cumplan cupo+tiempo, por ser la de mayor conteo real.
    grupos = [{"grupo": 1, "rigidez": "FLEXIBLE", "sucursales": [42],
               "unidades_afines": "DOMINANTE:7 | MINORITARIO:1",
               "dias_admisibles": ["MARTES"], "dia_preferido": "MARTES"}]
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
    dominante = _ruta_vacia("DOMINANTE", "martes", "DOMINANTE")
    minoritario = _ruta_vacia("MINORITARIO", "martes", "MINORITARIO")
    rutas = [origen, minoritario, dominante]  # orden de lista no debe importar

    resolver_fuera_de_horario(rutas, _cfg_cierre_08_30(), grupos, consultar_osrm_fn=None)

    assert 42 in [s["num_tienda"] for s in dominante["sucursales"]]
    assert [s["num_tienda"] for s in minoritario["sucursales"]] == []


def test_resolver_fuera_de_horario_dia_canonico_antes_que_otro_dia_admisible(monkeypatch):
    import logic.tiempo_reubicacion as tr
    monkeypatch.setattr(tr, "TIEMPO_REUBICACION_ACTIVA", True)
    # Mismo vehiculo DEST disponible martes y jueves; el grupo admite ambos
    # dias pero MARTES es el preferido/canonico (primero en dias_admisibles)
    # -> se prueba primero, y como cumple, se elige ahi.
    grupos = [{"grupo": 1, "rigidez": "FLEXIBLE", "sucursales": [42],
               "unidades_afines": "DEST:5", "dias_admisibles": ["MARTES", "JUEVES"],
               "dia_preferido": "MARTES"}]
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
    dest_martes = _ruta_vacia("DEST_MARTES", "martes", "DEST")
    dest_jueves = _ruta_vacia("DEST_JUEVES", "jueves", "DEST")
    rutas = [origen, dest_jueves, dest_martes]

    resolver_fuera_de_horario(rutas, _cfg_cierre_08_30(), grupos, consultar_osrm_fn=None)

    assert 42 in [s["num_tienda"] for s in dest_martes["sucursales"]]
    assert [s["num_tienda"] for s in dest_jueves["sucursales"]] == []


def test_resolver_fuera_de_horario_grupo_rigido_mueve_ambos_miembros_juntos(monkeypatch):
    import logic.tiempo_reubicacion as tr
    monkeypatch.setattr(tr, "TIEMPO_REUBICACION_ACTIVA", True)
    # 76 fuera de horario; 77 (su pareja rigida) SI llega a tiempo en ORIGEN
    # -- de todos modos se mueven juntas al reubicar 76.
    grupos = [{"grupo": 30, "rigidez": "RIGIDO", "sucursales": [76, 77],
               "unidades_afines": "DEST:9", "dias_admisibles": ["MARTES"],
               "dia_preferido": "MARTES"}]
    origen = {
        "id": "ORIGEN", "dia": "martes", "vehiculo_abrev": "ORIGEN",
        "capacidad_ton": 3.5, "peso_kg": 0, "pct_utilizacion": 0.0,
        "sucursales": [
            {"num_tienda": 1, "nombre": "Cercana", "orden": 1, "peso_kg": 100,
             "latitud": 0.05, "longitud": 0.05},
            {"num_tienda": 77, "nombre": "Pareja", "orden": 2, "peso_kg": 50,
             "latitud": 0.06, "longitud": 0.06},
            {"num_tienda": 76, "nombre": "Lejana", "orden": 3, "peso_kg": 100,
             "latitud": 0.5, "longitud": 0.5},
        ],
        "mayoristas": [],
    }
    destino = _ruta_vacia("DEST", "martes", "DEST")
    rutas = [origen, destino]

    movio = resolver_fuera_de_horario(rutas, _cfg_cierre_08_30(), grupos, consultar_osrm_fn=None)

    assert movio is True
    assert [s["num_tienda"] for s in origen["sucursales"]] == [1]
    assert {s["num_tienda"] for s in destino["sucursales"]} == {76, 77}


def test_resolver_fuera_de_horario_grupo_rigido_sin_cupo_perfecto_igual_mueve_el_par_junto(monkeypatch):
    import logic.tiempo_reubicacion as tr
    monkeypatch.setattr(tr, "TIEMPO_REUBICACION_ACTIVA", True)
    # DEST cabe para 76 sola (100kg) pero no para 76+77 juntas (100+3300kg =
    # 97.1% > 85%) -- _mejor_candidata_grupo la descarta por peso. Como DEST
    # es la UNICA candidata dentro de unidades_afines, "menos malo" la elige
    # de todos modos (no hay gate de cupo en el ultimo recurso, mismo
    # criterio que v1) -- pero SIEMPRE mueve 76 Y 77 JUNTAS, nunca una sola
    # separada de su pareja rigida.
    grupos = [{"grupo": 30, "rigidez": "RIGIDO", "sucursales": [76, 77],
               "unidades_afines": "DEST:9", "dias_admisibles": ["MARTES"],
               "dia_preferido": "MARTES"}]
    origen = {
        "id": "ORIGEN", "dia": "martes", "vehiculo_abrev": "ORIGEN",
        "capacidad_ton": 3.5, "peso_kg": 0, "pct_utilizacion": 0.0,
        "sucursales": [
            {"num_tienda": 1, "nombre": "Cercana", "orden": 1, "peso_kg": 100,
             "latitud": 0.05, "longitud": 0.05},
            {"num_tienda": 77, "nombre": "Pareja", "orden": 2, "peso_kg": 3300,
             "latitud": 0.06, "longitud": 0.06},
            {"num_tienda": 76, "nombre": "Lejana", "orden": 3, "peso_kg": 100,
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

    movio = resolver_fuera_de_horario(rutas, _cfg_cierre_08_30(), grupos, consultar_osrm_fn=None)

    # "menos malo" no tiene gate de cupo (mismo criterio que v1): con DEST
    # como unica candidata dentro de unidades_afines, se elige de todos
    # modos aunque quede sobrecargada -- pero 76 y 77 SIEMPRE juntas.
    assert movio is True
    assert {s["num_tienda"] for s in destino["sucursales"]} == {76, 77}
    assert [s["num_tienda"] for s in origen["sucursales"]] == [1]


def test_resolver_fuera_de_horario_grupo_flexible_no_arrastra_companero_a_tiempo(monkeypatch):
    import logic.tiempo_reubicacion as tr
    monkeypatch.setattr(tr, "TIEMPO_REUBICACION_ACTIVA", True)
    # 100 fuera de horario; 86 (su companero FLEXIBLE) llega a tiempo y NO
    # debe moverse tambien.
    grupos = [{"grupo": 19, "rigidez": "FLEXIBLE", "sucursales": [86, 100],
               "unidades_afines": "DEST:7", "dias_admisibles": ["MARTES"],
               "dia_preferido": "MARTES"}]
    origen = {
        "id": "ORIGEN", "dia": "martes", "vehiculo_abrev": "ORIGEN",
        "capacidad_ton": 3.5, "peso_kg": 0, "pct_utilizacion": 0.0,
        "sucursales": [
            {"num_tienda": 86, "nombre": "Companera", "orden": 1, "peso_kg": 100,
             "latitud": 0.05, "longitud": 0.05},
            {"num_tienda": 100, "nombre": "Lejana", "orden": 2, "peso_kg": 100,
             "latitud": 0.5, "longitud": 0.5},
        ],
        "mayoristas": [],
    }
    destino = _ruta_vacia("DEST", "martes", "DEST")
    rutas = [origen, destino]

    movio = resolver_fuera_de_horario(rutas, _cfg_cierre_08_30(), grupos, consultar_osrm_fn=None)

    assert movio is True
    assert [s["num_tienda"] for s in origen["sucursales"]] == [86]
    assert [s["num_tienda"] for s in destino["sucursales"]] == [100]


def test_resolver_fuera_de_horario_menos_malo_nunca_sale_del_grupo(monkeypatch):
    import logic.tiempo_reubicacion as tr
    monkeypatch.setattr(tr, "TIEMPO_REUBICACION_ACTIVA", True)
    # SIN_AFINIDAD esta vacia (cabria perfecto) pero su vehiculo NO aparece
    # en unidades_afines del grupo -- nunca debe elegirse, ni como "menos
    # malo". CON_AFINIDAD si aparece mas esta llena -- debe preferirse
    # sobre no moverse en absoluto.
    grupos = [{"grupo": 1, "rigidez": "FLEXIBLE", "sucursales": [42],
               "unidades_afines": "CON_AFINIDAD:2", "dias_admisibles": ["MARTES"],
               "dia_preferido": "MARTES"}]
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
    sin_afinidad = _ruta_vacia("SIN_AFINIDAD", "martes", "SIN_AFINIDAD")
    con_afinidad = {
        "id": "CON_AFINIDAD", "dia": "martes", "vehiculo_abrev": "CON_AFINIDAD",
        "capacidad_ton": 3.5, "peso_kg": 3000, "pct_utilizacion": 85.7,
        "sucursales": [{"num_tienda": 5, "nombre": "Llena", "orden": 1, "peso_kg": 3000,
                        "latitud": 0.05, "longitud": 0.05}],
        "mayoristas": [],
    }
    rutas = [origen, sin_afinidad, con_afinidad]

    resolver_fuera_de_horario(rutas, _cfg_cierre_08_30(), grupos, consultar_osrm_fn=None)

    assert [s["num_tienda"] for s in sin_afinidad["sucursales"]] == []
    assert 42 in [s["num_tienda"] for s in con_afinidad["sucursales"]]


def test_resolver_fuera_de_horario_mayorista_se_mueve_con_grupo_de_su_ancla(monkeypatch):
    import logic.tiempo_reubicacion as tr
    monkeypatch.setattr(tr, "TIEMPO_REUBICACION_ACTIVA", True)
    grupos = [{"grupo": 1, "rigidez": "FLEXIBLE", "sucursales": [1],
               "unidades_afines": "DEST:3", "dias_admisibles": ["MARTES"],
               "dia_preferido": "MARTES"}]
    origen = {
        "id": "ORIGEN", "dia": "martes", "vehiculo_abrev": "ORIGEN",
        "capacidad_ton": 3.5, "peso_kg": 0, "pct_utilizacion": 0.0,
        "sucursales": [
            {"num_tienda": 1, "nombre": "Ancla", "orden": 1, "peso_kg": 50,
             "latitud": 0.49, "longitud": 0.49},
        ],
        "mayoristas": [
            {"id_cliente": 7, "documento": "BB1", "nombre": "Mayorista lejano",
             "orden": 2, "peso_kg": 30, "latitud": 0.5, "longitud": 0.5},
        ],
    }
    destino = _ruta_vacia("DEST", "martes", "DEST")
    rutas = [origen, destino]

    movio = resolver_fuera_de_horario(rutas, _cfg_cierre_08_30(), grupos, consultar_osrm_fn=None)

    assert movio is True
    assert origen["mayoristas"] == []
    assert [m["id_cliente"] for m in destino["mayoristas"]] == [7]
    # El ancla (sucursal 1) NO se mueve -- solo el mayorista era el que
    # estaba fuera de horario.
    assert [s["num_tienda"] for s in origen["sucursales"]] == [1]


def test_resolver_fuera_de_horario_procesa_varias_paradas_en_la_misma_ruta(monkeypatch):
    import logic.tiempo_reubicacion as tr
    monkeypatch.setattr(tr, "TIEMPO_REUBICACION_ACTIVA", True)
    # ORIGEN tiene DOS paradas fuera de horario (42 y 43). Cada una tiene
    # grupo propio con destino distinto, vacio, del mismo dia. Deben
    # resolverse una por una (re-evaluando ORIGEN tras cada movimiento).
    grupos = [
        {"grupo": 1, "rigidez": "FLEXIBLE", "sucursales": [42],
         "unidades_afines": "DEST1:2", "dias_admisibles": ["MARTES"], "dia_preferido": "MARTES"},
        {"grupo": 2, "rigidez": "FLEXIBLE", "sucursales": [43],
         "unidades_afines": "DEST2:2", "dias_admisibles": ["MARTES"], "dia_preferido": "MARTES"},
    ]
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
    dest1 = _ruta_vacia("DEST1", "martes", "DEST1")
    dest2 = _ruta_vacia("DEST2", "martes", "DEST2")
    rutas = [origen, dest1, dest2]

    movio = resolver_fuera_de_horario(rutas, _cfg_cierre_08_30(), grupos, consultar_osrm_fn=None)

    assert movio is True
    assert [s["num_tienda"] for s in origen["sucursales"]] == [1]
    assert [s["num_tienda"] for s in dest1["sucursales"]] == [42]
    assert [s["num_tienda"] for s in dest2["sucursales"]] == [43]


def test_resolver_fuera_de_horario_es_idempotente(monkeypatch):
    import logic.tiempo_reubicacion as tr
    monkeypatch.setattr(tr, "TIEMPO_REUBICACION_ACTIVA", True)
    grupos = [{"grupo": 1, "rigidez": "FLEXIBLE", "sucursales": [42],
               "unidades_afines": "DEST:5", "dias_admisibles": ["MARTES"],
               "dia_preferido": "MARTES"}]
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
    destino = _ruta_vacia("DEST", "martes", "DEST")
    rutas = [origen, destino]

    primera = resolver_fuera_de_horario(rutas, _cfg_cierre_08_30(), grupos, consultar_osrm_fn=None)
    segunda = resolver_fuera_de_horario(rutas, _cfg_cierre_08_30(), grupos, consultar_osrm_fn=None)

    assert primera is True
    assert segunda is False


def test_resolver_fuera_de_horario_flag_dedicado_apagado_no_hace_nada(monkeypatch):
    import logic.tiempo_reubicacion as tr
    monkeypatch.setattr(tr, "TIEMPO_REUBICACION_ACTIVA", False)
    ruta = {"id": "R1", "dia": "martes", "sucursales": [], "mayoristas": []}
    assert resolver_fuera_de_horario([ruta], _cfg_cierre_08_30(), []) is False


def test_regresion_tierra_blanca_7_no_va_a_ruta_sin_relacion_geografica(monkeypatch):
    import logic.tiempo_reubicacion as tr
    monkeypatch.setattr(tr, "TIEMPO_REUBICACION_ACTIVA", True)
    # Datos reales: grupo 30, RIGIDO, sucursales [76, 77], unidades_afines
    # real "T 23:3 | K 16:2 | T 20:2 | T 17_1:1 | T 25:1". T20 SI aparece
    # (2/9 semanas) -- si T20 no tiene ruta el lunes esta semana, T20 nunca
    # debe elegirse (no hay ruta real que buscar), y si la tiene, se mueven
    # 76 Y 77 juntas, nunca 76 sola separada de su pareja rigida.
    grupos = [{"grupo": 30, "rigidez": "RIGIDO", "sucursales": [76, 77],
               "unidades_afines": "T 23:3 | K 16:2 | T 20:2 | T 17_1:1 | T 25:1",
               "dias_admisibles": ["LUNES"], "dia_preferido": "LUNES"}]
    origen = {
        "id": "ORIGEN_T25_LUNES", "dia": "lunes", "vehiculo_abrev": "T 25",
        "capacidad_ton": 3.5, "peso_kg": 0, "pct_utilizacion": 0.0,
        "sucursales": [
            {"num_tienda": 1, "nombre": "Cercana", "orden": 1, "peso_kg": 100,
             "latitud": 0.05, "longitud": 0.05},
            {"num_tienda": 77, "nombre": "Tierra Blanca 8", "orden": 2, "peso_kg": 50,
             "latitud": 0.06, "longitud": 0.06},
            {"num_tienda": 76, "nombre": "Tierra Blanca 7", "orden": 3, "peso_kg": 100,
             "latitud": 0.5, "longitud": 0.5},
        ],
        "mayoristas": [],
    }
    # T 20 SI tiene ruta el lunes esta semana (candidata real de unidades_afines).
    t20_lunes = _ruta_vacia("T20_LUNES", "lunes", "T 20")
    # Ruta geograficamente ajena, sin ninguna relacion con el grupo 30 --
    # NUNCA debe recibir a 76/77 aunque tuviera cupo de sobra.
    ruta_ajena = _ruta_vacia("AJENA_SIN_AFINIDAD", "lunes", "AJENA")
    rutas = [origen, t20_lunes, ruta_ajena]

    resolver_fuera_de_horario(rutas, _cfg_cierre_08_30(), grupos, consultar_osrm_fn=None)

    # La ruta ajena (sin presencia en unidades_afines) jamas recibe nada.
    assert ruta_ajena["sucursales"] == []
    # Si algo se movio, 76 y 77 se movieron JUNTAS (nunca una sola).
    movidas_a_t20 = {s["num_tienda"] for s in t20_lunes["sucursales"]}
    if movidas_a_t20:
        assert movidas_a_t20 == {76, 77}
    else:
        # Si T20 no cupo para ambas, el grupo se queda completo en origen.
        assert {s["num_tienda"] for s in origen["sucursales"]} == {1, 76, 77}


def test_regresion_amatitlan_prefiere_vehiculo_dominante_sobre_uno_de_una_semana(monkeypatch):
    import logic.tiempo_reubicacion as tr
    monkeypatch.setattr(tr, "TIEMPO_REUBICACION_ACTIVA", True)
    # Datos reales: grupo 19, FLEXIBLE, sucursales [86, 100], cohesion 0.67,
    # unidades_afines real "F 350_1:7 | T 20:1 | T 25:1". F 350_1 es el
    # hogar dominante (7/9 semanas) -- si F 350_1 tiene otra ruta disponible
    # (otro dia admisible) con cupo+tiempo, debe preferirse sobre T 25
    # (1/9 semanas) aunque T 25 tambien cumpla.
    grupos = [{"grupo": 19, "rigidez": "FLEXIBLE", "sucursales": [86, 100],
               "unidades_afines": "F 350_1:7 | T 20:1 | T 25:1",
               "dias_admisibles": ["MARTES", "JUEVES"], "dia_preferido": "JUEVES"}]
    origen = {
        "id": "ORIGEN_F350_1_MARTES", "dia": "martes", "vehiculo_abrev": "F 350_1",
        "capacidad_ton": 3.5, "peso_kg": 0, "pct_utilizacion": 0.0,
        "sucursales": [
            {"num_tienda": 1, "nombre": "Cercana", "orden": 1, "peso_kg": 100,
             "latitud": 0.05, "longitud": 0.05},
            {"num_tienda": 100, "nombre": "Amatitlan", "orden": 2, "peso_kg": 100,
             "latitud": 0.5, "longitud": 0.5},
        ],
        "mayoristas": [],
    }
    f350_1_jueves = _ruta_vacia("F350_1_JUEVES", "jueves", "F 350_1")
    t25_jueves = _ruta_vacia("T25_JUEVES", "jueves", "T 25")
    rutas = [origen, t25_jueves, f350_1_jueves]  # orden de lista no debe importar

    resolver_fuera_de_horario(rutas, _cfg_cierre_08_30(), grupos, consultar_osrm_fn=None)

    # F 350_1 (dominante, 7/9) se prueba antes que T 25 (1/9) -- si F 350_1
    # cumple cupo+tiempo, se elige ahi, no en T 25.
    assert 100 in [s["num_tienda"] for s in f350_1_jueves["sucursales"]]
    assert [s["num_tienda"] for s in t25_jueves["sucursales"]] == []
