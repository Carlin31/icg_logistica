"""
cargar_ancla_mayoristas.py

Carga datos/ancla_mayoristas.csv en la tabla ancla_mayoristas con reemplazo
COMPLETO: el CSV es la fuente de verdad de todas las anclas, asi que la
tabla se vacia antes de insertar (nunca acumula, mismo criterio que
scripts/cargar_orden_fijo.py).

Uso:
    python scripts/cargar_ancla_mayoristas.py [ruta_csv]
"""
import sys, os, csv
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import create_app
from db import get_table, transaccion

CSV_DEFAULT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "datos", "ancla_mayoristas.csv")


def cargar(csv_path: str = CSV_DEFAULT):
    with open(csv_path, newline="", encoding="utf-8") as f:
        filas = [
            {"id_cliente": int(r["id_cliente"]),
             "num_tienda": int(r["num_tienda"]),
             "nota": (r.get("nota") or "").strip() or None}
            for r in csv.DictReader(f)
        ]

    vistos: dict = {}
    for f in filas:
        anterior = vistos.get(f["id_cliente"])
        if anterior is not None and anterior != f["num_tienda"]:
            raise ValueError(
                f"id_cliente {f['id_cliente']} aparece anclado a dos sucursales "
                f"distintas ({anterior} y {f['num_tienda']}) -- un mayorista solo "
                f"puede tener un ancla a la vez."
            )
        vistos[f["id_cliente"]] = f["num_tienda"]

    t = get_table("ancla_mayoristas")
    with transaccion() as conn:
        conn.execute(t.delete())
        if filas:
            conn.execute(t.insert(), filas)

    print(f"Anclas cargadas: {len(filas)}")
    for f in filas:
        print(f"   mayorista {f['id_cliente']} -> despues de la sucursal {f['num_tienda']}")


def main():
    app = create_app()
    with app.app_context():
        csv_path = sys.argv[1] if len(sys.argv) > 1 else CSV_DEFAULT
        cargar(csv_path)


if __name__ == "__main__":
    main()
