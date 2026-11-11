"""
tests/test_pdf_chofer_por_dia.py

Un camión con choferes distintos en días distintos (p. ej. F 350_1: CÉSAR el
martes, SERGIO el jueves) sale en UNA sola tabla del PDF: el encabezado lleva
al chofer principal y el día de otro chofer lleva su nombre bajo el día.
Puro: sin BD.
"""
import io
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from reportlab.lib.pagesizes import LETTER, portrait
from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate

from logic import pdf_logic as P
from logic.excel_logic import leer_pdf


def _ruta(dia, nombre, chofer=None, override=None, placas="XU-1", abrev="F 350_1"):
    r = {"id": f"{abrev}_{dia}", "dia": dia, "vehiculo_abrev": abrev,
         "vehiculo_placas": placas, "peso_kg": 500.0, "pct_utilizacion": 20.0,
         "tipo": "ruta", "mayoristas": [],
         "sucursales": [{"orden": 1, "nombre": nombre, "peso_kg": 500.0, "num_tienda": None}]}
    if chofer is not None:
        r["chofer"] = chofer
    if override is not None:
        r["chofer_override"] = override
    return r


def _pdf(grupos) -> bytes:
    buf = io.BytesIO()
    doc = BaseDocTemplate(buf, pagesize=portrait(LETTER), rightMargin=P.MARGEN,
                          leftMargin=P.MARGEN, topMargin=65, bottomMargin=P.MARGEN)
    marcos = [Frame(P.MARGEN + i * (P.ANCHO_COL + P.ESPACIO_ENTRE_COLS), P.MARGEN,
                    P.ANCHO_COL, P.PH - 75, leftPadding=0, rightPadding=0,
                    topPadding=0, bottomPadding=0, id=f"c{i}") for i in range(2)]
    doc.addPageTemplates([PageTemplate(id="p", frames=marcos,
                                       onPage=P._draw_header("Semana", "01/01/2026"))])
    el = []
    for g in grupos:
        el.extend(P._tabla_vehiculo(g["veh"], g["placas"], g["rutas"], g["chofer"],
                                    g["ton"], None, {}, None))
    doc.build(el)
    return buf.getvalue()


CHOFER_POR_PLACAS = {"XU-1": {"chofer": "CÉSAR", "ton": 4.0}}


# ── _agrupar_rutas_por_vehiculo ───────────────────────────────
def test_un_camion_con_dos_choferes_es_un_solo_grupo():
    rutas = [_ruta("martes", "A", chofer="CÉSAR"), _ruta("jueves", "B", chofer="SERGIO")]
    grupos = P._agrupar_rutas_por_vehiculo(rutas, CHOFER_POR_PLACAS)
    assert len(grupos) == 1
    assert grupos[0]["veh"] == "F 350_1"
    assert [r["_chofer_dia"] for r in grupos[0]["rutas"]] == ["CÉSAR", "SERGIO"]


def test_empate_el_encabezado_usa_el_chofer_por_defecto_del_camion():
    rutas = [_ruta("martes", "A", chofer="SERGIO"), _ruta("jueves", "B", chofer="CÉSAR")]
    grupos = P._agrupar_rutas_por_vehiculo(rutas, CHOFER_POR_PLACAS)
    assert grupos[0]["chofer"] == "CÉSAR"


def test_gana_el_chofer_con_mas_dias():
    rutas = [_ruta("martes", "A", chofer="ADOLFO"), _ruta("jueves", "B", chofer="ADOLFO"),
             _ruta("viernes", "C", chofer="CÉSAR")]
    grupos = P._agrupar_rutas_por_vehiculo(rutas, CHOFER_POR_PLACAS)
    assert grupos[0]["chofer"] == "ADOLFO"


def test_override_como_dict_se_resuelve_a_su_nombre():
    # `chofer_override` llega de BD como {"nombre", "chofer_id"} (no como texto)
    rutas = [_ruta("martes", "A", override={"nombre": "SERGIO", "chofer_id": "x"})]
    grupos = P._agrupar_rutas_por_vehiculo(rutas, CHOFER_POR_PLACAS)
    assert grupos[0]["rutas"][0]["_chofer_dia"] == "SERGIO"


def test_sin_chofer_en_la_ruta_usa_el_del_camion():
    grupos = P._agrupar_rutas_por_vehiculo([_ruta("martes", "A")], CHOFER_POR_PLACAS)
    assert grupos[0]["chofer"] == "CÉSAR"
    assert grupos[0]["rutas"][0]["_chofer_dia"] == "CÉSAR"


def test_camiones_distintos_siguen_separados_y_ordenados():
    rutas = [_ruta("martes", "A", chofer="X", placas="P2", abrev="T 23"),
             _ruta("martes", "B", chofer="Y", placas="P1", abrev="F 350_1")]
    grupos = P._agrupar_rutas_por_vehiculo(rutas, {})
    assert [g["veh"] for g in grupos] == ["F 350_1", "T 23"]


# ── _tabla_vehiculo / PDF ─────────────────────────────────────
def test_pdf_una_sola_tabla_y_el_dia_de_otro_chofer_lleva_su_nombre():
    rutas = [_ruta("martes", "A", chofer="CÉSAR"), _ruta("jueves", "B", chofer="SERGIO")]
    datos = leer_pdf(_pdf(P._agrupar_rutas_por_vehiculo(rutas, CHOFER_POR_PLACAS)))
    assert len(datos["vehiculos"]) == 1
    assert datos["vehiculos"][0]["titulo"] == "F 350_1 · CÉSAR (4 ton)"
    assert [d["dia"] for d in datos["vehiculos"][0]["dias"]] == ["MARTES", "JUEVES (SERGIO)"]


def test_pdf_sin_cambio_de_chofer_no_agrega_nada_al_dia():
    rutas = [_ruta("martes", "A", chofer="CÉSAR"), _ruta("jueves", "B", chofer="CÉSAR")]
    datos = leer_pdf(_pdf(P._agrupar_rutas_por_vehiculo(rutas, CHOFER_POR_PLACAS)))
    assert [d["dia"] for d in datos["vehiculos"][0]["dias"]] == ["MARTES", "JUEVES"]
