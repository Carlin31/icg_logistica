"""
tests/test_orden_fijo_paradas.py
"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from logic.orden_fijo_paradas import aplicar_orden_fijo


def test_aplica_orden_fijo_cuando_toda_la_ruta_pertenece_a_la_regla():
    orden_fijo = {4: ("regla_a", 1), 27: ("regla_a", 2), 75: ("regla_a", 3)}
    miembros = [{"sid": 75}, {"sid": 4}, {"sid": 27}]
    assert aplicar_orden_fijo(miembros, orden_fijo) == [4, 27, 75]


def test_aplica_orden_fijo_con_sucursales_faltantes_esa_semana():
    orden_fijo = {4: ("regla_a", 1), 27: ("regla_a", 2), 75: ("regla_a", 3)}
    miembros = [{"sid": 75}, {"sid": 4}]          # sin la 27 esta semana
    assert aplicar_orden_fijo(miembros, orden_fijo) == [4, 75]


def test_no_aplica_si_hay_una_sucursal_ajena_a_la_regla():
    orden_fijo = {4: ("regla_a", 1), 27: ("regla_a", 2)}
    miembros = [{"sid": 4}, {"sid": 27}, {"sid": 999}]   # 999 no está en ninguna regla
    assert aplicar_orden_fijo(miembros, orden_fijo) is None


def test_no_aplica_si_mezcla_dos_reglas_distintas():
    orden_fijo = {4: ("regla_a", 1), 100: ("regla_b", 1)}
    miembros = [{"sid": 4}, {"sid": 100}]
    assert aplicar_orden_fijo(miembros, orden_fijo) is None


def test_no_aplica_con_orden_fijo_vacio():
    miembros = [{"sid": 4}, {"sid": 27}]
    assert aplicar_orden_fijo(miembros, {}) is None


def test_no_aplica_con_miembros_vacios():
    assert aplicar_orden_fijo([], {4: ("regla_a", 1)}) is None


# ── Validación de colisión de num_tienda entre reglas (scripts/cargar_orden_fijo.py) ──
from scripts.cargar_orden_fijo import cargar


def test_cargar_rechaza_num_tienda_en_dos_reglas_distintas(tmp_path):
    csv_path = tmp_path / "orden_fijo_paradas_colision.csv"
    csv_path.write_text(
        "nombre_regla,num_tienda,posicion\n"
        "regla_a,4,1\n"
        "regla_b,4,1\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="num_tienda 4"):
        cargar(str(csv_path))


# ── Integración con BD real (se salta si no hay SQL Server) ────────────────
@pytest.fixture(scope="module")
def app_ctx():
    try:
        from app import create_app
        app = create_app()
        ctx = app.app_context(); ctx.push()
        from db import get_db, get_table
        get_db().execute  # fuerza apertura de conexión
        get_table("orden_fijo_paradas")
    except Exception as e:  # sin BD o sin la tabla
        pytest.skip(f"BD no disponible: {e}")
        return
    yield app
    ctx.pop()


def test_obtener_orden_fijo_lee_la_tabla_real(app_ctx):
    from db import get_table, transaccion, get_db
    from logic.orden_fijo_paradas import obtener_orden_fijo

    t = get_table("orden_fijo_paradas")
    regla_prueba = "prueba_test_orden_fijo_paradas"
    # num_tienda ficticios y fuera de rango de cualquier tienda real, para no
    # chocar con num_tienda de reglas reales ya cargadas (la PK es compuesta
    # (nombre_regla, num_tienda), así que un num_tienda real bajo -p.ej. 1 o 2-
    # coexistiría sin error con la fila de otra regla y volvería el resultado
    # de obtener_orden_fijo no determinístico para esa clave).
    sid_a, sid_b = 999901, 999902
    with transaccion() as conn:
        conn.execute(t.delete().where(t.c.nombre_regla == regla_prueba))
        conn.execute(t.insert(), [
            {"nombre_regla": regla_prueba, "num_tienda": sid_a, "posicion": 1},
            {"nombre_regla": regla_prueba, "num_tienda": sid_b, "posicion": 2},
        ])
    try:
        orden_fijo = obtener_orden_fijo(get_db())
        assert orden_fijo.get(sid_a) == (regla_prueba, 1)
        assert orden_fijo.get(sid_b) == (regla_prueba, 2)
    finally:
        with transaccion() as conn:
            conn.execute(t.delete().where(t.c.nombre_regla == regla_prueba))


# ── Regresión con las dos reglas reales cargadas por scripts/cargar_orden_fijo.py ──
def test_regresion_orden_fijo_cosamaloapan_carrillo_amatitlan(app_ctx):
    from db import get_db
    from logic.orden_fijo_paradas import obtener_orden_fijo

    orden_fijo = obtener_orden_fijo(get_db())
    if 4 not in orden_fijo:
        pytest.skip("regla cosamaloapan_carrillo_amatitlan no está cargada todavía")
    miembros = [{"sid": s} for s in [100, 49, 86, 4, 75, 27]]   # orden mezclado a propósito
    assert aplicar_orden_fijo(miembros, orden_fijo) == [4, 27, 75, 86, 49, 100]


def test_regresion_orden_fijo_tuxtepec_f350_2(app_ctx):
    from db import get_db
    from logic.orden_fijo_paradas import obtener_orden_fijo

    orden_fijo = obtener_orden_fijo(get_db())
    if 2 not in orden_fijo:
        pytest.skip("regla tuxtepec_f350_2 no está cargada todavía")
    miembros = [{"sid": s} for s in [15, 54, 7, 55, 74, 31, 2]]  # orden mezclado a propósito
    assert aplicar_orden_fijo(miembros, orden_fijo) == [2, 31, 74, 55, 7, 54, 15]
