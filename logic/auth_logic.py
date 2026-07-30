"""
logic/auth_logic.py
Autenticación de usuarios contra SQL Server: hash de contraseñas con bcrypt
(incluye salt automático), validación de credenciales y bloqueo temporal
de cuentas tras intentos fallidos repetidos.
"""
import re
import secrets
import string
import unicodedata
from datetime import datetime, timedelta

import bcrypt
from bson import ObjectId
from bson.errors import InvalidId
from sqlalchemy import select, insert, update, delete, func

from db import get_db, get_table

ROL_LOGISTICA = "logistica"
ROL_CONDUCTOR = "conductor"

MAX_INTENTOS_FALLIDOS = 5
BLOQUEO_MINUTOS = 15

ZOOM_MIN     = 80
ZOOM_MAX     = 150
ZOOM_DEFAULT = 100


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def _verificar_password(password: str, password_hash) -> bool:
    if isinstance(password_hash, str):
        password_hash = password_hash.encode("utf-8")
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash)
    except (ValueError, TypeError):
        return False


def generar_password_segura(longitud: int = 14) -> str:
    """Genera una contraseña aleatoria criptográficamente segura."""
    alfabeto = string.ascii_letters + string.digits + "!@#$%&*?"
    return "".join(secrets.choice(alfabeto) for _ in range(longitud))


def _nuevo_id() -> str:
    """Genera un id nuevo con el mismo formato que los mongo_id ya migrados."""
    return str(ObjectId())


def _id_valido(doc_id: str) -> "str | None":
    """Valida el formato de un mongo_id (24 hex, igual que ObjectId). None si es inválido."""
    try:
        return str(ObjectId(doc_id))
    except (InvalidId, TypeError):
        return None


def crear_usuario(
    username: str,
    password: str,
    nombre: str = "",
    es_admin: bool = False,
    rol: str = ROL_LOGISTICA,
    chofer_id=None,
) -> dict:
    db     = get_db()
    tabla  = get_table("usuarios")
    username = username.strip().lower()
    if db.execute(select(tabla.c.mongo_id).where(tabla.c.username == username)).first():
        return {"status": "error", "mensaje": f"El usuario '{username}' ya existe"}

    nuevo_id = _nuevo_id()
    db.execute(insert(tabla).values(
        mongo_id=nuevo_id,
        username=username,
        password_hash=_hash_password(password),
        nombre=nombre or username,
        es_admin=es_admin,
        rol=rol,
        chofer_id=str(chofer_id) if chofer_id else None,
        activo=True,
        intentos_fallidos=0,
        bloqueado_hasta=None,
        ultimo_login=None,
        creado_en=datetime.utcnow(),
    ))
    return {"status": "ok", "id": nuevo_id}


def crear_usuarios_iniciales() -> list:
    """
    Crea 5 usuarios iniciales con contraseñas aleatorias si la tabla
    'usuarios' está vacía. Retorna las credenciales en texto plano (única
    vez posible, ya que el hash bcrypt no es reversible) para que el
    administrador las distribuya de forma segura.
    """
    db    = get_db()
    tabla = get_table("usuarios")
    total = db.execute(select(func.count()).select_from(tabla)).scalar()
    if total > 0:
        return []

    base_usuarios = [
        ("admin",       "Administrador", True),
        ("logistica1",  "Logística 1",   False),
        ("logistica2",  "Logística 2",   False),
        ("logistica3",  "Logística 3",   False),
        ("logistica4",  "Logística 4",   False),
    ]
    credenciales = []
    for username, nombre, es_admin in base_usuarios:
        password = generar_password_segura()
        crear_usuario(username, password, nombre, es_admin)
        credenciales.append({"username": username, "password": password, "nombre": nombre})
    return credenciales


def autenticar(username: str, password: str) -> dict:
    """
    Valida credenciales contra SQL Server.
    Bloquea la cuenta por BLOQUEO_MINUTOS tras MAX_INTENTOS_FALLIDOS intentos.
    Retorna {status:'ok', usuario:{...}} o {status:'error', mensaje:str}.
    """
    db    = get_db()
    tabla = get_table("usuarios")
    username = (username or "").strip().lower()
    if not username or not password:
        return {"status": "error", "mensaje": "Usuario y contraseña requeridos"}

    usuario = db.execute(select(tabla).where(tabla.c.username == username)).mappings().first()
    error_generico = {"status": "error", "mensaje": "Usuario o contraseña incorrectos"}
    if not usuario or not (usuario["activo"] if usuario["activo"] is not None else True):
        return error_generico

    ahora = datetime.utcnow()
    bloqueado_hasta_raw = usuario["bloqueado_hasta"]
    bloqueado_hasta = datetime.fromisoformat(bloqueado_hasta_raw) if bloqueado_hasta_raw else None
    if bloqueado_hasta and bloqueado_hasta > ahora:
        minutos = max(1, int((bloqueado_hasta - ahora).total_seconds() // 60) + 1)
        return {"status": "error", "mensaje": f"Cuenta bloqueada temporalmente. Intenta en {minutos} min."}

    if not _verificar_password(password, usuario["password_hash"]):
        intentos = (usuario["intentos_fallidos"] or 0) + 1
        valores = {"intentos_fallidos": intentos}
        if intentos >= MAX_INTENTOS_FALLIDOS:
            valores["bloqueado_hasta"]   = (ahora + timedelta(minutes=BLOQUEO_MINUTOS)).isoformat()
            valores["intentos_fallidos"] = 0
        db.execute(update(tabla).where(tabla.c.mongo_id == usuario["mongo_id"]).values(**valores))
        return error_generico

    db.execute(
        update(tabla)
        .where(tabla.c.mongo_id == usuario["mongo_id"])
        .values(intentos_fallidos=0, bloqueado_hasta=None, ultimo_login=ahora)
    )
    return {
        "status": "ok",
        "usuario": {
            "id":        usuario["mongo_id"],
            "username":  usuario["username"],
            "nombre":    usuario["nombre"] or usuario["username"],
            "es_admin":  bool(usuario["es_admin"]),
            "rol":       usuario["rol"] or ROL_LOGISTICA,
            "chofer_id": usuario["chofer_id"],
        },
    }


def _username_desde_nombre(nombre: str) -> str:
    """Convierte un nombre de chofer en un username slug: 'Juan Pérez' -> 'juan.perez'."""
    sin_acentos = unicodedata.normalize("NFKD", nombre or "").encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", ".", sin_acentos.lower()).strip(".")
    return slug or "chofer"


def generar_acceso_chofer(chofer_id: str) -> dict:
    """
    Crea credenciales de acceso (usuario + contraseña) para un chofer ya
    registrado en la tabla `choferes`, vinculando el usuario creado de
    vuelta al chofer (`choferes.usuario_id`). La contraseña solo se devuelve
    en esta llamada (no es recuperable después, igual que crear_usuarios_iniciales).
    """
    db  = get_db()
    tabla_usuarios  = get_table("usuarios")
    tabla_choferes  = get_table("choferes")

    cid = _id_valido(chofer_id)
    if cid is None:
        return {"status": "error", "mensaje": "ID de chofer inválido"}

    chofer = db.execute(select(tabla_choferes).where(tabla_choferes.c.mongo_id == cid)).mappings().first()
    if not chofer:
        return {"status": "error", "mensaje": "Chofer no encontrado"}
    if chofer["usuario_id"]:
        return {"status": "error", "mensaje": "Este chofer ya tiene acceso generado"}

    nombre = chofer["nombre"] or ""
    base_username = _username_desde_nombre(nombre)
    username = base_username
    sufijo = 1
    while db.execute(select(tabla_usuarios.c.mongo_id).where(tabla_usuarios.c.username == username)).first():
        sufijo += 1
        username = f"{base_username}{sufijo}"

    password  = generar_password_segura()
    resultado = crear_usuario(username, password, nombre, es_admin=False, rol=ROL_CONDUCTOR, chofer_id=cid)
    if resultado.get("status") != "ok":
        return resultado

    db.execute(update(tabla_choferes).where(tabla_choferes.c.mongo_id == cid).values(usuario_id=resultado["id"]))
    return {"status": "ok", "username": username, "password": password, "nombre": nombre}


def _serializar_usuario(row) -> dict:
    """Versión segura de un usuario para listarlo (nunca incluye password_hash)."""
    return {
        "id":             row["mongo_id"],
        "username":       row["username"] or "",
        "nombre":         row["nombre"] or "",
        "rol":            row["rol"] or ROL_LOGISTICA,
        "es_admin":       bool(row["es_admin"]),
        "activo":         row["activo"] if row["activo"] is not None else True,
        "chofer_id":      row["chofer_id"],
        "ultimo_login":   row["ultimo_login"].isoformat() if row["ultimo_login"] else None,
        "creado_en":      row["creado_en"].isoformat() if row["creado_en"] else None,
    }


def listar_usuarios() -> list:
    """Todos los usuarios del sistema (Logística y Conductor), sin datos sensibles."""
    db    = get_db()
    tabla = get_table("usuarios")
    filas = db.execute(select(tabla).order_by(tabla.c.username)).mappings().all()
    return [_serializar_usuario(f) for f in filas]


def obtener_usuario(usuario_id: str) -> "dict | None":
    uid = _id_valido(usuario_id)
    if uid is None:
        return None
    db    = get_db()
    tabla = get_table("usuarios")
    fila = db.execute(select(tabla).where(tabla.c.mongo_id == uid)).mappings().first()
    return _serializar_usuario(fila) if fila else None


def crear_usuario_admin(username: str, password: str, nombre: str, es_admin: bool = False) -> dict:
    """
    Crea un usuario de Logística desde la pantalla de Gestión de Usuarios.
    Solo crea usuarios de rol "logistica" — los de rol "conductor" se crean
    exclusivamente desde Configuración > Choferes (quedan vinculados a un
    chofer real; crear uno "suelto" aquí los dejaría sin acceso al portal).
    """
    username = (username or "").strip().lower()
    password = password or ""
    if not username or not password:
        return {"status": "error", "mensaje": "Usuario y contraseña son obligatorios"}
    if len(password) < 6:
        return {"status": "error", "mensaje": "La contraseña debe tener al menos 6 caracteres"}
    return crear_usuario(username, password, nombre, es_admin=es_admin, rol=ROL_LOGISTICA)


def editar_usuario(usuario_id: str, datos: dict, usuario_actual_id: "str | None" = None) -> dict:
    """
    Modifica username/nombre/es_admin y, opcionalmente, la contraseña
    (solo si se envía no vacía) de un usuario existente.
    """
    uid = _id_valido(usuario_id)
    if uid is None:
        return {"status": "error", "mensaje": "ID inválido"}

    db    = get_db()
    tabla = get_table("usuarios")
    fila  = db.execute(select(tabla).where(tabla.c.mongo_id == uid)).mappings().first()
    if not fila:
        return {"status": "error", "mensaje": "Usuario no encontrado"}

    cambios: dict = {}

    nuevo_username = (datos.get("username") or "").strip().lower()
    if nuevo_username and nuevo_username != fila["username"]:
        dup = db.execute(
            select(tabla.c.mongo_id)
            .where(tabla.c.username == nuevo_username, tabla.c.mongo_id != uid)
        ).first()
        if dup:
            return {"status": "error", "mensaje": f"El usuario '{nuevo_username}' ya existe"}
        cambios["username"] = nuevo_username

    if "nombre" in datos and (datos.get("nombre") or "").strip():
        cambios["nombre"] = datos["nombre"].strip()

    # No permitir que un administrador se quite a sí mismo el rol de admin
    # por accidente desde este mismo formulario (evita quedar sin acceso).
    if "es_admin" in datos:
        es_admin_nuevo = bool(datos.get("es_admin"))
        if fila["es_admin"] and not es_admin_nuevo and str(uid) == str(usuario_actual_id):
            return {"status": "error", "mensaje": "No puedes quitarte el rol de administrador a ti mismo"}
        cambios["es_admin"] = es_admin_nuevo

    password_nueva = datos.get("password") or ""
    if password_nueva:
        if len(password_nueva) < 6:
            return {"status": "error", "mensaje": "La contraseña debe tener al menos 6 caracteres"}
        cambios["password_hash"] = _hash_password(password_nueva)

    if not cambios:
        return {"status": "ok"}

    db.execute(update(tabla).where(tabla.c.mongo_id == uid).values(**cambios))
    return {"status": "ok"}


def eliminar_usuario(usuario_id: str, usuario_actual_id: "str | None" = None) -> dict:
    """
    Elimina un usuario. Si era de rol conductor, libera el vínculo en su
    chofer (`choferes.usuario_id`) para que se le pueda generar un acceso
    nuevo más adelante. Protege contra auto-eliminación y contra dejar el
    sistema sin ningún administrador activo.
    """
    uid = _id_valido(usuario_id)
    if uid is None:
        return {"status": "error", "mensaje": "ID inválido"}

    if str(uid) == str(usuario_actual_id):
        return {"status": "error", "mensaje": "No puedes eliminar tu propia cuenta"}

    db    = get_db()
    tabla = get_table("usuarios")
    fila  = db.execute(select(tabla).where(tabla.c.mongo_id == uid)).mappings().first()
    if not fila:
        return {"status": "error", "mensaje": "Usuario no encontrado"}

    if fila["es_admin"]:
        otros_admins = db.execute(
            select(func.count()).select_from(tabla)
            .where(tabla.c.es_admin == True, tabla.c.mongo_id != uid)  # noqa: E712
        ).scalar()
        if otros_admins == 0:
            return {"status": "error", "mensaje": "No puedes eliminar al último administrador del sistema"}

    if fila["rol"] == ROL_CONDUCTOR and fila["chofer_id"]:
        tabla_choferes = get_table("choferes")
        db.execute(update(tabla_choferes).where(tabla_choferes.c.mongo_id == fila["chofer_id"]).values(usuario_id=None))

    db.execute(delete(tabla).where(tabla.c.mongo_id == uid))
    return {"status": "ok"}


def obtener_zoom(usuario_id: str) -> int:
    """Devuelve el nivel de zoom de página guardado para el usuario (o el default)."""
    uid = _id_valido(usuario_id)
    if uid is None:
        return ZOOM_DEFAULT
    db    = get_db()
    tabla = get_table("usuarios")
    fila = db.execute(select(tabla.c.zoom_pct).where(tabla.c.mongo_id == uid)).mappings().first()
    if not fila:
        return ZOOM_DEFAULT
    zoom = fila["zoom_pct"]
    if zoom is not None and ZOOM_MIN <= int(zoom) <= ZOOM_MAX:
        return int(zoom)
    return ZOOM_DEFAULT


def guardar_zoom(usuario_id: str, zoom) -> dict:
    """Persiste el nivel de zoom elegido por el usuario, para que se mantenga entre sesiones."""
    uid = _id_valido(usuario_id)
    if uid is None:
        return {"status": "error", "mensaje": "Sesión inválida"}
    try:
        zoom_int = int(zoom)
    except (TypeError, ValueError):
        return {"status": "error", "mensaje": "Zoom inválido"}

    zoom_int = max(ZOOM_MIN, min(ZOOM_MAX, zoom_int))
    db    = get_db()
    tabla = get_table("usuarios")
    db.execute(update(tabla).where(tabla.c.mongo_id == uid).values(zoom_pct=zoom_int))
    return {"status": "ok", "zoom": zoom_int}
