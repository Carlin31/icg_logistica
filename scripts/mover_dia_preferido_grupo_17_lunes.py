"""
mover_dia_preferido_grupo_17_lunes.py

Agrega LUNES como dia admisible de grupo 17 (Sotavento / Veracruz 3 /
Veracruz 4 / Tejeria) y lo vuelve su dia preferido -- hoy corre JUEVES o
VIERNES (ver mover_dia_preferido_grupo_17.py, version 43), pero los tres
camiones chicos (T20/T23/T25, 1.5t) ya estan saturados esos dos dias, asi
que el motor termina usando T17_2 (2.5t) para una carga de apenas ~793 kg
(31.7% de utilizacion) -- desperdicia un camion grande en una carga chica.

Contexto (usuario, 2026-09-02): confirmado por coordenadas que las 4
sucursales del grupo estan bien, el problema es solo el tamano del camion.
LUNES tiene a T23 y T25 completamente libres esta semana (0 rutas), y no es
un dia ajeno a estas unidades -- otros grupos de la plantilla ya prefieren
LUNES con T17_2/T20/T23/T25 (grupos 14, 15, 24, 27). La afinidad historica
propia de grupo 17 ("unidades_afines": T20:2 | T17_2:2 | T23:2 | ...) ya
tiene a T23 empatado en primer lugar, asi que el motor deberia preferirlo
sobre T25 (sin afinidad registrada con este grupo) al repartir por peso.

JUEVES y VIERNES se mantienen como dias admisibles (fallback si algun
lunes futuro tampoco tiene espacio en camion chico) -- LUNES solo se vuelve
el PREFERIDO (es_canonico=True).

Uso:
    python scripts/mover_dia_preferido_grupo_17_lunes.py --dry-run   # solo muestra
    python scripts/mover_dia_preferido_grupo_17_lunes.py              # escribe
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
            t.c.grupo == GRUPO, t.c.dia == "VIERNES", t.c.vigente == True
        ).values(es_canonico=False))
        db.execute(insert(t).values(
            version=version_nueva, grupo=GRUPO, dia="LUNES", es_canonico=True,
            orden=0, vigente_desde=datetime.now().isoformat(), vigente=True))
        db.commit()

        print("\nDESPUES:")
        _mostrar(db, t)

        from logic.plantilla_canonica import obtener_grupos
        plantilla = {g["grupo"]: g for g in obtener_grupos()}
        g17 = plantilla[GRUPO]
        assert g17["dia_preferido"] == "LUNES", g17["dia_preferido"]
        assert set(g17["dias_admisibles"]) == {"JUEVES", "VIERNES", "LUNES"}, g17["dias_admisibles"]
        print("\nOK -- grupo 17 ahora admite JUEVES, VIERNES y LUNES, preferido LUNES")


if __name__ == "__main__":
    main()
