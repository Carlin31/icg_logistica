"""
tests/test_pdf_logic.py

Backfill de `documento` para mayoristas que vienen de `convrp_mayoristas`
(vía obtener_mayoristas_guardados): ese camino trabaja a nivel cliente y
no conserva el folio del pedido (ver mayoristas_logic.py:1083). El PDF
necesita mostrar los folios ('BB3909/10/11'), así que se reponen cruzando
por id_cliente contra los mayoristas crudos de `extraccion.mayoristas`.
Puro: sin BD.
"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from logic.pdf_logic import (
    _agrupar_documentos_por_cliente,
    _backfill_documento,
    _formatear_docs_agrupados,
)


def test_agrupar_documentos_por_cliente_junta_folios_de_un_mismo_cliente():
    raw = [
        {"codigo": 440, "documento": "BB3909", "nombre": "ABARROTES DON LALO", "peso_total_kg": 10.0},
        {"codigo": 440, "documento": "BB3910", "nombre": "ABARROTES DON LALO", "peso_total_kg": 20.0},
        {"codigo": 440, "documento": "BB3911", "nombre": "ABARROTES DON LALO", "peso_total_kg": 5.0},
    ]
    mapa = _agrupar_documentos_por_cliente(raw)
    assert mapa[440] == "BB3909/10/11"


def test_agrupar_documentos_por_cliente_un_solo_folio():
    raw = [{"codigo": 7, "documento": "AA100", "nombre": "X", "peso_total_kg": 1.0}]
    mapa = _agrupar_documentos_por_cliente(raw)
    assert mapa[7] == "AA100"


def test_agrupar_documentos_por_cliente_ignora_filas_sin_documento():
    raw = [{"codigo": 9, "documento": "", "nombre": "SIN FOLIO", "peso_total_kg": 1.0}]
    mapa = _agrupar_documentos_por_cliente(raw)
    assert 9 not in mapa


def test_backfill_documento_rellena_desde_mapa_por_cliente():
    mayoristas = [{"id_cliente": 440, "nombre": "ABARROTES DON LALO", "peso_kg": 35.0}]
    _backfill_documento(mayoristas, {440: "BB3909/10/11"})
    assert mayoristas[0]["documento"] == "BB3909/10/11"


def test_backfill_documento_no_sobreescribe_documento_ya_presente():
    mayoristas = [{"id_cliente": 1, "documento": "AA1", "nombre": "X"}]
    _backfill_documento(mayoristas, {1: "ZZ9"})
    assert mayoristas[0]["documento"] == "AA1"


def test_backfill_documento_cliente_sin_mapa_deja_vacio_no_falla():
    mayoristas = [{"id_cliente": 5, "nombre": "Y"}]
    _backfill_documento(mayoristas, {})
    assert mayoristas[0]["documento"] == ""


def test_formatear_docs_agrupados_no_pierde_folios_de_cliente_ya_comprimido():
    # Caso real 2026-10-05 (F 350_2 martes, Tuxtepec): _agrupar_documentos_por_cliente
    # entrega un texto ya comprimido por cliente ('BB4490/91', 'BB4494/95/96/97') y
    # al juntar clientes en una sola parada se volvia a comprimir tomando solo los
    # ultimos 2 digitos de TODO el texto: salia 'BB4488/89/91/97' y los folios
    # 4490, 4494, 4495 y 4496 desaparecian de la etiqueta (aunque su peso si sumaba).
    docs = ["BB4494/95/96/97", "BB4490/91", "BB4488/89"]
    assert _formatear_docs_agrupados(docs) == "BB4488/89/90/91/94/95/96/97"


def test_formatear_docs_agrupados_mezcla_comprimidos_y_sueltos():
    docs = ["BB4490/91", "BB4492"]
    assert _formatear_docs_agrupados(docs) == "BB4490/91/92"


def test_formatear_docs_agrupados_comprimido_de_otro_prefijo_conserva_su_prefijo():
    docs = ["AA2107", "BB4490/91"]
    assert _formatear_docs_agrupados(docs) == "AA2107/BB4490/91"


def test_formatear_docs_agrupados_sueltos_siguen_igual():
    assert _formatear_docs_agrupados(["BB2874", "BB2872", "BB2873"]) == "BB2872/73/74"
