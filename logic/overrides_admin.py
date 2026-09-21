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

from sqlalchemy import select, insert, update

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


# ── Operaciones concretas por tipo de override ─────────────────────────────
#
# A diferencia de registrar_override/historial (que nunca lanzan, para no
# tumbar la generacion de rutas si algo falta), estas funciones SI lanzan
# ValueError ante datos invalidos: son operaciones de administracion que un
# humano dispara a proposito, deben fallar ruidosamente para que se note de
# inmediato, igual que los ASSERT de verificacion al final de los scripts
# mover_dia_preferido_grupo_*.py / afinidad_grupo27_tb1_tb4.py que generalizan.


def fijar_dia_preferido(grupo: int, dia: str, motivo: str,
                         aplicado_por: str = None, dry_run: bool = False) -> dict:
    """
    Generaliza scripts/mover_dia_preferido_grupo_*.py: cambia cual dia
    admisible de `grupo` es el preferido (es_canonico). NO agrega un dia
    admisible nuevo -- `dia` debe ya estar entre los admisibles del grupo;
    agregar uno es una operacion distinta (INSERT de fila nueva, no UPDATE).
    """
    db = get_db()
    tgd = get_table("plantilla_grupo_dia")
    filas = db.execute(
        select(tgd).where(tgd.c.grupo == grupo, tgd.c.vigente == True)
    ).mappings().all()
    if not filas:
        raise ValueError(f"grupo {grupo} no tiene dias admisibles en la plantilla vigente")

    admisibles = {f["dia"] for f in filas}
    if dia not in admisibles:
        raise ValueError(
            f"{dia} no es un dia admisible de grupo {grupo} (admisibles: {sorted(admisibles)}) "
            f"-- agregar un dia admisible nuevo es una operacion distinta, no soportada aqui"
        )

    actual = next((f["dia"] for f in filas if f["es_canonico"]), None)
    resultado = dict(grupo=grupo, antes=actual, despues=dia)
    if dry_run:
        return resultado
    if actual == dia:
        resultado["sin_cambios"] = True
        return resultado

    with transaccion() as conn:
        conn.execute(update(tgd).where(
            tgd.c.grupo == grupo, tgd.c.vigente == True, tgd.c.es_canonico == True
        ).values(es_canonico=False))
        conn.execute(update(tgd).where(
            tgd.c.grupo == grupo, tgd.c.vigente == True, tgd.c.dia == dia
        ).values(es_canonico=True))
        registrar_override("dia_preferido", f"grupo:{grupo}", actual, dia,
                            motivo, aplicado_por, conn=conn)
    return resultado


def fijar_afinidad(grupo: int, afinidad: str, motivo: str,
                    aplicado_por: str = None, dry_run: bool = False) -> dict:
    """
    Generaliza scripts/afinidad_grupo27_tb1_tb4.py: fija
    plantilla_grupo.unidades_afines para `grupo`. `afinidad` es texto plano
    ya en el formato esperado por el motor (ej. "T 25:5 | T 23:4 | T 20:3").
    """
    db = get_db()
    tg = get_table("plantilla_grupo")
    fila = db.execute(
        select(tg.c.unidades_afines).where(tg.c.grupo == grupo, tg.c.vigente == True)
    ).mappings().first()
    if fila is None:
        raise ValueError(f"grupo {grupo} no existe en la plantilla vigente")

    antes = fila["unidades_afines"]
    resultado = dict(grupo=grupo, antes=antes, despues=afinidad)
    if dry_run:
        return resultado

    with transaccion() as conn:
        conn.execute(update(tg).where(
            tg.c.grupo == grupo, tg.c.vigente == True
        ).values(unidades_afines=afinidad))
        registrar_override("afinidad", f"grupo:{grupo}", antes, afinidad,
                            motivo, aplicado_por, conn=conn)
    return resultado


def fijar_unidades_excluidas(grupo: int, unidades, motivo: str,
                              aplicado_por: str = None, dry_run: bool = False) -> dict:
    """
    Fija plantilla_grupo.unidades_excluidas para `grupo`. `unidades` puede
    ser una lista (se junta con "|", mismo formato que usa
    logic/plantilla_canonica.py) o ya el texto con "|".
    """
    valor = "|".join(unidades) if isinstance(unidades, (list, tuple)) else unidades

    db = get_db()
    tg = get_table("plantilla_grupo")
    fila = db.execute(
        select(tg.c.unidades_excluidas).where(tg.c.grupo == grupo, tg.c.vigente == True)
    ).mappings().first()
    if fila is None:
        raise ValueError(f"grupo {grupo} no existe en la plantilla vigente")

    antes = fila["unidades_excluidas"]
    resultado = dict(grupo=grupo, antes=antes, despues=valor)
    if dry_run:
        return resultado

    with transaccion() as conn:
        conn.execute(update(tg).where(
            tg.c.grupo == grupo, tg.c.vigente == True
        ).values(unidades_excluidas=valor))
        registrar_override("unidades_excluidas", f"grupo:{grupo}", antes, valor,
                            motivo, aplicado_por, conn=conn)
    return resultado


def crear_ancla_mayorista(id_cliente: int, num_tienda: int, motivo: str,
                           nota: str = None, aplicado_por: str = None,
                           dry_run: bool = False) -> dict:
    """
    Generaliza scripts/cargar_ancla_mayoristas.py para un solo cliente:
    fija "este mayorista se visita inmediatamente despues de esta
    sucursal" (ver logic/ancla_mayoristas.py). Upsert -- PK es id_cliente,
    un cliente solo puede tener un ancla a la vez.
    """
    db = get_db()
    t = get_table("ancla_mayoristas")
    fila = db.execute(
        select(t.c.num_tienda).where(t.c.id_cliente == id_cliente)
    ).mappings().first()
    antes = fila["num_tienda"] if fila else None
    resultado = dict(id_cliente=id_cliente, antes=antes, despues=num_tienda)
    if dry_run:
        return resultado

    with transaccion() as conn:
        conn.execute(t.delete().where(t.c.id_cliente == id_cliente))
        conn.execute(t.insert().values(id_cliente=id_cliente, num_tienda=num_tienda, nota=nota))
        registrar_override("ancla_mayorista", f"cliente:{id_cliente}", antes, num_tienda,
                            motivo, aplicado_por, conn=conn)
    return resultado


def fijar_grupo_mayorista(id_cliente: int, grupo: int, motivo: str,
                           nota: str = None, aplicado_por: str = None,
                           dry_run: bool = False) -> dict:
    """
    Generaliza scripts/cargar_grupo_fijo_mayoristas.py para un solo
    cliente: fija "este mayorista viaja con la ruta de este grupo" (ver
    logic/grupo_fijo_mayoristas.py). Upsert -- PK es id_cliente.
    """
    db = get_db()
    t = get_table("grupo_fijo_mayoristas")
    fila = db.execute(
        select(t.c.grupo).where(t.c.id_cliente == id_cliente)
    ).mappings().first()
    antes = fila["grupo"] if fila else None
    resultado = dict(id_cliente=id_cliente, antes=antes, despues=grupo)
    if dry_run:
        return resultado

    with transaccion() as conn:
        conn.execute(t.delete().where(t.c.id_cliente == id_cliente))
        conn.execute(t.insert().values(id_cliente=id_cliente, grupo=grupo, nota=nota))
        registrar_override("grupo_fijo_mayorista", f"cliente:{id_cliente}", antes, grupo,
                            motivo, aplicado_por, conn=conn)
    return resultado


def cargar_orden_fijo_regla(nombre_regla: str, entradas: list, motivo: str,
                             aplicado_por: str = None, dry_run: bool = False) -> dict:
    """
    Generaliza scripts/cargar_orden_fijo.py para una sola regla: reemplazo
    completo (borra las filas actuales de `nombre_regla` e inserta las
    nuevas, nunca acumula). `entradas`: [(num_tienda, posicion), ...].

    A diferencia del loader original, valida colision NO solo dentro del
    lote sino tambien CONTRA cualquier otra regla ya presente en la tabla.
    Ese hueco -- validar solo dentro del CSV que se estaba cargando, nunca
    contra lo que ya habia en la base -- fue la causa real de que
    tuxtepec_f350_2 quedara chocando en silencio contra zona_5/zona_25
    (encontrado con scripts/listar_overrides.py, 2026-09-21): al cargar las
    reglas nuevas nadie valido contra la regla vieja porque no estaba en el
    mismo CSV.
    """
    if not entradas:
        raise ValueError("entradas vacio, nada que cargar")

    vistos = {}
    for nt, pos in entradas:
        if nt in vistos:
            raise ValueError(f"num_tienda {nt} aparece dos veces en el mismo lote")
        vistos[nt] = pos

    db = get_db()
    t = get_table("orden_fijo_paradas")
    filas_actuales = db.execute(select(t)).mappings().all()

    conflictos = {}
    for f in filas_actuales:
        nt = int(f["num_tienda"])
        if nt in vistos and f["nombre_regla"] != nombre_regla:
            conflictos.setdefault(f["nombre_regla"], []).append((nt, f["posicion"]))
    if conflictos:
        detalle = "; ".join(f"{regla}: {paradas}" for regla, paradas in conflictos.items())
        raise ValueError(
            f"colision con otra(s) regla(s) ya activas -- {detalle}. "
            f"Revisa con scripts/listar_overrides.py antes de forzar."
        )

    antes = sorted(
        (int(f["num_tienda"]), f["posicion"]) for f in filas_actuales
        if f["nombre_regla"] == nombre_regla
    )
    despues = sorted(entradas)
    resultado = dict(regla=nombre_regla, antes=antes, despues=despues)
    if dry_run:
        return resultado

    with transaccion() as conn:
        conn.execute(t.delete().where(t.c.nombre_regla == nombre_regla))
        conn.execute(t.insert(), [
            {"nombre_regla": nombre_regla, "num_tienda": nt, "posicion": pos}
            for nt, pos in entradas
        ])
        registrar_override("orden_fijo", f"regla:{nombre_regla}", antes, despues,
                            motivo, aplicado_por, conn=conn)
    return resultado
