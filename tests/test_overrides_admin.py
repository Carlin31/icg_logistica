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


def _limpiar_auditoria(clave):
    from db import get_table, transaccion
    ta = get_table("overrides_auditoria")
    with transaccion() as conn:
        conn.execute(ta.delete().where(ta.c.clave == clave))


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


# ── grupo sintetico para fijar_dia_preferido / fijar_afinidad / fijar_unidades_excluidas ──

GRUPO_TEST = 999901
VERSION_TEST = 999999


@pytest.fixture
def grupo_sintetico(app_ctx):
    from db import get_table, transaccion
    tg = get_table("plantilla_grupo")
    tgd = get_table("plantilla_grupo_dia")
    with transaccion() as conn:
        conn.execute(tg.delete().where(tg.c.grupo == GRUPO_TEST))
        conn.execute(tgd.delete().where(tgd.c.grupo == GRUPO_TEST))
        conn.execute(tg.insert().values(
            version=VERSION_TEST, grupo=GRUPO_TEST, rigidez="FLEXIBLE", dia="LUNES",
            tam=1, cohesion=None, unidad_ref=None, que_hace_vrp=None,
            unidades_afines=None, unidades_excluidas=None,
            vigente_desde="TEST", vigente=True,
        ))
        conn.execute(tgd.insert(), [
            dict(version=VERSION_TEST, grupo=GRUPO_TEST, dia="LUNES",
                 es_canonico=True, orden=1, vigente_desde="TEST", vigente=True),
            dict(version=VERSION_TEST, grupo=GRUPO_TEST, dia="MARTES",
                 es_canonico=False, orden=2, vigente_desde="TEST", vigente=True),
        ])
    yield GRUPO_TEST
    with transaccion() as conn:
        conn.execute(tg.delete().where(tg.c.grupo == GRUPO_TEST))
        conn.execute(tgd.delete().where(tgd.c.grupo == GRUPO_TEST))
    _limpiar_auditoria(f"grupo:{GRUPO_TEST}")


def test_fijar_dia_preferido_actualiza_es_canonico_y_registra_auditoria(grupo_sintetico):
    from db import get_table, get_db
    from sqlalchemy import select
    from logic.overrides_admin import fijar_dia_preferido, historial

    resultado = fijar_dia_preferido(GRUPO_TEST, "MARTES", motivo="prueba TDD")
    assert resultado == {"grupo": GRUPO_TEST, "antes": "LUNES", "despues": "MARTES"}

    tgd = get_table("plantilla_grupo_dia")
    filas = get_db().execute(
        select(tgd.c.dia, tgd.c.es_canonico).where(tgd.c.grupo == GRUPO_TEST, tgd.c.vigente == True)
    ).mappings().all()
    canonico = {f["dia"] for f in filas if f["es_canonico"]}
    assert canonico == {"MARTES"}

    filas_hist = historial(clave=f"grupo:{GRUPO_TEST}")
    assert len(filas_hist) == 1
    assert filas_hist[0]["tipo"] == "dia_preferido"
    assert filas_hist[0]["valor_anterior"] == "LUNES"
    assert filas_hist[0]["valor_nuevo"] == "MARTES"


def test_fijar_dia_preferido_rechaza_dia_no_admisible(grupo_sintetico):
    from logic.overrides_admin import fijar_dia_preferido
    with pytest.raises(ValueError, match="admisible"):
        fijar_dia_preferido(GRUPO_TEST, "VIERNES", motivo="prueba TDD")


def test_fijar_dia_preferido_dry_run_no_escribe(grupo_sintetico):
    from db import get_table, get_db
    from sqlalchemy import select
    from logic.overrides_admin import fijar_dia_preferido, historial

    resultado = fijar_dia_preferido(GRUPO_TEST, "MARTES", motivo="prueba TDD", dry_run=True)
    assert resultado["antes"] == "LUNES" and resultado["despues"] == "MARTES"

    tgd = get_table("plantilla_grupo_dia")
    canonico = get_db().execute(
        select(tgd.c.dia).where(tgd.c.grupo == GRUPO_TEST, tgd.c.vigente == True, tgd.c.es_canonico == True)
    ).scalar()
    assert canonico == "LUNES"
    assert historial(clave=f"grupo:{GRUPO_TEST}") == []


def test_fijar_afinidad_actualiza_y_registra_auditoria(grupo_sintetico):
    from db import get_table, get_db
    from sqlalchemy import select
    from logic.overrides_admin import fijar_afinidad, historial

    valor = "T 25:5 | T 23:4"
    resultado = fijar_afinidad(GRUPO_TEST, valor, motivo="prueba TDD")
    assert resultado == {"grupo": GRUPO_TEST, "antes": None, "despues": valor}

    tg = get_table("plantilla_grupo")
    actual = get_db().execute(
        select(tg.c.unidades_afines).where(tg.c.grupo == GRUPO_TEST, tg.c.vigente == True)
    ).scalar()
    assert actual == valor
    assert historial(clave=f"grupo:{GRUPO_TEST}", tipo="afinidad")[0]["valor_nuevo"] == valor


def test_fijar_afinidad_rechaza_grupo_inexistente(app_ctx):
    from logic.overrides_admin import fijar_afinidad
    with pytest.raises(ValueError, match="no existe"):
        fijar_afinidad(999888, "T 25:5", motivo="prueba TDD")


def test_fijar_unidades_excluidas_acepta_lista_y_las_junta_con_pipe(grupo_sintetico):
    from db import get_table, get_db
    from sqlalchemy import select
    from logic.overrides_admin import fijar_unidades_excluidas

    fijar_unidades_excluidas(GRUPO_TEST, ["F 350_1", "F 350_2"], motivo="prueba TDD")
    tg = get_table("plantilla_grupo")
    actual = get_db().execute(
        select(tg.c.unidades_excluidas).where(tg.c.grupo == GRUPO_TEST, tg.c.vigente == True)
    ).scalar()
    assert actual == "F 350_1|F 350_2"


# ── crear_ancla_mayorista / fijar_grupo_mayorista ──────────────────────────

CLIENTE_TEST = 999901


@pytest.fixture
def limpiar_pines_mayorista(app_ctx):
    from db import get_table, transaccion
    tam = get_table("ancla_mayoristas")
    tgm = get_table("grupo_fijo_mayoristas")
    with transaccion() as conn:
        conn.execute(tam.delete().where(tam.c.id_cliente == CLIENTE_TEST))
        conn.execute(tgm.delete().where(tgm.c.id_cliente == CLIENTE_TEST))
    yield CLIENTE_TEST
    with transaccion() as conn:
        conn.execute(tam.delete().where(tam.c.id_cliente == CLIENTE_TEST))
        conn.execute(tgm.delete().where(tgm.c.id_cliente == CLIENTE_TEST))
    _limpiar_auditoria(f"cliente:{CLIENTE_TEST}")


def test_crear_ancla_mayorista_inserta_y_registra_auditoria(limpiar_pines_mayorista):
    from db import get_table, get_db
    from sqlalchemy import select
    from logic.overrides_admin import crear_ancla_mayorista, historial

    resultado = crear_ancla_mayorista(CLIENTE_TEST, 30, motivo="prueba TDD")
    assert resultado == {"id_cliente": CLIENTE_TEST, "antes": None, "despues": 30}

    t = get_table("ancla_mayoristas")
    actual = get_db().execute(
        select(t.c.num_tienda).where(t.c.id_cliente == CLIENTE_TEST)
    ).scalar()
    assert actual == 30
    assert historial(clave=f"cliente:{CLIENTE_TEST}", tipo="ancla_mayorista")[0]["valor_nuevo"] == "30"


def test_crear_ancla_mayorista_actualiza_si_ya_existe(limpiar_pines_mayorista):
    from db import get_table, get_db
    from sqlalchemy import select
    from logic.overrides_admin import crear_ancla_mayorista

    crear_ancla_mayorista(CLIENTE_TEST, 30, motivo="prueba TDD 1")
    resultado = crear_ancla_mayorista(CLIENTE_TEST, 45, motivo="prueba TDD 2")
    assert resultado == {"id_cliente": CLIENTE_TEST, "antes": 30, "despues": 45}

    t = get_table("ancla_mayoristas")
    actual = get_db().execute(
        select(t.c.num_tienda).where(t.c.id_cliente == CLIENTE_TEST)
    ).scalar()
    assert actual == 45


def test_fijar_grupo_mayorista_inserta_y_registra_auditoria(limpiar_pines_mayorista):
    from db import get_table, get_db
    from sqlalchemy import select
    from logic.overrides_admin import fijar_grupo_mayorista, historial

    resultado = fijar_grupo_mayorista(CLIENTE_TEST, 27, motivo="prueba TDD")
    assert resultado == {"id_cliente": CLIENTE_TEST, "antes": None, "despues": 27}

    t = get_table("grupo_fijo_mayoristas")
    actual = get_db().execute(
        select(t.c.grupo).where(t.c.id_cliente == CLIENTE_TEST)
    ).scalar()
    assert actual == 27
    assert historial(clave=f"cliente:{CLIENTE_TEST}", tipo="grupo_fijo_mayorista")[0]["valor_nuevo"] == "27"


# ── cargar_orden_fijo_regla ─────────────────────────────────────────────

REGLA_TEST = "prueba_admin_overrides"
REGLA_TEST_2 = "prueba_admin_overrides_2"


@pytest.fixture
def limpiar_orden_fijo(app_ctx):
    from db import get_table, transaccion
    t = get_table("orden_fijo_paradas")
    with transaccion() as conn:
        conn.execute(t.delete().where(t.c.nombre_regla.in_([REGLA_TEST, REGLA_TEST_2])))
    yield
    with transaccion() as conn:
        conn.execute(t.delete().where(t.c.nombre_regla.in_([REGLA_TEST, REGLA_TEST_2])))
    _limpiar_auditoria(f"regla:{REGLA_TEST}")
    _limpiar_auditoria(f"regla:{REGLA_TEST_2}")


def test_cargar_orden_fijo_regla_reemplaza_completo_y_registra_auditoria(limpiar_orden_fijo):
    from db import get_table, get_db
    from sqlalchemy import select
    from logic.overrides_admin import cargar_orden_fijo_regla, historial

    cargar_orden_fijo_regla(REGLA_TEST, [(999910, 1), (999911, 2)], motivo="prueba TDD 1")
    resultado = cargar_orden_fijo_regla(REGLA_TEST, [(999910, 1), (999912, 2)], motivo="prueba TDD 2")
    assert resultado["antes"] == [(999910, 1), (999911, 2)]
    assert resultado["despues"] == [(999910, 1), (999912, 2)]

    t = get_table("orden_fijo_paradas")
    filas = get_db().execute(
        select(t.c.num_tienda).where(t.c.nombre_regla == REGLA_TEST)
    ).scalars().all()
    assert sorted(filas) == [999910, 999912]  # reemplazo completo, no acumula

    assert len(historial(clave=f"regla:{REGLA_TEST}", tipo="orden_fijo")) == 2


def test_cargar_orden_fijo_regla_rechaza_colision_dentro_del_lote(limpiar_orden_fijo):
    from logic.overrides_admin import cargar_orden_fijo_regla
    with pytest.raises(ValueError, match="dos veces"):
        cargar_orden_fijo_regla(REGLA_TEST, [(999910, 1), (999910, 2)], motivo="prueba TDD")


def test_cargar_orden_fijo_regla_rechaza_colision_contra_otra_regla_existente(limpiar_orden_fijo):
    # Regresion directa del bug real encontrado 2026-09-21: tuxtepec_f350_2
    # quedo chocando en silencio contra zona_5/zona_25 porque el loader viejo
    # (scripts/cargar_orden_fijo.py) solo validaba colision DENTRO del CSV
    # que se estaba cargando, nunca contra lo que ya habia en la tabla.
    from logic.overrides_admin import cargar_orden_fijo_regla

    cargar_orden_fijo_regla(REGLA_TEST, [(999910, 1), (999911, 2)], motivo="prueba TDD regla vieja")
    with pytest.raises(ValueError, match="colision"):
        cargar_orden_fijo_regla(REGLA_TEST_2, [(999910, 1)], motivo="prueba TDD regla nueva")


def test_cargar_orden_fijo_regla_dry_run_no_escribe(limpiar_orden_fijo):
    from db import get_table, get_db
    from sqlalchemy import select
    from logic.overrides_admin import cargar_orden_fijo_regla, historial

    cargar_orden_fijo_regla(REGLA_TEST, [(999910, 1)], motivo="prueba TDD", dry_run=True)
    t = get_table("orden_fijo_paradas")
    assert get_db().execute(
        select(t.c.num_tienda).where(t.c.nombre_regla == REGLA_TEST)
    ).first() is None
    assert historial(clave=f"regla:{REGLA_TEST}") == []
