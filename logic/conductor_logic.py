"""
logic/conductor_logic.py
Lógica de negocio para el portal del Conductor (Fase 1) y para la
autorización de rutas por parte de Logística.

Fuente de datos: `modificaciones_rutas` (+ tablas normalizadas, vía
`modificacion_logic.obtener_modificacion_previa()`) en SQL Server, más
`entregas`/`entregas_historial` (una fila por parada y una fila por
evento de historial, respectivamente — en Mongo el historial era un
array embebido dentro del documento de la entrega).

Reglas clave:
  - Un chofer SOLO puede ver rutas de logísticas explícitamente AUTORIZADAS
    por Logística (`modificaciones_rutas.autorizado == True`) — generar el
    PDF ya NO autoriza por sí solo; es una acción separada e intencional.
  - La pertenencia de una ruta a un chofer se valida por `chofer_id` (real)
    y, como respaldo para datos antiguos sin chofer_id, por coincidencia
    de nombre (case-insensitive).
  - Toda función de lectura/escritura de una ruta puntual revalida la
    pertenencia del chofer antes de devolver o modificar nada.
"""
from datetime import datetime
from bson import ObjectId
from bson.errors import InvalidId
from sqlalchemy import select, update, insert, delete, func

from db import get_db, get_table, transaccion
from logic.auditoria_logic import registrar_auditoria
from logic.modificacion_logic import (
    obtener_modificacion_previa,
    consultar_osrm_con_reintentos,
    MATRIZ_LAT_DEFAULT,
    MATRIZ_LON_DEFAULT,
    ORDEN_DIAS,
)

ESTADO_ENTREGADO = "entregado"
ESTADO_CANCELADO = "cancelada"
ESTADOS_VALIDOS  = {ESTADO_ENTREGADO, ESTADO_CANCELADO}


def _id_valido(doc_id: str) -> "str | None":
    try:
        return str(ObjectId(doc_id))
    except (InvalidId, TypeError):
        return None


def _parada_key(parada: dict) -> str:
    """Clave estable de una parada (igual criterio que sucursal/mayorista en Modificación)."""
    if parada.get("tipo") == "mayorista":
        return str(parada.get("documento") or parada.get("id_cliente") or "")
    return str(parada.get("num_tienda", ""))


def _ruta_pertenece_a_chofer(ruta: dict, chofer_id: str, nombre_chofer: str) -> bool:
    rid_ruta = ruta.get("chofer_id")
    if rid_ruta:
        return str(rid_ruta) == str(chofer_id)
    # Dato legado sin chofer_id: respaldo por nombre.
    nombre_ruta = (ruta.get("chofer") or "").strip().lower()
    return bool(nombre_ruta) and nombre_ruta == (nombre_chofer or "").strip().lower()


def _paradas_combinadas(ruta: dict) -> list:
    """Sucursales + mayoristas de una ruta, en una sola lista ordenada por 'orden'."""
    paradas = []
    for s in ruta.get("sucursales", []) or []:
        paradas.append({**s, "tipo": "sucursal"})
    for m in ruta.get("mayoristas", []) or []:
        paradas.append({**m, "tipo": "mayorista"})
    paradas.sort(key=lambda p: p.get("orden") if p.get("orden") is not None else 9999)
    return paradas


def _logisticas_ids_con_modificacion(solo_autorizadas: bool = False) -> list:
    """IDs de logística con fila en modificaciones_rutas (opcionalmente solo las autorizadas)."""
    db = get_db()
    t = get_table("modificaciones_rutas")
    stmt = select(t.c.logistica_id)
    if solo_autorizadas:
        stmt = stmt.where(t.c.autorizado == True)  # noqa: E712
    return [str(r.logistica_id) for r in db.execute(stmt)]


def _entregas_por_ruta(db, ruta_id: str) -> dict:
    """Mapa parada_key -> fila de entrega (dict) para una ruta (todos los estados)."""
    t = get_table("entregas")
    filas = db.execute(select(t).where(t.c.ruta_id == ruta_id)).mappings()
    return {f["parada_key"]: dict(f) for f in filas}


def _nombre_parada_en_ruta(ruta: dict, parada_tipo: str, parada_key: str) -> str:
    """Devuelve el nombre legible de una parada buscándola en los datos de la ruta."""
    lista = ruta.get("sucursales", []) if parada_tipo == "sucursal" else ruta.get("mayoristas", [])
    for p in lista or []:
        key = str(p.get("num_tienda", "")) if parada_tipo == "sucursal" \
              else str(p.get("documento") or p.get("id_cliente") or "")
        if key == str(parada_key):
            return p.get("nombre") or parada_key
    return str(parada_key)


def _historial_de_entrega(db, entrega_id: str) -> list:
    """Eventos de `entregas_historial` de una entrega, en orden cronológico."""
    if not entrega_id:
        return []
    t = get_table("entregas_historial")
    filas = db.execute(
        select(t).where(t.c.entrega_id == entrega_id).order_by(t.c.fecha)
    ).mappings()
    return [dict(f) for f in filas]


def _fmt_historial(historial: list) -> list:
    """Serializa el array de historial para envío al frontend."""
    resultado = []
    for ev in historial or []:
        resultado.append({
            "estado":          ev.get("estado"),
            "fecha":           ev.get("fecha"),
            "usuario_nombre":  ev.get("usuario_nombre", ""),
            "observacion":     ev.get("observacion", ""),
        })
    return resultado


def _buscar_ruta_autorizada(chofer_id: str, nombre_chofer: str, ruta_id: str):
    """
    Busca `ruta_id` entre todas las logísticas AUTORIZADAS y valida que
    pertenezca al chofer. Devuelve (ruta_dict, logistica_id, status_dict|None).
    """
    for lid in _logisticas_ids_con_modificacion(solo_autorizadas=True):
        doc = obtener_modificacion_previa(lid)
        if not doc:
            continue
        for ruta in doc.get("rutas_confirmadas", []) or []:
            if ruta.get("id") == ruta_id:
                if not _ruta_pertenece_a_chofer(ruta, chofer_id, nombre_chofer):
                    return None, None, {"status": "error", "mensaje": "No autorizado para ver esta ruta"}
                return ruta, lid, None
    return None, None, {"status": "error", "mensaje": "Ruta no encontrada o no autorizada"}


def obtener_rutas_chofer(chofer_id: str, nombre_chofer: str) -> dict:
    """
    Devuelve las rutas asignadas a un chofer, agrupadas por logística y día,
    SOLO de logísticas explícitamente autorizadas por Logística.
    Marca `logistica_completada` cuando todas las rutas del bucket están procesadas.
    """
    db = get_db()
    t_log = get_table("logisticas")
    t_ent = get_table("entregas")
    logisticas_cache: dict = {}

    def _logistica(lid):
        key = str(lid)
        if key not in logisticas_cache:
            row = db.execute(select(t_log).where(t_log.c.mongo_id == lid)).mappings().first() if lid else None
            logisticas_cache[key] = {
                "id":           key,
                "slug":         row["slug"] if row else "",
                "nombre":       row["nombre"] if row else "Logística",
                "fecha_inicio": row["fecha_inicio"] if row else "",
                "fecha_fin":    row["fecha_fin"] if row else "",
            }
        return logisticas_cache[key]

    resultado: dict = {}
    for lid in _logisticas_ids_con_modificacion(solo_autorizadas=True):
        doc = obtener_modificacion_previa(lid)
        if not doc:
            continue
        for ruta in doc.get("rutas_confirmadas", []) or []:
            if not _ruta_pertenece_a_chofer(ruta, chofer_id, nombre_chofer):
                continue

            ruta_id   = ruta.get("id")
            total     = len(ruta.get("sucursales", []) or []) + len(ruta.get("mayoristas", []) or [])
            entregadas = db.execute(select(func.count()).select_from(t_ent).where(
                t_ent.c.ruta_id == ruta_id, t_ent.c.estado == ESTADO_ENTREGADO
            )).scalar()
            canceladas = db.execute(select(func.count()).select_from(t_ent).where(
                t_ent.c.ruta_id == ruta_id, t_ent.c.estado == ESTADO_CANCELADO
            )).scalar()
            procesadas = entregadas + canceladas
            pct        = round((entregadas / total) * 100, 1) if total else 0.0
            # Una ruta se considera "completada" cuando el chofer procesó todas las paradas
            # (ya sea entregándolas o cancelándolas).
            completada = total > 0 and procesadas >= total

            log_info = _logistica(lid)
            bucket = resultado.setdefault(log_info["id"], {"logistica": log_info, "dias": {}})
            dia = ruta.get("dia", "")
            bucket["dias"].setdefault(dia, []).append({
                "id":               ruta_id,
                "nombre":           ruta.get("nombre"),
                "dia":              dia,
                "vehiculo_abrev":   ruta.get("vehiculo_abrev"),
                "vehiculo_placas":  ruta.get("vehiculo_placas"),
                "hora_salida":      ruta.get("hora_salida"),
                "hora_regreso":     ruta.get("hora_regreso_estimada") or ruta.get("hora_regreso"),
                "total_paradas":    total,
                "entregadas":       entregadas,
                "canceladas":       canceladas,
                "pct_avance":       pct,
                "completada":       completada,
            })

    # Ordenar días de cada logística según ORDEN_DIAS
    for bucket in resultado.values():
        bucket["dias"] = dict(sorted(
            bucket["dias"].items(),
            key=lambda kv: ORDEN_DIAS.index(kv[0]) if kv[0] in ORDEN_DIAS else 99,
        ))
        # Marca el bucket como completado cuando TODAS las rutas de todos los días
        # han sido procesadas por el chofer.
        todas_completadas = all(
            r["completada"]
            for rutas in bucket["dias"].values()
            for r in rutas
        )
        bucket["logistica_completada"] = todas_completadas

    return {"status": "ok", "logisticas": list(resultado.values())}


def obtener_detalle_ruta_chofer(chofer_id: str, nombre_chofer: str, ruta_id: str) -> dict:
    ruta, logistica_id, error = _buscar_ruta_autorizada(chofer_id, nombre_chofer, ruta_id)
    if error:
        return error

    db = get_db()
    paradas = _paradas_combinadas(ruta)
    entregas_map = _entregas_por_ruta(db, ruta_id)

    paradas_ui = []
    coords_geometria = [(MATRIZ_LAT_DEFAULT, MATRIZ_LON_DEFAULT)]
    for p in paradas:
        key     = _parada_key(p)
        entrega = entregas_map.get(key)
        lat, lon = p.get("latitud"), p.get("longitud")
        if lat is not None and lon is not None:
            coords_geometria.append((float(lat), float(lon)))

        estado_parada = entrega.get("estado", "pendiente") if entrega else "pendiente"
        historial = _historial_de_entrega(db, entrega.get("mongo_id")) if entrega else []
        paradas_ui.append({
            "tipo":         p["tipo"],
            "key":          key,
            "nombre":       p.get("nombre") or ("Mayorista " + key if p["tipo"] == "mayorista" else f"Sucursal {key}"),
            "num_tienda":   p.get("num_tienda"),
            "documento":    p.get("documento"),
            "peso_kg":      p.get("peso_kg", 0),
            "orden":        p.get("orden"),
            "latitud":      lat,
            "longitud":     lon,
            "estado":       estado_parada,
            "entregado":    estado_parada == ESTADO_ENTREGADO,
            "cancelado":    estado_parada == ESTADO_CANCELADO,
            "entregado_en": entrega.get("entregado_en").isoformat() if entrega and entrega.get("entregado_en") else None,
            "historial":    _fmt_historial(historial),
        })

    geometry = []
    try:
        if len(coords_geometria) >= 2:
            geometry = consultar_osrm_con_reintentos(coords_geometria).get("geometry", [])
    except Exception as e:
        print(f"[obtener_detalle_ruta_chofer] OSRM error: {e}")

    return {
        "status":          "ok",
        "id":               ruta.get("id"),
        "nombre":           ruta.get("nombre"),
        "dia":              ruta.get("dia"),
        "vehiculo_abrev":   ruta.get("vehiculo_abrev"),
        "hora_salida":      ruta.get("hora_salida"),
        "hora_regreso":     ruta.get("hora_regreso_estimada") or ruta.get("hora_regreso"),
        "paradas":          paradas_ui,
        "geometry":         geometry,
        "matriz":           [MATRIZ_LAT_DEFAULT, MATRIZ_LON_DEFAULT],
    }


def marcar_entrega(
    usuario_id: str,
    chofer_id: str,
    nombre_chofer: str,
    ruta_id: str,
    parada_tipo: str,
    parada_key: str,
    estado: str = ESTADO_ENTREGADO,
    observacion: str = "",
) -> dict:
    ruta, logistica_id, error = _buscar_ruta_autorizada(chofer_id, nombre_chofer, ruta_id)
    if error:
        return error

    if parada_tipo not in ("sucursal", "mayorista"):
        return {"status": "error", "mensaje": "Tipo de parada inválido"}

    if estado not in ESTADOS_VALIDOS:
        return {"status": "error", "mensaje": "Estado inválido"}

    parada_key = str(parada_key)
    ahora  = datetime.now()
    evento = {
        "estado":         estado,
        "fecha":          ahora.isoformat(),
        "usuario_id":     str(usuario_id or ""),
        "usuario_nombre": str(nombre_chofer or ""),
        "observacion":    (observacion or "").strip(),
    }

    t_ent  = get_table("entregas")
    t_hist = get_table("entregas_historial")

    try:
        es_primera_accion = False
        es_reentrega       = False
        entrega_id         = None
        error_cancelar     = None

        with transaccion() as conn:
            # Verificar antes de modificar si el chofer aún no ha registrado ninguna
            # acción en esta ruta (primera entrega = inicio de ruta).
            es_primera_accion = conn.execute(
                select(func.count()).select_from(t_ent).where(t_ent.c.ruta_id == ruta_id)
            ).scalar() == 0

            existente = conn.execute(
                select(t_ent).where(
                    t_ent.c.ruta_id == ruta_id,
                    t_ent.c.parada_tipo == parada_tipo,
                    t_ent.c.parada_key == parada_key,
                )
            ).mappings().first()

            if estado == ESTADO_ENTREGADO:
                # Detectar re-entrega (había sido cancelada) para auditoría.
                es_reentrega = existente is not None and existente["estado"] == ESTADO_CANCELADO

                if existente:
                    entrega_id = existente["mongo_id"]
                    conn.execute(update(t_ent).where(t_ent.c.mongo_id == entrega_id).values(
                        logistica_id=logistica_id,
                        dia=ruta.get("dia"),
                        estado=ESTADO_ENTREGADO,
                        entregado_en=ahora,
                        registrado_por=str(usuario_id or ""),
                        observacion=(observacion or "").strip(),
                    ))
                else:
                    entrega_id = str(ObjectId())
                    conn.execute(insert(t_ent).values(
                        mongo_id=entrega_id, ruta_id=ruta_id, parada_tipo=parada_tipo, parada_key=parada_key,
                        logistica_id=logistica_id, dia=ruta.get("dia"), estado=ESTADO_ENTREGADO,
                        entregado_en=ahora, registrado_por=str(usuario_id or ""),
                        observacion=(observacion or "").strip(),
                    ))

                conn.execute(insert(t_hist).values(
                    entrega_id=entrega_id, logistica_id=logistica_id, parada_key=parada_key,
                    dia=ruta.get("dia"), estado=evento["estado"], fecha=evento["fecha"],
                    usuario_id=evento["usuario_id"], usuario_nombre=evento["usuario_nombre"],
                    observacion=evento["observacion"],
                ))

            else:  # ESTADO_CANCELADO
                if existente is None or existente["estado"] != ESTADO_ENTREGADO:
                    error_cancelar = "No se encontró una entrega activa para cancelar"
                else:
                    entrega_id = existente["mongo_id"]
                    conn.execute(update(t_ent).where(t_ent.c.mongo_id == entrega_id).values(
                        estado=ESTADO_CANCELADO,
                        cancelado_en=ahora,
                        cancelado_por=str(usuario_id or ""),
                    ))
                    conn.execute(insert(t_hist).values(
                        entrega_id=entrega_id, logistica_id=logistica_id, parada_key=parada_key,
                        dia=ruta.get("dia"), estado=evento["estado"], fecha=evento["fecha"],
                        usuario_id=evento["usuario_id"], usuario_nombre=evento["usuario_nombre"],
                        observacion=evento["observacion"],
                    ))
    except Exception as e:
        return {"status": "error", "mensaje": str(e)}

    if estado == ESTADO_CANCELADO and error_cancelar:
        return {"status": "error", "mensaje": error_cancelar}

    tipo_label    = "Sucursal" if parada_tipo == "sucursal" else "Mayorista"
    nombre_parada = _nombre_parada_en_ruta(ruta, parada_tipo, parada_key)

    if estado == ESTADO_ENTREGADO:
        if es_primera_accion:
            registrar_auditoria(
                usuario_id, nombre_chofer, "iniciar_ruta", logistica_id,
                f"Ruta iniciada: {ruta.get('nombre', '')}",
            )
        if es_reentrega:
            registrar_auditoria(
                usuario_id, nombre_chofer, "reautorizar_entrega", logistica_id,
                f"{tipo_label} re-entregada: {nombre_parada} · Ruta: {ruta.get('nombre', '')}",
            )
        else:
            registrar_auditoria(
                usuario_id, nombre_chofer, "entrega_realizada", logistica_id,
                f"{tipo_label} entregada: {nombre_parada} · Ruta: {ruta.get('nombre', '')}",
            )
    else:
        registrar_auditoria(
            usuario_id, nombre_chofer, "cancelar_entrega", logistica_id,
            f"{tipo_label} cancelada: {nombre_parada} · Ruta: {ruta.get('nombre', '')}",
        )

    db = get_db()
    total      = len(ruta.get("sucursales", []) or []) + len(ruta.get("mayoristas", []) or [])
    entregadas = db.execute(select(func.count()).select_from(t_ent).where(
        t_ent.c.ruta_id == ruta_id, t_ent.c.estado == ESTADO_ENTREGADO
    )).scalar()
    canceladas = db.execute(select(func.count()).select_from(t_ent).where(
        t_ent.c.ruta_id == ruta_id, t_ent.c.estado == ESTADO_CANCELADO
    )).scalar()
    return {
        "status":     "ok",
        "entregadas": entregadas,
        "canceladas": canceladas,
        "total":      total,
        "pct_avance": round((entregadas / total) * 100, 1) if total else 0.0,
    }


def obtener_seguimiento_logistica(logistica_id: str) -> dict:
    """Estado de avance de todas las rutas de una logística, para la pestaña 'Seguimiento'."""
    oid = _id_valido(logistica_id)
    if not oid:
        return {"status": "error", "mensaje": "logistica_id inválido"}

    doc = obtener_modificacion_previa(oid)
    if not doc or not doc.get("rutas_confirmadas"):
        return {"status": "ok", "rutas": [], "pdf_generado": False}

    db = get_db()
    t_ent = get_table("entregas")
    rutas_ui = []
    for ruta in doc.get("rutas_confirmadas", []) or []:
        ruta_id  = ruta.get("id")
        total    = len(ruta.get("sucursales", []) or []) + len(ruta.get("mayoristas", []) or [])
        entregas = [dict(f) for f in db.execute(select(t_ent).where(t_ent.c.ruta_id == ruta_id)).mappings()]

        entregadas_docs = [e for e in entregas if e.get("estado") == ESTADO_ENTREGADO]
        canceladas_docs = [e for e in entregas if e.get("estado") == ESTADO_CANCELADO]
        entregadas  = len(entregadas_docs)
        canceladas  = len(canceladas_docs)
        procesadas  = entregadas + canceladas
        pct         = round((entregadas / total) * 100, 1) if total else 0.0

        if procesadas == 0:
            estado = "sin_iniciar"
        elif total > 0 and procesadas >= total:
            estado = "completada"
        else:
            estado = "en_curso"

        # hora_inicio_real: primer timestamp de cualquier acción (entregado o cancelado)
        todas_fechas = [e["entregado_en"] for e in entregadas_docs if e.get("entregado_en")] + \
                       [e["cancelado_en"]  for e in canceladas_docs  if e.get("cancelado_en")]
        rutas_ui.append({
            "id":              ruta_id,
            "nombre":          ruta.get("nombre"),
            "dia":             ruta.get("dia"),
            "chofer":          ruta.get("chofer") or "Sin asignar",
            "vehiculo_abrev":  ruta.get("vehiculo_abrev"),
            "vehiculo_placas": ruta.get("vehiculo_placas"),
            "total_paradas":   total,
            "entregadas":      entregadas,
            "canceladas":      canceladas,
            "pct_avance":      pct,
            "estado":          estado,
            "hora_inicio_real": min(todas_fechas).isoformat() if todas_fechas else None,
            "hora_fin_real":    max(todas_fechas).isoformat() if estado == "completada" and todas_fechas else None,
        })

    return {"status": "ok", "rutas": rutas_ui, "autorizado": bool(doc.get("autorizado"))}


def _nombres_paradas_ruta(ruta_id: str) -> dict:
    """Mapa parada_key -> nombre legible para todos los stops de una ruta."""
    for lid in _logisticas_ids_con_modificacion():
        doc = obtener_modificacion_previa(lid)
        if not doc:
            continue
        for ruta in doc.get("rutas_confirmadas", []) or []:
            if ruta.get("id") != ruta_id:
                continue
            nombres = {}
            for s in ruta.get("sucursales", []) or []:
                key = str(s.get("num_tienda", ""))
                nombres[key] = s.get("nombre") or f"Sucursal {key}"
            for m in ruta.get("mayoristas", []) or []:
                key = str(m.get("documento") or m.get("id_cliente") or "")
                nombres[key] = m.get("nombre") or f"Mayorista {key}"
            return nombres
    return {}


def obtener_historial_entregas_ruta(ruta_id: str) -> dict:
    """
    Para la vista de administración (Seguimiento): historial completo de
    estado de cada parada de una ruta, incluyendo todos los cambios.
    """
    db      = get_db()
    nombres = _nombres_paradas_ruta(ruta_id)
    t_ent   = get_table("entregas")
    docs    = [dict(f) for f in db.execute(select(t_ent).where(t_ent.c.ruta_id == ruta_id)).mappings()]
    paradas = []
    for e in docs:
        key = e.get("parada_key", "")
        historial = _historial_de_entrega(db, e.get("mongo_id"))
        paradas.append({
            "parada_key":   key,
            "parada_tipo":  e.get("parada_tipo"),
            "nombre":       nombres.get(key) or key,
            "estado":       e.get("estado"),
            "entregado_en": e.get("entregado_en").isoformat() if e.get("entregado_en") else None,
            "cancelado_en": e.get("cancelado_en").isoformat()  if e.get("cancelado_en")  else None,
            "historial":    _fmt_historial(historial),
        })
    return {"status": "ok", "paradas": paradas}


# ── Autorización de rutas (Logística) ─────────────────────────────

def _estado_autorizacion_doc(doc: dict | None) -> str:
    if not doc:
        return "sin_autorizar"
    if doc.get("autorizado"):
        return "autorizado"
    if doc.get("cancelado_en"):
        return "cancelada"
    return "sin_autorizar"


def obtener_estado_autorizacion(logistica_id: str) -> dict:
    """Estado actual de autorización de una logística, para la UI de PDF y el menú principal."""
    oid = _id_valido(logistica_id)
    if not oid:
        return {"status": "error", "mensaje": "logistica_id inválido"}
    doc = obtener_modificacion_previa(oid)
    if not doc:
        return {
            "status": "ok", "estado": "sin_autorizar", "puede_autorizar": False,
            "autorizado_en": None, "autorizado_por": None, "cancelado_en": None, "cancelado_por": None,
        }
    return {
        "status":         "ok",
        "estado":         _estado_autorizacion_doc(doc),
        "puede_autorizar": bool(doc.get("rutas_confirmadas")),
        "autorizado_en":  doc["autorizado_en"].isoformat() if doc.get("autorizado_en") else None,
        "autorizado_por": doc.get("autorizado_por_nombre"),
        "cancelado_en":   doc["cancelado_en"].isoformat() if doc.get("cancelado_en") else None,
        "cancelado_por":  doc.get("cancelado_por_nombre"),
    }


def contar_entregas_logistica(logistica_id: str) -> int:
    oid = _id_valido(logistica_id)
    if not oid:
        return 0
    db = get_db()
    t_ent = get_table("entregas")
    return db.execute(select(func.count()).select_from(t_ent).where(
        t_ent.c.logistica_id == oid, t_ent.c.estado == ESTADO_ENTREGADO
    )).scalar()


def autorizar_rutas(logistica_id: str, usuario_id: str, usuario_nombre: str) -> dict:
    """
    Autoriza TODAS las rutas confirmadas de una logística de una sola vez:
    quedan visibles de inmediato para los choferes correspondientes y
    habilita la pestaña 'Seguimiento' para esa logística.
    """
    oid = _id_valido(logistica_id)
    if not oid:
        return {"status": "error", "mensaje": "logistica_id inválido"}

    doc = obtener_modificacion_previa(oid)
    if not doc or not doc.get("rutas_confirmadas"):
        return {"status": "error", "mensaje": "No hay rutas confirmadas para autorizar. Genera el PDF primero."}

    db = get_db()
    t  = get_table("modificaciones_rutas")
    ahora = datetime.now()
    db.execute(update(t).where(t.c.logistica_id == oid).values(
        autorizado=True,
        autorizado_en=ahora,
        autorizado_por=usuario_id,
        autorizado_por_nombre=usuario_nombre,
    ))
    n = len(doc.get("rutas_confirmadas", []))
    registrar_auditoria(usuario_id, usuario_nombre, "autorizar_rutas", oid,
                         f"Se autorizaron {n} ruta{'s' if n != 1 else ''} para los conductores")
    return {"status": "ok", "estado": "autorizado", "autorizado_en": ahora.isoformat()}


def cancelar_autorizacion(logistica_id: str, usuario_id: str, usuario_nombre: str) -> dict:
    """
    Retira la autorización de una logística: deja de ser visible para los
    choferes y se eliminan los registros de entrega ya hechos (la asignación
    deja de existir para ellos). Deshabilita la pestaña 'Seguimiento'.

    `entregas` y `entregas_historial` son dos tablas normalizadas que en
    Mongo eran un solo documento por entrega (historial embebido) — al
    borrar las entregas hay que borrar también su historial para no dejar
    filas huérfanas en `entregas_historial`.
    """
    oid = _id_valido(logistica_id)
    if not oid:
        return {"status": "error", "mensaje": "logistica_id inválido"}

    doc = obtener_modificacion_previa(oid)
    if not doc or not doc.get("autorizado"):
        return {"status": "error", "mensaje": "Esta logística no está autorizada actualmente."}

    t_ent  = get_table("entregas")
    t_hist = get_table("entregas_historial")
    t_mr   = get_table("modificaciones_rutas")
    ahora  = datetime.now()

    try:
        with transaccion() as conn:
            entrega_ids = [r.mongo_id for r in conn.execute(
                select(t_ent.c.mongo_id).where(t_ent.c.logistica_id == oid)
            )]
            entregas_eliminadas = len(entrega_ids)
            if entrega_ids:
                conn.execute(delete(t_hist).where(t_hist.c.entrega_id.in_(entrega_ids)))
                conn.execute(delete(t_ent).where(t_ent.c.logistica_id == oid))

            conn.execute(update(t_mr).where(t_mr.c.logistica_id == oid).values(
                autorizado=False,
                cancelado_en=ahora,
                cancelado_por=usuario_id,
                cancelado_por_nombre=usuario_nombre,
            ))
    except Exception as e:
        return {"status": "error", "mensaje": str(e)}

    registrar_auditoria(usuario_id, usuario_nombre, "cancelar_autorizacion", oid,
                         f"Autorización retirada — {entregas_eliminadas} entrega{'s eliminadas' if entregas_eliminadas != 1 else ' eliminada'}")
    return {"status": "ok", "estado": "cancelada", "entregas_eliminadas": entregas_eliminadas}
