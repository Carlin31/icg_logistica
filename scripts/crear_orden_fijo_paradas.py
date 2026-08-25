"""
crear_orden_fijo_paradas.py

Script de un solo uso (idempotente): crea la tabla orden_fijo_paradas en
SQL Server si no existe. No borra ni modifica datos.

Uso:
    python scripts/crear_orden_fijo_paradas.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy import text
from app import create_app
from db import get_engine

DDL = """
    CREATE TABLE orden_fijo_paradas (
        nombre_regla  NVARCHAR(100) NOT NULL,
        num_tienda    INT           NOT NULL,
        posicion      INT           NOT NULL,
        CONSTRAINT PK_orden_fijo_paradas PRIMARY KEY (nombre_regla, num_tienda),
        CONSTRAINT UQ_orden_fijo_paradas_posicion UNIQUE (nombre_regla, posicion)
    )
"""


def main():
    app = create_app()
    with app.app_context():
        eng = get_engine()
        with eng.begin() as conn:
            existe = conn.execute(
                text("SELECT OBJECT_ID(:t, 'U')"), {"t": "dbo.orden_fijo_paradas"}
            ).scalar()
            if existe is None:
                conn.execute(text(DDL))
                print("Tabla creada: orden_fijo_paradas")
            else:
                print("Ya existía: orden_fijo_paradas")
        print("\nNota: reinicia el proceso Flask para que la reflexión de "
              "MetaData recoja la tabla nueva (db.py refleja una sola vez "
              "por proceso).")


if __name__ == "__main__":
    main()
