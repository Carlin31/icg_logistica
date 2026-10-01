"""
scripts/dividir_zona22_santiago.py

Parte la zona 22 (grupo 22, ruta de F 350_3: Santiago Tuxtla, San Andres,
Catemaco y Covarrubias, 9 sucursales) en dos grupos, por pedido de los
jefes de practicas (2026-10-01):

  - grupo 29 (zona 25, NUEVA): Santiago Tuxtla 1 y 2 (sucursales 32, 78)
  - grupo 22 (zona 22):        San Andres 1-4 (90, 91, 98, 103), Catemaco 1 y 2
                               (39, 96) y Juan Diaz Covarrubias (45)

Grupo 29 hereda de grupo 22 rigidez (RIGIDO) y dias (MARTES preferido,
admite JUEVES); NO hereda unidad_ref/afinidad (F 350_3): sus 2 sucursales
pesan ~1.1 t y el motor elige el camion por volumen/peso, ya no por
unidad_ref (vestigial). Grupo 22 conserva todo (rigidez, dias, afinidad
F 350_3) y solo baja de 9 a 7 sucursales.

Se usa el numero de grupo 29 y no 28 porque (version 43, grupo 28) ya existe
como fila retirada (tercer grupo de Tierra Blanca, ver
scripts/dividir_zona11_dos_grupos.py).

Orden fijo de paradas (reglas zona_22 y zona_29) se actualiza con
logic.overrides_admin.cargar_orden_fijo_regla -- primero zona_22 sin
Santiago, luego zona_29, para no chocar con la validacion de colision:
    zona_29: 32 Santiago Tuxtla 1 (1), 78 Santiago Tuxtla 2 (2)
    zona_22: 90 (1), 91 (2), 98 (3), 103 (4), 39 (5), 96 (6), 45 (7)
(mismo orden relativo que ya tenia zona_22, sin las dos de Santiago)

Parche puntual no-destructivo sobre la version vigente, mismo patron que
scripts/dividir_zona5_tuxtepec_dos_grupos.py.

Uso:
    python scripts/dividir_zona22_santiago.py --dry-run
    python scripts/dividir_zona22_santiago.py --motivo "..."
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

GRUPO_RESTO = 22
GRUPO_NUEVO = 29
ZONA_NUEVA = 25
SUCURSALES_NUEVO = [32, 78]                       # Santiago Tuxtla 1, 2
SUCURSALES_RESTO = [90, 91, 98, 103, 39, 96, 45]  # en orden de visita
REGLA_RESTO = "zona_22"
REGLA_NUEVO = "zona_29"


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
            select(tg).where(tg.c.grupo == GRUPO_RESTO, tg.c.vigente == True)
        ).mappings().first()
        if not g22:
            raise SystemExit(f"grupo {GRUPO_RESTO} no tiene version vigente")
        version = g22["version"]

        if db.execute(select(tg.c.grupo).where(
                tg.c.version == version, tg.c.grupo == GRUPO_NUEVO)).first():
            raise SystemExit(f"(version {version}, grupo {GRUPO_NUEVO}) ya existe -- "
                             f"el parche ya se aplico o el numero esta ocupado.")

        actuales = sorted(r.num_tienda for r in db.execute(
            select(tgs.c.num_tienda).where(
                tgs.c.grupo == GRUPO_RESTO, tgs.c.vigente == True)))
        esperado = sorted(SUCURSALES_NUEVO + SUCURSALES_RESTO)
        if actuales != esperado:
            raise SystemExit(f"grupo {GRUPO_RESTO} hoy tiene {actuales}, se esperaba "
                             f"{esperado} -- revisa con scripts/listar_overrides.py")

        dias = db.execute(
            select(tgd).where(tgd.c.grupo == GRUPO_RESTO, tgd.c.vigente == True)
        ).mappings().all()

        print(f"Version vigente: {version}")
        print(f"ANTES:   grupo {GRUPO_RESTO} = {actuales} (tam {g22['tam']})")
        print(f"DESPUES: grupo {GRUPO_RESTO} = {sorted(SUCURSALES_RESTO)} (tam {len(SUCURSALES_RESTO)})")
        print(f"         grupo {GRUPO_NUEVO} (zona {ZONA_NUEVA}) = {sorted(SUCURSALES_NUEVO)} "
              f"(tam {len(SUCURSALES_NUEVO)}), {g22['rigidez']}, dias "
              f"{[d['dia'] for d in sorted(dias, key=lambda d: d['orden'])]}")
        print(f"Orden fijo: {REGLA_NUEVO} = {SUCURSALES_NUEVO}; {REGLA_RESTO} = {SUCURSALES_RESTO}")

        if args.dry_run:
            print("\n--dry-run: no se escribio nada.")
            return

        ahora = datetime.now().isoformat()
        quien = args.aplicado_por or os.environ.get("USERNAME") or os.environ.get("USER") or getpass.getuser()
        with transaccion() as conn:
            conn.execute(insert(tg).values(
                version=version, grupo=GRUPO_NUEVO, rigidez=g22["rigidez"],
                dia=g22["dia"], tam=len(SUCURSALES_NUEVO), cohesion=None,
                unidad_ref=None, que_hace_vrp=None, vigente_desde=ahora,
                vigente=True, unidades_afines=None, unidad_forzada=False,
                zona=ZONA_NUEVA, unidades_excluidas=None, exclusivo=g22["exclusivo"],
            ))
            conn.execute(insert(tgd), [
                dict(version=version, grupo=GRUPO_NUEVO, dia=d["dia"],
                     es_canonico=d["es_canonico"], orden=d["orden"],
                     vigente_desde=ahora, vigente=True)
                for d in dias
            ])
            conn.execute(update(tgs).where(
                tgs.c.grupo == GRUPO_RESTO, tgs.c.vigente == True,
                tgs.c.num_tienda.in_(SUCURSALES_NUEVO)
            ).values(vigente=False))
            conn.execute(insert(tgs), [
                dict(version=version, grupo=GRUPO_NUEVO, num_tienda=nt,
                     vigente_desde=ahora, vigente=True)
                for nt in SUCURSALES_NUEVO
            ])
            conn.execute(update(tg).where(
                tg.c.grupo == GRUPO_RESTO, tg.c.vigente == True
            ).values(tam=len(SUCURSALES_RESTO)))

            from logic.overrides_admin import registrar_override
            registrar_override(
                "zona_partida", f"grupo:{GRUPO_RESTO}",
                f"sucursales={actuales} tam={g22['tam']}",
                f"sucursales={sorted(SUCURSALES_RESTO)} tam={len(SUCURSALES_RESTO)}; "
                f"nuevo grupo {GRUPO_NUEVO} (zona {ZONA_NUEVA}) sucursales={sorted(SUCURSALES_NUEVO)}",
                args.motivo, quien, conn=conn,
            )

        from logic.overrides_admin import cargar_orden_fijo_regla
        for regla, sucs in ((REGLA_RESTO, SUCURSALES_RESTO), (REGLA_NUEVO, SUCURSALES_NUEVO)):
            cargar_orden_fijo_regla(
                regla, [(nt, i) for i, nt in enumerate(sucs, 1)],
                motivo=args.motivo, aplicado_por=quien)

        from logic.plantilla_canonica import obtener_grupos
        grupos = {g["grupo"]: g for g in obtener_grupos()}
        assert sorted(grupos[GRUPO_RESTO]["sucursales"]) == sorted(SUCURSALES_RESTO)
        assert sorted(grupos[GRUPO_NUEVO]["sucursales"]) == sorted(SUCURSALES_NUEVO)
        print(f"\nOK -- zona 22 partida: grupo {GRUPO_RESTO}={SUCURSALES_RESTO}, "
              f"grupo {GRUPO_NUEVO} (zona {ZONA_NUEVA})={SUCURSALES_NUEVO}")


if __name__ == "__main__":
    main()
