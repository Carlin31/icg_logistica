"""
logic/auditoria_logic.py
Historial de auditoría de acciones sensibles (autorizar/cancelar rutas, etc.).
Solo registra — no expone edición ni borrado.
"""
import re
from datetime import datetime

from bson import ObjectId
from sqlalchemy import select, insert, desc

from db import get_db, get_table


def registrar_auditoria(usuario_id: str, usuario_nombre: str, accion: str, logistica_id, detalle: str = "") -> None:
    """Inserta un evento de auditoría. Nunca lanza excepción hacia el flujo principal."""
    try:
        db    = get_db()
        tabla = get_table("auditoria")
        db.execute(insert(tabla).values(
            mongo_id=str(ObjectId()),
            usuario_id=str(usuario_id) if usuario_id is not None else None,
            usuario_nombre=usuario_nombre,
            accion=accion,
            logistica_id=str(logistica_id) if logistica_id is not None else None,
            detalle=detalle,
            fecha=datetime.now(),
        ))
    except Exception as e:
        print(f"[registrar_auditoria] Error: {e}")


# ── Resolución de nombres para registros en formato antiguo ────────────────

_RE_FORMATO_ANTIGUO = re.compile(
    r'^Parada\s+(\S+)\s+\((sucursal|mayorista)\)\s+(\S+)\s+en ruta\s+(\S+)',
    re.IGNORECASE,
)


def _construir_cache_nombres(db) -> dict:
    """Construye mapa {ruta_id: {parada_key: nombre, _nombre_ruta: str}} desde modificacion_rutas + hijas."""
    tabla_rutas      = get_table("modificacion_rutas")
    tabla_sucursales = get_table("modificacion_ruta_sucursales")
    tabla_mayoristas = get_table("modificacion_ruta_mayoristas")

    cache: dict = {}
    rutas = db.execute(
        select(tabla_rutas.c.modificacion_id, tabla_rutas.c.ruta_key, tabla_rutas.c.nombre)
    ).all()

    for modificacion_id, ruta_key, nombre_ruta in rutas:
        if not ruta_key:
            continue
        nombres: dict = {}
        for s in db.execute(
            select(tabla_sucursales.c.num_tienda, tabla_sucursales.c.nombre)
            .where(tabla_sucursales.c.modificacion_id == modificacion_id, tabla_sucursales.c.ruta_key == ruta_key)
        ):
            key = str(s.num_tienda or "")
            if key:
                nombres[key] = s.nombre or key
        for m in db.execute(
            select(tabla_mayoristas.c.documento, tabla_mayoristas.c.id_cliente, tabla_mayoristas.c.nombre)
            .where(tabla_mayoristas.c.modificacion_id == modificacion_id, tabla_mayoristas.c.ruta_key == ruta_key)
        ):
            key = str(m.documento or m.id_cliente or "")
            if key:
                nombres[key] = m.nombre or key
        # Igual que en Mongo (iterar todos los documentos y sobrescribir por
        # ruta_id repetido): si el mismo ruta_key aparece en más de una
        # modificación, gana la última fila devuelta por la consulta.
        cache[ruta_key] = {
            "nombres":     nombres,
            "nombre_ruta": nombre_ruta or ruta_key,
        }
    return cache


def _enriquecer_detalle(detalle: str, cache: dict) -> str:
    """Convierte un detalle en formato antiguo a formato legible con nombre real de la parada."""
    if not detalle or " en ruta " not in detalle:
        return detalle

    m = _RE_FORMATO_ANTIGUO.match(detalle)
    if not m:
        return detalle

    parada_key = m.group(1)
    parada_tipo = m.group(2).lower()
    accion_raw  = m.group(3)   # "cancelada", "re-entregada"
    ruta_id     = m.group(4)

    tipo_label = "Sucursal" if parada_tipo == "sucursal" else "Mayorista"

    ruta_data = cache.get(ruta_id)
    if not ruta_data:
        # Ruta ya no existe en BD; al menos quitar el ID crudo
        return f"{tipo_label} {parada_key} · {accion_raw}"

    nombre      = ruta_data["nombres"].get(str(parada_key), parada_key)
    ruta_nombre = ruta_data["nombre_ruta"]
    return f"{tipo_label} {accion_raw}: {nombre} · Ruta: {ruta_nombre}"


# ── Listado ────────────────────────────────────────────────────────────────

def listar_auditoria(logistica_id=None, limite: int = 200) -> list:
    """Devuelve los eventos más recientes, opcionalmente filtrados por logística.
    Los registros en formato antiguo se enriquecen con el nombre real de la parada."""
    db    = get_db()
    tabla = get_table("auditoria")

    consulta = select(tabla).order_by(desc(tabla.c.fecha)).limit(limite)
    if logistica_id:
        consulta = consulta.where(tabla.c.logistica_id == str(logistica_id))
    registros = db.execute(consulta).mappings().all()

    # Solo construir el cache si hay registros con formato antiguo
    necesita_cache = any(" en ruta " in (r.get("detalle") or "") for r in registros)
    cache = _construir_cache_nombres(db) if necesita_cache else {}

    resultado = []
    for d in registros:
        d = dict(d)
        d["fecha"]   = d["fecha"].isoformat() if d.get("fecha") else None
        d["detalle"] = _enriquecer_detalle(d.get("detalle") or "", cache)
        resultado.append(d)
    return resultado
