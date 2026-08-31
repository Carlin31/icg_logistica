"""
afinidad_grupo27_tb1_tb4.py

Fija la afinidad de grupo 27 (Tierra Blanca 1 y 4, sucursales 1 y 36 --
tambien incluye 63 y 101 desde la particion en 2 grupos del 2026-08-31,
ver scripts/dividir_zona11_dos_grupos.py) a T 25, T 23, T 20, K 20 y K 16,
en ese orden de preferencia.

Contexto (pedido de los jefes de practicas, via el usuario, 2026-08-31):
cuando esten las sucursales de Tierra Blanca 1 y 4, usar de preferencia
esos 5 vehiculos. K 16 y K 20 son 2.5t nominal -- MISMO nivel de capacidad
que T 17_1, T 17_2, J 18 y J 19 (ver logic/vrp_logic.py:capacidad_efectiva_kg,
ninguno tiene excepcion CAP-4/CAP-1.5). La afinidad (`unidades_afines`) solo
desempata DENTRO del mismo nivel de capacidad (confirmado: ver
convrp_integracion.py y tests/test_convrp_logic.py::test_afinidad_no_gana_sobre_capacidad_distinta) --
nunca elige un camion mas grande que el que toca por peso. Por eso este
parche es exactamente lo que hace falta: cuando grupo 27 pese lo bastante
para necesitar un camion de 2.5t, esta afinidad hace que el motor prefiera
K 16/K 20 sobre T 17_1/T 17_2/J 18/J 19 en el desempate. Para pesos que
caben en 1.5t, T 25/T 23/T 20 ya eran los primeros candidatos por orden de
capacidad ascendente (esto solo formaliza cual de los 3 se prefiere primero
si hay empate real).

NO toca unidades_excluidas (F 350_1/2/3 se mantienen excluidos, ya estaba
asi desde la reorganizacion de Zona 11).

Parche puntual sobre la version vigente (43): solo actualiza el campo
unidades_afines de la fila plantilla_grupo de grupo 27, sin crear version
nueva ni tocar ningun otro grupo.

Uso:
    python scripts/afinidad_grupo27_tb1_tb4.py --dry-run   # solo muestra
    python scripts/afinidad_grupo27_tb1_tb4.py              # escribe
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
from db import get_db, get_table, transaccion
from app import create_app

GRUPO = 27
AFINIDAD = "T 25:5 | T 23:4 | T 20:3 | K 20:2 | K 16:1"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    app = create_app()
    with app.app_context():
        db = get_db()
        tg = get_table("plantilla_grupo")

        fila = db.execute(
            select(tg).where(tg.c.grupo == GRUPO, tg.c.vigente == True)
        ).mappings().first()
        print("ANTES:", dict(fila))

        if args.dry_run:
            print("\n--dry-run: no se escribio nada.")
            return

        with transaccion() as conn:
            conn.execute(update(tg).where(
                tg.c.grupo == GRUPO, tg.c.vigente == True
            ).values(unidades_afines=AFINIDAD))

        from logic.plantilla_canonica import obtener_grupos
        g27 = {g["grupo"]: g for g in obtener_grupos()}[GRUPO]
        print("\nDESPUES:", g27)
        assert g27["unidades_afines"] == AFINIDAD
        assert set(g27["unidades_excluidas"]) == {"F 350_1", "F 350_2", "F 350_3"}
        print("\nOK -- grupo 27 ahora prefiere T25 > T23 > T20 > K20 > K16")


if __name__ == "__main__":
    main()
