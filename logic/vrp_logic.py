"""
logic/vrp_logic.py
Lógica VRP Híbrida — ported from app_rutas.py, adapted for Flask + MongoDB.

Combina historial de rutas confirmadas con el algoritmo VRP para generar
asignaciones coherentes con los patrones históricos y la capacidad de flota.
"""

from math import radians, sin, cos, sqrt, atan2
from collections import defaultdict

from sqlalchemy import select

from db import get_db, get_table

# ── Parámetros VRP ────────────────────────────────────────────────────────────
VRP_DEV_NORMAL   = 0.30   # desviación ≤ 30 % → NORMAL
VRP_DEV_ESPECIAL = 0.50   # desviación ≤ 50 % → EDGE_CASE
UMBRAL_PEQUEÑO   = 1300   # kg ≤ este valor → excepción (no restricción de desviación)

# CAP-4: regla de capacidad máxima permitida (igual que en asignacion_logic.py).
# Los únicos vehículos que pueden superar el 100 % de su capacidad nominal son
# los de 3.5 a 4.0 t (3500-4000 kg), con un tope fijo de 3900 kg (promedio de
# carga 3.5 t, sin superar nunca las 4 t). CAP-1.5 (abajo) es la única otra
# excepción -- cualquier otro vehículo tiene como límite máximo exactamente
# el 100 % de su capacidad nominal, sin tolerancia adicional.
#
# NOTA: a diferencia de CAP-4, CAP-1.5 NO está espejada en
# asignacion_logic.py:_capacidad_efectiva_ton() -- deliberado, ese flujo
# (utilización de creacion_rutas/dia_sugerido) no es el que arma el PDF real
# (ver Task 12 del plan de asignación por peso). Si algún día se necesita
# consistencia ahí también, hace falta agregarla a propósito, no asumir que
# ya está.
CAP_EXCEPCION_MIN_KG  = 3500
CAP_EXCEPCION_MAX_KG  = 4000
CAP_EXCEPCION_TOPE_KG = 3900

# CAP-1.5: los vehículos de 1.5 t (1500 kg nominal) tienen tolerancia
# operativa hasta 1549 kg -- confirmado por el usuario, así lo admite la
# empresa. A diferencia de CAP-4 (que LIMITA un nominal ya inflado hacia
# abajo), ésta da margen hacia ARRIBA de un nominal real de 1.5 t.
CAP_1500_MIN_KG  = 1500
CAP_1500_MAX_KG  = 1500
CAP_1500_TOPE_KG = 1549


def capacidad_efectiva_kg(cap_kg: float) -> float:
    """Aplica las reglas CAP-4 y CAP-1.5 a una capacidad nominal en kg."""
    if CAP_EXCEPCION_MIN_KG <= cap_kg <= CAP_EXCEPCION_MAX_KG:
        return float(CAP_EXCEPCION_TOPE_KG)
    if CAP_1500_MIN_KG <= cap_kg <= CAP_1500_MAX_KG:
        return float(CAP_1500_TOPE_KG)
    return float(cap_kg)

_CONSOL_DEV_LOW = 0.50    # ruta >50 % por debajo del histórico → candidata a consolidar
_CONSOL_MAX_KM  = 100.0   # distancia máxima entre centroides para consolidar

VRP_STATUS_LABEL = {
    "NORMAL":      "Normal (≤30%)",
    "EDGE_CASE":   "Caso especial (30–50%)",
    "CRITICO":     "Crítico (>50%)",
    "SOBRECARGA":  "Sobrecarga",
    "SIN_HIST":    "Sin historial",
    "OK_PEQUEÑO":  "Camión pequeño (cap. ok)",
    "CONSOLIDADA": "Consolidada en ruta cercana",
}

DIA_ORDEN = {
    "LUNES": 1, "MARTES": 2, "MIERCOLES": 3,
    "JUEVES": 4, "VIERNES": 5, "SABADO": 6, "DOMINGO": 7,
}


# ── Utilidades geográficas ─────────────────────────────────────────────────────

def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    dl  = radians(lat2 - lat1)
    dlo = radians(lon2 - lon1)
    a   = sin(dl / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlo / 2) ** 2
    return 2 * R * atan2(sqrt(a), sqrt(1 - a))


def nearest_neighbor(sc_dict):
    """
    sc_dict: {id: (lat, lon)}
    Ordena los puntos por nearest-neighbor iniciando por el más al norte.
    """
    ids = list(sc_dict.keys())
    if len(ids) <= 1:
        return ids
    start = max(ids, key=lambda x: sc_dict[x][0])
    visited = [start]
    rem = set(ids) - {start}
    while rem:
        last = visited[-1]
        la, lo = sc_dict[last]
        near = min(rem, key=lambda x: haversine(la, lo, sc_dict[x][0], sc_dict[x][1]))
        visited.append(near)
        rem.remove(near)
    return visited


def ordenar_paradas_por_historico(members: list, coords: dict) -> list:
    """
    Ordena los `sid` de una ruta priorizando la secuencia histórica.

    members: [{"sid": id, "seq": int}, …]  (seq == 999 → sin historial para
              esta ruta: sucursal nueva o movida por consolidación/reasignación)
    coords:  {sid: (lat, lon)}

    Las paradas SIN historial válido se insertan por proximidad geográfica
    junto a su vecino más cercano ya ubicado, en lugar de recalcular toda la
    ruta por distancia (nearest-neighbor), lo que antes descartaba el orden
    histórico de las demás paradas con solo una parada nueva en la ruta.
    """
    def _coord(sid):
        return coords.get(sid, (0.0, 0.0))

    con_hist = [m for m in members if m.get("seq") != 999]
    sin_hist = [m for m in members if m.get("seq") == 999]

    if not con_hist:
        # Ninguna parada tiene historial en esta ruta: única opción es distancia.
        sc = {m["sid"]: _coord(m["sid"]) for m in members}
        return nearest_neighbor(sc)

    seqs = [m["seq"] for m in con_hist]
    if len(seqs) == len(set(seqs)):
        ordenados = sorted(con_hist, key=lambda m: m["seq"])
    else:
        ordenados = _ordenar_empates_por_proximidad(con_hist, _coord)

    for m in sin_hist:
        pos = _insertar_pos_proxima(ordenados, m["sid"], _coord)
        ordenados.insert(pos, m)

    return [m["sid"] for m in ordenados]


def _ordenar_empates_por_proximidad(con_hist: list, coord_fn) -> list:
    """Dentro de cada grupo con el mismo seq histórico, ordena por cercanía encadenada."""
    grupos = defaultdict(list)
    for m in con_hist:
        grupos[m["seq"]].append(m)

    ordenados: list = []
    for seq in sorted(grupos.keys()):
        grupo = grupos[seq]
        if len(grupo) == 1:
            ordenados.append(grupo[0])
            continue
        actual    = coord_fn(ordenados[-1]["sid"]) if ordenados else coord_fn(grupo[0]["sid"])
        restante  = list(grupo)
        while restante:
            siguiente = min(restante, key=lambda m: haversine(actual[0], actual[1], *coord_fn(m["sid"])))
            ordenados.append(siguiente)
            actual = coord_fn(siguiente["sid"])
            restante.remove(siguiente)
    return ordenados


def _insertar_pos_proxima(ordenados: list, sid, coord_fn) -> int:
    """Índice (0-based) tras el cual insertar `sid` junto a su vecino más cercano."""
    if not ordenados:
        return 0
    lat_n, lon_n = coord_fn(sid)
    best_idx, best_dist = len(ordenados), float("inf")
    for i, m in enumerate(ordenados):
        dist = haversine(lat_n, lon_n, *coord_fn(m["sid"]))
        if dist < best_dist:
            best_dist, best_idx = dist, i + 1
    return best_idx


# ── Lectura de datos de MongoDB ────────────────────────────────────────────────

def obtener_capacidades_vehiculos() -> dict:
    """
    Lee capacidades de vehículos desde SQL Server (TODOS, sin filtrar por
    `activo` -- así era también en el Mongo original, a diferencia de
    obtener_placas_por_abrev()/obtener_info_vehiculos() que sí filtran; se
    preserva esa asimetría tal cual).
    Retorna: {abreviatura: capacidad_kg (int)} — ya con las reglas CAP-4 y
    CAP-1.5 aplicadas (tope fijo de 3900 kg para vehículos de 3.5-4 t; tope
    de 1549 kg para vehículos de 1.5 t; 100 % nominal para el resto).
    """
    try:
        db    = get_db()
        tabla = get_table("vehiculos")
        caps  = {}
        for v in db.execute(select(tabla)).mappings():
            abrev   = (v.get("abreviatura") or v.get("descripcion") or "").strip()
            cap_ton = float(v.get("capacidad_toneladas") or 0)
            if abrev and cap_ton > 0:
                caps[abrev] = int(capacidad_efectiva_kg(cap_ton * 1000))
        return caps
    except Exception:
        return {}


def obtener_volumenes_vehiculos() -> dict:
    """
    Lee el volumen (m³) de cada vehículo desde SQL Server.
    Retorna: {abreviatura: volumen_m3 (float)} — solo vehículos con volumen > 0.
    Espejo de obtener_capacidades_vehiculos() pero para el límite volumétrico.
    """
    try:
        db    = get_db()
        tabla = get_table("vehiculos")
        vols  = {}
        for v in db.execute(select(tabla)).mappings():
            abrev = (v.get("abreviatura") or v.get("descripcion") or "").strip()
            vol   = float(v.get("volumen_m3") or 0)
            if abrev and vol > 0:
                vols[abrev] = vol
        return vols
    except Exception:
        return {}


def obtener_placas_por_abrev() -> dict:
    """
    Mapea abreviatura → placas para vehículos activos.
    Retorna: {abreviatura: placas}
    """
    try:
        db     = get_db()
        tabla  = get_table("vehiculos")
        placas = {}
        for v in db.execute(select(tabla).where(tabla.c.activo == True)).mappings():  # noqa: E712
            abrev = (v.get("abreviatura") or v.get("descripcion") or "").strip()
            pl    = (v.get("placas") or "").strip()
            if abrev and pl:
                placas[abrev] = pl
        return placas
    except Exception:
        return {}


def obtener_info_vehiculos() -> dict:
    """
    Información completa de vehículos activos.
    Retorna: {abreviatura: {placas, capacidad_ton, abreviatura, descripcion}}
    """
    try:
        db    = get_db()
        tabla = get_table("vehiculos")
        res   = {}
        for v in db.execute(select(tabla).where(tabla.c.activo == True)).mappings():  # noqa: E712
            abrev   = (v.get("abreviatura") or v.get("descripcion") or "").strip()
            cap_ton = float(v.get("capacidad_toneladas") or 0)
            if abrev:
                res[abrev] = {
                    "placas":        (v.get("placas") or "").strip(),
                    "capacidad_ton": cap_ton,
                    "abreviatura":   abrev,
                    "descripcion":   (v.get("descripcion") or "").strip(),
                    "chofer":        (v.get("chofer") or "").strip(),
                }
        return res
    except Exception:
        return {}


# ── Construcción de template desde historial ───────────────────────────────────

def build_template_from_history(dfs: list, recency_weights: list = None) -> tuple:
    """
    Construye template dinámico desde lista de DataFrames históricos.

    Parámetros
    ----------
    dfs              : list[DataFrame] — ejemplos históricos (más antiguo → más reciente)
    recency_weights  : list[float]    — pesos por ejemplo (None → 1, 2, …, n)

    Retorna
    -------
    template       : {id_sucursal: (vehiculo, dia_semana, seq_mediana)}
    kg_hist        : {id_sucursal: float}  — kg promedio ponderado
    route_stats    : {(vehiculo, dia): dict} — estadísticas por ruta
    historial_rutas: {id_sucursal: {(vehiculo, dia): peso_voto}} — TODAS las
                      rutas a las que ha pertenecido históricamente cada
                      sucursal (no solo la dominante), usado para reasignar
                      sucursales cuando una ruta excede su capacidad.
    """
    if not dfs:
        return {}, {}, {}, {}

    n = len(dfs)
    if recency_weights is None:
        recency_weights = [float(i + 1) for i in range(n)]

    vd_votes    = defaultdict(lambda: defaultdict(float))
    kg_records  = defaultdict(list)
    seq_records = defaultdict(lambda: defaultdict(list))
    route_kg    = defaultdict(list)

    vehiculos_cap = obtener_capacidades_vehiculos()

    for w, df in zip(recency_weights, dfs):
        if df is None:
            continue
        df = df.copy()
        df.columns = [c.strip() for c in df.columns]
        required = ["id_sucursal", "vehiculo", "dia_semana", "secuencia_visita", "kg_entrega"]
        if not all(c in df.columns for c in required):
            continue

        df["id_sucursal"]      = df["id_sucursal"].astype(int)
        df["secuencia_visita"] = df["secuencia_visita"].astype(int)
        df["kg_entrega"]       = df["kg_entrega"].astype(float)
        df["dia_semana"]       = df["dia_semana"].str.strip().str.upper()

        for _, row in df.iterrows():
            sid = int(row["id_sucursal"])
            veh = str(row["vehiculo"]).strip()
            dia = str(row["dia_semana"]).strip()
            vd_votes[sid][(veh, dia)] += w
            kg_records[sid].append((float(row["kg_entrega"]), w))
            seq_records[sid][(veh, dia)].append(int(row["secuencia_visita"]))

        for (veh, dia), grp in df.groupby(["vehiculo", "dia_semana"]):
            route_kg[(str(veh).strip(), str(dia).strip())].append(grp["kg_entrega"].sum())

    # Template: (veh, dia) con mayor peso por sucursal
    template = {}
    for sid, votes in vd_votes.items():
        (veh, dia) = max(votes, key=votes.get)
        seqs = seq_records[sid].get((veh, dia), [])
        seq  = int(round(sum(seqs) / len(seqs))) if seqs else 999
        template[sid] = (veh, dia, seq)

    # kg promedio ponderado por sucursal
    kg_hist = {}
    for sid, records in kg_records.items():
        total_w    = sum(w for _, w in records)
        kg_hist[sid] = sum(kg * w for kg, w in records) / total_w if total_w > 0 else 0.0

    # Estadísticas por ruta (vehiculo, dia)
    route_stats = {}
    for (veh, dia), totals in route_kg.items():
        members_map = {}
        for sid, (tv, td, tseq) in template.items():
            if tv == veh and td == dia:
                seqs = seq_records[sid].get((veh, dia), [])
                members_map[sid] = int(round(sum(seqs) / len(seqs))) if seqs else 999

        ordered = sorted(members_map.items(), key=lambda x: x[1])
        avg = sum(totals) / len(totals)
        std = (sum((t - avg) ** 2 for t in totals) / len(totals)) ** 0.5 if len(totals) > 1 else 0.0
        cap = vehiculos_cap.get(veh, 3500)

        route_stats[(veh, dia)] = {
            "kg_avg":     round(avg, 1),
            "kg_std":     round(std, 1),
            "kg_min":     round(min(totals), 1),
            "kg_max":     round(max(totals), 1),
            "n_ejemplos": len(totals),
            "members":    ordered,
            "capacity":   cap,
            "is_small":   cap <= UMBRAL_PEQUEÑO,
        }

    historial_rutas = {sid: dict(votes) for sid, votes in vd_votes.items()}

    return template, kg_hist, route_stats, historial_rutas


# ── Estado VRP ─────────────────────────────────────────────────────────────────

def _vrp_status(total_kg: float, hist_avg, capacity: int, is_small: bool) -> str:
    if total_kg > capacity:
        return "SOBRECARGA"
    if is_small:
        return "OK_PEQUEÑO"
    if hist_avg is None or hist_avg == 0:
        return "SIN_HIST"
    dev = abs(total_kg - hist_avg) / hist_avg
    if dev > VRP_DEV_ESPECIAL:
        return "CRITICO"
    if dev > VRP_DEV_NORMAL:
        return "EDGE_CASE"
    return "NORMAL"


# ── Centroide de un grupo ──────────────────────────────────────────────────────

def _centroid(mems: list, coords: dict) -> tuple:
    if not mems:
        return (0.0, 0.0)
    lats = [coords.get(m["sid"], (0.0, 0.0))[0] for m in mems]
    lons = [coords.get(m["sid"], (0.0, 0.0))[1] for m in mems]
    return (sum(lats) / len(lats), sum(lons) / len(lons))


# ── Consolidación de rutas subocupadas ─────────────────────────────────────────

def _consolidate_underloaded(groups, kg_map, route_stats, coords, vehiculos_cap):
    """
    Rutas críticamente por debajo del histórico (>50%) se integran en la ruta
    del mismo día más cercana geográficamente, siempre que:
      - La ruta destino tenga capacidad suficiente
      - La ruta destino no supere 50% de desviación tras la fusión
      - La distancia entre centroides sea ≤ 100 km
      - Camiones pequeños (≤ UMBRAL_PEQUEÑO kg) nunca se consolidan
    """
    log = []

    def _total(key):
        return sum(kg_map.get(m["sid"], 0) for m in groups.get(key, []))

    candidates = []
    for (veh, dia) in list(groups.keys()):
        mems = groups[(veh, dia)]
        if not mems:
            continue
        cap = vehiculos_cap.get(veh, 3500)
        if cap <= UMBRAL_PEQUEÑO:
            continue
        hist_avg = route_stats.get((veh, dia), {}).get("kg_avg")
        if not hist_avg or hist_avg == 0:
            continue
        total_kg = _total((veh, dia))
        dev_low  = (hist_avg - total_kg) / hist_avg
        if dev_low > _CONSOL_DEV_LOW:
            candidates.append((veh, dia, dev_low, total_kg))

    candidates.sort(key=lambda x: -x[2])

    for (src_veh, src_dia, dev_low, src_kg) in candidates:
        src_mems = groups.get((src_veh, src_dia), [])
        if not src_mems:
            continue

        src_ctr   = _centroid(src_mems, coords)
        best_dest = None
        best_dist = float("inf")

        for (dst_veh, dst_dia) in list(groups.keys()):
            if (dst_veh, dst_dia) == (src_veh, src_dia):
                continue
            if dst_dia != src_dia:
                continue
            dst_mems = groups[(dst_veh, dst_dia)]
            if not dst_mems:
                continue
            dst_cap = vehiculos_cap.get(dst_veh, 3500)
            if dst_cap <= UMBRAL_PEQUEÑO:
                continue
            dst_total = _total((dst_veh, dst_dia))
            if dst_total + src_kg > dst_cap:
                continue
            dst_hist_avg = route_stats.get((dst_veh, dst_dia), {}).get("kg_avg")
            if dst_hist_avg and dst_hist_avg > 0:
                new_dev = abs(dst_total + src_kg - dst_hist_avg) / dst_hist_avg
                if new_dev > VRP_DEV_ESPECIAL:
                    continue
            dst_ctr = _centroid(dst_mems, coords)
            dist    = haversine(src_ctr[0], src_ctr[1], dst_ctr[0], dst_ctr[1])
            if dist > _CONSOL_MAX_KM:
                continue
            if dist < best_dist:
                best_dist = dist
                best_dest = (dst_veh, dst_dia)

        if best_dest:
            dst_veh, dst_dia = best_dest
            for m in src_mems:
                m["seq"] = 999
                groups[(dst_veh, dst_dia)].append(m)
            log.append({
                "origen":          f"{src_veh} — {src_dia}",
                "destino":         f"{dst_veh} — {dst_dia}",
                "paradas_movidas": len(src_mems),
                "kg_movidos":      round(src_kg),
                "dist_km":         round(best_dist, 1),
                "motivo":          f"Ruta {dev_low * 100:.0f}% por debajo del histórico",
            })
            groups[(src_veh, src_dia)] = []

    return {k: v for k, v in groups.items() if v}, log


# ── Resolución de sobrecarga usando historial ──────────────────────────────────

def _centroide_miembros(miembros: list) -> tuple | None:
    pts = [(m["lat"], m["lon"]) for m in miembros if m.get("lat") is not None and m.get("lon") is not None]
    if not pts:
        return None
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def _resolver_sobrecarga_historica(
    groups: dict,
    pedidos_dict: dict,
    vehiculos_cap: dict,
    historial_rutas: dict,
    max_iter: int = 200,
) -> dict:
    """
    Si una ruta excede su capacidad efectiva, mueve una sucursal hacia otra
    ruta. La sobrecarga NUNCA debe quedar sin resolver mientras exista
    capacidad disponible en alguna otra ruta. Se prueba candidata por
    candidata (de menor a mayor peso histórico en la ruta actual) y, para
    cada una, se busca destino en orden:
      1. Otra (vehículo, día) a la que también haya pertenecido históricamente
         (mayor voto histórico) y que tenga capacidad disponible.
      2. Si ninguna ruta con historial tiene cupo, la más cercana
         geográficamente de entre TODAS las rutas con capacidad disponible
         (proximidad y disponibilidad, sin requisito histórico).
    Solo se deja sobrecargada si NINGUNA ruta del sistema tiene capacidad
    disponible (caso límite: flota completa sin espacio).
    """
    historial_rutas = historial_rutas or {}

    def _total(key):
        return sum(pedidos_dict.get(m["sid"], 0) for m in groups.get(key, []))

    def _mejor_destino(cand: dict, origen: tuple) -> tuple | None:
        cand_kg    = pedidos_dict.get(cand["sid"], 0)
        votos_cand = historial_rutas.get(cand["sid"], {})

        # Nivel 1: ruta con historial de pertenencia y capacidad disponible.
        mejor_dest, mejor_voto = None, -1.0
        for (alt_veh, alt_dia), voto in votos_cand.items():
            if (alt_veh, alt_dia) == origen or (alt_veh, alt_dia) not in groups:
                continue
            if _total((alt_veh, alt_dia)) + cand_kg > vehiculos_cap.get(alt_veh, 3500):
                continue
            if voto > mejor_voto:
                mejor_voto = voto
                mejor_dest = (alt_veh, alt_dia)
        if mejor_dest:
            return mejor_dest

        # Nivel 2: ninguna ruta con historial tiene cupo — se garantiza la
        # resolución por proximidad y disponibilidad entre TODAS las rutas.
        cand_coords = (cand.get("lat"), cand.get("lon")) if cand.get("lat") is not None else None
        mejor_dest, mejor_dist = None, float("inf")
        for (alt_veh, alt_dia) in groups:
            if (alt_veh, alt_dia) == origen:
                continue
            alt_mems = groups[(alt_veh, alt_dia)]
            if _total((alt_veh, alt_dia)) + cand_kg > vehiculos_cap.get(alt_veh, 3500):
                continue
            centro = _centroide_miembros(alt_mems) if alt_mems else None
            if cand_coords and centro:
                dist = haversine(cand_coords[0], cand_coords[1], centro[0], centro[1])
            else:
                dist = 0.0  # ruta vacía o sin coordenadas: candidata válida igual
            if dist < mejor_dist:
                mejor_dist = dist
                mejor_dest = (alt_veh, alt_dia)
        return mejor_dest

    for _ in range(max_iter):
        cambio = False
        for (veh, dia) in list(groups.keys()):
            miembros = groups.get((veh, dia), [])
            if not miembros or len(miembros) <= 1:
                continue
            cap = vehiculos_cap.get(veh, 3500)
            if _total((veh, dia)) <= cap:
                continue

            # Probar cada sucursal (de menor a mayor voto histórico en esta
            # ruta) hasta encontrar una con destino disponible.
            candidatas = sorted(
                miembros,
                key=lambda m: historial_rutas.get(m["sid"], {}).get((veh, dia), 0.0),
            )
            for cand in candidatas:
                mejor_dest = _mejor_destino(cand, (veh, dia))
                if mejor_dest:
                    miembros.remove(cand)
                    cand["seq"] = 999
                    groups[mejor_dest].append(cand)
                    cambio = True
                    break

        if not cambio:
            break

    return groups


# ── Generación principal VRP ───────────────────────────────────────────────────

def generate_routes_vrp(
    pedidos_dict:    dict,
    coords_dict:     dict,
    active_template: dict,
    kg_hist:         dict,
    route_stats:     dict,
    vehiculos_cap:   dict,
    historial_rutas: dict | None = None,
) -> tuple:
    """
    Genera rutas VRP híbridas.

    Parámetros
    ----------
    pedidos_dict    : {num_tienda (int): kg_entrega (float)}
    coords_dict     : {num_tienda (int): (lat, lon)}
    active_template : {num_tienda (int): (vehiculo, dia_semana, seq)}
    kg_hist         : {num_tienda (int): float}
    route_stats     : {(vehiculo, dia): dict}
    vehiculos_cap   : {abreviatura (str): capacidad_kg (int)}
    historial_rutas : {num_tienda (int): {(vehiculo, dia): peso_voto}} — todas
                       las rutas a las que ha pertenecido históricamente cada
                       sucursal (ver build_template_from_history). Se usa para
                       reasignar sucursales cuando una ruta excede su capacidad.

    Retorna
    -------
    rows         : list[dict]  — filas de asignación por sucursal
    report_rows  : list[dict]  — reporte de estado VRP por ruta
    consol_log   : list[dict]  — registro de consolidaciones realizadas
    """
    # ── Asignar (vehiculo, dia) según template histórico ──────────────────────
    groups = defaultdict(list)

    for num_tienda, kg in pedidos_dict.items():
        lat, lon = coords_dict.get(num_tienda, (0.0, 0.0))

        if num_tienda in active_template:
            veh, dia, seq = active_template[num_tienda]
        else:
            # Fallback geográfico para sucursales no vistas en historial
            near = min(
                (k for k in active_template if k in coords_dict),
                key=lambda k: haversine(lat, lon, coords_dict[k][0], coords_dict[k][1]),
                default=None,
            )
            if near:
                veh, dia = active_template[near][0], active_template[near][1]
                seq = 999
            elif vehiculos_cap:
                veh, dia, seq = next(iter(vehiculos_cap)), "LUNES", 999
            else:
                veh, dia, seq = "VEHICULO", "LUNES", 999

        groups[(veh, dia)].append({"sid": num_tienda, "seq": seq, "lat": lat, "lon": lon})

    # ── Consolidar rutas críticamente subocupadas ──────────────────────────────
    consol_log = []
    if route_stats:
        groups, consol_log = _consolidate_underloaded(
            groups, pedidos_dict, route_stats, coords_dict, vehiculos_cap
        )

    # ── Resolver sobrecarga: nunca debe quedar un vehículo sobre su capacidad
    # mientras exista cupo en otra ruta (histórica primero, proximidad después) ──
    groups = _resolver_sobrecarga_historica(
        groups, pedidos_dict, vehiculos_cap, historial_rutas
    )

    rows        = []
    report_rows = []

    consol_dst_map = {}
    for entry in consol_log:
        dst_key = entry["destino"]
        if dst_key not in consol_dst_map:
            consol_dst_map[dst_key] = []
        consol_dst_map[dst_key].append(
            f"+{entry['paradas_movidas']} paradas de {entry['origen']} ({entry['kg_movidos']:,} kg)"
        )

    for (veh, dia), members in sorted(groups.items()):
        total_kg = sum(pedidos_dict.get(m["sid"], 0) for m in members)
        capacity = vehiculos_cap.get(veh, 3500)
        is_small = capacity <= UMBRAL_PEQUEÑO

        hist     = route_stats.get((veh, dia), {})
        hist_avg = hist.get("kg_avg", None)

        deviation = None
        if hist_avg and hist_avg > 0:
            deviation = abs(total_kg - hist_avg) / hist_avg

        status   = _vrp_status(total_kg, hist_avg, capacity, is_small)
        ruta_key = f"{veh} — {dia}"
        notas    = "; ".join(consol_dst_map.get(ruta_key, []))

        report_rows.append({
            "vehiculo":     veh,
            "dia_semana":   dia,
            "sucursales":   len(members),
            "kg_total":     round(total_kg),
            "capacidad_kg": capacity,
            "is_small":     is_small,
            "uso_%":        round(total_kg / capacity * 100, 1) if capacity > 0 else 0,
            "estado":       status,
            "notas":        notas,
        })

        # Secuencia: prioriza el orden histórico de cada parada. Las paradas sin
        # historial válido (nuevas o movidas por consolidación) se insertan por
        # proximidad geográfica, sin descartar el orden histórico del resto.
        coords_members = {m["sid"]: (m["lat"], m["lon"]) for m in members}
        ordered = ordenar_paradas_por_historico(members, coords_members)

        for i, sid in enumerate(ordered, 1):
            rows.append({
                "num_tienda":       sid,
                "vehiculo":         veh,
                "dia_semana":       dia,
                "secuencia_visita": i,
                "kg_entrega":       int(pedidos_dict.get(sid, 0)),
            })

    # Agregar filas de rutas consolidadas al reporte
    for entry in consol_log:
        src_parts   = entry["origen"].split(" — ")
        src_veh     = src_parts[0]
        src_dia     = src_parts[1] if len(src_parts) > 1 else ""
        hist_avg_src = route_stats.get((src_veh, src_dia), {}).get("kg_avg")
        report_rows.append({
            "vehiculo":     src_veh,
            "dia_semana":   src_dia,
            "sucursales":   entry["paradas_movidas"],
            "kg_total":     entry["kg_movidos"],
            "kg_hist_avg":  round(hist_avg_src) if hist_avg_src else None,
            "desviacion_%": None,
            "capacidad_kg": vehiculos_cap.get(src_veh, 3500),
            "uso_%":        None,
            "estado":       "CONSOLIDADA",
            "notas":        f"→ {entry['destino']} ({entry['dist_km']} km) · {entry['motivo']}",
        })

    # Ordenar filas por vehiculo, dia, secuencia
    rows.sort(key=lambda r: (
        r["vehiculo"],
        DIA_ORDEN.get(r["dia_semana"], 9),
        r["secuencia_visita"],
    ))

    return rows, report_rows, consol_log
