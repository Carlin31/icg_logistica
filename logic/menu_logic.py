"""
logic/menu_logistica.py
Lógica de negocio para la gestión de Logísticas semanales.

Cada logística almacena SOLO metadatos en la tabla `logisticas`.
Los datos operativos (extraccion, asignacion, etc.) viven en sus propias
tablas con referencia a logistica_id:

  logisticas           → metadatos (nombre, fechas, estado)
  extraccion           → { logistica_id, datos, guardado_en, ... }
  asignaciones (+ tablas normalizadas) → { logistica_id, ...payload }
  modificaciones_rutas (+ tablas normalizadas) → { logistica_id, rutas_confirmadas, ... }
  config_dias          → { logistica_id, dia, habilitado, hora_salida, hora_limite }

NO se usan archivos JSON para ningún dato operativo.
"""
import re
import unicodedata
from datetime import datetime, date

from bson import ObjectId
from bson.errors import InvalidId
from sqlalchemy import select, insert, update, delete, desc

from db import get_db, get_table

MESES_ES = [
    "", "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]

# Mapeo sección → clave que espera el frontend en secciones_completadas
_CLAVE_FRONTEND = {
    "extraccion":           "extraccion",
    "asignaciones":         "asignacion",
    "modificaciones_rutas": "modificacion",
    "config_dias":          "config_dias",
}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _id_valido(doc_id: str) -> "str | None":
    try:
        return str(ObjectId(doc_id))
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


def _serialize(row) -> dict:
    d = dict(row)
    d["_id"] = d.pop("mongo_id")
    return d


# ── CRUD de Logísticas ───────────────────────────────────────────────────────

def listar_logisticas() -> list:
    """
    Devuelve todas las logísticas ordenadas de más reciente a más antigua.
    Enriquece cada logística con indicadores de progreso por sección.
    """
    db    = get_db()
    tabla = get_table("logisticas")
    filas = db.execute(select(tabla).order_by(desc(tabla.c.fecha_inicio))).mappings().all()
    logisticas = [_serialize(f) for f in filas]

    # Descartar documentos con _id inválido o nulo
    logisticas = [l for l in logisticas if l.get("_id") not in (None, "None", "")]

    tabla_extraccion   = get_table("extraccion")
    tabla_asignaciones = get_table("asignaciones")
    tabla_modrutas     = get_table("modificaciones_rutas")
    tabla_config_dias  = get_table("config_dias")

    for log in logisticas:
        lid = log["_id"]
        secciones = {}

        existe_extraccion = db.execute(
            select(tabla_extraccion.c.mongo_id).where(tabla_extraccion.c.logistica_id == lid)
        ).first()
        secciones[_CLAVE_FRONTEND["extraccion"]] = existe_extraccion is not None

        existe_asignaciones = db.execute(
            select(tabla_asignaciones.c.mongo_id).where(tabla_asignaciones.c.logistica_id == lid)
        ).first()
        secciones[_CLAVE_FRONTEND["asignaciones"]] = existe_asignaciones is not None

        doc_mod = db.execute(
            select(tabla_modrutas.c.autorizado, tabla_modrutas.c.cancelado_en)
            .where(tabla_modrutas.c.logistica_id == lid)
        ).mappings().first()
        secciones[_CLAVE_FRONTEND["modificaciones_rutas"]] = doc_mod is not None
        if not doc_mod:
            log["estado_autorizacion"] = "sin_autorizar"
        elif doc_mod["autorizado"]:
            log["estado_autorizacion"] = "autorizado"
        elif doc_mod["cancelado_en"]:
            log["estado_autorizacion"] = "cancelada"
        else:
            log["estado_autorizacion"] = "sin_autorizar"

        existe_config_dias = db.execute(
            select(tabla_config_dias.c.config_dias_id).where(tabla_config_dias.c.logistica_id == lid)
        ).first()
        secciones[_CLAVE_FRONTEND["config_dias"]] = existe_config_dias is not None

        log["secciones_completadas"] = secciones

    return logisticas


def obtener_logistica(logistica_id: str) -> "dict | None":
    """Devuelve los metadatos de la logística (sin datos de secciones)."""
    lid = _id_valido(logistica_id)
    if lid is None:
        return None
    db    = get_db()
    tabla = get_table("logisticas")
    fila  = db.execute(select(tabla).where(tabla.c.mongo_id == lid)).mappings().first()
    return _serialize(fila) if fila else None


def crear_logistica(fecha_inicio: str, fecha_fin: str) -> dict:
    """Crea una nueva logística. Valida rango de fechas y duplicados."""
    try:
        fi = date.fromisoformat(fecha_inicio)
        ff = date.fromisoformat(fecha_fin)
    except ValueError:
        return {"status": "error", "mensaje": "Formato de fecha inválido (esperado YYYY-MM-DD)."}

    if fi > ff:
        return {"status": "error", "mensaje": "La fecha de inicio no puede ser posterior a la fecha fin."}

    db    = get_db()
    tabla = get_table("logisticas")
    existente = db.execute(
        select(tabla.c.mongo_id).where(tabla.c.fecha_inicio == fecha_inicio, tabla.c.fecha_fin == fecha_fin)
    ).first()
    if existente:
        return {
            "status":  "error",
            "mensaje": f"Ya existe una logística para ese rango ({_nombre_automatico(fecha_inicio, fecha_fin)}).",
        }

    nombre = _nombre_automatico(fecha_inicio, fecha_fin)
    slug   = _slugify(nombre)
    ahora  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    nuevo_id = str(ObjectId())

    db.execute(insert(tabla).values(
        mongo_id=nuevo_id,
        nombre=nombre,
        slug=slug,
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        estado="en_progreso",
        creado_en=ahora,
        ultima_modificacion=ahora,
    ))
    return {
        "status": "ok",
        "id":     nuevo_id,
        "nombre": nombre,
        "slug":   slug,
    }


def obtener_logistica_por_slug(slug: str) -> "dict | None":
    """
    Busca una logística por su slug.
    Primero consulta el campo `slug` indexado; si no existe (registros previos
    sin slug), computa el slug dinámicamente desde el nombre.
    """
    db    = get_db()
    tabla = get_table("logisticas")
    fila  = db.execute(select(tabla).where(tabla.c.slug == slug)).mappings().first()
    if fila:
        return _serialize(fila)
    # Fallback para logísticas creadas antes de que se guardara el slug
    for fila in db.execute(select(tabla)).mappings():
        nombre = fila.get("nombre") or ""
        if _slugify(nombre) == slug:
            return _serialize(fila)
    return None


def eliminar_datos_asignacion(lid: str) -> None:
    """
    Borra la fila de `asignaciones` de esta logística y sus 8 tablas
    normalizadas hijas. `lid` debe ser un mongo_id ya validado.
    Reutilizada por eliminar_logistica() y por extraccion_router.eliminar_fuente()
    (que también debe limpiar la asignación vigente al borrar una fuente).
    """
    db = get_db()
    for nombre_tabla in (
        "asignaciones_rutas", "asignaciones_sucursales", "asignaciones_mayoristas",
        "asignaciones_chofer_overrides", "asignaciones_orden_overrides",
        "asignaciones_mayoristas_overrides", "asignaciones_sucursales_pendientes",
        "asignaciones_rutas_confirmadas",
    ):
        t = get_table(nombre_tabla)
        db.execute(delete(t).where(t.c.logistica_id == lid))
    db.execute(delete(get_table("asignaciones")).where(get_table("asignaciones").c.logistica_id == lid))


def eliminar_datos_vrp_reportes(lid: str) -> None:
    """
    Borra la fila de `vrp_reportes` (fuente 'vrp_reportes'; NO toca
    `vrp_reportes_afinidad`, igual que el código Mongo original) de esta
    logística y sus 4 tablas normalizadas hijas. `lid` debe ser un
    mongo_id ya validado. Reutilizada por eliminar_logistica() y por
    extraccion_router.eliminar_fuente().
    """
    db = get_db()
    tabla_vrp = get_table("vrp_reportes")
    ids_reporte = [
        row.mongo_id for row in
        db.execute(select(tabla_vrp.c.mongo_id).where(tabla_vrp.c.logistica_id == lid))
    ]
    if ids_reporte:
        for nombre_tabla in ("vrp_reportes_rutas", "vrp_reportes_sucursales", "vrp_reportes_mayoristas", "vrp_reportes_resumen"):
            t = get_table(nombre_tabla)
            db.execute(delete(t).where(t.c.reporte_id.in_(ids_reporte), t.c.fuente == "vrp_reportes"))
    db.execute(delete(tabla_vrp).where(tabla_vrp.c.logistica_id == lid))


def eliminar_logistica(logistica_id: str) -> dict:
    """
    Elimina permanentemente la logística y TODOS sus datos operativos
    en las tablas asociadas (base + tablas normalizadas hijas).
    """
    lid = _id_valido(logistica_id)
    if lid is None:
        return {"status": "error", "mensaje": "ID inválido."}

    db = get_db()
    tabla_logisticas = get_table("logisticas")
    if not db.execute(select(tabla_logisticas.c.mongo_id).where(tabla_logisticas.c.mongo_id == lid)).first():
        return {"status": "error", "mensaje": "Logística no encontrada."}

    # ── extraccion + su tabla normalizada ───────────────────────
    db.execute(delete(get_table("extraccion_desglose")).where(get_table("extraccion_desglose").c.logistica_id == lid))
    db.execute(delete(get_table("extraccion")).where(get_table("extraccion").c.logistica_id == lid))

    eliminar_datos_asignacion(lid)

    # ── modificaciones_rutas + sus 2 tablas normalizadas (por modificacion_id) ──
    tabla_modrutas = get_table("modificaciones_rutas")
    ids_modificacion = [
        row.mongo_id for row in
        db.execute(select(tabla_modrutas.c.mongo_id).where(tabla_modrutas.c.logistica_id == lid))
    ]
    if ids_modificacion:
        t_suc = get_table("modificacion_ruta_sucursales")
        t_may = get_table("modificacion_ruta_mayoristas")
        t_hdr = get_table("modificacion_rutas")
        db.execute(delete(t_suc).where(t_suc.c.modificacion_id.in_(ids_modificacion)))
        db.execute(delete(t_may).where(t_may.c.modificacion_id.in_(ids_modificacion)))
        db.execute(delete(t_hdr).where(t_hdr.c.modificacion_id.in_(ids_modificacion)))
    db.execute(delete(tabla_modrutas).where(tabla_modrutas.c.logistica_id == lid))

    # ── config_dias ──────────────────────────────────────────────
    t_config_dias = get_table("config_dias")
    db.execute(delete(t_config_dias).where(t_config_dias.c.logistica_id == lid))

    # rutas_historicas guarda logistica_id como string, no ObjectId (igual que en Mongo)
    db.execute(delete(get_table("rutas_historicas")).where(get_table("rutas_historicas").c.logistica_id == lid))

    eliminar_datos_vrp_reportes(lid)

    db.execute(delete(tabla_logisticas).where(tabla_logisticas.c.mongo_id == lid))

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
    lid = _id_valido(logistica_id)
    if lid is None:
        return {"status": "error", "mensaje": "ID inválido."}
    db    = get_db()
    tabla = get_table("logisticas")
    resultado = db.execute(
        update(tabla)
        .where(tabla.c.mongo_id == lid)
        .values(estado="completada", ultima_modificacion=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    if resultado.rowcount == 0:
        return {"status": "error", "mensaje": "Logística no encontrada."}
    return {"status": "ok"}
