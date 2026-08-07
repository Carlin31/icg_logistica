"""
pdf_convrp_preview.py — PDF de una semana generado por el ConVRP, SIN PERSISTIR.

Corre el builder en memoria sobre la demanda real de una semana y produce el
mismo PDF que la empresa ya conoce, para que el planeador diga si se parece a
como trabajan. NO escribe en `asignaciones` ni en ninguna otra tabla: usa
`generar_pdf(..., rutas_inyectadas=...)`.

Uso:
    python scripts/pdf_convrp_preview.py "18 al 22 de mayo"
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
    filtro = sys.argv[1] if len(sys.argv) > 1 else "18 al 22 de mayo"
    app = create_app()
    with app.app_context():
        from db import get_db, get_table
        from logic.convrp_integracion import construir_groups_convrp
        from logic.vrp_logic import (obtener_capacidades_vehiculos,
                                     obtener_volumenes_vehiculos,
                                     obtener_info_vehiculos)
        from logic.asignacion_logic import MATRIZ_LAT_DEFAULT, MATRIZ_LON_DEFAULT
        from logic.pdf_logic import generar_pdf

        db = get_db()
        cb = db.execute(select(get_table("configuracion"))).mappings().first() or {}
        depot = (float(cb.get("matriz_lat") or MATRIZ_LAT_DEFAULT),
                 float(cb.get("matriz_lon") or MATRIZ_LON_DEFAULT))

        suc = {}
        for s in db.execute(select(get_table("sucursales"))).mappings():
            if s.get("num_tienda") is None:
                continue
            suc[int(s["num_tienda"])] = {
                "nombre": s.get("nombre_base") or str(s["num_tienda"]),
                "lat": (float(s["latitud"]) if s.get("latitud") is not None else None),
                "lon": (float(s["longitud"]) if s.get("longitud") is not None else None)}
        coords = {k: (v["lat"], v["lon"]) for k, v in suc.items() if v["lat"] is not None}

        caps = obtener_capacidades_vehiculos()
        vols = obtener_volumenes_vehiculos()
        info_veh = obtener_info_vehiculos()

        # ── demanda real de la semana (histórico confirmado) ──
        h = get_table("rutas_historicas")
        reg = None
        for r in db.execute(select(h.c.mongo_id, h.c.nombre, h.c.filas, h.c.tipo_registro,
                                   h.c.fecha_inicio, h.c.fecha_fin, h.c.logistica_id)).mappings():
            if r["tipo_registro"] == "sucursales" and filtro.lower() in str(r["nombre"]).lower():
                reg = r
                break
        if reg is None:
            print(f"No encontré la semana '{filtro}'")
            return 1
        filas = json.loads(reg["filas"]) if reg["filas"] else []
        pedidos, may_filas = {}, []
        for f in filas:
            kg = float(f.get("kg_entrega") or 0)
            if f.get("tipo") == "mayorista":
                may_filas.append(f)
            elif f.get("id_sucursal") is not None:
                sid = int(f["id_sucursal"])
                pedidos[sid] = pedidos.get(sid, 0.0) + kg
        print(f"semana: {reg['nombre']}")
        print(f"  sucursales con pedido: {len(pedidos)}  ({sum(pedidos.values()):,.0f} kg)")
        print(f"  paradas de mayoristas: {len(may_filas)} "
              f"({sum(float(f.get('kg_entrega') or 0) for f in may_filas):,.0f} kg)")

        # ── mayoristas de la semana (entran AL MOTOR, no después) ──
        from collections import Counter, defaultdict
        cli = {int(c["id_cliente"]): dict(c) for c in
               db.execute(select(get_table("clientes_mayoristas"))).mappings()
               if c.get("id_cliente") is not None}
        kg_cli = defaultdict(float)
        for f in may_filas:
            if f.get("id_cliente") is not None:
                kg_cli[int(f["id_cliente"])] += float(f.get("kg_entrega") or 0)

        # ── documentos de la semana (BB3306, AA1430…) ──
        # `rutas_historicas` guarda las paradas de mayoreo sólo por id_cliente,
        # sin el nº de documento, así que el PDF salía rotulado "444/182/552_…"
        # en vez de "BB3306 AL 3318/22_COSVER". Los documentos viven en
        # `extraccion.mayoristas`, que sí los conserva: se cruzan por
        # `codigo` = id_cliente para poder imprimir el formato de siempre.
        docs_por_cliente = defaultdict(list)
        lid = None
        for r in db.execute(select(get_table("logisticas"))).mappings():
            if filtro.lower() in str(r["nombre"]).lower():
                lid = r["mongo_id"]
                break
        if lid:
            te = get_table("extraccion")
            crudo = db.execute(select(te.c.mayoristas)
                               .where(te.c.logistica_id == lid)).scalar()
            for d in (json.loads(crudo) if crudo else []):
                if d.get("codigo") is not None and d.get("documento"):
                    docs_por_cliente[int(d["codigo"])].append(d)
        n_doc = sum(len(v) for v in docs_por_cliente.values())

        # ── consolidación: documentos del mismo punto = UNA parada ──
        # Sin esto el VRP rutea documento por documento y parte una entrega en
        # tres viajes distintos (los 4 folios de PLAZA COMERCIAL RIO acabaron en
        # F 350_2/martes, T 25/miércoles y T 17_2/jueves). La parada consolidada
        # es carga indivisible.
        from logic.consolidacion_mayoristas import consolidar_documentos
        planos = [d for v in docs_por_cliente.values() for d in v]
        if planos:
            mayoristas = consolidar_documentos(planos, cli)
        else:
            mayoristas = [{"id_cliente": cid, "id_clientes": [cid],
                           "nombre": (cli.get(cid) or {}).get("nombre") or f"Cliente {cid}",
                           "poblacion": (cli.get(cid) or {}).get("poblacion"),
                           "latitud": (cli.get(cid) or {}).get("latitud"),
                           "longitud": (cli.get(cid) or {}).get("longitud"),
                           "peso_kg": kg, "folios": [str(cid)], "etiqueta": str(cid),
                           "documentos": []}
                          for cid, kg in sorted(kg_cli.items())]
        print(f"  documentos de mayoreo: {n_doc} -> {len(mayoristas)} paradas "
              f"consolidadas ({len(kg_cli)} clientes)"
              + ("" if n_doc else "   [sin extracción: una parada por cliente]"))

        # ── ConVRP en memoria, con la carga de mayoristas dentro ──
        from logic.convrp_integracion import construir_rutas_con_mayoristas
        groups, por_ruta, exc, detalle, meta = construir_rutas_con_mayoristas(
            pedidos, {}, coords, caps, vols, depot, mayoristas)
        print(f"  ConVRP: plantilla v{meta['version_plantilla']}, "
              f"{len(groups)} viajes, {len(exc)} excepciones, "
              f"{meta['pasadas_mayoristas']} pasada(s) de enganche"
              f"{'' if meta['convergio_mayoristas'] else ' SIN punto fijo'}")

        # ── rutas en el formato que consume el PDF ──
        from logic.convrp_logic import _orden_dia
        rutas = []
        claves = set(groups) | {k for k, v in por_ruta.items() if v}
        for (veh, dia) in sorted(claves, key=lambda k: (_orden_dia(k[1]), str(k[0]))):
            miembros = groups.get((veh, dia), [])   # [] = viaje de mayoristas solo
            cap_kg = float(caps.get(veh, 3500))
            sucursales = []
            for i, m in enumerate(sorted(miembros, key=lambda x: x["sid"]), 1):
                sid = m["sid"]
                d = suc.get(sid, {})
                sucursales.append({
                    "tipo": "sucursal", "num_tienda": sid, "orden": i,
                    "nombre": d.get("nombre", str(sid)),
                    "peso_kg": float(pedidos.get(sid, 0)),
                    "latitud": d.get("lat"), "longitud": d.get("lon")})
            peso = sum(s["peso_kg"] for s in sucursales)
            rutas.append({
                "id": f"convrp_{veh.replace(' ', '_').lower()}_{dia.lower()}",
                "nombre": f"{veh} — {dia.capitalize()}",
                "tipo": "convrp", "dia": dia,
                "vehiculo_abrev": veh,
                "vehiculo_placas": info_veh.get(veh, {}).get("placas", "—"),
                "capacidad_ton": cap_kg / 1000,
                "peso_kg": peso,
                "pct_utilizacion": round(peso / cap_kg * 100, 1) if cap_kg else 0,
                "chofer_override": None,
                "sucursales": sucursales,
                "mayoristas": []})

        # ── colocar los mayoristas ya enganchados en cada ruta ──
        idx = {(r["vehiculo_abrev"], r["dia"]): r for r in rutas}
        colocados = 0
        for clave, lst in por_ruta.items():
            r = idx.get(clave)
            if not r:
                continue
            # Una fila POR DOCUMENTO, como el formato original: el PDF agrupa
            # documentos consecutivos del mismo cliente/población en
            # 'BB3313/14/15_CTES. COSAMALOAPAN'. Si un cliente no tiene
            # documentos (semana sin extracción), se emite una sola fila.
            for i, m in enumerate(sorted(lst, key=lambda x: str(x.get("etiqueta"))), 1):
                # una fila por PARADA (no por documento): la etiqueta ya trae los
                # folios concatenados y el PDF la imprime tal cual.
                r["mayoristas"].append({
                    "tipo": "mayorista", "id_cliente": m["id_cliente"],
                    "documento": m.get("etiqueta") or str(m["id_cliente"]),
                    "nombre": "", "poblacion": m.get("poblacion"),
                    "orden": len(r["sucursales"]) + i,
                    "peso_kg": float(m["peso_kg"]),
                    "latitud": m.get("latitud"), "longitud": m.get("longitud")})
                colocados += 1
            # Ordenados por nº de documento, como el original: así el PDF puede
            # colapsarlos en rango ('BB3306 AL 3318/22_COSVER') en vez de
            # listarlos salteados ('BB3309/08/06/07…').
            r["mayoristas"].sort(key=lambda m: str(m.get("documento")))
            for k, m in enumerate(r["mayoristas"], 1):
                m["orden"] = len(r["sucursales"]) + k
            r["peso_kg"] += sum(float(m["peso_kg"]) for m in lst)
            cap = float(r["capacidad_ton"]) * 1000
            r["pct_utilizacion"] = round(r["peso_kg"] / cap * 100, 1) if cap else 0

        vz = Counter(d["via_zona"] for d in detalle)
        vd = Counter(d["via_destino"] for d in detalle)
        print(f"\n  MAYORISTAS: {len(mayoristas)} clientes, "
              f"{sum(m['peso_kg'] for m in mayoristas):,.0f} kg -> {colocados} colocados")
        print(f"    via ZONA   : {dict(vz)}")
        print(f"    via DESTINO: {dict(vd)}")
        kg_via = defaultdict(float)
        for d in detalle:
            kg_via[d["via_destino"]] += float(d["peso_kg"] or 0)
        for v, k in sorted(kg_via.items(), key=lambda x: -x[1]):
            print(f"      {v:<22} {k:>8,.0f} kg")
        solos = [d for d in detalle if d["via_destino"] == "VIAJE_MAYORISTAS_SOLO"]
        if solos:
            print(f"    sin ruta ({len(solos)}): "
                  f"{[(d['nombre'][:20], d['zona']) for d in solos][:6]}")
        satur = [r for r in rutas if r["pct_utilizacion"] > 100]
        print(f"    rutas por encima del 100% tras enganchar: {len(satur)}"
              f"{[(r['vehiculo_abrev'], r['dia'], r['pct_utilizacion']) for r in satur] if satur else ''}")

        datos = {"id": str(reg["logistica_id"] or reg["mongo_id"]),
                 "nombre": str(reg["nombre"]).replace("Logística del ", "", 1)
                           + " — VISTA PREVIA ConVRP",
                 "fecha_inicio": reg["fecha_inicio"], "fecha_fin": reg["fecha_fin"]}
        ruta_pdf = generar_pdf(datos, rutas_inyectadas=rutas)
        print(f"\nPDF generado (sin persistir nada): {ruta_pdf}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
