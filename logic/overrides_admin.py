"""
logic/overrides_admin.py

Registro auditable de parches puntuales (overrides) sobre la plantilla
canonica y las tablas de pines de mayoristas. Ver
scripts/crear_overrides_auditoria.py para el esquema de
overrides_auditoria y scripts/listar_overrides.py para el diagnostico del
estado actual.

No reemplaza los scripts existentes (mover_dia_preferido_grupo_*.py,
afinidad_grupo27_tb1_tb4.py, etc.) -- son historial valido, no se tocan.
Es el punto de entrada para el PROXIMO override: cada escritura queda con
tipo/clave/motivo/fecha en una tabla consultable, en vez de solo en el
nombre y el docstring de un archivo.

La tabla es append-only: nunca se actualiza ni se borra una fila, aunque
`clave` se repita -- cada cambio es un evento nuevo.
"""
from datetime import datetime

from sqlalchemy import select, insert

from db import get_db, get_table, transaccion

TIPOS_VALIDOS = {
    "dia_preferido", "afinidad", "unidades_excluidas",
    "orden_fijo", "ancla_mayorista", "grupo_fijo_mayorista", "zona_partida",
}


def registrar_override(tipo: str, clave: str, valor_anterior, valor_nuevo,
                        motivo: str, aplicado_por: str = None, conn=None) -> None:
    """
    Inserta una fila en overrides_auditoria.

    tipo: uno de TIPOS_VALIDOS.
    clave: que se toco, ej. "grupo:27", "cliente:555", "zona:5".
    valor_anterior/valor_nuevo: se guardan como texto (str()); cada tipo de
        override tiene su propio formato (una lista con "|", un solo dia,
        etc.) -- no se normaliza aqui, igual que unidades_afines/
        unidades_excluidas en plantilla_grupo ya son texto plano.
    motivo: obligatorio -- es la pieza que hoy solo vive en docstrings.
    conn: conexion de una transaccion() ya abierta, para insertar la
        auditoria en la MISMA transaccion que el UPDATE real que dispara el
        override. Si no se da, abre su propia transaccion.
    """
    if tipo not in TIPOS_VALIDOS:
        raise ValueError(f"tipo de override desconocido: {tipo!r} (validos: {sorted(TIPOS_VALIDOS)})")
    if not motivo or not motivo.strip():
        raise ValueError("motivo es obligatorio")

    valores = dict(
        tipo=tipo, clave=clave,
        valor_anterior=str(valor_anterior) if valor_anterior is not None else None,
        valor_nuevo=str(valor_nuevo) if valor_nuevo is not None else None,
        motivo=motivo.strip(), aplicado_por=aplicado_por,
        aplicado_en=datetime.now(),
    )
    t = get_table("overrides_auditoria")
    if conn is not None:
        conn.execute(insert(t).values(**valores))
        return
    with transaccion() as c:
        c.execute(insert(t).values(**valores))


def historial(clave: str = None, tipo: str = None, db=None) -> list:
    """
    Lista de overrides aplicados, mas reciente primero. Sin filtros trae
    todo el historial.

    Nunca propaga una excepcion: sin contexto de aplicacion o si la tabla
    no existe todavia (base sin migrar, ver
    scripts/crear_overrides_auditoria.py), devuelve [] -- mismo contrato de
    degradacion que ancla_mayoristas/grupo_fijo_mayoristas.
    """
    try:
        if db is None:
            db = get_db()
        t = get_table("overrides_auditoria")
        stmt = select(t)
        if clave is not None:
            stmt = stmt.where(t.c.clave == clave)
        if tipo is not None:
            stmt = stmt.where(t.c.tipo == tipo)
        filas = [dict(f) for f in db.execute(stmt).mappings().all()]
    except Exception as e:  # noqa: BLE001
        print(f"[overrides_admin] historial no disponible: {type(e).__name__}: {e}")
        return []
    return sorted(filas, key=lambda f: f["aplicado_en"], reverse=True)
