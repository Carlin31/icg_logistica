"""
mover_dia_preferido_grupo_16.py

Vuelve VIERNES el dia preferido de grupo 16 (Veracruz 1, El Tejar, Anton
Lizardo, Jamapa); JUEVES queda de respaldo. Mismo patron que
scripts/mover_dia_preferido_grupo_17.py (commit a491de7) y
scripts/mover_dia_preferido_grupo_20.py (2026-08-31): parche puntual sobre
plantilla_grupo_dia, sin crear version nueva y sin tocar ningun otro grupo.

Contexto (pedido del usuario 2026-08-31): en el PDF real, grupo 16 aparecio
en JUEVES/T 17_1 (2.5t) con 1,536 kg -- 13 kg por debajo del tope de 1.5t
(1,549 kg), pero fue a un camion mas grande de todos modos.

NOTA -- a diferencia de grupo 20, aqui el respaldo historico es mixto: de
las 4 semanas en que las 4 sucursales viajaron juntas (13 semanas en
rutas_historicas), 2 en JUEVES (1,752 kg -> T 17_1 correcto porque excede
1,549; 1,535 kg -> T 17_1, mismo patron que hoy) y 2 en VIERNES (1,491 kg
-> T 17_2, tambien incorrecto; 1,495 kg -> T 20, correcto). VIERNES no
evito el camion grande de forma confiable para ESTE grupo. La alternativa
con mejor respaldo (particionar el grupo en 2, como Tierra Blanca -- 7/13
semanas se dividio solo en [Veracruz 1] + [El Tejar, Anton Lizardo,
Jamapa], siempre en camion chico en ambas partes) fue presentada al
usuario, que prefirio este cambio de dia mas simple pese al respaldo mas
debil. Si el problema persiste tras este cambio, la particion en 2 grupos
es la alternativa con mejor evidencia.

Uso:
    python scripts/mover_dia_preferido_grupo_16.py --dry-run   # solo muestra
    python scripts/mover_dia_preferido_grupo_16.py              # escribe
"""
import sys
import os
import argparse

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy import select, update
from db import get_db, get_table, transaccion
from app import create_app

GRUPO = 16


def _mostrar(db, tabla):
    rows = list(db.execute(
        select(tabla).where(tabla.c.grupo == GRUPO, tabla.c.vigente == True)).mappings())
    for r in sorted(rows, key=lambda r: (r["orden"] if r["orden"] is not None else 99)):
        print("   ", dict(r))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    app = create_app()
    with app.app_context():
        db = get_db()
        t  = get_table("plantilla_grupo_dia")
        tg = get_table("plantilla_grupo")

        version = db.execute(
            select(tg.c.version).where(tg.c.grupo == GRUPO, tg.c.vigente == True)
        ).scalar()
        print(f"Version vigente detectada: {version}")

        print("ANTES:")
        _mostrar(db, t)

        if args.dry_run:
            print("\n--dry-run: no se escribio nada.")
            return

        with transaccion() as conn:
            conn.execute(update(t).where(
                t.c.grupo == GRUPO, t.c.dia == "JUEVES", t.c.vigente == True
            ).values(es_canonico=False))
            conn.execute(update(t).where(
                t.c.grupo == GRUPO, t.c.dia == "VIERNES", t.c.vigente == True
            ).values(es_canonico=True))

        print("\nDESPUES:")
        _mostrar(db, t)

        from logic.plantilla_canonica import obtener_grupos
        plantilla = {g["grupo"]: g for g in obtener_grupos()}
        g16 = plantilla[GRUPO]
        assert g16["dia_preferido"] == "VIERNES", g16["dia_preferido"]
        assert set(g16["dias_admisibles"]) == {"JUEVES", "VIERNES"}, g16["dias_admisibles"]
        print("\nOK -- grupo 16 ahora prefiere VIERNES, JUEVES de respaldo")


if __name__ == "__main__":
    main()
