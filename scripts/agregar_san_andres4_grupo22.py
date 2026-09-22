"""
scripts/agregar_san_andres4_grupo22.py

San Andres 4 (sucursal 103, num_tienda 103) se dio de alta en el catalogo
`sucursales` el 2026-09-09 (junto con la sucursal 102 de Tuxtepec) pero
nunca se agrego a `plantilla_grupo_sucursal` -- cero filas en toda su
historia. Por eso su pedido se extraia bien (ver extraccion_logic.py,
id_sucursal 103 con peso/volumen correctos) pero quedaba huerfano: el
motor de ruteo no sabia a que grupo/camion asignarlo y no aparecia en
ningun PDF de logistica.

El usuario confirmo que San Andres 4 pertenece a la zona 22 (Santiago 1 y
2, San Andres 1/2/3, Catemaco 1 y 2, Covarrubias), inmediatamente despues
de San Andres 3 y antes de Catemaco -- mismo patron no-destructivo que
scripts/dividir_zona5_tuxtepec_dos_grupos.py: parche puntual sobre la
version vigente de plantilla_grupo_sucursal, sin tocar dia/unidad/rigidez
del grupo 22.

Orden fijo de paradas de zona_22 se actualiza aparte con
scripts/admin_overrides.py orden-fijo (mismo patron que el resto de
zonas), no en este script.

Uso:
    python scripts/agregar_san_andres4_grupo22.py --dry-run
    python scripts/agregar_san_andres4_grupo22.py --motivo "..."
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

GRUPO = 22
NUM_TIENDA = 103


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

        fila_grupo = db.execute(
            select(tg.c.version, tg.c.tam).where(tg.c.grupo == GRUPO, tg.c.vigente == True)
        ).mappings().first()
        if not fila_grupo:
            raise SystemExit(f"grupo {GRUPO} no tiene version vigente")
        version, tam_actual = fila_grupo["version"], fila_grupo["tam"]

        ya_existe = db.execute(
            select(tgs.c.num_tienda).where(
                tgs.c.grupo == GRUPO, tgs.c.num_tienda == NUM_TIENDA, tgs.c.vigente == True
            )
        ).first()
        if ya_existe:
            print(f"num_tienda {NUM_TIENDA} ya esta vigente en grupo {GRUPO} -- nada que hacer.")
            return

        antes = sorted(r.num_tienda for r in db.execute(
            select(tgs.c.num_tienda).where(tgs.c.grupo == GRUPO, tgs.c.vigente == True)))
        despues = sorted(antes + [NUM_TIENDA])

        print(f"Grupo {GRUPO} -- version vigente {version}, tam actual {tam_actual}")
        print(f"ANTES:   {antes}")
        print(f"DESPUES: {despues}")

        if args.dry_run:
            print("\n--dry-run: no se escribio nada.")
            return

        ahora = datetime.now().isoformat()
        quien = args.aplicado_por or os.environ.get("USERNAME") or os.environ.get("USER") or getpass.getuser()
        with transaccion() as conn:
            conn.execute(insert(tgs).values(
                version=version, grupo=GRUPO, num_tienda=NUM_TIENDA,
                vigente_desde=ahora, vigente=True,
            ))
            conn.execute(update(tg).where(
                tg.c.grupo == GRUPO, tg.c.vigente == True
            ).values(tam=len(despues)))

            from logic.overrides_admin import registrar_override
            registrar_override(
                "zona_partida", f"grupo:{GRUPO}",
                f"sucursales={antes} tam={tam_actual}",
                f"sucursales={despues} tam={len(despues)}",
                args.motivo, quien, conn=conn,
            )

        from logic.plantilla_canonica import obtener_grupos
        grupos = {g["grupo"]: g for g in obtener_grupos()}
        assert sorted(grupos[GRUPO]["sucursales"]) == despues, \
            f"esperado {despues}, quedo {sorted(grupos[GRUPO]['sucursales'])}"
        print(f"\nOK -- grupo {GRUPO} ahora incluye sucursal {NUM_TIENDA}. "
              "Falta actualizar el orden fijo de paradas (zona_22) con "
              "scripts/admin_overrides.py orden-fijo.")


if __name__ == "__main__":
    main()
