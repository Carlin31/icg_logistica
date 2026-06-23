"""
logic/auditoria_logic.py
Historial de auditoría de acciones sensibles (autorizar/cancelar rutas, etc.).
Solo registra — no expone edición ni borrado.
"""
import re
from datetime import datetime

from db import get_db


def registrar_auditoria(usuario_id: str, usuario_nombre: str, accion: str, logistica_id, detalle: str = "") -> None:
    """Inserta un evento de auditoría. Nunca lanza excepción hacia el flujo principal."""
    try:
        db = get_db()
        db["auditoria"].insert_one({
            "usuario_id":     usuario_id,
            "usuario_nombre": usuario_nombre,
            "accion":         accion,
            "logistica_id":   logistica_id,
            "detalle":        detalle,
            "fecha":          datetime.now(),
        })
    except Exception as e:
        print(f"[registrar_auditoria] Error: {e}")


# ── Resolución de nombres para registros en formato antiguo ────────────────

_RE_FORMATO_ANTIGUO = re.compile(
    r'^Parada\s+(\S+)\s+\((sucursal|mayorista)\)\s+(\S+)\s+en ruta\s+(\S+)',
    re.IGNORECASE,
)


def _construir_cache_nombres(db) -> dict:
    """Construye mapa {ruta_id: {parada_key: nombre, _nombre_ruta: str}} desde modificaciones_rutas."""
    cache = {}
    for doc in db["modificaciones_rutas"].find({}, {"rutas_confirmadas": 1}):
        for ruta in doc.get("rutas_confirmadas", []) or []:
            ruta_id = ruta.get("id")
            if not ruta_id:
                continue
            nombres = {}
            for s in ruta.get("sucursales", []) or []:
                key = str(s.get("num_tienda", ""))
                if key:
                    nombres[key] = s.get("nombre") or key
            for m in ruta.get("mayoristas", []) or []:
                key = str(m.get("documento") or m.get("id_cliente") or "")
                if key:
                    nombres[key] = m.get("nombre") or key
            cache[ruta_id] = {
                "nombres":      nombres,
                "nombre_ruta":  ruta.get("nombre") or ruta_id,
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
    db = get_db()
    query = {"logistica_id": logistica_id} if logistica_id else {}
    registros = list(db["auditoria"].find(query).sort("fecha", -1).limit(limite))

    # Solo construir el cache si hay registros con formato antiguo
    necesita_cache = any(" en ruta " in (r.get("detalle") or "") for r in registros)
    cache = _construir_cache_nombres(db) if necesita_cache else {}

    resultado = []
    for d in registros:
        d = dict(d)
        d["_id"]          = str(d["_id"])
        d["logistica_id"] = str(d["logistica_id"]) if d.get("logistica_id") else None
        d["fecha"]        = d["fecha"].isoformat() if d.get("fecha") else None
        d["detalle"]      = _enriquecer_detalle(d.get("detalle") or "", cache)
        resultado.append(d)
    return resultado
