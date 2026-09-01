"""
db.py
Conexión a SQL Server vía SQLAlchemy Core.

El Engine y el MetaData reflejado (las tablas de la base leídas una sola
vez al arrancar la app, sin declarar ~40 objetos Table a mano) se guardan
en app.extensions (scope de aplicación) para reutilizar el connection
pool interno de SQLAlchemy entre requests — mismo patrón que antes se
usaba para el MongoClient. Crear un Engine nuevo por request destruiría y
recrearía el pool en cada petición.

Cada conexión abierta por get_db() usa aislamiento AUTOCOMMIT: cada
sentencia se confirma de inmediato, igual que en MongoDB, donde cada
find/update_one/insert_one es una operación atómica independiente.
Esto es correcto para la gran mayoría del código (una sola sentencia
por operación), pero varias operaciones de este proyecto hacen un
"reemplazo completo" en dos pasos — DELETE de las filas viejas seguido
de INSERT de las nuevas — para representar en tablas normalizadas lo
que en Mongo era un único documento reemplazado con un solo $set
atómico (p. ej. `extraccion_desglose`, y en el módulo de Modificación:
`crear_ruta_manual`, `guardar_modificacion`, etc.). Bajo AUTOCOMMIT
esos dos pasos son dos transacciones independientes: existe una
ventana en la que otro request vería la tabla vacía a medias, y si el
proceso truena entre el DELETE y el INSERT, los datos quedan a medias
sin rollback posible.

Para esos casos, usa transaccion() en vez de get_db(): abre una
conexión propia (no la de flask.g) dentro de una transacción real, que
hace commit automático si el bloque `with` termina sin excepción, o
rollback automático si lanza. Todas las lecturas y escrituras de esa
unidad atómica deben pasar por la conexión que entrega el `with`, no
por get_db() (son conexiones distintas: una no ve los cambios sin
confirmar de la otra, por diseño).
"""
from contextlib import contextmanager
from urllib.parse import quote_plus

from flask import current_app, g
from sqlalchemy import create_engine, MetaData
from sqlalchemy.engine import Engine

def _build_connection_url(app) -> str:
    driver   = app.config["SQL_DRIVER"]
    server   = app.config["SQL_SERVER"]
    database = app.config["SQL_DATABASE"]
    auth     = app.config["SQL_AUTH"]
    driver_q = quote_plus(driver)

    trust_cert = "yes" if app.config["SQL_TRUST_SERVER_CERTIFICATE"] else "no"
    extra      = f"&TrustServerCertificate={trust_cert}"

    if auth == "windows":
        return (
            f"mssql+pyodbc://@{server}/{database}"
            f"?driver={driver_q}&trusted_connection=yes{extra}"
        )

    user     = quote_plus(app.config["SQL_USER"])
    password = quote_plus(app.config["SQL_PASSWORD"])
    return (
        f"mssql+pyodbc://{user}:{password}@{server}/{database}"
        f"?driver={driver_q}{extra}"
    )


def _engine_and_metadata() -> tuple[Engine, MetaData]:
    app = current_app._get_current_object()
    if "sql_engine" not in app.extensions:
        # autocommit=True al nivel del DBAPI (pyodbc, default False): sin esto,
        # pool_pre_ping y metadata.reflect() abren una transacción implícita en
        # la conexión física (SELECT bajo autobegin de SQLAlchemy) que nunca se
        # confirma ni revierte — queda "open_transaction_count=1" en el server
        # para siempre en esa conexión del pool, y cada request que la reutiliza
        # (incluido get_db() con isolation_level=AUTOCOMMIT, que solo cambia
        # cómo empiezan sentencias NUEVAS, no cierra una transacción ya abierta)
        # sigue escribiendo dentro de esa misma transacción huérfana. Con el
        # tiempo eso es lo que volvía lentísimo (7+ min) y luego tronaba con
        # 500 el guardado en Modificación. Verificado con sys.dm_exec_sessions:
        # sin este flag, reflect(bind=engine) por sí solo ya deja la conexión
        # en open_transaction_count=1; con el flag, queda en 0. transaccion()
        # (engine.begin()) sigue commiteando/revirtiendo real igual que antes.
        engine   = create_engine(_build_connection_url(app), pool_pre_ping=True,
                                  connect_args={"autocommit": True})
        metadata = MetaData()
        metadata.reflect(bind=engine)
        app.extensions["sql_engine"]   = engine
        app.extensions["sql_metadata"] = metadata
    return app.extensions["sql_engine"], app.extensions["sql_metadata"]


def get_engine() -> Engine:
    """Devuelve el Engine de SQLAlchemy (scope de aplicación, un solo pool)."""
    engine, _ = _engine_and_metadata()
    return engine


def get_metadata() -> MetaData:
    """Devuelve el MetaData reflejado con todas las tablas de icgdb."""
    _, metadata = _engine_and_metadata()
    return metadata


def get_table(nombre: str):
    """Devuelve el objeto Table reflejado para `nombre` — equivalente a db['nombre'] en Mongo."""
    return get_metadata().tables[nombre]


def get_db():
    """
    Devuelve la Connection de SQLAlchemy del request actual.

    Se abre una sola vez por request (guardada en flask.g) y se libera al
    pool en close_db(). Aislamiento AUTOCOMMIT (vía connect_args del engine,
    ver _engine_and_metadata): no hace falta llamar a conn.commit() tras cada
    insert/update/delete.

    Ojo: NO fijar aquí `.execution_options(isolation_level="AUTOCOMMIT")` —
    verificado con sys.dm_exec_sessions que, en esta combinación SQLAlchemy
    2.0/pyodbc/mssql, pedir ese isolation_level a nivel de Connection sobre
    una conexión que YA es autocommit por connect_args deja
    open_transaction_count=1 (transacción huérfana, nunca commiteada) al
    cerrar la conexión — se acumula una por cada conexión del pool que pasa
    por aquí y es la causa raíz confirmada de que "Guardar y seguir" en
    Modificación tardara minutos y terminara en 500. Con el autocommit ya
    puesto en connect_args, este override es redundante y además el que
    rompe el cierre limpio.
    """
    if "sql_connection" not in g:
        engine, _ = _engine_and_metadata()
        g.sql_connection = engine.connect()
    return g.sql_connection


def close_db(e=None):
    """Hook de teardown registrado en app.py: libera la conexión del request al pool."""
    conn = g.pop("sql_connection", None)
    if conn is not None:
        conn.close()


@contextmanager
def transaccion():
    """
    Abre una transacción real (BEGIN/COMMIT/ROLLBACK) para operaciones que
    deben ser atómicas — típicamente un reemplazo "borrar todo e insertar
    de nuevo" que en Mongo era un único $set atómico.

    Usa una conexión propia del Engine (no la conexión AUTOCOMMIT de
    get_db()), así que no interfiere con otras queries del mismo request
    hechas fuera de este bloque. Commit automático si el `with` termina
    sin excepción; rollback automático si lanza.

    Uso:
        with transaccion() as conn:
            conn.execute(delete(tabla).where(...))
            conn.execute(insert(tabla), filas_nuevas)
        # ya quedó confirmado aquí; si algo lanzó adentro, se revirtió todo

    Importante: todas las lecturas/escrituras de esa unidad atómica deben
    hacerse con el `conn` que entrega este context manager, no con
    get_db() — son conexiones distintas.
    """
    with get_engine().begin() as conn:
        yield conn
