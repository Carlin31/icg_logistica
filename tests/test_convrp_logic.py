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


# ══ 2. La unidad es preferencia, no libre ══════════════════════════════════
def test_grupo_usa_su_unidad_de_referencia_cuando_cabe():
    plantilla = [_grupo(1, "RIGIDO", "LUNES", [1, 2], unidad_ref="V2")]
    pedidos = {1: 100, 2: 100}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, {"V1": 5000, "V2": 5000},
        {"V1": 20, "V2": 20}, _sin_tiempo())
    assert ("V2", "LUNES") in groups


def test_dos_grupos_misma_unidad_y_dia_comparten_ruta():
    plantilla = [_grupo(1, "RIGIDO", "LUNES", [1, 2], unidad_ref="V1"),
                 _grupo(2, "FLEXIBLE", "LUNES", [3, 4], unidad_ref="V1")]
    pedidos = {1: 100, 2: 100, 3: 100, 4: 100}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, {"V1": 5000}, {"V1": 20}, _sin_tiempo())
    assert len(groups) == 1
    assert len(groups[("V1", "LUNES")]) == 4


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
    tipos = [e["tipo"] for e in exc]
    assert "MOVIDO_UNIDAD" in tipos


def test_excepcion_registra_restriccion_peso():
    plantilla = [_grupo(1, "RIGIDO", "LUNES", [1, 2], unidad_ref="V1"),
                 _grupo(2, "FLEXIBLE", "LUNES", [3, 4], unidad_ref="V1")]
    pedidos = {1: 300, 2: 300, 3: 300, 4: 300}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, {"V1": 1000, "V2": 1000},
        {"V1": 20, "V2": 20}, _sin_tiempo())
    mov = [e for e in exc if e["tipo"] == "MOVIDO_UNIDAD"]
    assert mov and mov[0]["restriccion"] == "PESO"


def test_excepcion_registra_restriccion_volumen():
    # El peso cabe de sobra; lo que satura es el volumen.
    plantilla = [_grupo(1, "RIGIDO", "LUNES", [1, 2], unidad_ref="V1"),
                 _grupo(2, "FLEXIBLE", "LUNES", [3, 4], unidad_ref="V1")]
    pedidos = {1: 10, 2: 10, 3: 10, 4: 10}
    volumenes = {1: 3.0, 2: 3.0, 3: 3.0, 4: 3.0}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, volumenes, COORDS, plantilla, {"V1": 9999, "V2": 9999},
        {"V1": 8.0, "V2": 8.0}, _sin_tiempo())
    mov = [e for e in exc if e["tipo"] == "MOVIDO_UNIDAD"]
    assert mov and mov[0]["restriccion"] == "VOLUMEN"


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


def test_grupo_conserva_unidad_ref_cuando_cabe_aunque_haya_otras_libres():
    plantilla = [_grupo(1, "RIGIDO", "LUNES", [1, 2], unidad_ref="V3")]
    pedidos = {1: 100, 2: 100}
    caps = {f"V{i}": 5000 for i in range(1, 5)}
    vols = {f"V{i}": 99 for i in range(1, 5)}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, caps, vols, _sin_tiempo())
    assert ("V3", "LUNES") in groups                 # respeta la preferencia
    assert not exc


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
def test_excepcion_registra_origen_lores():
    plantilla = [_grupo(1, "RIGIDO", "LUNES", [1, 2], unidad_ref="V1"),
                 _grupo(2, "FLEXIBLE", "LUNES", [3, 4], unidad_ref="V1")]
    pedidos = {1: 300, 2: 300, 3: 300, 4: 300}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, {"V1": 1000, "V2": 1000},
        {"V1": 20, "V2": 20}, _sin_tiempo())
    mov = [e for e in exc if e["tipo"] == "MOVIDO_UNIDAD"]
    assert mov and mov[0]["origen_carga"] == "LORES"


def test_excepcion_registra_origen_mayoristas():
    # La demanda Lores cabe sola; lo que satura es la carga de mayoristas
    # enganchada a esa ruta (Playa Vicente mete ~1456 kg sobre un grupo que en
    # Lores lleva ~692: el enganche por zona puede saturar la ruta que recibe).
    plantilla = [_grupo(1, "RIGIDO", "LUNES", [1, 2], unidad_ref="V1",
                        dias_admisibles=["LUNES"]),
                 _grupo(2, "FLEXIBLE", "LUNES", [3, 4], unidad_ref="V1")]
    pedidos = {1: 100, 2: 100, 3: 100, 4: 100}          # Lores = 400, cabe sola
    mayoristas = {1: 600, 2: 600}                        # 1200 solos YA exceden
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, {"V1": 1000, "V2": 1000},
        {"V1": 20, "V2": 20}, _sin_tiempo(), kg_mayoristas=mayoristas)
    disparadas = [e for e in exc if e.get("origen_carga")]
    assert disparadas, "la carga de mayoristas debió disparar alivio"
    assert any(e["origen_carga"] == "MAYORISTAS" for e in disparadas)


def test_excepcion_registra_origen_ambas():
    plantilla = [_grupo(1, "RIGIDO", "LUNES", [1, 2], unidad_ref="V1",
                        dias_admisibles=["LUNES"]),
                 _grupo(2, "FLEXIBLE", "LUNES", [3, 4], unidad_ref="V1")]
    # AMBAS = cada fuente satura por su cuenta
    pedidos = {1: 600, 2: 600, 3: 300, 4: 300}          # Lores solos exceden
    mayoristas = {1: 600, 2: 600}                        # mayoristas solos también
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, {"V1": 1000, "V2": 1000},
        {"V1": 20, "V2": 20}, _sin_tiempo(), kg_mayoristas=mayoristas)
    assert any(e.get("origen_carga") == "AMBAS" for e in exc)


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
# Al ceder la unidad de referencia, manda la AFINIDAD, no el abecedario
#
# El g24 (Playa Vicente) no cabía en su unidad de referencia el miércoles y el
# motor lo mandaba a F 350_1 sobre F 350_2 sólo porque ambas estaban vacías y
# "F 350_1" ordena antes. Resultado: FELIPE aparecía con un día de trabajo que
# la operación no hace, mientras la unidad que sí lleva esa carga quedaba libre.
# El desempate tiene que ser en qué camión viaja ese grupo históricamente.
# ═══════════════════════════════════════════════════════════════════════════

def test_al_ceder_la_unidad_gana_la_afinidad_historica():
    plantilla = [{"grupo": 1, "rigidez": "RIGIDO", "dia": "LUNES",
                  "unidad_ref": "CHICA", "sucursales": [1, 2],
                  "dias_admisibles": ["LUNES"]}]
    pedidos = {1: 1500, 2: 1500}                  # 3,000: no cabe en CHICA
    caps = {"CHICA": 1000, "A_GRANDE": 3900, "Z_GRANDE": 3900}
    cfg = dict(cfg_por_defecto(), chequear_tiempo=False,
               afinidad_unidad={1: {"Z_GRANDE": 5}})
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, {}, plantilla, caps, {}, cfg)
    assert ("Z_GRANDE", "LUNES") in groups, \
        f"eligió por abecedario, no por afinidad: {sorted(groups)}"


def test_sin_afinidad_el_reparto_no_cambia():
    # Sin datos de afinidad el comportamiento es el de antes (consolidar y
    # desempatar por nombre): el cambio no puede alterar semanas sin historia.
    plantilla = [{"grupo": 1, "rigidez": "RIGIDO", "dia": "LUNES",
                  "unidad_ref": "CHICA", "sucursales": [1, 2],
                  "dias_admisibles": ["LUNES"]}]
    pedidos = {1: 1500, 2: 1500}
    caps = {"CHICA": 1000, "A_GRANDE": 3900, "Z_GRANDE": 3900}
    cfg = dict(cfg_por_defecto(), chequear_tiempo=False)
    groups, _ = construir_groups_desde_plantilla(
        pedidos, {}, {}, plantilla, caps, {}, cfg)
    assert ("A_GRANDE", "LUNES") in groups


def test_la_afinidad_no_rompe_la_consolidacion():
    # Preferir la unidad afín NO debe abrir un viaje nuevo si el grupo cabe en
    # una que ya está trabajando ese día: consolidar sigue primero (el histórico
    # lleva ~1.4 grupos por viaje, no 1.0).
    plantilla = [
        {"grupo": 1, "rigidez": "FLEXIBLE", "dia": "LUNES", "unidad_ref": "USADA",
         "sucursales": [1], "dias_admisibles": ["LUNES"]},
        {"grupo": 2, "rigidez": "FLEXIBLE", "dia": "LUNES", "unidad_ref": "CHICA",
         "sucursales": [2], "dias_admisibles": ["LUNES"]},
    ]
    pedidos = {1: 1000, 2: 500}
    caps = {"CHICA": 100, "USADA": 3900, "VACIA": 3900}
    cfg = dict(cfg_por_defecto(), chequear_tiempo=False,
               afinidad_unidad={2: {"VACIA": 9}})
    groups, _ = construir_groups_desde_plantilla(
        pedidos, {}, {}, plantilla, caps, {}, cfg)
    assert len(groups) == 1 and ("USADA", "LUNES") in groups


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


# ═══════════════════════════════════════════════════════════════════════════
# Palanca 4 — ninguna ruta se queda con una sola sucursal, salvo que el
# vehículo ya esté al límite de su capacidad (peso Lores + mayoristas).
# Regla de negocio explícita del 2026-08-11, encontrada al revisar viajes
# como San Bartolo (K16, 1 sola sucursal) en la logística real.
# ═══════════════════════════════════════════════════════════════════════════

def test_solitaria_se_consolida_en_ruta_activa_compatible_con_cupo():
    plantilla = [_grupo(1, "FLEXIBLE", "LUNES", [1], unidad_ref="V1"),
                 _grupo(2, "RIGIDO", "LUNES", [2, 3], unidad_ref="V2")]
    pedidos = {1: 100, 2: 300, 3: 300}
    caps = {"V1": 1000, "V2": 1000}
    coocurrencia = {frozenset((1, 2)): 1}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, {}, plantilla, caps, {}, _sin_tiempo(coocurrencia_grupos=coocurrencia))
    assert ("V1", "LUNES") not in groups
    assert sorted(m["sid"] for m in groups[("V2", "LUNES")]) == [1, 2, 3]
    assert any(e["tipo"] == "CONSOLIDADO_SOLITARIA" for e in exc)


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
    plantilla = [_grupo(1, "FLEXIBLE", "LUNES", [1], unidad_ref="V1"),
                 _grupo(2, "RIGIDO", "LUNES", [2, 3], unidad_ref="V2")]
    pedidos = {1: 100, 2: 300, 3: 300}
    caps = {"V1": 1000, "V2": 1000}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, {}, plantilla, caps, {}, _sin_tiempo(coocurrencia_grupos={}))
    assert sorted(m["sid"] for m in groups[("V1", "LUNES")]) == [1]     # nunca coincidieron
    assert any(e["tipo"] == "AVISO_RUTA_SOLITARIA" for e in exc)


def test_solitaria_considera_mayoristas_al_evaluar_el_limite():
    # Lores solo (100 kg) deja mucho margen en V1 (cap 1000), pero con los
    # mayoristas ya anclados (900 kg) llega al límite -- no debe moverse.
    plantilla = [_grupo(1, "FLEXIBLE", "LUNES", [1], unidad_ref="V1"),
                 _grupo(2, "RIGIDO", "LUNES", [2, 3], unidad_ref="V2")]
    pedidos = {1: 100, 2: 300, 3: 300}
    caps = {"V1": 1000, "V2": 1000}
    coocurrencia = {frozenset((1, 2)): 1}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, {}, plantilla, caps, {}, _sin_tiempo(coocurrencia_grupos=coocurrencia),
        kg_mayoristas={1: 900})
    assert sorted(m["sid"] for m in groups[("V1", "LUNES")]) == [1]
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
