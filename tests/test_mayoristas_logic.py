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


# ── zigzag de mayoristas entre poblaciones (bug real 2026-08-31, reportado ──
# por el usuario en la ruta de jueves de T 17_1: la secuencia alternaba
# Jalapa de Diaz / Ojitlan / Jalapa de Diaz / Ojitlan en vez de agrupar cada
# poblacion junta). Causa raiz: tanto _ordenar_mayoristas_en_ruta como
# _integrar_paradas ordenaban/procesaban los mayoristas por distancia cruda
# al centroide (o ranking historico) SIN agrupar antes por poblacion, asi
# que dos mayoristas de pueblos distintos con distancias intercaladas se
# insertaban uno a la vez en ese orden intercalado. Estas pruebas usan
# coordenadas IDENTICAS para los 4 mayoristas a proposito: aisla el efecto
# del ORDEN de procesamiento (que es lo que se corrige) del efecto de la
# geometria real (que ya autocorrige casi siempre y esconderia el bug).
def test_ordenar_mayoristas_en_ruta_agrupa_por_poblacion_sin_intercalar():
    from logic.mayoristas_logic import _ordenar_mayoristas_en_ruta
    sucursales = [{"latitud": 0.0, "longitud": 0.0}]
    # Mismas coordenadas para los 4 -> sin agrupar por poblacion, el orden
    # ingenuo por distancia (empatada) desempata por id_cliente: 1,2,3,4 =
    # JALAPA, OJITLAN, JALAPA, OJITLAN -- intercalado.
    mayoristas = [
        {"id_cliente": 1, "poblacion": "JALAPA DE DIAZ", "latitud": 1.0, "longitud": 1.0},
        {"id_cliente": 2, "poblacion": "OJITLAN",        "latitud": 1.0, "longitud": 1.0},
        {"id_cliente": 3, "poblacion": "JALAPA DE DIAZ", "latitud": 1.0, "longitud": 1.0},
        {"id_cliente": 4, "poblacion": "OJITLAN",        "latitud": 1.0, "longitud": 1.0},
    ]
    ordenadas = _ordenar_mayoristas_en_ruta(mayoristas, "vrpaf_t17_1_jueves", {}, sucursales)
    poblaciones = [m["poblacion"] for m in ordenadas]
    assert poblaciones == ["JALAPA DE DIAZ", "JALAPA DE DIAZ", "OJITLAN", "OJITLAN"], (
        f"mayoristas de la misma poblacion quedaron intercalados: {poblaciones}")


def test_integrar_paradas_agrupa_por_poblacion_sin_intercalar():
    from logic.mayoristas_logic import _integrar_paradas
    sucursales = [
        {"num_tienda": 1, "nombre_base": "Ancla", "latitud": 0.0, "longitud": 0.0, "orden": 1},
    ]
    id_a_poblacion = {1: "JALAPA DE DIAZ", 2: "OJITLAN", 3: "JALAPA DE DIAZ", 4: "OJITLAN"}
    mayoristas = [
        {"id_cliente": i, "poblacion": pob, "latitud": 1.0, "longitud": 1.0, "peso_kg": 5.0}
        for i, pob in id_a_poblacion.items()
    ]
    paradas = _integrar_paradas(sucursales, mayoristas)
    poblaciones = [id_a_poblacion[p["id_cliente"]] for p in paradas if p["tipo"] == "mayorista"]
    assert poblaciones in (
        ["JALAPA DE DIAZ", "JALAPA DE DIAZ", "OJITLAN", "OJITLAN"],
        ["OJITLAN", "OJITLAN", "JALAPA DE DIAZ", "JALAPA DE DIAZ"],
    ), f"mayoristas de la misma poblacion quedaron intercalados: {poblaciones}"


def test_ordenar_mayoristas_en_ruta_sin_poblacion_no_se_agrupan_entre_si():
    # Los mayoristas sin poblacion (cadena vacia) no comparten nada entre
    # si -- no deben tratarse como un solo grupo gigante que ignore su
    # propio orden por distancia/historico.
    from logic.mayoristas_logic import _ordenar_mayoristas_en_ruta
    sucursales = [{"latitud": 0.0, "longitud": 0.0}]
    mayoristas = [
        {"id_cliente": 1, "poblacion": "", "latitud": 0.0, "longitud": 0.30},
        {"id_cliente": 2, "poblacion": "", "latitud": 0.0, "longitud": 0.10},
    ]
    ordenadas = _ordenar_mayoristas_en_ruta(mayoristas, "vrpaf_x_lunes", {}, sucursales)
    assert [m["id_cliente"] for m in ordenadas] == [2, 1]


def test_integrar_paradas_no_parte_bloques_con_datos_reales_jalapa_ojitlan():
    # Reproduccion fiel del bug real 2026-08-31 (ruta de jueves de T 17_1,
    # "Jalapa de Diaz 2"/"San Felipe Jalapa de Diaz") con coordenadas REALES
    # tomadas de la BD -- no sinteticas. Con solo el agrupar-antes-de-ordenar
    # (sin este fix), el orden de PROCESAMIENTO ya quedaba agrupado por
    # poblacion pero la insercion uno-a-la-vez seguia partiendo los bloques:
    # SAN LUCAS OJITLAN 3+1, JALAPA DE DIAZ 2+1, JALAPA 4+1. Este test
    # verifica que _insertar_mayoristas_en_bloques ya no los parte.
    from logic.mayoristas_logic import _integrar_paradas
    sucursales = [
        {"num_tienda": 97, "nombre_base": "Jalapa de Diaz 2",
         "latitud": 18.07389925, "longitud": -96.53703431, "orden": 1},
        {"num_tienda": 35, "nombre_base": "San Felipe Jalapa de Diaz",
         "latitud": 18.07117598, "longitud": -96.53689484, "orden": 2},
    ]
    # (id_cliente, poblacion, latitud, longitud) -- orden persistido real.
    datos = [
        (437, "JALAPA",            18.071751, -96.537349),
        (530, "JALAPA",            18.071493, -96.537159),
        (598, "JALAPA",            18.071400, -96.537100),
        (558, "JALAPA",            18.071203, -96.537053),
        (555, "JALAPA",            18.071048, -96.537577),
        (609, "JALAPA DE DIAZ",    18.071695, -96.537300),
        (318, "JALAPA DE DIAZ",    18.071295, -96.536932),
        (594, "JALAPA DE DIAZ",    18.069931, -96.535417),
        (433, "SAN LUCAS OJITLAN", 18.071300, -96.536900),
        (440, "SAN LUCAS OJITLAN", 18.080451, -96.527644),
        (528, "SAN LUCAS OJITLAN", 18.059700, -96.395700),
        (654, "SAN LUCAS OJITLAN", 18.058777, -96.394301),
    ]
    id_a_poblacion = {id_cl: pob for id_cl, pob, _, _ in datos}
    mayoristas = [
        {"id_cliente": id_cl, "poblacion": pob, "latitud": lat, "longitud": lon, "peso_kg": 10.0}
        for id_cl, pob, lat, lon in datos
    ]

    paradas = _integrar_paradas(sucursales, mayoristas)
    poblaciones = [id_a_poblacion[p["id_cliente"]] for p in paradas if p["tipo"] == "mayorista"]

    # Cada poblacion debe aparecer como un solo tramo contiguo (no partida
    # en dos o mas grupos separados por otra poblacion).
    tramos = []
    for pob in poblaciones:
        if not tramos or tramos[-1] != pob:
            tramos.append(pob)
    assert len(tramos) == len(set(poblaciones)), (
        f"una poblacion quedo partida en mas de un tramo: {poblaciones}")


# ── orden por carretera real (OSRM) en vez de linea recta ──────────────────
# Bug real 2026-08-31 (misma ruta de jueves de T 17_1): con datos reales,
# San Lucas Ojitlan mide MAS lejos que Jalapa de Diaz por linea recta
# (haversine: 103.2km vs 99.2km desde la matriz), pero por carretera real es
# al reves (OSRM: 198.7km vs 209.9km) -- Ojitlan si esta mas cerca por
# carretera. _insertar_pos_proxima/_insertar_mayoristas_en_bloques aceptan
# ahora distancias reales inyectadas (via `distancias_km`/
# `calcular_distancias_km`) para que el bloque se ubique donde realmente
# corresponde por carretera, en vez de la linea recta que producia el orden
# incorrecto.
def test_insertar_pos_proxima_usa_distancias_km_si_se_proveen():
    from logic.mayoristas_logic import _insertar_pos_proxima
    route = [
        {"latitud": 0.0, "longitud": 0.0},
        {"latitud": 0.0, "longitud": 10.0},
    ]
    nuevo = {"latitud": 0.0, "longitud": 1.0}

    # Por linea recta, la parada 0 (a 1 grado) gana sobre la 1 (a 9 grados).
    assert _insertar_pos_proxima(route, nuevo) == 1

    # Con distancias reales invertidas (simula carretera), debe ganar la 1.
    assert _insertar_pos_proxima(route, nuevo, distancias_km=[500.0, 5.0]) == 2


def test_insertar_pos_proxima_distancias_km_none_ignora_esa_parada():
    from logic.mayoristas_logic import _insertar_pos_proxima
    route = [
        {"latitud": 0.0, "longitud": 0.0},
        {"latitud": 0.0, "longitud": 10.0},
    ]
    nuevo = {"latitud": 0.0, "longitud": 1.0}
    # parada 0 sin distancia real disponible (None) -> se ignora, gana la 1.
    assert _insertar_pos_proxima(route, nuevo, distancias_km=[None, 5.0]) == 2


def test_insertar_pos_proxima_depot_permite_insertar_antes_de_la_primera_parada():
    # Bug real 2026-09-01 (version 1, no bastaba): comparar la distancia
    # del bloque a la matriz contra su distancia DIRECTA a la parada mas
    # proxima nunca puede ganar -- la distancia a la matriz es de toda la
    # ruta (~200km) y la directa es local (~20km), asi que el vecino mas
    # cercano siempre gana. Ver _pos_por_distancia_depot: compara
    # distancia-a-matriz contra distancia-a-matriz, esa si funciona.
    from logic.mayoristas_logic import _insertar_pos_proxima, _pos_por_distancia_depot
    route = [
        {"latitud": 0.0, "longitud": 5.0},
        {"latitud": 0.0, "longitud": 10.0},
    ]
    nuevo = {"latitud": 0.0, "longitud": 1.0}
    assert _insertar_pos_proxima(route, nuevo, distancias_km=[4.0, 9.0]) == 1

    # Con distancia real a la matriz (no a la parada): la parada existente
    # mide 209.3km de la matriz, el bloque nuevo solo 198.4km -> debe ir
    # ANTES de esa parada (idx 0), aunque este a solo 4km de distancia local.
    assert _pos_por_distancia_depot([209.3], 198.4) == 0
    assert _pos_por_distancia_depot([150.0, 209.3], 198.4) == 1
    assert _pos_por_distancia_depot(None, 198.4) is None
    assert _pos_por_distancia_depot([209.3], None) is None


def test_pos_por_distancia_depot_detecta_ruta_decreciente():
    # Bug real 2026-09-02: T20 martes visita Tetela (65.9km de la matriz),
    # Vicente Camalote (59.3km) y Acatlan de Perez Figueroa (51.9km) en
    # orden DECRECIENTE de distancia a la matriz (no creciente, que era el
    # unico sentido que _pos_por_distancia_depot sabia comparar). El
    # mayorista AA1881_CASA PENA esta a 0.17km de Acatlan (51.75km de la
    # matriz, incluso mas cerca que la propia Acatlan) y debia insertarse
    # AL FINAL (idx 3, junto a Acatlan) -- pero como ninguna parada tenia
    # distancia-a-matriz <= 51.75 (todas eran mayores), la version anterior
    # devolvia pos=0: el mayorista quedaba ANTES de Tetela, en el extremo
    # opuesto de la ruta real.
    from logic.mayoristas_logic import _pos_por_distancia_depot
    depot_distancias_route = [65.9, 59.3, 51.9]  # Tetela, Camalote, Acatlan
    assert _pos_por_distancia_depot(depot_distancias_route, 51.75) == 3


def test_insertar_mayoristas_en_bloques_punto_local_gana_a_distancia_matriz_ruidosa():
    # Bug real 2026-09-02 (T20 jueves): BB4067_ABARROTES EL GUERO esta a
    # 0.31km de Tlacojalpan por carretera (practicamente la misma parada),
    # pero su distancia real A LA MATRIZ (162.6km) resulto ~1.5km MAYOR que
    # la de Tlacojalpan (161.1km, la parada mas lejana de una ruta con forma
    # de "bulto": Chacaltianguis 147/Tlacojalpan 161/Otatitlan 143/Papaloapan
    # 138 -- ni creciente ni decreciente). Esa diferencia de ~1.5km sobre
    # magnitudes de ~150km es ruido de snapping de OSRM a la red vial, no
    # una senal real de que el mayorista va mas lejos que TODA la ruta.
    # _pos_por_distancia_depot, al no hallar ninguna parada con
    # distancia-a-matriz >= la del bloque, lo mandaba a pos=0 (antes de TODA
    # la ruta) en vez de junto a Tlacojalpan. Fix: cuando la distancia LOCAL
    # a una parada ya puesta es minuscula (< MISMO_PUNTO_KM), esa senal gana
    # sobre la distancia a la matriz.
    from logic.mayoristas_logic import _insertar_mayoristas_en_bloques
    depot = (0.0, 0.0)
    paradas = [
        {"tipo": "sucursal", "num_tienda": 1, "latitud": 0.0, "longitud": 5.0},
        {"tipo": "sucursal", "num_tienda": 2, "latitud": 0.0, "longitud": 9.0},
    ]
    bloque = [{"id_cliente": 1, "poblacion": "TLACOJALPAN", "latitud": 0.0, "longitud": 9.001}]

    def fake_distancias(origen, destinos):
        if origen == depot:
            # Precomputo inicial: A muy lejos (200), B mas cerca (150) --
            # tendencia decreciente.
            assert destinos == [(0.0, 5.0), (0.0, 9.0)]
            return [200.0, 150.0]
        # Por bloque: ancla -> [depot, A, B]. El bloque mide 205 de la
        # matriz (MAS que A, el mas lejano) pero esta a 0.11km de B.
        assert destinos == [depot, (0.0, 5.0), (0.0, 9.0)]
        return [205.0, 50.0, 0.11]

    _insertar_mayoristas_en_bloques(
        paradas, bloque, lambda m: {"tipo": "mayorista", "id_cliente": m["id_cliente"]},
        calcular_distancias_km=fake_distancias, depot=depot,
    )
    assert [p["tipo"] for p in paradas] == ["sucursal", "sucursal", "mayorista"]


def test_insertar_mayoristas_en_bloques_depot_ordena_por_distancia_real_a_la_matriz():
    # Reproduccion del bug real: la sucursal "Jalapa de Diaz 2" mide 209.3km
    # de la matriz por carretera; el bloque de San Lucas Ojitlan solo
    # 198.4km -- debe insertarse ANTES de esa sucursal, aunque este mucho
    # mas cerca de ella (21.6km) que de la matriz. Antes de este fix,
    # comparar contra la distancia directa (21.6km) siempre hacia ganar al
    # vecino mas cercano.
    from logic.mayoristas_logic import _insertar_mayoristas_en_bloques
    depot = (0.0, 0.0)
    paradas = [
        {"tipo": "sucursal", "num_tienda": 1, "latitud": 0.0, "longitud": 10.0},
    ]
    bloque = [{"id_cliente": 1, "poblacion": "OJITLAN", "latitud": 0.0, "longitud": 9.0}]

    def fake_distancias(origen, destinos):
        if origen == depot:
            # Precomputo inicial: matriz -> paradas existentes.
            assert destinos == [(0.0, 10.0)]
            return [209.3]
        # Por bloque: ancla -> [depot] + paradas existentes.
        assert destinos == [depot, (0.0, 10.0)]
        return [198.4, 21.6]

    _insertar_mayoristas_en_bloques(
        paradas, bloque, lambda m: {"tipo": "mayorista", "id_cliente": m["id_cliente"]},
        calcular_distancias_km=fake_distancias, depot=depot,
    )
    assert [p["tipo"] for p in paradas] == ["mayorista", "sucursal"]


def test_insertar_mayoristas_en_bloques_depot_considera_bloques_ya_insertados():
    # El segundo bloque debe compararse tambien contra la distancia a la
    # matriz del bloque YA insertado (no solo contra las sucursales
    # originales) -- si no, quedaria mal ubicado respecto a un bloque
    # vecino que ya se movio antes de la primera sucursal.
    from logic.mayoristas_logic import _insertar_mayoristas_en_bloques

    def _nodo(m):
        return {"tipo": "mayorista", "id_cliente": m["id_cliente"],
                "latitud": m["latitud"], "longitud": m["longitud"]}

    depot = (0.0, 0.0)
    paradas = [
        {"tipo": "sucursal", "num_tienda": 1, "latitud": 0.0, "longitud": 10.0},
    ]
    mayoristas_ordenados = [
        {"id_cliente": 1, "poblacion": "OJITLAN", "latitud": 0.0, "longitud": 9.0},
        {"id_cliente": 2, "poblacion": "MEDIO", "latitud": 0.0, "longitud": 9.5},
    ]

    def fake_distancias(origen, destinos):
        if origen == depot:
            return [209.3]  # precomputo inicial: solo la sucursal
        if origen == (0.0, 9.0):
            # bloque OJITLAN (procesado primero): [depot, sucursal]
            return [198.4, 21.6]
        if origen == (0.0, 9.5):
            # bloque MEDIO (procesado despues, ya existe OJITLAN insertado):
            # [depot, sucursal, nodo OJITLAN]
            return [203.0, 20.0, 5.0]
        raise AssertionError(f"origen inesperado: {origen}, destinos: {destinos}")

    _insertar_mayoristas_en_bloques(
        paradas, mayoristas_ordenados, _nodo,
        calcular_distancias_km=fake_distancias, depot=depot,
    )
    # Orden esperado por distancia creciente a la matriz:
    # OJITLAN (198.4) < MEDIO (203.0) < sucursal (209.3)
    assert [p.get("id_cliente") or p.get("num_tienda") for p in paradas] == [1, 2, 1]


def test_insertar_mayoristas_en_bloques_sin_depot_no_cambia_comportamiento():
    from logic.mayoristas_logic import _insertar_mayoristas_en_bloques
    paradas = [
        {"tipo": "sucursal", "num_tienda": 1, "latitud": 0.0, "longitud": 10.0},
    ]
    bloque = [{"id_cliente": 1, "poblacion": "OJITLAN", "latitud": 0.0, "longitud": 9.0}]

    def fake_distancias(origen, destinos):
        assert len(destinos) == 1  # sin depot, no se agrega destino extra
        return [50.0]

    _insertar_mayoristas_en_bloques(
        paradas, bloque, lambda m: {"tipo": "mayorista", "id_cliente": m["id_cliente"]},
        calcular_distancias_km=fake_distancias,
    )
    assert [p["tipo"] for p in paradas] == ["sucursal", "mayorista"]


def test_insertar_mayoristas_en_bloques_usa_calcular_distancias_km():
    from logic.mayoristas_logic import _insertar_mayoristas_en_bloques
    paradas = [
        {"tipo": "sucursal", "latitud": 0.0, "longitud": 0.0},
        {"tipo": "sucursal", "latitud": 0.0, "longitud": 10.0},
    ]
    bloque = [{"id_cliente": 1, "poblacion": "X", "latitud": 0.0, "longitud": 1.0}]
    llamadas = []

    def fake_distancias(origen, destinos):
        llamadas.append((origen, destinos))
        return [500.0, 5.0]  # invierte el resultado de haversine

    _insertar_mayoristas_en_bloques(
        paradas, bloque, lambda m: {"tipo": "mayorista", "id_cliente": m["id_cliente"]},
        calcular_distancias_km=fake_distancias,
    )

    assert [p.get("id_cliente") for p in paradas] == [None, None, 1]
    assert llamadas == [((0.0, 1.0), [(0.0, 0.0), (0.0, 10.0)])]


def test_insertar_mayoristas_en_bloques_fallback_haversine_si_calcular_distancias_km_falla():
    from logic.mayoristas_logic import _insertar_mayoristas_en_bloques
    paradas = [
        {"tipo": "sucursal", "latitud": 0.0, "longitud": 0.0},
        {"tipo": "sucursal", "latitud": 0.0, "longitud": 10.0},
    ]
    bloque = [{"id_cliente": 1, "poblacion": "X", "latitud": 0.0, "longitud": 1.0}]

    def rota(origen, destinos):
        raise RuntimeError("sin red")

    _insertar_mayoristas_en_bloques(
        paradas, bloque, lambda m: {"tipo": "mayorista", "id_cliente": m["id_cliente"]},
        calcular_distancias_km=rota,
    )

    # Sin distancias reales -> cae a haversine: parada 0 mas cerca (1 grado).
    assert [p.get("id_cliente") for p in paradas] == [None, 1, None]


def test_distancias_carretera_km_parsea_respuesta_osrm_ok(monkeypatch):
    from logic import mayoristas_logic

    class _FakeResp:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self):
            return b'{"code": "Ok", "distances": [[0, 12345.6, null]]}'

    monkeypatch.setattr(mayoristas_logic.urllib.request, "urlopen", lambda *a, **k: _FakeResp())

    resultado = mayoristas_logic._distancias_carretera_km(
        (18.87, -96.94), [(18.07, -96.53), (18.06, -96.46)])
    assert resultado[0] == pytest.approx(12.3456)
    assert resultado[1] is None


def test_distancias_carretera_km_none_si_falla_la_consulta(monkeypatch):
    from logic import mayoristas_logic

    def _rompe(*a, **k):
        raise mayoristas_logic.urllib.error.URLError("sin conexion")

    monkeypatch.setattr(mayoristas_logic.urllib.request, "urlopen", _rompe)

    resultado = mayoristas_logic._distancias_carretera_km((18.87, -96.94), [(18.07, -96.53)])
    assert resultado is None


def test_integrar_paradas_usa_calcular_distancias_km_si_se_provee():
    from logic.mayoristas_logic import _integrar_paradas, MATRIZ_LAT_DEFAULT, MATRIZ_LON_DEFAULT
    sucursales = [
        {"num_tienda": 1, "nombre_base": "A", "latitud": 0.0, "longitud": 0.0, "orden": 1},
        {"num_tienda": 2, "nombre_base": "B", "latitud": 0.0, "longitud": 10.0, "orden": 2},
    ]
    mayoristas = [{"id_cliente": 1, "poblacion": "X", "latitud": 0.0, "longitud": 1.0, "peso_kg": 5.0}]

    def fake_distancias(origen, destinos):
        if origen == (MATRIZ_LAT_DEFAULT, MATRIZ_LON_DEFAULT):
            # Precomputo matriz->paradas: no disponible en esta prueba, que
            # busca aislar el efecto de la distancia real LOCAL (no la
            # orden por matriz) -- cae a _insertar_pos_proxima.
            return None
        # destinos = [depot, sucursal A, sucursal B]. Depot lejos (no debe
        # ganar), y se invierte la linea recta entre A/B: gana B.
        return [1000.0, 500.0, 5.0]

    paradas = _integrar_paradas(sucursales, mayoristas, calcular_distancias_km=fake_distancias)
    orden = [(p["tipo"], p.get("num_tienda") or p.get("id_cliente")) for p in paradas]
    assert orden == [("sucursal", 1), ("sucursal", 2), ("mayorista", 1)]


def test_integrar_paradas_bloque_mas_cerca_del_depot_va_antes_de_la_primera_sucursal():
    # Reproduccion del bug real 2026-09-01: en el PDF, "Jalapa de Diaz 2"
    # (sucursal, SEC 1) seguia apareciendo antes que el mayorista de San
    # Lucas Ojitlan (SEC 2), aun con distancia real por carretera entre
    # mayoristas -- porque nada permitia insertar un bloque ANTES de la
    # primera sucursal de la ruta.
    from logic.mayoristas_logic import _integrar_paradas
    sucursales = [
        {"num_tienda": 1, "nombre_base": "Jalapa de Diaz 2", "latitud": 0.0, "longitud": 10.0, "orden": 1},
    ]
    mayoristas = [{"id_cliente": 1, "poblacion": "OJITLAN", "latitud": 0.0, "longitud": 9.0, "peso_kg": 5.0}]

    depot = (0.0, 0.0)

    def fake_distancias(origen, destinos):
        if origen == depot:
            return [209.3]  # precomputo: matriz -> sucursal
        return [198.4, 21.6]  # bloque: [depot, sucursal] -- mas cerca de la matriz

    paradas = _integrar_paradas(
        sucursales, mayoristas, depot_lat=0.0, depot_lon=0.0,
        calcular_distancias_km=fake_distancias,
    )
    assert [p["tipo"] for p in paradas] == ["mayorista", "sucursal"]


def test_distancias_carretera_km_sin_destinos_con_coords_no_consulta(monkeypatch):
    from logic import mayoristas_logic

    def _no_deberia_llamarse(*a, **k):
        raise AssertionError("no debe consultar OSRM si no hay destinos con coordenadas")

    monkeypatch.setattr(mayoristas_logic.urllib.request, "urlopen", _no_deberia_llamarse)

    resultado = mayoristas_logic._distancias_carretera_km((18.87, -96.94), [None, None])
    assert resultado == [None, None]


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
    # id_cliente 709 ("SUPER LA DESPENSA") en vez del 5 original del enunciado:
    # 5 nunca fue una fila real en `clientes_mayoristas`, y desde el fix de
    # "mayoristas sin coordenadas van a sin_coords" (última ronda de
    # revisión), un id sin coords reales ya NO aparece en
    # `mayoristas_por_ruta`/`paradas_integradas` (aparece en `sin_coords`) --
    # este test necesita un id con coords reales para seguir ejercitando el
    # camino normal (con coords) que es lo que verifica.
    from logic.mayoristas_logic import guardar_mayoristas_convrp, obtener_mayoristas_guardados
    lid = "507f1f77bcf86cd799439014"
    rutas = [{"_id": "vrpaf_v1_lunes", "sucursales": [
        {"num_tienda": 1, "nombre_base": "Suc 1", "latitud": 19.0, "longitud": -96.0,
         "orden": 1, "peso_kg": 500}]}]
    por_ruta = {("V1", "LUNES"): [
        {"id_cliente": 709, "nombre": "ABARROTES Y", "peso_kg": 30.0,
         "latitud": 19.001, "longitud": -96.001}]}
    guardar_mayoristas_convrp(lid, por_ruta, [], rutas)
    dist = obtener_mayoristas_guardados(lid, rutas)
    assert dist is not None
    assert dist["mayoristas_por_ruta"]["vrpaf_v1_lunes"][0]["id_cliente"] == 709
    assert dist["mayoristas_por_ruta"]["vrpaf_v1_lunes"][0]["peso_kg"] == 30.0
    paradas = dist["paradas_integradas"]["vrpaf_v1_lunes"]
    assert any(p["tipo"] == "mayorista" and p["id_cliente"] == 709 for p in paradas)
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


def test_obtener_mayoristas_guardados_ruta_ausente_en_rutas_no_se_pierde(app_ctx):
    from logic.mayoristas_logic import guardar_mayoristas_convrp, obtener_mayoristas_guardados
    lid = "507f1f77bcf86cd799439018"
    rutas_guardado = [{"_id": "vrpaf_v1_jueves", "sucursales": [
        {"num_tienda": 1, "latitud": 19.0, "longitud": -96.0, "orden": 1}]}]
    por_ruta = {("V1", "JUEVES"): [
        {"id_cliente": 709, "nombre": "SUPER LA DESPENSA", "peso_kg": 25.0,
         "latitud": 19.370, "longitud": -96.375}]}
    guardar_mayoristas_convrp(lid, por_ruta, [], rutas_guardado)
    # el llamador ahora pide SIN pasar esa ruta (simula un llamador que manda
    # un subconjunto de rutas, como router/asignacion_router.py)
    dist = obtener_mayoristas_guardados(lid, [])
    assert dist is not None
    assert dist["mayoristas_por_ruta"]["vrpaf_v1_jueves"][0]["id_cliente"] == 709


def test_obtener_mayoristas_guardados_orden_consistente_entre_mayoristas_por_ruta_y_paradas(app_ctx):
    from logic.mayoristas_logic import guardar_mayoristas_convrp, obtener_mayoristas_guardados
    lid = "507f1f77bcf86cd799439019"
    rutas = [{"_id": "vrpaf_v1_viernes", "sucursales": [
        {"num_tienda": 1, "latitud": 19.500, "longitud": -96.500, "orden": 1},
        {"num_tienda": 2, "latitud": 19.000, "longitud": -96.000, "orden": 2}]}]
    por_ruta = {("V1", "VIERNES"): [
        {"id_cliente": 709, "nombre": "SUPER LA DESPENSA", "peso_kg": 25.0,
         "latitud": 19.001, "longitud": -96.001}]}
    guardar_mayoristas_convrp(lid, por_ruta, [], rutas)
    dist = obtener_mayoristas_guardados(lid, rutas)
    entrada = dist["mayoristas_por_ruta"]["vrpaf_v1_viernes"][0]
    assert "orden" in entrada
    parada = next(p for p in dist["paradas_integradas"]["vrpaf_v1_viernes"]
                  if p["tipo"] == "mayorista")
    assert entrada["orden"] == parada["orden"]


def test_obtener_mayoristas_guardados_sin_coordenadas_va_a_sin_coords(app_ctx):
    from logic.mayoristas_logic import guardar_mayoristas_convrp, obtener_mayoristas_guardados
    lid = "507f1f77bcf86cd799439020"
    rutas = [{"_id": "vrpaf_v1_sabado", "sucursales": [
        {"num_tienda": 1, "latitud": 19.0, "longitud": -96.0, "orden": 1}]}]
    # id_cliente sin fila en clientes_mayoristas -> sin coordenadas
    # (verificado directamente contra la BD antes de escribir esta prueba:
    # 999999 no existe en clientes_mayoristas en este entorno)
    por_ruta = {("V1", "SABADO"): [
        {"id_cliente": 999999, "nombre": "SIN COORDS", "peso_kg": 15.0,
         "latitud": None, "longitud": None}]}
    guardar_mayoristas_convrp(lid, por_ruta, [], rutas)
    dist = obtener_mayoristas_guardados(lid, rutas)
    assert any(m["id_cliente"] == 999999 for m in dist["sin_coords"])
    assert dist["mayoristas_por_ruta"]["vrpaf_v1_sabado"] == []
    assert not any(p.get("id_cliente") == 999999 for p in dist["paradas_integradas"]["vrpaf_v1_sabado"])


def test_obtener_mayoristas_guardados_respeta_orden_no_posicion_de_lista(app_ctx):
    # Bug real (2026-08-28): el llamador (obtener_rutas_para_modificar) arma
    # `sucursales` leyendo `asignaciones_sucursales` con un SELECT sin
    # ORDER BY -- SQL Server no garantiza el orden de fila sin eso -- pero
    # cada sucursal SÍ trae su "orden" real correcto (el fijado por
    # orden_fijo_paradas/histórico). `obtener_mayoristas_guardados` debe
    # respetar ese campo "orden", no la posición en la lista de entrada.
    from logic.mayoristas_logic import guardar_mayoristas_convrp, obtener_mayoristas_guardados
    lid = "507f1f77bcf86cd799439021"
    rutas = [{"_id": "vrpaf_v1_domingo", "sucursales": [
        {"num_tienda": 30, "latitud": 19.002, "longitud": -96.002, "orden": 3},
        {"num_tienda": 10, "latitud": 19.000, "longitud": -96.000, "orden": 1},
        {"num_tienda": 20, "latitud": 19.001, "longitud": -96.001, "orden": 2},
    ]}]
    por_ruta = {("V1", "DOMINGO"): [
        {"id_cliente": 709, "nombre": "SUPER LA DESPENSA", "peso_kg": 10.0,
         "latitud": 19.0005, "longitud": -96.0005}]}
    guardar_mayoristas_convrp(lid, por_ruta, [], rutas)
    dist = obtener_mayoristas_guardados(lid, rutas)
    orden_map = dist["orden_sucursales"]["vrpaf_v1_domingo"]
    assert orden_map["10"] < orden_map["20"] < orden_map["30"], (
        f"se perdió el orden real de las sucursales: {orden_map}")
