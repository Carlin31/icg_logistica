"""
tests/test_lector_icg.py

`sucursales_validas` -- filtro por lista blanca: una columna sólo se trata
como sucursal si su nombre (sin mayúsculas) está en el catálogo real de
tiendas. Hallado en producción 2026-08-12: columnas de resumen del Excel
('Piezas Pedido', 'Venta Lores Piezas') se colaban como sucursales
fantasma con peso real (684 kg y 1,329 kg) porque su nombre no coincidía
EXACTO con ninguna entrada de `COLUMNAS_IGNORAR` -- la lista negra de
exclusión ya había fallado así antes con Proalmex ('INV. EN BODEGA' vs
'INV. BODEGA', ver test_lector_proalmex.py). Una lista blanca no depende
de adivinar cada variante nueva del Excel.
"""
import io

import pandas as pd

from logic.logic_extraccion.lector_icg import LectorICG


def _excel_bytes(headers: list, filas: list) -> io.BytesIO:
    """Excel en memoria con una fila en blanco antes del header (lector_icg.py
    usa header=1: la segunda fila del Excel trae los encabezados reales)."""
    buf = io.BytesIO()
    fila_blanco = [""] * len(headers)
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        pd.DataFrame([fila_blanco, headers] + filas).to_excel(writer, index=False, header=False)
    buf.seek(0)
    return buf


def test_sin_lista_blanca_columna_de_resumen_se_cuela_como_sucursal():
    # Comportamiento previo (documentado, no deseado): sin sucursales_validas
    # 'Venta Lores Piezas' no calza ninguna entrada de COLUMNAS_IGNORAR y
    # entra como si fuera una tienda más.
    headers = ["CLAVE SAE", "Tux Centro", "Venta Lores Piezas"]
    filas = [[123, 5, 168]]
    archivo = _excel_bytes(headers, filas)

    df = LectorICG.leer_y_normalizar(archivo)

    assert "Venta Lores Piezas" in df["Sucursal"].values


def test_lista_blanca_excluye_columna_de_resumen_que_la_lista_negra_no_cubre():
    headers = ["CLAVE SAE", "Tux Centro", "Venta Lores Piezas", "Piezas Pedido"]
    filas = [[123, 5, 168, 120]]
    archivo = _excel_bytes(headers, filas)
    sucursales_validas = {"tux centro"}

    df = LectorICG.leer_y_normalizar(archivo, sucursales_validas=sucursales_validas)

    assert set(df["Sucursal"].values) == {"Tux Centro"}


def test_lista_blanca_no_excluye_sucursal_real_por_error():
    headers = ["CLAVE SAE", "Tux Centro", "Jardines"]
    filas = [[123, 5, 3]]
    archivo = _excel_bytes(headers, filas)
    sucursales_validas = {"tux centro", "jardines"}

    df = LectorICG.leer_y_normalizar(archivo, sucursales_validas=sucursales_validas)

    assert set(df["Sucursal"].values) == {"Tux Centro", "Jardines"}


def test_lista_blanca_ignora_mayusculas():
    headers = ["CLAVE SAE", "TUX CENTRO"]
    filas = [[123, 5]]
    archivo = _excel_bytes(headers, filas)
    sucursales_validas = {"tux centro"}

    df = LectorICG.leer_y_normalizar(archivo, sucursales_validas=sucursales_validas)

    assert set(df["Sucursal"].values) == {"TUX CENTRO"}
