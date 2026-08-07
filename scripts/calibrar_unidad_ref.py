"""
calibrar_unidad_ref.py — recalcula `unidad_ref` como ASIGNACIÓN GLOBAL por día.

El Excel canónico trae, para cada grupo, la unidad en la que viajó más veces.
Calculada grupo por grupo, esa moda ignora que la unidad es un recurso
compartido: 8 grupos apuntaban a T 17_2 y 8 a T 20, mientras J 18, J 19 y K 20
no eran referencia de ninguno y el motor sólo las tocaba por desbordamiento.
Sobre 9 semanas eso dejaba T 17_1 con +17 días de trabajo contra la operación
real y a K 16 con -12.

Aquí la referencia se resuelve donde la restricción existe —POR DÍA y contra la
capacidad— maximizando la afinidad histórica de cada grupo con cada unidad.
Medido fuera de muestra (origen móvil, 5 pliegues):

    composición de ruta   22.0 %  ->  27.4 %
    jaccard de compañeros 48.6    ->  53.5
    error de reparto      15.6    ->  14.0
    unidades ociosas       0.2    ->   0.0

NO escribe en la BD: genera `datos/unidad_ref_por_grupo.csv`, que
`cargar_plantilla.py` aplica al crear la versión siguiente.

Uso:
    python scripts/calibrar_unidad_ref.py            # muestra y escribe el CSV
    python scripts/calibrar_unidad_ref.py --dry-run  # sólo muestra
"""
import sys, os, json, csv
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from collections import Counter, defaultdict
from sqlalchemy import select
from app import create_app

SALIDA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "datos", "unidad_ref_por_grupo.csv")


def main():
    seco = "--dry-run" in sys.argv
    app = create_app()
    with app.app_context():
        from db import get_db, get_table
        from logic.plantilla_canonica import obtener_grupos, version_vigente
        from logic.convrp_validacion import asignar_unidad_ref, kg_representativo
        from logic.vrp_logic import obtener_capacidades_vehiculos

        db = get_db()
        caps = obtener_capacidades_vehiculos()
        plantilla = obtener_grupos()
        if not plantilla:
            print("No hay plantilla vigente.")
            return 1
        grupo_de = {}
        for g in plantilla:
            for s in g.get("sucursales", []):
                grupo_de[s] = int(g["grupo"])

        # ── afinidad (semanas en que el grupo viajó en cada unidad) y kg típico ──
        semanas = [dict(x) for x in db.execute(
            select(get_table("rutas_historicas"))).mappings()]
        afinidad = defaultdict(Counter)
        kg_semanal = defaultdict(list)
        n_sem = 0
        for r in semanas:
            if r["tipo_registro"] != "sucursales" or "julio" in str(r["nombre"]).lower():
                continue
            n_sem += 1
            votos, kg = defaultdict(Counter), defaultdict(float)
            for f in json.loads(r["filas"] or "[]"):
                if f.get("tipo") == "mayorista" or f.get("id_sucursal") is None:
                    continue
                g = grupo_de.get(int(f["id_sucursal"]))
                if g is None:
                    continue
                votos[g][str(f.get("vehiculo"))] += 1
                kg[g] += float(f.get("kg_entrega") or 0)
            for g, c in votos.items():
                afinidad[g][c.most_common(1)[0][0]] += 1
            for g, v in kg.items():
                kg_semanal[g].append(v)

        # ── carga de MAYORISTAS que cada grupo arrastra ──
        # La zona de mayoreo viaja pegada al grupo núcleo (Fase 3), así que su
        # peso es parte de la ruta y tiene que entrar al elegir camión. Sin
        # esto, el g24 (Playa Vicente, 680 kg Lores) quedaba referenciado a un
        # T 17_1 de 2,500 y los 3,276 kg de PLAZA COMERCIAL RIO lo empujaban a
        # un F 350 distinto cada semana, abriéndole un día de trabajo a esa
        # unidad que la operación no hace.
        from logic.enganche_zona import _norm
        tp = get_table("plantilla_poblacion_zona")
        pobl = {_norm(r.poblacion): str(r.zona) for r in
                db.execute(select(tp.c.poblacion, tp.c.zona).where(tp.c.vigente == 1))}
        tz = get_table("plantilla_zona_mayorista")
        nucleo = {str(r.zona): r.grupo_nucleo for r in
                  db.execute(select(tz.c.zona, tz.c.grupo_nucleo)
                             .where(tz.c.vigente == 1)) if r.grupo_nucleo is not None}
        grupo_de_cliente = {}
        for c in db.execute(select(get_table("clientes_mayoristas"))).mappings():
            z = pobl.get(_norm(c.get("poblacion")))
            g = nucleo.get(z) if z else None
            if g is not None and c.get("id_cliente") is not None:
                grupo_de_cliente[int(c["id_cliente"])] = int(g)
        may_semanal = defaultdict(list)
        for r in semanas:
            if r["tipo_registro"] != "sucursales" or "julio" in str(r["nombre"]).lower():
                continue
            kg = defaultdict(float)
            for f in json.loads(r["filas"] or "[]"):
                if f.get("tipo") != "mayorista" or f.get("id_cliente") is None:
                    continue
                g = grupo_de_cliente.get(int(f["id_cliente"]))
                if g is not None:
                    kg[g] += float(f.get("kg_entrega") or 0)
            for g, v in kg.items():
                may_semanal[g].append(v)
        con_may = sorted(may_semanal, key=lambda g: -kg_representativo(may_semanal[g]))
        print("carga de mayoristas por grupo (p90 kg/sem):",
              {g: round(kg_representativo(may_semanal[g])) for g in con_may[:8]})

        # Dos cantidades distintas (ver `asignar_unidad_ref`):
        #   kg_tipico = MEDIANA — cuánto ocupa el grupo al empacar varios juntos.
        #   kg_minimo = PERCENTIL 90 — cuánto tiene que aguantar el camión.
        # Se suma la carga de mayoristas del grupo (percentil equivalente): el
        # camión de referencia tiene que poder con la ruta completa, no sólo
        # con la parte Lores.
        kg_tipico, kg_minimo = {}, {}
        for x in plantilla:
            g = int(x["grupo"])
            kg_tipico[g] = (kg_representativo(kg_semanal.get(g, []), percentil=0.5)
                            + kg_representativo(may_semanal.get(g, []), percentil=0.5))
            kg_minimo[g] = (kg_representativo(kg_semanal.get(g, []))
                            + kg_representativo(may_semanal.get(g, [])))

        # viajes por día que hace la operación: acota cuántas unidades se abren.
        # Sin este tope la afinidad sola abre un viaje por grupo (fuera de
        # muestra: 34.6 viajes/semana contra 29.8 reales, utilización 58 %).
        por_dia = Counter()
        for r in semanas:
            if r["tipo_registro"] != "sucursales" or "julio" in str(r["nombre"]).lower():
                continue
            for k in {(str(f.get("vehiculo")), str(f.get("dia_semana")).upper())
                      for f in json.loads(r["filas"] or "[]")
                      if f.get("tipo") != "mayorista" and f.get("id_sucursal") is not None}:
                por_dia[k[1]] += 1
        objetivo = {d: max(1, round(n / max(n_sem, 1))) for d, n in por_dia.items()}
        print("viajes/día en la operación real:",
              {d: objetivo[d] for d in sorted(objetivo)})
        nueva = asignar_unidad_ref(plantilla, {g: dict(c) for g, c in afinidad.items()},
                                   kg_tipico, caps, viajes_objetivo=objetivo,
                                   kg_minimo=kg_minimo)

        actual = {int(g["grupo"]): g.get("unidad_ref") for g in plantilla}
        dia_de = {int(g["grupo"]): str(g.get("dia_preferido") or g.get("dia")).upper()
                  for g in plantilla}
        print(f"plantilla vigente v{version_vigente()} — {n_sem} semanas de historia\n")
        print(f"{'g':>4}{'día':<11}{'kg tip':>8}{'kg p90':>8}{'cap':>7}{'antes':<10}{'nuevo':<10}"
              f"  afinidad histórica (semanas por unidad)")
        cambios = 0
        for g in sorted(nueva):
            a, b = actual.get(g), nueva[g]
            if a != b:
                cambios += 1
            af = ", ".join(f"{u}:{n}" for u, n in afinidad[g].most_common(4))
            print(f"{g:>4}{dia_de.get(g, ''):<11}{kg_tipico.get(g, 0):>8,.0f}"
                  f"{kg_minimo.get(g, 0):>8,.0f}{caps.get(b, 0):>7,.0f}"
                  f"{str(a):<10}{b:<10}{'  <-- cambia' if a != b else '            '}  {af}")

        print(f"\ncambios: {cambios} de {len(nueva)} grupos")
        antes, despues = Counter(actual.values()), Counter(nueva.values())
        print(f"{'unidad':<10}{'cap':>6}{'grupos antes':>14}{'grupos después':>16}"
              f"{'días reales/sem':>17}")
        dias_reales = defaultdict(set)
        for r in semanas:
            if r["tipo_registro"] != "sucursales" or "julio" in str(r["nombre"]).lower():
                continue
            for f in json.loads(r["filas"] or "[]"):
                if f.get("id_sucursal") is not None:
                    dias_reales[str(f.get("vehiculo"))].add(
                        (str(r["nombre"]), str(f.get("dia_semana")).upper()))
        for u in sorted(caps):
            print(f"{u:<10}{caps[u]:>6}{antes.get(u, 0):>14}{despues.get(u, 0):>16}"
                  f"{len(dias_reales.get(u, ()))/max(n_sem,1):>17.1f}")

        if seco:
            print("\n--dry-run: no se escribió el CSV.")
            return 0
        with open(SALIDA, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["grupo", "dia_preferido", "unidad_ref", "unidad_anterior",
                        "kg_tipico", "kg_p90", "afinidad"])
            for g in sorted(nueva):
                w.writerow([g, dia_de.get(g, ""), nueva[g], actual.get(g, ""),
                            round(kg_tipico.get(g, 0)), round(kg_minimo.get(g, 0)),
                            " | ".join(f"{u}:{n}" for u, n in afinidad[g].most_common())])
        print(f"\nCSV escrito: {SALIDA}")
        print("Aplícalo con: python scripts/cargar_plantilla.py  (crea la versión siguiente)")
        return 0


if __name__ == "__main__":
    sys.exit(main())
