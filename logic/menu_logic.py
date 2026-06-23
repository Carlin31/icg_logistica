"""
logic/menu_logistica.py
Lógica de negocio para la gestión de Logísticas semanales.

Renombrado desde logistica_logic.py para unificar la nomenclatura del módulo
de menú principal bajo el prefijo "menu_".

Cada logística almacena SOLO metadatos en la colección `logisticas`.
Los datos operativos (extraccion, asignacion, etc.) viven en sus propias
colecciones con referencia a logistica_id:

  logisticas           → metadatos (nombre, fechas, estado)
  extraccion           → { logistica_id, datos, guardado_en }
  asignaciones         → { logistica_id, ...payload }
  modificaciones_rutas → { logistica_id, rutas_confirmadas, ... }
  config_dias          → { logistica_id, config_dias, actualizado_en }

NO se usan archivos JSON para ningún dato operativo.
"""
import os
import re
import unicodedata
from datetime import datetime, date
from bson import ObjectId
from bson.errors import InvalidId

from db import get_db

MESES_ES = [
    "", "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]

# Colecciones asociadas a una logística (para limpieza al eliminar)
COLECCIONES_SECCION = [
    "extraccion",
    "asignaciones",
    "modificaciones_rutas",
    "config_dias",
]

# Mapeo colección → clave que espera el frontend en secciones_completadas
_CLAVE_FRONTEND = {
    "extraccion":           "extraccion",
    "asignaciones":         "asignacion",
    "modificaciones_rutas": "modificacion",
    "config_dias":          "config_dias",
}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _serialize(doc: dict) -> dict:
    """Convierte ObjectId → str para serializar a JSON."""
    doc = dict(doc)
    raw_id = doc.get("_id")
    if raw_id is not None:
        doc["_id"] = str(raw_id)
    else:
        doc["_id"] = None
    return doc


def _parse_oid(doc_id: str) -> "ObjectId | None":
    try:
        return ObjectId(doc_id)
    except (InvalidId, TypeError):
        return None


def _slugify(text: str) -> str:
    """Convierte un nombre de logística en un slug URL-seguro.
    Ej: "Logística del 1 al 5 de abril del 2026" → "logistica-del-1-al-5-de-abril-del-2026"
    """
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def _nombre_automatico(fecha_inicio_str: str, fecha_fin_str: str) -> str:
    fi = date.fromisoformat(fecha_inicio_str)
    ff = date.fromisoformat(fecha_fin_str)
    mes_i = MESES_ES[fi.month]
    mes_f = MESES_ES[ff.month]
    anio = ff.year
    if fi.month == ff.month and fi.year == ff.year:
        return f"Logística del {fi.day} al {ff.day} de {mes_i} del {anio}"
    return f"Logística del {fi.day} de {mes_i} al {ff.day} de {mes_f} del {anio}"


# ── CRUD de Logísticas ───────────────────────────────────────────────────────

def listar_logisticas() -> list:
    """
    Devuelve todas las logísticas ordenadas de más reciente a más antigua.
    Enriquece cada logística con indicadores de progreso por sección.
    """
    db = get_db()
    cursor = db.logisticas.find({}).sort("fecha_inicio", -1)
    logisticas = [_serialize(doc) for doc in cursor]

    # Descartar documentos con _id inválido o nulo
    logisticas = [l for l in logisticas if l.get("_id") not in (None, "None", "")]

    # Agregar indicadores de secciones completadas + estado de autorización
    for log in logisticas:
        lid = _parse_oid(log["_id"])
        if not lid:
            log["secciones_completadas"] = {}
            log["estado_autorizacion"] = "sin_autorizar"
            continue
        secciones = {}
        for col in COLECCIONES_SECCION:
            clave = _CLAVE_FRONTEND.get(col, col)
            try:
                if col == "modificaciones_rutas":
                    doc = db[col].find_one({"logistica_id": lid}, {"autorizado": 1, "cancelado_en": 1})
                    secciones[clave] = doc is not None
                    if not doc:
                        log["estado_autorizacion"] = "sin_autorizar"
                    elif doc.get("autorizado"):
                        log["estado_autorizacion"] = "autorizado"
                    elif doc.get("cancelado_en"):
                        log["estado_autorizacion"] = "cancelada"
                    else:
                        log["estado_autorizacion"] = "sin_autorizar"
                else:
                    existe = db[col].find_one({"logistica_id": lid}, {"_id": 1})
                    secciones[clave] = existe is not None
            except Exception:
                secciones[clave] = False
        log["secciones_completadas"] = secciones

    return logisticas


def obtener_logistica(logistica_id: str) -> "dict | None":
    """Devuelve los metadatos de la logística (sin datos de secciones)."""
    oid = _parse_oid(logistica_id)
    if oid is None:
        return None
    db = get_db()
    doc = db.logisticas.find_one({"_id": oid})
    return _serialize(doc) if doc else None


def crear_logistica(fecha_inicio: str, fecha_fin: str) -> dict:
    """Crea una nueva logística. Valida rango de fechas y duplicados."""
    try:
        fi = date.fromisoformat(fecha_inicio)
        ff = date.fromisoformat(fecha_fin)
    except ValueError:
        return {"status": "error", "mensaje": "Formato de fecha inválido (esperado YYYY-MM-DD)."}

    if fi > ff:
        return {"status": "error", "mensaje": "La fecha de inicio no puede ser posterior a la fecha fin."}

    db = get_db()
    existente = db.logisticas.find_one({
        "fecha_inicio": fecha_inicio,
        "fecha_fin":    fecha_fin,
    })
    if existente:
        return {
            "status":  "error",
            "mensaje": f"Ya existe una logística para ese rango ({_nombre_automatico(fecha_inicio, fecha_fin)}).",
        }

    nombre = _nombre_automatico(fecha_inicio, fecha_fin)
    slug   = _slugify(nombre)
    ahora  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    doc = {
        "nombre":              nombre,
        "slug":                slug,
        "fecha_inicio":        fecha_inicio,
        "fecha_fin":           fecha_fin,
        "estado":              "en_progreso",
        "creado_en":           ahora,
        "ultima_modificacion": ahora,
    }

    result = db.logisticas.insert_one(doc)
    return {
        "status": "ok",
        "id":     str(result.inserted_id),
        "nombre": nombre,
        "slug":   slug,
    }


def obtener_logistica_por_slug(slug: str) -> "dict | None":
    """
    Busca una logística por su slug.
    Primero consulta el campo `slug` indexado; si no existe (registros previos
    sin slug), computa el slug dinámicamente desde el nombre.
    """
    db  = get_db()
    doc = db.logisticas.find_one({"slug": slug})
    if doc:
        return _serialize(doc)
    # Fallback para logísticas creadas antes de que se guardara el slug
    for doc in db.logisticas.find({}):
        nombre = doc.get("nombre", "")
        if _slugify(nombre) == slug:
            return _serialize(doc)
    return None


def eliminar_logistica(logistica_id: str) -> dict:
    """
    Elimina permanentemente la logística y TODOS sus datos operativos
    en las colecciones asociadas.
    """
    oid = _parse_oid(logistica_id)
    if oid is None:
        return {"status": "error", "mensaje": "ID inválido."}

    db = get_db()
    result = db.logisticas.delete_one({"_id": oid})
    if result.deleted_count == 0:
        return {"status": "error", "mensaje": "Logística no encontrada."}

    for col in COLECCIONES_SECCION:
        db[col].delete_many({"logistica_id": oid})

    # rutas_historicas guarda logistica_id como string, no ObjectId
    db["rutas_historicas"].delete_many({"logistica_id": str(oid)})
    db["vrp_reportes"].delete_many({"logistica_id": oid})

    return {"status": "ok"}


# ── Activación ───────────────────────────────────────────────────────────────

def activar_logistica(logistica_id: str) -> dict:
    """
    Activa una logística existente.
    Solo valida que exista y devuelve sus metadatos.
    """
    logistica = obtener_logistica(logistica_id)
    if not logistica:
        return {"status": "error", "mensaje": "Logística no encontrada."}

    slug = logistica.get("slug") or _slugify(logistica["nombre"])
    return {
        "status":       "ok",
        "id":           logistica["_id"],
        "nombre":       logistica["nombre"],
        "slug":         slug,
        "fecha_inicio": logistica["fecha_inicio"],
        "fecha_fin":    logistica["fecha_fin"],
        "estado":       logistica.get("estado", "en_progreso"),
    }


def marcar_completada(logistica_id: str) -> dict:
    """Cambia el estado de la logística a 'completada'."""
    oid = _parse_oid(logistica_id)
    if oid is None:
        return {"status": "error", "mensaje": "ID inválido."}
    db = get_db()
    result = db.logisticas.update_one(
        {"_id": oid},
        {"$set": {
            "estado": "completada",
            "ultima_modificacion": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }},
    )
    if result.matched_count == 0:
        return {"status": "error", "mensaje": "Logística no encontrada."}
    return {"status": "ok"}


def _actualizar_timestamp(logistica_id: str) -> None:
    """Actualiza ultima_modificacion en la logística padre."""
    oid = _parse_oid(logistica_id)
    if not oid:
        return
    try:
        db = get_db()
        db.logisticas.update_one(
            {"_id": oid},
            {"$set": {"ultima_modificacion": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}},
        )
    except Exception:
        pass