"""
logic/mayoristas_logic.py
Asignación de mayoristas mediante cheapest insertion global.

Algoritmo:
  1. Se construye la secuencia base de cada ruta:
       depósito → suc_0 → suc_1 → … → suc_n → depósito
  2. Se procesan los mayoristas de forma greedy: en cada iteración se
     elige el par (mayorista, posición_en_ruta) que produce el menor
     incremento de distancia total (Δ = d(prev,m) + d(m,nxt) - d(prev,nxt)).
  3. El mayorista se inserta en esa posición y la secuencia se actualiza
     para los cálculos siguientes.

Esto garantiza que cada mayorista queda en el hueco más conveniente
de la ruta —antes de la primera parada, entre dos sucursales o al final—
en lugar de siempre después de la sucursal más cercana.
"""
import math
from datetime import datetime
from bson import ObjectId
from bson.errors import InvalidId

from db import get_db


# ── Utilidades ─────────────────────────────────────────────────────────────────

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


def _costo_insercion(prev: dict, nuevo: dict, nxt: dict) -> float:
    """
    Incremento de distancia al insertar 'nuevo' entre 'prev' y 'nxt'.
    Δ = d(prev,nuevo) + d(nuevo,nxt) - d(prev,nxt)
    """
    return (
        _haversine_km(prev["latitud"], prev["longitud"],
                      nuevo["latitud"], nuevo["longitud"])
        + _haversine_km(nuevo["latitud"], nuevo["longitud"],
                        nxt["latitud"],  nxt["longitud"])
        - _haversine_km(prev["latitud"], prev["longitud"],
                        nxt["latitud"],  nxt["longitud"])
    )


# ── Ordenamiento base de sucursales ────────────────────────────────────────────

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


# ── Lectura de datos ───────────────────────────────────────────────────────────

def _leer_depot(db) -> tuple:
    """Coordenadas del depósito (matriz logística) desde la configuración."""
    cfg = db["configuracion"].find_one({}) or {}
    lat = _to_float(cfg.get("matriz_lat")) or 18.87329315661368
    lon = _to_float(cfg.get("matriz_lon")) or -96.9491574270346
    return lat, lon


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
            "nombre":    (c.get("nombre") or "").strip(),
            "latitud":   _to_float(c.get("latitud")),
            "longitud":  _to_float(c.get("longitud")),
        }
    return coords


# ── Integración de paradas ─────────────────────────────────────────────────────

def _integrar_paradas(sucursales: list, mayoristas: list,
                      depot_lat: float = None, depot_lon: float = None) -> list:
    """
    Inserta los mayoristas en la secuencia de sucursales usando cheapest
    insertion cuando se conocen las coordenadas del depósito, o evaluando
    solo los huecos entre sucursales cuando no se dispone del depósito
    (compatibilidad con llamadas externas que no lo pasan).

    Parámetros
    ----------
    sucursales  : lista de dicts con lat/lon y metadatos de sucursal
    mayoristas  : lista de dicts con lat/lon y metadatos de mayorista
    depot_lat   : latitud del depósito (opcional)
    depot_lon   : longitud del depósito (opcional)
    """
    sucs_nodos = [
        {
            "tipo":       "sucursal",
            "num_tienda": s.get("num_tienda"),
            "nombre_base": s.get("nombre_base") or s.get("nombre") or "",
            "latitud":    _to_float(s.get("latitud")),
            "longitud":   _to_float(s.get("longitud")),
        }
        for s in sucursales
    ]

    if not mayoristas:
        for idx, p in enumerate(sucs_nodos, start=1):
            p["orden"] = idx
        return sucs_nodos

    mays_con = [m for m in mayoristas
                if _to_float(m.get("latitud")) is not None
                and _to_float(m.get("longitud")) is not None]
    mays_sin = [m for m in mayoristas if m not in mays_con]

    # Construir secuencia de trabajo con o sin depósito
    if depot_lat is not None and depot_lon is not None:
        _sentinel = {"latitud": depot_lat, "longitud": depot_lon, "_depot": True}
        secuencia = [_sentinel] + sucs_nodos + [_sentinel]
    else:
        # Sin depósito: solo los huecos entre sucursales
        secuencia = list(sucs_nodos)

    # Cheapest insertion greedy sobre los mayoristas con coordenadas
    pendientes = list(mays_con)
    while pendientes:
        best_mi   = None
        best_pos  = None
        best_cost = float("inf")

        n_gaps = len(secuencia) - 1 if depot_lat is not None else len(secuencia) + 1
        for mi, m in enumerate(pendientes):
            m_node = {
                "latitud":  _to_float(m.get("latitud")),
                "longitud": _to_float(m.get("longitud")),
            }
            # Evaluar todos los huecos de la secuencia actual
            for i in range(len(secuencia) - 1):
                prev = secuencia[i]
                nxt  = secuencia[i + 1]
                if prev.get("latitud") is None or nxt.get("latitud") is None:
                    continue
                costo = _costo_insercion(prev, m_node, nxt)
                if costo < best_cost:
                    best_cost = costo
                    best_mi   = mi
                    best_pos  = i + 1

        if best_mi is None:
            mays_sin.extend(pendientes)
            break

        m = pendientes.pop(best_mi)
        nodo = {
            "tipo":       "mayorista",
            "id_cliente": m.get("id_cliente"),
            "nombre_base": m.get("nombre") or "",
            "latitud":    _to_float(m.get("latitud")),
            "longitud":   _to_float(m.get("longitud")),
            "peso_kg":    float(m.get("peso_kg") or 0),
            "desvio_m":   round(best_cost * 1000, 1),
        }
        secuencia.insert(best_pos, nodo)

    # Quitar centinelas del depósito y agregar paradas sin coords al final
    paradas = [p for p in secuencia if not p.get("_depot")]

    for m in mays_sin:
        paradas.append({
            "tipo":       "mayorista",
            "id_cliente": m.get("id_cliente"),
            "nombre_base": m.get("nombre") or "",
            "latitud":    _to_float(m.get("latitud")),
            "longitud":   _to_float(m.get("longitud")),
            "peso_kg":    float(m.get("peso_kg") or 0),
            "desvio_m":   m.get("desvio_m"),
        })

    for idx, p in enumerate(paradas, start=1):
        p["orden"] = idx
    return paradas


# ── Función principal ──────────────────────────────────────────────────────────

def calcular_distribucion_mayoristas(logistica_id: str, rutas: list | None = None) -> dict:
    """
    Calcula la distribución de mayoristas por ruta usando cheapest insertion
    global: en cada paso se selecciona el par (mayorista, hueco_en_ruta) que
    minimiza el incremento de distancia total, considerando la secuencia
    completa depósito → sucursales → depósito de cada ruta.
    """
    oid = _parse_oid(logistica_id)
    if not oid:
        return {
            "mayoristas_por_ruta": {},
            "paradas_integradas":  {},
            "orden_sucursales":    {},
            "todos_mayoristas":    [],
            "sin_asignar":         [],
            "sin_coords":          [],
        }

    db = get_db()
    pesos_may, nombres_may = _leer_pesos_mayoristas(db, oid)
    ids = set(pesos_may.keys())
    if not ids:
        return {
            "mayoristas_por_ruta": {},
            "paradas_integradas":  {},
            "orden_sucursales":    {},
            "todos_mayoristas":    [],
            "sin_asignar":         [],
            "sin_coords":          [],
        }

    coords_may = _leer_coords_mayoristas(db, ids)
    mayoristas: list = []
    sin_coords: list = []
    for id_int, peso in pesos_may.items():
        cat    = coords_may.get(id_int) or {}
        lat    = cat.get("latitud")
        lon    = cat.get("longitud")
        nombre = cat.get("nombre") or nombres_may.get(id_int) or ""
        entry  = {
            "id_cliente": id_int,
            "nombre":     nombre,
            "peso_kg":    float(peso),
            "latitud":    lat,
            "longitud":   lon,
        }
        if lat is None or lon is None:
            sin_coords.append(entry)
        else:
            mayoristas.append(entry)

    if rutas is None:
        rutas = list(db["rutas_config"].find({}, {"sucursales": 1}))

    rutas_index:     dict = {}
    rutas_sucursales: dict = {}
    for r in rutas or []:
        rid = str(r.get("_id") or r.get("id") or "")
        if not rid:
            continue
        rutas_index[rid]     = r
        rutas_sucursales[rid] = _ordenar_sucursales_planificacion(r.get("sucursales", []))

    # ── Depósito ────────────────────────────────────────────────────────────
    depot_lat, depot_lon = _leer_depot(db)
    _depot = {"latitud": depot_lat, "longitud": depot_lon, "_depot": True}

    # ── Secuencias iniciales: depósito → sucursales → depósito ─────────────
    rutas_paradas: dict = {}
    for rid, sucs in rutas_sucursales.items():
        seq = [_depot]
        for s in sucs:
            seq.append({
                "tipo":       "sucursal",
                "num_tienda": s.get("num_tienda"),
                "nombre_base": s.get("nombre_base") or s.get("nombre") or "",
                "latitud":    _to_float(s.get("latitud")),
                "longitud":   _to_float(s.get("longitud")),
            })
        seq.append(_depot)
        rutas_paradas[rid] = seq

    # ── Cheapest insertion global ───────────────────────────────────────────
    # En cada iteración se elige el mayorista+hueco con menor Δ distancia.
    mayoristas_por_ruta: dict = {rid: [] for rid in rutas_index}
    sin_asignar:         list = []
    pendientes = list(mayoristas)

    while pendientes:
        best_mi   = None
        best_rid  = None
        best_pos  = None
        best_cost = float("inf")

        for mi, m in enumerate(pendientes):
            m_node = {"latitud": m["latitud"], "longitud": m["longitud"]}
            for rid, seq in rutas_paradas.items():
                for i in range(len(seq) - 1):
                    prev = seq[i]
                    nxt  = seq[i + 1]
                    if prev.get("latitud") is None or nxt.get("latitud") is None:
                        continue
                    costo = _costo_insercion(prev, m_node, nxt)
                    if costo < best_cost:
                        best_cost = costo
                        best_mi   = mi
                        best_rid  = rid
                        best_pos  = i + 1

        if best_mi is None:
            sin_asignar.extend(pendientes)
            break

        m = pendientes.pop(best_mi)
        m_parada = {
            "tipo":       "mayorista",
            "id_cliente": m["id_cliente"],
            "nombre_base": m.get("nombre") or "",
            "latitud":    m["latitud"],
            "longitud":   m["longitud"],
            "peso_kg":    float(m.get("peso_kg") or 0),
            "desvio_m":   round(best_cost * 1000, 1),
        }
        rutas_paradas[best_rid].insert(best_pos, m_parada)

        m_copy = dict(m)
        m_copy["desvio_m"] = round(best_cost * 1000, 1)
        mayoristas_por_ruta[best_rid].append(m_copy)

    # ── Construir paradas_integradas y orden_sucursales ────────────────────
    paradas_integradas: dict = {}
    orden_sucursales:   dict = {}

    for rid, seq in rutas_paradas.items():
        paradas = [p for p in seq if not p.get("_depot")]
        for idx, p in enumerate(paradas, start=1):
            p["orden"] = idx
        paradas_integradas[rid] = paradas

        orden_map: dict = {}
        for p in paradas:
            if p.get("tipo") != "sucursal":
                continue
            nt = p.get("num_tienda")
            if nt is not None:
                orden_map[str(nt)] = p.get("orden")
        orden_sucursales[rid] = orden_map

        orden_may: dict = {}
        for p in paradas:
            if p.get("tipo") != "mayorista":
                continue
            id_cl = p.get("id_cliente")
            if id_cl is not None:
                orden_may[int(id_cl)] = p.get("orden")
        for m in mayoristas_por_ruta.get(rid, []):
            id_cl = m.get("id_cliente")
            if id_cl is not None and int(id_cl) in orden_may:
                m["orden"] = orden_may[int(id_cl)]

    return {
        "mayoristas_por_ruta": mayoristas_por_ruta,
        "paradas_integradas":  paradas_integradas,
        "orden_sucursales":    orden_sucursales,
        "todos_mayoristas":    mayoristas,
        "sin_asignar":         sin_asignar,
        "sin_coords":          sin_coords,
        "actualizado_en":      datetime.now().isoformat(),
    }
