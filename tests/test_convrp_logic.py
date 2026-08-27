"""
tests/test_convrp_logic.py

Pruebas del builder ConVRP (Fase 2): el VRP como AJUSTADOR sobre la plantilla
canónica. Puras — la plantilla se inyecta, no se lee de la BD.

Contrato bajo prueba:
  - El grupo son las sucursales CON PEDIDO esa semana, no el roster completo.
  - La unidad es preferencia con penalización, no libre.
  - El día se mueve sólo dentro de los días admisibles del grupo.
  - Orden de palancas: unidad → día → partir rígido (último recurso).
  - Partir un rígido es determinista, y queda registrado como excepción.
  - Se registra QUÉ restricción ató (peso / volumen / tiempo).
  - Determinismo: mismo insumo → misma salida, siempre.
  - La cascada de movimientos de día está acotada explícitamente.
"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from logic.convrp_logic import construir_groups_desde_plantilla, cfg_por_defecto


# ── helpers de armado ──────────────────────────────────────────────────────
def _grupo(gid, rigidez, dia, sucursales, unidad_ref="V1", dias_admisibles=None):
    return dict(grupo=gid, rigidez=rigidez, dia=dia, unidad_ref=unidad_ref,
                sucursales=list(sucursales),
                dias_admisibles=list(dias_admisibles or [dia]),
                dia_preferido=dia)


def _cfg(**kw):
    c = cfg_por_defecto()
    c.update(kw)
    return c


def _sin_tiempo(**kw):
    """cfg con el chequeo de tiempo apagado (aísla peso/volumen en las pruebas)."""
    return _cfg(chequear_tiempo=False, **kw)


COORDS = {i: (18.0 + i * 0.01, -96.0) for i in range(1, 60)}


# ══ 1. El grupo son las sucursales con pedido esa semana ═══════════════════
def test_grupo_rigido_de_6_con_demanda_en_4_viaja_de_4():
    plantilla = [_grupo(1, "RIGIDO", "LUNES", [1, 2, 3, 4, 5, 6])]
    pedidos = {1: 100, 2: 100, 3: 100, 4: 100}          # 5 y 6 sin pedido
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, {"V1": 5000}, {"V1": 20}, _sin_tiempo())
    sids = sorted(m["sid"] for ms in groups.values() for m in ms)
    assert sids == [1, 2, 3, 4]


def test_grupo_sin_demanda_no_genera_ruta():
    plantilla = [_grupo(1, "RIGIDO", "LUNES", [1, 2]),
                 _grupo(2, "FLEXIBLE", "MARTES", [3, 4])]
    pedidos = {1: 100, 2: 100}                           # grupo 2 sin pedidos
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, {"V1": 5000}, {"V1": 20}, _sin_tiempo())
    assert len(groups) == 1
    assert list(groups.keys())[0][1] == "LUNES"


# ══ 2. Grupos con la misma unidad y día comparten ruta ═════════════════════
def test_dos_grupos_misma_unidad_y_dia_comparten_ruta():
    plantilla = [_grupo(1, "RIGIDO", "LUNES", [1, 2], unidad_ref="V1"),
                 _grupo(2, "FLEXIBLE", "LUNES", [3, 4], unidad_ref="V1")]
    pedidos = {1: 100, 2: 100, 3: 100, 4: 100}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, {"V1": 5000}, {"V1": 20}, _sin_tiempo())
    assert len(groups) == 1
    assert len(groups[("V1", "LUNES")]) == 4


# ══ 2b. Selección por peso: la más chica que alcanza, no el abecedario ═════
def test_sin_preferencia_elige_la_unidad_mas_chica_que_alcanza():
    # Sin unidad_ref, dos unidades de igual capacidad ordenan por nombre HOY
    # (comportamiento viejo) -- el nuevo contrato debe elegir por CAPACIDAD
    # ascendente cuando las capacidades difieren, no caer siempre en la
    # primera alfabética.
    plantilla = [_grupo(1, "FLEXIBLE", "LUNES", [1, 2], unidad_ref=None)]
    pedidos = {1: 200, 2: 200}                      # 400 kg
    caps = {"Z_CHICA": 500, "A_GRANDE": 5000}        # Z ordena después de A
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, caps, {"Z_CHICA": 99, "A_GRANDE": 99},
        _sin_tiempo())
    assert ("Z_CHICA", "LUNES") in groups, \
        "debió elegir la unidad más chica que alcanza (400<=500), no la más grande"


def test_grupo_no_cabe_en_la_mas_chica_escala_a_la_siguiente():
    plantilla = [_grupo(1, "FLEXIBLE", "LUNES", [1, 2], unidad_ref=None)]
    pedidos = {1: 800, 2: 800}                      # 1600 kg
    caps = {"CHICA": 1000, "MEDIANA": 2500, "GRANDE": 3900}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, caps,
        {"CHICA": 99, "MEDIANA": 99, "GRANDE": 99}, _sin_tiempo())
    assert ("MEDIANA", "LUNES") in groups, \
        "no cabe en CHICA (1600>1000); debe escalar a MEDIANA, no saltar a GRANDE"


def test_entre_misma_capacidad_gana_la_ya_cargada_ese_dia():
    # Dos grupos, dos unidades de igual capacidad: el segundo grupo debe
    # sumarse a la que ya lleva carga (consolidar), no abrir la vacía.
    plantilla = [_grupo(1, "FLEXIBLE", "LUNES", [1], unidad_ref=None),
                 _grupo(2, "FLEXIBLE", "LUNES", [2], unidad_ref=None)]
    pedidos = {1: 1000, 2: 100}
    caps = {"V1": 5000, "V2": 5000}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, caps, {"V1": 99, "V2": 99}, _sin_tiempo())
    assert len(groups) == 1, "el segundo grupo debió consolidar, no abrir otra unidad"


# ══ 2c. `unidades_excluidas`: prohibición dura, nunca se asigna en silencio ═
def test_grupo_con_exclusion_nunca_recibe_la_unidad_excluida():
    plantilla = [_grupo(1, "FLEXIBLE", "LUNES", [1, 2], unidad_ref=None)]
    plantilla[0]["unidades_excluidas"] = ["CHICA"]
    pedidos = {1: 50, 2: 50}                        # 100 kg: cabría en CHICA
    caps = {"CHICA": 1000, "GRANDE": 5000}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, caps, {"CHICA": 99, "GRANDE": 99},
        _sin_tiempo())
    assert ("CHICA", "LUNES") not in groups
    assert ("GRANDE", "LUNES") in groups


def test_sin_unidad_disponible_cuando_la_exclusion_deja_la_flota_vacia():
    from logic.convrp_logic import _asignar_unidades
    asign = {1: {"grupo": 1, "unidad": None, "dia": "LUNES", "miembros": [1],
                "unidad_ref": None, "rigidez": "FLEXIBLE",
                "dias_admisibles": ["LUNES"], "unidades_excluidas": ["UNICA"]}}
    pedidos = {1: 100}
    caps = {"UNICA": 5000}                          # la única unidad de la flota, excluida
    exc = _asignar_unidades(asign, pedidos, {}, {}, caps, {}, _sin_tiempo())
    assert asign[1]["unidad"] == "SIN_UNIDAD"
    assert any(e["tipo"] == "SIN_UNIDAD_DISPONIBLE" for e in exc)
    assert asign[1]["unidad"] != "UNICA", \
        "nunca debe caer en la unidad excluida, ni en este caso degenerado"


# ══ 2d. `unidad_forzada` ya no existe como concepto ════════════════════════
def test_unidad_forzada_ya_no_bloquea_el_reparto():
    # Antes `unidad_forzada=True` anclaba la unidad sin importar sobrecupo.
    # Ahora el grupo se re-evalúa por peso como cualquier otro -- si no cabe,
    # cede (y si de verdad no cabe en nadie, cae al último recurso normal).
    plantilla = [_grupo(1, "FLEXIBLE", "LUNES", [1], unidad_ref=None)]
    plantilla[0]["unidad_forzada"] = True
    pedidos = {1: 400}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, {"V1": 1000, "V2": 5000},
        {"V1": 99, "V2": 99}, _sin_tiempo(), kg_mayoristas={1: 700})  # 1100>1000: no cabe en V1
    assert ("V2", "LUNES") in groups, \
        "unidad_forzada ya no debe impedir que el grupo ceda cuando no cabe"


def test_exclusion_tambien_aplica_en_el_ultimo_recurso():
    # El grupo no cabe en NINGUNA unidad no excluida (pesa más que
    # cualquiera); el último recurso (más espacio libre) debe seguir
    # respetando la exclusión, y la pieza que la partición separa también
    # debe reubicarse sin caer en la excluida (vía _unidad_alternativa).
    plantilla = [_grupo(1, "FLEXIBLE", "LUNES", [1, 2], unidad_ref=None)]
    plantilla[0]["unidades_excluidas"] = ["GRANDE"]
    pedidos = {1: 3000, 2: 3000}                    # 6000 kg: no cabe en nadie
    caps = {"CHICA": 1000, "GRANDE": 9000}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, caps, {"CHICA": 99, "GRANDE": 99},
        _sin_tiempo())
    assert ("GRANDE", "LUNES") not in groups, \
        "nunca debe asignar la unidad excluida, ni siquiera en el último recurso"
    assert ("CHICA", "LUNES") in groups            # se parte después, pero arranca aquí


def test_unidad_alternativa_nunca_ofrece_una_excluida():
    from logic.convrp_logic import _unidad_alternativa
    asign = {1: {"grupo": 1, "unidad": "ORIGEN", "dia": "LUNES", "miembros": [1],
                "unidades_excluidas": ["PROHIBIDA"]}}
    a = dict(grupo=2, unidad="ORIGEN", dia="LUNES", miembros=[9],
             unidades_excluidas=["PROHIBIDA"])
    pedidos = {9: 100}
    caps = {"ORIGEN": 1000, "PROHIBIDA": 5000}
    resultado = _unidad_alternativa(asign, a, pedidos, {}, {}, caps, {}, _sin_tiempo())
    assert resultado is None, "PROHIBIDA no debió ofrecerse aunque sea la única con cupo"


def test_unidad_alternativa_prefiere_la_de_menor_capacidad():
    from logic.convrp_logic import _unidad_alternativa
    asign = {}
    a = dict(grupo=1, unidad="ORIGEN", dia="LUNES", miembros=[9],
             unidades_excluidas=None)
    pedidos = {9: 400}
    caps = {"ORIGEN": 100, "CHICA": 500, "GRANDE": 5000}
    resultado = _unidad_alternativa(asign, a, pedidos, {}, {}, caps, {}, _sin_tiempo())
    assert resultado == "CHICA"


def test_pedazo_partido_hereda_unidades_excluidas():
    # El grupo (excluido de GRANDE) pesa más que CHICA y se parte. El pedazo
    # separado, tras partir, debe seguir sin poder consolidarse ni rellenar
    # en GRANDE aunque GRANDE tenga cupo y sea compatible -- si no heredó
    # unidades_excluidas, Palancas 4/5 lo mandarían ahí de todos modos.
    plantilla = [
        _grupo(1, "FLEXIBLE", "LUNES", [1, 2], unidad_ref=None,
               dias_admisibles=["LUNES"]),
        _grupo(2, "FLEXIBLE", "LUNES", [3], unidad_ref=None,
               dias_admisibles=["LUNES"]),
    ]
    plantilla[0]["unidades_excluidas"] = ["GRANDE"]
    pedidos = {1: 900, 2: 900, 3: 100}          # g1 = 1800 > CHICA(1000): se parte
    caps = {"CHICA": 1000, "GRANDE": 5000}
    coocurrencia = {frozenset((1, 2)): 1}       # compatibles: aisla SOLO la exclusion
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, caps, {"CHICA": 99, "GRANDE": 99},
        _sin_tiempo(coocurrencia_grupos=coocurrencia))
    assert not any(e["tipo"] == "CONSOLIDADO_SOLITARIA" and e["a_unidad"] == "GRANDE"
                  for e in exc), \
        "el pedazo partido nunca debe consolidarse en GRANDE (excluida para el grupo 1)"
    for (unidad, dia), miembros in groups.items():
        if unidad == "GRANDE":
            sids = {m["sid"] for m in miembros}
            assert not (sids & {1, 2}), \
                "ninguna sucursal del grupo 1 (excluido de GRANDE) debe terminar ahí"


def test_plantilla_con_unidades_excluidas_las_respeta_en_construir_groups():
    plantilla = [{"grupo": 1, "rigidez": "FLEXIBLE", "dia": "LUNES",
                  "unidad_ref": None, "sucursales": [1, 2],
                  "dias_admisibles": ["LUNES"],
                  "unidades_excluidas": ["GRANDE"]}]
    pedidos = {1: 50, 2: 50}
    caps = {"CHICA": 1000, "GRANDE": 5000}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, caps, {"CHICA": 99, "GRANDE": 99},
        _sin_tiempo())
    assert ("CHICA", "LUNES") in groups
    assert ("GRANDE", "LUNES") not in groups


def test_plantilla_sin_unidades_excluidas_no_rompe():
    # La mayoría de los grupos NO trae unidades_excluidas -- debe comportarse
    # como lista vacía (sin restricción), no explotar con KeyError/TypeError.
    plantilla = [_grupo(1, "FLEXIBLE", "LUNES", [1, 2], unidad_ref=None)]
    pedidos = {1: 50, 2: 50}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, {"V1": 5000}, {"V1": 99}, _sin_tiempo())
    assert ("V1", "LUNES") in groups


# ══ 3. Palanca 1: mover de unidad dentro del mismo día ═════════════════════
def test_sobrecupo_mueve_flexible_a_otra_unidad_del_mismo_dia():
    # V1 no aguanta los dos grupos (1200 kg > 1000); el FLEXIBLE se va a V2.
    plantilla = [_grupo(1, "RIGIDO", "LUNES", [1, 2], unidad_ref="V1"),
                 _grupo(2, "FLEXIBLE", "LUNES", [3, 4], unidad_ref="V1")]
    pedidos = {1: 300, 2: 300, 3: 300, 4: 300}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, {"V1": 1000, "V2": 1000},
        {"V1": 20, "V2": 20}, _sin_tiempo())
    # el rígido conserva su unidad de referencia
    assert sorted(m["sid"] for m in groups[("V1", "LUNES")]) == [1, 2]
    # el flexible se movió de unidad, mismo día
    assert ("V2", "LUNES") in groups
    assert sorted(m["sid"] for m in groups[("V2", "LUNES")]) == [3, 4]


def test_varios_grupos_con_la_misma_unidad_ref_usan_la_flota_sin_partirse():
    # `unidad_ref` es PREFERENCIA, no asignación dura: 4 grupos que la comparten
    # y no caben juntos deben repartirse en la flota libre, SIN partir a nadie.
    plantilla = [_grupo(i, "RIGIDO", "LUNES", [i * 2 - 1, i * 2], unidad_ref="V1",
                        dias_admisibles=["LUNES"]) for i in range(1, 5)]
    pedidos = {i: 400 for i in range(1, 9)}          # 800 kg por grupo
    caps = {f"V{i}": 1000 for i in range(1, 5)}
    vols = {f"V{i}": 99 for i in range(1, 5)}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, caps, vols, _sin_tiempo())
    assert not [e for e in exc if e["tipo"] == "PARTIDO_CAPACIDAD"], \
        "no debe partir ningún grupo habiendo unidades libres"
    assert len(groups) == 4                          # una unidad por grupo
    for ms in groups.values():
        assert len(ms) == 2                          # composición intacta


def test_muchos_grupos_comparten_unidad_ref_no_agota_los_barridos():
    # Caso real: 8 grupos del mismo día comparten `unidad_ref`. El reparto debe
    # resolverse en la ASIGNACIÓN, no a base de barridos de reparación (que
    # están acotados) — si no, se acaban los barridos y se parten rígidos.
    plantilla = [_grupo(i, "RIGIDO", "LUNES", [i * 2 - 1, i * 2], unidad_ref="V1",
                        dias_admisibles=["LUNES"]) for i in range(1, 9)]
    pedidos = {i: 400 for i in range(1, 17)}         # 800 kg por grupo
    caps = {f"V{i}": 1000 for i in range(1, 9)}      # 8 unidades, una por grupo
    vols = {f"V{i}": 99 for i in range(1, 9)}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, caps, vols, _sin_tiempo())
    partidos = [e for e in exc if e["tipo"] == "PARTIDO_CAPACIDAD"]
    assert not partidos, f"no debió partir nada; partió {len(partidos)}"
    assert len(groups) == 8


def test_grupo_desplazado_consolida_en_ruta_existente_no_abre_una_nueva():
    # Al ceder `unidad_ref` hay que sumarse a un viaje YA formado que tenga cupo,
    # no estrenar una unidad vacía: en el histórico un viaje (unidad, día) lleva
    # 1.4 grupos en promedio, no 1.0.
    plantilla = [_grupo(1, "RIGIDO", "LUNES", [1, 2], unidad_ref="V1", dias_admisibles=["LUNES"]),
                 _grupo(2, "RIGIDO", "LUNES", [3, 4], unidad_ref="V1", dias_admisibles=["LUNES"]),
                 _grupo(3, "RIGIDO", "LUNES", [5, 6], unidad_ref="V2", dias_admisibles=["LUNES"])]
    pedidos = {1: 400, 2: 400,      # g1 = 800 → V1
               3: 150, 4: 150,      # g2 = 300 → no cabe en V1; debe ir con g3 a V2
               5: 250, 6: 250}      # g3 = 500 → V2
    caps = {"V1": 1000, "V2": 1000, "V3": 1000}
    vols = {"V1": 99, "V2": 99, "V3": 99}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, caps, vols, _sin_tiempo())
    assert ("V3", "LUNES") not in groups, "no debe estrenar una unidad vacía"
    assert len(groups) == 2
    assert sorted(m["sid"] for m in groups[("V2", "LUNES")]) == [3, 4, 5, 6]


# ══ 4. Palanca 2: mover de día, sólo dentro de los admisibles ══════════════
def test_mueve_a_otro_dia_admisible_cuando_no_hay_unidad_libre():
    # Una sola unidad: no hay palanca de unidad. El flexible admite MARTES.
    plantilla = [_grupo(1, "RIGIDO", "LUNES", [1, 2], unidad_ref="V1"),
                 _grupo(2, "FLEXIBLE", "LUNES", [3, 4], unidad_ref="V1",
                        dias_admisibles=["LUNES", "MARTES"])]
    pedidos = {1: 300, 2: 300, 3: 300, 4: 300}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, {"V1": 1000}, {"V1": 20}, _sin_tiempo())
    assert ("V1", "MARTES") in groups
    assert sorted(m["sid"] for m in groups[("V1", "MARTES")]) == [3, 4]
    assert any(e["tipo"] == "MOVIDO_DIA" for e in exc)


def test_no_mueve_fuera_del_conjunto_admisible():
    # El flexible sólo admite LUNES: no puede moverse de día aunque sature.
    plantilla = [_grupo(1, "RIGIDO", "LUNES", [1, 2], unidad_ref="V1"),
                 _grupo(2, "FLEXIBLE", "LUNES", [3, 4], unidad_ref="V1",
                        dias_admisibles=["LUNES"])]
    pedidos = {1: 300, 2: 300, 3: 300, 4: 300}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, {"V1": 1000}, {"V1": 20}, _sin_tiempo())
    dias = {dia for _, dia in groups}
    assert dias == {"LUNES"}                       # nadie salió de LUNES
    assert not any(e["tipo"] == "MOVIDO_DIA" for e in exc)


def test_rigido_tambien_puede_mover_dia_si_es_admisible():
    # Rigidez de composición y flexibilidad de día son independientes.
    plantilla = [_grupo(1, "RIGIDO", "LUNES", [1, 2], unidad_ref="V1",
                        dias_admisibles=["LUNES", "MARTES"]),
                 _grupo(2, "RIGIDO", "LUNES", [3, 4], unidad_ref="V1",
                        dias_admisibles=["LUNES"])]
    pedidos = {1: 300, 2: 300, 3: 300, 4: 300}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, {"V1": 1000}, {"V1": 20}, _sin_tiempo())
    # el grupo 1 (que admite MARTES) se movió; ninguno se partió
    assert ("V1", "MARTES") in groups
    assert not any(e["tipo"] == "PARTIDO_CAPACIDAD" for e in exc)


# ══ 5. Palanca 3: partir rígido, último recurso y determinista ═════════════
def test_parte_rigido_solo_si_ninguna_palanca_alcanza():
    # Un solo rígido, una sola unidad, un solo día: no cabe → hay que partir.
    plantilla = [_grupo(1, "RIGIDO", "LUNES", [1, 2, 3], unidad_ref="V1",
                        dias_admisibles=["LUNES"])]
    pedidos = {1: 500, 2: 400, 3: 300}                   # 1200 > 1000
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, {"V1": 1000}, {"V1": 99}, _sin_tiempo())
    part = [e for e in exc if e["tipo"] == "PARTIDO_CAPACIDAD"]
    assert part, "debió registrarse la partición como excepción explícita"
    assert part[0]["grupo"] == 1
    assert part[0]["restriccion"] == "PESO"


def test_particion_de_rigido_es_determinista_pela_el_mayor():
    # Debe pelar la sucursal que más reduce el sobrecupo (mayor kg), no la primera.
    plantilla = [_grupo(1, "RIGIDO", "LUNES", [1, 2, 3], unidad_ref="V1",
                        dias_admisibles=["LUNES"])]
    pedidos = {1: 500, 2: 400, 3: 300}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, {"V1": 1000}, {"V1": 99}, _sin_tiempo())
    part = [e for e in exc if e["tipo"] == "PARTIDO_CAPACIDAD"][0]
    assert part["sucursales_separadas"] == [1]          # 500 kg: el mayor


def test_grupo_mas_pesado_que_toda_unidad_se_parte_minimamente():
    # g1 pesa más que cualquier vehículo: hay que partirlo sí o sí. Debe
    # colocarse en la unidad MÁS VACÍA (no sobre una ya cargada) y pelar lo
    # MÍNIMO — no arrastrar medio grupo por caer en una unidad ocupada.
    plantilla = [_grupo(1, "FLEXIBLE", "LUNES", [1, 2, 3], unidad_ref="V1",
                        dias_admisibles=["LUNES"]),
                 _grupo(2, "RIGIDO", "LUNES", [4, 5], unidad_ref="V1",
                        dias_admisibles=["LUNES"])]
    pedidos = {1: 900, 2: 400, 3: 400, 4: 300, 5: 300}   # g1 = 1700 > cap 1000
    caps = {"V1": 1000, "V2": 1000, "V3": 1000}
    vols = {"V1": 99, "V2": 99, "V3": 99}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, caps, vols, _sin_tiempo())
    part = [e for e in exc if e["tipo"] == "PARTIDO_CAPACIDAD"]
    assert len(part) == 1 and part[0]["grupo"] == 1
    # pela sólo la sucursal más pesada (900); el resto (800) ya cabe
    assert part[0]["sucursales_separadas"] == [1]
    assert part[0]["destino_unidad"] is not None      # se reubica de verdad
    # el rígido conserva su composición completa
    assert any(sorted(m["sid"] for m in ms) == [4, 5] for ms in groups.values())


def test_particion_registra_toda_restriccion_que_ato_durante_el_pelado():
    # El modelo de TIEMPO sobrestima en rutas de muchas paradas chicas. Si el
    # pelado continúa por TIEMPO aunque la restricción inicial fuera PESO, la
    # excepción debe dejarlo asentado — así se distingue alivio real de fantasma.
    plantilla = [_grupo(1, "FLEXIBLE", "LUNES", [1, 2, 3, 4], unidad_ref="V1",
                        dias_admisibles=["LUNES"])]
    pedidos = {1: 600, 2: 200, 3: 200, 4: 200}       # 1200 > cap 1000 → PESO
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, {"V1": 1000}, {"V1": 99},
        _cfg(chequear_tiempo=True, hora_salida_min=420, hora_cierre_min=460))
    part = [e for e in exc if e["tipo"] == "PARTIDO_CAPACIDAD"]
    assert part
    assert "restricciones_durante_particion" in part[0]
    assert "TIEMPO" in part[0]["restricciones_durante_particion"]


def test_particion_desempata_por_num_tienda_ascendente():
    # Dos sucursales con el mismo peso: gana el num_tienda menor, no "la que caiga".
    plantilla = [_grupo(1, "RIGIDO", "LUNES", [7, 3, 5], unidad_ref="V1",
                        dias_admisibles=["LUNES"])]
    pedidos = {7: 400, 3: 400, 5: 400}                   # 1200 > 1000, empate
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, {"V1": 1000}, {"V1": 99}, _sin_tiempo())
    part = [e for e in exc if e["tipo"] == "PARTIDO_CAPACIDAD"][0]
    assert part["sucursales_separadas"] == [3]


def test_tiempo_solo_no_parte_un_rigido():
    # RIGIDO con 8 paradas chicas: peso y volumen sobran de holgura: la
    # ventana horaria es tan corta que SOLO el modelo de tiempo ata. No
    # debe partirse -- composición intacta, aviso registrado en vez de
    # PARTIDO_CAPACIDAD.
    plantilla = [_grupo(1, "RIGIDO", "LUNES", list(range(1, 9)),
                        unidad_ref="V1", dias_admisibles=["LUNES"])]
    pedidos = {i: 10 for i in range(1, 9)}          # peso irrelevante
    cfg = _cfg(chequear_tiempo=True, hora_salida_min=420, hora_cierre_min=430)
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, {"V1": 99999}, {"V1": 9999}, cfg)
    assert not any(e["tipo"] == "PARTIDO_CAPACIDAD" for e in exc), \
        "TIEMPO solo no debe partir un RIGIDO"
    assert len(groups[("V1", "LUNES")]) == 8, \
        "la composición del RIGIDO debe seguir completa"
    assert any(e["tipo"] == "AVISO_TIEMPO_RIGIDO_NO_PARTIDO" for e in exc)


# ══ 6. Determinismo ════════════════════════════════════════════════════════
def _escenario_grande():
    plantilla = [
        _grupo(1, "RIGIDO", "LUNES", [1, 2, 3], unidad_ref="V1", dias_admisibles=["LUNES", "MARTES"]),
        _grupo(2, "FLEXIBLE", "LUNES", [4, 5], unidad_ref="V1", dias_admisibles=["LUNES", "MARTES"]),
        _grupo(3, "RIGIDO", "MARTES", [6, 7, 8], unidad_ref="V2", dias_admisibles=["MARTES"]),
        _grupo(4, "FLEXIBLE", "MARTES", [9, 10], unidad_ref="V2", dias_admisibles=["MARTES", "MIERCOLES"]),
        _grupo(5, "RIGIDO", "MIERCOLES", [11, 12], unidad_ref="V1", dias_admisibles=["MIERCOLES"]),
    ]
    pedidos = {i: 200 + (i * 37) % 300 for i in range(1, 13)}
    caps = {"V1": 1500, "V2": 1500}
    vols = {"V1": 20, "V2": 20}
    return plantilla, pedidos, caps, vols


def test_determinismo_dos_corridas_identicas():
    plantilla, pedidos, caps, vols = _escenario_grande()
    g1, e1 = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, caps, vols, _sin_tiempo())
    g2, e2 = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, caps, vols, _sin_tiempo())
    norm = lambda g: sorted((k, sorted(m["sid"] for m in v)) for k, v in g.items())
    assert norm(g1) == norm(g2)
    assert e1 == e2


def test_determinismo_no_depende_del_orden_de_entrada():
    plantilla, pedidos, caps, vols = _escenario_grande()
    g1, e1 = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, caps, vols, _sin_tiempo())
    # mismos datos, orden de plantilla y de pedidos invertido
    g2, e2 = construir_groups_desde_plantilla(
        dict(reversed(list(pedidos.items()))), {}, COORDS,
        list(reversed(plantilla)), caps, vols, _sin_tiempo())
    norm = lambda g: sorted((k, sorted(m["sid"] for m in v)) for k, v in g.items())
    assert norm(g1) == norm(g2)
    assert e1 == e2


# ══ 7. Cascada acotada ═════════════════════════════════════════════════════
def test_cascada_de_dias_esta_acotada_y_termina():
    # Cadena: cada día admite el siguiente; mover uno satura el próximo.
    # Debe terminar (no colgarse) y respetar el tope de iteraciones.
    plantilla = [
        _grupo(1, "FLEXIBLE", "LUNES", [1, 2], unidad_ref="V1", dias_admisibles=["LUNES", "MARTES"]),
        _grupo(2, "FLEXIBLE", "MARTES", [3, 4], unidad_ref="V1", dias_admisibles=["MARTES", "MIERCOLES"]),
        _grupo(3, "FLEXIBLE", "MIERCOLES", [5, 6], unidad_ref="V1", dias_admisibles=["MIERCOLES", "JUEVES"]),
        _grupo(4, "RIGIDO", "LUNES", [7, 8], unidad_ref="V1", dias_admisibles=["LUNES"]),
    ]
    pedidos = {i: 300 for i in range(1, 9)}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, {"V1": 1000}, {"V1": 99},
        _sin_tiempo(max_iteraciones=2))
    # termina, no pierde sucursales, y no excede el tope de barridos
    sids = sorted(m["sid"] for ms in groups.values() for m in ms)
    assert sids == list(range(1, 9))
    assert all(e.get("iteracion", 0) <= 2 for e in exc)


def test_ninguna_sucursal_se_pierde_ni_se_duplica():
    plantilla, pedidos, caps, vols = _escenario_grande()
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, caps, vols, _sin_tiempo())
    sids = [m["sid"] for ms in groups.values() for m in ms]
    assert sorted(sids) == sorted(pedidos)
    assert len(sids) == len(set(sids))


# ══ 7b. Horario POR DÍA (config_dias: el lunes sale 11:00, no 07:00) ═══════
def test_usa_horario_por_dia_cuando_se_indica():
    # Mismo grupo, mismo peso: el LUNES (salida 11:00) el tiempo ata; el MARTES
    # (salida 07:00) no. Cablear 07:00 para todos regala 4 h los lunes.
    plantilla_lun = [_grupo(1, "FLEXIBLE", "LUNES", [1, 2, 3, 4], unidad_ref="V1",
                            dias_admisibles=["LUNES"])]
    plantilla_mar = [_grupo(1, "FLEXIBLE", "MARTES", [1, 2, 3, 4], unidad_ref="V1",
                            dias_admisibles=["MARTES"])]
    pedidos = {1: 10, 2: 10, 3: 10, 4: 10}
    horarios = {"LUNES": (660, 1200), "MARTES": (420, 1200)}   # 11:00 / 07:00
    cfg = _cfg(chequear_tiempo=True, horarios_por_dia=horarios,
               hora_cierre_min=1200, aviso_paradas=None)
    # ~40 min de descarga por parada + traslados: con salida 11:00 no alcanza
    cfg_corto = dict(cfg, horarios_por_dia={"LUNES": (660, 800), "MARTES": (420, 800)})
    _, exc_lun = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla_lun, {"V1": 9999}, {"V1": 99}, cfg_corto)
    _, exc_mar = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla_mar, {"V1": 9999}, {"V1": 99}, cfg_corto)
    tarde_lun = [e for e in exc_lun if e.get("restriccion") == "TIEMPO"]
    tarde_mar = [e for e in exc_mar if e.get("restriccion") == "TIEMPO"]
    assert tarde_lun, "el lunes (salida 11:00) el tiempo debe atar"
    assert not tarde_mar, "el martes (salida 07:00) el tiempo no debe atar"


def test_horario_por_dia_cae_al_default_si_no_hay_entrada():
    plantilla = [_grupo(1, "FLEXIBLE", "SABADO", [1, 2], unidad_ref="V1",
                        dias_admisibles=["SABADO"])]
    pedidos = {1: 10, 2: 10}
    cfg = _cfg(chequear_tiempo=True, horarios_por_dia={"LUNES": (660, 1200)})
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, {"V1": 9999}, {"V1": 99}, cfg)
    assert ("V1", "SABADO") in groups          # usa el default, no revienta


# ══ 8. Aviso de paradas (no bloquea) ═══════════════════════════════════════
def test_aviso_de_ruta_larga_no_bloquea():
    plantilla = [_grupo(1, "FLEXIBLE", "LUNES", list(range(1, 13)),
                        unidad_ref="V1", dias_admisibles=["LUNES"])]
    pedidos = {i: 10 for i in range(1, 13)}              # 12 paradas, poco peso
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, {"V1": 9999}, {"V1": 99},
        _sin_tiempo(aviso_paradas=10))
    assert len(groups[("V1", "LUNES")]) == 12            # no se partió
    avisos = [e for e in exc if e["tipo"] == "AVISO_RUTA_LARGA"]
    assert avisos and avisos[0]["paradas"] == 12


# ══ 9. Origen de la carga que disparó cada excepción ═══════════════════════
def test_kg_mayoristas_cuenta_para_la_capacidad():
    plantilla = [_grupo(1, "RIGIDO", "LUNES", [1, 2], unidad_ref="V1",
                        dias_admisibles=["LUNES"])]
    pedidos = {1: 400, 2: 400}
    # sin mayoristas cabe (800 <= 1000); con ellos no (1400 > 1000)
    g1, e1 = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, {"V1": 1000}, {"V1": 99}, _sin_tiempo())
    assert not [e for e in e1 if e["tipo"] == "PARTIDO_CAPACIDAD"]
    g2, e2 = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, {"V1": 1000}, {"V1": 99}, _sin_tiempo(),
        kg_mayoristas={1: 600})
    assert [e for e in e2 if e["tipo"] == "PARTIDO_CAPACIDAD"]


# ═══════════════════════════════════════════════════════════════════════════
# Un grupo más pesado que cualquier vehículo
#
# El g1 de la semana del 6-10 abril pesa 3,981 kg y la unidad más grande de la
# flota son 3,900: no cabe entero en NADA. El reparto lo mandaba a la unidad con
# menos carga del día — que resultó ser un T 25 de 1,300 kg — y la partición
# posterior no encontraba destino para lo pelado, así que la ruta se quedaba al
# 306 %. "Más vacía" tiene que significar MÁS ESPACIO LIBRE, no menos kilos
# encima: si el grupo no cabe en ningún lado, va donde quepa la mayor parte.
# ═══════════════════════════════════════════════════════════════════════════

def test_grupo_que_no_cabe_en_ninguna_unidad_va_a_la_de_mas_espacio_libre():
    # Réplica del 6-10 abril: el g1 no cabe entero en NADA, así que el reparto
    # cae al último recurso. Con "la de menos carga" acababa en Z_CHICA sólo
    # porque todas estaban en cero y desempató el nombre; para cuando la
    # partición corría, las grandes ya estaban ocupadas y lo pelado no tenía a
    # dónde ir. La demanda del día SÍ cabe en la flota: es reparto, no falta de
    # camiones.
    plantilla = [
        {"grupo": 1, "rigidez": "FLEXIBLE", "dia": "LUNES", "unidad_ref": "A",
         "sucursales": [1, 2, 3, 4], "dias_admisibles": ["LUNES"]},
        {"grupo": 2, "rigidez": "FLEXIBLE", "dia": "LUNES", "unidad_ref": "A",
         "sucursales": [5, 6], "dias_admisibles": ["LUNES"]},
        {"grupo": 3, "rigidez": "FLEXIBLE", "dia": "LUNES", "unidad_ref": "B",
         "sucursales": [7, 8], "dias_admisibles": ["LUNES"]},
    ]
    pedidos = {1: 1200, 2: 1000, 3: 900, 4: 881,     # g1 = 3,981 kg
               5: 1900, 6: 1900,                     # g2 = 3,800 kg
               7: 1200, 8: 1200}                     # g3 = 2,400 kg
    caps = {"A": 3900, "B": 3900, "M": 2500, "Z_CHICA": 1300}   # 11,600 kg
    cfg = dict(cfg_por_defecto(), chequear_tiempo=False)
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, {}, plantilla, caps, {}, cfg)
    for (unidad, dia), miembros in groups.items():
        kg = sum(pedidos[m["sid"]] for m in miembros)
        assert kg <= caps[unidad], f"{unidad}/{dia} quedó con {kg} sobre {caps[unidad]}"
    assert any(e["tipo"] == "PARTIDO_CAPACIDAD" for e in exc)


def test_lo_pelado_al_partir_no_se_queda_en_la_misma_ruta():
    # Si la parte separada vuelve a la ruta que ya estaba saturada, la partición
    # no alivió nada y la ruta sale por encima del 100 % igual.
    plantilla = [{"grupo": 1, "rigidez": "RIGIDO", "dia": "LUNES",
                  "unidad_ref": "CHICA", "sucursales": [1, 2],
                  "dias_admisibles": ["LUNES"]}]
    pedidos = {1: 900, 2: 900}
    caps = {"CHICA": 1000, "OTRA": 1000}
    cfg = dict(cfg_por_defecto(), chequear_tiempo=False)
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, {}, plantilla, caps, {}, cfg)
    assert len(groups) == 2                        # se repartió en dos viajes
    for (unidad, _), miembros in groups.items():
        assert sum(pedidos[m["sid"]] for m in miembros) <= caps[unidad]


# ═══════════════════════════════════════════════════════════════════════════
# Coocurrencia al ceder unidad/día — encontrado en producción el 2026-08-10:
# el grupo 19 (Carlos A. Carrillo 2 + Amatitlán) no cabía en F 350_1/JUEVES
# por TIEMPO y el motor lo cedía a F 350_3, que esa semana ya llevaba Jalapa
# de Díaz — un camión que en la realidad nunca hace esa combinación.
#
# Primer intento: filtrar por distancia. Descartado con datos reales: hay
# pares que SÍ viajaron juntos a 84 km y una pareja (grupos 19/22) que NUNCA
# coincidió a sólo 58 km — la distancia no discrimina bien en ningún sentido.
# El reparto usa el historial real de qué grupos compartieron camión-día.
# ═══════════════════════════════════════════════════════════════════════════

def test_al_ceder_unidad_prefiere_la_compatible_por_coocurrencia():
    plantilla = [
        _grupo(1, "RIGIDO", "LUNES", [1, 2], unidad_ref="V1", dias_admisibles=["LUNES"]),
        _grupo(2, "FLEXIBLE", "LUNES", [5, 6], unidad_ref="V2", dias_admisibles=["LUNES"]),
        _grupo(3, "FLEXIBLE", "LUNES", [3, 4], unidad_ref="V3", dias_admisibles=["LUNES"]),
    ]
    # kg: grupo2 (2000) > grupo3 (1900) > grupo1, el que cede (1800) -- los
    # dos primeros ya están asignados a su unidad_ref cuando toca repartir el
    # que cede, así que sin coocurrencia "otras" preferiría V2 por venir más
    # cargada (2000>1900). Sólo grupo1-grupo3 comparten historia.
    pedidos = {1: 900, 2: 900, 3: 950, 4: 950, 5: 1000, 6: 1000}
    caps = {"V1": 1000, "V2": 5000, "V3": 5000}
    coocurrencia = {frozenset((1, 3)): 2}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, {}, plantilla, caps, {}, _sin_tiempo(coocurrencia_grupos=coocurrencia))
    assert sorted(m["sid"] for m in groups[("V3", "LUNES")]) == [1, 2, 3, 4]
    assert sorted(m["sid"] for m in groups[("V2", "LUNES")]) == [5, 6]


def test_al_ceder_unidad_coocurrencia_cede_si_es_la_unica_opcion():
    # Sin V3 (la opción con precedente), el grupo 1 debe ir a V2 igual --
    # mejor una combinación sin historia que sin camión.
    plantilla = [
        _grupo(1, "RIGIDO", "LUNES", [1, 2], unidad_ref="V1", dias_admisibles=["LUNES"]),
        _grupo(2, "FLEXIBLE", "LUNES", [5, 6], unidad_ref="V2", dias_admisibles=["LUNES"]),
    ]
    pedidos = {1: 900, 2: 900, 5: 1000, 6: 1000}
    caps = {"V1": 1000, "V2": 5000}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, {}, plantilla, caps, {}, _sin_tiempo(coocurrencia_grupos={}))
    assert sorted(m["sid"] for m in groups[("V2", "LUNES")]) == [1, 2, 5, 6]


def test_dia_alternativo_prefiere_destino_compatible_por_coocurrencia():
    # _dia_alternativo aislada: MARTES ya tiene el grupo 3 en V1 (sin
    # precedente con el que se mueve) y el grupo 4 en V2 (sí lo tiene).
    from logic.convrp_logic import _dia_alternativo
    asign = {
        3: {"grupo": 3, "unidad": "V1", "dia": "MARTES", "miembros": [7, 8],
            "unidad_ref": "V1", "rigidez": "RIGIDO", "dias_admisibles": ["MARTES"]},
        4: {"grupo": 4, "unidad": "V2", "dia": "MARTES", "miembros": [9, 10],
            "unidad_ref": "V2", "rigidez": "RIGIDO", "dias_admisibles": ["MARTES"]},
    }
    a = {"grupo": 2, "unidad": "V1", "dia": "LUNES", "miembros": [3, 4],
         "unidad_ref": "V1", "rigidez": "FLEXIBLE", "dias_admisibles": ["LUNES", "MARTES"]}
    pedidos = {3: 300, 4: 300, 7: 100, 8: 100, 9: 150, 10: 150}
    caps = {"V1": 1000, "V2": 1000}
    coocurrencia = {frozenset((2, 4)): 1}
    resultado = _dia_alternativo(asign, a, pedidos, {}, {}, caps, {},
                                 _sin_tiempo(coocurrencia_grupos=coocurrencia))
    assert resultado == ("MARTES", "V2")


def test_dia_alternativo_coocurrencia_cede_si_es_la_unica_opcion():
    from logic.convrp_logic import _dia_alternativo
    asign = {3: {"grupo": 3, "unidad": "V1", "dia": "MARTES", "miembros": [7, 8],
                "unidad_ref": "V1", "rigidez": "RIGIDO", "dias_admisibles": ["MARTES"]}}
    a = {"grupo": 2, "unidad": "V1", "dia": "LUNES", "miembros": [3, 4],
         "unidad_ref": "V1", "rigidez": "FLEXIBLE", "dias_admisibles": ["LUNES", "MARTES"]}
    pedidos = {3: 300, 4: 300, 7: 100, 8: 100}
    resultado = _dia_alternativo(asign, a, pedidos, {}, {}, {"V1": 1000}, {},
                                 _sin_tiempo(coocurrencia_grupos={}))
    assert resultado == ("MARTES", "V1")          # única unidad: se acepta sin precedente


def test_dia_alternativo_nunca_ofrece_una_excluida():
    from logic.convrp_logic import _dia_alternativo
    asign = {}
    a = {"grupo": 1, "unidad": "ORIGEN", "dia": "LUNES", "miembros": [1],
         "unidad_ref": "PROHIBIDA", "rigidez": "FLEXIBLE",
         "dias_admisibles": ["LUNES", "MARTES"], "unidades_excluidas": ["PROHIBIDA"]}
    pedidos = {1: 100}
    caps = {"ORIGEN": 1000, "PROHIBIDA": 5000}
    resultado = _dia_alternativo(asign, a, pedidos, {}, {}, caps, {}, _sin_tiempo())
    assert resultado is None or resultado[1] != "PROHIBIDA"


# ═══════════════════════════════════════════════════════════════════════════
# Palanca 4 — ninguna ruta se queda con una sola sucursal, salvo que el
# vehículo ya esté al límite de su capacidad (peso Lores + mayoristas).
# Regla de negocio explícita del 2026-08-11, encontrada al revisar viajes
# como San Bartolo (K16, 1 sola sucursal) en la logística real.
# ═══════════════════════════════════════════════════════════════════════════

def test_solitaria_se_consolida_en_ruta_activa_compatible_con_cupo():
    # Sin preferencia, la selección por peso ya consolida ambos grupos en la
    # MISMA unidad desde la asignación inicial (grupo 2, más pesado, procesa
    # primero y grupo 1 -- compatible por coocurrencia -- se suma a esa ruta
    # por el desempate de consolidación) -- Palanca 4 ni siquiera necesita
    # intervenir, así que CONSOLIDADO_SOLITARIA no se emite. El comportamiento
    # de negocio protegido (el grupo 1 nunca queda solo pudiendo compartir
    # camión con precedente histórico) sigue intacto, verificado corriendo
    # el motor real.
    plantilla = [_grupo(1, "FLEXIBLE", "LUNES", [1], unidad_ref="V1"),
                 _grupo(2, "RIGIDO", "LUNES", [2, 3], unidad_ref="V2")]
    pedidos = {1: 100, 2: 300, 3: 300}
    caps = {"V1": 1000, "V2": 1000}
    coocurrencia = {frozenset((1, 2)): 1}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, {}, plantilla, caps, {}, _sin_tiempo(coocurrencia_grupos=coocurrencia))
    assert len(groups) == 1, "el grupo 1 nunca debió quedar solo en su propia ruta"
    assert sorted(m["sid"] for ms in groups.values() for m in ms) == [1, 2, 3]


def test_solitaria_no_se_mueve_si_ya_esta_al_limite_de_capacidad():
    plantilla = [_grupo(1, "FLEXIBLE", "LUNES", [1], unidad_ref="V1"),
                 _grupo(2, "RIGIDO", "LUNES", [2, 3], unidad_ref="V2")]
    pedidos = {1: 1000, 2: 300, 3: 300}          # grupo 1 sola ya = cap de V1
    caps = {"V1": 1000, "V2": 1000}
    coocurrencia = {frozenset((1, 2)): 1}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, {}, plantilla, caps, {}, _sin_tiempo(coocurrencia_grupos=coocurrencia))
    assert sorted(m["sid"] for m in groups[("V1", "LUNES")]) == [1]
    assert not any(e["tipo"] == "CONSOLIDADO_SOLITARIA" for e in exc)


def test_solitaria_sin_ruta_activa_ese_dia_queda_como_aviso():
    plantilla = [_grupo(1, "FLEXIBLE", "LUNES", [1], unidad_ref="V1")]
    pedidos = {1: 100}
    caps = {"V1": 1000, "V2": 1000}              # V2 existe pero sin ninguna ruta ese día
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, {}, plantilla, caps, {}, _sin_tiempo())
    assert ("V1", "LUNES") in groups
    assert ("V2", "LUNES") not in groups         # nunca se estrena una unidad vacía
    assert any(e["tipo"] == "AVISO_RUTA_SOLITARIA" for e in exc)


def test_solitaria_respeta_coocurrencia_aunque_haya_cupo():
    # Sin preferencia, el grupo 2 (más pesado) procesa primero y se lleva la
    # unidad que ordena primero alfabéticamente (V1); el grupo 1, sin ningún
    # precedente histórico con el 2, no puede sumarse ahí y queda solo en la
    # OTRA unidad (V2) -- el nombre de unidad cambia respecto al viejo orden
    # por preferencia, pero el comportamiento protegido (nunca comparte
    # camión sin precedente) sigue intacto, verificado corriendo el motor.
    plantilla = [_grupo(1, "FLEXIBLE", "LUNES", [1], unidad_ref="V1"),
                 _grupo(2, "RIGIDO", "LUNES", [2, 3], unidad_ref="V2")]
    pedidos = {1: 100, 2: 300, 3: 300}
    caps = {"V1": 1000, "V2": 1000}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, {}, plantilla, caps, {}, _sin_tiempo(coocurrencia_grupos={}))
    assert sorted(m["sid"] for m in groups[("V2", "LUNES")]) == [1]     # nunca coincidieron
    assert any(e["tipo"] == "AVISO_RUTA_SOLITARIA" for e in exc)


def test_solitaria_considera_mayoristas_al_evaluar_el_limite():
    # Lores solo (100 kg) deja mucho margen en la unidad que le toca, pero con
    # los mayoristas ya anclados (900 kg) llega al límite -- no debe moverse.
    # Mismo cambio de nombre de unidad que el test anterior (V2 en vez de V1,
    # por el nuevo orden de procesamiento por peso), comportamiento protegido
    # intacto.
    plantilla = [_grupo(1, "FLEXIBLE", "LUNES", [1], unidad_ref="V1"),
                 _grupo(2, "RIGIDO", "LUNES", [2, 3], unidad_ref="V2")]
    pedidos = {1: 100, 2: 300, 3: 300}
    caps = {"V1": 1000, "V2": 1000}
    coocurrencia = {frozenset((1, 2)): 1}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, {}, plantilla, caps, {}, _sin_tiempo(coocurrencia_grupos=coocurrencia),
        kg_mayoristas={1: 900})
    assert sorted(m["sid"] for m in groups[("V2", "LUNES")]) == [1]
    assert not any(e["tipo"] == "CONSOLIDADO_SOLITARIA" for e in exc)


def test_dos_solitarias_compatibles_se_consolidan_entre_si():
    plantilla = [_grupo(1, "FLEXIBLE", "LUNES", [1], unidad_ref="V1"),
                 _grupo(2, "FLEXIBLE", "LUNES", [2], unidad_ref="V2")]
    pedidos = {1: 100, 2: 100}
    caps = {"V1": 1000, "V2": 1000}
    coocurrencia = {frozenset((1, 2)): 1}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, {}, plantilla, caps, {}, _sin_tiempo(coocurrencia_grupos=coocurrencia))
    assert len(groups) == 1                       # una sola ruta con las dos
    assert sorted(next(iter(groups.values())), key=lambda m: m["sid"])[0]["sid"] == 1
    assert sum(len(v) for v in groups.values()) == 2


# ══ 10. Palanca 5: relleno de capacidad libre ═══════════════════════════════
def test_cfg_por_defecto_incluye_relleno_capacidad_activado():
    assert cfg_por_defecto()["relleno_capacidad"] is True


def test_relleno_capacidad_mueve_grupo_compatible_a_ruta_con_espacio():
    from logic.convrp_logic import _rellenar_capacidad_libre
    asign = {
        1: {"grupo": 1, "unidad": "V1", "dia": "LUNES", "miembros": [1, 2],
            "unidad_ref": "V1", "dia_preferido": "LUNES", "rigidez": "RIGIDO",
            "dias_admisibles": ["LUNES"]},
        2: {"grupo": 2, "unidad": "V2", "dia": "LUNES", "miembros": [3, 4],
            "unidad_ref": "V3", "dia_preferido": "MARTES", "rigidez": "FLEXIBLE",
            "dias_admisibles": ["LUNES"]},           # desviado de su hogar V3/MARTES
    }
    pedidos = {1: 100, 2: 100, 3: 100, 4: 100}
    caps = {"V1": 1000, "V2": 1000, "V3": 1000}
    coocurrencia = {frozenset((1, 2)): 1}
    exc = _rellenar_capacidad_libre(
        asign, pedidos, {}, {}, caps, {}, _sin_tiempo(coocurrencia_grupos=coocurrencia), {})
    assert asign[2]["unidad"] == "V1" and asign[2]["dia"] == "LUNES"
    assert any(e["tipo"] == "RELLENO_CAPACIDAD_LIBRE" for e in exc)


def test_relleno_capacidad_no_mueve_sin_coocurrencia():
    from logic.convrp_logic import _rellenar_capacidad_libre
    asign = {
        1: {"grupo": 1, "unidad": "V1", "dia": "LUNES", "miembros": [1, 2],
            "unidad_ref": "V1", "dia_preferido": "LUNES", "rigidez": "RIGIDO",
            "dias_admisibles": ["LUNES"]},
        2: {"grupo": 2, "unidad": "V2", "dia": "LUNES", "miembros": [3, 4],
            "unidad_ref": "V3", "dia_preferido": "MARTES", "rigidez": "FLEXIBLE",
            "dias_admisibles": ["LUNES"]},
    }
    pedidos = {1: 100, 2: 100, 3: 100, 4: 100}
    caps = {"V1": 1000, "V2": 1000, "V3": 1000}
    exc = _rellenar_capacidad_libre(
        asign, pedidos, {}, {}, caps, {}, _sin_tiempo(coocurrencia_grupos={}), {})
    assert asign[2]["unidad"] == "V2" and asign[2]["dia"] == "LUNES"
    assert not exc


def test_relleno_capacidad_no_mueve_grupo_con_unidad_forzada():
    from logic.convrp_logic import _rellenar_capacidad_libre
    asign = {
        1: {"grupo": 1, "unidad": "V1", "dia": "LUNES", "miembros": [1, 2],
            "unidad_ref": "V1", "dia_preferido": "LUNES", "rigidez": "RIGIDO",
            "dias_admisibles": ["LUNES"]},
        2: {"grupo": 2, "unidad": "V2", "dia": "LUNES", "miembros": [3, 4],
            "unidad_ref": "V3", "dia_preferido": "MARTES", "rigidez": "FLEXIBLE",
            "dias_admisibles": ["LUNES"], "unidad_forzada": True},
    }
    pedidos = {1: 100, 2: 100, 3: 100, 4: 100}
    caps = {"V1": 1000, "V2": 1000, "V3": 1000}
    coocurrencia = {frozenset((1, 2)): 1}
    exc = _rellenar_capacidad_libre(
        asign, pedidos, {}, {}, caps, {}, _sin_tiempo(coocurrencia_grupos=coocurrencia), {})
    assert asign[2]["unidad"] == "V2"
    assert not exc


def test_relleno_capacidad_no_mueve_fuera_de_dias_admisibles():
    from logic.convrp_logic import _rellenar_capacidad_libre
    asign = {
        1: {"grupo": 1, "unidad": "V1", "dia": "LUNES", "miembros": [1, 2],
            "unidad_ref": "V1", "dia_preferido": "LUNES", "rigidez": "RIGIDO",
            "dias_admisibles": ["LUNES"]},
        2: {"grupo": 2, "unidad": "V2", "dia": "LUNES", "miembros": [3, 4],
            "unidad_ref": "V3", "dia_preferido": "MARTES", "rigidez": "FLEXIBLE",
            "dias_admisibles": ["MARTES"]},          # LUNES no es admisible para él
    }
    pedidos = {1: 100, 2: 100, 3: 100, 4: 100}
    caps = {"V1": 1000, "V2": 1000, "V3": 1000}
    coocurrencia = {frozenset((1, 2)): 1}
    exc = _rellenar_capacidad_libre(
        asign, pedidos, {}, {}, caps, {}, _sin_tiempo(coocurrencia_grupos=coocurrencia), {})
    assert asign[2]["unidad"] == "V2"
    assert not exc


def test_relleno_capacidad_no_mueve_si_no_cabe_por_peso():
    from logic.convrp_logic import _rellenar_capacidad_libre
    asign = {
        1: {"grupo": 1, "unidad": "V1", "dia": "LUNES", "miembros": [1, 2],
            "unidad_ref": "V1", "dia_preferido": "LUNES", "rigidez": "RIGIDO",
            "dias_admisibles": ["LUNES"]},
        2: {"grupo": 2, "unidad": "V2", "dia": "LUNES", "miembros": [3, 4],
            "unidad_ref": "V3", "dia_preferido": "MARTES", "rigidez": "FLEXIBLE",
            "dias_admisibles": ["LUNES"]},           # desviado, compatible, admisible... pero no cabe
    }
    pedidos = {1: 800, 2: 800, 3: 500, 4: 500}       # destino ya lleva 1600; candidato pesa 1000
    caps = {"V1": 2000, "V2": 5000, "V3": 5000}      # 1600 + 1000 = 2600 > 2000: no cabe
    coocurrencia = {frozenset((1, 2)): 1}
    exc = _rellenar_capacidad_libre(
        asign, pedidos, {}, {}, caps, {}, _sin_tiempo(coocurrencia_grupos=coocurrencia), {})
    assert asign[2]["unidad"] == "V2"               # no se movió: no cabía por peso
    assert not exc


def test_relleno_capacidad_no_mueve_grupo_que_ya_esta_en_su_hogar():
    from logic.convrp_logic import _rellenar_capacidad_libre
    asign = {
        1: {"grupo": 1, "unidad": "V1", "dia": "LUNES", "miembros": [1, 2],
            "unidad_ref": "V1", "dia_preferido": "LUNES", "rigidez": "RIGIDO",
            "dias_admisibles": ["LUNES"]},
        2: {"grupo": 2, "unidad": "V2", "dia": "LUNES", "miembros": [3, 4],
            "unidad_ref": "V2", "dia_preferido": "LUNES", "rigidez": "FLEXIBLE",
            "dias_admisibles": ["LUNES"]},           # ya está en SU propio hogar
    }
    pedidos = {1: 100, 2: 100, 3: 100, 4: 100}
    caps = {"V1": 1000, "V2": 1000}
    coocurrencia = {frozenset((1, 2)): 1}
    exc = _rellenar_capacidad_libre(
        asign, pedidos, {}, {}, caps, {}, _sin_tiempo(coocurrencia_grupos=coocurrencia), {})
    assert asign[2]["unidad"] == "V2"              # nunca se toca: no está desviado
    assert not exc


def test_relleno_capacidad_prioriza_regreso_a_casa_sobre_maximizar_ocupacion():
    from logic.convrp_logic import _rellenar_capacidad_libre
    asign = {
        1: {"grupo": 1, "unidad": "V1", "dia": "LUNES", "miembros": [1],
            "unidad_ref": "V1", "dia_preferido": "LUNES", "rigidez": "RIGIDO",
            "dias_admisibles": ["LUNES"]},
        2: {"grupo": 2, "unidad": "V2", "dia": "LUNES", "miembros": [2],
            "unidad_ref": "V3", "dia_preferido": "MIERCOLES", "rigidez": "FLEXIBLE",
            "dias_admisibles": ["LUNES"]},           # desviado, NO es hogar de V1/LUNES, kg alto
        3: {"grupo": 3, "unidad": "V3", "dia": "MARTES", "miembros": [3],
            "unidad_ref": "V1", "dia_preferido": "LUNES", "rigidez": "FLEXIBLE",
            "dias_admisibles": ["LUNES", "MARTES"]}, # desviado, SÍ es hogar de V1/LUNES, kg bajo
    }
    pedidos = {1: 100, 2: 850, 3: 500}
    caps = {"V1": 1000, "V2": 5000, "V3": 5000}
    coocurrencia = {frozenset((1, 2)): 1, frozenset((1, 3)): 1}
    exc = _rellenar_capacidad_libre(
        asign, pedidos, {}, {}, caps, {}, _sin_tiempo(coocurrencia_grupos=coocurrencia), {})
    assert asign[3]["unidad"] == "V1" and asign[3]["dia"] == "LUNES"
    assert asign[2]["unidad"] == "V2" and asign[2]["dia"] == "LUNES"
    relleno = [e for e in exc if e["tipo"] == "RELLENO_CAPACIDAD_LIBRE"]
    assert len(relleno) == 1 and relleno[0]["grupo"] == 3
    assert relleno[0]["motivo_regreso_hogar"] is True


def test_relleno_capacidad_sin_candidato_en_casa_maximiza_ocupacion():
    from logic.convrp_logic import _rellenar_capacidad_libre
    asign = {
        1: {"grupo": 1, "unidad": "V1", "dia": "LUNES", "miembros": [1],
            "unidad_ref": "V1", "dia_preferido": "LUNES", "rigidez": "RIGIDO",
            "dias_admisibles": ["LUNES"]},
        2: {"grupo": 2, "unidad": "V2", "dia": "LUNES", "miembros": [2],
            "unidad_ref": "V3", "dia_preferido": "MARTES", "rigidez": "FLEXIBLE",
            "dias_admisibles": ["LUNES"]},           # desviado, ninguno es hogar de V1/LUNES
        3: {"grupo": 3, "unidad": "V3", "dia": "MARTES", "miembros": [3],
            "unidad_ref": "V4", "dia_preferido": "JUEVES", "rigidez": "FLEXIBLE",
            "dias_admisibles": ["LUNES", "MARTES"]}, # desviado, kg alto: deja mejor % en V1
    }
    pedidos = {1: 100, 2: 200, 3: 850}
    caps = {"V1": 1000, "V2": 5000, "V3": 5000, "V4": 5000}
    coocurrencia = {frozenset((1, 2)): 1, frozenset((1, 3)): 1}
    exc = _rellenar_capacidad_libre(
        asign, pedidos, {}, {}, caps, {}, _sin_tiempo(coocurrencia_grupos=coocurrencia), {})
    assert asign[3]["unidad"] == "V1" and asign[3]["dia"] == "LUNES"
    relleno = [e for e in exc if e["tipo"] == "RELLENO_CAPACIDAD_LIBRE"]
    assert relleno[0]["grupo"] == 3
    assert relleno[0]["motivo_regreso_hogar"] is False


def test_relleno_capacidad_puede_rellenar_con_mas_de_un_grupo():
    from logic.convrp_logic import _rellenar_capacidad_libre
    asign = {
        1: {"grupo": 1, "unidad": "V1", "dia": "LUNES", "miembros": [1],
            "unidad_ref": "V1", "dia_preferido": "LUNES", "rigidez": "RIGIDO",
            "dias_admisibles": ["LUNES"]},
        2: {"grupo": 2, "unidad": "V2", "dia": "LUNES", "miembros": [2],
            "unidad_ref": "V3", "dia_preferido": "MARTES", "rigidez": "FLEXIBLE",
            "dias_admisibles": ["LUNES"]},
        3: {"grupo": 3, "unidad": "V3", "dia": "MARTES", "miembros": [3],
            "unidad_ref": "V4", "dia_preferido": "JUEVES", "rigidez": "FLEXIBLE",
            "dias_admisibles": ["LUNES", "MARTES"]},
    }
    pedidos = {1: 100, 2: 300, 3: 300}
    caps = {"V1": 1000, "V2": 1000, "V3": 1000, "V4": 1000}
    coocurrencia = {frozenset((1, 2)): 1, frozenset((1, 3)): 1, frozenset((2, 3)): 1}
    exc = _rellenar_capacidad_libre(
        asign, pedidos, {}, {}, caps, {}, _sin_tiempo(coocurrencia_grupos=coocurrencia), {})
    assert asign[2]["unidad"] == "V1" and asign[2]["dia"] == "LUNES"
    assert asign[3]["unidad"] == "V1" and asign[3]["dia"] == "LUNES"
    relleno = [e for e in exc if e["tipo"] == "RELLENO_CAPACIDAD_LIBRE"]
    assert len(relleno) == 2


def test_relleno_capacidad_cada_grupo_se_mueve_como_maximo_una_vez():
    from logic.convrp_logic import _rellenar_capacidad_libre
    asign = {
        1: {"grupo": 1, "unidad": "V1", "dia": "LUNES", "miembros": [1],
            "unidad_ref": "V1", "dia_preferido": "LUNES", "rigidez": "RIGIDO",
            "dias_admisibles": ["LUNES"]},
        2: {"grupo": 2, "unidad": "V2", "dia": "LUNES", "miembros": [2],
            "unidad_ref": "V2", "dia_preferido": "LUNES", "rigidez": "RIGIDO",
            "dias_admisibles": ["LUNES"]},
        3: {"grupo": 3, "unidad": "V3", "dia": "MARTES", "miembros": [3],
            "unidad_ref": "V4", "dia_preferido": "JUEVES", "rigidez": "FLEXIBLE",
            "dias_admisibles": ["LUNES", "MARTES"]},  # el único candidato posible
    }
    pedidos = {1: 100, 2: 100, 3: 300}
    caps = {"V1": 1000, "V2": 1000, "V3": 1000, "V4": 1000}
    coocurrencia = {frozenset((1, 3)): 1, frozenset((2, 3)): 1}
    exc = _rellenar_capacidad_libre(
        asign, pedidos, {}, {}, caps, {}, _sin_tiempo(coocurrencia_grupos=coocurrencia), {})
    relleno = [e for e in exc if e["tipo"] == "RELLENO_CAPACIDAD_LIBRE"]
    assert len(relleno) == 1                          # grupo 3 sólo se mueve una vez
    assert asign[3]["unidad"] == "V1" and asign[3]["dia"] == "LUNES"


# ═══════════════════════════════════════════════════════════════════════════
# Integración: la Palanca 5 corre dentro de construir_groups_desde_plantilla.
# Escenario original: grupo 2 (unidad_ref="V1", inválida en esta flota) caía
# por defecto a V2, "desviado" desde el arranque, y la Palanca 5 lo reubicaba
# en V3. Sin preferencia, `unidad_ref` inválido ya no provoca ningún desvío:
# la asignación inicial por peso consolida ambos grupos en la MISMA unidad
# desde el arranque (V2, que ordena primero alfabéticamente entre las dos
# vacías) -- Palanca 5 ni siquiera necesita correr. El comportamiento
# protegido (nunca queda una ruta residual vacía cuando todo cabe junto)
# sigue intacto, verificado corriendo el motor real.
# ═══════════════════════════════════════════════════════════════════════════
def test_relleno_capacidad_integrado_rellena_y_vacia_la_ruta_origen():
    plantilla = [
        {"grupo": 1, "rigidez": "RIGIDO", "dia": "LUNES", "unidad_ref": "V3",
         "sucursales": [1, 4], "dias_admisibles": ["LUNES"]},
        {"grupo": 2, "rigidez": "FLEXIBLE", "dia": "LUNES", "unidad_ref": "V1",
         "sucursales": [2, 3], "dias_admisibles": ["LUNES"]},
    ]
    pedidos = {1: 50, 4: 50, 2: 150, 3: 150}
    caps = {"V2": 1000, "V3": 1000}
    vols = {"V2": 99, "V3": 99}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, caps, vols, _sin_tiempo())
    assert ("V3", "LUNES") not in groups             # nunca se abrió la otra ruta
    assert sorted(m["sid"] for m in groups[("V2", "LUNES")]) == [1, 2, 3, 4]


def test_relleno_capacidad_desactivado_no_cambia_nada():
    plantilla = [
        {"grupo": 1, "rigidez": "RIGIDO", "dia": "LUNES", "unidad_ref": "V3",
         "sucursales": [1, 4], "dias_admisibles": ["LUNES"]},
        {"grupo": 2, "rigidez": "FLEXIBLE", "dia": "LUNES", "unidad_ref": "V1",
         "sucursales": [2, 3], "dias_admisibles": ["LUNES"]},
    ]
    pedidos = {1: 50, 4: 50, 2: 150, 3: 150}
    caps = {"V2": 1000, "V3": 1000}
    vols = {"V2": 99, "V3": 99}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, caps, vols,
        _sin_tiempo(relleno_capacidad=False))
    assert ("V2", "LUNES") in groups                 # ya NO se rellena
    assert not any(e["tipo"] == "RELLENO_CAPACIDAD_LIBRE" for e in exc)


# ═══════════════════════════════════════════════════════════════════════════
# Regresión — inspirada en el caso real que motivó esta palanca: la logística
# del 24-28 de agosto 2026 mostraba F 350_1/MARTES con sólo 2,318 de 3,900 kg
# (59 %) mientras el grupo 19 (Amatitlán + Carlos A. Carrillo 2, FLEXIBLE,
# hogar histórico F 350_1) no aparecía ahí. Sin preferencia, `unidad_ref`
# inválido ya no provoca ningún desvío: grupo 19 (el más pesado) procesa
# primero y se lleva la unidad que ordena primero alfabéticamente (AUX20);
# el ancla (grupo 30) se suma ahí mismo por el desempate de consolidación,
# en vez de reclamar F350_1 -- ambos terminan en la MISMA ruta desde el
# arranque, sin necesitar la Palanca 5. El comportamiento protegido (grupo
# 19 nunca queda en una ruta aparte de su ancla) sigue intacto, verificado
# corriendo el motor real.
# ═══════════════════════════════════════════════════════════════════════════
def test_relleno_capacidad_regresion_grupo_19_amatitlan_carrillo():
    plantilla = [
        {"grupo": 30, "rigidez": "RIGIDO", "dia": "MARTES", "unidad_ref": "F350_1",
         "sucursales": [101, 102], "dias_admisibles": ["MARTES"]},   # ancla
        {"grupo": 19, "rigidez": "FLEXIBLE", "dia": "MARTES", "unidad_ref": "NO_ASIGNADA",
         "sucursales": [86, 100], "dias_admisibles": ["MARTES"]},    # Amatitlán + Carrillo 2
    ]
    pedidos = {101: 150, 102: 150,      # grupo 30 (ancla) = 300
               86: 250, 100: 250}       # grupo 19 = 500, procesa primero (más pesado)
    caps = {"F350_1": 3900, "AUX20": 3900}   # "AUX20" < "F350_1" alfabéticamente
    vols = {"F350_1": 99, "AUX20": 99}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, caps, vols, _sin_tiempo())
    assert sorted(m["sid"] for m in groups[("AUX20", "MARTES")]) == [86, 100, 101, 102]
    assert ("F350_1", "MARTES") not in groups


def test_consolidar_solitarios_nunca_mueve_a_unidad_excluida():
    from logic.convrp_logic import _consolidar_solitarios
    asign = {
        1: {"grupo": 1, "unidad": "CHICA", "dia": "LUNES", "miembros": [1],
            "unidad_ref": None, "rigidez": "FLEXIBLE",
            "dias_admisibles": ["LUNES"], "unidades_excluidas": ["GRANDE"]},
        2: {"grupo": 2, "unidad": "GRANDE", "dia": "LUNES", "miembros": [2, 3],
            "unidad_ref": None, "rigidez": "RIGIDO",
            "dias_admisibles": ["LUNES"], "unidades_excluidas": []},
    }
    pedidos = {1: 100, 2: 200, 3: 200}
    caps = {"CHICA": 1000, "GRANDE": 5000}
    coocurrencia = {frozenset((1, 2)): 1}      # compatibles: aisla SOLO la exclusion
    exc = _consolidar_solitarios(asign, pedidos, {}, {}, caps, {},
                                 _sin_tiempo(coocurrencia_grupos=coocurrencia), {})
    assert asign[1]["unidad"] == "CHICA", \
        "nunca debe consolidar en GRANDE (excluida para el grupo 1)"
    assert not any(e["tipo"] == "CONSOLIDADO_SOLITARIA" for e in exc)


def test_relleno_capacidad_nunca_mueve_a_unidad_excluida():
    from logic.convrp_logic import _rellenar_capacidad_libre
    asign = {
        1: {"grupo": 1, "unidad": "CHICA", "dia": "LUNES", "miembros": [1, 2],
            "unidad_ref": None, "dia_preferido": "LUNES", "rigidez": "FLEXIBLE",
            "dias_admisibles": ["LUNES"], "unidades_excluidas": ["GRANDE"]},
        2: {"grupo": 2, "unidad": "GRANDE", "dia": "LUNES", "miembros": [3],
            "unidad_ref": "GRANDE", "dia_preferido": "LUNES", "rigidez": "RIGIDO",
            "dias_admisibles": ["LUNES"], "unidades_excluidas": []},
    }
    pedidos = {1: 100, 2: 100, 3: 100}
    caps = {"CHICA": 1000, "GRANDE": 5000}      # GRANDE con mucho espacio libre
    coocurrencia = {frozenset((1, 2)): 3}       # compatibles: aisla SOLO la exclusion
    exc = _rellenar_capacidad_libre(asign, pedidos, {}, {}, caps, {},
                                    _sin_tiempo(coocurrencia_grupos=coocurrencia), {})
    assert asign[1]["unidad"] == "CHICA", \
        "nunca debe rellenar GRANDE con el grupo 1 (excluida para ese grupo)"


def test_relleno_capacidad_nunca_ofrece_sin_unidad_como_destino():
    from logic.convrp_logic import _rellenar_capacidad_libre
    asign = {
        1: {"grupo": 1, "unidad": "SIN_UNIDAD", "dia": "LUNES", "miembros": [1],
            "unidad_ref": None, "dia_preferido": "LUNES", "rigidez": "FLEXIBLE",
            "dias_admisibles": ["LUNES"], "unidades_excluidas": []},
        2: {"grupo": 2, "unidad": "CHICA", "dia": "LUNES", "miembros": [2],
            "unidad_ref": None, "dia_preferido": "MARTES", "rigidez": "FLEXIBLE",
            "dias_admisibles": ["LUNES", "MARTES"], "unidades_excluidas": []},
    }
    pedidos = {1: 100, 2: 100}
    caps = {"CHICA": 1000}                      # "SIN_UNIDAD" no está en caps a propósito
    exc = _rellenar_capacidad_libre(asign, pedidos, {}, {}, caps, {}, _sin_tiempo(), {})
    assert asign[2]["unidad"] != "SIN_UNIDAD", \
        "SIN_UNIDAD nunca debe tratarse como una ruta real con espacio libre"


def test_exclusion_se_mantiene_a_traves_de_todas_las_fases():
    # Escenario con varios grupos, un día saturado, y un grupo excluido de
    # GRANDE que fuerza pasar por movimiento de dia, particion, y deja a
    # otro grupo listo para consolidacion/relleno el mismo dia -- GRANDE
    # nunca debe aparecer como destino de ninguna sucursal del grupo 1 en
    # ninguna fase.
    plantilla = [
        _grupo(1, "FLEXIBLE", "LUNES", [1, 2, 3], unidad_ref=None,
               dias_admisibles=["LUNES", "MARTES"]),
        _grupo(2, "FLEXIBLE", "LUNES", [4], unidad_ref=None,
               dias_admisibles=["LUNES"]),
    ]
    plantilla[0]["unidades_excluidas"] = ["GRANDE"]
    pedidos = {1: 600, 2: 600, 3: 600, 4: 100}   # g1 = 1800 (> CHICA), g2 = 100
    caps = {"CHICA": 1000, "GRANDE": 5000}
    coocurrencia = {frozenset((1, 2)): 1}         # compatibles: aisla SOLO la exclusion
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, caps, {"CHICA": 99, "GRANDE": 99},
        _sin_tiempo(coocurrencia_grupos=coocurrencia))
    for (unidad, dia), miembros in groups.items():
        if unidad == "GRANDE":
            sids = {m["sid"] for m in miembros}
            assert not (sids & {1, 2, 3}), \
                f"sucursal del grupo excluido terminó en GRANDE: {sids}"
    for e in exc:
        assert e.get("a_unidad") != "GRANDE" and e.get("destino_unidad") != "GRANDE", \
            f"ninguna excepcion debe mover el grupo excluido a GRANDE: {e}"


# ══ 9. Afinidad histórica como desempate entre unidades empatadas en capacidad ═
def test_entre_capacidad_empatada_gana_la_afinidad_historica():
    plantilla = [_grupo(1, "FLEXIBLE", "LUNES", [1, 2], unidad_ref=None)]
    pedidos = {1: 1500, 2: 1500}          # 3000 kg: cabe en cualquiera de las dos
    caps = {"A_GRANDE": 3900, "Z_GRANDE": 3900}     # empatadas en capacidad
    cfg = dict(cfg_por_defecto(), chequear_tiempo=False,
               afinidad_unidad={1: {"Z_GRANDE": 9}})
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, caps, {"A_GRANDE": 99, "Z_GRANDE": 99}, cfg)
    assert ("Z_GRANDE", "LUNES") in groups, \
        "debio ganar la afinidad historica (Z_GRANDE), no el abecedario (A_GRANDE)"


def test_afinidad_no_gana_sobre_capacidad_distinta():
    # La afinidad es SOLO desempate entre empatadas -- si las capacidades
    # difieren, sigue mandando la mas chica que alcanza, aunque la afinidad
    # apunte a la mas grande.
    plantilla = [_grupo(1, "FLEXIBLE", "LUNES", [1, 2], unidad_ref=None)]
    pedidos = {1: 400, 2: 400}             # 800 kg: cabe en CHICA y en GRANDE
    caps = {"CHICA": 1000, "GRANDE": 5000}
    cfg = dict(cfg_por_defecto(), chequear_tiempo=False,
               afinidad_unidad={1: {"GRANDE": 9}})
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, caps, {"CHICA": 99, "GRANDE": 99}, cfg)
    assert ("CHICA", "LUNES") in groups, \
        "la capacidad ascendente sigue mandando cuando no hay empate real"


def test_sin_afinidad_el_desempate_sigue_siendo_alfabetico():
    # Sin datos de afinidad (grupo nuevo, sin historial) el comportamiento
    # no cambia: sigue siendo capacidad -> consolidacion -> alfabetico.
    plantilla = [_grupo(1, "FLEXIBLE", "LUNES", [1, 2], unidad_ref=None)]
    pedidos = {1: 1500, 2: 1500}
    caps = {"A_GRANDE": 3900, "Z_GRANDE": 3900}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, caps, {"A_GRANDE": 99, "Z_GRANDE": 99},
        _sin_tiempo())
    assert ("A_GRANDE", "LUNES") in groups


def test_ultimo_recurso_tambien_desempata_por_afinidad():
    # Ningun candidato "cabe" (fuerza el bloque de ultimo recurso, no el
    # sort normal) -- entre F350_1/2/3, todas vacias y de igual capacidad,
    # debe ganar la afinidad, no el abecedario.
    from logic.convrp_logic import _asignar_unidades
    asign = {1: {"grupo": 1, "unidad": None, "dia": "LUNES", "miembros": [1],
                "unidad_ref": None, "rigidez": "RIGIDO",
                "dias_admisibles": ["LUNES"], "unidades_excluidas": []}}
    pedidos = {1: 5000}      # mas pesado que cualquier unidad: fuerza ultimo recurso
    caps = {"A_GRANDE": 3900, "Z_GRANDE": 3900}
    cfg = dict(cfg_por_defecto(), chequear_tiempo=False,
               afinidad_unidad={1: {"Z_GRANDE": 9}})
    _asignar_unidades(asign, pedidos, {}, {}, caps, {}, cfg)
    assert asign[1]["unidad"] == "Z_GRANDE", \
        "el ultimo recurso tambien debe desempatar por afinidad, no solo por espacio libre/abecedario"


def test_grupo_pesado_sin_afinidad_no_ocupa_la_reservada_de_uno_pendiente():
    # grupo 1 (mas pesado, SIN afinidad) se procesa primero; grupo 2 (mas
    # liviano, CON afinidad fuerte a A_GRANDE) todavia no tuvo su turno.
    # A_GRANDE es ademas la que gana el desempate alfabetico por defecto
    # (mismo patron que en produccion: F 350_1 < F 350_3) -- sin la reserva,
    # grupo 1 la ocuparia igual; con la reserva, debe cederla y usar
    # Z_GRANDE, dejando A_GRANDE libre para grupo 2.
    plantilla = [
        _grupo(1, "FLEXIBLE", "LUNES", [1, 2], unidad_ref=None),
        _grupo(2, "FLEXIBLE", "LUNES", [3, 4], unidad_ref=None),
    ]
    pedidos = {1: 1600, 2: 1600, 3: 1000, 4: 1000}   # g1=3200 (mas pesado), g2=2000
    caps = {"A_GRANDE": 3900, "Z_GRANDE": 3900}
    cfg = dict(cfg_por_defecto(), chequear_tiempo=False,
               afinidad_unidad={2: {"A_GRANDE": 9}})
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, caps, {"A_GRANDE": 99, "Z_GRANDE": 99}, cfg)
    assert sorted(m["sid"] for m in groups[("A_GRANDE", "LUNES")]) == [3, 4], \
        "grupo 2 (afinidad fuerte, aun no tenia turno) debe quedarse con A_GRANDE"
    assert sorted(m["sid"] for m in groups[("Z_GRANDE", "LUNES")]) == [1, 2], \
        "grupo 1 (sin afinidad, mas pesado) debe ceder A_GRANDE y usar Z_GRANDE"


def test_reserva_de_afinidad_cede_si_es_la_unica_opcion():
    # Solo existe una unidad -- reservarla para el grupo pendiente dejaria
    # a grupo 1 sin ningun destino, asi que la reserva cede.
    plantilla = [
        _grupo(1, "FLEXIBLE", "LUNES", [1, 2], unidad_ref=None),
        _grupo(2, "FLEXIBLE", "LUNES", [3, 4], unidad_ref=None),
    ]
    pedidos = {1: 1600, 2: 1600, 3: 500, 4: 500}
    caps = {"Z_GRANDE": 5000}          # unica unidad de toda la flota
    cfg = dict(cfg_por_defecto(), chequear_tiempo=False,
               afinidad_unidad={2: {"Z_GRANDE": 9}})
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, caps, {"Z_GRANDE": 99}, cfg)
    assert len(groups) == 1 and ("Z_GRANDE", "LUNES") in groups
    sids = sorted(m["sid"] for ms in groups.values() for m in ms)
    assert sids == [1, 2, 3, 4]


def test_reserva_de_afinidad_ignora_reclamo_a_unidad_excluida_para_el_propio_grupo():
    # grupo 2 (pendiente) tiene afinidad a GRANDE, pero GRANDE esta en SUS
    # PROPIAS unidades_excluidas (dato historico que ya no aplica, mismo
    # riesgo que KANGOO) -- ese reclamo no debe reservar nada: grupo 1 (mas
    # pesado, procesado primero) puede usar GRANDE sin problema.
    plantilla = [
        _grupo(1, "FLEXIBLE", "LUNES", [1, 2], unidad_ref=None),
        _grupo(2, "FLEXIBLE", "LUNES", [3, 4], unidad_ref=None),
    ]
    plantilla[1]["unidades_excluidas"] = ["GRANDE"]
    pedidos = {1: 1600, 2: 1600, 3: 1000, 4: 1000}
    caps = {"GRANDE": 3900, "OTRA": 3900}
    cfg = dict(cfg_por_defecto(), chequear_tiempo=False,
               afinidad_unidad={2: {"GRANDE": 9}})
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, caps, {"GRANDE": 99, "OTRA": 99}, cfg)
    assert sorted(m["sid"] for m in groups[("GRANDE", "LUNES")]) == [1, 2], \
        "GRANDE no debia quedar reservada: grupo 2 tiene esa unidad excluida, no puede usarla"
    # sin el fix, esto igual converge al mismo `groups` final -- pero sólo
    # después de partir grupo 1 y recomponerlo vía relleno de capacidad
    # libre (3 excepciones de más). Con el fix, grupo 1 entra directo a
    # GRANDE en la primera pasada: cero excepciones.
    assert exc == [], \
        f"debio asignarse directo sin partir/relocar; huellas de la reserva mal aplicada: {exc}"
