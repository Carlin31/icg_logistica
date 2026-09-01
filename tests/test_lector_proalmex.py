import io

import pandas as pd

from logic.logic_extraccion.lector_proalmex import LectorProalmex


def _excel_bytes(headers: list, filas: list) -> io.BytesIO:
    """Construye un Excel en memoria con una fila en blanco antes del header
    (lector_proalmex.py usa header=1, es decir, la SEGUNDA fila del Excel
    trae los encabezados reales -- la primera es un título/blank)."""
    buf = io.BytesIO()
    fila_blanco = [""] * len(headers)
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        pd.DataFrame([fila_blanco, headers] + filas).to_excel(writer, index=False, header=False)
    buf.seek(0)
    return buf


def test_excluye_inv_bodega_con_en_en_medio():
    # Caso real que se coló: 'INV. EN BODEGA' (con 'EN') no es un sufijo
    # exacto de 'INV. BODEGA', así que un match por sufijo estricto no lo
    # detecta -- debe excluirse igual que 'INV. BODEGA'.
    headers = ["", "Descripción", "Tamaño", "Tux 18 Marzo", "TOTAL CAJAS", "IMPORTE",
               "INV. EN BODEGA", "INV. DISPONIBLE"]
    filas = [["", "Producto A", "Chico", 5, 5, 100.0, 569, 498]]
    archivo = _excel_bytes(headers, filas)

    df = LectorProalmex.leer_y_normalizar(archivo)

    assert "INV. EN BODEGA" not in df["Sucursal"].values
    assert "Tux 18 Marzo" in df["Sucursal"].values


def test_sigue_excluyendo_inv_bodega_sin_en():
    # No regresionar el caso ya cubierto antes de este fix.
    headers = ["", "Descripción", "Tamaño", "Tux Centro", "INV. BODEGA"]
    filas = [["", "Producto A", "Chico", 5, 569]]
    archivo = _excel_bytes(headers, filas)

    df = LectorProalmex.leer_y_normalizar(archivo)

    assert "INV. BODEGA" not in df["Sucursal"].values
    assert "Tux Centro" in df["Sucursal"].values


def test_no_excluye_sucursal_real_por_error():
    headers = ["", "Descripción", "Tamaño", "Tux Centro", "Jardines"]
    filas = [["", "Producto A", "Chico", 5, 3]]
    archivo = _excel_bytes(headers, filas)

    df = LectorProalmex.leer_y_normalizar(archivo)

    assert set(df["Sucursal"].values) == {"Tux Centro", "Jardines"}


# ═══════════════════════════════════════════════════════════════════════════
# `sucursales_validas` -- lista blanca en vez de lista negra. Hallado en
# producción 2026-08-12 (mismo día, esta vez en ICG): columnas de resumen
# del Excel se colaban como sucursales fantasma porque su nombre no calzaba
# EXACTO ninguna entrada de la lista de exclusión -- el mismo patrón que ya
# había pasado con 'INV. EN BODEGA' arriba. Una lista blanca no depende de
# adivinar cada variante nueva.
# ═══════════════════════════════════════════════════════════════════════════

def test_lista_blanca_excluye_columna_de_resumen_que_la_lista_negra_no_cubre():
    headers = ["", "Descripción", "Tamaño", "Tux Centro", "Venta Lores Piezas"]
    filas = [["", "Producto A", "Chico", 5, 168]]
    archivo = _excel_bytes(headers, filas)
    sucursales_validas = {"tux centro"}

    df = LectorProalmex.leer_y_normalizar(archivo, sucursales_validas=sucursales_validas)

    assert set(df["Sucursal"].values) == {"Tux Centro"}


def test_lista_blanca_no_excluye_sucursal_real_por_error():
    headers = ["", "Descripción", "Tamaño", "Tux Centro", "Jardines"]
    filas = [["", "Producto A", "Chico", 5, 3]]
    archivo = _excel_bytes(headers, filas)
    sucursales_validas = {"tux centro", "jardines"}

    df = LectorProalmex.leer_y_normalizar(archivo, sucursales_validas=sucursales_validas)

    assert set(df["Sucursal"].values) == {"Tux Centro", "Jardines"}
