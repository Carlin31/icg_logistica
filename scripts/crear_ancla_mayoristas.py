"""
crear_ancla_mayoristas.py

Script de un solo uso (idempotente): crea la tabla ancla_mayoristas en
SQL Server si no existe. No borra ni modifica datos.

La tabla fija "este mayorista se visita inmediatamente despues de esta
sucursal" -- ver logic/ancla_mayoristas.py para la semantica completa y
scripts/cargar_ancla_mayoristas.py para cargar datos/ancla_mayoristas.csv.

Uso:
    python scripts/crear_ancla_mayoristas.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy import text
from app import create_app
from db import get_engine

DDL = """
    CREATE TABLE ancla_mayoristas (
        id_cliente  INT            NOT NULL,
        num_tienda  INT            NOT NULL,
        nota        NVARCHAR(400)  NULL,
        CONSTRAINT PK_ancla_mayoristas PRIMARY KEY (id_cliente)
    )
"""


def main():
    app = create_app()
    with app.app_context():
        eng = get_engine()
        with eng.begin() as conn:
            existe = conn.execute(
                text("SELECT OBJECT_ID(:t, 'U')"), {"t": "dbo.ancla_mayoristas"}
            ).scalar()
            if existe is None:
                conn.execute(text(DDL))
                print("Tabla creada: ancla_mayoristas")
            else:
                print("Ya existia: ancla_mayoristas")
        print("\nNota: reinicia el proceso Flask para que la reflexion de "
              "MetaData recoja la tabla nueva (db.py refleja una sola vez "
              "por proceso).")


if __name__ == "__main__":
    main()
