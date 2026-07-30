"""
crear_usuarios_iniciales.py

Script de un solo uso: crea 5 usuarios iniciales en SQL Server con contraseñas
aleatorias seguras (hasheadas con bcrypt + salt, nunca en texto plano en BD).
Si la tabla 'usuarios' ya tiene filas, no hace nada.

Las contraseñas se imprimen UNA SOLA VEZ en la consola — el hash no es
reversible, así que cópialas y entrégalas de forma segura a cada usuario.

Uso:
    python scripts/crear_usuarios_iniciales.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import create_app
from logic.auth_logic import crear_usuarios_iniciales

app = create_app()

with app.app_context():
    credenciales = crear_usuarios_iniciales()

if not credenciales:
    print("Ya existen usuarios en la base de datos. No se creó ninguno nuevo.")
else:
    print("=" * 60)
    print("USUARIOS CREADOS — guarda estas credenciales ahora.")
    print("No se podrán recuperar después (se almacenan cifradas).")
    print("=" * 60)
    for c in credenciales:
        print(f"  Usuario: {c['username']:<12}  Contraseña: {c['password']}")
    print("=" * 60)
