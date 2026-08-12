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
