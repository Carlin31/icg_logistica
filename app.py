import os
from pathlib import Path
from dotenv import load_dotenv

# Cargar .env antes de leer cualquier variable de entorno.
# Necesario cuando se arranca con `python app.py` o gunicorn
# (Flask CLI lo hace automáticamente, pero gunicorn no).
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    load_dotenv(_env_path)

from datetime import datetime
from flask import Flask, redirect, url_for, session, request, jsonify
from config import Config
from db import close_db

from router.auth_router import auth_bp
from router.menu_router import menu_bp
from router.configuracion_router import configuracion_bp
from router.extraccion_router import extraccion_bp
from router.asignacion_router import asignacion_bp
from router.modificacion_router import modificacion_bp
from router.pdf_router import pdf_bp
from router.conductor_router import conductor_bp
from router.seguimiento_router import seguimiento_bp
from router.usuarios_router import usuarios_bp
from logic.auth_logic import ROL_CONDUCTOR
from logic.conductor_logic import obtener_estado_autorizacion

# Endpoints accesibles sin sesión iniciada.
_ENDPOINTS_PUBLICOS = {"auth.login", "auth.logout", "auth.seleccionar", "static"}


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # Validar variables de entorno críticas al arrancar
    Config.validar()

    # ── Blueprints ─────────────────────────────────────────────────────────
    app.register_blueprint(auth_bp,           url_prefix="/auth")
    app.register_blueprint(menu_bp,           url_prefix="/")
    app.register_blueprint(configuracion_bp,  url_prefix="/configuracion")
    app.register_blueprint(extraccion_bp,     url_prefix="/extraccion")
    app.register_blueprint(asignacion_bp,     url_prefix="/asignacion")
    app.register_blueprint(modificacion_bp,   url_prefix="/modificacion")
    app.register_blueprint(pdf_bp,            url_prefix="/pdf")
    app.register_blueprint(conductor_bp,      url_prefix="/conductor")
    app.register_blueprint(seguimiento_bp,    url_prefix="/seguimiento")
    app.register_blueprint(usuarios_bp,       url_prefix="/usuarios")

    # ── Autenticación: exige sesión de usuario para todo el sistema ────────
    @app.before_request
    def _requerir_login():
        endpoint = request.endpoint
        if endpoint is None or endpoint in _ENDPOINTS_PUBLICOS:
            return None

        accept = request.headers.get("Accept", "")
        es_api = request.is_json or request.method != "GET" or (
            "application/json" in accept and "text/html" not in accept
        )

        if not session.get("usuario_id"):
            # Peticiones AJAX/API: responder 401 JSON en vez de redirigir
            # (un redirect rompería un fetch() que espera JSON).
            if es_api:
                return jsonify({"status": "error", "mensaje": "Sesión no iniciada o expirada"}), 401
            return redirect(url_for("auth.login", next=request.path))

        # Separación por rol: un conductor solo puede usar /conductor/*,
        # y el panel de Logística no es accesible para conductores (ni viceversa).
        es_conductor       = session.get("rol") == ROL_CONDUCTOR
        es_modulo_conductor = endpoint.startswith("conductor.")
        if es_conductor and not es_modulo_conductor:
            if es_api:
                return jsonify({"status": "error", "mensaje": "No autorizado"}), 403
            return redirect(url_for("conductor.index"))
        if not es_conductor and es_modulo_conductor:
            if es_api:
                return jsonify({"status": "error", "mensaje": "No autorizado"}), 403
            return redirect(url_for("menu.index"))

        # Gestión de Usuarios: exclusivo para administradores.
        if endpoint.startswith("usuarios.") and not session.get("usuario_admin"):
            if es_api:
                return jsonify({"status": "error", "mensaje": "No autorizado"}), 403
            return redirect(url_for("menu.index"))
        return None

    # ── Evitar que el navegador cachee páginas autenticadas ─────────────────
    # Sin esto, tras cerrar sesión el botón "Atrás" puede mostrar una copia
    # en caché de una página protegida sin volver a pedírsela al servidor
    # (donde sí se validaría la sesión correctamente). Forzamos que cada
    # navegación revalide con el servidor, que ya redirige al login si no
    # hay sesión activa.
    @app.after_request
    def _sin_cache(response):
        if request.endpoint != "static":
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    # ── Context processor: perfil activo disponible en todos los templates ───
    @app.context_processor
    def inject_perfil_activo():
        slug   = session.get("logistica_slug", "")
        nombre = session.get("logistica_nombre", "")
        lid    = session.get("logistica_id", "")

        perfil_autorizado = False
        if lid and session.get("rol") != ROL_CONDUCTOR:
            try:
                perfil_autorizado = obtener_estado_autorizacion(lid).get("estado") == "autorizado"
            except Exception:
                perfil_autorizado = False

        return {
            "perfil_activo":      bool(lid),
            "perfil_slug":        slug,
            "perfil_nombre":      nombre,
            "perfil_autorizado":  perfil_autorizado,
            "usuario_nombre":     session.get("usuario_nombre", ""),
            "usuario_es_admin":   bool(session.get("usuario_admin")),
            "anio_actual":        datetime.now().year,
        }

    # ── Cierre de conexión MongoDB al final de cada contexto ───────────────
    app.teardown_appcontext(close_db)

    return app


# Expuesto a nivel de módulo para que gunicorn pueda encontrarlo:
#   gunicorn app:app
app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=Config.DEBUG)
