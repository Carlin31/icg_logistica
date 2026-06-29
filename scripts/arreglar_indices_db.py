"""
scripts/arreglar_indices_db.py
Script de mantenimiento único para recrear índices parciales en MongoDB.

Uso:
    python scripts/arreglar_indices_db.py

Ejecutar solo cuando se necesite migrar índices estrictos a índices parciales
(evita errores de unicidad en documentos sin el campo indexado).
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from db import get_db


def main():
    db = get_db()
    resultado = []

    # ── Productos ──────────────────────────────────────────────────────────────
    try:
        db.productos.drop_index("clave_sae_1")
        resultado.append("OK  Índice estricto de productos eliminado.")
    except Exception as e:
        resultado.append(f"SKIP No se pudo eliminar índice de productos: {e}")

    try:
        db.productos.create_index(
            "clave_sae",
            unique=True,
            partialFilterExpression={"clave_sae": {"$type": "number"}},
        )
        resultado.append("OK  Índice parcial de productos (clave_sae) creado.")
    except Exception as e:
        resultado.append(f"ERR Error al crear índice de productos: {e}")

    # ── Sucursales ─────────────────────────────────────────────────────────────
    try:
        db.sucursales.drop_index("num_tienda_1")
        resultado.append("OK  Índice estricto de sucursales eliminado.")
    except Exception as e:
        resultado.append(f"SKIP No se pudo eliminar índice de sucursales: {e}")

    try:
        db.sucursales.create_index(
            "num_tienda",
            unique=True,
            partialFilterExpression={"num_tienda": {"$type": "number"}},
        )
        resultado.append("OK  Índice parcial de sucursales creado.")
    except Exception as e:
        resultado.append(f"ERR Error al crear índice de sucursales: {e}")

    for linea in resultado:
        print(linea)


if __name__ == "__main__":
    main()
