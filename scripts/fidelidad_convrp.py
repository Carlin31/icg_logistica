"""
fidelidad_convrp.py — fidelidad del ConVRP por ORIGEN MÓVIL.

Entrena con las semanas 1..k y mide contra la k+1, k = 4..8 → 5 mediciones fuera
de muestra. La ventana de "últimas 4 semanas" que fija el día de cada grupo son
las últimas 4 del ENTRENAMIENTO de ese pliegue (sin fuga temporal).

No escribe nada: invoca el builder puro directamente, nunca el motor (si por
dentro corriera el de afinidad, el número no significaría nada).

Uso:
    python scripts/fidelidad_convrp.py
"""
import sys, os, json
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy import select
from app import create_app


def _clave_orden(nombre):
    meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
             "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    n = str(nombre).lower()
    mes = next((i for i, m in enumerate(meses) if m in n), 99)
    import re
    dias = [int(x) for x in re.findall(r"\b(\d{1,2})\b", n)]
    return (mes, dias[0] if dias else 0)


def main():
    # `--ventana N` = nº de semanas del entrenamiento que fijan el día de cada
    # grupo; `--ventana todo` usa TODAS. Sirve para comparar fuera de muestra la
    # regla actual (últimas 4) contra "el día dominante de todo el histórico".
    def _v(flag, por_defecto):
        if flag not in sys.argv:
            return por_defecto
        x = sys.argv[sys.argv.index(flag) + 1]
        return None if x.lower() in ("todo", "todas", "all") else int(x)
    ventana = _v("--ventana", 4)
    ventana_u = _v("--ventana-unidad", -1)
    unidad_global = "--unidad-global" in sys.argv
    app = create_app()
    with app.app_context():
        from db import get_db, get_table
        from logic.convrp_logic import construir_groups_desde_plantilla, cfg_por_defecto
        from logic.convrp_validacion import construir_plantilla_desde, medir_fidelidad
        from logic.plantilla_canonica import horarios_por_dia
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

        semanas = []
        for r in db.execute(select(get_table("rutas_historicas"))).mappings():
            if r["tipo_registro"] != "sucursales" or "julio" in str(r["nombre"]).lower():
                continue
            semanas.append((str(r["nombre"]),
                            json.loads(r["filas"]) if r["filas"] else []))
        semanas.sort(key=lambda x: _clave_orden(x[0]))
        print(f"semanas (orden cronológico): {len(semanas)}")
        for i, (n, _) in enumerate(semanas, 1):
            print(f"   {i}. {n[:46]}")

        # Días RECURRENTES por sucursal, tomados del histórico real (no de la
        # plantilla: medir contra ella daría 100 % por tautología, porque el
        # builder sólo coloca dentro de sus propios días admisibles).
        # Umbral 15 % de las apariciones de esa sucursal — sin él, la UNIÓN de
        # 9 semanas es tan permisiva que casi cualquier día cuenta.
        from collections import Counter as _C
        _vistos = {}
        for _, filas in semanas:
            for f in filas:
                if f.get("tipo") != "mayorista" and f.get("id_sucursal") is not None:
                    _vistos.setdefault(int(f["id_sucursal"]), _C())[
                        str(f.get("dia_semana")).upper()] += 1
        UMBRAL_DIA = 0.15
        adm_real = {sid: {d for d, n in c.items()
                          if n / max(sum(c.values()), 1) >= UMBRAL_DIA}
                    for sid, c in _vistos.items()}

        cfg = dict(cfg_por_defecto(), depot=depot, horarios_por_dia=horarios_por_dia())
        print()
        print(f"{'pliegue':<9}{'entrena':<9}{'mide contra':<30}"
              f"{'grupos':>8}{'día ok%':>9}{'día adm%':>10}"
              f"{'grupo ok%':>11}{'jaccard%':>10}{'rig part':>10}"
              f"{'Δflota':>8}{'ociosas':>9}{'viajes':>8}{'real':>6}{'util%':>7}")
        acum = []
        for k in range(4, len(semanas)):
            entrena = [f for _, f in semanas[:k]]
            nombre_test, filas_test = semanas[k]
            plantilla = construir_plantilla_desde(
                entrena, ventana_dia=ventana, ventana_unidad=ventana_u,
                vehiculos_cap=(caps if unidad_global else None), coords=coords)
            pedidos = {}
            for f in filas_test:
                if f.get("tipo") != "mayorista" and f.get("id_sucursal") is not None:
                    sid = int(f["id_sucursal"])
                    pedidos[sid] = pedidos.get(sid, 0) + float(f.get("kg_entrega") or 0)
            cfg_f = dict(cfg, afinidad_unidad={
                g["grupo"]: g["afinidad_unidad"] for g in plantilla
                if g.get("afinidad_unidad")})
            groups, exc = construir_groups_desde_plantilla(
                pedidos, {}, coords, plantilla, caps, vols, cfg_f)
            # OJO: el conjunto admisible debe salir del HISTÓRICO REAL, no de
            # la plantilla — el builder sólo coloca dentro de los días de la
            # plantilla, así que medir contra ella daría 100 % por tautología.
            # Aquí: días en que la empresa entregó realmente esa sucursal.
            fid = medir_fidelidad(groups, filas_test,
                                  admisibles_por_sucursal=adm_real)
            n_rig = len([e for e in exc if e["tipo"] == "PARTIDO_CAPACIDAD"
                         and e["rigidez"] == "RIGIDO"])
            # reparto de flota: días de trabajo por unidad, míos vs reales.
            # El acierto de ETIQUETA de unidad tiene techo del 43 % (los grupos
            # usan 5-8 unidades distintas en 9 semanas), así que lo que importa
            # no es el nombre sino que la carga se reparta como en la operación:
            # dejar una unidad ociosa mientras otra hace 45 días de trabajo es
            # un plan que la empresa no puede ejecutar.
            d_real, d_mio = {}, {}
            for f in filas_test:
                if f.get("tipo") != "mayorista" and f.get("id_sucursal") is not None:
                    d_real.setdefault(str(f.get("vehiculo")), set()).add(
                        str(f.get("dia_semana")).upper())
            for (u, dd) in groups:
                d_mio.setdefault(u, set()).add(str(dd).upper())
            delta = sum(abs(len(d_mio.get(u, ())) - len(d_real.get(u, ())))
                        for u in set(d_mio) | set(d_real))
            ociosas = len([u for u in d_real if not d_mio.get(u)])
            viajes_real = len({(str(f.get("vehiculo")), str(f.get("dia_semana")).upper())
                               for f in filas_test
                               if f.get("tipo") != "mayorista"
                               and f.get("id_sucursal") is not None})
            util = 0.0
            if groups:
                util = sum(sum(pedidos.get(m["sid"], 0) for m in ms)
                           / max(float(caps.get(u, 3500)), 1)
                           for (u, _), ms in groups.items()) / len(groups) * 100
            print(f"{'k=' + str(k):<9}{'1..' + str(k):<9}{nombre_test[10:38]:<30}"
                  f"{len(plantilla):>8}{fid.get('dia_correcto_pct', 0):>9}"
                  f"{fid.get('dia_admisible_pct') or 0:>10}"
                  f"{fid.get('grupo_correcto_pct', 0):>11}"
                  f"{fid.get('companeros_jaccard', 0):>10}{n_rig:>10}"
                  f"{delta:>8}{ociosas:>9}{len(groups):>8}{viajes_real:>6}{util:>7.1f}")
            acum.append((fid.get("dia_correcto_pct", 0),
                         fid.get("dia_admisible_pct") or 0,
                         fid.get("grupo_correcto_pct", 0),
                         fid.get("companeros_jaccard", 0), n_rig,
                         delta, ociosas, len(groups), viajes_real, util))
        if acum:
            n = len(acum)
            print(f"\n{'PROMEDIO':<48}{'':>8}"
                  f"{sum(a[0] for a in acum) / n:>9.1f}"
                  f"{sum(a[1] for a in acum) / n:>10.1f}"
                  f"{sum(a[2] for a in acum) / n:>11.1f}"
                  f"{sum(a[3] for a in acum) / n:>10.1f}"
                  f"{sum(a[4] for a in acum):>10}"
                  f"{sum(a[5] for a in acum) / n:>8.1f}{sum(a[6] for a in acum) / n:>9.1f}"
                  f"{sum(a[7] for a in acum) / n:>8.1f}{sum(a[8] for a in acum) / n:>6.1f}"
                  f"{sum(a[9] for a in acum) / n:>7.1f}")
            print(f"\n({n} mediciones fuera de muestra; la ventana de día de cada "
                  f"pliegue usa sólo sus semanas de entrenamiento)")
            print("  día ok%  = día exacto (métrica dura, análisis interno);"
                  " techo del oráculo 71.5 %.")
            print("  día adm% = cae en un día RECURRENTE (≥15 % de apariciones)"
                  " de esa sucursal en el\n             histórico real —"
                  " conjunto independiente de la plantilla.")


if __name__ == "__main__":
    main()
