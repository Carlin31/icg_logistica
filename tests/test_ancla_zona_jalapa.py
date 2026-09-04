"""
tests/test_ancla_zona_jalapa.py

La capa de zona de mayoristas (poblacion -> zona -> grupo nucleo -> ruta) esta
inactiva desde el 2026-08-12: `plantilla_poblacion_zona` quedo con 0 filas
vigentes al cargarse las versiones 16/17 sin el CSV de poblaciones, asi que
`_construir_cache_zonas` devolvia None y TODOS los mayoristas se enganchaban
por geografia.

scripts/anclar_zona_jalapa_de_diaz.py la reactiva SOLO para la zona JALAPA DE
DIAZ (decision del usuario 2026-09-04: restaurarla entera movia 30 clientes de
ruta y deshacia arreglos ya validados). Estas pruebas fijan ese alcance.
"""
import pytest

from logic.mayoristas_logic import _seleccionar_ruta_por_zona

ZONA = "JALAPA DE DIAZ"
POBLACIONES = {"JALAPA DE DIAZ", "JALAPA", "SAN FELIPE JALAPA DE DIAZ"}
# Sucursales Lores de Jalapa de Diaz (grupo nucleo de la zona).
SUC_JALAPA = (35, 97)


def test_seleccionar_ruta_por_zona_sin_cache_no_engancha():
    # Contrato de degradacion: sin capa de zona el llamador debe caer a
    # `_seleccionar_ruta` (geografia), no quedarse sin ruta.
    m = {"poblacion": "JALAPA", "latitud": 18.071, "longitud": -96.537}
    assert _seleccionar_ruta_por_zona(m, None) is None
    assert _seleccionar_ruta_por_zona(m, {}) is None


def test_seleccionar_ruta_por_zona_ignora_mayorista_sin_poblacion():
    assert _seleccionar_ruta_por_zona({"poblacion": ""}, {"JALAPA": "r1"}) is None


# ── Integración con BD real (se salta si no hay SQL Server) ────────────────
@pytest.fixture(scope="module")
def app_ctx():
    try:
        from app import create_app
        app = create_app()
        ctx = app.app_context(); ctx.push()
        from db import get_db, get_table
        get_db().execute  # fuerza apertura de conexion
        get_table("plantilla_poblacion_zona")
        get_table("plantilla_zona_mayorista")
    except Exception as e:  # sin BD o sin las tablas
        pytest.skip(f"BD no disponible: {e}")
        return
    yield app
    ctx.pop()


def test_poblaciones_de_jalapa_estan_vigentes(app_ctx):
    from sqlalchemy import select
    from db import get_db, get_table
    t = get_table("plantilla_poblacion_zona")
    vigentes = {r.poblacion: r.zona for r in
                get_db().execute(select(t.c.poblacion, t.c.zona).where(t.c.vigente == True))}
    assert POBLACIONES <= set(vigentes), f"faltan poblaciones vigentes: {POBLACIONES - set(vigentes)}"
    for p in POBLACIONES:
        assert vigentes[p] == ZONA


def test_zona_jalapa_tiene_grupo_nucleo_y_no_es_regla_debil(app_ctx):
    # Sin `grupo_nucleo` la zona no tiene destino de enganche y
    # `_construir_cache_zonas` la ignora -- fue exactamente el estado que dejo
    # la capa muerta. Y una zona BAJA (<3 semanas o nucleo <0.60) no debe
    # ganarle a la geografia.
    from sqlalchemy import select
    from db import get_db, get_table
    t = get_table("plantilla_zona_mayorista")
    fila = get_db().execute(
        select(t).where(t.c.zona == ZONA, t.c.vigente == True)).mappings().first()
    assert fila is not None, f"la zona {ZONA} no tiene fila vigente"
    assert fila["grupo_nucleo"] is not None
    assert fila["confianza"] in ("ALTA", "MEDIA")


def test_cache_de_zonas_engancha_jalapa_a_la_ruta_de_sus_sucursales(app_ctx):
    # El caso real: BB4145_FARMA PRONTO JALAPA (cliente 555, poblacion
    # "JALAPA") debe resolver a la ruta que lleva las sucursales Lores de
    # Jalapa de Diaz, por HISTORIA -- no por la ruta que le quede mas cerca
    # esa semana.
    from db import get_db
    from logic.mayoristas_logic import _construir_cache_zonas

    ruta_jalapa = "ruta_jalapa"
    rutas = {
        ruta_jalapa: [{"num_tienda": SUC_JALAPA[0], "latitud": 18.0712, "longitud": -96.5369},
                      {"num_tienda": SUC_JALAPA[1], "latitud": 18.0739, "longitud": -96.5370}],
        "ruta_tuxtepec": [{"num_tienda": 46, "latitud": 18.0856, "longitud": -96.1300},
                          {"num_tienda": 57, "latitud": 18.0696, "longitud": -96.1240}],
    }
    cache = _construir_cache_zonas(get_db(), rutas)
    assert cache, "la capa de zona quedo inactiva (cache None o vacio)"

    m = {"poblacion": "JALAPA", "latitud": 18.071048, "longitud": -96.537577}
    assert _seleccionar_ruta_por_zona(m, cache) == ruta_jalapa


def test_el_ancla_no_engancha_poblaciones_ajenas_a_jalapa(app_ctx):
    # Alcance: reactivar la zona de Jalapa no debe enganchar por historia a
    # mayoristas de otras poblaciones -- esos siguen cayendo a geografia,
    # igual que antes del ancla.
    from db import get_db
    from logic.mayoristas_logic import _construir_cache_zonas
    rutas = {
        "ruta_jalapa": [{"num_tienda": 35, "latitud": 18.0712, "longitud": -96.5369}],
        "ruta_tuxtepec": [{"num_tienda": 46, "latitud": 18.0856, "longitud": -96.1300}],
    }
    cache = _construir_cache_zonas(get_db(), rutas)
    for poblacion in ("TUXTEPEC", "COTAXTLA", "TLACOJALPAN", "LA TINAJA"):
        assert _seleccionar_ruta_por_zona({"poblacion": poblacion}, cache) is None, \
            f"{poblacion} quedo enganchada por zona; el ancla debia limitarse a {ZONA}"
