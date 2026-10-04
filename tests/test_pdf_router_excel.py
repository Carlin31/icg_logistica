"""
tests/test_pdf_router_excel.py

POST /pdf/generar-excel: recibe el PDF de la vista previa y devuelve el .xlsx.
Flask mínimo con solo el blueprint del PDF y sesión de prueba. Sin BD.
"""
import io
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import openpyxl
import pytest
from flask import Flask

from router import pdf_router
from tests.test_excel_logic import _pdf, _pdf_sin_tablas, _veh_simple

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture
def cliente():
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.secret_key = "test"
    app.register_blueprint(pdf_router.pdf_bp, url_prefix="/pdf")
    return app.test_client()


def _activar_logistica(cliente):
    with cliente.session_transaction() as s:
        s["logistica_id"] = "abc123"
        s["logistica_nombre"] = "Semana de prueba"


def _post(cliente, contenido, nombre="reporte.pdf"):
    return cliente.post("/pdf/generar-excel",
                        data={"pdf": (io.BytesIO(contenido), nombre)},
                        content_type="multipart/form-data")


def test_sin_logistica_activa_responde_400(cliente):
    r = _post(cliente, _pdf([_veh_simple("F 350_1", "CESAR")]))
    assert r.status_code == 400
    assert r.get_json()["status"] == "error"


def test_sin_archivo_responde_400(cliente):
    _activar_logistica(cliente)
    r = cliente.post("/pdf/generar-excel", data={}, content_type="multipart/form-data")
    assert r.status_code == 400
    assert "PDF" in r.get_json()["mensaje"]


def test_archivo_que_no_es_pdf_responde_400(cliente):
    _activar_logistica(cliente)
    r = _post(cliente, b"esto no es un pdf", nombre="falso.pdf")
    assert r.status_code == 400


def test_archivo_demasiado_grande_responde_400(cliente, monkeypatch):
    _activar_logistica(cliente)
    monkeypatch.setattr(pdf_router, "MAX_PDF_BYTES", 100)
    r = _post(cliente, _pdf([_veh_simple("F 350_1", "CESAR")]))
    assert r.status_code == 400
    assert "grande" in r.get_json()["mensaje"]


def test_pdf_sin_tablas_de_rutas_responde_422(cliente):
    _activar_logistica(cliente)
    r = _post(cliente, _pdf_sin_tablas())
    assert r.status_code == 422
    assert "no contiene tablas" in r.get_json()["mensaje"]


def test_exito_devuelve_un_xlsx_adjunto_con_los_datos_del_pdf(cliente):
    _activar_logistica(cliente)
    r = _post(cliente, _pdf([_veh_simple("F 350_1", "CESAR")]))
    assert r.status_code == 200
    assert r.mimetype == XLSX
    assert "Semana_de_prueba.xlsx" in r.headers["Content-Disposition"]
    ws = openpyxl.load_workbook(io.BytesIO(r.data)).active
    assert ws.title == "RESUMEN"
    assert ws["A5"].value == "F 350_1 · CESAR (4 ton)"
