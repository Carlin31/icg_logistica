"""
mover_dia_preferido_grupos_10_8.py

Cambia el DIA PREFERIDO (es_canonico=True en plantilla_grupo_dia) de dos
grupos de la plantilla vigente, sin crear una version nueva -- solo mueve
la bandera es_canonico entre filas que YA existian como dias_admisibles.

Contexto (ver docs/superpowers/plans/2026-08-26-asignacion-vehiculos-por-peso.md,
seccion posterior a la Task 14): el jueves 24-28 de agosto tenia mas demanda
"de camion chico" (<=1549 kg) de la que caben los 3 camiones chicos reales
(T25+T23+T20 = 4647 kg de capacidad) -- no es un bug de asignacion, es un
limite matematico de flota ese dia especifico. De los grupos afectados, dos
tenian otro dia admisible con espacio libre real en camion chico:

- Grupo 10 (Temascal / Los Naranjos, ~1078 kg): admisible JUEVES/MIERCOLES/
  VIERNES. T 25 estaba completamente vacia el miercoles -- ahora es su dia
  preferido, cae ahi con 1078/1549 kg (69.6%).
- Grupo 8 (Valle Nacional / Chiltepec, ~900 kg): admisible JUEVES/VIERNES.
  El viernes casi no tenia demanda asignada -- ahora es su dia preferido,
  cae en T 23/VIERNES con 900/1549 kg (58.1%).

Grupo 7 (San Felipe Jalapa de Diaz / Jalapa de Diaz 2, 1392 kg, admisible
MIERCOLES/JUEVES) y grupo 17 (Sotavento y zona, 793 kg, SOLO admite JUEVES)
se dejan sin tocar -- decision explicita del usuario de mover solo estos 2
grupos por ahora.

Uso:
    python scripts/mover_dia_preferido_grupos_10_8.py --dry-run   # solo muestra
    python scripts/mover_dia_preferido_grupos_10_8.py              # escribe
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
from db import get_db, get_table
from app import create_app

MOVIMIENTOS = [
    # (grupo, dia_actual_canonico, dia_nuevo_canonico)
    (10, "JUEVES", "MIERCOLES"),
    (8, "JUEVES", "VIERNES"),
]


def _mostrar(db, tabla, grupo):
    rows = list(db.execute(
        select(tabla).where(tabla.c.grupo == grupo, tabla.c.vigente == True)).mappings())
    for r in sorted(rows, key=lambda r: (r["orden"] if r["orden"] is not None else 99)):
        print("   ", dict(r))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    app = create_app()
    with app.app_context():
        db = get_db()
        t = get_table("plantilla_grupo_dia")

        print("ANTES:")
        for grupo, _, _ in MOVIMIENTOS:
            print(f" grupo {grupo}:")
            _mostrar(db, t, grupo)

        if args.dry_run:
            print("\n--dry-run: no se escribio nada.")
            return

        for grupo, dia_viejo, dia_nuevo in MOVIMIENTOS:
            db.execute(update(t).where(
                t.c.grupo == grupo, t.c.dia == dia_viejo, t.c.vigente == True
            ).values(es_canonico=False))
            db.execute(update(t).where(
                t.c.grupo == grupo, t.c.dia == dia_nuevo, t.c.vigente == True
            ).values(es_canonico=True))
        db.commit()

        print("\nDESPUES:")
        for grupo, _, dia_nuevo in MOVIMIENTOS:
            print(f" grupo {grupo}:")
            _mostrar(db, t, grupo)

        from logic.plantilla_canonica import obtener_grupos
        plantilla = {g["grupo"]: g for g in obtener_grupos()}
        for grupo, _, dia_nuevo in MOVIMIENTOS:
            real = plantilla[grupo]["dia_preferido"]
            assert real == dia_nuevo, f"grupo {grupo}: esperaba {dia_nuevo}, quedo {real}"
        print("\nOK -- dia_preferido actualizado para los grupos", [m[0] for m in MOVIMIENTOS])


if __name__ == "__main__":
    main()
