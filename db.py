from pymongo import MongoClient
from flask import current_app


def get_db():
    """
    Devuelve la instancia de la base de datos MongoDB.

    El MongoClient se almacena en app.extensions (scope de aplicación) para
    reutilizar el connection pool interno de PyMongo entre requests.
    Crear un nuevo MongoClient por request destruiría y recrearía el pool
    en cada petición, lo que es muy ineficiente.
    """
    app = current_app._get_current_object()
    if 'mongo_client' not in app.extensions:
        app.extensions['mongo_client'] = MongoClient(app.config["MONGO_URI"])
    return app.extensions['mongo_client'][app.config["MONGO_DB_NAME"]]


def close_db(e=None):
    """
    Hook de teardown registrado en app.py.
    El cliente de nivel de app se cierra solo al apagar el servidor;
    no se cierra por request para preservar el connection pool.
    """
    pass