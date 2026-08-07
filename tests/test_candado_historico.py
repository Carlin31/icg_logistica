"""
tests/test_candado_historico.py

Candado sobre `rutas_historicas`: las 9 semanas canónicas no se reescriben.

Por qué existe: al guardar en el módulo de Modificación, el front dispara
`POST /modificacion/guardar-historico` en fire-and-forget
(`static/js/modificacion.js`), y `guardar_en_historico` hace **upsert**. Es
decir, cualquiera que abra una semana vieja y guarde reemplaza el histórico
canónico — y como `cargado_en` se fabrica desde la fecha de inicio de la
logística, no queda rastro de cuándo pasó.

Eso ya ocurrió con 18-22 mayo el 2026-08-03. La capa de sucursales sobrevivió
porque lo que estaba cargado era el plan del planeador, pero fue suerte.

Regla: si la logística corresponde a una de las 9 semanas canónicas, el guardado
se RECHAZA con un mensaje legible, salvo que el llamador pase `permitir_canon`
explícitamente (uso programático, nunca desde la UI).
"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import SEMANAS_CANONICAS, es_semana_canonica


def test_las_nueve_semanas_del_corpus_estan_en_la_lista():
    assert len(SEMANAS_CANONICAS) == 9
    # las tres que más se han tocado en la sesión
    assert es_semana_canonica("2026-05-18")      # 18-22 mayo
    assert es_semana_canonica("2026-06-01")      # 1-5 junio
    assert es_semana_canonica("2026-06-15")      # 15-19 junio


def test_las_semanas_de_julio_no_son_canonicas():
    # Son salida del motor (claves vrpaf_), no plan del planeador: no hay nada
    # que proteger y no deben confundirse con el corpus.
    assert not es_semana_canonica("2026-07-13")
    assert not es_semana_canonica("2026-07-27")


def test_una_semana_nueva_no_esta_protegida():
    assert not es_semana_canonica("2026-08-10")
    assert not es_semana_canonica("")
    assert not es_semana_canonica(None)


def test_tolera_formatos_de_fecha_con_hora():
    # `logisticas.fecha_inicio` a veces trae la hora pegada.
    assert es_semana_canonica("2026-05-18T00:00:00")
    assert es_semana_canonica(" 2026-05-18 ")


def test_el_candado_rechaza_y_explica(monkeypatch):
    # No debe escribir NADA cuando la semana es canónica.
    import logic.historico_logic as hl
    escrituras = []
    monkeypatch.setattr(hl, "_fecha_inicio_de_logistica", lambda lid: "2026-05-18")
    monkeypatch.setattr(hl, "get_db", lambda: escrituras.append("¡escribió!"))
    r = hl.guardar_en_historico("cualquier-id", "X", [{"num_tienda": 1, "vehiculo": "V1",
                                                       "dia_semana": "LUNES",
                                                       "secuencia_visita": 1,
                                                       "kg_entrega": 10}])
    assert r["status"] == "error"
    assert r["codigo"] == "SEMANA_CANONICA"          # el front puede distinguirlo
    assert "2026-05-18" in r["mensaje"]              # dice CUÁL semana
    assert len(r["mensaje"]) > 60                    # explica, no sólo rechaza
    assert escrituras == [], "el candado no debe llegar a tocar la BD"


def test_el_flag_explicito_levanta_el_candado(monkeypatch):
    import logic.historico_logic as hl
    monkeypatch.setattr(hl, "_fecha_inicio_de_logistica", lambda lid: "2026-05-18")
    llamadas = []
    monkeypatch.setattr(hl, "_escribir_historico",
                        lambda *a, **k: llamadas.append(1) or {"status": "ok"})
    r = hl.guardar_en_historico("cualquier-id", "X",
                                [{"num_tienda": 1, "vehiculo": "V1",
                                  "dia_semana": "LUNES", "secuencia_visita": 1,
                                  "kg_entrega": 10}],
                                permitir_canon=True)
    assert r["status"] == "ok" and llamadas == [1]


def test_una_semana_no_canonica_pasa_sin_flag(monkeypatch):
    import logic.historico_logic as hl
    monkeypatch.setattr(hl, "_fecha_inicio_de_logistica", lambda lid: "2026-08-10")
    llamadas = []
    monkeypatch.setattr(hl, "_escribir_historico",
                        lambda *a, **k: llamadas.append(1) or {"status": "ok"})
    r = hl.guardar_en_historico("otra", "X", [{"num_tienda": 1, "vehiculo": "V1",
                                               "dia_semana": "LUNES",
                                               "secuencia_visita": 1, "kg_entrega": 10}])
    assert r["status"] == "ok" and llamadas == [1]


# ── el camino de MAYOREO escribe por su cuenta ─────────────────────────────
# `mayoristas_logic._guardar_historico_mayoristas` no pasa por
# `guardar_en_historico`: hace un INSERT directo sobre `rutas_historicas`. Es
# una reimplementación paralela, así que el candado hay que ponerlo también
# ahí o queda un camino abierto al corpus.
#
# Atenúa el daño que ese camino sólo INSERTA (nunca hace upsert), así que no
# puede reemplazar una semana canónica — pero sí puede ensuciarla con filas
# nuevas atribuidas a esa fecha.

def test_el_camino_de_mayoreo_tambien_respeta_el_candado(monkeypatch):
    import logic.mayoristas_logic as ml
    escrituras = []
    monkeypatch.setattr(ml, "_fecha_inicio_de_logistica", lambda lid: "2026-06-01")
    monkeypatch.setattr(ml, "get_db", lambda: escrituras.append("¡escribió!"))
    r = ml._guardar_historico_mayoristas("id-x", "Mayoristas confirmados",
                                         [{"id_cliente": 1, "vehiculo": "V1",
                                           "dia_semana": "LUNES", "kg_entrega": 5}])
    assert r["status"] == "error" and r["codigo"] == "SEMANA_CANONICA"
    assert escrituras == []


def test_mayoreo_sin_semana_atribuible_tambien_se_rechaza(monkeypatch):
    # Una escritura al histórico que no se puede atribuir a una semana es basura
    # por construcción: este camino ya dejó una fila huérfana (logística
    # borrada, id_cliente 8888888, ruta_test_1) que nos costó una vuelta de
    # conteo. Sin fecha, no se escribe.
    import logic.mayoristas_logic as ml
    monkeypatch.setattr(ml, "_fecha_inicio_de_logistica", lambda lid: "")
    llamadas = []
    monkeypatch.setattr(ml, "_insertar_historico_mayoristas",
                        lambda *a, **k: llamadas.append(1) or {"status": "ok"})
    r = ml._guardar_historico_mayoristas("fantasma", "X", [{"id_cliente": 1}])
    assert r["status"] == "error" and r["codigo"] == "SIN_SEMANA"
    assert llamadas == []
