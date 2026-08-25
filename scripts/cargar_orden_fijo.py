"""
cargar_orden_fijo.py

Carga datos/orden_fijo_paradas.csv en la tabla orden_fijo_paradas con
reemplazo completo por nombre_regla: borra las filas de cada regla presente
en el CSV e inserta las del CSV -- nunca acumula, mismo criterio que el
resto del proyecto (ver db.transaccion()).

Uso:
    python scripts/cargar_orden_fijo.py [ruta_csv]
"""
import sys, os, csv
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import create_app
from db import get_table, transaccion

CSV_DEFAULT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "datos", "orden_fijo_paradas.csv")


def cargar(csv_path: str = CSV_DEFAULT):
    with open(csv_path, newline="", encoding="utf-8") as f:
        filas = [
            {"nombre_regla": r["nombre_regla"].strip(),
             "num_tienda": int(r["num_tienda"]),
             "posicion": int(r["posicion"])}
            for r in csv.DictReader(f)
        ]
    if not filas:
        print("CSV vacío, nada que cargar.")
        return

    reglas = sorted({f["nombre_regla"] for f in filas})
    t = get_table("orden_fijo_paradas")
    with transaccion() as conn:
        for regla in reglas:
            conn.execute(t.delete().where(t.c.nombre_regla == regla))
        conn.execute(t.insert(), filas)

    print(f"Reglas cargadas ({len(reglas)}): {', '.join(reglas)}")
    print(f"Filas insertadas: {len(filas)}")


def main():
    app = create_app()
    with app.app_context():
        csv_path = sys.argv[1] if len(sys.argv) > 1 else CSV_DEFAULT
        cargar(csv_path)


if __name__ == "__main__":
    main()
