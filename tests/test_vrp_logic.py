from logic.vrp_logic import capacidad_efectiva_kg


def test_cap4_sigue_igual_3500_a_4000_kg():
    assert capacidad_efectiva_kg(3500) == 3900
    assert capacidad_efectiva_kg(3900) == 3900
    assert capacidad_efectiva_kg(4000) == 3900


def test_cap1500_da_tolerancia_hasta_1549_kg():
    assert capacidad_efectiva_kg(1500) == 1549


def test_cap1500_no_se_ensancha_a_kg_vecinos():
    # El rango es exactamente 1500 -- ni 1499 ni 1501 deben heredar la
    # tolerancia por error (a diferencia de CAP-4, que sí es un rango real).
    assert capacidad_efectiva_kg(1499) == 1499
    assert capacidad_efectiva_kg(1501) == 1501


def test_cap1500_no_afecta_capacidades_fuera_de_1500_kg():
    # 1300 kg (T 25 antes de corregir el dato) sigue siendo 100% nominal
    assert capacidad_efectiva_kg(1300) == 1300
    # Camiones medianos y KANGOO tampoco cambian
    assert capacidad_efectiva_kg(2500) == 2500
    assert capacidad_efectiva_kg(600) == 600
    # F350 (CAP-4) no se confunde con CAP-1.5
    assert capacidad_efectiva_kg(3900) == 3900


# ── solo_activos: un camion fuera de servicio no debe entrar al ruteo ──────
import pytest


@pytest.fixture(scope="module")
def app_ctx():
    try:
        from app import create_app
        app = create_app()
        ctx = app.app_context(); ctx.push()
        from db import get_db, get_table
        get_db().execute  # fuerza apertura de conexion
        get_table("vehiculos")
    except Exception as e:  # sin BD o sin la tabla
        pytest.skip(f"BD no disponible: {e}")
        return
    yield
    ctx.pop()


def _abrevs_inactivos():
    from sqlalchemy import select
    from db import get_db, get_table
    t = get_table("vehiculos")
    return {(v["abreviatura"] or "").strip()
            for v in get_db().execute(select(t)).mappings() if not v["activo"]}


def test_solo_activos_excluye_vehiculos_inactivos(app_ctx):
    from logic.vrp_logic import obtener_capacidades_vehiculos, obtener_volumenes_vehiculos
    inactivos = _abrevs_inactivos()
    assert inactivos, "se esperaba al menos un vehiculo inactivo (KANGOO) en la BD"
    # default sin filtrar: sigue incluyendo la flota completa (calibraciones/fidelidad)
    assert inactivos & set(obtener_capacidades_vehiculos())
    # con filtro: ninguno de los inactivos
    assert not inactivos & set(obtener_capacidades_vehiculos(solo_activos=True))
    assert not inactivos & set(obtener_volumenes_vehiculos(solo_activos=True))
