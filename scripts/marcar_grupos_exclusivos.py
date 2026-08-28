"""
marcar_grupos_exclusivos.py

Marca exclusivo=1 en los grupos que nunca deben compartir camión con otro
grupo, aunque el peso combinado quepa. Corrige la plantilla VIGENTE in
place -- no crea una versión nueva, mismo criterio ya usado para cargar
grupos_unidad_forzada.csv sobre `unidad_forzada`.

Ver docs/superpowers/specs/2026-08-28-grupos-exclusivos-convrp-design.md.

Uso:
    python scripts/marcar_grupos_exclusivos.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy import update
from app import create_app
from db import get_table, transaccion

# 4  = Zona 4 (Chacaltianguis, Tlacojalpan, Otatitlán, Papaloapan)
# 24 = Zona 24 (Amatlán) -- casi siempre acompañada de mayoristas
# 25 = sub-grupo jueves de Tuxtepec (Tuxtepec 5, 6, 8)
GRUPOS_EXCLUSIVOS = [4, 24, 25]


def main():
    app = create_app()
    with app.app_context():
        t = get_table("plantilla_grupo")
        with transaccion() as conn:
            for grupo in GRUPOS_EXCLUSIVOS:
                res = conn.execute(
                    update(t).where(t.c.grupo == grupo, t.c.vigente == 1)
                    .values(exclusivo=1))
                print(f"grupo {grupo}: {res.rowcount} fila(s) marcada(s) exclusivo=1")


if __name__ == "__main__":
    main()
