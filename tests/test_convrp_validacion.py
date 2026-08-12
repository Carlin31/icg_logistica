"""
tests/test_convrp_validacion.py

Pruebas del arnés de fidelidad (origen móvil). Puras: sin BD.
"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from logic.convrp_validacion import medir_fidelidad, construir_plantilla_desde


def _fila(sid, veh, dia):
    return {"id_sucursal": sid, "vehiculo": veh, "dia_semana": dia,
            "tipo": "sucursal", "kg_entrega": 100, "secuencia_visita": 1}


def test_dia_exacto_y_dia_admisible():
    # El plan pone a la sucursal 1 en MARTES; en la realidad fue MIERCOLES.
    # Día exacto falla, pero MIERCOLES está entre los admisibles de su grupo,
    # así que la métrica "admisible" debe darlo por bueno.
    groups = {("V1", "MARTES"): [{"sid": 1, "seq": 1}, {"sid": 2, "seq": 2}]}
    reales = [_fila(1, "V1", "MIERCOLES"), _fila(2, "V1", "MIERCOLES")]
    admisibles = {1: ["MARTES", "MIERCOLES"], 2: ["MARTES", "MIERCOLES"]}
    fid = medir_fidelidad(groups, reales, admisibles_por_sucursal=admisibles)
    assert fid["dia_correcto_pct"] == 0.0
    assert fid["dia_admisible_pct"] == 100.0


def test_dia_admisible_castiga_fuera_del_conjunto():
    groups = {("V1", "VIERNES"): [{"sid": 1, "seq": 1}]}
    reales = [_fila(1, "V1", "LUNES")]
    admisibles = {1: ["MARTES", "MIERCOLES"]}       # VIERNES no es admisible
    fid = medir_fidelidad(groups, reales, admisibles_por_sucursal=admisibles)
    assert fid["dia_admisible_pct"] == 0.0


def test_sin_admisibles_la_metrica_no_aparece():
    groups = {("V1", "MARTES"): [{"sid": 1, "seq": 1}]}
    reales = [_fila(1, "V1", "MARTES")]
    fid = medir_fidelidad(groups, reales)
    assert fid["dia_correcto_pct"] == 100.0
    assert fid.get("dia_admisible_pct") is None


def test_construir_plantilla_agrupa_lo_que_viaja_junto():
    # 1 y 2 viajan siempre juntas; 3 siempre sola.
    semanas = [[_fila(1, "V1", "LUNES"), _fila(2, "V1", "LUNES"), _fila(3, "V2", "MARTES")]
               for _ in range(4)]
    pl = construir_plantilla_desde(semanas)
    grupos = sorted(tuple(g["sucursales"]) for g in pl)
    assert (1, 2) in grupos
    assert (3,) in grupos


def test_construir_plantilla_no_usa_semanas_fuera_de_la_ventana():
    # Las 2 primeras semanas dicen LUNES, las 2 últimas MARTES. Con ventana=2 el
    # día debe salir MARTES (sólo las últimas), no LUNES.
    semanas = ([[_fila(1, "V1", "LUNES")]] * 2) + ([[_fila(1, "V1", "MARTES")]] * 2)
    pl = construir_plantilla_desde(semanas, ventana_dia=2)
    assert pl[0]["dia"] == "MARTES"


def test_ventana_de_dia_y_de_unidad_son_independientes():
    # Son dos preguntas distintas ("¿qué día opera el grupo?" vs "¿en qué
    # camión?") y medirlas con la misma ventana impide saber cuál movió el
    # resultado. Aquí: 3 semanas en V1/LUNES y una última en V2/MARTES.
    from logic.convrp_validacion import construir_plantilla_desde
    viejas = [[{"id_sucursal": 1, "vehiculo": "V1", "dia_semana": "LUNES"}]] * 3
    nueva = [{"id_sucursal": 1, "vehiculo": "V2", "dia_semana": "MARTES"}]
    semanas = viejas + [nueva]
    # ventana 1 para ambos: manda la última semana
    p = construir_plantilla_desde(semanas, ventana_dia=1)[0]
    assert (p["dia_preferido"], p["unidad_ref"]) == ("MARTES", "V2")
    # día por la última semana, unidad por todo el histórico
    p = construir_plantilla_desde(semanas, ventana_dia=1, ventana_unidad=None)[0]
    assert (p["dia_preferido"], p["unidad_ref"]) == ("MARTES", "V1")
    # y al revés
    p = construir_plantilla_desde(semanas, ventana_dia=None, ventana_unidad=1)[0]
    assert (p["dia_preferido"], p["unidad_ref"]) == ("LUNES", "V2")


# ═══════════════════════════════════════════════════════════════════════════
# Asignación GLOBAL de unidad_ref
#
# La moda por grupo ignora que la unidad es un recurso compartido: 8 grupos
# apuntan a T 17_2 y 8 a T 20 mientras J 18, J 19 y K 20 no son referencia de
# ninguno. Medido sobre 9 semanas, eso deja T 17_1 con +17 días de trabajo
# contra la realidad y a K 16 con -12. La referencia hay que resolverla POR DÍA
# y contra la capacidad, no grupo por grupo.
# ═══════════════════════════════════════════════════════════════════════════

def test_asignacion_global_no_apila_grupos_en_la_misma_unidad():
    from logic.convrp_validacion import asignar_unidad_ref
    # tres grupos el mismo día, todos con máxima afinidad por V1, que no puede
    # con los tres: la afinidad manda, pero la capacidad decide.
    grupos = [{"grupo": i, "dia_preferido": "LUNES"} for i in (1, 2, 3)]
    afinidad = {1: {"V1": 9}, 2: {"V1": 9}, 3: {"V1": 9}}
    kg = {1: 900, 2: 900, 3: 900}
    caps = {"V1": 1000, "V2": 1000, "V3": 1000}
    ref = asignar_unidad_ref(grupos, afinidad, kg, caps)
    assert sorted(ref.values()) == ["V1", "V2", "V3"]


def test_asignacion_global_respeta_la_afinidad_cuando_cabe():
    from logic.convrp_validacion import asignar_unidad_ref
    grupos = [{"grupo": 1, "dia_preferido": "LUNES"},
              {"grupo": 2, "dia_preferido": "LUNES"}]
    afinidad = {1: {"V2": 7, "V1": 1}, 2: {"V1": 8}}
    kg = {1: 100, 2: 100}
    caps = {"V1": 1000, "V2": 1000}
    ref = asignar_unidad_ref(grupos, afinidad, kg, caps)
    assert ref == {1: "V2", 2: "V1"}


def test_asignacion_global_no_colisiona_entre_dias_distintos():
    # dos grupos con la misma afinidad pero en días distintos SÍ pueden
    # compartir unidad: la restricción es por día, no por semana.
    from logic.convrp_validacion import asignar_unidad_ref
    grupos = [{"grupo": 1, "dia_preferido": "LUNES"},
              {"grupo": 2, "dia_preferido": "MARTES"}]
    afinidad = {1: {"V1": 9}, 2: {"V1": 9}}
    kg = {1: 900, 2: 900}
    ref = asignar_unidad_ref(grupos, afinidad, kg, {"V1": 1000, "V2": 1000})
    assert ref == {1: "V1", 2: "V1"}


def test_asignacion_global_es_determinista():
    from logic.convrp_validacion import asignar_unidad_ref
    grupos = [{"grupo": i, "dia_preferido": "LUNES"} for i in (1, 2, 3, 4)]
    afinidad = {i: {} for i in (1, 2, 3, 4)}          # sin historia: empate total
    kg = {i: 500 for i in (1, 2, 3, 4)}
    caps = {"A": 1000, "B": 1000, "C": 1000}
    a = asignar_unidad_ref(grupos, afinidad, kg, caps)
    b = asignar_unidad_ref(list(reversed(grupos)), afinidad, kg, caps)
    assert a == b


def test_asignacion_global_usa_toda_la_flota_antes_de_sobrecargar():
    from logic.convrp_validacion import asignar_unidad_ref
    grupos = [{"grupo": i, "dia_preferido": "LUNES"} for i in range(1, 5)]
    afinidad = {i: {"V1": 9} for i in range(1, 5)}    # todos quieren V1
    kg = {i: 800 for i in range(1, 5)}
    caps = {"V1": 1000, "V2": 1000, "V3": 1000, "V4": 1000}
    ref = asignar_unidad_ref(grupos, afinidad, kg, caps)
    assert len(set(ref.values())) == 4                # una unidad por grupo


def test_asignacion_global_respeta_el_objetivo_de_viajes_del_dia():
    # Repartir por afinidad sin más abre un viaje por grupo: fuera de muestra
    # subía de 29.4 a 34.6 viajes/semana contra 29.8 reales, y la utilización
    # caía de 65 % a 58 %. La empresa hace ~6 viajes por día, no uno por grupo:
    # el objetivo del día acota cuántas unidades se abren.
    from logic.convrp_validacion import asignar_unidad_ref
    grupos = [{"grupo": i, "dia_preferido": "LUNES"} for i in (1, 2, 3, 4)]
    afinidad = {1: {"V1": 9}, 2: {"V2": 9}, 3: {"V3": 9}, 4: {"V4": 9}}
    kg = {i: 400 for i in (1, 2, 3, 4)}               # 1,600 kg en total
    caps = {"V1": 1000, "V2": 1000, "V3": 1000, "V4": 1000}
    libre = asignar_unidad_ref(grupos, afinidad, kg, caps)
    assert len(set(libre.values())) == 4              # sin objetivo: 4 unidades
    acotada = asignar_unidad_ref(grupos, afinidad, kg, caps,
                                 viajes_objetivo={"LUNES": 2})
    assert len(set(acotada.values())) == 2            # con objetivo: 2


def test_objetivo_de_viajes_cede_si_la_carga_no_cabe():
    # El objetivo es una preferencia, no un tope duro: si los grupos del día no
    # caben en esas unidades, se abre otra en vez de sobrecargar.
    from logic.convrp_validacion import asignar_unidad_ref
    grupos = [{"grupo": i, "dia_preferido": "LUNES"} for i in (1, 2, 3)]
    afinidad = {1: {"V1": 9}, 2: {"V2": 9}, 3: {"V3": 9}}
    kg = {i: 900 for i in (1, 2, 3)}                  # 2,700 kg
    caps = {"V1": 1000, "V2": 1000, "V3": 1000}
    ref = asignar_unidad_ref(grupos, afinidad, kg, caps,
                             viajes_objetivo={"LUNES": 1})
    assert len(set(ref.values())) == 3


def test_kg_representativo_no_usa_la_mediana_para_decidir_camion():
    # Elegir capacidad con la MEDIANA subdimensiona: el g38 (Tierra Blanca 1)
    # tiene mediana 1,091 kg y quedó referenciado a un T 23 de 1,500 — pero su
    # semana pico son 1,529 y no cabe. El motor tuvo que cederlo a un F 350 y
    # le abrió a FELIPE un día de trabajo que no existe en la operación.
    # Para decidir camión hay que mirar el percentil alto, no el centro.
    from logic.convrp_validacion import kg_representativo
    semanas = [900, 1000, 1091, 1100, 1200, 1250, 1300, 1400, 1529]
    assert kg_representativo(semanas) >= 1400
    assert kg_representativo(semanas) <= 1529          # no es el máximo ciego
    assert kg_representativo([]) == 0.0
    assert kg_representativo([500]) == 500


def test_la_unidad_de_referencia_tiene_que_aguantar_la_semana_alta():
    # Dos cantidades distintas: cuánto OCUPA el grupo al empacar varios en un
    # mismo camión (la semana típica) y cuánto tiene que AGUANTAR ese camión
    # (la semana alta). Mezclarlas subdimensiona: con la mediana el g38 quedó
    # referenciado a un T 23 de 1,500 y su pico de 1,529 no cabía.
    from logic.convrp_validacion import asignar_unidad_ref
    grupos = [{"grupo": 1, "dia_preferido": "LUNES"}]
    afinidad = {1: {"CHICA": 9}}          # la historia dice CHICA…
    ref = asignar_unidad_ref(grupos, afinidad, {1: 1000},
                             {"CHICA": 1200, "GRANDE": 3000},
                             kg_minimo={1: 1500})    # …pero no aguanta el pico
    assert ref == {1: "GRANDE"}


def test_el_minimo_no_infla_el_empaque():
    # El mínimo filtra candidatos, no reserva capacidad: dos grupos con pico
    # alto pero consumo típico bajo siguen cabiendo juntos en el mismo camión.
    from logic.convrp_validacion import asignar_unidad_ref
    grupos = [{"grupo": 1, "dia_preferido": "LUNES"},
              {"grupo": 2, "dia_preferido": "LUNES"}]
    afinidad = {1: {"V1": 9}, 2: {"V1": 9}}
    ref = asignar_unidad_ref(grupos, afinidad, {1: 400, 2: 400}, {"V1": 1000},
                             kg_minimo={1: 900, 2: 900})
    assert ref == {1: "V1", 2: "V1"}


# ═══════════════════════════════════════════════════════════════════════════
# Filtro de coocurrencia — encontrado en producción el 2026-08-10.
#
# Primer intento: filtrar por distancia (centroide, 60 km). Descartado con
# datos reales: hay pares que SÍ viajaron juntos a 84 km (grupos 9/19, F 350_1
# martes) y una pareja que NUNCA coincidió a sólo 58 km (grupos 19/22 — J 19
# jueves mezcló Amatitlán/Carrillo 2 con Temascal/Los Naranjos, cero
# precedente en 11 semanas). La distancia no discrimina bien en ningún
# sentido; el historial real de qué grupos SÍ compartieron camión-día sí.
# ═══════════════════════════════════════════════════════════════════════════

def test_coocurrencia_separa_grupos_sin_precedente_si_hay_alternativa():
    from logic.convrp_validacion import asignar_unidad_ref
    grupos = [{"grupo": 1, "dia_preferido": "LUNES"},
              {"grupo": 2, "dia_preferido": "LUNES"}]
    # 1 y 3 sí compartieron camión alguna vez; 1 y 2 nunca.
    coocurrencia = {frozenset((1, 3)): 2}
    afinidad = {1: {"V1": 9}, 2: {"V1": 9}}     # ambos "quieren" V1 por afinidad
    kg = {1: 100, 2: 100}
    caps = {"V1": 1000, "V2": 1000}
    ref = asignar_unidad_ref(grupos, afinidad, kg, caps, coocurrencia=coocurrencia)
    assert ref[1] == "V1"
    assert ref[2] == "V2"          # sin precedente con lo que ya hay en V1 -> cede a V2


def test_coocurrencia_cede_si_es_la_unica_unidad():
    # Sin alternativa, mejor una combinación sin precedente que sin camión.
    from logic.convrp_validacion import asignar_unidad_ref
    grupos = [{"grupo": 1, "dia_preferido": "LUNES"},
              {"grupo": 2, "dia_preferido": "LUNES"}]
    afinidad = {1: {"V1": 9}, 2: {"V1": 9}}
    kg = {1: 100, 2: 100}
    ref = asignar_unidad_ref(grupos, afinidad, kg, {"V1": 1000}, coocurrencia={})
    assert ref == {1: "V1", 2: "V1"}


def test_coocurrencia_permite_grupos_con_precedente_real():
    # Regresión: si el histórico SÍ los puso juntos alguna vez, se consolidan
    # igual que antes del filtro (aunque estén "lejos" en línea recta).
    from logic.convrp_validacion import asignar_unidad_ref
    grupos = [{"grupo": 1, "dia_preferido": "LUNES"},
              {"grupo": 2, "dia_preferido": "LUNES"}]
    coocurrencia = {frozenset((1, 2)): 3}
    afinidad = {1: {"V1": 9}, 2: {"V1": 9}}
    kg = {1: 100, 2: 100}
    caps = {"V1": 1000, "V2": 1000}
    ref = asignar_unidad_ref(grupos, afinidad, kg, caps, coocurrencia=coocurrencia)
    assert ref == {1: "V1", 2: "V1"}


def test_coocurrencia_abre_unidad_fresca_si_el_cupo_ya_se_lleno_sin_precedente():
    # Hallado en producción el 2026-08-11: F 350_2 (Tuxtepec) jueves se quedó
    # con el grupo de Veracruz 1/Tejería. El filtro de coocurrencia sólo elige
    # ENTRE lo que ya está abierto ese día -- nunca abre una unidad nueva. Si
    # los grupos más pesados ya llenaron el cupo del día antes de que le
    # tocara su turno a este grupo, su unidad de mayor afinidad real (T 20)
    # nunca llegó a abrirse, y "cede" apilándolo en lo que sea que ya esté
    # abierto aunque tenga cero precedente. Mejor abrir una unidad fresca
    # compatible -- igual que ya se hace cuando lo que falta es capacidad.
    from logic.convrp_validacion import asignar_unidad_ref
    grupos = [{"grupo": 1, "dia_preferido": "JUEVES"},
              {"grupo": 2, "dia_preferido": "JUEVES"}]
    afinidad = {1: {"V1": 9}, 2: {"V2": 5}}   # el 2 nunca viajó en V1
    coocurrencia = {}                          # 1 y 2 nunca compartieron camión-día
    kg = {1: 100, 2: 100}
    caps = {"V1": 1000, "V2": 1000}
    ref = asignar_unidad_ref(grupos, afinidad, kg, caps,
                             viajes_objetivo={"JUEVES": 1}, coocurrencia=coocurrencia)
    assert ref[1] == "V1"
    assert ref[2] == "V2"      # abre V2 (fresca, con precedente propio) en vez de apilar en V1


def test_coocurrencia_no_abre_unidad_fresca_si_la_afinidad_propia_es_debil():
    # Medido contra 9 semanas de historia real: abrir una unidad fresca cada
    # vez que "no hay precedente" (afinidad 0 o 1 con la fresca) subió JUEVES
    # de 8 a 11 viajes/semana -- casi todos sin evidencia real de que ESE
    # grupo prefiera esa unidad, sólo que técnicamente nunca coincidió con lo
    # ya abierto. El rescate sólo debe activarse cuando el grupo mismo tiene
    # afinidad fuerte (>=2 semanas) con la unidad fresca -- si no, mejor
    # ceder como antes que abrir un camión de más por una corazonada débil.
    from logic.convrp_validacion import asignar_unidad_ref
    grupos = [{"grupo": 1, "dia_preferido": "JUEVES"},
              {"grupo": 2, "dia_preferido": "JUEVES"}]
    afinidad = {1: {"V1": 9}, 2: {"V2": 1}}   # el 2 sólo viajó en V2 una vez
    coocurrencia = {}
    kg = {1: 100, 2: 100}
    caps = {"V1": 1000, "V2": 1000}
    ref = asignar_unidad_ref(grupos, afinidad, kg, caps,
                             viajes_objetivo={"JUEVES": 1}, coocurrencia=coocurrencia)
    assert ref[2] == "V1"      # afinidad débil con V2: cede en V1 en vez de abrirla


def test_coocurrencia_sigue_cediendo_si_no_hay_unidad_fresca_disponible():
    # Si ya no queda ninguna unidad sin abrir ese día, sigue aplicando el
    # "cede" de siempre -- el rescate de arriba sólo entra cuando de verdad
    # hay una unidad fresca a la que abrir.
    from logic.convrp_validacion import asignar_unidad_ref
    grupos = [{"grupo": 1, "dia_preferido": "JUEVES"},
              {"grupo": 2, "dia_preferido": "JUEVES"}]
    afinidad = {1: {"V1": 9}, 2: {"V1": 9}}
    coocurrencia = {}
    kg = {1: 100, 2: 100}
    caps = {"V1": 1000}       # única unidad de la flota: no hay fresca que abrir
    ref = asignar_unidad_ref(grupos, afinidad, kg, caps,
                             viajes_objetivo={"JUEVES": 1}, coocurrencia=coocurrencia)
    assert ref == {1: "V1", 2: "V1"}


def test_sin_coocurrencia_el_filtro_no_actua():
    # Comportamiento previo intacto si no se pasa `coocurrencia` (compatibilidad
    # con los llamadores que aún no la tienen disponible).
    from logic.convrp_validacion import asignar_unidad_ref
    grupos = [{"grupo": 1, "dia_preferido": "LUNES"},
              {"grupo": 2, "dia_preferido": "LUNES"}]
    afinidad = {1: {"V1": 9}, 2: {"V1": 9}}
    kg = {1: 100, 2: 100}
    caps = {"V1": 1000, "V2": 1000}
    ref = asignar_unidad_ref(grupos, afinidad, kg, caps)
    assert ref == {1: "V1", 2: "V1"}


# ── coocurrencia_grupos (contra semanas sintéticas) ─────────────────────────
def test_coocurrencia_grupos_cuenta_semanas_compartidas():
    from logic.convrp_validacion import coocurrencia_grupos
    grupo_de = {1: 10, 2: 10, 3: 20, 4: 30}
    semana_a = [_fila(1, "V1", "LUNES"), _fila(3, "V1", "LUNES")]           # 10-20
    semana_b = [_fila(1, "V1", "LUNES"), _fila(3, "V1", "LUNES")]           # 10-20 otra vez
    semana_c = [_fila(1, "V1", "LUNES"), _fila(4, "V2", "MARTES")]         # 10 y 30, distinto viaje
    coo = coocurrencia_grupos(grupo_de, [semana_a, semana_b, semana_c])
    assert coo[frozenset((10, 20))] == 2
    assert frozenset((10, 30)) not in coo      # nunca compartieron (unidad,día)


def test_coocurrencia_grupos_ignora_mayoristas():
    from logic.convrp_validacion import coocurrencia_grupos
    grupo_de = {1: 10, 2: 20}
    semana = [_fila(1, "V1", "LUNES"), _fila(2, "V1", "LUNES"),
              {"id_cliente": 99, "tipo": "mayorista", "vehiculo": "V1", "dia_semana": "LUNES"}]
    coo = coocurrencia_grupos(grupo_de, [semana])
    assert coo == {frozenset((10, 20)): 1}
