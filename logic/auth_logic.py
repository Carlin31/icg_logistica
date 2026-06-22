"""
logic/auth_logic.py
Autenticación de usuarios contra MongoDB: hash de contraseñas con bcrypt
(incluye salt automático), validación de credenciales y bloqueo temporal
de cuentas tras intentos fallidos repetidos.
"""
import secrets
import string
from datetime import datetime, timedelta

import bcrypt

from db import get_db

MAX_INTENTOS_FALLIDOS = 5
BLOQUEO_MINUTOS = 15


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


def crear_usuario(username: str, password: str, nombre: str = "", es_admin: bool = False) -> dict:
    db = get_db()
    username = username.strip().lower()
    if db.usuarios.find_one({"username": username}):
        return {"status": "error", "mensaje": f"El usuario '{username}' ya existe"}
    doc = {
        "username":          username,
        "password_hash":     _hash_password(password),
        "nombre":            nombre or username,
        "es_admin":          es_admin,
        "activo":            True,
        "intentos_fallidos": 0,
        "bloqueado_hasta":   None,
        "ultimo_login":      None,
        "creado_en":         datetime.utcnow(),
    }
    db.usuarios.insert_one(doc)
    return {"status": "ok"}


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
            "id":       str(usuario["_id"]),
            "username": usuario["username"],
            "nombre":   usuario.get("nombre", usuario["username"]),
            "es_admin": usuario.get("es_admin", False),
        },
    }
