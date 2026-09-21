"""
tests/test_overrides_admin.py

Registro auditable de parches puntuales (overrides) -- ver
logic/overrides_admin.py.
"""
import time

import pytest


def test_registrar_override_rechaza_tipo_desconocido():
    from logic.overrides_admin import registrar_override
    with pytest.raises(ValueError):
        registrar_override("tipo_inventado", "grupo:27", None, "x", motivo="prueba")


def test_registrar_override_exige_motivo():
    from logic.overrides_admin import registrar_override
    with pytest.raises(ValueError):
        registrar_override("dia_preferido", "grupo:27", "JUEVES", "VIERNES", motivo="")
    with pytest.raises(ValueError):
        registrar_override("dia_preferido", "grupo:27", "JUEVES", "VIERNES", motivo="   ")


def test_historial_degrada_a_vacio_sin_contexto_de_aplicacion():
    from logic.overrides_admin import historial
    assert historial() == []


# ── Integracion con BD real (se salta si no hay SQL Server) ───────────────
@pytest.fixture(scope="module")
def app_ctx():
    try:
        from app import create_app
        app = create_app()
        ctx = app.app_context(); ctx.push()
        from db import get_db, get_table
        get_db().execute  # fuerza apertura de conexion
        get_table("overrides_auditoria")
    except Exception as e:  # sin BD o sin la tabla
        pytest.skip(f"BD no disponible: {e}")
        return
    yield app
    ctx.pop()


def test_registrar_y_leer_historial_real(app_ctx):
    from db import get_table, transaccion, get_db
    from logic.overrides_admin import registrar_override, historial

    clave_prueba = "grupo:999901"  # fuera del rango de cualquier grupo real
    t = get_table("overrides_auditoria")
    with transaccion() as conn:
        conn.execute(t.delete().where(t.c.clave == clave_prueba))
    try:
        registrar_override("dia_preferido", clave_prueba, "JUEVES", "VIERNES",
                            motivo="prueba automatizada", aplicado_por="pytest")
        filas = historial(clave=clave_prueba, db=get_db())
        assert len(filas) == 1
        assert filas[0]["tipo"] == "dia_preferido"
        assert filas[0]["valor_anterior"] == "JUEVES"
        assert filas[0]["valor_nuevo"] == "VIERNES"
        assert filas[0]["motivo"] == "prueba automatizada"
        assert filas[0]["aplicado_por"] == "pytest"
    finally:
        with transaccion() as conn:
            conn.execute(t.delete().where(t.c.clave == clave_prueba))


def test_historial_mas_reciente_primero(app_ctx):
    from db import get_table, transaccion, get_db
    from logic.overrides_admin import registrar_override, historial

    clave_prueba = "grupo:999902"
    t = get_table("overrides_auditoria")
    with transaccion() as conn:
        conn.execute(t.delete().where(t.c.clave == clave_prueba))
    try:
        registrar_override("afinidad", clave_prueba, None, "primero", motivo="prueba 1")
        time.sleep(0.01)
        registrar_override("afinidad", clave_prueba, "primero", "segundo", motivo="prueba 2")
        filas = historial(clave=clave_prueba, db=get_db())
        assert [f["valor_nuevo"] for f in filas] == ["segundo", "primero"]
    finally:
        with transaccion() as conn:
            conn.execute(t.delete().where(t.c.clave == clave_prueba))


def test_historial_filtra_por_tipo(app_ctx):
    from db import get_table, transaccion, get_db
    from logic.overrides_admin import registrar_override, historial

    clave_prueba = "grupo:999903"
    t = get_table("overrides_auditoria")
    with transaccion() as conn:
        conn.execute(t.delete().where(t.c.clave == clave_prueba))
    try:
        registrar_override("dia_preferido", clave_prueba, "A", "B", motivo="prueba")
        registrar_override("afinidad", clave_prueba, "X", "Y", motivo="prueba")
        filas = historial(clave=clave_prueba, tipo="afinidad", db=get_db())
        assert len(filas) == 1
        assert filas[0]["tipo"] == "afinidad"
    finally:
        with transaccion() as conn:
            conn.execute(t.delete().where(t.c.clave == clave_prueba))
