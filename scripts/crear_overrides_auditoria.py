"""
crear_overrides_auditoria.py

Script de un solo uso (idempotente): crea la tabla overrides_auditoria en
SQL Server si no existe. No borra ni modifica datos.

Registro auditable (append-only, nunca se actualiza ni borra una fila) de
los parches puntuales aplicados sobre la plantilla canonica y las tablas de
pines de mayoristas -- reemplaza a que la unica explicacion de un override
viva en el docstring de un script de un solo uso. Ver
logic/overrides_admin.py para la escritura/lectura y
scripts/listar_overrides.py para el diagnostico del estado actual.

Uso:
    python scripts/crear_overrides_auditoria.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy import text
from app import create_app
from db import get_engine

DDL = """
    CREATE TABLE overrides_auditoria (
        id             INT            IDENTITY(1,1) NOT NULL,
        tipo           NVARCHAR(40)   NOT NULL,
        clave          NVARCHAR(60)   NOT NULL,
        valor_anterior NVARCHAR(400)  NULL,
        valor_nuevo    NVARCHAR(400)  NULL,
        motivo         NVARCHAR(500)  NOT NULL,
        aplicado_por   NVARCHAR(120)  NULL,
        aplicado_en    DATETIME2      NOT NULL,
        CONSTRAINT PK_overrides_auditoria PRIMARY KEY (id)
    )
"""


def main():
    app = create_app()
    with app.app_context():
        eng = get_engine()
        with eng.begin() as conn:
            existe = conn.execute(
                text("SELECT OBJECT_ID(:t, 'U')"), {"t": "dbo.overrides_auditoria"}
            ).scalar()
            if existe is None:
                conn.execute(text(DDL))
                print("Tabla creada: overrides_auditoria")
            else:
                print("Ya existia: overrides_auditoria")
        print("\nNota: reinicia el proceso Flask para que la reflexion de "
              "MetaData recoja la tabla nueva (db.py refleja una sola vez "
              "por proceso).")


if __name__ == "__main__":
    main()
