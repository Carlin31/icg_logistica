"""
dividir_zona5_tuxtepec_dos_grupos.py

Reorganiza Tuxtepec (Zona 5) de 3 grupos (5, 25, 26; 6+3+1 sucursales) a 2
(5, 25; 6+5), con el orden real de visita que dieron los jefes de prácticas
el 2026-09-14. Parche puntual sobre la versión vigente -- mismo patrón
no-destructivo que scripts/dividir_zona11_dos_grupos.py: NO llama a
cargar_zonas_manual (crearía una versión nueva completa y pisaría ajustes
puntuales ya aplicados a otros grupos). Solo toca
plantilla_grupo/plantilla_grupo_sucursal de los grupos 5, 25 y 26.

Contexto: el orden fijo lo dieron los jefes en dos bloques con nombre
("Tuxtepec 1" .. "Tuxtepec 11"), que NO corresponden 1 a 1 con la
numeración vieja de sucursal en BD (p.ej. "Tuxtepec 5 (Hacienda)" del
pedido es la sucursal 55, que en BD se llama "Tuxtepec 7 (Hacienda)"). El
mapeo nombre->num_tienda se resolvió por nombre_base en la tabla
`sucursales`:

    Grupo 5  (Tuxtepec 1-6, "Centro"):
        1 Centro                   -> 2
        2 Hidalgo                  -> 31
        3 18 de marzo               -> 102  (sucursal nueva, sin grupo hasta hoy)
        4 Sam Bartolo (San Bartolo) -> 74   (hoy en grupo 26)
        5 Hacienda                  -> 55
        6 Bulevar                   -> 7

    Grupo 25 (Tuxtepec 7-11, "Hidalgo/oriente"):
        7 Jardines del Arroyo             -> 54  (hoy en grupo 5)
        8 20 Nov                          -> 46
        9 Prolongación-Independencia      -> 15  (hoy en grupo 5)
        10 Las Limas                      -> 57
        11 23 de noviembre                -> 38

Grupo 26 (antes solo sucursal 74) se retira -- su única sucursal pasa a
formar parte de grupo 5. El día/unidad de cada grupo NO cambia (se
confirmó con el usuario mantener lo que ya tenían):
  - grupo 5:  MARTES, admite MARTES/MIÉRCOLES, FLEXIBLE, F 350_2
  - grupo 25: JUEVES rígido, exclusivo, F 350_2

El orden de visita (posición 1..6 / 1..5 de arriba) se carga aparte en
datos/orden_fijo_paradas.csv bajo las reglas "zona_5" y "zona_25" (mismo
patrón que el resto de zonas simples) -- correr después:
    python scripts/cargar_orden_fijo.py

Uso:
    python scripts/dividir_zona5_tuxtepec_dos_grupos.py --dry-run   # solo muestra
    python scripts/dividir_zona5_tuxtepec_dos_grupos.py              # escribe
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

GRUPO_A = 5    # Tuxtepec 1-6 (Centro)
GRUPO_B = 25   # Tuxtepec 7-11 (Hidalgo/oriente)
GRUPO_RETIRADO = 26

SUCURSALES_A = [2, 31, 102, 74, 55, 7]
SUCURSALES_B = [54, 46, 15, 57, 38]

# num_tienda -> grupo actual (antes del parche), para calcular altas/bajas
ACTUAL_A = [2, 7, 15, 31, 54, 55]
ACTUAL_B = [38, 46, 57]
ACTUAL_RETIRADO = [74]


def _mostrar(db, tgs, tg):
    for g in (GRUPO_A, GRUPO_B, GRUPO_RETIRADO):
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
        db = get_db()
        tg = get_table("plantilla_grupo")
        tgs = get_table("plantilla_grupo_sucursal")
        tgd = get_table("plantilla_grupo_dia")

        version = db.execute(
            select(tg.c.version).where(tg.c.grupo == GRUPO_A, tg.c.vigente == True)
        ).scalar()
        print(f"Version vigente detectada: {version}")

        print("\nANTES:")
        _mostrar(db, tgs, tg)

        if args.dry_run:
            print(f"\nDESPUES (esperado): grupo {GRUPO_A}={sorted(SUCURSALES_A)}  "
                  f"grupo {GRUPO_B}={sorted(SUCURSALES_B)}  grupo {GRUPO_RETIRADO}=retirado")
            print("\n--dry-run: no se escribio nada.")
            return

        ahora = datetime.now().isoformat()
        with transaccion() as conn:
            # 1) Retirar de grupo A las sucursales que se van a grupo B.
            quitar_de_a = [s for s in ACTUAL_A if s not in SUCURSALES_A]
            if quitar_de_a:
                conn.execute(update(tgs).where(
                    tgs.c.grupo == GRUPO_A, tgs.c.vigente == True,
                    tgs.c.num_tienda.in_(quitar_de_a)
                ).values(vigente=False))

            # 2) Retirar por completo grupo 26 (grupo, sucursales, dia).
            conn.execute(update(tgs).where(
                tgs.c.grupo == GRUPO_RETIRADO, tgs.c.vigente == True
            ).values(vigente=False))
            conn.execute(update(tgd).where(
                tgd.c.grupo == GRUPO_RETIRADO, tgd.c.vigente == True
            ).values(vigente=False))
            conn.execute(update(tg).where(
                tg.c.grupo == GRUPO_RETIRADO, tg.c.vigente == True
            ).values(vigente=False))

            # 3) Insertar en grupo A las sucursales nuevas (102 nunca asignada,
            #    74 viene de grupo 26).
            agregar_a_a = [s for s in SUCURSALES_A if s not in ACTUAL_A]
            if agregar_a_a:
                conn.execute(insert(tgs), [
                    dict(version=version, grupo=GRUPO_A, num_tienda=nt,
                         vigente_desde=ahora, vigente=True)
                    for nt in agregar_a_a
                ])

            # 4) Insertar en grupo B las sucursales que llegan de grupo A.
            agregar_a_b = [s for s in SUCURSALES_B if s not in ACTUAL_B]
            if agregar_a_b:
                conn.execute(insert(tgs), [
                    dict(version=version, grupo=GRUPO_B, num_tienda=nt,
                         vigente_desde=ahora, vigente=True)
                    for nt in agregar_a_b
                ])

            # 5) Actualizar tam de A y B.
            conn.execute(update(tg).where(
                tg.c.grupo == GRUPO_A, tg.c.vigente == True
            ).values(tam=len(SUCURSALES_A)))
            conn.execute(update(tg).where(
                tg.c.grupo == GRUPO_B, tg.c.vigente == True
            ).values(tam=len(SUCURSALES_B)))

        print("\nDESPUES:")
        _mostrar(db, tgs, tg)

        from logic.plantilla_canonica import obtener_grupos
        grupos = {g["grupo"]: g for g in obtener_grupos()}
        assert GRUPO_RETIRADO not in grupos, f"grupo {GRUPO_RETIRADO} sigue vigente"
        assert sorted(grupos[GRUPO_A]["sucursales"]) == sorted(SUCURSALES_A)
        assert sorted(grupos[GRUPO_B]["sucursales"]) == sorted(SUCURSALES_B)
        print(f"\nOK -- Zona 5 (Tuxtepec) ahora en 2 grupos: "
              f"{GRUPO_A}={grupos[GRUPO_A]['sucursales']}  "
              f"{GRUPO_B}={grupos[GRUPO_B]['sucursales']}")


if __name__ == "__main__":
    main()
