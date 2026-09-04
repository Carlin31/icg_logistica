"""
logic/ancla_mayoristas.py

Ancla de visita para mayoristas: "este mayorista se visita inmediatamente
DESPUES de esta sucursal, cuando ambos caen en la misma ruta".

Es el equivalente para mayoristas de logic/orden_fijo_paradas.py (que fija
el orden de las SUCURSALES de una ruta). Mismo contrato de todo-o-nada: el
ancla solo gana cuando aplica por completo (todos los miembros del bloque
anclados a la MISMA sucursal, y esa sucursal presente en la ruta); en
cualquier otro caso no se toca nada y el bloque sigue el camino geografico
normal de _insertar_mayoristas_en_bloques.

Por que existe (caso real 2026-09-04, ruta de LUNES de T 17_1 / zona 23):
AA1907_SUPER MAGUITO (Paso de Boca) esta, por carretera, mas cerca de
Piedras Negras (10.3 km) que de Tlalixcoyan (16.3 km), y mas cerca de la
matriz (102.8 km) que Tlalixcoyan (106.6 km) -- asi que tanto el orden por
distancia a la matriz como la insercion mas barata lo colocan ENTRE Piedras
Negras y Tlalixcoyan. Operacion pide visitarlo DESPUES de Tlalixcoyan. No
es un error de calculo que se pueda corregir afinando la heuristica: es una
decision de operacion que contradice a la geometria, y por eso necesita un
pin explicito en vez de un ajuste de heuristica que afectaria a todas las
demas rutas.
"""
from sqlalchemy import select

from db import get_db, get_table


def obtener_anclas_mayoristas(db=None) -> dict:
    """
    Lee ancla_mayoristas y arma {id_cliente: num_tienda}.

    Se llama UNA sola vez por corrida (antes del bucle de rutas), no por
    ruta -- es una tabla chica de referencia, igual que orden_fijo_paradas.
    Sin `db` abre la conexion del request actual (get_db()).

    Nunca propaga una excepcion: si la tabla no existe todavia (base sin
    migrar, ver scripts/crear_ancla_mayoristas.py) o no hay contexto de
    aplicacion, devuelve {} y el motor sigue con el orden geografico de
    siempre. Un ancla ausente degrada al comportamiento anterior; no debe
    tumbar la generacion de rutas.
    """
    try:
        if db is None:
            db = get_db()
        t = get_table("ancla_mayoristas")
        filas = db.execute(select(t.c.id_cliente, t.c.num_tienda)).mappings().all()
    except Exception as e:  # noqa: BLE001
        print(f"[mayoristas] anclas no disponibles, se usa solo el orden "
              f"geografico: {type(e).__name__}: {e}")
        return {}
    return {int(f["id_cliente"]): int(f["num_tienda"]) for f in filas}


def posicion_ancla(paradas: list, bloque: list, anclas: dict) -> "int | None":
    """
    Indice (0-based) EN el que insertar `bloque` para que quede
    inmediatamente despues de su sucursal ancla, o None si el ancla no
    aplica y el bloque debe ubicarse por geografia.

    No aplica (devuelve None) cuando:
      - no hay anclas cargadas, o el bloque esta vacio;
      - algun miembro del bloque no tiene ancla registrada;
      - los miembros apuntan a sucursales ancla distintas entre si;
      - la sucursal ancla no esta en `paradas` (p. ej. cayo en otra ruta
        esta semana).

    Si la sucursal ancla aparece mas de una vez en la ruta (no deberia,
    pero el esquema no lo impide), gana la PRIMERA aparicion.
    """
    if not anclas or not bloque:
        return None

    destinos = set()
    for m in bloque:
        id_cliente = m.get("id_cliente")
        try:
            id_cliente = int(id_cliente)
        except (TypeError, ValueError):
            return None
        if id_cliente not in anclas:
            return None
        destinos.add(anclas[id_cliente])

    if len(destinos) != 1:
        return None
    num_tienda = destinos.pop()

    for i, p in enumerate(paradas):
        if p.get("tipo") != "sucursal":
            continue
        nt = p.get("num_tienda")
        try:
            nt = int(nt)
        except (TypeError, ValueError):
            continue
        if nt == num_tienda:
            return i + 1
    return None
