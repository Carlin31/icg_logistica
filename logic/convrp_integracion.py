"""
logic/convrp_integracion.py

Puente entre el builder puro (`convrp_logic`) y la BD: arma las entradas desde
la plantilla vigente y persiste las excepciones para que el planeador las vea
en el módulo de modificación.

El builder se mantiene puro (sin BD) para poder probarlo con datos sintéticos;
todo lo que toca SQL Server vive aquí.

⚠️ REGLA DURA — nunca correr el motor con el flag encendido contra registros de
producción. Para probar el flujo completo:
  - usar `construir_groups_convrp(...)`, que corre EN MEMORIA y **no persiste**;
  - `generar_rutas_vrp_afinidad(...)` SÍ persiste (sobreescribe `asignaciones`
    de esa logística): sólo contra una semana sandbox o una copia de la BD.
La persistencia es opt-in explícito (`guardar_excepciones_convrp`), nunca un
efecto colateral del camino de prueba.
"""
from datetime import datetime
import json as _json

from sqlalchemy import select, insert, delete

from db import get_db, get_table
from logic.convrp_logic import construir_groups_desde_plantilla, cfg_por_defecto
from logic.plantilla_canonica import obtener_grupos, horarios_por_dia, version_vigente


def _afinidad_de_plantilla(plantilla: list) -> dict:
    """
    {grupo: {unidad: semanas}} desde la columna `unidades_afines`
    ('T 17_1:4 | F 350_3:2'). El motor la usa para desempatar cuando cede la
    unidad de referencia; sin ella el desempate es el nombre del camión.

    Los nombres de unidad llevan espacios ('F 350_1'), así que el corte es por
    el ÚLTIMO ':' de cada término, no por el primero.
    """
    salida: dict = {}
    for g in plantilla or []:
        crudo = g.get("unidades_afines")
        if not crudo:
            continue
        pares = {}
        for termino in str(crudo).split("|"):
            termino = termino.strip()
            if ":" not in termino:
                continue
            unidad, _, n = termino.rpartition(":")
            try:
                pares[unidad.strip()] = float(n)
            except ValueError:
                continue
        if pares:
            salida[int(g["grupo"])] = pares
    return salida


def _coocurrencia_de_bd(plantilla: list) -> dict:
    """
    Coocurrencia real grupo-a-grupo (ver `convrp_validacion.coocurrencia_grupos`)
    desde `rutas_historicas`. Se recalcula en cada corrida en vez de persistirse
    junto a la plantilla: es barata (unas pocas semanas, unos cientos de filas)
    y evita otra columna que se pueda quedar desincronizada.

    Mismas semanas "confirmadas" que usa el resto del arnés de calibración
    (excluye las de julio, aún no confirmadas — ver `calibrar_unidad_ref.py`).
    """
    from logic.convrp_validacion import coocurrencia_grupos
    grupo_de = {s: int(g["grupo"]) for g in plantilla for s in g.get("sucursales", [])}
    db = get_db()
    t = get_table("rutas_historicas")
    semanas = []
    for r in db.execute(select(t.c.tipo_registro, t.c.nombre, t.c.filas)).mappings():
        if r["tipo_registro"] != "sucursales" or "julio" in str(r["nombre"]).lower():
            continue
        semanas.append(_json.loads(r["filas"]) if r["filas"] else [])
    return coocurrencia_grupos(grupo_de, semanas)


def construir_groups_convrp(pedidos_dict: dict, volumenes_dict: dict,
                            coords_dict: dict, vehiculos_cap: dict,
                            vehiculos_vol: dict, depot: tuple,
                            kg_mayoristas: dict = None):
    """
    Carga la plantilla canónica vigente y arma las rutas de la semana.

    `kg_mayoristas` ({num_tienda: kg}) es la carga de mayoristas ya anclada a
    sucursales: entra a las restricciones del motor, para que el sobrecupo que
    provoca el enganche por zona dispare las palancas (unidad → día → partir) en
    vez de aparecer al pintar el PDF con la ruta al 148 %.

    NO persiste nada: corre en memoria. Devuelve (groups, excepciones, meta).
    `groups` tiene el mismo formato que produce el motor de afinidad
    —{(vehiculo, dia): [{"sid","seq"}]}— así que el resto del flujo (reporte,
    secuencia, PDF, persistencia) no cambia.

    Lanza si la plantilla o sus llaves no están sanas: en el arnés de validación
    un fallo silencioso mediría el motor equivocado.
    """
    plantilla = obtener_grupos()
    if not plantilla:
        raise ValueError("No hay plantilla canónica vigente. "
                         "Corre scripts/cargar_plantilla.py primero.")
    sin_unidad = sorted({g["grupo"] for g in plantilla
                         if g["unidad_ref"] is not None
                         and g["unidad_ref"] not in (vehiculos_cap or {})})
    if sin_unidad:
        raise ValueError(
            f"unidad_ref sin resolver contra el catálogo de vehículos en los "
            f"grupos {sin_unidad}: la preferencia de unidad se ignoraría en "
            f"silencio. Recarga la plantilla (scripts/cargar_plantilla.py).")
    cfg = dict(cfg_por_defecto(), depot=depot,
               horarios_por_dia=horarios_por_dia(),
               afinidad_unidad=_afinidad_de_plantilla(plantilla),
               coocurrencia_grupos=_coocurrencia_de_bd(plantilla))
    groups, excepciones = construir_groups_desde_plantilla(
        pedidos_dict, volumenes_dict or {}, coords_dict, plantilla,
        vehiculos_cap, vehiculos_vol or {}, cfg,
        kg_mayoristas=kg_mayoristas or {})
    meta = {
        "version_plantilla": version_vigente(),
        "grupos_plantilla": len(plantilla),
        "grupos_con_demanda": len({m["grupo"] for ms in groups.values() for m in ms}),
        "viajes": len(groups),
    }
    return groups, excepciones, meta


def guardar_excepciones_convrp(logistica_id: str, excepciones: list) -> int:
    """
    Reemplaza las excepciones de esa logística (una corrida sustituye a la
    anterior, igual criterio que el reporte VRP).

    Devuelve el nº de filas escritas, o **-1 si falló** — nunca 0, que es un
    resultado VÁLIDO (una corrida sin excepciones). El llamador debe poder
    distinguir "no hubo excepciones" de "no se pudieron guardar".
    """
    if not logistica_id:
        return 0
    ahora = datetime.now().isoformat()
    try:
        db = get_db()
        t = get_table("convrp_excepciones")
        db.execute(delete(t).where(t.c.logistica_id == logistica_id))
        filas = []
        for i, e in enumerate(excepciones or []):
            extra = {k: v for k, v in e.items()
                     if k not in ("tipo", "grupo", "rigidez", "restriccion",
                                  "desde_unidad", "a_unidad", "desde_dia",
                                  "a_dia", "paradas", "motivo")}
            filas.append({
                "logistica_id": logistica_id, "generado_en": ahora, "orden": i,
                "tipo": e.get("tipo"), "grupo": e.get("grupo"),
                "rigidez": e.get("rigidez"), "restriccion": e.get("restriccion"),
                "desde_unidad": e.get("desde_unidad") or e.get("unidad"),
                "a_unidad": e.get("a_unidad"),
                "desde_dia": e.get("desde_dia") or e.get("dia"),
                "a_dia": e.get("a_dia"), "paradas": e.get("paradas"),
                "detalle": _json.dumps(extra, ensure_ascii=False) if extra else None,
                "motivo": (e.get("motivo") or "")[:600],
            })
        if filas:
            db.execute(insert(t), filas)
        return len(filas)
    except Exception as exc:  # noqa: BLE001
        import traceback
        print("=" * 70)
        print(f"[convrp] NO SE GUARDARON LAS EXCEPCIONES: {type(exc).__name__}: {exc}")
        print("[convrp] el planeador no las verá en el módulo de modificación.")
        print(traceback.format_exc())
        print("=" * 70)
        return -1          # distinguible de 0 = "no hubo excepciones"


def obtener_excepciones_convrp(logistica_id: str):
    """
    Excepciones de la última corrida ConVRP de esa logística (para la UI).

    Devuelve la lista, o **None si falló la lectura** — nunca [] por error, que
    es indistinguible de "esta corrida no tuvo excepciones".
    """
    if not logistica_id:
        return []
    try:
        db = get_db()
        t = get_table("convrp_excepciones")
        filas = []
        for r in db.execute(
                select(t).where(t.c.logistica_id == logistica_id)
                .order_by(t.c.orden)).mappings():
            d = dict(r)
            if d.get("detalle"):
                try:
                    d["detalle"] = _json.loads(d["detalle"])
                except ValueError:
                    pass
            filas.append(d)
        return filas
    except Exception as exc:  # noqa: BLE001
        import traceback
        print("=" * 70)
        print(f"[convrp] NO SE PUDIERON LEER LAS EXCEPCIONES: "
              f"{type(exc).__name__}: {exc}")
        print(traceback.format_exc())
        print("=" * 70)
        return None        # distinguible de [] = "no hubo excepciones"


def enganchar_mayoristas_por_zona(groups: dict, mayoristas: list, coords_rutas: dict = None):
    """
    Engancha cada mayorista a la RUTA de su zona, en vez de la inserción más
    barata global (Fase 3).

    Para cada cliente:
      1. resuelve su ZONA (historia → geografía → fallback), y
      2. resuelve el DESTINO dentro de la semana con la cadena
         núcleo → segundo grupo → geografía de ruta → viaje de mayoristas solo.

    `mayoristas` : [{id_cliente, nombre, poblacion, latitud, longitud, peso_kg}]
    `groups`     : {(unidad, dia): [{sid, grupo}]} del builder.
    `coords_rutas`: {(unidad, dia): (lat, lon)} centroide de cada ruta; si no se
                    pasa, se calcula de las sucursales de cada ruta.

    Devuelve (por_ruta, detalle):
      por_ruta : {(unidad, dia): [mayorista, ...]}
      detalle  : una fila por cliente con via_zona, zona, via_destino, destino…
                 (nunca se pierde un pedido en silencio).
    """
    from logic.enganche_zona import (resolver_zona_cliente, resolver_destino_enganche,
                                     centroides_desde_clientes)
    db = get_db()
    tz = get_table("plantilla_zona_mayorista")
    zonas = {str(r["zona"]): dict(r) for r in
             db.execute(select(tz).where(tz.c.vigente == 1)).mappings()}
    conf = {z: i.get("confianza") for z, i in zonas.items()}
    nucleo = {z: i.get("grupo_nucleo") for z, i in zonas.items()}
    tp = get_table("plantilla_poblacion_zona")
    hist = {str(r.poblacion): str(r.zona) for r in
            db.execute(select(tp.c.poblacion, tp.c.zona).where(tp.c.vigente == 1))}
    cm = [dict(r) for r in db.execute(select(get_table("clientes_mayoristas"))).mappings()]
    centroides = centroides_desde_clientes(cm, hist)

    # grupos presentes en cada ruta de ESTA semana
    rutas_por_grupo = {k: sorted({m.get("grupo") for m in v if m.get("grupo")})
                       for k, v in groups.items()}
    if coords_rutas is None:
        coords_rutas = {}

    por_ruta: dict = {}
    detalle: list = []
    for may in sorted(mayoristas, key=lambda m: str(m.get("id_cliente"))):
        z = resolver_zona_cliente(may.get("poblacion"), may.get("latitud"),
                                  may.get("longitud"), hist, centroides,
                                  confianza_por_zona=conf)
        zona = z["zona"]
        nuc = nucleo.get(zona) if zona else None
        # otros grupos de la zona, por si el núcleo no viaja esta semana
        otros = []
        if zona and zonas.get(zona, {}).get("grupos_lores"):
            import re as _re
            otros = [int(x) for x in _re.split(r"[^0-9]+", str(zonas[zona]["grupos_lores"]))
                     if x.isdigit() and int(x) != nuc]
        d = resolver_destino_enganche(nuc, otros, rutas_por_grupo,
                                      may.get("latitud"), may.get("longitud"),
                                      coords_rutas)
        if d["destino"]:
            por_ruta.setdefault(d["destino"], []).append(may)
        detalle.append({
            "id_cliente": may.get("id_cliente"), "nombre": may.get("nombre"),
            "poblacion": may.get("poblacion"), "peso_kg": may.get("peso_kg"),
            "zona": zona, "via_zona": z["via"], "confianza_zona": z.get("confianza"),
            "desacuerdo_zona": z.get("desacuerdo"),
            "grupo_nucleo": nuc, "via_destino": d["via"],
            "grupo_destino": d.get("grupo"),
            "destino": d["destino"], "motivo": d["motivo"]})
    return por_ruta, detalle


def _centroides_de_rutas(groups: dict, coords: dict) -> dict:
    """{(unidad, dia): (lat, lon)} — centroide de las sucursales de cada ruta."""
    salida = {}
    for clave, miembros in (groups or {}).items():
        pts = [coords[m["sid"]] for m in miembros if m["sid"] in (coords or {})]
        if pts:
            salida[clave] = (sum(p[0] for p in pts) / len(pts),
                             sum(p[1] for p in pts) / len(pts))
    return salida


def _anclar_kg_mayoristas(por_ruta: dict, detalle: list, groups: dict,
                          coords: dict):
    """
    {num_tienda: kg} — la carga de mayoristas repartida sobre las sucursales a
    las que se ancla, más {id_cliente: num_tienda}.

    El ancla es una sucursal del GRUPO al que la zona se enganchó, para que el
    peso viaje con el grupo (la zona sigue al grupo). Ver
    `enganche_zona.elegir_ancla_mayorista`.
    """
    from logic.enganche_zona import elegir_ancla_mayorista
    grupo_de = {d["id_cliente"]: d.get("grupo_destino") if d.get("grupo_destino")
                is not None else d.get("grupo_nucleo") for d in (detalle or [])}
    kg: dict = {}
    anclas: dict = {}
    for clave in sorted((por_ruta or {}), key=lambda k: (str(k[1]), str(k[0]))):
        miembros = groups.get(clave) or []
        sids_ruta = sorted(m["sid"] for m in miembros)
        for may in sorted(por_ruta[clave], key=lambda m: str(m.get("id_cliente"))):
            g = grupo_de.get(may.get("id_cliente"))
            sids_grupo = sorted(m["sid"] for m in miembros if m.get("grupo") == g)
            sid = elegir_ancla_mayorista(sids_ruta, sids_grupo,
                                         may.get("latitud"), may.get("longitud"),
                                         coords)
            if sid is None:
                continue
            anclas[may.get("id_cliente")] = sid
            kg[sid] = kg.get(sid, 0.0) + float(may.get("peso_kg") or 0)
    return kg, anclas


def _elegir_mejor_pasada(historial: list) -> int:
    """
    Cuando el enganche de mayoristas no llega a punto fijo en `max_pasadas`,
    hay que elegir con QUÉ pasada quedarse. Hallado en producción
    2026-08-12: el enganche entra en un ciclo de 2 que nunca converge, y
    quedarse con la ÚLTIMA pasada (lo que hacía antes) depende sólo de la
    paridad de `max_pasadas` -- con 4 (par) el corte caía siempre en la
    mitad "mala" del ciclo, con grupos completos desviados de su unidad_ref
    sin ningún motivo geográfico (Tuxtepec+Veracruz en un camión, luego
    Veracruz+Amatitlán en otro, cada semana un par distinto).

    Se elige la pasada con MENOS excepciones (desviaciones de unidad/día/
    partición): es la más fiel al historial, el mismo criterio que ya usa
    `calibrar_unidad_ref.py` para calificar una asignación. Empate → la
    pasada más temprana (determinista).

    `historial`: [{"excepciones": [...]}, ...] en orden de pasada.
    Devuelve el índice elegido.
    """
    return min(range(len(historial)),
              key=lambda i: (len(historial[i]["excepciones"]), i))


def construir_rutas_con_mayoristas(pedidos: dict, volumenes: dict, coords: dict,
                                   vehiculos_cap: dict, vehiculos_vol: dict,
                                   depot: tuple, mayoristas: list,
                                   max_pasadas: int = 4):
    """
    Rutas de la semana con la carga de mayoristas DENTRO del motor.

    El enganche por zona y el reparto de unidades son mutuamente dependientes:
    a qué ruta va un mayorista depende de dónde quedó su grupo, y dónde cabe el
    grupo depende de cuánta carga de mayoristas trae. Se resuelve por punto
    fijo, con tope de pasadas (determinista y acotado, mismo criterio que la
    cascada de días):

        pasada 1: motor sin carga de mayoristas → enganche → anclaje
        pasada n: motor CON la carga anclada    → enganche → anclaje
        se corta cuando el anclaje deja de cambiar (punto fijo) o al tope.

    Al final corre la garantía dura de cupo (`reubicar_mayoristas_por_cupo`),
    que es la red para el caso que las palancas no pueden resolver: un solo
    cliente que pesa más que el hueco disponible.

    Devuelve (groups, por_ruta, excepciones, detalle, meta). No persiste nada.
    """
    from logic.enganche_zona import reubicar_mayoristas_por_cupo
    kg_may: dict = {}
    groups, por_ruta, detalle, meta = {}, {}, [], {}
    excepciones: list = []
    coords_rutas: dict = {}
    convergio, pasada = False, 0
    historial: list = []
    for pasada in range(1, int(max_pasadas) + 1):
        groups, excepciones, meta = construir_groups_convrp(
            pedidos, volumenes, coords, vehiculos_cap, vehiculos_vol, depot,
            kg_mayoristas=kg_may)
        coords_rutas = _centroides_de_rutas(groups, coords)
        por_ruta, detalle = enganchar_mayoristas_por_zona(
            groups, mayoristas, coords_rutas)
        nuevo_kg, anclas = _anclar_kg_mayoristas(por_ruta, detalle, groups, coords)
        historial.append(dict(kg_may=kg_may, groups=groups, excepciones=excepciones,
                              por_ruta=por_ruta, detalle=detalle, meta=meta,
                              coords_rutas=coords_rutas))
        if nuevo_kg == kg_may:
            convergio = True
            break
        kg_may = nuevo_kg

    pasada_elegida = pasada
    if not convergio and len(historial) > 1:
        # No hay punto fijo (típico: ciclo de 2) -- en vez de quedarse con
        # la última pasada por pura paridad de `max_pasadas`, usa la más
        # fiel al historial (ver `_elegir_mejor_pasada`).
        idx = _elegir_mejor_pasada(historial)
        pasada_elegida = idx + 1
        elegida = historial[idx]
        kg_may, groups, excepciones, por_ruta, detalle, meta, coords_rutas = (
            elegida["kg_may"], elegida["groups"], elegida["excepciones"],
            elegida["por_ruta"], elegida["detalle"], elegida["meta"],
            elegida["coords_rutas"])

    # ── garantía dura: ninguna ruta por encima del 100 % ──
    kg_lores = {clave: sum(float(pedidos.get(m["sid"]) or 0) for m in miembros)
                for clave, miembros in groups.items()}
    por_ruta, exc_cupo = reubicar_mayoristas_por_cupo(
        por_ruta, kg_lores, vehiculos_cap, coords_rutas)
    excepciones = list(excepciones) + list(exc_cupo)

    # el detalle refleja el destino FINAL (tras reubicar), no el del enganche
    destino_final = {m.get("id_cliente"): clave
                     for clave, lst in por_ruta.items() for m in lst}
    for d in detalle:
        final = destino_final.get(d["id_cliente"])
        if final and final != d["destino"]:
            d["destino_enganche"] = d["destino"]
            d["destino"] = final
            d["via_destino"] = "REUBICADO_CUPO"

    meta = dict(meta or {}, pasadas_mayoristas=pasada, convergio_mayoristas=convergio,
                pasada_elegida=pasada_elegida,
                kg_mayoristas_anclados=round(sum(kg_may.values()), 1),
                viajes_solo_mayoristas=len([k for k in por_ruta
                                            if k not in groups and por_ruta[k]]))
    if not convergio:
        print("=" * 70)
        print(f"[convrp] el enganche de mayoristas NO llegó a punto fijo en "
              f"{max_pasadas} pasadas (probable ciclo); se usa la pasada "
              f"{pasada_elegida} de {max_pasadas} (la de menos desviaciones "
              f"de unidad_ref, {len(excepciones)}).")
        print("[convrp] revisa `detalle` — puede haber clientes oscilando entre "
              "dos rutas y el reparto de carga no es el óptimo.")
        print("=" * 70)
    return groups, por_ruta, excepciones, detalle, meta
