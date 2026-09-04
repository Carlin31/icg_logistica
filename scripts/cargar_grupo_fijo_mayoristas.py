"""
cargar_grupo_fijo_mayoristas.py

Carga datos/grupo_fijo_mayoristas.csv en la tabla grupo_fijo_mayoristas con
reemplazo COMPLETO: el CSV es la fuente de verdad de todos los pines, asi que
la tabla se vacia antes de insertar (mismo criterio que
scripts/cargar_ancla_mayoristas.py).

Valida que cada grupo exista en la plantilla vigente: un pin a un grupo que no
existe nunca encontraria ruta y quedaria mudo, pareciendo aplicado.

Uso:
    python scripts/cargar_grupo_fijo_mayoristas.py [ruta_csv]
"""
import sys, os, csv
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import create_app
from db import get_table, transaccion

CSV_DEFAULT = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                           "datos", "grupo_fijo_mayoristas.csv")


def cargar(csv_path: str = CSV_DEFAULT):
    from logic.plantilla_canonica import obtener_grupos

    with open(csv_path, newline="", encoding="utf-8") as f:
        filas = [
            {"id_cliente": int(r["id_cliente"]),
             "grupo": int(r["grupo"]),
             "nota": (r.get("nota") or "").strip() or None}
            for r in csv.DictReader(f)
        ]

    vistos: dict = {}
    for f in filas:
        anterior = vistos.get(f["id_cliente"])
        if anterior is not None and anterior != f["grupo"]:
            raise ValueError(
                f"id_cliente {f['id_cliente']} aparece fijado a dos grupos "
                f"distintos ({anterior} y {f['grupo']}) -- un mayorista solo "
                f"puede tener un pin a la vez.")
        vistos[f["id_cliente"]] = f["grupo"]

    grupos_vigentes = {int(g["grupo"]) for g in obtener_grupos()}
    desconocidos = sorted({f["grupo"] for f in filas} - grupos_vigentes)
    if desconocidos:
        raise ValueError(
            f"grupos inexistentes en la plantilla vigente: {desconocidos}. "
            f"Un pin a un grupo que no existe nunca encuentra ruta y quedaria "
            f"mudo; corrige el CSV.")

    t = get_table("grupo_fijo_mayoristas")
    with transaccion() as conn:
        conn.execute(t.delete())
        if filas:
            conn.execute(t.insert(), filas)

    print(f"Pines cargados: {len(filas)}")
    for f in filas:
        print(f"   mayorista {f['id_cliente']} -> grupo {f['grupo']}")


def main():
    app = create_app()
    with app.app_context():
        cargar(sys.argv[1] if len(sys.argv) > 1 else CSV_DEFAULT)


if __name__ == "__main__":
    main()
