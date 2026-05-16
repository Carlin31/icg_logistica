"""
logic/mayoristas_logic.py
Calculo de mayoristas por cercania y secuencia integrada.
"""
import math
from datetime import datetime
from bson import ObjectId
from bson.errors import InvalidId

from db import get_db


def _parse_oid(doc_id: str) -> ObjectId | None:
    try:
        return ObjectId(doc_id)
    except (InvalidId, TypeError):
        return None


def _to_float(valor):
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def _extraer_secuencia_sucursal(sucursal: dict) -> "int | None":
    for key in ("secuencia_visita", "orden", "secuencia", "seq"):
        valor = sucursal.get(key)
        if valor is None:
            continue
        try:
            return int(float(valor))
        except (TypeError, ValueError):
            continue
    return None


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _ordenar_sucursales_planificacion(sucursales: list) -> list:
    if not isinstance(sucursales, list) or len(sucursales) <= 1:
        return sucursales or []

    secuencias = [_extraer_secuencia_sucursal(s) for s in sucursales]
    if all(seq is not None for seq in secuencias) and len(set(secuencias)) == len(secuencias):
        return sorted(sucursales, key=lambda s: _extraer_secuencia_sucursal(s) or 999999)

    con_coords = []
    sin_coords = []
    for idx, s in enumerate(sucursales):
        lat = _to_float(s.get("latitud"))
        lon = _to_float(s.get("longitud"))
        if lat is None or lon is None:
            sin_coords.append((idx, s))
            continue
        con_coords.append((idx, s, lat, lon))

    if len(con_coords) <= 1:
        return list(sucursales)

    pendientes = list(con_coords)
    actual = max(pendientes, key=lambda t: (t[2], -t[0]))
    ordenadas = [actual]
    pendientes.remove(actual)

    while pendientes:
        _, _, lat_a, lon_a = ordenadas[-1]
        prox = min(
            pendientes,
            key=lambda t: (_haversine_km(lat_a, lon_a, t[2], t[3]), t[0]),
        )
        ordenadas.append(prox)
        pendientes.remove(prox)

    resultado = [t[1] for t in ordenadas]
    if sin_coords:
        resultado.extend(s for _idx, s in sorted(sin_coords, key=lambda t: t[0]))
    return resultado


def _leer_pesos_mayoristas(db, oid: ObjectId) -> tuple[dict, dict]:
    pesos: dict = {}
    nombres: dict = {}
    ext = db["extraccion"].find_one({"logistica_id": oid})
    if not ext:
        return pesos, nombres
    for m in ext.get("mayoristas", []):
        codigo = m.get("codigo") or m.get("id_cliente")
        if codigo is None:
            continue
        try:
            peso = float(m.get("peso_total_kg", 0) or 0)
            if peso <= 0:
                continue
            id_int = int(str(codigo).split(".")[0])
            pesos[id_int] = peso
            nombre = (m.get("nombre") or "").strip()
            if nombre:
                nombres[id_int] = nombre
        except (TypeError, ValueError):
            continue
    return pesos, nombres


def _leer_coords_mayoristas(db, ids: set) -> dict:
    coords: dict = {}
    if not ids:
        return coords
    for c in db["clientes_mayoristas"].find(
        {},
        {"_id": 0, "id_cliente": 1, "nombre": 1, "latitud": 1, "longitud": 1},
    ):
        id_cl = c.get("id_cliente")
        if id_cl is None:
            continue
        try:
            id_int = int(str(id_cl).split(".")[0])
        except (TypeError, ValueError):
            continue
        if id_int not in ids:
            continue
        coords[id_int] = {
            "nombre": (c.get("nombre") or "").strip(),
            "latitud": _to_float(c.get("latitud")),
            "longitud": _to_float(c.get("longitud")),
        }
    return coords


def _integrar_paradas(sucursales: list, mayoristas: list) -> list:
    sucs = list(sucursales)
    if not mayoristas:
        paradas = []
        for s in sucs:
            paradas.append({
                "tipo": "sucursal",
                "num_tienda": s.get("num_tienda"),
                "nombre_base": s.get("nombre_base") or s.get("nombre") or "",
                "latitud": _to_float(s.get("latitud")),
                "longitud": _to_float(s.get("longitud")),
            })
        for idx, p in enumerate(paradas, start=1):
            p["orden"] = idx
        return paradas

    buckets: dict[int, list] = {}
    sin_idx: list = []
    for m in mayoristas:
        lat_m = _to_float(m.get("latitud"))
        lon_m = _to_float(m.get("longitud"))
        if lat_m is None or lon_m is None:
            sin_idx.append(m)
            continue
        best_idx = None
        best_dist = float("inf")
        for i, s in enumerate(sucs):
            lat_s = _to_float(s.get("latitud"))
            lon_s = _to_float(s.get("longitud"))
            if lat_s is None or lon_s is None:
                continue
            dist = _haversine_km(lat_m, lon_m, lat_s, lon_s)
            if dist < best_dist:
                best_dist = dist
                best_idx = i
        if best_idx is None:
            sin_idx.append(m)
        else:
            m["desvio_m"] = round(best_dist * 1000, 1)
            buckets.setdefault(best_idx, []).append((best_dist, m))

    paradas: list = []
    for i, s in enumerate(sucs):
        paradas.append({
            "tipo": "sucursal",
            "num_tienda": s.get("num_tienda"),
            "nombre_base": s.get("nombre_base") or s.get("nombre") or "",
            "latitud": _to_float(s.get("latitud")),
            "longitud": _to_float(s.get("longitud")),
        })
        for _dist, m in sorted(buckets.get(i, []), key=lambda t: t[0]):
            paradas.append({
                "tipo": "mayorista",
                "id_cliente": m.get("id_cliente"),
                "nombre_base": m.get("nombre") or "",
                "latitud": _to_float(m.get("latitud")),
                "longitud": _to_float(m.get("longitud")),
                "peso_kg": float(m.get("peso_kg") or 0),
                "desvio_m": m.get("desvio_m"),
            })

    for m in sin_idx:
        paradas.append({
            "tipo": "mayorista",
            "id_cliente": m.get("id_cliente"),
            "nombre_base": m.get("nombre") or "",
            "latitud": _to_float(m.get("latitud")),
            "longitud": _to_float(m.get("longitud")),
            "peso_kg": float(m.get("peso_kg") or 0),
            "desvio_m": m.get("desvio_m"),
        })

    for idx, p in enumerate(paradas, start=1):
        p["orden"] = idx
    return paradas


def calcular_distribucion_mayoristas(logistica_id: str, rutas: list | None = None) -> dict:
    oid = _parse_oid(logistica_id)
    if not oid:
        return {
            "mayoristas_por_ruta": {},
            "paradas_integradas": {},
            "orden_sucursales": {},
            "todos_mayoristas": [],
            "sin_asignar": [],
            "sin_coords": [],
        }

    db = get_db()
    pesos_may, nombres_may = _leer_pesos_mayoristas(db, oid)
    ids = set(pesos_may.keys())
    if not ids:
        return {
            "mayoristas_por_ruta": {},
            "paradas_integradas": {},
            "orden_sucursales": {},
            "todos_mayoristas": [],
            "sin_asignar": [],
            "sin_coords": [],
        }

    coords_may = _leer_coords_mayoristas(db, ids)
    mayoristas: list = []
    sin_coords: list = []
    for id_int, peso in pesos_may.items():
        cat = coords_may.get(id_int) or {}
        lat = cat.get("latitud")
        lon = cat.get("longitud")
        nombre = cat.get("nombre") or nombres_may.get(id_int) or ""
        entry = {
            "id_cliente": id_int,
            "nombre": nombre,
            "peso_kg": float(peso),
            "latitud": lat,
            "longitud": lon,
        }
        if lat is None or lon is None:
            sin_coords.append(entry)
        else:
            mayoristas.append(entry)

    if rutas is None:
        rutas = list(db["rutas_config"].find({}, {"sucursales": 1}))

    rutas_index: dict = {}
    rutas_sucursales: dict = {}
    for r in rutas or []:
        rid = str(r.get("_id") or r.get("id") or "")
        if not rid:
            continue
        rutas_index[rid] = r
        rutas_sucursales[rid] = _ordenar_sucursales_planificacion(r.get("sucursales", []))

    mayoristas_por_ruta: dict = {rid: [] for rid in rutas_index.keys()}
    sin_asignar: list = []

    for m in mayoristas:
        best_rid = None
        best_dist = float("inf")
        for rid, sucs in rutas_sucursales.items():
            if not sucs:
                continue
            dist_min = float("inf")
            for s in sucs:
                lat_s = _to_float(s.get("latitud"))
                lon_s = _to_float(s.get("longitud"))
                if lat_s is None or lon_s is None:
                    continue
                d = _haversine_km(m["latitud"], m["longitud"], lat_s, lon_s)
                if d < dist_min:
                    dist_min = d
            if dist_min < best_dist:
                best_dist = dist_min
                best_rid = rid
        if best_rid:
            m_copy = dict(m)
            m_copy["desvio_m"] = round(best_dist * 1000, 1)
            mayoristas_por_ruta.setdefault(best_rid, []).append(m_copy)
        else:
            sin_asignar.append(dict(m))

    paradas_integradas: dict = {}
    orden_sucursales: dict = {}

    for rid, sucs in rutas_sucursales.items():
        mays = mayoristas_por_ruta.get(rid, [])
        paradas = _integrar_paradas(sucs, mays)
        paradas_integradas[rid] = paradas
        orden_map: dict = {}
        for p in paradas:
            if p.get("tipo") != "sucursal":
                continue
            nt = p.get("num_tienda")
            if nt is None:
                continue
            orden_map[str(nt)] = p.get("orden")
        orden_sucursales[rid] = orden_map

        # Propagar orden a mayoristas para consumo directo
        orden_may: dict = {}
        for p in paradas:
            if p.get("tipo") != "mayorista":
                continue
            id_cl = p.get("id_cliente")
            if id_cl is None:
                continue
            orden_may[int(id_cl)] = p.get("orden")
        for m in mays:
            id_cl = m.get("id_cliente")
            if id_cl is None:
                continue
            if int(id_cl) in orden_may:
                m["orden"] = orden_may[int(id_cl)]

    return {
        "mayoristas_por_ruta": mayoristas_por_ruta,
        "paradas_integradas": paradas_integradas,
        "orden_sucursales": orden_sucursales,
        "todos_mayoristas": mayoristas,
        "sin_asignar": sin_asignar,
        "sin_coords": sin_coords,
        "actualizado_en": datetime.now().isoformat(),
    }
