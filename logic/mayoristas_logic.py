"""
logic/mayoristas_logic.py
Distribución de mayoristas con el mismo flujo histórico y de VPR que las sucursales.

Se elimina la lógica de cheapest insertion. En su lugar:
  1. Se usa la estructura histórica ya persistida para recordar orden y recorrido.
  2. La asignación por ruta se resuelve por proximidad al conjunto de sucursales
     de cada ruta, sin insertar por costo marginal mínimo.
  3. El resultado se puede persistir en el histórico con el mismo contrato que
     las rutas confirmadas, pero marcado como tipo_registro='mayoristas'.
"""

import json
import math
from datetime import datetime

from bson import ObjectId
from bson.errors import InvalidId
from sqlalchemy import select, insert

from db import get_db, get_table
from logic.vrp_logic import capacidad_efectiva_kg


def _id_valido(doc_id: str) -> "str | None":
    try:
        return str(ObjectId(doc_id))
    except (InvalidId, TypeError):
        return None


def _to_float(valor):
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def _normalizar_id(valor) -> "int | None":
    try:
        return int(str(valor).split(".")[0])
    except (TypeError, ValueError, IndexError):
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


def _insertar_pos_proxima(route: list, nuevo: dict) -> int:
    """
    Devuelve el índice (0-based) DESPUÉS del cual insertar `nuevo`
    para colocarlo junto a su parada más cercana en `route`.
    Si `nuevo` no tiene coordenadas, devuelve len(route) (al final).
    """
    lat_n = _to_float(nuevo.get("latitud"))
    lon_n = _to_float(nuevo.get("longitud"))
    if lat_n is None or lon_n is None:
        return len(route)

    best_idx = len(route)
    best_dist = float("inf")
    for i, parada in enumerate(route):
        lat_p = _to_float(parada.get("latitud"))
        lon_p = _to_float(parada.get("longitud"))
        if lat_p is None or lon_p is None:
            continue
        dist = _haversine_km(lat_n, lon_n, lat_p, lon_p)
        if dist < best_dist:
            best_dist = dist
            best_idx = i + 1   # insertar DESPUÉS de esta parada
    return best_idx


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


def _leer_depot(db) -> tuple:
    tabla = get_table("configuracion")
    cfg = db.execute(select(tabla)).mappings().first() or {}
    lat = _to_float(cfg.get("matriz_lat")) or 18.87329315661368
    lon = _to_float(cfg.get("matriz_lon")) or -96.9491574270346
    return lat, lon


def _leer_pesos_mayoristas(db, oid: str) -> tuple:
    """
    Lee los pedidos de mayoristas desde extraccion.mayoristas (columna JSON).

    Retorna:
        pedidos : list de {documento, id_cliente, peso}  — uno por documento/pedido
        nombres : {id_cliente: nombre}
    """
    pedidos: list = []
    nombres: dict = {}
    tabla = get_table("extraccion")
    fila = db.execute(select(tabla.c.mayoristas).where(tabla.c.logistica_id == oid)).mappings().first()
    if not fila or not fila["mayoristas"]:
        return pedidos, nombres
    for m in json.loads(fila["mayoristas"]):
        codigo = m.get("codigo") or m.get("id_cliente")
        id_int = _normalizar_id(codigo)
        if id_int is None:
            continue
        try:
            peso = float(m.get("peso_total_kg", 0) or 0)
        except (TypeError, ValueError):
            continue
        # peso 0 = pedido pendiente (sin peso capturado en el origen): se
        # conserva para ofrecerlo como opción de agregado manual a una ruta.
        # Solo se descartan pesos negativos (datos inválidos).
        if peso < 0:
            continue
        documento = str(m.get("documento", "") or id_int).strip()
        pedidos.append({"documento": documento, "id_cliente": id_int, "peso": peso})
        nombre = (m.get("nombre") or "").strip()
        if nombre:
            nombres[id_int] = nombre
    return pedidos, nombres


def _leer_coords_mayoristas(db, ids: set) -> dict:
    coords: dict = {}
    if not ids:
        return coords
    tabla = get_table("clientes_mayoristas")
    for c in db.execute(select(tabla.c.id_cliente, tabla.c.nombre, tabla.c.latitud, tabla.c.longitud)):
        id_int = _normalizar_id(c.id_cliente)
        if id_int is None or id_int not in ids:
            continue
        coords[id_int] = {
            "nombre": (c.nombre or "").strip(),
            "latitud": _to_float(c.latitud),
            "longitud": _to_float(c.longitud),
        }
    return coords


def _ruta_centroid(sucursales: list) -> tuple:
    coords = [
        (_to_float(s.get("latitud")), _to_float(s.get("longitud")))
        for s in sucursales
    ]
    coords = [(lat, lon) for lat, lon in coords if lat is not None and lon is not None]
    if not coords:
        return None, None
    return (
        sum(lat for lat, _ in coords) / len(coords),
        sum(lon for _, lon in coords) / len(coords),
    )


def _cargar_historico_mayoristas(logistica_id: str) -> dict:
    """
    Recupera el último orden de mayoristas guardado para esta logística.
    El índice resultante es {ruta_id: {id_cliente: orden}}.
    """
    oid = _id_valido(logistica_id)
    if not oid:
        return {}

    try:
        db    = get_db()
        tabla = get_table("rutas_historicas")
        docs = [
            dict(d) for d in db.execute(
                select(tabla.c.filas, tabla.c.confirmada, tabla.c.fecha_inicio, tabla.c.cargado_en)
                .where(tabla.c.logistica_id == str(logistica_id), tabla.c.tipo_registro == "mayoristas")
            ).mappings()
        ]
        docs.sort(key=lambda doc: (bool(doc.get("confirmada")), doc.get("fecha_inicio") or "", doc.get("cargado_en") or ""))

        orden_por_ruta: dict = {}
        for doc in docs:
            filas = json.loads(doc["filas"]) if doc.get("filas") else []
            for fila in filas:
                ruta_id = str(fila.get("ruta_id") or "")
                id_cliente = _normalizar_id(fila.get("id_cliente") or fila.get("id_sucursal"))
                orden = int(fila.get("secuencia_visita") or fila.get("orden") or 1)
                if not ruta_id or id_cliente is None:
                    continue
                orden_por_ruta.setdefault(ruta_id, {})[id_cliente] = min(
                    orden,
                    orden_por_ruta[ruta_id].get(id_cliente, orden),
                )
        return orden_por_ruta
    except Exception:
        return {}


def _seleccionar_ruta(mayorista: dict, rutas_sucursales: dict) -> "str | None":
    lat_m = mayorista.get("latitud")
    lon_m = mayorista.get("longitud")
    if lat_m is None or lon_m is None:
        return None

    best_rid = None
    best_dist = float("inf")
    for rid, sucursales in rutas_sucursales.items():
        centroid_lat, centroid_lon = _ruta_centroid(sucursales)
        if centroid_lat is None or centroid_lon is None:
            continue
        dist = _haversine_km(lat_m, lon_m, centroid_lat, centroid_lon)
        if dist < best_dist:
            best_dist = dist
            best_rid = rid

    return best_rid


def _ordenar_mayoristas_en_ruta(mayoristas: list, ruta_id: str, historico_orden: dict, sucursales: list) -> list:
    if not mayoristas:
        return []

    centro_lat, centro_lon = _ruta_centroid(sucursales)
    mayoristas_con = [m for m in mayoristas if m.get("latitud") is not None and m.get("longitud") is not None]
    mayoristas_sin = [m for m in mayoristas if m not in mayoristas_con]

    if not mayoristas_con:
        return mayoristas_sin

    if ruta_id in historico_orden:
        ranking = historico_orden[ruta_id]
        ordenadas = sorted(
            mayoristas_con,
            key=lambda m: (
                ranking.get(m["id_cliente"], 999999),
                m["id_cliente"],
            ),
        )
    else:
        if centro_lat is None or centro_lon is None:
            ordenadas = sorted(
                mayoristas_con,
                key=lambda m: (
                    -(m.get("latitud") or -999999),
                    m["id_cliente"],
                ),
            )
        else:
            ordenadas = sorted(
                mayoristas_con,
                key=lambda m: (
                    _haversine_km(m["latitud"], m["longitud"], centro_lat, centro_lon),
                    m["id_cliente"],
                ),
            )

    return ordenadas + sorted(mayoristas_sin, key=lambda m: m["id_cliente"])


def _otras_rutas_historicas(id_cliente: int, ruta_actual: str, historico_orden: dict) -> list:
    """Rutas (distintas a la actual) donde este mayorista ha aparecido históricamente."""
    return [
        rid for rid, ranking in historico_orden.items()
        if rid != ruta_actual and id_cliente in ranking
    ]


def _resolver_sobrecarga_mayoristas(
    mayoristas_por_ruta: dict,
    peso_sucursales: dict,
    capacidad_kg: dict,
    historico_orden: dict,
    centros_ruta: "dict | None" = None,
    max_iter: int = 200,
) -> dict:
    """
    Si una ruta excede su capacidad efectiva al integrar mayoristas, mueve el
    mayorista excedente a otra ruta. La sobrecarga NUNCA debe quedar sin
    resolver mientras exista capacidad disponible en otra ruta:
      1. Se prioriza una ruta donde el mayorista también haya pertenecido
         históricamente y tenga capacidad disponible.
      2. Si ninguna ruta con historial tiene cupo, se usa la más cercana
         geográficamente de entre TODAS las rutas con capacidad disponible.
    Solo se deja sobrecargada si NINGUNA ruta tiene capacidad disponible.
    """
    centros_ruta = centros_ruta or {}

    def _peso_total(rid: str) -> float:
        return peso_sucursales.get(rid, 0.0) + sum(
            float(m.get("peso_kg", 0) or 0) for m in mayoristas_por_ruta.get(rid, [])
        )

    def _mejor_destino(m: dict, rid_origen: str) -> "str | None":
        peso_m = float(m.get("peso_kg", 0) or 0)
        id_cl  = m.get("id_cliente")

        if id_cl is not None:
            for alt_rid in _otras_rutas_historicas(id_cl, rid_origen, historico_orden):
                alt_cap = capacidad_kg.get(alt_rid)
                if not alt_cap or alt_cap <= 0:
                    continue
                if _peso_total(alt_rid) + peso_m <= alt_cap:
                    return alt_rid

        lat_m, lon_m = m.get("latitud"), m.get("longitud")
        mejor_rid, mejor_dist = None, float("inf")
        for alt_rid, alt_cap in capacidad_kg.items():
            if alt_rid == rid_origen or not alt_cap or alt_cap <= 0:
                continue
            if _peso_total(alt_rid) + peso_m > alt_cap:
                continue
            centro = centros_ruta.get(alt_rid)
            if lat_m is not None and lon_m is not None and centro and centro[0] is not None:
                dist = _haversine_km(lat_m, lon_m, centro[0], centro[1])
            else:
                dist = 0.0
            if dist < mejor_dist:
                mejor_dist = dist
                mejor_rid = alt_rid
        return mejor_rid

    for _ in range(max_iter):
        cambio = False
        for rid in list(mayoristas_por_ruta.keys()):
            mays = mayoristas_por_ruta.get(rid, [])
            cap = capacidad_kg.get(rid)
            if not mays or not cap or cap <= 0:
                continue
            if _peso_total(rid) <= cap:
                continue

            for m in sorted(mays, key=lambda x: float(x.get("peso_kg", 0) or 0), reverse=True):
                destino = _mejor_destino(m, rid)
                if destino:
                    mayoristas_por_ruta[rid].remove(m)
                    mayoristas_por_ruta.setdefault(destino, []).append(m)
                    cambio = True
                    break

        if not cambio:
            break

    return mayoristas_por_ruta


def _guardar_historico_mayoristas(logistica_id: str, nombre: str, filas_list: list) -> dict:
    """
    Persiste una fotografía de mayoristas confirmados en `rutas_historicas`
    (tipo_registro='mayoristas'). Equivalente a
    logic/historico_logic.guardar_en_historico(..., tipo_registro="mayoristas")
    -- reimplementado aquí en SQL porque historico_logic.py sigue en Mongo
    (Fase 7) y este módulo ya está en SQL Server.

    Comportamiento preservado del original: el filtro de upsert de Mongo
    excluía explícitamente tipo_registro="mayoristas" ({"$ne": "mayoristas"}),
    así que una llamada con ese tipo NUNCA actualizaba un documento existente
    -- siempre insertaba uno nuevo. _cargar_historico_mayoristas() ya asume
    varios snapshots acumulados (por eso ordena y combina varios documentos).
    Aquí se preserva: siempre se hace INSERT, nunca UPDATE.
    """
    try:
        filas = []
        for r in filas_list:
            filas.append({
                "id_sucursal":      r.get("id_sucursal") or r.get("num_tienda", 0),
                "id_cliente":       r.get("id_cliente"),
                "ruta_id":          r.get("ruta_id"),
                "tipo":             r.get("tipo") or "mayorista",
                "vehiculo":         r.get("vehiculo", ""),
                "dia_semana":       str(r.get("dia_semana", "")).upper(),
                "secuencia_visita": int(r.get("secuencia_visita") or r.get("orden") or 1),
                "kg_entrega":       float(r.get("kg_entrega") or r.get("peso_kg") or 0),
            })

        if not filas:
            return {"status": "error", "mensaje": "No hay rutas para guardar"}

        n_may = len(set(str(f.get("id_cliente") or "") for f in filas if f.get("id_cliente")))
        n_rut = len(set((f["vehiculo"], f["dia_semana"]) for f in filas))
        dias  = sorted({f["dia_semana"] for f in filas if f["dia_semana"]})

        db = get_db()
        oid_log = _id_valido(str(logistica_id))
        fecha_inicio = ""
        fecha_fin    = ""
        if oid_log:
            tabla_log = get_table("logisticas")
            log_fila = db.execute(
                select(tabla_log.c.fecha_inicio, tabla_log.c.fecha_fin).where(tabla_log.c.mongo_id == oid_log)
            ).mappings().first()
            if log_fila:
                fecha_inicio = log_fila["fecha_inicio"] or ""
                fecha_fin    = log_fila["fecha_fin"] or ""

        cargado_en = f"{fecha_inicio}T00:00:00" if fecha_inicio else datetime.now().isoformat()

        nuevo_id = str(ObjectId())
        tabla = get_table("rutas_historicas")
        db.execute(insert(tabla).values(
            mongo_id=nuevo_id,
            nombre=nombre,
            filas=json.dumps(filas, ensure_ascii=False),
            n_sucursales=0,
            n_mayoristas=n_may,
            n_rutas=n_rut,
            dias=json.dumps(dias, ensure_ascii=False),
            logistica_id=str(logistica_id),
            fecha_inicio=fecha_inicio,
            fecha_fin=fecha_fin,
            cargado_en=cargado_en,
            confirmada=True,
            tipo_registro="mayoristas",
        ))
        return {"status": "ok", "id": nuevo_id}
    except Exception as e:
        return {"status": "error", "mensaje": str(e)}


def _persistir_historico_mayoristas(logistica_id: str, rutas_info: dict, mayoristas_por_ruta: dict) -> None:
    """
    Guarda una fotografía de la distribución para que el flujo de mayoristas
    tenga memoria histórica igual que las sucursales.
    """
    try:
        db  = get_db()
        oid = _id_valido(logistica_id)
        if not oid:
            return

        # asignaciones.asignaciones (campo distinto de detalle_por_dia) nunca
        # tuvo datos reales en ningún documento migrado a SQL Server -- no
        # existe columna para él. Se preserva el mismo resultado que en
        # Mongo, donde este lookup tampoco encontraba nada en producción.
        asignaciones: dict = {}

        tabla_veh = get_table("vehiculos")
        vehiculos = {
            (v.placas or "").strip(): {"placas": v.placas, "abreviatura": v.abreviatura}
            for v in db.execute(select(tabla_veh.c.placas, tabla_veh.c.abreviatura))
            if (v.placas or "").strip()
        }

        filas = []
        for ruta_id, mayoristas in mayoristas_por_ruta.items():
            asig = asignaciones.get(ruta_id) or {}
            placas = (asig.get("placas") or "").strip()
            vehiculo = ""
            if placas and placas in vehiculos:
                vehiculo = (vehiculos[placas].get("abreviatura") or placas).strip()
            elif placas:
                vehiculo = placas

            dia_semana = str(asig.get("dia") or asig.get("dia_semana") or "").upper()
            if not vehiculo and not dia_semana:
                continue
            for orden, m in enumerate(mayoristas, start=1):
                filas.append({
                    "ruta_id":          ruta_id,
                    "id_cliente":       m.get("id_cliente"),
                    "documento":        m.get("documento", ""),
                    "vehiculo":         vehiculo,
                    "dia_semana":       dia_semana,
                    "secuencia_visita": orden,
                    "kg_entrega":       float(m.get("peso_kg") or 0),
                    "tipo":             "mayorista",
                })

        if filas:
            _guardar_historico_mayoristas(logistica_id, "Mayoristas confirmados", filas)
    except Exception:
        pass


def _integrar_paradas(sucursales: list, mayoristas: list,
                      depot_lat: float = None, depot_lon: float = None) -> list:
    """
    Combina sucursales y mayoristas en una secuencia unificada ordenada por proximidad.

    Cada mayorista se inserta en la posición más cercana a su vecino geográfico
    dentro de la lista ya construida. Mayoristas sin coordenadas van al final.
    """
    sucs_nodos = [
        {
            "tipo": "sucursal",
            "num_tienda": s.get("num_tienda"),
            "nombre_base": s.get("nombre_base") or s.get("nombre") or "",
            "latitud": _to_float(s.get("latitud")),
            "longitud": _to_float(s.get("longitud")),
        }
        for s in _ordenar_sucursales_planificacion(sucursales or [])
    ]

    if not mayoristas:
        for idx, p in enumerate(sucs_nodos, start=1):
            p["orden"] = idx
        return sucs_nodos

    mayoristas_con = [m for m in mayoristas if _to_float(m.get("latitud")) is not None and _to_float(m.get("longitud")) is not None]
    mayoristas_sin = [m for m in mayoristas if m not in mayoristas_con]

    centro_lat, centro_lon = _ruta_centroid(sucursales or [])
    if centro_lat is None or centro_lon is None:
        mayoristas_ordenados = sorted(mayoristas_con, key=lambda m: m.get("id_cliente") or 0)
    else:
        mayoristas_ordenados = sorted(
            mayoristas_con,
            key=lambda m: (
                _haversine_km(m["latitud"], m["longitud"], centro_lat, centro_lon),
                m.get("id_cliente") or 0,
            ),
        )

    paradas = list(sucs_nodos)
    for m in mayoristas_ordenados:
        nodo = {
            "tipo":       "mayorista",
            "id_cliente": m.get("id_cliente"),
            "documento":  m.get("documento", ""),
            "nombre_base": m.get("nombre") or "",
            "latitud":    _to_float(m.get("latitud")),
            "longitud":   _to_float(m.get("longitud")),
            "peso_kg":    float(m.get("peso_kg") or 0),
            "desvio_m":   m.get("desvio_m"),
        }
        pos = _insertar_pos_proxima(paradas, nodo)
        paradas.insert(pos, nodo)

    for m in mayoristas_sin:
        paradas.append({
            "tipo":       "mayorista",
            "id_cliente": m.get("id_cliente"),
            "documento":  m.get("documento", ""),
            "nombre_base": m.get("nombre") or "",
            "latitud":    None,
            "longitud":   None,
            "peso_kg":    float(m.get("peso_kg") or 0),
            "desvio_m":   m.get("desvio_m"),
        })

    for idx, p in enumerate(paradas, start=1):
        p["orden"] = idx
    return paradas


def calcular_distribucion_mayoristas(logistica_id: str, rutas: "list | None" = None) -> dict:
    """
    Calcula la distribución de mayoristas sin cheapest insertion.

    Flujo:
      1. Lee mayoristas activos desde la extracción.
      2. Asigna cada mayorista a la ruta cuyo conjunto de sucursales esté
         más cerca geográficamente.
      3. Ordena los mayoristas dentro de cada ruta usando su propio histórico
         si existe; si no, usa proximidad al centroide de la ruta.
      4. Construye la misma estructura de respuesta que consumen asignación,
         modificación y mapa.
      5. Persiste una fotografía del resultado en el histórico de mayoristas.
    """
    vacio = {
        "mayoristas_por_ruta": {},
        "paradas_integradas": {},
        "orden_sucursales": {},
        "todos_mayoristas": [],
        "sin_asignar": [],
        "sin_coords": [],
    }

    oid = _id_valido(logistica_id)
    if not oid:
        return dict(vacio)

    db = get_db()
    pedidos_may, nombres_may = _leer_pesos_mayoristas(db, oid)
    ids = set(p["id_cliente"] for p in pedidos_may)
    if not ids:
        return dict(vacio)

    coords_may = _leer_coords_mayoristas(db, ids)
    # Cada documento es una entrada separada en la lista de mayoristas
    mayoristas: list = []
    sin_coords: list = []
    pendientes: list = []   # peso 0: disponibles para agregar a mano, nunca auto-asignados
    for pedido in pedidos_may:
        id_int = pedido["id_cliente"]
        cat = coords_may.get(id_int) or {}
        lat = cat.get("latitud")
        lon = cat.get("longitud")
        nombre = cat.get("nombre") or nombres_may.get(id_int) or ""
        entry = {
            "id_cliente": id_int,
            "documento":  pedido["documento"],
            "nombre":     nombre,
            "peso_kg":    float(pedido["peso"]),
            "latitud":    lat,
            "longitud":   lon,
        }
        # Pendientes (peso 0): quedan disponibles para agregado manual, pero
        # NO entran al reparto automático por ruta.
        if pedido["peso"] <= 0:
            entry["pendiente"] = True
            pendientes.append(entry)
        elif lat is None or lon is None:
            sin_coords.append(entry)
        else:
            mayoristas.append(entry)

    if rutas is None:
        tabla_rc  = get_table("rutas_config")
        tabla_rcs = get_table("rutas_config_sucursales")
        sucs_por_ruta: dict = {}
        for s in db.execute(select(tabla_rcs)):
            sucs_por_ruta.setdefault(s.ruta_config_id, []).append({
                "num_tienda":    s.num_tienda,
                "nombre_base":   s.nombre_base,
                "nombre_tienda": s.nombre_tienda,
                "nombre_pedido": s.nombre_pedido,
                "nombre":        s.nombre,
                "latitud":       s.latitud,
                "longitud":      s.longitud,
                "orden":         s.orden,
                "peso_kg":       s.peso_kg,
            })
        rutas = [
            {"_id": r.mongo_id, "sucursales": sucs_por_ruta.get(r.mongo_id, [])}
            for r in db.execute(select(tabla_rc.c.mongo_id))
        ]

    rutas_index: dict = {}
    rutas_sucursales: dict = {}
    rutas_cap_kg: dict = {}
    for r in rutas or []:
        rid = str(r.get("_id") or r.get("id") or "")
        if not rid:
            continue
        rutas_index[rid] = r
        rutas_sucursales[rid] = _ordenar_sucursales_planificacion(r.get("sucursales", []))
        cap_ton = _to_float(r.get("cap_ton"))
        if cap_ton and cap_ton > 0:
            rutas_cap_kg[rid] = capacidad_efectiva_kg(cap_ton * 1000)

    historico_orden = _cargar_historico_mayoristas(str(logistica_id))

    mayoristas_por_ruta: dict = {rid: [] for rid in rutas_index}
    sin_asignar: list = []
    for m in mayoristas:
        ruta_id = _seleccionar_ruta(m, rutas_sucursales)
        if not ruta_id:
            sin_asignar.append(dict(m))
            continue
        mayoristas_por_ruta.setdefault(ruta_id, []).append(dict(m))

    # Si se conoce la capacidad del vehículo de cada ruta, reasignar los
    # mayoristas que provocan sobrecarga: primero hacia una ruta donde
    # históricamente también hayan pertenecido, y si ninguna tiene cupo,
    # hacia la más cercana con capacidad disponible (nunca queda sobrecargada
    # mientras exista cupo en otra ruta).
    if rutas_cap_kg:
        peso_sucursales = {
            rid: sum(float(s.get("peso_kg", 0) or 0) for s in sucs)
            for rid, sucs in rutas_sucursales.items()
        }
        centros_ruta = {
            rid: _ruta_centroid(sucs) for rid, sucs in rutas_sucursales.items()
        }
        mayoristas_por_ruta = _resolver_sobrecarga_mayoristas(
            mayoristas_por_ruta, peso_sucursales, rutas_cap_kg, historico_orden, centros_ruta,
        )

    # Ordenar cada ruta sin cheapest insertion: histórico → proximidad
    paradas_integradas: dict = {}
    orden_sucursales: dict = {}

    for rid, sucursales_raw in rutas_sucursales.items():
        sucursales = [dict(s, tipo="sucursal") for s in sucursales_raw]
        for idx, suc in enumerate(sucursales, start=1):
            suc["orden"] = idx

        mays = _ordenar_mayoristas_en_ruta(
            mayoristas_por_ruta.get(rid, []),
            rid,
            historico_orden,
            sucursales_raw,
        )

        # Insertar cada mayorista junto a su parada más cercana en la ruta
        paradas = list(sucursales)
        centro_raw = _ruta_centroid(sucursales_raw)
        for m in mays:
            lat_m = _to_float(m.get("latitud"))
            lon_m = _to_float(m.get("longitud"))
            p = {
                "tipo":       "mayorista",
                "id_cliente": m.get("id_cliente"),
                "documento":  m.get("documento", ""),
                "nombre_base": m.get("nombre") or "",
                "latitud":    lat_m,
                "longitud":   lon_m,
                "peso_kg":    float(m.get("peso_kg") or 0),
                "desvio_m": round(
                    (_haversine_km(
                        centro_raw[0] or lat_m or 0,
                        centro_raw[1] or lon_m or 0,
                        lat_m or 0,
                        lon_m or 0,
                    ) if lat_m is not None and lon_m is not None else 0.0) * 1000,
                    1,
                ),
            }
            pos = _insertar_pos_proxima(paradas, p)
            paradas.insert(pos, p)

        # Asignar orden secuencial a la lista entrelazada
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

        mayoristas_por_ruta[rid] = [
            {
                **dict(m),
                "ruta_id": rid,
                "orden": next(
                    (p["orden"] for p in paradas
                     if p.get("tipo") == "mayorista"
                     and p.get("id_cliente") == m.get("id_cliente")
                     and p.get("documento") == m.get("documento")),
                    9999,
                ),
            }
            for m in mays
        ]

    _persistir_historico_mayoristas(logistica_id, rutas_index, mayoristas_por_ruta)

    return {
        "mayoristas_por_ruta": mayoristas_por_ruta,
        "paradas_integradas": paradas_integradas,
        "orden_sucursales": orden_sucursales,
        # Los pendientes se ofrecen como disponibles, pero no se auto-asignan
        "todos_mayoristas": mayoristas + pendientes,
        "sin_asignar": sin_asignar,
        "sin_coords": sin_coords,
        "pendientes": pendientes,
        "actualizado_en": datetime.now().isoformat(),
    }
