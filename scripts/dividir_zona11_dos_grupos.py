"""
dividir_zona11_dos_grupos.py

Reduce Tierra Blanca (Zona 11) de 3 grupos (11 Norte, 27 Centro, 28 Sur;
3+3+2 sucursales) a 2, fusionando grupo 28 en los otros dos. Parche
puntual sobre la versión vigente (43) -- mismo patrón no-destructivo que
scripts/mover_dia_preferido_grupo_17.py: NO llama a cargar_zonas_manual
(eso crearía una versión nueva completa y pisaría los ajustes puntuales ya
aplicados a otros grupos, p. ej. el día preferido de grupo 17). Solo toca
las filas de plantilla_grupo/plantilla_grupo_sucursal/plantilla_grupo_dia
de los grupos 11, 27 y 28 -- ningún otro grupo/zona se altera.

Contexto (pedido del usuario 2026-08-31, ver
docs/superpowers/specs/2026-08-26-asignacion-vehiculos-por-peso-design.md
para las bandas de peso T25/T23/T20 vs F350): el split de 3 grupos evitaba
F350 (`unidades_excluidas`) pero el usuario pidió volver a 2, siempre que
ningún grupo caiga en rango F350. Sobre las 13 semanas reales en
`rutas_historicas`, la única combinación de 2 grupos que preserva los pares
de coasignación histórica más fuertes (24,25 juntos 12/13 semanas; 76,77
juntos 12/13 semanas) Y minimiza el riesgo de F350 es:

    grupo 11 (zona 11): sucursales 24, 25, 76, 77  -- 0/13 semanas >2549 kg
                         (avg 2195 kg, máx 2547 kg -- nunca F350)
    grupo 27 (zona 11): sucursales 1, 36, 63, 101   -- 3/13 semanas >2549 kg
                         (avg 2439 kg, máx 3815 kg -- absorbido por
                         PARTIDO_CAPACIDAD igual que hoy absorbe el
                         desborde ocasional de grupo 27 "Centro")

Esto es estrictamente mejor que el split de 2 grupos original (abandonado
el 2026-08-27): ese tenía un grupo que caía en rango F350 las 13/13
semanas, siempre.

Uso:
    python scripts/dividir_zona11_dos_grupos.py --dry-run   # solo muestra
    python scripts/dividir_zona11_dos_grupos.py              # escribe
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

GRUPO_NORTE  = 11   # queda con 24, 25, 76, 77
GRUPO_CENTRO = 27   # queda con 1, 36, 63, 101
GRUPO_SUR    = 28   # se retira -- sus sucursales 63/76 se reparten arriba

MOVER_A_NORTE  = [76]   # de grupo 28 a grupo 11
MOVER_A_CENTRO = [63]   # de grupo 28 a grupo 27


def _mostrar(db, tgs, tg):
    for g in (GRUPO_NORTE, GRUPO_CENTRO, GRUPO_SUR):
        sucs = sorted(r.num_tienda for r in db.execute(
            select(tgs.c.num_tienda).where(tgs.c.grupo == g, tgs.c.vigente == True)))
        row = db.execute(
            select(tg.c.tam, tg.c.vigente).where(tg.c.grupo == g, tg.c.vigente == True)
        ).mappings().first()
        print(f"   grupo {g}: sucursales={sucs}  tam(BD)={row['tam'] if row else '-'}  "
              f"vigente={'si' if row else 'no (retirado)'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    app = create_app()
    with app.app_context():
        db  = get_db()
        tg  = get_table("plantilla_grupo")
        tgs = get_table("plantilla_grupo_sucursal")
        tgd = get_table("plantilla_grupo_dia")

        version = db.execute(
            select(tg.c.version).where(tg.c.grupo == GRUPO_NORTE, tg.c.vigente == True)
        ).scalar()
        print(f"Version vigente detectada: {version}")

        print("\nANTES:")
        _mostrar(db, tgs, tg)

        if args.dry_run:
            print("\n--dry-run: no se escribio nada.")
            return

        ahora = datetime.now().isoformat()
        with transaccion() as conn:
            # 1) Retirar las sucursales de grupo 28 que se van a reasignar.
            conn.execute(update(tgs).where(
                tgs.c.grupo == GRUPO_SUR, tgs.c.vigente == True,
                tgs.c.num_tienda.in_(MOVER_A_NORTE + MOVER_A_CENTRO)
            ).values(vigente=False))

            # 2) Insertarlas en sus nuevos grupos.
            conn.execute(insert(tgs), [
                dict(version=version, grupo=GRUPO_NORTE, num_tienda=nt,
                     vigente_desde=ahora, vigente=True)
                for nt in MOVER_A_NORTE
            ] + [
                dict(version=version, grupo=GRUPO_CENTRO, num_tienda=nt,
                     vigente_desde=ahora, vigente=True)
                for nt in MOVER_A_CENTRO
            ])

            # 3) Actualizar el tamaño (tam) de los grupos que crecen.
            conn.execute(update(tg).where(
                tg.c.grupo == GRUPO_NORTE, tg.c.vigente == True
            ).values(tam=4))
            conn.execute(update(tg).where(
                tg.c.grupo == GRUPO_CENTRO, tg.c.vigente == True
            ).values(tam=4))

            # 4) Retirar grupo 28 por completo (grupo, sucursales restantes, dia).
            conn.execute(update(tgs).where(
                tgs.c.grupo == GRUPO_SUR, tgs.c.vigente == True
            ).values(vigente=False))
            conn.execute(update(tgd).where(
                tgd.c.grupo == GRUPO_SUR, tgd.c.vigente == True
            ).values(vigente=False))
            conn.execute(update(tg).where(
                tg.c.grupo == GRUPO_SUR, tg.c.vigente == True
            ).values(vigente=False))

        print("\nDESPUES:")
        _mostrar(db, tgs, tg)

        from logic.plantilla_canonica import obtener_grupos
        grupos = {g["grupo"]: g for g in obtener_grupos()}
        assert GRUPO_SUR not in grupos, "grupo 28 sigue vigente"
        assert sorted(grupos[GRUPO_NORTE]["sucursales"]) == [24, 25, 76, 77]
        assert sorted(grupos[GRUPO_CENTRO]["sucursales"]) == [1, 36, 63, 101]
        print("\nOK -- Zona 11 ahora en 2 grupos: "
              f"{GRUPO_NORTE}={grupos[GRUPO_NORTE]['sucursales']}  "
              f"{GRUPO_CENTRO}={grupos[GRUPO_CENTRO]['sucursales']}")


if __name__ == "__main__":
    main()
