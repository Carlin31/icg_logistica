"""
smoke_convrp.py — GATE PERMANENTE del builder ConVRP contra datos reales.

Los unitarios (tests/test_convrp_logic.py) pasaron al 100 % mientras el builder
tenía dos bugs que sólo aparecieron aquí: dispersaba los grupos en ~40 viajes
(la operación real usa ~31) y la preferencia de unidad se ignoraba en silencio
porque el Excel escribe 'F350_2' y el catálogo 'F 350_2'. Correr este smoke
después de tocar el builder, la plantilla o el catálogo de vehículos.

Métricas de aceptación (contra las 9 semanas confirmadas feb–jun):
  - VIAJES (unidad,día) distintos por semana  ≈ los reales (~31; razón ~1.4
    grupos por viaje). Muy por encima = no consolida; muy por debajo = empaca
    de más y pierde fidelidad.
  - RÍGIDOS partidos: lo más cerca de 0 posible.
  - Ninguna sucursal perdida ni duplicada.
  - Determinismo: misma salida invirtiendo el orden de entrada.

Uso:
    python scripts/smoke_convrp.py
"""
import sys, os, json
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy import select
from app import create_app


def main():
    app = create_app()
    with app.app_context():
        from db import get_db, get_table
        from logic.plantilla_canonica import obtener_grupos
        from logic.convrp_logic import construir_groups_desde_plantilla, cfg_por_defecto
        from logic.vrp_logic import obtener_capacidades_vehiculos, obtener_volumenes_vehiculos
        from logic.asignacion_logic import MATRIZ_LAT_DEFAULT, MATRIZ_LON_DEFAULT

        db = get_db()
        cb = db.execute(select(get_table("configuracion"))).mappings().first() or {}
        depot = (float(cb.get("matriz_lat") or MATRIZ_LAT_DEFAULT),
                 float(cb.get("matriz_lon") or MATRIZ_LON_DEFAULT))
        coords = {int(s.num_tienda): (float(s.latitud), float(s.longitud))
                  for s in db.execute(select(get_table("sucursales"))).mappings()
                  if s.get("latitud") is not None and s.get("num_tienda") is not None}
        caps = obtener_capacidades_vehiculos()
        vols = obtener_volumenes_vehiculos()
        plantilla = obtener_grupos()

        # ── VERIFICACIÓN DE LLAVES ──────────────────────────────────────────
        # num_tienda y unidad_ref fueron el mismo tipo de fallo silencioso y los
        # dos se descubrieron de casualidad. Aquí cada join se verifica al 100 %
        # esperado y falla ruidosamente si no empata.
        from logic.plantilla_canonica import horarios_por_dia
        fallos = []
        print("── verificación de llaves ──")

        # 1. sucursal: miembros de la plantilla ↔ catálogo de sucursales
        miembros = {s for g in plantilla for s in g["sucursales"]}
        huerfanas = sorted(miembros - set(coords))
        print(f"  sucursal   : {len(miembros) - len(huerfanas)}/{len(miembros)} "
              f"con coordenada en `sucursales`")
        if huerfanas:
            fallos.append(f"sucursales de la plantilla sin coordenada: {huerfanas}")

        # 2. unidad: unidad_ref ↔ catálogo de vehículos
        sin_uni = sorted({g["grupo"] for g in plantilla if g["unidad_ref"] not in caps})
        print(f"  unidad     : {len(plantilla) - len(sin_uni)}/{len(plantilla)} "
              f"con unidad_ref resuelta")
        if sin_uni:
            fallos.append(f"grupos con unidad_ref sin resolver: {sin_uni}")

        # 3. día: días de la plantilla ↔ días del histórico y de config_dias
        dias_pl = {d for g in plantilla for d in g["dias_admisibles"]} | \
                  {g["dia"] for g in plantilla if g["dia"]}
        horarios = horarios_por_dia()
        sin_horario = sorted(dias_pl - set(horarios))
        print(f"  día        : {len(dias_pl) - len(sin_horario)}/{len(dias_pl)} "
              f"con horario en config_dias  (horarios: {horarios or 'NINGUNO'})")
        if sin_horario:
            fallos.append(f"días de la plantilla sin horario en config_dias "
                          f"(usarían el default 07:00–20:00): {sin_horario}")

        # 4. catálogo de zonas: un catálogo VACÍO es indistinguible de uno
        #    cargado desde fuera y dejaría el enganche de mayoristas sin
        #    destino, funcionando en apariencia y sin enganchar nada.
        ZONAS_ESPERADAS = 48
        t_zonas = get_table("plantilla_zona_mayorista")
        zonas = [dict(r) for r in db.execute(
            select(t_zonas).where(t_zonas.c.vigente == 1)).mappings()]
        con_nucleo = [z for z in zonas if z.get("grupo_nucleo") is not None]
        print(f"  zonas      : {len(zonas)}/{ZONAS_ESPERADAS} en el catálogo, "
              f"{len(con_nucleo)} con grupo núcleo calculado")
        if len(zonas) != ZONAS_ESPERADAS:
            fallos.append(f"catálogo de zonas con {len(zonas)} filas, se esperaban "
                          f"{ZONAS_ESPERADAS} (¿carga a medias?)")
        if not con_nucleo:
            fallos.append("ninguna zona tiene grupo núcleo: corre "
                          "recalcular_confianza_zonas() tras cargar la plantilla")

        # 5. población → zona (Fase 3): sin diccionario cargado, todo cae al
        #    fallback global; se avisa pero no se considera falla todavía.
        t_pz = get_table("plantilla_poblacion_zona")
        n_pz = len(list(db.execute(select(t_pz.c.poblacion).where(t_pz.c.vigente == 1))))
        pobs = {str(x.poblacion).strip() for x in
                db.execute(select(get_table("clientes_mayoristas").c.poblacion))
                if x.poblacion}
        print(f"  población  : diccionario población→zona con {n_pz} entradas "
              f"para {len(pobs)} poblaciones de clientes"
              f"{'  [PENDIENTE: cargar mapeo_poblacion_a_zona.csv]' if not n_pz else ''}")

        cfg = dict(cfg_por_defecto(), depot=depot, horarios_por_dia=horarios)
        print()
        mio, real, rig = [], [], 0
        norm = lambda g: sorted((k, sorted(m["sid"] for m in v)) for k, v in g.items())

        # catálogo de mayoristas, para el barrido CON enganche por zona
        from collections import Counter
        from logic.convrp_integracion import construir_rutas_con_mayoristas
        cli_may = {int(c["id_cliente"]): dict(c) for c in
                   db.execute(select(get_table("clientes_mayoristas"))).mappings()
                   if c.get("id_cliente") is not None}
        via_zona, via_destino, kg_via = Counter(), Counter(), Counter()
        peor_pct, viajes_con_may, solos = 0.0, [], 0

        # materializado: dentro del bucle se hacen más consultas (enganche por
        # zona) y el driver no admite dos cursores abiertos sobre la conexión.
        semanas = [dict(x) for x in
                   db.execute(select(get_table("rutas_historicas"))).mappings()]
        for r in semanas:
            if r["tipo_registro"] != "sucursales" or "julio" in str(r["nombre"]).lower():
                continue
            filas = json.loads(r["filas"]) if r["filas"] else []
            pedidos, kg_cli = {}, {}
            for f in filas:
                if f.get("tipo") == "mayorista":
                    if f.get("id_cliente") is not None:
                        c = int(f["id_cliente"])
                        kg_cli[c] = kg_cli.get(c, 0.0) + float(f.get("kg_entrega") or 0)
                elif f.get("id_sucursal") is not None:
                    sid = int(f["id_sucursal"])
                    pedidos[sid] = pedidos.get(sid, 0) + float(f.get("kg_entrega") or 0)
            vd_real = {(str(f.get("vehiculo")), str(f.get("dia_semana"))) for f in filas
                       if f.get("tipo") != "mayorista" and f.get("id_sucursal") is not None}

            groups, exc = construir_groups_desde_plantilla(
                pedidos, {}, coords, plantilla, caps, vols, cfg)
            sids = [m["sid"] for ms in groups.values() for m in ms]
            if sorted(sids) != sorted(pedidos) or len(sids) != len(set(sids)):
                fallos.append(f"{r['nombre']}: sucursales perdidas o duplicadas")
            # determinismo: orden de entrada invertido
            g2, e2 = construir_groups_desde_plantilla(
                dict(reversed(list(pedidos.items()))), {}, coords,
                list(reversed(plantilla)), caps, vols, cfg)
            if norm(groups) != norm(g2) or exc != e2:
                fallos.append(f"{r['nombre']}: NO determinista")

            # capacidad: ninguna ruta puede quedar sobre su vehículo
            for (u, d), ms in groups.items():
                kg = sum(pedidos.get(m["sid"], 0) for m in ms)
                if kg > float(caps.get(u, 3500)) + 0.01:
                    fallos.append(f"{r['nombre']}: {u}/{d} con {kg:,.0f} kg sobre "
                                  f"{caps.get(u)} (sólo Lores)")

            # ── barrido CON mayoristas: la carga entra al motor ──
            mayoristas = [{"id_cliente": c,
                           "nombre": (cli_may.get(c) or {}).get("nombre") or str(c),
                           "poblacion": (cli_may.get(c) or {}).get("poblacion"),
                           "latitud": (cli_may.get(c) or {}).get("latitud"),
                           "longitud": (cli_may.get(c) or {}).get("longitud"),
                           "peso_kg": kg} for c, kg in sorted(kg_cli.items())]
            gm, por_ruta, exc_m, detalle, meta_m = construir_rutas_con_mayoristas(
                pedidos, {}, coords, caps, vols, depot, mayoristas)
            if not meta_m["convergio_mayoristas"]:
                fallos.append(f"{r['nombre']}: el enganche de mayoristas no llegó "
                              f"a punto fijo")
            colocados = sum(len(v) for v in por_ruta.values())
            if colocados != len(mayoristas):
                fallos.append(f"{r['nombre']}: {len(mayoristas)-colocados} "
                              f"mayoristas sin colocar")
            # determinismo del enganche: orden de clientes invertido
            _, pr2, _, _, _ = construir_rutas_con_mayoristas(
                pedidos, {}, coords, caps, vols, depot, list(reversed(mayoristas)))
            colocacion = lambda p: sorted(
                (str(k), sorted(m["id_cliente"] for m in v)) for k, v in p.items() if v)
            if colocacion(por_ruta) != colocacion(pr2):
                fallos.append(f"{r['nombre']}: enganche de mayoristas NO determinista")
            pct_semana = 0.0
            for clave in set(gm) | set(por_ruta):
                total = (sum(pedidos.get(m["sid"], 0) for m in gm.get(clave, []))
                         + sum(m["peso_kg"] for m in por_ruta.get(clave, [])))
                pct = total / float(caps.get(clave[0], 3500)) * 100
                pct_semana = max(pct_semana, pct)
                if pct > 100.01:
                    fallos.append(f"{r['nombre']}: {clave[0]}/{clave[1]} al "
                                  f"{pct:.0f}% CON mayoristas")
            peor_pct = max(peor_pct, pct_semana)
            via_zona.update(d["via_zona"] for d in detalle)
            via_destino.update(d["via_destino"] for d in detalle)
            for d in detalle:
                kg_via[d["via_destino"]] += float(d["peso_kg"] or 0)
            solos += meta_m["viajes_solo_mayoristas"]
            viajes_con_may.append(len(set(gm) | {k for k, v in por_ruta.items() if v}))

            n_rig = len([e for e in exc if e["tipo"] == "PARTIDO_CAPACIDAD"
                         and e["rigidez"] == "RIGIDO"])
            rig += n_rig
            n_grp = len({m["grupo"] for ms in groups.values() for m in ms})
            mio.append(len(groups)); real.append(len(vd_real))
            print(f"  {str(r['nombre'])[:38]:<39} viajes mío={len(groups):<3} "
                  f"real={len(vd_real):<3} ({len(groups)-len(vd_real):+d})  "
                  f"grupos/viaje={n_grp/max(len(groups),1):.2f}  rígidos partidos={n_rig}"
                  f"  c/may={viajes_con_may[-1]:<3} pico={pct_semana:.0f}%")

        print("\n" + "=" * 68)
        print(f"VIAJES/semana   mío={sum(mio)/len(mio):.1f}   real={sum(real)/len(real):.1f}"
              f"   con mayoristas={sum(viajes_con_may)/len(viajes_con_may):.1f}")
        print(f"RÍGIDOS partidos (9 semanas): {rig}")
        print(f"PICO de utilización con mayoristas: {peor_pct:.1f} %"
              f"   (viajes sólo-mayoristas: {solos})")
        print("MAYORISTAS — por qué vía se resolvió cada parada:")
        tot = sum(via_destino.values()) or 1
        print(f"  zona   : " + "  ".join(f"{k} {v} ({v*100//tot}%)"
                                         for k, v in via_zona.most_common()))
        for k, v in via_destino.most_common():
            print(f"  destino: {k:<24} {v:>4} paradas ({v*100//tot:>2}%) "
                  f"{kg_via[k]:>10,.0f} kg")
        if fallos:
            print("FALLAS:")
            for f in fallos:
                print("  -", f)
        print("RESULTADO:", "FALLA" if fallos else "OK")
        return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
