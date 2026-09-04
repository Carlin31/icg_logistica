"""
tests/test_grupo_fijo_mayoristas.py

Pin de ASIGNACION cliente -> grupo (logic/grupo_fijo_mayoristas.py).

Las pruebas de integracion pasan por `enganchar_mayoristas_por_zona()`, que es
el camino que REALMENTE asigna mayoristas en la corrida semanal del ConVRP.
Ese fue el punto ciego del intento anterior (2026-09-04): un dry-run que solo
ejercitaba `mayoristas_logic._construir_cache_zonas` dio 0 cambios y aun asi
el cambio disperso los mayoristas de Jalapa al correr la semana.
"""
import pytest

from logic.grupo_fijo_mayoristas import rid_por_grupo, ruta_fija

RUTA_JALAPA = ("T 17_1", "JUEVES")
RUTA_TUXTEPEC = ("T 23", "JUEVES")


# ── helpers puros ─────────────────────────────────────────────

def test_ruta_fija_devuelve_la_ruta_del_grupo_fijado():
    assert ruta_fija({"id_cliente": 555}, {555: 7}, {7: "r_jalapa"}) == "r_jalapa"


def test_ruta_fija_none_para_un_cliente_sin_pin():
    # El grueso de los mayoristas no tiene pin: deben seguir el camino normal.
    assert ruta_fija({"id_cliente": 999}, {555: 7}, {7: "r_jalapa"}) is None


def test_ruta_fija_none_si_el_grupo_fijado_no_viaja_esta_semana():
    # Contrato todo-o-nada: sin ruta para ese grupo el pin calla, no inventa
    # un destino.
    assert ruta_fija({"id_cliente": 555}, {555: 7}, {25: "r_tuxtepec"}) is None


def test_ruta_fija_none_sin_pines():
    assert ruta_fija({"id_cliente": 555}, {}, {7: "r"}) is None
    assert ruta_fija({"id_cliente": 555}, {555: 7}, {}) is None


def test_rid_por_grupo_es_determinista_si_un_grupo_esta_en_dos_rutas():
    # Gana la primera por orden de ruta_id, no el orden en que SQL devolvio
    # las filas.
    rutas = {"r_b": [{"num_tienda": 35}], "r_a": [{"num_tienda": 97}]}
    assert rid_por_grupo(rutas, {35: 7, 97: 7}) == {7: "r_a"}


def test_obtener_grupo_fijo_degrada_a_vacio_sin_contexto():
    from logic.grupo_fijo_mayoristas import obtener_grupo_fijo
    assert obtener_grupo_fijo() == {}


# ── Integración con el camino real del ConVRP ─────────────────
@pytest.fixture(scope="module")
def app_ctx():
    try:
        from app import create_app
        app = create_app()
        ctx = app.app_context(); ctx.push()
        from db import get_db, get_table
        get_db().execute  # fuerza apertura de conexion
        get_table("grupo_fijo_mayoristas")
    except Exception as e:  # sin BD o sin la tabla
        pytest.skip(f"BD no disponible: {e}")
        return
    yield app
    ctx.pop()


def _escenario(con_grupo_7=True):
    groups = {RUTA_TUXTEPEC: [{"sid": 46, "grupo": 25}, {"sid": 57, "grupo": 25}]}
    if con_grupo_7:
        groups[RUTA_JALAPA] = [{"sid": 35, "grupo": 7}, {"sid": 97, "grupo": 7}]
    mayoristas = [
        {"id_cliente": 555, "nombre": "FARMA PRONTO", "poblacion": "JALAPA",
         "latitud": 18.071048, "longitud": -96.537577, "peso_kg": 54},
        # Cliente ajeno, pegado a Tuxtepec: no debe moverse por culpa del pin.
        {"id_cliente": 999901, "nombre": "AJENO TUXTEPEC", "poblacion": "TUXTEPEC",
         "latitud": 18.0856, "longitud": -96.1300, "peso_kg": 10},
    ]
    coords = {RUTA_JALAPA: (18.0725, -96.5370), RUTA_TUXTEPEC: (18.0776, -96.1270)}
    return groups, mayoristas, {k: v for k, v in coords.items() if k in groups}


def test_convrp_el_pin_manda_para_el_cliente_fijado(app_ctx):
    from logic.convrp_integracion import enganchar_mayoristas_por_zona
    groups, mayoristas, coords = _escenario()
    por_ruta, detalle = enganchar_mayoristas_por_zona(groups, mayoristas, coords)

    fila = next(d for d in detalle if d["id_cliente"] == 555)
    assert fila["destino"] == RUTA_JALAPA
    assert fila["via_destino"] == "GRUPO_FIJO"
    assert fila["grupo_destino"] == 7
    assert 555 in [m["id_cliente"] for m in por_ruta[RUTA_JALAPA]]


def test_convrp_el_pin_no_toca_a_los_demas_clientes(app_ctx):
    # La clave de este enfoque frente a reactivar la capa de zona: el pin no
    # altera `hist` ni los centroides, asi que no puede cambiarle el destino a
    # nadie mas.
    from logic.convrp_integracion import enganchar_mayoristas_por_zona
    groups, mayoristas, coords = _escenario()
    _, detalle = enganchar_mayoristas_por_zona(groups, mayoristas, coords)

    ajeno = next(d for d in detalle if d["id_cliente"] == 999901)
    assert ajeno["destino"] == RUTA_TUXTEPEC
    assert ajeno["via_destino"] != "GRUPO_FIJO"


def test_convrp_el_pin_calla_si_su_grupo_no_viaja_esta_semana(app_ctx):
    # Sin ruta para el grupo 7, el cliente 555 vuelve al camino normal en vez
    # de quedarse sin destino o forzar una ruta que no existe.
    from logic.convrp_integracion import enganchar_mayoristas_por_zona
    groups, mayoristas, coords = _escenario(con_grupo_7=False)
    _, detalle = enganchar_mayoristas_por_zona(groups, mayoristas, coords)

    fila = next(d for d in detalle if d["id_cliente"] == 555)
    assert fila["via_destino"] != "GRUPO_FIJO"


def test_pin_real_de_farma_pronto_sigue_cargado(app_ctx):
    # Regresion del caso del usuario: cliente 555 -> grupo 7 (Jalapa de Diaz).
    from db import get_db
    from logic.grupo_fijo_mayoristas import obtener_grupo_fijo
    assert obtener_grupo_fijo(get_db()).get(555) == 7
