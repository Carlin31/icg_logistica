"""
mover_dia_preferido_grupo_17.py

Agrega VIERNES como dia admisible de grupo 17 (Sotavento / Veracruz 3 /
Veracruz 4 / Tejeria) y lo vuelve su dia preferido -- hoy solo tiene JUEVES
en plantilla_grupo_dia (FLEXIBLE, sin ningun otro dia admisible), lo que le
impide moverse aunque el jueves no tenga espacio de camion chico.

Contexto (ver docs/superpowers/plans/2026-08-26-asignacion-vehiculos-por-peso.md,
seccion posterior a la Task 15): el jueves 24-28 de agosto solo hay 3
camiones chicos (T25+T23+T20 = 4647 kg) para mas demanda "chica" de la que
caben -- confirmado que no es un bug de asignacion (Tasks 13/14/15 ya
corrigieron todo lo que si era un bug real). De los grupos afectados,
grupo 7 y grupo 10 tenian otro dia admisible en la plantilla; grupo 17 no
tenia ninguno.

Respaldo historico (13 semanas en rutas_historicas): los sids de grupo 17
(67, 71, 72, 99) operaron en VIERNES al menos 2 veces (semanas del 9-13 de
febrero y 23-27 de febrero de 2026), ademas de LUNES y MARTES una vez cada
uno -- JUEVES es el dia mas comun pero no el unico historicamente. VIERNES
tenia, ademas, muy poca demanda ya asignada esa semana (casi toda la flota
libre), lo que lo hace la mejor opcion real disponible.

Se agrega VIERNES como NUEVO renglon (no existia) y se le pasa la bandera
es_canonico; JUEVES se mantiene como dia admisible (por si algun jueves
futuro VIERNES no tiene espacio), solo deja de ser el preferido.

Uso:
    python scripts/mover_dia_preferido_grupo_17.py --dry-run   # solo muestra
    python scripts/mover_dia_preferido_grupo_17.py              # escribe
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
from db import get_db, get_table
from app import create_app

GRUPO = 17
VERSION = 43


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

        if args.dry_run:
            print("\n--dry-run: no se escribio nada.")
            return

        db.execute(update(t).where(
            t.c.grupo == GRUPO, t.c.dia == "JUEVES", t.c.vigente == True
        ).values(es_canonico=False))
        db.execute(insert(t).values(
            version=VERSION, grupo=GRUPO, dia="VIERNES", es_canonico=True,
            orden=1, vigente_desde=datetime.now().isoformat(), vigente=True))
        db.commit()

        print("\nDESPUES:")
        _mostrar(db, t)

        from logic.plantilla_canonica import obtener_grupos
        plantilla = {g["grupo"]: g for g in obtener_grupos()}
        g17 = plantilla[GRUPO]
        assert g17["dia_preferido"] == "VIERNES", g17["dia_preferido"]
        assert set(g17["dias_admisibles"]) == {"JUEVES", "VIERNES"}, g17["dias_admisibles"]
        print("\nOK -- grupo 17 ahora admite JUEVES y VIERNES, preferido VIERNES")


if __name__ == "__main__":
    main()
