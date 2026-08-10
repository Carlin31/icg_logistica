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
