"""
tests/test_mayoristas_logic.py

Reparto de mayoristas por zona histórica real, en vez de solo geografía del
momento (mismo defecto de fondo ya corregido para sucursales — ver
docs/superpowers/specs/2026-08-07-tiempo-entrega-faseB-grupos-rigidos-design.md
§1 — encontrado el 2026-08-07 con datos reales: AMAVER/Amatitlán y LA
CONA/Dos Bocas caían en F 350_1 martes por cercanía geográfica hoy, cuando su
histórico real (`plantilla_zona_mayorista`) dice que viajan viernes/jueves con
otro vehículo).

- Pruebas PURAS (sin BD): `_seleccionar_ruta_por_zona` contra una caché en
  memoria.
- Pruebas de BD (`_construir_cache_zonas`): se saltan si no hay conexión a
  SQL Server, mismo criterio que `test_plantilla_canonica.py`.
"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from logic.mayoristas_logic import _seleccionar_ruta_por_zona


# ── _seleccionar_ruta_por_zona (puro) ───────────────────────────────────────
def test_seleccionar_ruta_por_zona_usa_la_cache_por_poblacion():
    cache = {"AMATITLAN": "vrpaf_f_350_3_jueves", "DOS BOCAS": "vrpaf_f_350_3_jueves"}
    m = {"id_cliente": 282, "nombre": "ABARROTES AMAVER", "poblacion": "AMATITLAN"}
    assert _seleccionar_ruta_por_zona(m, cache) == "vrpaf_f_350_3_jueves"


def test_seleccionar_ruta_por_zona_normaliza_acentos_y_mayusculas():
    # La plantilla puede traer 'AMATITLÁN' con acento o distinta mayúscula
    # que `clientes_mayoristas.poblacion` -- no debe perder el match por eso.
    cache = {"AMATITLAN": "vrpaf_f_350_3_jueves"}
    m = {"id_cliente": 282, "poblacion": "amatitlán"}
    assert _seleccionar_ruta_por_zona(m, cache) == "vrpaf_f_350_3_jueves"

    m2 = {"id_cliente": 282, "poblacion": "  Amatitlan  "}
    assert _seleccionar_ruta_por_zona(m2, cache) == "vrpaf_f_350_3_jueves"


def test_seleccionar_ruta_por_zona_sin_cache_es_none():
    # Cae al criterio geográfico de siempre (_seleccionar_ruta) -- degradación
    # segura si la plantilla canónica no está cargada.
    m = {"id_cliente": 282, "poblacion": "AMATITLAN"}
    assert _seleccionar_ruta_por_zona(m, None) is None
    assert _seleccionar_ruta_por_zona(m, {}) is None


def test_seleccionar_ruta_por_zona_sin_poblacion_es_none():
    cache = {"AMATITLAN": "vrpaf_f_350_3_jueves"}
    assert _seleccionar_ruta_por_zona({"id_cliente": 282}, cache) is None
    assert _seleccionar_ruta_por_zona({"id_cliente": 282, "poblacion": ""}, cache) is None


def test_seleccionar_ruta_por_zona_poblacion_sin_zona_registrada_es_none():
    # Ej. real: "CARLOS A. CARRILLO" no tiene fila en plantilla_poblacion_zona
    # -- cae a geografía, que en ese caso sí acierta (es la sucursal misma).
    cache = {"AMATITLAN": "vrpaf_f_350_3_jueves"}
    m = {"id_cliente": 406, "poblacion": "CARLOS A. CARRILLO"}
    assert _seleccionar_ruta_por_zona(m, cache) is None


# ── _construir_cache_zonas (contra BD real) ────────────────────────────────
@pytest.fixture(scope="module")
def app_ctx():
    try:
        from app import create_app
        app = create_app()
        ctx = app.app_context(); ctx.push()
        from db import get_db, get_table
        get_db().execute  # fuerza apertura de conexión
        get_table("plantilla_zona_mayorista")
    except Exception as e:  # sin BD o sin tablas
        pytest.skip(f"BD no disponible: {e}")
        return
    yield app
    ctx.pop()


def test_construir_cache_zonas_resuelve_grupo_nucleo_real(app_ctx):
    from db import get_db
    from logic.mayoristas_logic import _construir_cache_zonas
    from logic.plantilla_canonica import obtener_grupos, _norm

    db = get_db()
    grupos = obtener_grupos()
    # grupo 19 real: FLEXIBLE, sucursales [86 (Carlos A. Carrillo 2), 100 (Amatitlán)].
    g19 = next((g for g in grupos if g["grupo"] == 19), None)
    if g19 is None:
        pytest.skip("grupo 19 no existe en la plantilla vigente de este entorno")

    nt_g19 = g19["sucursales"][0]
    rutas_sucursales = {
        "RUTA_CON_G19": [{"num_tienda": nt_g19}],
        "RUTA_SIN_G19": [{"num_tienda": 999999}],  # num_tienda inexistente a propósito
    }
    cache = _construir_cache_zonas(db, rutas_sucursales)
    if cache is None:
        pytest.skip("plantilla_poblacion_zona/plantilla_zona_mayorista vacías en este entorno")

    # AMATITLAN -> zona AMATITLAN -> grupo núcleo 19 (verificado a mano el
    # 2026-08-07 contra la BD real) -> debe resolver a la ruta que sí trae
    # una sucursal del grupo 19, nunca a la que no.
    if _norm("AMATITLAN") in cache:
        assert cache[_norm("AMATITLAN")] == "RUTA_CON_G19"


def test_construir_cache_zonas_sin_coincidencia_de_grupo_no_entra_en_la_cache(app_ctx):
    from db import get_db
    from logic.mayoristas_logic import _construir_cache_zonas

    db = get_db()
    # Ninguna ruta de esta semana trae ninguna sucursal real -> ningún grupo
    # núcleo puede resolver a una ruta -> la caché queda vacía (o None), pero
    # nunca inventa una ruta que no fue pasada.
    rutas_sucursales = {"RUTA_X": [{"num_tienda": 999999}]}
    cache = _construir_cache_zonas(db, rutas_sucursales)
    if cache:
        assert "RUTA_X" not in cache.values()


# ── guardar_mayoristas_convrp / obtener_mayoristas_guardados ───────────────
def test_guardar_mayoristas_convrp_secuencia_por_proximidad(app_ctx):
    from logic.mayoristas_logic import guardar_mayoristas_convrp
    from db import get_db, get_table
    from sqlalchemy import select
    # ruta con 2 sucursales; sin histórico, `_ordenar_mayoristas_en_ruta`
    # ordena por cercanía al CENTROIDE de la ruta -- no por el orden en que
    # aparecen en `por_ruta` (aquí se insertan a propósito en orden inverso
    # al esperado, para probar que sí se reordenan).
    lid = "507f1f77bcf86cd799439011"
    por_ruta = {
        ("V1", "LUNES"): [
            {"id_cliente": 902, "nombre": "LEJANO", "peso_kg": 30.0,
             "latitud": 25.000, "longitud": -100.000, "poblacion": "PRUEBA"},
            {"id_cliente": 901, "nombre": "CERCANO", "peso_kg": 20.0,
             "latitud": 19.001, "longitud": -96.001, "poblacion": "PRUEBA"},
        ],
    }
    detalle = [
        {"id_cliente": 901, "via_zona": "HISTORIA", "via_destino": "NUCLEO"},
        {"id_cliente": 902, "via_zona": "HISTORIA", "via_destino": "NUCLEO"},
    ]
    rutas = [
        {"_id": "vrpaf_v1_lunes", "sucursales": [
            {"num_tienda": 1, "latitud": 19.500, "longitud": -96.500, "orden": 1},
            {"num_tienda": 2, "latitud": 19.000, "longitud": -96.000, "orden": 2},
        ]},
    ]
    n = guardar_mayoristas_convrp(lid, por_ruta, detalle, rutas)
    assert n == 2

    db = get_db()
    t = get_table("convrp_mayoristas")
    filas = list(db.execute(
        select(t).where(t.c.logistica_id == lid).order_by(t.c.orden)
    ).mappings())
    # el cercano (901) debe quedar con orden menor que el lejano (902),
    # aunque en `por_ruta` se pasó primero el lejano.
    assert [f["id_cliente"] for f in filas] == [901, 902]


def test_guardar_mayoristas_convrp_reemplaza_corrida_anterior(app_ctx):
    from logic.mayoristas_logic import guardar_mayoristas_convrp
    from db import get_db, get_table
    from sqlalchemy import select
    lid = "507f1f77bcf86cd799439012"
    rutas = [{"_id": "vrpaf_v1_lunes", "sucursales": [
        {"num_tienda": 1, "latitud": 19.0, "longitud": -96.0, "orden": 1}]}]
    por_ruta_1 = {("V1", "LUNES"): [
        {"id_cliente": 1, "nombre": "A", "peso_kg": 10.0, "latitud": 19.0, "longitud": -96.0}]}
    por_ruta_2 = {("V1", "LUNES"): [
        {"id_cliente": 2, "nombre": "B", "peso_kg": 20.0, "latitud": 19.0, "longitud": -96.0}]}
    guardar_mayoristas_convrp(lid, por_ruta_1, [], rutas)
    guardar_mayoristas_convrp(lid, por_ruta_2, [], rutas)
    db = get_db()
    t = get_table("convrp_mayoristas")
    filas = list(db.execute(select(t).where(t.c.logistica_id == lid)).mappings())
    assert len(filas) == 1
    assert filas[0]["id_cliente"] == 2


def test_guardar_mayoristas_convrp_sin_mayoristas_devuelve_cero(app_ctx):
    from logic.mayoristas_logic import guardar_mayoristas_convrp
    n = guardar_mayoristas_convrp("507f1f77bcf86cd799439013", {}, [], [])
    assert n == 0


def test_guardar_mayoristas_convrp_fusiona_parada_partida_por_capacidad(app_ctx):
    # `logic/consolidacion_mayoristas.partir_parada_por_capacidad` puede
    # producir dos entradas con el mismo id_cliente en la misma ruta (mismo
    # cliente, folio partido por capacidad). La PK de `convrp_mayoristas` es
    # (logistica_id, unidad, dia, id_cliente) -- deben fusionarse en una sola
    # fila sumando peso_kg, o el INSERT viola la PK y se pierde toda la
    # corrida (la excepción amplia devolvería -1 después de un DELETE ya
    # comprometido).
    from logic.mayoristas_logic import guardar_mayoristas_convrp
    from db import get_db, get_table
    from sqlalchemy import select
    lid = "507f1f77bcf86cd799439014"
    rutas = [{"_id": "vrpaf_v1_lunes", "sucursales": [
        {"num_tienda": 1, "latitud": 19.0, "longitud": -96.0, "orden": 1}]}]
    por_ruta = {("V1", "LUNES"): [
        {"id_cliente": 5, "nombre": "PARTIDO", "peso_kg": 15.0,
         "latitud": 19.0, "longitud": -96.0},
        {"id_cliente": 5, "nombre": "PARTIDO", "peso_kg": 25.0,
         "latitud": 19.0, "longitud": -96.0},
    ]}
    n = guardar_mayoristas_convrp(lid, por_ruta, [], rutas)
    assert n == 1

    db = get_db()
    t = get_table("convrp_mayoristas")
    filas = list(db.execute(select(t).where(t.c.logistica_id == lid)).mappings())
    assert len(filas) == 1
    assert filas[0]["id_cliente"] == 5
    assert filas[0]["peso_kg"] == 40.0


def test_guardar_mayoristas_convrp_sin_coordenadas_no_rompe(app_ctx):
    # Ejercita la rama `mayoristas_sin` de `_ordenar_mayoristas_en_ruta`
    # (sin lat/lon, no puede ordenarse por proximidad) -- no debe reventar
    # ni perderse la fila.
    from logic.mayoristas_logic import guardar_mayoristas_convrp
    from db import get_db, get_table
    from sqlalchemy import select
    lid = "507f1f77bcf86cd799439015"
    rutas = [{"_id": "vrpaf_v1_lunes", "sucursales": [
        {"num_tienda": 1, "latitud": 19.0, "longitud": -96.0, "orden": 1}]}]
    por_ruta = {("V1", "LUNES"): [
        {"id_cliente": 6, "nombre": "SIN COORDS", "peso_kg": 12.0,
         "latitud": None, "longitud": None},
    ]}
    n = guardar_mayoristas_convrp(lid, por_ruta, [], rutas)
    assert n == 1

    db = get_db()
    t = get_table("convrp_mayoristas")
    filas = list(db.execute(select(t).where(t.c.logistica_id == lid)).mappings())
    assert len(filas) == 1
    assert filas[0]["id_cliente"] == 6


def test_obtener_mayoristas_guardados_reconstruye_forma_esperada(app_ctx):
    from logic.mayoristas_logic import guardar_mayoristas_convrp, obtener_mayoristas_guardados
    lid = "507f1f77bcf86cd799439014"
    rutas = [{"_id": "vrpaf_v1_lunes", "sucursales": [
        {"num_tienda": 1, "nombre_base": "Suc 1", "latitud": 19.0, "longitud": -96.0,
         "orden": 1, "peso_kg": 500}]}]
    por_ruta = {("V1", "LUNES"): [
        {"id_cliente": 5, "nombre": "ABARROTES Y", "peso_kg": 30.0,
         "latitud": 19.001, "longitud": -96.001}]}
    guardar_mayoristas_convrp(lid, por_ruta, [], rutas)
    dist = obtener_mayoristas_guardados(lid, rutas)
    assert dist is not None
    assert dist["mayoristas_por_ruta"]["vrpaf_v1_lunes"][0]["id_cliente"] == 5
    assert dist["mayoristas_por_ruta"]["vrpaf_v1_lunes"][0]["peso_kg"] == 30.0
    paradas = dist["paradas_integradas"]["vrpaf_v1_lunes"]
    assert any(p["tipo"] == "mayorista" and p["id_cliente"] == 5 for p in paradas)
    assert any(p["tipo"] == "sucursal" and p["num_tienda"] == 1 for p in paradas)
    assert dist["orden_sucursales"]["vrpaf_v1_lunes"]["1"] is not None


def test_obtener_mayoristas_guardados_sin_filas_es_none(app_ctx):
    # NOTA: se usa un lid distinto ("...9016") al del enunciado ("...9015")
    # porque ese último ya lo puebla `test_guardar_mayoristas_convrp_sin_coordenadas_no_rompe`
    # (fila id_cliente=6, nunca borrada) -- reutilizarlo hacía que esta prueba
    # encontrara esa fila ajena y fallara la aserción `dist is None` de forma
    # determinista, no por un error de la implementación.
    from logic.mayoristas_logic import obtener_mayoristas_guardados
    dist = obtener_mayoristas_guardados("507f1f77bcf86cd799439016", [])
    assert dist is None


def test_obtener_mayoristas_guardados_no_colisiona_orden_y_trae_coords(app_ctx):
    # id_cliente 709 ("SUPER LA DESPENSA") se eligió porque ya existe como
    # fila real en `clientes_mayoristas` en este entorno (verificado por
    # consulta directa antes de escribir la prueba) -- `obtener_mayoristas_guardados`
    # backfillea lat/lon desde esa tabla por `id_cliente`, no desde lo que se
    # le pasa a `guardar_mayoristas_convrp` (esa tabla ni siquiera tiene
    # columnas de coordenadas), así que un id inventado (p.ej. 900) resolvería
    # a coords None y la aserción fallaría por falta de fixture, no por un
    # bug real. Se usa un id ya presente para no tener que insertar y limpiar
    # una fila de prueba en una tabla que ningún otro test de este archivo toca.
    from logic.mayoristas_logic import guardar_mayoristas_convrp, obtener_mayoristas_guardados
    lid = "507f1f77bcf86cd799439017"
    rutas = [{"_id": "vrpaf_v1_martes", "sucursales": [
        {"num_tienda": 1, "latitud": 19.500, "longitud": -96.500, "orden": 1},
        {"num_tienda": 2, "latitud": 19.000, "longitud": -96.000, "orden": 2},
    ]}]
    por_ruta = {("V1", "MARTES"): [
        {"id_cliente": 709, "nombre": "SUPER LA DESPENSA", "peso_kg": 40.0,
         "latitud": 19.370, "longitud": -96.376, "poblacion": "José Cardel"},
    ]}
    guardar_mayoristas_convrp(lid, por_ruta, [], rutas)
    dist = obtener_mayoristas_guardados(lid, rutas)
    paradas = dist["paradas_integradas"]["vrpaf_v1_martes"]
    ordenes = [p["orden"] for p in paradas]
    assert len(ordenes) == len(set(ordenes)), f"orden colisiona: {ordenes}"
    mayorista = next(p for p in paradas if p["tipo"] == "mayorista")
    assert mayorista["latitud"] is not None
    assert mayorista["longitud"] is not None
