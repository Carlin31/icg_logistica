"""
tests/test_ancla_mayoristas.py

Ancla de visita para mayoristas: "este mayorista se visita inmediatamente
DESPUES de esta sucursal". Ver logic/ancla_mayoristas.py.
"""
import pytest

from logic.ancla_mayoristas import posicion_ancla
from logic.mayoristas_logic import _insertar_mayoristas_en_bloques


def _suc(num_tienda, lon):
    return {"tipo": "sucursal", "num_tienda": num_tienda, "latitud": 0.0, "longitud": lon}


def _nodo(m):
    return {"tipo": "mayorista", "id_cliente": m["id_cliente"],
            "latitud": m.get("latitud"), "longitud": m.get("longitud")}


# ── posicion_ancla ────────────────────────────────────────────

def test_posicion_ancla_devuelve_el_indice_justo_despues_de_la_sucursal():
    paradas = [_suc(29, 5.0), _suc(30, 6.0), _suc(28, 7.0)]
    bloque = [{"id_cliente": 501}]
    assert posicion_ancla(paradas, bloque, {501: 30}) == 2


def test_posicion_ancla_none_si_la_sucursal_ancla_no_esta_en_la_ruta():
    # El ancla puede apuntar a una sucursal que esta semana cayo en otra
    # ruta -- entonces no aplica y el bloque vuelve al orden geografico.
    paradas = [_suc(29, 5.0), _suc(28, 7.0)]
    assert posicion_ancla(paradas, [{"id_cliente": 501}], {501: 30}) is None


def test_posicion_ancla_none_si_algun_miembro_del_bloque_no_esta_anclado():
    # Contrato todo-o-nada, igual que aplicar_orden_fijo: un bloque a medio
    # anclar se ubica por geografia, no se parte.
    paradas = [_suc(30, 6.0)]
    bloque = [{"id_cliente": 501}, {"id_cliente": 999}]
    assert posicion_ancla(paradas, bloque, {501: 30}) is None


def test_posicion_ancla_none_si_el_bloque_apunta_a_dos_sucursales_distintas():
    paradas = [_suc(29, 5.0), _suc(30, 6.0)]
    bloque = [{"id_cliente": 501}, {"id_cliente": 502}]
    assert posicion_ancla(paradas, bloque, {501: 30, 502: 29}) is None


def test_posicion_ancla_none_sin_anclas():
    assert posicion_ancla([_suc(30, 6.0)], [{"id_cliente": 501}], {}) is None
    assert posicion_ancla([_suc(30, 6.0)], [{"id_cliente": 501}], None) is None


# ── integracion con _insertar_mayoristas_en_bloques ───────────

def test_ancla_gana_sobre_la_distancia_a_la_matriz():
    # Caso real 2026-09-04, LUNES de T 17_1 (zona 23). Distancias reales por
    # carretera (OSRM local), matriz 18.873/-96.949:
    #   Piedras Negras 92.5 | SUPER MAGUITO 102.8 | Tlalixcoyan 106.6 |
    #   Ignacio de la Llave 115.8
    # Por distancia creciente a la matriz -- y tambien por insercion mas
    # barata -- MAGUITO cae ENTRE Piedras Negras y Tlalixcoyan. Operacion
    # lo quiere DESPUES de Tlalixcoyan: el ancla debe ganar.
    depot = (0.0, 0.0)
    paradas = [_suc(29, 92.5), _suc(30, 106.6), _suc(28, 115.8)]
    bloque = [{"id_cliente": 501, "poblacion": "PASO DE BOCA",
               "latitud": 0.0, "longitud": 102.8}]

    def fake_distancias(origen, destinos):
        if origen == depot:
            return [92.5, 106.6, 115.8]
        return [102.8, 10.3, 16.3, 13.2]

    _insertar_mayoristas_en_bloques(
        paradas, bloque, _nodo, calcular_distancias_km=fake_distancias,
        depot=depot, anclas={501: 30},
    )
    assert [p.get("num_tienda") or p.get("id_cliente") for p in paradas] == [29, 30, 501, 28]


def test_sin_ancla_el_bloque_sigue_el_orden_geografico_de_siempre():
    # Mismo escenario sin ancla: se reproduce el comportamiento anterior
    # (MAGUITO entre Piedras Negras y Tlalixcoyan), para que quede claro que
    # el pin es lo unico que cambia el resultado.
    depot = (0.0, 0.0)
    paradas = [_suc(29, 92.5), _suc(30, 106.6), _suc(28, 115.8)]
    bloque = [{"id_cliente": 501, "poblacion": "PASO DE BOCA",
               "latitud": 0.0, "longitud": 102.8}]

    def fake_distancias(origen, destinos):
        if origen == depot:
            return [92.5, 106.6, 115.8]
        return [102.8, 10.3, 16.3, 13.2]

    _insertar_mayoristas_en_bloques(
        paradas, bloque, _nodo, calcular_distancias_km=fake_distancias, depot=depot,
    )
    assert [p.get("num_tienda") or p.get("id_cliente") for p in paradas] == [29, 501, 30, 28]


def test_ancla_no_consulta_distancias_para_el_bloque_anclado():
    # El ancla es una decision de operacion: no hace falta medir nada, y
    # ahorrarse la consulta a OSRM tambien evita que un fallo de OSRM
    # cambie la posicion de un bloque que ya estaba decidida.
    depot = (0.0, 0.0)
    paradas = [_suc(30, 6.0)]
    bloque = [{"id_cliente": 501, "poblacion": "PASO DE BOCA",
               "latitud": 0.0, "longitud": 3.0}]
    origenes = []

    def fake_distancias(origen, destinos):
        origenes.append(origen)
        return [10.0] * len(destinos)

    _insertar_mayoristas_en_bloques(
        paradas, bloque, _nodo, calcular_distancias_km=fake_distancias,
        depot=depot, anclas={501: 30},
    )
    assert [p.get("num_tienda") or p.get("id_cliente") for p in paradas] == [30, 501]
    # Solo el precomputo matriz -> paradas existentes; ninguna consulta del bloque.
    assert origenes == [depot]


def test_bloques_posteriores_ignoran_el_hueco_none_del_bloque_anclado():
    # El bloque anclado deja None en depot_distancias (no se midio). Un
    # bloque posterior debe seguir ubicandose bien contra las paradas que
    # SI tienen distancia -- _pos_por_distancia_depot ya ignora los None.
    depot = (0.0, 0.0)
    paradas = [_suc(29, 92.5), _suc(30, 106.6)]
    mayoristas = [
        {"id_cliente": 501, "poblacion": "PASO DE BOCA", "latitud": 0.0, "longitud": 102.8},
        {"id_cliente": 675, "poblacion": "COTAXTLA", "latitud": 0.0, "longitud": 67.1},
    ]

    def fake_distancias(origen, destinos):
        if origen == depot:
            return [92.5, 106.6]
        if origen == (0.0, 102.8):
            raise AssertionError("el bloque anclado no debe medirse")
        # COTAXTLA: [depot, Piedras Negras, Tlalixcoyan, MAGUITO anclado]
        return [67.1, 25.4, 39.5, 35.7]

    _insertar_mayoristas_en_bloques(
        paradas, mayoristas, _nodo, calcular_distancias_km=fake_distancias,
        depot=depot, anclas={501: 30},
    )
    # COTAXTLA (67.1 de la matriz) va antes de todo; MAGUITO justo tras Tlalixcoyan.
    assert [p.get("num_tienda") or p.get("id_cliente") for p in paradas] == [675, 29, 30, 501]


def test_obtener_anclas_degrada_a_vacio_sin_contexto_de_aplicacion():
    # Sin app context (o con la tabla aun sin crear) no debe reventar: el
    # motor sigue con el orden geografico de siempre.
    from logic.ancla_mayoristas import obtener_anclas_mayoristas
    assert obtener_anclas_mayoristas() == {}


def test_integrar_paradas_sin_anclas_no_revienta_sin_contexto():
    # _integrar_paradas (editor de Modificacion) carga las anclas solo; si
    # no puede, cae al comportamiento anterior en vez de fallar.
    from logic.mayoristas_logic import _integrar_paradas
    sucursales = [{"num_tienda": 30, "nombre_base": "Tlalixcoyan",
                   "latitud": 0.0, "longitud": 6.0}]
    mayoristas = [{"id_cliente": 501, "nombre": "SUPER MAGUITO",
                   "poblacion": "PASO DE BOCA", "latitud": 0.0, "longitud": 6.01,
                   "peso_kg": 155}]
    paradas = _integrar_paradas(sucursales, mayoristas)
    assert [p["tipo"] for p in paradas] == ["sucursal", "mayorista"]


# ── Integracion con BD real (se salta si no hay SQL Server) ───────────────
@pytest.fixture(scope="module")
def app_ctx():
    try:
        from app import create_app
        app = create_app()
        ctx = app.app_context(); ctx.push()
        from db import get_db, get_table
        get_db().execute  # fuerza apertura de conexion
        get_table("ancla_mayoristas")
    except Exception as e:  # sin BD o sin la tabla
        pytest.skip(f"BD no disponible: {e}")
        return
    yield app
    ctx.pop()


def test_obtener_anclas_lee_la_tabla_real(app_ctx):
    from db import get_table, transaccion, get_db
    from logic.ancla_mayoristas import obtener_anclas_mayoristas

    t = get_table("ancla_mayoristas")
    id_prueba = 999901  # fuera del rango de cualquier id_cliente real
    with transaccion() as conn:
        conn.execute(t.delete().where(t.c.id_cliente == id_prueba))
        conn.execute(t.insert(), [{"id_cliente": id_prueba, "num_tienda": 999902,
                                   "nota": "fila de prueba"}])
    try:
        assert obtener_anclas_mayoristas(get_db()).get(id_prueba) == 999902
    finally:
        with transaccion() as conn:
            conn.execute(t.delete().where(t.c.id_cliente == id_prueba))


def test_regresion_ancla_real_super_maguito_despues_de_tlalixcoyan(app_ctx):
    # El ancla que cargo scripts/cargar_ancla_mayoristas.py debe seguir ahi:
    # AA1907_SUPER MAGUITO (id_cliente 501) despues de Tlalixcoyan (30).
    from db import get_db
    from logic.ancla_mayoristas import obtener_anclas_mayoristas
    assert obtener_anclas_mayoristas(get_db()).get(501) == 30
