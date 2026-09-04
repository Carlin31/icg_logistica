"""
tests/test_pdf_snapshot_coherente.py

`generar_pdf()` prefiere la Modificacion guardada sobre la asignacion, y lo
hacia sin verificar nada. Si la pestana de Modificacion se cargaba ANTES de
que el motor terminara y se guardaba despues, el snapshot conservaba el
reparto pre-motor y el PDF lo imprimia en silencio: el planeador veia la
pantalla correcta y el PDF equivocado al mismo tiempo (bug real, tres veces
entre el 2026-08-28 y el 2026-09-04).

Ojo con la trampa en la que cai al disenarlo: NO sirve comparar
`guardado_en < generado_en`. En el caso medido la Modificacion se guardo a las
12:00:17, DESPUES de que el motor corriera a las 11:58:02 -- lo viejo era la
CARGA de la pestana, no el guardado. Hay que comparar el DATO (a que ruta
quedo cada mayorista), no el reloj.
"""
import pytest

from logic.pdf_logic import _snapshot_incoherente

OID_PRUEBA = "ffffffffffffffffffffff01"   # 24 hex: no choca con logisticas reales
RUTA_MOTOR = "vrpaf_t_17_1_jueves"
RUTA_VIEJA = "vrpaf_t_23_jueves"
CLIENTE = 999555


@pytest.fixture(scope="module")
def app_ctx():
    try:
        from app import create_app
        app = create_app()
        ctx = app.app_context(); ctx.push()
        from db import get_db, get_table
        get_db().execute  # fuerza apertura de conexion
        get_table("convrp_mayoristas")
        get_table("asignaciones_mayoristas_overrides")
    except Exception as e:
        pytest.skip(f"BD no disponible: {e}")
        return
    yield app
    ctx.pop()


@pytest.fixture
def corrida_del_motor(app_ctx):
    """Deja una corrida del motor que pone a CLIENTE en RUTA_MOTOR."""
    from db import get_table, transaccion
    t = get_table("convrp_mayoristas")
    t_ov = get_table("asignaciones_mayoristas_overrides")
    with transaccion() as conn:
        conn.execute(t.delete().where(t.c.logistica_id == OID_PRUEBA))
        conn.execute(t_ov.delete().where(t_ov.c.logistica_id == OID_PRUEBA))
        conn.execute(t.insert(), [{
            "logistica_id": OID_PRUEBA, "generado_en": "2026-09-04T11:58:02.641833",
            "unidad": "T 17_1", "dia": "JUEVES", "orden": 5, "id_cliente": CLIENTE,
            "nombre": "FARMA PRONTO DE PRUEBA", "peso_kg": 54.03,
            "via_zona": "FALLBACK", "via_destino": "GRUPO_FIJO"}])
    yield
    with transaccion() as conn:
        conn.execute(t.delete().where(t.c.logistica_id == OID_PRUEBA))
        conn.execute(t_ov.delete().where(t_ov.c.logistica_id == OID_PRUEBA))


def _rutas(ruta_key):
    return [{"id": ruta_key, "mayoristas": [
        {"id_cliente": CLIENTE, "nombre": "FARMA PRONTO DE PRUEBA", "documento": "BB9999"}]}]


def test_detecta_el_mayorista_que_el_snapshot_dejo_en_otra_ruta(corrida_del_motor):
    from db import get_db
    malos = _snapshot_incoherente(get_db(), OID_PRUEBA, _rutas(RUTA_VIEJA))
    assert len(malos) == 1
    assert malos[0]["id_cliente"] == CLIENTE
    assert malos[0]["en_snapshot"] == RUTA_VIEJA
    assert malos[0]["segun_motor"] == RUTA_MOTOR


def test_snapshot_que_concuerda_con_el_motor_no_bloquea(corrida_del_motor):
    from db import get_db
    assert _snapshot_incoherente(get_db(), OID_PRUEBA, _rutas(RUTA_MOTOR)) == []


def test_un_movimiento_manual_registrado_no_cuenta_como_incoherencia(corrida_del_motor):
    # Mover un mayorista a mano es legitimo y queda en
    # asignaciones_mayoristas_overrides: no debe bloquear el PDF.
    from db import get_db, get_table, transaccion
    t_ov = get_table("asignaciones_mayoristas_overrides")
    with transaccion() as conn:
        conn.execute(t_ov.insert(), [{
            "asignacion_id": "x", "logistica_id": OID_PRUEBA, "ruta_key": RUTA_VIEJA,
            "tipo_override": "incluido", "clave": str(CLIENTE)}])
    assert _snapshot_incoherente(get_db(), OID_PRUEBA, _rutas(RUTA_VIEJA)) == []


def test_sin_corrida_del_motor_no_bloquea(app_ctx):
    # Nada contra que comparar: el PDF debe salir igual que antes.
    from db import get_db
    assert _snapshot_incoherente(get_db(), "ffffffffffffffffffffff99",
                                 _rutas(RUTA_VIEJA)) == []


def test_no_bloquea_por_mayoristas_que_el_motor_no_conoce(corrida_del_motor):
    # Un mayorista agregado a mano que el motor nunca vio no tiene con que
    # compararse; no es evidencia de snapshot viejo.
    from db import get_db
    rutas = [{"id": RUTA_VIEJA, "mayoristas": [{"id_cliente": 999999, "nombre": "NUEVO"}]}]
    assert _snapshot_incoherente(get_db(), OID_PRUEBA, rutas) == []
