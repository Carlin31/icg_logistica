"""
tests/test_enganche_zona.py

Resolución de la ZONA de enganche de un cliente mayorista (Fase 3). Puro: sin BD.

Tres vías, en orden de autoridad:
  1. HISTORIA   — la población está en el diccionario histórico población→zona.
  2. GEOGRAFIA  — no hay evidencia histórica: se engancha a la zona cuyo
                  centroide (la ruta canónica) queda más cerca de sus
                  coordenadas.
  3. FALLBACK   — sin coordenada válida, o la zona más cercana queda más lejos
                  del umbral: se deja al comportamiento global anterior.

La vía queda registrada en cada resolución para poder auditarla.
"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from logic.enganche_zona import resolver_zona_cliente

# centroides ficticios, separados ~11 km por cada 0.1° de latitud
CENTROIDES = {
    "COSAMALOAPAN": (18.37, -95.80),
    "TUXTEPEC": (18.09, -96.12),
    "CATEMACO": (18.42, -95.11),
}
HIST = {"COSAMALOAPAN": "COSAMALOAPAN", "PLAYA VICENTE": "TUXTEPEC"}


def test_zona_baja_confianza_cede_ante_la_geografia():
    # La historia de esta población es de UNA sola semana. Preferir esa evidencia
    # débil sobre la inferencia geográfica es al revés de lo que corresponde:
    # gana la geografía y el desacuerdo queda registrado.
    hist = {"PUEBLO X": "COSAMALOAPAN"}
    conf = {"COSAMALOAPAN": "BAJA", "CATEMACO": "ALTA"}
    r = resolver_zona_cliente("PUEBLO X", 18.42, -95.11, hist, CENTROIDES,
                              confianza_por_zona=conf)
    assert r["zona"] == "CATEMACO"
    assert r["via"] == "GEOGRAFIA"
    assert r["desacuerdo"] is True
    assert r["zona_historica"] == "COSAMALOAPAN"
    assert r["confianza"] == "BAJA"


def test_zona_baja_confianza_pero_coinciden_no_es_desacuerdo():
    hist = {"PUEBLO X": "CATEMACO"}
    conf = {"CATEMACO": "BAJA"}
    r = resolver_zona_cliente("PUEBLO X", 18.42, -95.11, hist, CENTROIDES,
                              confianza_por_zona=conf)
    assert r["zona"] == "CATEMACO"
    assert r["desacuerdo"] is False


def test_zona_alta_confianza_manda_aunque_difiera_de_la_geografia():
    hist = {"PLAYA VICENTE": "TUXTEPEC"}
    conf = {"TUXTEPEC": "ALTA"}
    r = resolver_zona_cliente("PLAYA VICENTE", 18.42, -95.11, hist, CENTROIDES,
                              confianza_por_zona=conf)
    assert r["zona"] == "TUXTEPEC"
    assert r["via"] == "HISTORIA"
    assert r["desacuerdo"] is True          # se registra, pero manda la historia


def test_historia_manda_sobre_geografia():
    # El cliente está físicamente pegado a CATEMACO, pero el histórico dice que
    # su población se engancha a TUXTEPEC: gana el histórico.
    r = resolver_zona_cliente("PLAYA VICENTE", 18.42, -95.11, HIST, CENTROIDES)
    assert r["zona"] == "TUXTEPEC"
    assert r["via"] == "HISTORIA"


def test_geografia_cuando_no_hay_historia():
    r = resolver_zona_cliente("POBLACION NUEVA", 18.41, -95.12, HIST, CENTROIDES)
    assert r["zona"] == "CATEMACO"
    assert r["via"] == "GEOGRAFIA"
    assert r["distancia_km"] < 5


def test_geografia_normaliza_la_poblacion():
    # 'Cosamaloapan' con acentos/minúsculas debe seguir resolviendo por historia
    r = resolver_zona_cliente("cosamaloapán", 0, 0, HIST, CENTROIDES)
    assert r["via"] == "HISTORIA" and r["zona"] == "COSAMALOAPAN"


def test_fallback_si_no_hay_coordenada():
    r = resolver_zona_cliente("POBLACION NUEVA", None, None, HIST, CENTROIDES)
    assert r["zona"] is None
    assert r["via"] == "FALLBACK"
    assert "coordenada" in r["motivo"].lower()


def test_fallback_si_excede_el_umbral_de_distancia():
    # Zongolica: en la sierra, lejos de toda zona. No forzar un enganche absurdo.
    r = resolver_zona_cliente("ZONGOLICA", 18.67, -97.00, HIST, CENTROIDES,
                              max_km=40)
    assert r["zona"] is None
    assert r["via"] == "FALLBACK"
    assert r["distancia_km"] > 40


def test_umbral_configurable_permite_el_enganche_lejano():
    r = resolver_zona_cliente("ZONGOLICA", 18.67, -97.00, HIST, CENTROIDES,
                              max_km=500)
    assert r["via"] == "GEOGRAFIA"
    assert r["zona"] is not None


def test_determinista_ante_empate():
    # Dos zonas exactamente a la misma distancia: gana el nombre menor.
    cent = {"BBB": (18.0, -96.0), "AAA": (18.0, -96.0)}
    a = resolver_zona_cliente("X", 18.1, -96.0, {}, cent)
    b = resolver_zona_cliente("X", 18.1, -96.0, {}, dict(reversed(list(cent.items()))))
    assert a["zona"] == b["zona"] == "AAA"


def test_confianza_dos_ejes_frecuencia_y_nucleo():
    from logic.enganche_zona import confianza_zona
    # Consistencia = presencia del GRUPO NÚCLEO, no coincidencia del conjunto.
    # COSAMALOAPAN aparece con "4" y con "4|19", pero el g4 está SIEMPRE: la zona
    # no duda de su destino, lo que varía es qué otros grupos suben al camión.
    assert confianza_zona(semanas=7, pct_nucleo=1.00) == "ALTA"   # COSAMALOAPAN
    assert confianza_zona(semanas=7, pct_nucleo=1.00) == "ALTA"   # PLAYA VICENTE
    assert confianza_zona(semanas=5, pct_nucleo=1.00) == "ALTA"   # JALAPA DE DIAZ
    # COTAXTLA: 7 semanas pero su núcleo g31 sólo está en el 40 % de las paradas
    assert confianza_zona(semanas=7, pct_nucleo=0.40) == "BAJA"
    # CUITLAHUAC: 4 semanas, núcleo al 68 %
    assert confianza_zona(semanas=4, pct_nucleo=0.68) == "MEDIA"
    # frecuencia baja: débil aunque el núcleo sea perfecto
    assert confianza_zona(semanas=1, pct_nucleo=1.0) == "BAJA"


def test_confianza_no_usa_el_dia():
    """El día lo hereda del grupo y los días admisibles ya lo modelan aguas
    arriba; medirlo en la zona duplicaría esa variabilidad (JALAPA DE DIAZ tiene
    día 58 % sólo porque el g21 alterna entre sus admisibles)."""
    from logic.enganche_zona import confianza_zona
    import inspect
    assert "pct_dia" not in inspect.signature(confianza_zona).parameters


def test_confianza_tolera_datos_faltantes():
    from logic.enganche_zona import confianza_zona
    assert confianza_zona(None, None) == "BAJA"


def test_centroides_desde_clientes_promedia_los_de_cada_zona():
    from logic.enganche_zona import centroides_desde_clientes
    clientes = [
        {"poblacion": "COSAMALOAPAN", "latitud": 18.36, "longitud": -95.80},
        {"poblacion": "cosamaloapán", "latitud": 18.38, "longitud": -95.80},
        {"poblacion": "PLAYA VICENTE", "latitud": 18.09, "longitud": -96.12},
        {"poblacion": "SIN MAPEO", "latitud": 18.00, "longitud": -96.00},
        {"poblacion": "COSAMALOAPAN", "latitud": None, "longitud": None},
    ]
    cent = centroides_desde_clientes(clientes, HIST)
    assert set(cent) == {"COSAMALOAPAN", "TUXTEPEC"}      # 'SIN MAPEO' no entra
    assert abs(cent["COSAMALOAPAN"][0] - 18.37) < 1e-9    # promedia, ignora sin coord


def test_centroides_desde_clientes_sin_datos_no_rompe():
    from logic.enganche_zona import centroides_desde_clientes
    assert centroides_desde_clientes([], HIST) == {}
    assert centroides_desde_clientes(None, None) == {}


def test_historia_a_zona_inexistente_cae_a_geografia():
    # El diccionario apunta a una zona que ya no está en la plantilla.
    hist = {"RARA": "ZONA_QUE_NO_EXISTE"}
    r = resolver_zona_cliente("RARA", 18.41, -95.12, hist, CENTROIDES)
    assert r["via"] == "GEOGRAFIA"
    assert r["zona"] == "CATEMACO"


# ══ destino de enganche cuando el grupo núcleo no tiene ruta esa semana ═════
def _rutas(*claves):
    """{(unidad, dia): [grupos]} — rutas existentes esa semana."""
    return {k: list(v) for k, v in claves}


def test_destino_usa_el_nucleo_cuando_tiene_ruta():
    from logic.enganche_zona import resolver_destino_enganche
    rutas = {("V1", "LUNES"): [31], ("V2", "MARTES"): [4]}
    r = resolver_destino_enganche(nucleo=31, otros_grupos=[4], rutas_por_grupo=rutas,
                                  lat=18.0, lon=-96.0, coords_rutas={})
    assert r["destino"] == ("V1", "LUNES")
    assert r["via"] == "NUCLEO"


def test_destino_cae_al_segundo_grupo_si_el_nucleo_no_viaja():
    # El núcleo (g31, Amatlán) no tiene pedido esa semana: no hay ruta suya.
    from logic.enganche_zona import resolver_destino_enganche
    rutas = {("V2", "MARTES"): [4]}
    r = resolver_destino_enganche(nucleo=31, otros_grupos=[4], rutas_por_grupo=rutas,
                                  lat=18.0, lon=-96.0, coords_rutas={})
    assert r["destino"] == ("V2", "MARTES")
    assert r["via"] == "SEGUNDO_GRUPO"


def test_destino_por_geografia_si_ningun_grupo_de_la_zona_viaja():
    from logic.enganche_zona import resolver_destino_enganche
    rutas = {("V3", "JUEVES"): [77]}
    coords = {("V3", "JUEVES"): (18.01, -96.0)}
    r = resolver_destino_enganche(nucleo=31, otros_grupos=[4], rutas_por_grupo=rutas,
                                  lat=18.0, lon=-96.0, coords_rutas=coords)
    assert r["destino"] == ("V3", "JUEVES")
    assert r["via"] == "GEOGRAFIA_RUTA"


def test_destino_viaje_de_mayoristas_solo_como_ultimo_recurso():
    # En el histórico existen viajes que llevaron sólo mayoristas (Cotaxtla tuvo
    # 5), así que el caso es real — pero nunca es la primera opción.
    from logic.enganche_zona import resolver_destino_enganche
    r = resolver_destino_enganche(nucleo=31, otros_grupos=[], rutas_por_grupo={},
                                  lat=18.0, lon=-96.0, coords_rutas={})
    assert r["destino"] is None
    assert r["via"] == "VIAJE_MAYORISTAS_SOLO"


def test_destino_nunca_devuelve_none_silencioso():
    from logic.enganche_zona import resolver_destino_enganche
    r = resolver_destino_enganche(nucleo=None, otros_grupos=None, rutas_por_grupo=None,
                                  lat=None, lon=None, coords_rutas=None)
    assert r["via"] == "VIAJE_MAYORISTAS_SOLO"
    assert r["motivo"]


def test_destino_geografia_respeta_el_umbral():
    from logic.enganche_zona import resolver_destino_enganche
    rutas = {("V3", "JUEVES"): [77]}
    coords = {("V3", "JUEVES"): (19.5, -96.0)}      # ~165 km
    r = resolver_destino_enganche(nucleo=31, otros_grupos=[], rutas_por_grupo=rutas,
                                  lat=18.0, lon=-96.0, coords_rutas=coords, max_km=60)
    assert r["via"] == "VIAJE_MAYORISTAS_SOLO"


# ═══════════════════════════════════════════════════════════════════════════
# Peso de los mayoristas: anclaje y garantía de cupo
#
# El enganche por zona concentra carga: PLAZA COMERCIAL RIO mete 3,152 kg sobre
# una ruta cuyo grupo Lores lleva 447. Si esa carga no entra al motor, la ruta
# sale al 148 % y el planeador ve un camión que no existe. Dos piezas:
#   - ANCLAJE : a qué sucursal se le carga el peso del mayorista, para que el
#               builder lo vea y el peso VIAJE CON EL GRUPO (si el grupo cambia
#               de unidad o de día, su mayorista va detrás).
#   - CUPO    : garantía dura de que ninguna ruta queda por encima del 100 %,
#               reubicando o abriendo viaje, nunca desbordando en silencio.
# ═══════════════════════════════════════════════════════════════════════════

def test_ancla_es_la_sucursal_del_grupo_mas_cercana_al_cliente():
    from logic.enganche_zona import elegir_ancla_mayorista
    coords = {1: (18.0, -96.0), 2: (18.5, -96.0), 3: (18.9, -96.0)}
    # la 3 es la más cercana de la ruta, pero NO es del grupo resuelto
    sid = elegir_ancla_mayorista(sids_ruta=[1, 2, 3], sids_grupo=[1, 2],
                                 lat=18.6, lon=-96.0, coords=coords)
    assert sid == 2


def test_ancla_cae_a_la_ruta_cuando_el_grupo_no_esta():
    # destino resuelto por GEOGRAFIA_RUTA: no hay grupo de la zona en la ruta.
    from logic.enganche_zona import elegir_ancla_mayorista
    coords = {7: (18.0, -96.0), 9: (18.9, -96.0)}
    sid = elegir_ancla_mayorista([7, 9], [], 18.8, -96.0, coords)
    assert sid == 9


def test_ancla_sin_coordenadas_es_determinista():
    from logic.enganche_zona import elegir_ancla_mayorista
    assert elegir_ancla_mayorista([9, 7, 8], [], None, None, {}) == 7


def test_cupo_no_toca_las_rutas_que_caben():
    from logic.enganche_zona import reubicar_mayoristas_por_cupo
    por_ruta = {("V1", "LUNES"): [{"id_cliente": 1, "peso_kg": 100,
                                   "latitud": 18.0, "longitud": -96.0}]}
    nuevo, exc = reubicar_mayoristas_por_cupo(
        por_ruta, kg_lores={("V1", "LUNES"): 500}, vehiculos_cap={"V1": 1000},
        coords_rutas={("V1", "LUNES"): (18.0, -96.0)})
    assert nuevo == por_ruta
    assert exc == []


def test_cupo_reubica_el_mayorista_mas_pesado_a_otra_ruta_del_dia():
    from logic.enganche_zona import reubicar_mayoristas_por_cupo
    may_grande = {"id_cliente": 1, "peso_kg": 600, "latitud": 18.0, "longitud": -96.0}
    may_chico = {"id_cliente": 2, "peso_kg": 50, "latitud": 18.0, "longitud": -96.0}
    por_ruta = {("V1", "LUNES"): [may_grande, may_chico]}
    nuevo, exc = reubicar_mayoristas_por_cupo(
        por_ruta,
        kg_lores={("V1", "LUNES"): 500, ("V2", "LUNES"): 100},
        vehiculos_cap={"V1": 1000, "V2": 2000},
        coords_rutas={("V1", "LUNES"): (18.0, -96.0), ("V2", "LUNES"): (18.05, -96.0)})
    assert nuevo[("V1", "LUNES")] == [may_chico]          # el chico se queda
    assert nuevo[("V2", "LUNES")] == [may_grande]         # el grande se va
    assert exc and exc[0]["tipo"] == "MAYORISTA_REUBICADO_CUPO"
    assert exc[0]["desde_unidad"] == "V1" and exc[0]["a_unidad"] == "V2"


def test_cupo_abre_viaje_de_mayoristas_solo_si_no_hay_ruta_con_espacio():
    from logic.enganche_zona import reubicar_mayoristas_por_cupo
    may = {"id_cliente": 1, "peso_kg": 600, "latitud": 18.0, "longitud": -96.0}
    por_ruta = {("V1", "LUNES"): [may]}
    nuevo, exc = reubicar_mayoristas_por_cupo(
        por_ruta, kg_lores={("V1", "LUNES"): 500},
        vehiculos_cap={"V1": 1000, "V9": 800},           # V9 libre ese día
        coords_rutas={("V1", "LUNES"): (18.0, -96.0)})
    assert nuevo[("V9", "LUNES")] == [may]
    assert exc[0]["tipo"] == "VIAJE_MAYORISTAS_SOLO"


def test_cupo_prefiere_la_unidad_libre_mas_chica_que_alcance():
    # abrir un tráiler para 600 kg desperdicia flota; se toma la más chica que
    # todavía admita la carga.
    from logic.enganche_zona import reubicar_mayoristas_por_cupo
    may = {"id_cliente": 1, "peso_kg": 600, "latitud": 18.0, "longitud": -96.0}
    nuevo, exc = reubicar_mayoristas_por_cupo(
        {("V1", "LUNES"): [may]}, {("V1", "LUNES"): 500},
        {"V1": 1000, "GRANDE": 3900, "CHICA": 700, "MINI": 300},
        {("V1", "LUNES"): (18.0, -96.0)})
    assert ("CHICA", "LUNES") in nuevo


def test_cupo_nunca_pierde_un_pedido_ni_entra_en_bucle():
    # cliente más pesado que CUALQUIER unidad de la flota: no hay destino posible.
    # Se registra y se queda donde estaba — pero no desaparece ni cicla.
    from logic.enganche_zona import reubicar_mayoristas_por_cupo
    may = {"id_cliente": 1, "peso_kg": 9000, "latitud": 18.0, "longitud": -96.0}
    nuevo, exc = reubicar_mayoristas_por_cupo(
        {("V1", "LUNES"): [may]}, {("V1", "LUNES"): 0},
        {"V1": 1000}, {("V1", "LUNES"): (18.0, -96.0)})
    colocados = [m for lst in nuevo.values() for m in lst]
    assert len(colocados) == 1
    assert exc[0]["tipo"] == "MAYORISTA_SIN_CUPO"


def test_cupo_es_determinista_ante_orden_de_entrada_invertido():
    from logic.enganche_zona import reubicar_mayoristas_por_cupo
    ms = [{"id_cliente": i, "peso_kg": 300, "latitud": 18.0, "longitud": -96.0}
          for i in (1, 2, 3)]
    cap = {"V1": 1000, "V2": 1000}
    coords = {("V1", "LUNES"): (18.0, -96.0), ("V2", "LUNES"): (18.02, -96.0)}
    a, _ = reubicar_mayoristas_por_cupo({("V1", "LUNES"): list(ms)},
                                        {("V1", "LUNES"): 500, ("V2", "LUNES"): 0},
                                        cap, coords)
    b, _ = reubicar_mayoristas_por_cupo({("V1", "LUNES"): list(reversed(ms))},
                                        {("V1", "LUNES"): 500, ("V2", "LUNES"): 0},
                                        cap, coords)
    norm = lambda d: {k: sorted(m["id_cliente"] for m in v) for k, v in d.items() if v}
    assert norm(a) == norm(b)


def test_cupo_deja_todas_las_rutas_dentro_del_100_por_ciento():
    from logic.enganche_zona import reubicar_mayoristas_por_cupo
    ms = [{"id_cliente": i, "peso_kg": 400, "latitud": 18.0, "longitud": -96.0}
          for i in range(1, 6)]
    kg_lores = {("V1", "LUNES"): 800, ("V2", "LUNES"): 300}
    cap = {"V1": 1000, "V2": 1000, "V3": 2000}
    coords = {("V1", "LUNES"): (18.0, -96.0), ("V2", "LUNES"): (18.02, -96.0)}
    nuevo, exc = reubicar_mayoristas_por_cupo({("V1", "LUNES"): ms}, kg_lores,
                                              cap, coords)
    for clave, lst in nuevo.items():
        total = kg_lores.get(clave, 0) + sum(m["peso_kg"] for m in lst)
        assert total <= cap[clave[0]], f"{clave} quedó en {total} sobre {cap[clave[0]]}"


def test_una_parada_consolidada_que_no_cabe_se_parte_a_proposito():
    # La parada consolidada es carga indivisible, pero si NINGUNA ruta ni unidad
    # libre la admite, partirla a propósito es mejor que dejar la ruta marcada
    # por encima del 100 %. La partición va por folios completos y se registra.
    from logic.enganche_zona import reubicar_mayoristas_por_cupo
    parada = {"id_cliente": 173, "nombre": "PLAZA COMERCIAL RIO", "peso_kg": 3153.0,
              "latitud": 17.83, "longitud": -95.82,
              "folios": ["BB3304", "BB3320", "BB3321"],
              "documentos": [{"documento": "BB3304", "peso_total_kg": 241.0},
                             {"documento": "BB3320", "peso_total_kg": 1098.0},
                             {"documento": "BB3321", "peso_total_kg": 1814.0}]}
    # V2 libre da destino a la parte sobrante; sin ella no hay partición que
    # sirva (las dos mitades seguirían en el mismo camión).
    nuevo, exc = reubicar_mayoristas_por_cupo(
        {("V1", "LUNES"): [parada]}, {("V1", "LUNES"): 0},
        {"V1": 2500, "V2": 1500}, {("V1", "LUNES"): (17.83, -95.82)})
    for clave, lst in nuevo.items():
        assert sum(m["peso_kg"] for m in lst) <= 2500 + 1e-6
    folios = sorted(f for lst in nuevo.values() for m in lst for f in m["folios"])
    assert folios == ["BB3304", "BB3320", "BB3321"]      # ningún folio perdido
    assert any(e["tipo"] == "PARADA_MAYORISTA_PARTIDA" for e in exc)
