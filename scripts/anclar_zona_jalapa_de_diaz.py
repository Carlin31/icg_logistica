"""
anclar_zona_jalapa_de_diaz.py

Reactiva la capa de zona de mayoristas SOLO para la zona JALAPA DE DIAZ, para
que sus mayoristas se enganchen a la ruta de las sucursales de Jalapa de Diaz
por historia y no por la ruta que les quede mas cerca en linea recta esa
semana.

Contexto (usuario, 2026-09-04): BB4145_FARMA PRONTO JALAPA (cliente 555) salio
en la ruta de JUEVES de T 23 (Tuxtepec) en el PDF del 24-28 de agosto, cuando
sus coordenadas (18.071048 / -96.537577) estan a ~55 m de la sucursal 35 (San
Felipe Jalapa de Diaz) y a 43 km de Tuxtepec.

Por que un ancla y no el arreglo de fondo: la capa de zona completa lleva
inactiva desde el 2026-08-12 (`plantilla_poblacion_zona` quedo con 0 filas
vigentes al cargarse las versiones 16/17 sin el CSV de poblaciones, y ninguna
zona vigente conserva `grupo_nucleo`), asi que HOY el 100% de los mayoristas se
engancha por geografia. Restaurarla entera moveria 30 clientes de ruta --
incluidos COTAXTLA (pct_nucleo 0.35) y TLACOJALPAN (2 semanas), que desharian
los arreglos ya validados de RASTROVIA en T 17_1 LUNES y de ABARROTES EL GUERO
en T 20 JUEVES. El usuario decidio (2026-09-04) mover solo Jalapa de Diaz.

Alcance real: `_construir_cache_zonas` solo enganche a las poblaciones que
esten en `plantilla_poblacion_zona` vigente Y cuya zona tenga `grupo_nucleo`.
Al dejar vigentes unicamente las 3 poblaciones de esta zona, el resto de los
mayoristas cae igual que hoy a `_seleccionar_ruta` (geografia): cero cambios
fuera de Jalapa de Diaz.

Evidencia de la zona (calculada desde rutas_historicas, numeracion de grupos
vigente): grupo nucleo 7, pct_nucleo 1.00, 5 semanas, 33 paradas -> confianza
ALTA. Es la regla mas firme del catalogo, no una corazonada.

Uso:
    python scripts/anclar_zona_jalapa_de_diaz.py --dry-run   # solo muestra
    python scripts/anclar_zona_jalapa_de_diaz.py             # escribe
"""
import sys, os, argparse
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy import select, insert, update, delete
from app import create_app
from db import get_db, get_table, transaccion

ZONA = "JALAPA DE DIAZ"
# Poblaciones de datos/mapeo_poblacion_a_zona.csv cuya zona es JALAPA DE DIAZ.
# Se listan explicitas (y no se lee el CSV entero) justamente para que el
# alcance del ancla sea evidente al leer el script.
POBLACIONES = [
    ("JALAPA DE DIAZ",            "alta"),
    ("JALAPA",                    "alta"),   # alias real en clientes_mayoristas
    ("SAN FELIPE JALAPA DE DIAZ", "alta"),
]


def _evidencia(db):
    """grupo_nucleo/pct/semanas/paradas de la zona, desde el historico real."""
    from logic.enganche_zona import _norm
    from logic.plantilla_canonica import evidencia_zonas_desde_historico, obtener_grupos

    objetivo = {_norm(p) for p, _ in POBLACIONES}
    zona_de_cliente = {}
    for c in db.execute(select(get_table("clientes_mayoristas"))).mappings():
        if c["id_cliente"] is None:
            continue
        if _norm(c["poblacion"]) in objetivo:
            zona_de_cliente[str(int(c["id_cliente"]))] = ZONA

    grupo_de_sucursal = {}
    for g in obtener_grupos():
        for s in g.get("sucursales", []):
            grupo_de_sucursal[int(s)] = int(g["grupo"])

    return zona_de_cliente, evidencia_zonas_desde_historico(zona_de_cliente, grupo_de_sucursal).get(ZONA)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    app = create_app()
    with app.app_context():
        db = get_db()
        from logic.enganche_zona import confianza_zona
        from logic.plantilla_canonica import version_vigente

        zona_de_cliente, ev = _evidencia(db)
        print(f"clientes mayoristas en la zona {ZONA}: {len(zona_de_cliente)}")
        if not ev or ev.get("grupo_nucleo") is None:
            print(f"ABORTADO: la zona {ZONA} no tiene grupo nucleo en el historico; "
                  f"sin destino de enganche no se ancla nada (no se adivina).")
            return 1

        conf = confianza_zona(ev["semanas"], ev["pct_nucleo"])
        print(f"evidencia: nucleo=grupo {ev['grupo_nucleo']}  pct={ev['pct_nucleo']:.2f}  "
              f"semanas={ev['semanas']}  paradas={ev['paradas']}  confianza={conf}")
        if conf == "BAJA":
            print("ABORTADO: confianza BAJA (<3 semanas o nucleo <0.60); no es regla "
                  "firme y no debe ganarle a la geografia.")
            return 1

        ver = version_vigente()
        ahora = datetime.now().isoformat()
        print(f"\nse escribiria (version {ver}):")
        for p, c in POBLACIONES:
            print(f"   plantilla_poblacion_zona: {p!r} -> {ZONA!r} (confianza {c}, vigente)")
        print(f"   plantilla_zona_mayorista[{ZONA}].grupo_nucleo = {ev['grupo_nucleo']}, "
              f"pct_nucleo = {ev['pct_nucleo']}, confianza = {conf}")

        if args.dry_run:
            print("\n--dry-run: no se escribio nada.")
            return 0

        tp = get_table("plantilla_poblacion_zona")
        tz = get_table("plantilla_zona_mayorista")
        with transaccion() as conn:
            # Idempotente: se borran las filas vigentes de estas poblaciones
            # antes de reinsertarlas. No se toca ninguna otra poblacion.
            for p, _ in POBLACIONES:
                conn.execute(delete(tp).where(tp.c.poblacion == p, tp.c.vigente == True))
            conn.execute(insert(tp), [
                dict(version=ver, poblacion=p, zona=ZONA, confianza=c,
                     vigente_desde=ahora, vigente=1)
                for p, c in POBLACIONES
            ])
            conn.execute(update(tz).where(tz.c.zona == ZONA, tz.c.vigente == True).values(
                grupo_nucleo=ev["grupo_nucleo"], pct_nucleo=ev["pct_nucleo"],
                semanas=ev["semanas"], paradas=ev["paradas"], confianza=conf))

        print("\nlisto.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
