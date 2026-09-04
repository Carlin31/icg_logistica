"""
crear_grupo_fijo_mayoristas.py

Script de un solo uso (idempotente): crea la tabla grupo_fijo_mayoristas en
SQL Server si no existe. No borra ni modifica datos.

La tabla fija "este mayorista viaja con la ruta de este grupo" -- ver
logic/grupo_fijo_mayoristas.py para la semantica y
scripts/cargar_grupo_fijo_mayoristas.py para cargar
datos/grupo_fijo_mayoristas.csv.

Uso:
    python scripts/crear_grupo_fijo_mayoristas.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy import text
from app import create_app
from db import get_engine

DDL = """
    CREATE TABLE grupo_fijo_mayoristas (
        id_cliente  INT            NOT NULL,
        grupo       INT            NOT NULL,
        nota        NVARCHAR(400)  NULL,
        CONSTRAINT PK_grupo_fijo_mayoristas PRIMARY KEY (id_cliente)
    )
"""


def main():
    app = create_app()
    with app.app_context():
        eng = get_engine()
        with eng.begin() as conn:
            existe = conn.execute(
                text("SELECT OBJECT_ID(:t, 'U')"), {"t": "dbo.grupo_fijo_mayoristas"}
            ).scalar()
            if existe is None:
                conn.execute(text(DDL))
                print("Tabla creada: grupo_fijo_mayoristas")
            else:
                print("Ya existia: grupo_fijo_mayoristas")
        print("\nNota: reinicia el proceso Flask para que la reflexion de "
              "MetaData recoja la tabla nueva (db.py refleja una sola vez "
              "por proceso).")


if __name__ == "__main__":
    main()
