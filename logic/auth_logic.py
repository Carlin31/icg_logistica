"""
logic/auth_logic.py
Autenticación de usuarios contra MongoDB: hash de contraseñas con bcrypt
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

from db import get_db

ROL_LOGISTICA = "logistica"
ROL_CONDUCTOR = "conductor"

MAX_INTENTOS_FALLIDOS = 5
BLOQUEO_MINUTOS = 15

ZOOM_MIN     = 80
ZOOM_MAX     = 150
ZOOM_DEFAULT = 100


def _hash_password(password: str) -> bytes:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12))


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


def crear_usuario(
    username: str,
    password: str,
    nombre: str = "",
    es_admin: bool = False,
    rol: str = ROL_LOGISTICA,
    chofer_id=None,
) -> dict:
    db = get_db()
    username = username.strip().lower()
    if db.usuarios.find_one({"username": username}):
        return {"status": "error", "mensaje": f"El usuario '{username}' ya existe"}
    doc = {
        "username":          username,
        "password_hash":     _hash_password(password),
        "nombre":            nombre or username,
        "es_admin":          es_admin,
        "rol":               rol,
        "chofer_id":         chofer_id,
        "activo":            True,
        "intentos_fallidos": 0,
        "bloqueado_hasta":   None,
        "ultimo_login":      None,
        "creado_en":         datetime.utcnow(),
    }
    result = db.usuarios.insert_one(doc)
    return {"status": "ok", "id": str(result.inserted_id)}


def crear_usuarios_iniciales() -> list:
    """
    Crea 5 usuarios iniciales con contraseñas aleatorias si la colección
    'usuarios' está vacía. Retorna las credenciales en texto plano (única
    vez posible, ya que el hash bcrypt no es reversible) para que el
    administrador las distribuya de forma segura.
    """
    db = get_db()
    if db.usuarios.count_documents({}) > 0:
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
    Valida credenciales contra MongoDB.
    Bloquea la cuenta por BLOQUEO_MINUTOS tras MAX_INTENTOS_FALLIDOS intentos.
    Retorna {status:'ok', usuario:{...}} o {status:'error', mensaje:str}.
    """
    db = get_db()
    username = (username or "").strip().lower()
    if not username or not password:
        return {"status": "error", "mensaje": "Usuario y contraseña requeridos"}

    usuario = db.usuarios.find_one({"username": username})
    error_generico = {"status": "error", "mensaje": "Usuario o contraseña incorrectos"}
    if not usuario or not usuario.get("activo", True):
        return error_generico

    ahora = datetime.utcnow()
    bloqueado_hasta = usuario.get("bloqueado_hasta")
    if bloqueado_hasta and bloqueado_hasta > ahora:
        minutos = max(1, int((bloqueado_hasta - ahora).total_seconds() // 60) + 1)
        return {"status": "error", "mensaje": f"Cuenta bloqueada temporalmente. Intenta en {minutos} min."}

    if not _verificar_password(password, usuario["password_hash"]):
        intentos = usuario.get("intentos_fallidos", 0) + 1
        update = {"intentos_fallidos": intentos}
        if intentos >= MAX_INTENTOS_FALLIDOS:
            update["bloqueado_hasta"]   = ahora + timedelta(minutes=BLOQUEO_MINUTOS)
            update["intentos_fallidos"] = 0
        db.usuarios.update_one({"_id": usuario["_id"]}, {"$set": update})
        return error_generico

    db.usuarios.update_one(
        {"_id": usuario["_id"]},
        {"$set": {"intentos_fallidos": 0, "bloqueado_hasta": None, "ultimo_login": ahora}},
    )
    return {
        "status": "ok",
        "usuario": {
            "id":        str(usuario["_id"]),
            "username":  usuario["username"],
            "nombre":    usuario.get("nombre", usuario["username"]),
            "es_admin":  usuario.get("es_admin", False),
            "rol":       usuario.get("rol") or ROL_LOGISTICA,
            "chofer_id": str(usuario["chofer_id"]) if usuario.get("chofer_id") else None,
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
    registrado en la colección `choferes`, vinculando el usuario creado de
    vuelta al chofer (`choferes.usuario_id`). La contraseña solo se devuelve
    en esta llamada (no es recuperable después, igual que crear_usuarios_iniciales).
    """
    db = get_db()
    try:
        oid = ObjectId(chofer_id)
    except (InvalidId, TypeError):
        return {"status": "error", "mensaje": "ID de chofer inválido"}

    chofer = db.choferes.find_one({"_id": oid})
    if not chofer:
        return {"status": "error", "mensaje": "Chofer no encontrado"}
    if chofer.get("usuario_id"):
        return {"status": "error", "mensaje": "Este chofer ya tiene acceso generado"}

    nombre = chofer.get("nombre", "")
    base_username = _username_desde_nombre(nombre)
    username = base_username
    sufijo = 1
    while db.usuarios.find_one({"username": username}):
        sufijo += 1
        username = f"{base_username}{sufijo}"

    password  = generar_password_segura()
    resultado = crear_usuario(username, password, nombre, es_admin=False, rol=ROL_CONDUCTOR, chofer_id=oid)
    if resultado.get("status") != "ok":
        return resultado

    db.choferes.update_one({"_id": oid}, {"$set": {"usuario_id": ObjectId(resultado["id"])}})
    return {"status": "ok", "username": username, "password": password, "nombre": nombre}


def _serializar_usuario(doc: dict) -> dict:
    """Versión segura de un usuario para listarlo (nunca incluye password_hash)."""
    return {
        "id":             str(doc["_id"]),
        "username":       doc.get("username", ""),
        "nombre":         doc.get("nombre", ""),
        "rol":            doc.get("rol") or ROL_LOGISTICA,
        "es_admin":       bool(doc.get("es_admin", False)),
        "activo":         doc.get("activo", True),
        "chofer_id":      str(doc["chofer_id"]) if doc.get("chofer_id") else None,
        "ultimo_login":   doc["ultimo_login"].isoformat() if doc.get("ultimo_login") else None,
        "creado_en":      doc["creado_en"].isoformat() if doc.get("creado_en") else None,
    }


def listar_usuarios() -> list:
    """Todos los usuarios del sistema (Logística y Conductor), sin datos sensibles."""
    db = get_db()
    docs = db.usuarios.find({}).sort("username", 1)
    return [_serializar_usuario(d) for d in docs]


def obtener_usuario(usuario_id: str) -> dict | None:
    try:
        oid = ObjectId(usuario_id)
    except (InvalidId, TypeError):
        return None
    db  = get_db()
    doc = db.usuarios.find_one({"_id": oid})
    return _serializar_usuario(doc) if doc else None


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


def editar_usuario(usuario_id: str, datos: dict, usuario_actual_id: str | None = None) -> dict:
    """
    Modifica username/nombre/es_admin y, opcionalmente, la contraseña
    (solo si se envía no vacía) de un usuario existente.
    """
    try:
        oid = ObjectId(usuario_id)
    except (InvalidId, TypeError):
        return {"status": "error", "mensaje": "ID inválido"}

    db  = get_db()
    doc = db.usuarios.find_one({"_id": oid})
    if not doc:
        return {"status": "error", "mensaje": "Usuario no encontrado"}

    cambios: dict = {}

    nuevo_username = (datos.get("username") or "").strip().lower()
    if nuevo_username and nuevo_username != doc.get("username"):
        if db.usuarios.find_one({"username": nuevo_username, "_id": {"$ne": oid}}):
            return {"status": "error", "mensaje": f"El usuario '{nuevo_username}' ya existe"}
        cambios["username"] = nuevo_username

    if "nombre" in datos and (datos.get("nombre") or "").strip():
        cambios["nombre"] = datos["nombre"].strip()

    # No permitir que un administrador se quite a sí mismo el rol de admin
    # por accidente desde este mismo formulario (evita quedar sin acceso).
    if "es_admin" in datos:
        es_admin_nuevo = bool(datos.get("es_admin"))
        if doc.get("es_admin") and not es_admin_nuevo and str(oid) == str(usuario_actual_id):
            return {"status": "error", "mensaje": "No puedes quitarte el rol de administrador a ti mismo"}
        cambios["es_admin"] = es_admin_nuevo

    password_nueva = datos.get("password") or ""
    if password_nueva:
        if len(password_nueva) < 6:
            return {"status": "error", "mensaje": "La contraseña debe tener al menos 6 caracteres"}
        cambios["password_hash"] = _hash_password(password_nueva)

    if not cambios:
        return {"status": "ok"}

    db.usuarios.update_one({"_id": oid}, {"$set": cambios})
    return {"status": "ok"}


def eliminar_usuario(usuario_id: str, usuario_actual_id: str | None = None) -> dict:
    """
    Elimina un usuario. Si era de rol conductor, libera el vínculo en su
    chofer (`choferes.usuario_id`) para que se le pueda generar un acceso
    nuevo más adelante. Protege contra auto-eliminación y contra dejar el
    sistema sin ningún administrador activo.
    """
    try:
        oid = ObjectId(usuario_id)
    except (InvalidId, TypeError):
        return {"status": "error", "mensaje": "ID inválido"}

    if str(oid) == str(usuario_actual_id):
        return {"status": "error", "mensaje": "No puedes eliminar tu propia cuenta"}

    db  = get_db()
    doc = db.usuarios.find_one({"_id": oid})
    if not doc:
        return {"status": "error", "mensaje": "Usuario no encontrado"}

    if doc.get("es_admin"):
        otros_admins = db.usuarios.count_documents({"es_admin": True, "_id": {"$ne": oid}})
        if otros_admins == 0:
            return {"status": "error", "mensaje": "No puedes eliminar al último administrador del sistema"}

    if doc.get("rol") == ROL_CONDUCTOR and doc.get("chofer_id"):
        db.choferes.update_one({"_id": doc["chofer_id"]}, {"$unset": {"usuario_id": ""}})

    db.usuarios.delete_one({"_id": oid})
    return {"status": "ok"}


def obtener_zoom(usuario_id: str) -> int:
    """Devuelve el nivel de zoom de página guardado para el usuario (o el default)."""
    try:
        oid = ObjectId(usuario_id)
    except (InvalidId, TypeError):
        return ZOOM_DEFAULT
    db = get_db()
    usuario = db.usuarios.find_one({"_id": oid}, {"zoom_pct": 1})
    if not usuario:
        return ZOOM_DEFAULT
    zoom = usuario.get("zoom_pct")
    if isinstance(zoom, int) and ZOOM_MIN <= zoom <= ZOOM_MAX:
        return zoom
    return ZOOM_DEFAULT


def guardar_zoom(usuario_id: str, zoom) -> dict:
    """Persiste el nivel de zoom elegido por el usuario, para que se mantenga entre sesiones."""
    try:
        oid = ObjectId(usuario_id)
    except (InvalidId, TypeError):
        return {"status": "error", "mensaje": "Sesión inválida"}
    try:
        zoom_int = int(zoom)
    except (TypeError, ValueError):
        return {"status": "error", "mensaje": "Zoom inválido"}

    zoom_int = max(ZOOM_MIN, min(ZOOM_MAX, zoom_int))
    db = get_db()
    db.usuarios.update_one({"_id": oid}, {"$set": {"zoom_pct": zoom_int}})
    return {"status": "ok", "zoom": zoom_int}
