"""
mover_dia_preferido_grupo_20.py

Vuelve VIERNES el dia preferido de grupo 20 (Ursulo Galvan, Cempoala, Palma
Sola, Emilio Carranza, Vega de Alatorre); JUEVES queda de respaldo. Mismo
patron que scripts/mover_dia_preferido_grupo_17.py (commit a491de7,
2026-08-27): parche puntual sobre plantilla_grupo_dia, sin crear version
nueva y sin tocar ningun otro grupo.

Contexto (pedido del usuario 2026-08-31): en el PDF real, grupo 20 aparecio
en JUEVES/T 17_2 (2.5t) con 1,546 kg -- 3 kg por debajo del tope de 1.5t
(1,549 kg), pero fue a un camion mas grande de todos modos. No es un bug
de asignacion (el motor ya prueba T20/T23/T25 por capacidad ascendente
antes que T17_2): es saturacion de camiones chicos en jueves.

Respaldo historico confirmado (13 semanas en rutas_historicas, sids 43,
44, 48, 61, 82): las 4 veces que estas 5 sucursales viajaron juntas en un
solo grupo en JUEVES, terminaron en T 17_2 (1836, 1316, 1725 y 1546 kg --
nunca en camion de 1.5t). Las veces que operaron en VIERNES (multiples
semanas, a veces partido en dos camiones), NUNCA cayeron en T 17_2: siempre
T 17_1, T 20, T 23 o T 25. Viernes tiene menos camiones chicos ya
comprometidos por otros grupos ese dia.

grupo 20 ya tenia VIERNES como dia admisible (agregado en un ajuste previo,
igual que grupo 10/grupo 8 mencionados en el commit de grupo 17), pero el
preferido seguia siendo JUEVES. Este script solo invierte cual es el
preferido; JUEVES se mantiene admisible como respaldo.

Uso:
    python scripts/mover_dia_preferido_grupo_20.py --dry-run   # solo muestra
    python scripts/mover_dia_preferido_grupo_20.py              # escribe
"""
import sys
import os
import argparse
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy import select, update, insert
from db import get_db, get_table, transaccion
from app import create_app

GRUPO = 20


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
        g20 = plantilla[GRUPO]
        assert g20["dia_preferido"] == "VIERNES", g20["dia_preferido"]
        assert set(g20["dias_admisibles"]) == {"JUEVES", "VIERNES"}, g20["dias_admisibles"]
        print("\nOK -- grupo 20 ahora prefiere VIERNES, JUEVES de respaldo")


if __name__ == "__main__":
    main()
