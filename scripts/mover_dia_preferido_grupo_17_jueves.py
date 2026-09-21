"""
mover_dia_preferido_grupo_17_jueves.py

Revierte grupo 17 (Sotavento / Veracruz 3 / Veracruz 4 / Tejeria) de LUNES
de vuelta a JUEVES como dia preferido -- lo opuesto de
mover_dia_preferido_grupo_17_lunes.py (2026-09-02).

Contexto (usuario, 2026-09-21): en la semana 7-11 sept 2026, grupo 27
(Tierra Blanca 4/5/8, RIGIDO a LUNES -- ver zona 11) se parte por peso sobre
K20 y "lo que queda" (1,462 kg) no puede bajar a un camion de 1.5t porque
T20 y T23 ya estan ocupados el LUNES por los grupos 14 y 17. T23/JUEVES
esta completamente libre esta semana, y JUEVES ya es dia admisible de
grupo 17 (nunca se quito, solo dejo de ser el preferido) -- moverlo ahi
libera T23/LUNES sin abrir ningun dia nuevo.

RIESGO ACEPTADO EXPLICITAMENTE POR EL USUARIO: este es el mismo tipo de
vaiven que ya paso una vez (JUEVES/VIERNES -> LUNES el 2026-09-02, porque
T20/T23/T25 estaban saturados esos dos dias esa semana). Si en una semana
futura T20/T23/T25 vuelven a saturarse el JUEVES, grupo 17 volveria a caer
en T17_2 (2.5t) sobredimensionado -- el problema que la correccion de
septiembre resolvio. El usuario decidio aceptar ese riesgo en vez de dejar
esto como override puntual de una sola semana.

LUNES se mantiene como dia admisible (fallback), igual que el patch de
septiembre dejo JUEVES/VIERNES como fallback al preferir LUNES.

Uso:
    python scripts/mover_dia_preferido_grupo_17_jueves.py --dry-run   # solo muestra
    python scripts/mover_dia_preferido_grupo_17_jueves.py              # escribe
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

from sqlalchemy import select, update, insert, func
from db import get_db, get_table
from app import create_app

GRUPO = 17


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
        t = get_table("plantilla_grupo_dia")

        print("ANTES:")
        _mostrar(db, t)

        version_nueva = (db.execute(select(func.max(t.c.version))).scalar() or 0) + 1

        if args.dry_run:
            print(f"\n--dry-run: no se escribio nada (usaria version={version_nueva}).")
            return

        db.execute(update(t).where(
            t.c.grupo == GRUPO, t.c.dia == "LUNES", t.c.vigente == True
        ).values(es_canonico=False))
        db.execute(insert(t).values(
            version=version_nueva, grupo=GRUPO, dia="JUEVES", es_canonico=True,
            orden=0, vigente_desde=datetime.now().isoformat(), vigente=True))
        db.commit()

        print("\nDESPUES:")
        _mostrar(db, t)

        from logic.plantilla_canonica import obtener_grupos
        plantilla = {g["grupo"]: g for g in obtener_grupos()}
        g17 = plantilla[GRUPO]
        assert g17["dia_preferido"] == "JUEVES", g17["dia_preferido"]
        assert set(g17["dias_admisibles"]) == {"JUEVES", "VIERNES", "LUNES"}, g17["dias_admisibles"]
        print("\nOK -- grupo 17 ahora admite JUEVES, VIERNES y LUNES, preferido JUEVES")


if __name__ == "__main__":
    main()
