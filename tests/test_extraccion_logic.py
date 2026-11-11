"""
tests/test_extraccion_logic.py

`_mapa_nombre_sucursal` — construye el mapa nombre-de-columna-del-Excel →
nombre_base, case-insensitive. Hallado en producción 2026-08-12: el mapa de
ICG/Proalmex se armaba sensible a mayúsculas (sin `.lower()`, a diferencia
del de Bimbo, que sí lo hacía bien) — 56 de 101 sucursales tienen
`nombre_icg-proalmex` guardado en minúsculas en la BD, mientras los archivos
Excel usan Type Case en los encabezados de columna ('Cabada', 'Acatlan',
'Anton Lizardo'...). La traducción fallaba en silencio, dejando el nombre
crudo del Excel, que casi nunca coincide con `nombre_base` — de ahí el
"N/A" en la columna ID SUCURSAL. Puro: sin BD.
"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from logic.extraccion_logic import _mapa_nombre_sucursal


def test_mapa_ignora_mayusculas_entre_excel_y_bd():
    sucursales_db = [
        {"nombre_icg-proalmex": "cabada", "nombre_base": "Ángel R. Cabada"},
    ]
    mapa = _mapa_nombre_sucursal(sucursales_db, "nombre_icg-proalmex")
    assert mapa.get("cabada") == "Ángel R. Cabada"
    assert mapa.get("Cabada".lower()) == "Ángel R. Cabada"


def test_mapa_ignora_espacios_al_inicio_y_final():
    sucursales_db = [{"nombre_icg-proalmex": "  cardel  ", "nombre_base": "Jose Cardel"}]
    mapa = _mapa_nombre_sucursal(sucursales_db, "nombre_icg-proalmex")
    assert mapa == {"cardel": "Jose Cardel"}


def test_mapa_ignora_sucursales_sin_el_campo():
    sucursales_db = [
        {"nombre_icg-proalmex": "", "nombre_base": "Sin Alias"},
        {"nombre_base": "Sin Campo"},
        {"nombre_icg-proalmex": "acatlan", "nombre_base": "Acatlán de Pérez Figueroa"},
    ]
    mapa = _mapa_nombre_sucursal(sucursales_db, "nombre_icg-proalmex")
    assert mapa == {"acatlan": "Acatlán de Pérez Figueroa"}


def test_mapa_funciona_para_el_campo_bimbo_tambien():
    sucursales_db = [{"nombre_bimbo": "Anton", "nombre_base": "Antón Lizardo"}]
    mapa = _mapa_nombre_sucursal(sucursales_db, "nombre_bimbo")
    assert mapa == {"anton": "Antón Lizardo"}


# ── Clave (línea, tamaño) de Proalmex ────────────────────────────────────────
# Hallado en producción 2026-10-05: la BD guardaba el tamaño como '1,890 Ml'
# (con coma de miles) y el Excel de Proalmex lo traía como '1890 Ml'. La clave
# (línea, tamaño) no coincidía y Horchata de Arroz/Coco 1890 Ml salían "no
# encontradas" -> 0 kg y 0 m³ en la extracción.
import io

import pandas as pd

from logic.extraccion_logic import _clave_proalmex, procesar_archivos_extraccion


def test_clave_proalmex_ignora_coma_de_miles_en_tamano():
    assert _clave_proalmex("Horchata De Arroz", "1,890 Ml") == _clave_proalmex("Horchata De Arroz", "1890 Ml")


def test_clave_proalmex_ignora_mayusculas_y_espacios():
    assert _clave_proalmex("  Horchata De Arroz ", " 700 ML ") == ("horchata de arroz", "700 ml")


def test_clave_proalmex_respeta_punto_decimal():
    assert _clave_proalmex("Horchata De Arroz", "3.785 Lt") == ("horchata de arroz", "3.785 lt")


def test_clave_proalmex_no_toca_coma_decimal():
    # '1,5 Lt' es 1.5 litros, no 15: solo se quita la coma de miles (3 dígitos).
    assert _clave_proalmex("Jugo", "1,5 Lt") == ("jugo", "1,5 lt")


def test_clave_proalmex_tamano_vacio():
    assert _clave_proalmex("Producto A", None) == ("producto a", "")


def test_extraccion_proalmex_cruza_producto_con_coma_en_bd(monkeypatch):
    import logic.extraccion_logic as ex

    monkeypatch.setattr(ex, "listar_productos", lambda: [{"clave_sae": 1, "peso": 1, "largo": 1, "ancho": 1, "alto": 1}])
    monkeypatch.setattr(ex, "listar_productos_bimbo", lambda: [])
    monkeypatch.setattr(ex, "listar_productos_proalmex", lambda: [
        {"linea": "Horchata De Arroz", "tamano": "1,890 Ml", "peso": 15.44, "volumen": 0.0192},
    ])
    monkeypatch.setattr(ex, "listar_sucursales", lambda: [
        {"nombre_base": "Tiendas Lores Alvarado", "nombre_icg-proalmex": "alvarado", "num_tienda": 21},
    ])

    buf = io.BytesIO()
    filas = [["", "Descripción", "Tamaño", "Alvarado"], ["", "Horchata De Arroz ", "1890 Ml", 2]]
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        pd.DataFrame([["", "", "", ""]] + filas).to_excel(w, index=False, header=False)
    buf.seek(0)

    res = procesar_archivos_extraccion({"proalmex": buf})

    assert res["status"] == "ok"
    assert res["advertencias"]["claves_no_encontradas_proalmex"] == []
    # 2 cajas x 15.44 kg = 30.88; calcular_peso redondea a entero
    assert res["data"]["Tiendas Lores Alvarado"]["total_kg"] == 31
