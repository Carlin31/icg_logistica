"""
tests/test_consolidacion_mayoristas.py

Consolidación de documentos de mayoreo en PARADAS, previa al VRP. Puro: sin BD.

El sistema venía ruteando **documento por documento**: los 4 folios de PLAZA
COMERCIAL RIO (3,152 kg, un solo domicilio) terminaban en tres viajes distintos
—F 350_2/martes, T 25/miércoles, T 17_2/jueves— porque para el solver eran tres
pedidos independientes. La hoja del planeador los manda a los tres juntos: es
UNA parada.

Regla: los documentos que se entregan en el mismo punto son una sola parada, con
peso sumado, etiqueta de folios concatenada y **carga indivisible**. Si la parada
consolidada no cabe en la unidad, se parte A PROPÓSITO y la partición queda
registrada — nunca en silencio por el solver.

La llave es la PROXIMIDAD, no el nombre: los cuatro casos reales lo obligan.
  Cuitláhuac  AA1430..36  4 clientes distintos, 0.18 km  -> una parada (315 kg)
  Lombardo    BB3297..00  3 clientes, poblaciones que ni
                          se escriben igual, 0.22 km      -> una parada (265 kg)
  Playa Vic.  BB3304..21  1 cliente, 4 folios, 0.00 km    -> una parada (3,152 kg)
  Sochiapan   BB3294/96   2 clientes a **51 km**          -> NO se consolidan
"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from logic.consolidacion_mayoristas import (consolidar_documentos,
                                            partir_parada_por_capacidad)


# ── catálogo mínimo, con los datos reales de los cuatro casos ──────────────
CLIENTES = {
    # Playa Vicente: un solo cliente, cuatro folios
    173: {"nombre": "PLAZA COMERCIAL RIO", "poblacion": "PLAYA VICENTE",
          "latitud": 17.8328, "longitud": -95.8228},
    # Cuitláhuac: cuatro clientes, el último a 180 m
    686: {"nombre": "SUPER CUITLAHUAC", "poblacion": "CUITLAHUAC",
          "latitud": 18.81386, "longitud": -96.72224},
    424: {"nombre": "CRISTIAN SALDAÑA (SUPER CUITLAHUAC)", "poblacion": "CUITLAHUAC",
          "latitud": 18.81386, "longitud": -96.72224},
    425: {"nombre": "RENE SALDAÑA (SUPER CUITLAHUAC)", "poblacion": "CUITLAHUAC",
          "latitud": 18.81386, "longitud": -96.72224},
    503: {"nombre": "SUPER LA MICHOACANA", "poblacion": "CUITLAHUAC",
          "latitud": 18.8142, "longitud": -96.7239},
    # Lombardo: la población ni se escribe igual entre ellos
    401: {"nombre": "ABARROTES MATUS", "poblacion": "LOMBARDO",
          "latitud": 17.4500, "longitud": -95.4297},
    391: {"nombre": "ABARROTES MIRIAM", "poblacion": "MARIA LOMBARDO DE CASO",
          "latitud": 17.4500, "longitud": -95.4297},
    395: {"nombre": "CHILES Y SEMILLAS LA ESPERANZA", "poblacion": "MARIA LOMBARDO DE CASO",
          "latitud": 17.4487, "longitud": -95.4281},
    # Sochiapan: nombres parecidos, 51 km de distancia
    722: {"nombre": "TIENDA DE ABARROTES JEVAS", "poblacion": "XOCHIAPAN",
          "latitud": 17.9465, "longitud": -95.9185},
    183: {"nombre": "ABARROTES CRUZ VERDE", "poblacion": "SANTIAGO SOCHIAPAN",
          "latitud": 17.5395, "longitud": -95.6927},
}


def _doc(folio, codigo, kg):
    return {"documento": folio, "codigo": codigo,
            "nombre": CLIENTES[codigo]["nombre"], "peso_total_kg": kg}


def _parada_de(paradas, folio):
    return next(p for p in paradas if folio in p["folios"])


# ══ 1. Un cliente, varios folios ═══════════════════════════════════════════
def test_los_folios_de_un_mismo_cliente_son_una_sola_parada():
    docs = [_doc("BB3304", 173, 114.1), _doc("BB3305", 173, 127.2),
            _doc("BB3320", 173, 1098.12), _doc("BB3321", 173, 1813.24)]
    paradas = consolidar_documentos(docs, CLIENTES)
    assert len(paradas) == 1
    p = paradas[0]
    assert round(p["peso_kg"]) == 3153
    assert p["folios"] == ["BB3304", "BB3305", "BB3320", "BB3321"]
    assert p["id_clientes"] == [173]


# ══ 2. Varios clientes en el mismo punto ═══════════════════════════════════
def test_cuitlahuac_cuatro_clientes_distintos_son_una_parada():
    # AA1430 AL 36: cuatro razones sociales, el mismo local (y LA MICHOACANA a
    # 180 m). El planeador las escribe en una sola línea de 315 kg.
    docs = [_doc("AA1430", 686, 93.0), _doc("AA1431", 686, 41.4),
            _doc("AA1432", 424, 21.2), _doc("AA1433", 424, 56.0),
            _doc("AA1434", 425, 71.2), _doc("AA1435", 425, 11.5),
            _doc("AA1436", 503, 20.7)]
    paradas = consolidar_documentos(docs, CLIENTES)
    assert len(paradas) == 1
    assert round(paradas[0]["peso_kg"]) == 315
    assert sorted(paradas[0]["id_clientes"]) == [424, 425, 503, 686]


def test_lombardo_se_consolida_aunque_la_poblacion_este_escrita_distinto():
    # 'LOMBARDO' y 'MARIA LOMBARDO DE CASO' son el mismo pueblo escrito de dos
    # formas: agrupar por texto de población los separaría. Manda la distancia.
    docs = [_doc("BB3297", 401, 20.6), _doc("BB3298", 391, 142.2),
            _doc("BB3299", 395, 89.5), _doc("BB3300", 395, 12.6)]
    paradas = consolidar_documentos(docs, CLIENTES)
    assert len(paradas) == 1
    assert round(paradas[0]["peso_kg"]) == 265


# ══ 3. Lo que NO se debe consolidar ════════════════════════════════════════
def test_sochiapan_no_se_consolida_estan_a_51_km():
    # El planeador los escribe en una sola línea ('BB3294/96_CTES.SOCHIAPAN'),
    # pero los puntos están a 51 km: no puede ser una parada. Se reporta la
    # discrepancia; no se fuerza la unión.
    docs = [_doc("BB3294", 722, 14.3), _doc("BB3296", 183, 58.5)]
    paradas = consolidar_documentos(docs, CLIENTES)
    assert len(paradas) == 2


def test_sin_coordenadas_nunca_se_mezcla_con_otro_cliente():
    # Sin coordenada no hay evidencia de que sea el mismo punto: cada cliente
    # queda solo y la parada se marca, en vez de adivinar por nombre.
    clientes = dict(CLIENTES)
    clientes[999] = {"nombre": "SIN COORDENADA", "poblacion": "CUITLAHUAC",
                     "latitud": None, "longitud": None}
    docs = [_doc("AA1430", 686, 93.0),
            {"documento": "AA9999", "codigo": 999, "nombre": "SIN COORDENADA",
             "peso_total_kg": 10.0}]
    paradas = consolidar_documentos(docs, clientes)
    assert len(paradas) == 2
    sin = _parada_de(paradas, "AA9999")
    assert sin["sin_coordenada"] is True


# ══ 4. Determinismo y contabilidad ═════════════════════════════════════════
def test_no_se_pierde_ni_se_duplica_un_solo_kilo():
    docs = [_doc("AA1430", 686, 93.0), _doc("AA1436", 503, 20.7),
            _doc("BB3294", 722, 14.3), _doc("BB3296", 183, 58.5),
            _doc("BB3321", 173, 1813.24)]
    paradas = consolidar_documentos(docs, CLIENTES)
    assert round(sum(p["peso_kg"] for p in paradas), 2) == round(
        sum(d["peso_total_kg"] for d in docs), 2)
    folios = sorted(f for p in paradas for f in p["folios"])
    assert folios == sorted(d["documento"] for d in docs)


def test_es_determinista_ante_orden_de_entrada_invertido():
    docs = [_doc("AA1430", 686, 93.0), _doc("AA1432", 424, 21.2),
            _doc("AA1436", 503, 20.7), _doc("BB3297", 401, 20.6)]
    a = consolidar_documentos(docs, CLIENTES)
    b = consolidar_documentos(list(reversed(docs)), CLIENTES)
    norm = lambda ps: sorted((tuple(p["folios"]), round(p["peso_kg"], 2)) for p in ps)
    assert norm(a) == norm(b)


def test_la_etiqueta_lleva_los_folios_y_el_destinatario():
    docs = [_doc("BB3304", 173, 114.1), _doc("BB3321", 173, 1813.24)]
    p = consolidar_documentos(docs, CLIENTES)[0]
    assert p["etiqueta"].startswith("BB3304")
    assert "PLAZA COMERCIAL RIO" in p["etiqueta"]


def test_varios_clientes_la_etiqueta_usa_la_poblacion():
    docs = [_doc("AA1430", 686, 93.0), _doc("AA1432", 424, 21.2)]
    p = consolidar_documentos(docs, CLIENTES)[0]
    assert "CTES." in p["etiqueta"] and "CUITLAHUAC" in p["etiqueta"]


# ══ 5. Indivisible… salvo excepción explícita ══════════════════════════════
def test_una_parada_que_cabe_no_se_parte():
    docs = [_doc("BB3304", 173, 114.1), _doc("BB3321", 173, 1813.24)]
    p = consolidar_documentos(docs, CLIENTES)[0]
    partes, exc = partir_parada_por_capacidad(p, 3900)
    assert partes == [p] and exc is None


def test_una_parada_que_no_cabe_se_parte_por_folio_y_queda_registrada():
    # 3,153 kg no caben en un T 17 de 2,500: se parte A PROPÓSITO, por folios
    # completos (BB3321+BB3305+BB3304 = 2,054 y BB3320 = 1,098), y la división
    # se registra para imprimirla.
    docs = [_doc("BB3304", 173, 114.1), _doc("BB3305", 173, 127.2),
            _doc("BB3320", 173, 1098.12), _doc("BB3321", 173, 1813.24)]
    p = consolidar_documentos(docs, CLIENTES)[0]
    partes, exc = partir_parada_por_capacidad(p, 2500)
    assert len(partes) > 1
    assert all(x["peso_kg"] <= 2500 + 1e-6 for x in partes)
    assert round(sum(x["peso_kg"] for x in partes), 2) == round(p["peso_kg"], 2)
    assert sorted(f for x in partes for f in x["folios"]) == sorted(p["folios"])
    assert exc and exc["tipo"] == "PARADA_MAYORISTA_PARTIDA"
    assert exc["capacidad_kg"] == 2500


def test_un_solo_folio_mas_grande_que_el_camion_no_se_parte_en_silencio():
    # Un folio no se puede dividir: si por sí solo excede la unidad, se reporta
    # y se deja entero. El planeador tiene que verlo, no el solver resolverlo.
    docs = [_doc("BB3321", 173, 1813.24)]
    p = consolidar_documentos(docs, CLIENTES)[0]
    partes, exc = partir_parada_por_capacidad(p, 1300)
    assert len(partes) == 1
    assert exc and exc["tipo"] == "PARADA_MAYORISTA_SIN_CUPO"
