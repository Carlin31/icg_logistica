import os
from datetime import timedelta


class Config:
    # En producción esta variable DEBE estar definida como variable de entorno.
    # El valor de desarrollo es solo un fallback local (nunca usar en Render).
    SECRET_KEY = os.environ.get("SECRET_KEY", "clave-secreta-dev-local")

    # DEBUG activo solo si la variable de entorno lo indica explícitamente.
    # En Render nunca se define FLASK_DEBUG, así que queda en False.
    DEBUG = os.environ.get("FLASK_DEBUG", "false").lower() == "true"

    # ── Sesión / cookies ───────────────────────────────────────
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)
    SESSION_COOKIE_HTTPONLY    = True
    SESSION_COOKIE_SAMESITE    = "Lax"
    # Secure exige HTTPS; en local (DEBUG=True) se desactiva para no bloquear el desarrollo.
    SESSION_COOKIE_SECURE      = not DEBUG

    # ── MongoDB (en desuso — se conserva para rollback, ver db_mongo.py) ──
    MONGO_URI     = os.getenv("MONGO_URI",     "mongodb://localhost:27017/")
    MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "icg")

    # ── SQL Server ─────────────────────────────────────────────
    # SQL_AUTH: "windows" (Trusted_Connection, típico en desarrollo local)
    #           o "sql" (usuario/contraseña, requiere SQL_USER/SQL_PASSWORD).
    SQL_SERVER   = os.getenv("SQL_SERVER",   "localhost")
    SQL_DATABASE = os.getenv("SQL_DATABASE", "icgdb")
    SQL_DRIVER   = os.getenv("SQL_DRIVER",   "ODBC Driver 18 for SQL Server")
    SQL_AUTH     = os.getenv("SQL_AUTH",     "windows").lower()
    SQL_USER     = os.getenv("SQL_USER",     "")
    SQL_PASSWORD = os.getenv("SQL_PASSWORD", "")
    # Driver 18 exige TLS con certificado válido por defecto; el SQL Server
    # local usa un certificado autofirmado, así que se confía en él salvo
    # que se indique lo contrario (producción con certificado real: "false").
    SQL_TRUST_SERVER_CERTIFICATE = os.getenv("SQL_TRUST_SERVER_CERTIFICATE", "true").lower() == "true"

    # ── Groq (LLM para nombres de rutas) ──────────────────────
    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

    @classmethod
    def validar(cls):
        """Llama a esto en create_app() para detectar config faltante temprano."""
        faltantes = []
        if not cls.MONGO_URI:
            faltantes.append("MONGO_URI")
        if not cls.MONGO_DB_NAME:
            faltantes.append("MONGO_DB_NAME")
        if not cls.SQL_SERVER:
            faltantes.append("SQL_SERVER")
        if not cls.SQL_DATABASE:
            faltantes.append("SQL_DATABASE")
        if cls.SQL_AUTH not in ("windows", "sql"):
            faltantes.append('SQL_AUTH (debe ser "windows" o "sql")')
        if cls.SQL_AUTH == "sql" and (not cls.SQL_USER or not cls.SQL_PASSWORD):
            faltantes.append("SQL_USER/SQL_PASSWORD (requeridos cuando SQL_AUTH=sql)")
        if cls.SECRET_KEY == "clave-secreta-dev-local" and not cls.DEBUG:
            # En producción la SECRET_KEY genérica es un riesgo de seguridad.
            import warnings
            warnings.warn(
                "SECRET_KEY no está definida como variable de entorno. "
                "Define una clave segura en Render antes de ir a producción.",
                stacklevel=2,
            )
        if faltantes:
            raise RuntimeError(
                f"Variables de entorno faltantes: {', '.join(faltantes)}. "
                "Revisa las variables de entorno en Render."
            )
