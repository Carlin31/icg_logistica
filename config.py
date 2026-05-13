import os


class Config:
    # En producción esta variable DEBE estar definida como variable de entorno.
    # El valor de desarrollo es solo un fallback local (nunca usar en Render).
    SECRET_KEY = os.environ.get("SECRET_KEY", "clave-secreta-dev-local")

    # DEBUG activo solo si la variable de entorno lo indica explícitamente.
    # En Render nunca se define FLASK_DEBUG, así que queda en False.
    DEBUG = os.environ.get("FLASK_DEBUG", "false").lower() == "true"

    # ── MongoDB ────────────────────────────────────────────────
    MONGO_URI     = os.getenv("MONGO_URI",     "mongodb://localhost:27017/")
    MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "icg")

    @classmethod
    def validar(cls):
        """Llama a esto en create_app() para detectar config faltante temprano."""
        faltantes = []
        if not cls.MONGO_URI:
            faltantes.append("MONGO_URI")
        if not cls.MONGO_DB_NAME:
            faltantes.append("MONGO_DB_NAME")
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
