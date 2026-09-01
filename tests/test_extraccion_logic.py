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
