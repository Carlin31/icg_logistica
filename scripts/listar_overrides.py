"""
scripts/listar_overrides.py

Diagnostico de solo lectura: junta en un solo lugar todos los parches/
excepciones puntuales activos sobre un grupo, sucursal o zona de la
plantilla canonica -- hoy viven repartidos en plantilla_grupo
(unidades_afines/unidades_excluidas), plantilla_grupo_dia (dia preferido),
orden_fijo_paradas, ancla_mayoristas y grupo_fijo_mayoristas, sin un lugar
que los muestre juntos.

Pensado para correr ANTES de aplicar un parche puntual nuevo (nuevo dia
preferido, nueva afinidad, nueva zona partida, etc.) y ver de un vistazo
que otras reglas ya tocan ese mismo grupo/zona -- asi como paso el bug de
`_vigente_restaurada` y el de zona 23 orden_fijo invertido, donde un cambio
no sabia que pisaba un parche anterior.

Tambien muestra el historial de overrides_auditoria (motivo/quien/cuando)
para lo que se haya aplicado con scripts/admin_overrides.py -- los
overrides mas viejos, aplicados por los scripts de un solo uso de antes de
que existiera la auditoria, no tienen entrada ahi (mismo criterio que
migrar_auditoria_historico.py: sin registro no es lo mismo que sin motivo).

No escribe nada en la base.

Uso:
    python scripts/listar_overrides.py --grupo 27
    python scripts/listar_overrides.py --grupo 27 --grupo 11
    python scripts/listar_overrides.py --sucursal 1
    python scripts/listar_overrides.py --zona 11
"""
import sys
import os
import argparse

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy import select

from db import get_db, get_table
from app import create_app
from logic.overrides_admin import historial as historial_overrides


def _nombres_sucursales(db, num_tiendas):
    if not num_tiendas:
        return {}
    t = get_table("sucursales")
    filas = db.execute(select(t)).mappings().all()
    out = {}
    for f in filas:
        nt = f.get("num_tienda")
        if nt in num_tiendas:
            out[nt] = (f.get("nombre_base") or f.get("nombre_icg-proalmex")
                        or f.get("nombre_bimbo") or str(nt))
    return out


def _nombres_mayoristas(db, ids):
    if not ids:
        return {}
    t = get_table("clientes_mayoristas")
    filas = db.execute(select(t.c.id_cliente, t.c.nombre)).mappings().all()
    return {int(f["id_cliente"]): f["nombre"] for f in filas if int(f["id_cliente"]) in ids}


def mostrar_grupo(db, grupos_por_numero, grupo):
    g = grupos_por_numero.get(grupo)
    if not g:
        print(f"\nGrupo {grupo}: no existe en la plantilla vigente.")
        return

    sucursales = set(g["sucursales"])
    nombres = _nombres_sucursales(db, sucursales)

    print(f"\n=== Grupo {grupo} -- zona {g.get('zona') if g.get('zona') is not None else '(sin zona)'} ===")
    print(f"Sucursales ({len(sucursales)}): " +
          ", ".join(f"{nt} {nombres.get(nt, '')}".strip() for nt in sorted(sucursales)))
    print(f"Rigidez: {g['rigidez']} | Unidad ref: {g['unidad_ref']}")

    dia_override = bool(g["dias_admisibles"]) and g["dia_preferido"] != g["dias_admisibles"][0]
    print(f"Dias admisibles: {g['dias_admisibles']} | preferido: {g['dia_preferido']}"
          + ("  <- OVERRIDE (preferido distinto del primer admisible)" if dia_override else ""))

    if g["unidades_afines"]:
        print(f"Afinidad de camion (OVERRIDE): {g['unidades_afines']}")
    if g["unidades_excluidas"]:
        print(f"Camiones excluidos (OVERRIDE): {g['unidades_excluidas']}")

    tof = get_table("orden_fijo_paradas")
    filas_of = db.execute(select(tof)).mappings().all()
    of_grupo = [f for f in filas_of if int(f["num_tienda"]) in sucursales]
    if of_grupo:
        reglas = sorted(set(f["nombre_regla"] for f in of_grupo))
        print(f"Orden fijo de paradas (OVERRIDE): regla(s) {reglas}, "
              f"{len(of_grupo)}/{len(sucursales)} sucursales cubiertas")

    tam = get_table("ancla_mayoristas")
    anclas = db.execute(select(tam)).mappings().all()
    anclas_grupo = [a for a in anclas if int(a["num_tienda"]) in sucursales]
    tgm = get_table("grupo_fijo_mayoristas")
    pines = db.execute(select(tgm)).mappings().all()
    pines_grupo = [p for p in pines if int(p["grupo"]) == grupo]

    ids_mayoristas = {int(a["id_cliente"]) for a in anclas_grupo} | {int(p["id_cliente"]) for p in pines_grupo}
    nombres_may = _nombres_mayoristas(db, ids_mayoristas)

    if anclas_grupo:
        print("Anclas de mayorista (OVERRIDE de orden):")
        for a in anclas_grupo:
            idc = int(a["id_cliente"])
            nt = int(a["num_tienda"])
            print(f"   {nombres_may.get(idc, idc)} -> despues de sucursal {nt} {nombres.get(nt, '')}")

    if pines_grupo:
        print("Mayoristas fijados a este grupo (OVERRIDE de asignacion):")
        for p in pines_grupo:
            idc = int(p["id_cliente"])
            print(f"   {nombres_may.get(idc, idc)}")

    zona = g.get("zona")
    if zona is not None:
        hermanos = sorted(n for n, gg in grupos_por_numero.items()
                           if gg.get("zona") == zona and n != grupo)
        if hermanos:
            print(f"Zona {zona} esta repartida en mas de un grupo: {sorted([grupo] + hermanos)}")

    if not (g["unidades_afines"] or g["unidades_excluidas"] or of_grupo
            or anclas_grupo or pines_grupo or dia_override
            or (zona is not None and any(gg.get("zona") == zona for n, gg in grupos_por_numero.items() if n != grupo))):
        print("(sin overrides puntuales activos sobre este grupo)")

    claves = {f"grupo:{grupo}"}
    claves.update(f"regla:{r}" for r in {f["nombre_regla"] for f in of_grupo})
    claves.update(f"cliente:{idc}" for idc in ids_mayoristas)

    eventos = [ev for clave in claves for ev in historial_overrides(clave=clave, db=db)]
    if eventos:
        eventos.sort(key=lambda ev: ev["aplicado_en"], reverse=True)
        print("Historial (overrides_auditoria):")
        for ev in eventos:
            fecha = ev["aplicado_en"].strftime("%Y-%m-%d %H:%M")
            quien = f" [{ev['aplicado_por']}]" if ev.get("aplicado_por") else ""
            print(f"   {fecha}  {ev['tipo']:<20} {ev['clave']:<14} "
                  f"{ev['valor_anterior']} -> {ev['valor_nuevo']}  ({ev['motivo']}){quien}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--grupo", type=int, action="append", help="numero de grupo (repetible)")
    ap.add_argument("--sucursal", type=int, help="num_tienda: resuelve el grupo que la contiene")
    ap.add_argument("--zona", type=int, help="numero de zona: lista todos sus grupos")
    args = ap.parse_args()

    if not (args.grupo or args.sucursal is not None or args.zona is not None):
        ap.error("pasa --grupo, --sucursal o --zona")

    app = create_app()
    with app.app_context():
        db = get_db()
        from logic.plantilla_canonica import obtener_grupos
        grupos = obtener_grupos()
        grupos_por_numero = {g["grupo"]: g for g in grupos}

        objetivo = list(args.grupo or [])

        if args.sucursal is not None:
            encontrado = next((g["grupo"] for g in grupos if args.sucursal in g["sucursales"]), None)
            if encontrado is None:
                print(f"Sucursal {args.sucursal}: no esta en ningun grupo de la plantilla vigente.")
            else:
                objetivo.append(encontrado)

        if args.zona is not None:
            coincide = [g["grupo"] for g in grupos if g.get("zona") == args.zona]
            if not coincide:
                print(f"Zona {args.zona}: no se encontro ningun grupo.")
            objetivo.extend(coincide)

        for grupo in sorted(set(objetivo)):
            mostrar_grupo(db, grupos_por_numero, grupo)


if __name__ == "__main__":
    main()
