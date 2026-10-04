# Exportar la logística a Excel (a partir del PDF) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agregar un botón **Descargar Excel** (junto a *Descargar PDF*, visible solo tras generar el PDF) que convierte el PDF ya generado en un Excel vertical con un bloque por vehículo.

**Architecture:** El navegador reenvía el blob del PDF de la vista previa a `POST /pdf/generar-excel`; el servidor lo lee con `pdfplumber` (`logic/excel_logic.py`) y escribe un `.xlsx` con `openpyxl`. No se toca `logic/pdf_logic.py` ni `POST /pdf/generar`. Sin estado en el servidor.

**Tech Stack:** Python 3.11, Flask, pdfplumber 0.11.10 (nuevo), openpyxl 3.1.5, reportlab (solo en tests), pytest, JS vanilla.

**Spec:** `docs/superpowers/specs/2026-10-04-excel-desde-pdf-design.md`

## Reglas del proyecto que aplican

- Trabajar en `C:\Users\carli\Documents\ICG\logistica_icg`. **Solo commits locales; nunca `git push` ni deploy.**
- `git status` muestra `datos/grupo_fijo_mayoristas.csv` modificado y NO es parte de este trabajo: hacer `git add` **solo de los archivos listados en cada tarea**, nunca `git add -A` ni `git add .`.
- Los commits terminan con `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- `README.md` se actualiza en el mismo cambio (hay un endpoint y una dependencia nuevos).
- Comandos de ejemplo en bash (Git Bash), desde `logistica_icg/`; el intérprete es `./env/Scripts/python.exe`.

## Estructura de archivos

| Archivo | Acción | Responsabilidad |
|---|---|---|
| `requirements.txt` | Modificar | Agregar `pdfplumber==0.11.10` |
| `logic/excel_logic.py` | Crear | `leer_pdf()` (PDF → datos) y `pdf_a_excel()` (datos → bytes xlsx) |
| `tests/test_excel_logic.py` | Crear | Genera PDFs de muestra con el `_tabla_vehiculo` real y prueba lector + escritor |
| `router/pdf_router.py` | Modificar | Endpoint `POST /generar-excel` |
| `tests/test_pdf_router_excel.py` | Crear | Pruebas del endpoint con un Flask mínimo (sin BD) |
| `templates/pdf/index.html` | Modificar | Botón `btn-excel` (oculto por defecto) |
| `static/js/pdf.js` | Modificar | Mostrar el botón tras generar; `descargarExcel()` |
| `README.md` | Modificar | Endpoint, dependencia y flujo |

`logic/pdf_logic.py` **no se modifica** (verificación final en la Tarea 4).

---

### Task 1: Dependencia y módulo `excel_logic` (lector + escritor)

**Files:**
- Modify: `requirements.txt`
- Create: `tests/test_excel_logic.py`
- Create: `logic/excel_logic.py`

- [ ] **Step 1: Agregar la dependencia e instalarla**

En `requirements.txt`, agregar esta línea después de `reportlab==4.4.10`:

```
pdfplumber==0.11.10
```

Run: `./env/Scripts/python.exe -m pip install -r requirements.txt`
Expected: termina sin error; `./env/Scripts/python.exe -c "import pdfplumber; print(pdfplumber.__version__)"` imprime `0.11.10`.

- [ ] **Step 2: Escribir los tests (fallan porque el módulo no existe)**

Crear `tests/test_excel_logic.py` con exactamente este contenido:

```python
"""
tests/test_excel_logic.py

El Excel se arma leyendo el PDF ya generado (logic/excel_logic.py). Para que
estos tests fallen si el PDF cambia, el PDF de muestra se genera con el
`_tabla_vehiculo` REAL de logic/pdf_logic.py (db=None: sin BD). Puro: sin BD.
"""
import io
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import openpyxl
import pytest
from reportlab.lib.pagesizes import LETTER, portrait
from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate, Spacer

from logic import pdf_logic as P
from logic.excel_logic import leer_pdf, pdf_a_excel


# ── Fixtures ──────────────────────────────────────────────────
def _suc(orden, nombre, peso, num_tienda=None):
    return {"orden": orden, "nombre": nombre, "peso_kg": peso, "num_tienda": num_tienda}


def _may(orden, nombre, peso):
    return {"orden": orden, "nombre": nombre, "documento": nombre, "peso_kg": peso}


def _ruta(dia, sucursales, mayoristas=(), pct=50.0):
    peso = sum(s["peso_kg"] for s in sucursales) + sum(m["peso_kg"] for m in mayoristas)
    return {"dia": dia, "peso_kg": peso, "pct_utilizacion": pct, "tipo": "ruta",
            "sucursales": list(sucursales), "mayoristas": list(mayoristas)}


def _pdf(vehiculos, vol_map=None) -> bytes:
    """
    PDF con el mismo documento de dos columnas que arma generar_pdf
    (pdf_logic.py, 'doc_pdf'), pero alimentado con rutas en memoria.
    vehiculos: [(abrev, placas, chofer, ton, [rutas])]
    """
    buf = io.BytesIO()
    doc = BaseDocTemplate(buf, pagesize=portrait(LETTER),
                          rightMargin=P.MARGEN, leftMargin=P.MARGEN,
                          topMargin=65, bottomMargin=P.MARGEN)
    marcos = [
        Frame(P.MARGEN + i * (P.ANCHO_COL + P.ESPACIO_ENTRE_COLS), P.MARGEN,
              P.ANCHO_COL, P.PH - 75,
              leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0, id=f"c{i}")
        for i in range(2)
    ]
    doc.addPageTemplates([PageTemplate(
        id="DosColumnas", frames=marcos,
        onPage=P._draw_header("Semana de prueba  2026-10-05 — 2026-10-09",
                              "04/10/2026 10:00:00"))])
    elementos = []
    for abrev, placas, chofer, ton, rutas in vehiculos:
        elementos.extend(P._tabla_vehiculo(abrev, placas, rutas, chofer, ton,
                                           None, vol_map or {}, None))
    doc.build(elementos)
    return buf.getvalue()


def _pdf_sin_tablas() -> bytes:
    buf = io.BytesIO()
    doc = BaseDocTemplate(buf, pagesize=portrait(LETTER))
    doc.addPageTemplates([PageTemplate(id="p", frames=[Frame(20, 20, 500, 700)])])
    doc.build([Spacer(1, 10)])
    return buf.getvalue()


def _veh_simple(abrev, chofer, n_paradas=4):
    sucs = [_suc(i, f"Sucursal {abrev} {i}", 100 * i) for i in range(1, n_paradas + 1)]
    return (abrev, "XX-000", chofer, 4.0, [_ruta("martes", sucs)])


# ── leer_pdf ──────────────────────────────────────────────────
def test_cabecera_se_lee_del_pdf():
    datos = leer_pdf(_pdf([_veh_simple("F 350_1", "CESAR")]))
    assert datos["cabecera"][0] == "INTEGRADORA COMERCIAL DEL GOLFO"
    assert "Semana de prueba" in datos["cabecera"][1]
    assert datos["cabecera"][2] == "Expedido: 04/10/2026 10:00:00"


def test_titulo_del_vehiculo_incluye_chofer_y_toneladas():
    datos = leer_pdf(_pdf([_veh_simple("F 350_1", "CESAR")]))
    assert datos["vehiculos"][0]["titulo"] == "F 350_1 · CESAR (4 ton)"


def test_paradas_con_peso_volumen_y_porcentaje():
    ruta = _ruta("martes",
                 [_suc(1, "Cosamaloapan 1", 861, num_tienda=11),
                  _suc(2, "Cosamaloapan 2", 545, num_tienda=12)],
                 pct=35.2)
    datos = leer_pdf(_pdf([("F 350_1", "XX", "CESAR", 4.0, [ruta])],
                          vol_map={11: 1.209, 12: 0.909}))
    dia = datos["vehiculos"][0]["dias"][0]
    assert [p["parada"] for p in dia["paradas"]] == ["Cosamaloapan 1", "Cosamaloapan 2"]
    assert [p["sec"] for p in dia["paradas"]] == ["1", "2"]
    assert [p["peso"] for p in dia["paradas"]] == [861.0, 545.0]
    assert [p["vol"] for p in dia["paradas"]] == [1.209, 0.909]
    assert dia["paradas"][0]["pct"] == pytest.approx(0.61)   # el PDF muestra 61% (sin decimales)


def test_fila_total_por_dia():
    ruta = _ruta("martes", [_suc(1, "A", 861, 11), _suc(2, "B", 545, 12)], pct=35.2)
    datos = leer_pdf(_pdf([("F 350_1", "XX", "CESAR", 4.0, [ruta])],
                          vol_map={11: 1.0, 12: 0.5}))
    total = datos["vehiculos"][0]["dias"][0]["total"]
    assert total["parada"].startswith("TOTAL MARTES")
    assert total["peso"] == 1406.0
    assert total["vol"] == 1.5
    assert total["pct"] == pytest.approx(0.352)


def test_volumen_ausente_se_lee_como_none():
    ruta = _ruta("martes", [_suc(1, "Sin volumen", 100)])
    datos = leer_pdf(_pdf([("F 350_1", "XX", "CESAR", 4.0, [ruta])]))
    assert datos["vehiculos"][0]["dias"][0]["paradas"][0]["vol"] is None


def test_mayorista_se_detecta_por_el_color_naranja():
    ruta = _ruta("lunes",
                 [_suc(1, "Piedras Negras", 951), _suc(3, "Tlalixcoyan", 405)],
                 [_may(2, "AA2111_CLAUDIA GUEVARA", 21)])
    datos = leer_pdf(_pdf([("J 19", "XX", "VICTOR", 2.5, [ruta])]))
    paradas = datos["vehiculos"][0]["dias"][0]["paradas"]
    assert [(p["parada"], p["mayorista"]) for p in paradas] == [
        ("Piedras Negras", False),
        ("AA2111_CLAUDIA GUEVARA", True),
        ("Tlalixcoyan", False),
    ]
    assert "1 may." in datos["vehiculos"][0]["dias"][0]["total"]["parada"]


def test_varios_dias_dia_y_apoyo_correctos_y_en_orden():
    martes = _ruta("martes", [_suc(i, f"M{i}", 400) for i in range(1, 6)])   # 5 suc -> 2 aux
    jueves = _ruta("jueves", [_suc(1, "J1", 300), _suc(2, "J2", 200)])       # 500 kg -> 1 aux
    datos = leer_pdf(_pdf([("F 350_1", "XX", "CESAR", 4.0, [jueves, martes])]))
    dias = datos["vehiculos"][0]["dias"]
    assert [(d["dia"], d["apoyo"], len(d["paradas"])) for d in dias] == [
        ("MARTES", "2", 5), ("JUEVES", "1", 2)]


def test_varios_vehiculos_en_dos_columnas_y_paginas_conservan_el_orden():
    vehiculos = [_veh_simple(f"V{i:02d}", f"CHOFER{i}", n_paradas=8) for i in range(1, 13)]
    datos = leer_pdf(_pdf(vehiculos))
    assert [v["titulo"].split()[0] for v in datos["vehiculos"]] == [f"V{i:02d}" for i in range(1, 13)]
    assert all(len(v["dias"][0]["paradas"]) == 8 for v in datos["vehiculos"])


def test_pdf_sin_tablas_de_rutas_levanta_valueerror():
    with pytest.raises(ValueError, match="no contiene tablas"):
        leer_pdf(_pdf_sin_tablas())


# ── pdf_a_excel ───────────────────────────────────────────────
def _hoja(bytes_pdf):
    return openpyxl.load_workbook(io.BytesIO(pdf_a_excel(bytes_pdf))).active


def _fila_con(ws, texto, col=4):
    for r in range(1, ws.max_row + 1):
        if ws.cell(r, col).value == texto:
            return r
    raise AssertionError(f"no hay fila con {texto!r} en la columna {col}")


def test_excel_tiene_una_hoja_resumen_con_cabecera():
    ws = _hoja(_pdf([_veh_simple("F 350_1", "CESAR")]))
    assert ws.title == "RESUMEN"
    assert ws["A1"].value == "INTEGRADORA COMERCIAL DEL GOLFO"
    assert ws["A3"].value == "Expedido: 04/10/2026 10:00:00"
    assert ws["A5"].value == "F 350_1 · CESAR (4 ton)"


def test_excel_numeros_son_numeros_y_volumen_ausente_queda_vacio():
    ruta = _ruta("martes", [_suc(1, "Con vol", 1286, 11), _suc(2, "Sin vol", 150)], pct=40.0)
    ws = _hoja(_pdf([("F 350_1", "XX", "CESAR", 4.0, [ruta])], vol_map={11: 1.338}))
    r1, r2 = _fila_con(ws, "Con vol"), _fila_con(ws, "Sin vol")
    assert ws.cell(r1, 5).value == 1286 and isinstance(ws.cell(r1, 5).value, int)
    assert ws.cell(r1, 6).value == pytest.approx(1.338)
    assert ws.cell(r1, 7).value == pytest.approx(0.90)   # 1286/1436 = 89.5%; el PDF lo muestra como 90%
    assert ws.cell(r2, 6).value is None


def test_excel_bloques_apilados_verticalmente_con_fila_en_blanco():
    ws = _hoja(_pdf([_veh_simple("F 350_1", "CESAR"), _veh_simple("J 19", "VICTOR")]))
    r1 = next(r for r in range(1, ws.max_row + 1) if str(ws.cell(r, 1).value).startswith("F 350_1"))
    r2 = next(r for r in range(1, ws.max_row + 1) if str(ws.cell(r, 1).value).startswith("J 19"))
    assert r2 > r1
    assert all(ws.cell(r2 - 1, c).value is None for c in range(1, 8))   # fila en blanco


def test_excel_combina_dia_y_apoyo_y_los_pone_en_la_primera_fila_del_dia():
    ws = _hoja(_pdf([_veh_simple("F 350_1", "CESAR", n_paradas=4)]))
    primera = _fila_con(ws, "Sucursal F 350_1 1")
    assert ws.cell(primera, 1).value == "MARTES"
    assert ws.cell(primera, 2).value == 1          # 1000 kg, 4 suc -> 1 auxiliar
    rangos = {str(r) for r in ws.merged_cells.ranges}
    assert f"A{primera}:A{primera + 3}" in rangos
    assert f"B{primera}:B{primera + 3}" in rangos


def test_excel_mayorista_naranja_y_total_con_fondo_naranja():
    ruta = _ruta("lunes", [_suc(1, "Normal", 500)], [_may(2, "AA1_MAYORISTA", 50)])
    ws = _hoja(_pdf([("J 19", "XX", "VICTOR", 2.5, [ruta])]))
    rm, rn = _fila_con(ws, "AA1_MAYORISTA"), _fila_con(ws, "Normal")
    assert ws.cell(rm, 4).font.color.rgb.endswith("EA580C")
    assert not ws.cell(rn, 4).font.color.rgb.endswith("EA580C")
    total = next(r for r in range(1, ws.max_row + 1)
                 if str(ws.cell(r, 4).value or "").startswith("TOTAL"))
    assert ws.cell(total, 4).fill.start_color.rgb.endswith("FFEDD5")


def test_excel_total_sobre_100_por_ciento_en_rojo():
    ruta = _ruta("martes", [_suc(1, "Pesada", 5000)], pct=125.0)
    ws = _hoja(_pdf([("F 350_1", "XX", "CESAR", 4.0, [ruta])]))
    total = next(r for r in range(1, ws.max_row + 1)
                 if str(ws.cell(r, 4).value or "").startswith("TOTAL"))
    assert ws.cell(total, 7).value == pytest.approx(1.25)
    assert ws.cell(total, 7).font.color.rgb.endswith("C0392B")
```

- [ ] **Step 3: Correr los tests y verificar que fallan**

Run: `./env/Scripts/python.exe -m pytest tests/test_excel_logic.py -q`
Expected: error de colección `ModuleNotFoundError: No module named 'logic.excel_logic'`.

- [ ] **Step 4: Implementar `logic/excel_logic.py`**

Crear `logic/excel_logic.py` con exactamente este contenido:

```python
"""
logic/excel_logic.py
Convierte el PDF de reporte de pesos (generado por logic/pdf_logic.py) en un
Excel vertical: un bloque por vehículo, apilados uno debajo del otro.

El Excel es una reinterpretación del PDF YA generado: este módulo no consulta
la BD ni depende de pdf_logic. Si cambia el diseño del PDF (columnas, colores),
este lector debe ajustarse; tests/test_excel_logic.py genera el PDF con el
código real de pdf_logic y falla si dejan de calzar.
"""
import io
import re

import pdfplumber
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.properties import PageSetupProperties

# ── Lectura del PDF ───────────────────────────────────────────
NCOLS = 7                      # DIA, APOYO, SEC., PARADA, PESO, VOL, % RUTA
I_DIA, I_APOYO, I_SEC, I_PARADA, I_PESO, I_VOL, I_PCT = range(NCOLS)
ALTO_CABECERA_PDF = 58         # pt desde arriba: _draw_header dibuja hasta y=58
COLOR_MAYORISTA = (0xEA / 255, 0x58 / 255, 0x0C / 255)   # C_MAY_TEXT de pdf_logic
TOL_COLOR = 0.06
_VACIOS = {"", "—", "–", "-"}


def _color_cerca(color, objetivo) -> bool:
    if not color or len(color) != 3:
        return False
    return all(abs(a - b) <= TOL_COLOR for a, b in zip(color, objetivo))


def _txt(celda) -> str:
    return (celda or "").replace("\n", " ").strip()


def _num(texto: str):
    """'1,286' -> 1286.0 ; '0.843' -> 0.843 ; '—' -> None."""
    t = _txt(texto).replace(",", "")
    if t in _VACIOS:
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _pct(texto: str):
    """'28%' -> 0.28 ; '80.6%' -> 0.806 ; '—' -> None."""
    t = _txt(texto).replace("%", "")
    n = _num(t)
    return None if n is None else n / 100


def _es_mayorista(chars, bbox) -> bool:
    """True si el texto de la celda `bbox` está en naranja (C_MAY_TEXT)."""
    x0, top, x1, bottom = bbox
    en_celda = [c for c in chars
                if c["x0"] >= x0 - 1 and c["x1"] <= x1 + 1
                and c["top"] >= top - 1 and c["bottom"] <= bottom + 1
                and c["text"].strip()]
    return bool(en_celda) and all(
        _color_cerca(c.get("non_stroking_color"), COLOR_MAYORISTA) for c in en_celda)


def _filas_de_tabla(page, tabla) -> list:
    """Filas de datos de una tabla de vehículo (sin título ni encabezado)."""
    chars = page.chars
    filas = []
    for fila, celdas in list(zip(tabla.extract(), tabla.rows))[2:]:
        bbox_parada = celdas.cells[I_PARADA]
        filas.append({
            "dia":       _txt(fila[I_DIA]),
            "apoyo":     _txt(fila[I_APOYO]),
            "sec":       _txt(fila[I_SEC]),
            "parada":    _txt(fila[I_PARADA]),
            "peso":      _num(fila[I_PESO]),
            "vol":       _num(fila[I_VOL]),
            "pct":       _pct(fila[I_PCT]),
            "mayorista": bool(bbox_parada) and _es_mayorista(chars, bbox_parada),
        })
    return filas


def _agrupar_dias(filas: list) -> list:
    """Parte las filas en días: cada día termina en su fila 'TOTAL ...'."""
    dias, grupo = [], []
    for f in filas:
        grupo.append(f)
        if f["parada"].upper().startswith("TOTAL"):
            paradas, total = grupo[:-1], grupo[-1]
            dia = next((g["dia"] for g in paradas if g["dia"]), "")
            if not dia:
                m = re.match(r"TOTAL\s+(\S+)", total["parada"], re.I)
                dia = m.group(1) if m else ""
            apoyo = next((g["apoyo"] for g in paradas if g["apoyo"]), "")
            dias.append({"dia": dia, "apoyo": apoyo, "paradas": paradas, "total": total})
            grupo = []
    return dias


def leer_pdf(bytes_pdf: bytes) -> dict:
    """
    -> {"cabecera": [líneas], "vehiculos": [{"titulo": str, "dias": [...]}]}
    Los vehículos salen en el orden de lectura del PDF (por página: columna
    izquierda de arriba abajo y luego la derecha).
    """
    with pdfplumber.open(io.BytesIO(bytes_pdf)) as pdf:
        if not pdf.pages:
            raise ValueError("El PDF no contiene tablas de rutas.")
        cab = pdf.pages[0].crop((0, 0, pdf.pages[0].width, ALTO_CABECERA_PDF))
        cabecera = [l.strip() for l in (cab.extract_text() or "").splitlines() if l.strip()]

        bloques = []   # [{"titulo", "filas"}], una entrada por vehículo
        for page in pdf.pages:
            tablas = [t for t in page.find_tables()
                      if len(t.extract()) >= 3 and len(t.extract()[1]) == NCOLS]
            tablas.sort(key=lambda t: (0 if t.bbox[0] < page.width / 2 else 1, t.bbox[1]))
            for t in tablas:
                titulo = _txt(t.extract()[0][0])
                filas = _filas_de_tabla(page, t)
                # Continuación: reportlab repite título+encabezado si una
                # tabla se parte entre columnas/páginas.
                if bloques and bloques[-1]["titulo"] == titulo:
                    bloques[-1]["filas"].extend(filas)
                else:
                    bloques.append({"titulo": titulo, "filas": filas})

    if not bloques:
        raise ValueError("El PDF no contiene tablas de rutas.")
    return {
        "cabecera": cabecera,
        "vehiculos": [{"titulo": b["titulo"], "dias": _agrupar_dias(b["filas"])}
                      for b in bloques],
    }


# ── Escritura del Excel ───────────────────────────────────────
ANCHOS = [11, 8, 7, 46, 11, 10, 10]
AZUL, AZUL_CLARO, AZUL_DIA, AZUL_TOTAL = "1565C0", "E3F2FD", "F5F9FF", "BBDEFB"
NARANJA, NARANJA_FONDO, NARANJA_TOTAL = "EA580C", "FFF7ED", "FFEDD5"
ROJO = "C0392B"
_LADO = Side(style="thin", color="B0BEC5")
BORDE = Border(left=_LADO, right=_LADO, top=_LADO, bottom=_LADO)


def _fill(hex_):
    return PatternFill("solid", start_color=hex_, end_color=hex_)


def _entero_o_texto(valor: str):
    """APOYO '2' -> 2 ; '—' -> '—'."""
    return int(valor) if valor.isdigit() else valor


def _escribir_bloque(ws, fila: int, veh: dict) -> int:
    """Escribe un vehículo desde `fila`; devuelve la siguiente fila libre."""
    ws.merge_cells(start_row=fila, start_column=1, end_row=fila, end_column=NCOLS)
    c = ws.cell(fila, 1, veh["titulo"])
    c.font = Font(bold=True, color="FFFFFF", size=11)
    c.fill = _fill(AZUL)
    c.alignment = Alignment(horizontal="center", vertical="center")
    for col in range(1, NCOLS + 1):
        ws.cell(fila, col).border = BORDE
        ws.cell(fila, col).fill = _fill(AZUL)
    fila += 1

    for col, etiqueta in enumerate(
            ["DIA", "APOYO", "SEC.", "PARADA", "PESO (KG)", "VOL (m³)", "% RUTA"], 1):
        c = ws.cell(fila, col, etiqueta)
        c.font = Font(bold=True, color=AZUL, size=9)
        c.fill = _fill(AZUL_CLARO)
        c.border = BORDE
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    fila += 1

    for dia in veh["dias"]:
        inicio = fila
        for p in dia["paradas"]:
            _escribir_parada(ws, fila, p)
            fila += 1
        if fila - 1 > inicio:
            ws.merge_cells(start_row=inicio, start_column=1, end_row=fila - 1, end_column=1)
            ws.merge_cells(start_row=inicio, start_column=2, end_row=fila - 1, end_column=2)
        ws.cell(inicio, 1, dia["dia"])
        ws.cell(inicio, 2, _entero_o_texto(dia["apoyo"]))
        for col in (1, 2):
            c = ws.cell(inicio, col)
            c.font = Font(bold=True, size=9)
            c.alignment = Alignment(horizontal="center", vertical="center")
        _escribir_total(ws, fila, dia["total"])
        fila += 1
    return fila


def _escribir_parada(ws, fila: int, p: dict) -> None:
    color = NARANJA if p["mayorista"] else "000000"
    for col in range(1, NCOLS + 1):
        c = ws.cell(fila, col)
        c.border = BORDE
        c.fill = _fill(NARANJA_FONDO if p["mayorista"] else
                       (AZUL_DIA if col <= I_APOYO + 1 else "FFFFFF"))
    ws.cell(fila, I_SEC + 1, _entero_o_texto(p["sec"]))
    ws.cell(fila, I_PARADA + 1, p["parada"])
    _numero(ws, fila, I_PESO + 1, p["peso"], "#,##0")
    _numero(ws, fila, I_VOL + 1, p["vol"], "0.000")
    _numero(ws, fila, I_PCT + 1, p["pct"], "0%")
    for col in range(I_SEC + 1, NCOLS + 1):
        c = ws.cell(fila, col)
        c.font = Font(size=9, color=color, bold=p["mayorista"])
        c.alignment = Alignment(
            horizontal="left" if col == I_PARADA + 1 else
                       "center" if col in (I_SEC + 1, I_PCT + 1) else "right",
            vertical="center")


def _escribir_total(ws, fila: int, t: dict) -> None:
    con_may = "may." in t["parada"]
    fondo = NARANJA_TOTAL if con_may else AZUL_TOTAL
    for col in range(1, NCOLS + 1):
        c = ws.cell(fila, col)
        c.fill = _fill(fondo)
        c.border = BORDE
        c.font = Font(bold=True, size=9)
    ws.cell(fila, I_PARADA + 1, t["parada"]).alignment = Alignment(horizontal="left")
    _numero(ws, fila, I_PESO + 1, t["peso"], "#,##0")
    _numero(ws, fila, I_VOL + 1, t["vol"], "0.000")
    _numero(ws, fila, I_PCT + 1, t["pct"], "0.0%")
    for col in (I_PESO + 1, I_VOL + 1):
        ws.cell(fila, col).alignment = Alignment(horizontal="right")
    pct = ws.cell(fila, I_PCT + 1)
    pct.alignment = Alignment(horizontal="center")
    pct.font = Font(bold=True, size=9,
                    color=ROJO if (t["pct"] or 0) > 1 else AZUL)


def _numero(ws, fila: int, col: int, valor, formato: str) -> None:
    c = ws.cell(fila, col, None if valor is None else
                (int(valor) if formato == "#,##0" else valor))
    c.number_format = formato


def pdf_a_excel(bytes_pdf: bytes) -> bytes:
    """PDF de reporte de pesos -> bytes de un .xlsx vertical (hoja RESUMEN)."""
    datos = leer_pdf(bytes_pdf)

    wb = Workbook()
    ws = wb.active
    ws.title = "RESUMEN"
    for i, ancho in enumerate(ANCHOS, 1):
        ws.column_dimensions[get_column_letter(i)].width = ancho

    fila = 1
    for i, linea in enumerate(datos["cabecera"]):
        ws.merge_cells(start_row=fila, start_column=1, end_row=fila, end_column=NCOLS)
        c = ws.cell(fila, 1, linea)
        c.alignment = Alignment(horizontal="center")
        c.font = (Font(bold=True, size=14) if i == 0 else
                  Font(color="808080", size=9) if linea.startswith("Expedido") else
                  Font(size=10))
        fila += 1
    fila += 1

    for veh in datos["vehiculos"]:
        fila = _escribir_bloque(ws, fila, veh) + 1   # una fila en blanco entre bloques

    ws.page_setup.orientation = "portrait"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)

    salida = io.BytesIO()
    wb.save(salida)
    return salida.getvalue()
```

- [ ] **Step 5: Correr los tests y verificar que pasan**

Run: `./env/Scripts/python.exe -m pytest tests/test_excel_logic.py -q`
Expected: `15 passed`.

Nota para quien implementa: el % de cada parada sale **redondeado a entero** porque así lo imprime el PDF (`f"{pct_r:.0f}%"`); el % de la fila TOTAL sale con 1 decimal. El Excel conserva exactamente lo que muestra el PDF. Los tests lo reflejan (0.61, 0.90).

- [ ] **Step 6: Confirmar que los tests existentes del PDF siguen pasando**

Run: `./env/Scripts/python.exe -m pytest tests/test_pdf_logic.py tests/test_pdf_snapshot_coherente.py -q`
Expected: todos pasan (no se tocó `pdf_logic.py`).

- [ ] **Step 7: Commit**

```bash
git add requirements.txt logic/excel_logic.py tests/test_excel_logic.py
git commit -m "feat: convierte el PDF de reporte de pesos a Excel vertical (excel_logic)" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Endpoint `POST /pdf/generar-excel`

**Files:**
- Modify: `router/pdf_router.py` (imports, docstring, y un endpoint nuevo después de `generar()`)
- Create: `tests/test_pdf_router_excel.py`

- [ ] **Step 1: Escribir los tests del endpoint (fallan: la ruta no existe)**

Crear `tests/test_pdf_router_excel.py`:

```python
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
```

- [ ] **Step 2: Correr los tests y verificar que fallan**

Run: `./env/Scripts/python.exe -m pytest tests/test_pdf_router_excel.py -q`
Expected: los 6 fallan (404 en vez de 400/422/200, y `AttributeError` por `MAX_PDF_BYTES` en el de tamaño).

- [ ] **Step 3: Implementar el endpoint**

En `router/pdf_router.py`:

1. En el docstring del módulo, agregar la línea nueva después de la de `/generar`:

   Reemplazar
   ```
     POST /generar                → Genera y descarga el reporte PDF de la logística activa
   ```
   por
   ```
     POST /generar                → Genera y descarga el reporte PDF de la logística activa
     POST /generar-excel          → Convierte el PDF generado (enviado por el navegador) a Excel
   ```

2. Reemplazar los imports
   ```python
   from flask import Blueprint, render_template, send_file, jsonify, session, redirect, url_for
   from logic.pdf_logic import generar_pdf, SnapshotDesactualizado
   ```
   por
   ```python
   from io import BytesIO

   from flask import Blueprint, render_template, request, send_file, jsonify, session, redirect, url_for
   from logic.pdf_logic import generar_pdf, SnapshotDesactualizado
   from logic.excel_logic import pdf_a_excel
   ```

3. Reemplazar
   ```python
   pdf_bp = Blueprint('pdf', __name__)
   ```
   por
   ```python
   pdf_bp = Blueprint('pdf', __name__)

   MAX_PDF_BYTES = 10 * 1024 * 1024   # el PDF real pesa ~15 KB; 10 MB es holgura de sobra
   MIMETYPE_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
   ```

4. Insertar este endpoint justo después de la función `generar()` (antes de `@pdf_bp.route('/estado-autorizacion'...)`):

   ```python
   @pdf_bp.route('/generar-excel', methods=['POST'])
   def generar_excel():
       """
       Convierte a Excel el PDF que el navegador ya generó y tiene en la vista
       previa (campo multipart `pdf`). El Excel sale del mismo PDF que ve el
       usuario; no se consulta la BD ni se guarda nada en el servidor.
       """
       logistica = _logistica_activa()
       if not logistica:
           return jsonify({"status": "error", "mensaje": "No hay ninguna logística activa."}), 400

       archivo = request.files.get("pdf")
       if archivo is None:
           return jsonify({"status": "error", "mensaje": "No se recibió el PDF."}), 400

       contenido = archivo.read(MAX_PDF_BYTES + 1)
       if len(contenido) > MAX_PDF_BYTES:
           return jsonify({"status": "error", "mensaje": "El PDF es demasiado grande."}), 400
       if not contenido.startswith(b"%PDF"):
           return jsonify({"status": "error", "mensaje": "El archivo recibido no es un PDF."}), 400

       try:
           xlsx = pdf_a_excel(contenido)
       except ValueError as e:
           return jsonify({"status": "error", "mensaje": str(e)}), 422
       except Exception as e:
           return jsonify({"status": "error", "mensaje": f"Error al generar el Excel: {e}"}), 500

       return send_file(
           BytesIO(xlsx),
           as_attachment=True,
           download_name=f"{logistica['nombre'].replace(' ', '_')}.xlsx",
           mimetype=MIMETYPE_XLSX,
       )
   ```

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run: `./env/Scripts/python.exe -m pytest tests/test_pdf_router_excel.py tests/test_excel_logic.py -q`
Expected: `21 passed`.

- [ ] **Step 5: Commit**

```bash
git add router/pdf_router.py tests/test_pdf_router_excel.py
git commit -m "feat: endpoint POST /pdf/generar-excel que convierte el PDF generado a Excel" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Botón y descarga en la interfaz

**Files:**
- Modify: `templates/pdf/index.html`
- Modify: `static/js/pdf.js`

No hay framework de tests de JS en el proyecto; la verificación es manual (Step 4).

- [ ] **Step 1: Agregar el botón en `templates/pdf/index.html`**

Reemplazar
```html
            <button id="btn-descargar" class="btn btn-descargar" disabled>
                <i data-lucide="download" class="pdf-icon" aria-hidden="true"></i>
                <span>Descargar PDF</span>
            </button>
```
por
```html
            <button id="btn-descargar" class="btn btn-descargar" disabled>
                <i data-lucide="download" class="pdf-icon" aria-hidden="true"></i>
                <span>Descargar PDF</span>
            </button>
            <!-- Aparece solo cuando ya hay un PDF generado (ver generarPDF en pdf.js) -->
            <button id="btn-excel" class="btn btn-descargar" style="display:none;">
                <i data-lucide="file-spreadsheet" class="pdf-icon" aria-hidden="true"></i>
                <span>Descargar Excel</span>
            </button>
```

- [ ] **Step 2: Mensajes del loader y estado en `static/js/pdf.js`**

2a. Reemplazar
```js
    "Finalizando el reporte…",
  ],
};
```
por
```js
    "Finalizando el reporte…",
  ],
  excel: [
    "Generando Excel…",
    "Leyendo las tablas del PDF…",
    "Armando los bloques por vehículo…",
    "Aplicando formato al Excel…",
  ],
};
```

2b. Reemplazar
```js
let _blobUrl   = null;
let _filename  = "reporte_pesos.pdf";
```
por
```js
let _blobUrl   = null;
let _blob      = null;   // el mismo PDF, para reenviarlo al servidor y convertirlo a Excel
let _filename  = "reporte_pesos.pdf";
```

2c. En `inicializar()`, reemplazar
```js
  document.getElementById('btn-descargar')?.addEventListener('click', descargarPDF);
```
por
```js
  document.getElementById('btn-descargar')?.addEventListener('click', descargarPDF);
  document.getElementById('btn-excel')?.addEventListener('click', descargarExcel);
```

- [ ] **Step 3: Cambios en `generarPDF()` y función nueva `descargarExcel()`**

3a. En `generarPDF()`, reemplazar
```js
  const btnDesc     = document.getElementById('btn-descargar');
  const errDiv      = document.getElementById('mensaje-error');
```
por
```js
  const btnDesc     = document.getElementById('btn-descargar');
  const btnExcel    = document.getElementById('btn-excel');
  const errDiv      = document.getElementById('mensaje-error');
```

3b. Reemplazar
```js
  zonaPreview.style.display = 'none';

  // Liberar blob URL previo para no acumular memoria
```
por
```js
  zonaPreview.style.display = 'none';
  btnExcel.style.display    = 'none';   // el Excel solo existe a partir de un PDF ya generado
  _blob = null;

  // Liberar blob URL previo para no acumular memoria
```

3c. Reemplazar
```js
    const blob = await res.blob();
    _blobUrl   = URL.createObjectURL(blob);
```
por
```js
    const blob = await res.blob();
    _blob      = blob;
    _blobUrl   = URL.createObjectURL(blob);
```

3d. Reemplazar
```js
    // Habilitar descarga
    btnDesc.disabled = false;
```
por
```js
    // Habilitar descarga
    btnDesc.disabled = false;
    btnExcel.style.display = '';
```

3e. Insertar esta función completa justo después de `descargarPDF()` (antes del comentario `// ── Autorización de rutas`):

```js
// ── Descargar el Excel (el servidor convierte el PDF ya generado) ──
async function descargarExcel() {
  if (!_blob) return;
  const btn    = document.getElementById('btn-excel');
  const errDiv = document.getElementById('mensaje-error');

  errDiv.style.display = 'none';
  errDiv.textContent   = '';
  btn.disabled = true;
  Loader.show('Generando Excel', MSG_PDF.excel);

  try {
    const form = new FormData();
    form.append('pdf', _blob, _filename);
    const res = await fetch('/pdf/generar-excel', { method: 'POST', body: form });

    if (!res.ok) {
      let mensaje = `Error ${res.status}`;
      try { const json = await res.json(); mensaje = json.mensaje ?? mensaje; } catch (_) {}
      throw new Error(mensaje);
    }

    const disposition = res.headers.get('Content-Disposition') ?? '';
    const match       = disposition.match(/filename\*?=(?:UTF-8'')?["']?([^"';\n]+)/i);
    const nombre      = match ? decodeURIComponent(match[1])
                              : _filename.replace(/\.pdf$/i, '.xlsx');

    const urlXlsx = URL.createObjectURL(await res.blob());
    const a       = document.createElement('a');
    a.href        = urlXlsx;
    a.download    = nombre;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(urlXlsx);
  } catch (err) {
    errDiv.innerHTML = '<i data-lucide="circle-x" class="pdf-icon" aria-hidden="true"></i><span></span>';
    errDiv.querySelector('span').textContent = err.message;
    if (window.lucide?.createIcons) {
      window.lucide.createIcons({ attrs: { class: 'pdf-icon' } });
    }
    errDiv.style.display = '';
    console.error('Error al generar Excel:', err);
  } finally {
    Loader.hide();
    btn.disabled = false;
  }
}
```

- [ ] **Step 4: Verificación manual en el navegador**

1. Arrancar la app como siempre (`python app.py`), entrar a `127.0.0.1:5000/pdf/<slug-de-una-logística>`.
2. Antes de generar: **no** se ve el botón *Descargar Excel*.
3. Presionar *Generar PDF*: aparece la vista previa y, junto a *Descargar PDF*, el botón *Descargar Excel*.
4. Presionar *Descargar Excel*: se descarga `<Nombre_logística>.xlsx`. Abrirlo y comprobar: bloques por vehículo apilados, DIA/APOYO combinados, totales por día, mayoristas en naranja, números alineados y sumables.
5. Presionar *Generar PDF* otra vez: el botón de Excel desaparece mientras carga y reaparece al terminar.

Si no se puede abrir el navegador, indicarlo explícitamente en el reporte en vez de afirmar que quedó verificado.

- [ ] **Step 5: Commit**

```bash
git add templates/pdf/index.html static/js/pdf.js
git commit -m "feat: boton Descargar Excel junto a Descargar PDF (aparece tras generar el PDF)" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: README y verificación final

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Actualizar `README.md`**

1a. Reemplazar la fila
```
| PDF | `router/pdf_router.py` | Genera el reporte de reparto y autoriza/retira autorización hacia conductores |
```
por
```
| PDF | `router/pdf_router.py` | Genera el reporte de reparto (PDF), lo convierte a Excel vertical (`POST /pdf/generar-excel`, a partir del PDF ya generado) y autoriza/retira autorización hacia conductores |
```

1b. Reemplazar
```
- **Reportes**: ReportLab (PDF), openpyxl/pandas (Excel).
```
por
```
- **Reportes**: ReportLab (PDF), openpyxl/pandas (Excel). El Excel de la logística
  (`logic/excel_logic.py`) se arma **leyendo el PDF ya generado** con `pdfplumber`: un bloque
  por vehículo apilados verticalmente, con los mismos totales por día y los mayoristas en naranja.
  Si cambia el diseño del PDF (columnas o colores), hay que ajustar ese lector;
  `tests/test_excel_logic.py` genera el PDF con el código real de `pdf_logic` y falla si dejan de calzar.
```

- [ ] **Step 2: Suite de pruebas relacionada completa**

Run: `./env/Scripts/python.exe -m pytest tests/test_excel_logic.py tests/test_pdf_router_excel.py tests/test_pdf_logic.py tests/test_pdf_snapshot_coherente.py -q`
Expected: todos pasan (`21 passed` de los dos nuevos + los existentes).

- [ ] **Step 3: Verificar que el PDF no se tocó**

Run: `git diff --stat 7640b27 -- logic/pdf_logic.py`
Expected: salida vacía (sin cambios en `pdf_logic.py`).

- [ ] **Step 4: Probar con un PDF real**

Run:
```bash
./env/Scripts/python.exe -c "
from logic.excel_logic import pdf_a_excel
d = open(r'C:/Users/carli/Downloads/Logistica_del_5_al_9_de_octubre_del_2026.pdf','rb').read()
open(r'C:/Users/carli/Downloads/prueba_excel_logistica.xlsx','wb').write(pdf_a_excel(d))
print('ok')"
```
Expected: `ok`. Abrir `prueba_excel_logistica.xlsx`: 9 vehículos (F 350_1, F 350_2, F 350_3, J 19, K 16, T 17_2, T 20, T 23, T 25) y totales que coinciden con el PDF (p. ej. F 350_1 · MARTES = 3,034 kg, 77.8 %). Borrar ese archivo de prueba después.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: README documenta la exportacion a Excel desde el PDF" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```
