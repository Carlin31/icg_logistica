"""
scripts/agregar_sucursales_104_105.py

Dos altas nuevas en el catalogo `sucursales` (2026-10-02) que no estaban
asignadas a ningun grupo de la plantilla:

  - 104 San Andres 5 -> grupo 22 (zona 22), inmediatamente despues de
    San Andres 4 (103) y antes de Catemaco. Grupo 22 pasa de 7 a 8
    sucursales; no cambia dia/unidad/rigidez.
        zona_22: 90, 91, 98, 103, 104, 39, 96, 45  (posiciones 1..8)

  - 105 Jalcomulco -> zona de Carrizal/Actopan (grupo 19). Esa zona tenia
    Carrizal, Actopan, Rinconada y Cardel; por pedido de los jefes se parte en:
        grupo 19 (zona 19):  105 Jalcomulco, 81 Carrizal, 52 Actopan
        grupo 30 (zona 26, NUEVA): 53 Rinconada, 40 Cardel
    Grupo 30 hereda de grupo 19 rigidez (RIGIDO) y dias (JUEVES preferido,
    admite VIERNES); NO hereda unidad_ref/afinidad (mismo criterio que el
    grupo 29 de scripts/dividir_zona22_santiago.py). Grupo 19 conserva todo.
        zona_19: 105 (1), 81 (2), 52 (3)
        zona_30: 53 (1), 40 (2)

Parche puntual no-destructivo sobre la version vigente, mismo patron que
scripts/dividir_zona22_santiago.py.

Uso:
    python scripts/agregar_sucursales_104_105.py --dry-run
    python scripts/agregar_sucursales_104_105.py --motivo "..."
"""
import sys
import os
import argparse
import getpass
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy import select, update, insert
from db import get_db, get_table, transaccion
from app import create_app

# San Andres 5 -> grupo 22
GRUPO_SA = 22
SA_ANTES = [90, 91, 98, 103, 39, 96, 45]
SA_DESPUES = [90, 91, 98, 103, 104, 39, 96, 45]   # orden de visita
REGLA_SA = "zona_22"

# Jalcomulco + particion de grupo 19
GRUPO_CARRIZAL = 19
GRUPO_NUEVO = 30
ZONA_NUEVA = 26
CARRIZAL_ANTES = [81, 52, 53, 40]
CARRIZAL_DESPUES = [105, 81, 52]                  # Jalcomulco, Carrizal, Actopan
RINCONADA_NUEVO = [53, 40]                        # Rinconada, Cardel
REGLA_CARRIZAL = "zona_19"
REGLA_NUEVO = "zona_30"


def _sucursales_vigentes(db, tgs, grupo):
    return sorted(r.num_tienda for r in db.execute(
        select(tgs.c.num_tienda).where(tgs.c.grupo == grupo, tgs.c.vigente == True)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--motivo", default=None)
    ap.add_argument("--aplicado-por", dest="aplicado_por", default=None)
    args = ap.parse_args()
    if not args.dry_run and not args.motivo:
        raise SystemExit("--motivo es obligatorio (salvo --dry-run)")

    app = create_app()
    with app.app_context():
        db = get_db()
        tg = get_table("plantilla_grupo")
        tgs = get_table("plantilla_grupo_sucursal")
        tgd = get_table("plantilla_grupo_dia")

        g22 = db.execute(
            select(tg).where(tg.c.grupo == GRUPO_SA, tg.c.vigente == True)
        ).mappings().first()
        g19 = db.execute(
            select(tg).where(tg.c.grupo == GRUPO_CARRIZAL, tg.c.vigente == True)
        ).mappings().first()
        if not g22 or not g19:
            raise SystemExit("grupo 22 o 19 sin version vigente")
        version = g19["version"]
        if g22["version"] != version:
            raise SystemExit("grupos 22 y 19 en versiones distintas -- revisar")

        if db.execute(select(tg.c.grupo).where(
                tg.c.version == version, tg.c.grupo == GRUPO_NUEVO)).first():
            raise SystemExit(f"(version {version}, grupo {GRUPO_NUEVO}) ya existe -- "
                             f"el parche ya se aplico o el numero esta ocupado.")

        act22 = _sucursales_vigentes(db, tgs, GRUPO_SA)
        act19 = _sucursales_vigentes(db, tgs, GRUPO_CARRIZAL)
        if act22 != sorted(SA_ANTES):
            raise SystemExit(f"grupo 22 hoy tiene {act22}, se esperaba {sorted(SA_ANTES)}")
        if act19 != sorted(CARRIZAL_ANTES):
            raise SystemExit(f"grupo 19 hoy tiene {act19}, se esperaba {sorted(CARRIZAL_ANTES)}")
        for nt in (104, 105):
            if db.execute(select(tgs.c.grupo).where(
                    tgs.c.num_tienda == nt, tgs.c.vigente == True)).first():
                raise SystemExit(f"sucursal {nt} ya esta asignada a un grupo")

        dias19 = db.execute(
            select(tgd).where(tgd.c.grupo == GRUPO_CARRIZAL, tgd.c.vigente == True)
        ).mappings().all()

        print(f"Version vigente: {version}")
        print(f"Grupo 22: {act22} (tam {g22['tam']}) -> {sorted(SA_DESPUES)} (tam {len(SA_DESPUES)})")
        print(f"          orden fijo {REGLA_SA} = {SA_DESPUES}")
        print(f"Grupo 19: {act19} (tam {g19['tam']}) -> {sorted(CARRIZAL_DESPUES)} (tam {len(CARRIZAL_DESPUES)})")
        print(f"          orden fijo {REGLA_CARRIZAL} = {CARRIZAL_DESPUES}")
        print(f"Grupo {GRUPO_NUEVO} (zona {ZONA_NUEVA}, NUEVO): {sorted(RINCONADA_NUEVO)} "
              f"(tam {len(RINCONADA_NUEVO)}), {g19['rigidez']}, dias "
              f"{[d['dia'] for d in sorted(dias19, key=lambda d: d['orden'])]}")
        print(f"          orden fijo {REGLA_NUEVO} = {RINCONADA_NUEVO}")

        if args.dry_run:
            print("\n--dry-run: no se escribio nada.")
            return

        ahora = datetime.now().isoformat()
        quien = args.aplicado_por or os.environ.get("USERNAME") or os.environ.get("USER") or getpass.getuser()
        with transaccion() as conn:
            # --- San Andres 5 -> grupo 22
            conn.execute(insert(tgs).values(
                version=version, grupo=GRUPO_SA, num_tienda=104,
                vigente_desde=ahora, vigente=True))
            conn.execute(update(tg).where(
                tg.c.grupo == GRUPO_SA, tg.c.vigente == True
            ).values(tam=len(SA_DESPUES)))

            # --- Jalcomulco -> grupo 19, Rinconada/Cardel -> grupo 30 nuevo
            conn.execute(insert(tg).values(
                version=version, grupo=GRUPO_NUEVO, rigidez=g19["rigidez"],
                dia=g19["dia"], tam=len(RINCONADA_NUEVO), cohesion=None,
                unidad_ref=None, que_hace_vrp=None, vigente_desde=ahora,
                vigente=True, unidades_afines=None, unidad_forzada=False,
                zona=ZONA_NUEVA, unidades_excluidas=None, exclusivo=g19["exclusivo"]))
            conn.execute(insert(tgd), [
                dict(version=version, grupo=GRUPO_NUEVO, dia=d["dia"],
                     es_canonico=d["es_canonico"], orden=d["orden"],
                     vigente_desde=ahora, vigente=True)
                for d in dias19])
            conn.execute(update(tgs).where(
                tgs.c.grupo == GRUPO_CARRIZAL, tgs.c.vigente == True,
                tgs.c.num_tienda.in_(RINCONADA_NUEVO)).values(vigente=False))
            conn.execute(insert(tgs), [
                dict(version=version, grupo=GRUPO_NUEVO, num_tienda=nt,
                     vigente_desde=ahora, vigente=True)
                for nt in RINCONADA_NUEVO])
            conn.execute(insert(tgs).values(
                version=version, grupo=GRUPO_CARRIZAL, num_tienda=105,
                vigente_desde=ahora, vigente=True))
            conn.execute(update(tg).where(
                tg.c.grupo == GRUPO_CARRIZAL, tg.c.vigente == True
            ).values(tam=len(CARRIZAL_DESPUES)))

            from logic.overrides_admin import registrar_override
            registrar_override(
                "zona_partida", f"grupo:{GRUPO_SA}",
                f"sucursales={act22} tam={g22['tam']}",
                f"sucursales={sorted(SA_DESPUES)} tam={len(SA_DESPUES)}",
                args.motivo, quien, conn=conn)
            registrar_override(
                "zona_partida", f"grupo:{GRUPO_CARRIZAL}",
                f"sucursales={act19} tam={g19['tam']}",
                f"sucursales={sorted(CARRIZAL_DESPUES)} tam={len(CARRIZAL_DESPUES)}; "
                f"nuevo grupo {GRUPO_NUEVO} (zona {ZONA_NUEVA}) sucursales={sorted(RINCONADA_NUEVO)}",
                args.motivo, quien, conn=conn)

        # zona_19 primero (suelta 53 y 40) y luego zona_30, para no chocar
        # con la validacion de colision
        from logic.overrides_admin import cargar_orden_fijo_regla
        for regla, sucs in ((REGLA_SA, SA_DESPUES),
                            (REGLA_CARRIZAL, CARRIZAL_DESPUES),
                            (REGLA_NUEVO, RINCONADA_NUEVO)):
            cargar_orden_fijo_regla(
                regla, [(nt, i) for i, nt in enumerate(sucs, 1)],
                motivo=args.motivo, aplicado_por=quien)

        from logic.plantilla_canonica import obtener_grupos
        grupos = {g["grupo"]: g for g in obtener_grupos()}
        assert sorted(grupos[GRUPO_SA]["sucursales"]) == sorted(SA_DESPUES)
        assert sorted(grupos[GRUPO_CARRIZAL]["sucursales"]) == sorted(CARRIZAL_DESPUES)
        assert sorted(grupos[GRUPO_NUEVO]["sucursales"]) == sorted(RINCONADA_NUEVO)
        print(f"\nOK -- grupo 22={SA_DESPUES}, grupo 19={CARRIZAL_DESPUES}, "
              f"grupo {GRUPO_NUEVO} (zona {ZONA_NUEVA})={RINCONADA_NUEVO}")


if __name__ == "__main__":
    main()
