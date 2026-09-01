"""
scripts/agregar_mongo_id_pymsa_vina_real.py

Agrega la columna `mongo_id` (VARCHAR(MAX) NULL) a las tablas nuevas
`productos_pymsa` y `productos_vina_real`, y rellena cada fila existente con
un ID nuevo (str(bson.ObjectId())) -- mismo formato de 24 hex que usa el
resto de la app (ver `_nuevo_id()` en logic/configuracion_logic.py). Sin
esta columna, el CRUD generico de Productos (_listar/_obtener/_agregar/
_editar/_eliminar, que filtra/inserta por `tabla.c.mongo_id`) no funciona
contra estas tablas -- ambas se crearon con `id INT IDENTITY` como PK, sin
`mongo_id`, a diferencia de todas las demas tablas de la app.

Idempotente: si la columna ya existe, no la vuelve a crear; solo rellena las
filas que aun tengan mongo_id NULL. El ALTER TABLE se ejecuta fuera del
Engine cacheado (que ya reflejo el esquema viejo al importar `db`), asi que
se abre una conexion nueva de una vez y luego se recarga el metadata.

Uso:
    python scripts/agregar_mongo_id_pymsa_vina_real.py --dry-run   # solo muestra
    python scripts/agregar_mongo_id_pymsa_vina_real.py              # escribe
"""
import sys
import os
import argparse

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bson import ObjectId
from sqlalchemy import text
from db import get_engine
from app import create_app

TABLAS = ["productos_pymsa", "productos_vina_real"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    app = create_app()
    with app.app_context():
        engine = get_engine()
        with engine.connect() as conn:
            for tabla in TABLAS:
                existe = conn.execute(text(
                    "SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID(:t) AND name = 'mongo_id'"
                ), {"t": f"dbo.{tabla}"}).first()

                if existe:
                    print(f"[{tabla}] columna mongo_id ya existia.")
                elif args.dry_run:
                    print(f"[{tabla}] --dry-run: se crearia la columna mongo_id.")
                else:
                    conn.execute(text(f"ALTER TABLE dbo.{tabla} ADD mongo_id VARCHAR(MAX) NULL"))
                    print(f"[{tabla}] columna mongo_id creada.")

                filas = conn.execute(text(
                    f"SELECT id FROM dbo.{tabla} WHERE mongo_id IS NULL"
                )).fetchall() if (existe or not args.dry_run) else []

                if args.dry_run:
                    print(f"[{tabla}] --dry-run: {len(filas)} filas quedarian pendientes de mongo_id.")
                    continue

                for fila in filas:
                    conn.execute(
                        text(f"UPDATE dbo.{tabla} SET mongo_id = :mid WHERE id = :id"),
                        {"mid": str(ObjectId()), "id": fila.id},
                    )
                print(f"[{tabla}] {len(filas)} filas rellenadas con mongo_id nuevo.")


if __name__ == "__main__":
    main()
