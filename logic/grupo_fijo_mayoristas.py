"""
logic/grupo_fijo_mayoristas.py

Pin de ASIGNACION para mayoristas: "este cliente viaja con la ruta de este
grupo de la plantilla, sea cual sea la que le quede mas cerca esa semana".

Es el hermano de logic/ancla_mayoristas.py (que fija el ORDEN de visita, no la
ruta) y no tiene nada que ver con la capa de zona: NO toca
`plantilla_poblacion_zona`, `plantilla_zona_mayorista` ni los centroides de
`enganche_zona.centroides_desde_clientes()`, asi que no puede cambiarle la
ruta a ningun cliente que no este en la tabla.

Por que asi y no reactivando la capa de zona (caso real 2026-09-04):
BB4145_FARMA PRONTO JALAPA (cliente 555) cae en la ruta de Tuxtepec aunque
sus coordenadas esten a ~55 m de la sucursal 35 (San Felipe Jalapa de Diaz).
La capa de zona -- que existe justo para esto -- lleva inactiva desde el
2026-08-12, y reactivarla es TODO-O-NADA: `centroides_desde_clientes()` arma
un centroide por zona ACTIVADA y `resolver_zona_cliente()` engancha por
GEOGRAFIA a la zona activada mas cercana dentro de 60 km, asi que activar una
sola zona la vuelve la mas cercana para media region (medido: 37 clientes
ajenos se engancharon a JALAPA DE DIAZ y la sobrecarga disperso a los de
Jalapa hacia Tuxtepec, Tres Valles e Isla). Este pin resuelve el caso puntual
sin ese efecto de bola de nieve.

Contrato todo-o-nada, como el resto de los pines del proyecto: el pin solo
manda si el grupo fijado TIENE ruta esta semana. Si ese grupo no viaja, el
cliente sigue el camino normal -- nunca se inventa un destino.
"""
from sqlalchemy import select

from db import get_db, get_table


def _int(valor) -> "int | None":
    try:
        return int(str(valor).split(".")[0])
    except (TypeError, ValueError, IndexError):
        return None


def obtener_grupo_fijo(db=None) -> dict:
    """
    Lee grupo_fijo_mayoristas y arma {id_cliente: grupo}.

    Se llama UNA sola vez por corrida (antes del bucle de mayoristas), no por
    cliente -- es una tabla chica de referencia. Sin `db` abre la conexion del
    request actual.

    Nunca propaga una excepcion: si la tabla no existe todavia (base sin
    migrar, ver scripts/crear_grupo_fijo_mayoristas.py) o no hay contexto de
    aplicacion, devuelve {} y la asignacion sigue como siempre. Un pin ausente
    degrada al comportamiento anterior; no debe tumbar la generacion de rutas.
    """
    try:
        if db is None:
            db = get_db()
        t = get_table("grupo_fijo_mayoristas")
        filas = db.execute(select(t.c.id_cliente, t.c.grupo)).mappings().all()
    except Exception as e:  # noqa: BLE001
        print(f"[mayoristas] pines de grupo no disponibles, la asignacion sigue "
              f"como siempre: {type(e).__name__}: {e}")
        return {}
    salida = {}
    for f in filas:
        idc, grupo = _int(f["id_cliente"]), _int(f["grupo"])
        if idc is not None and grupo is not None:
            salida[idc] = grupo
    return salida


def rid_por_grupo(rutas_sucursales: dict, grupo_de_num_tienda: dict) -> dict:
    """
    {grupo: ruta_id} -- la ruta de esta semana que lleva las sucursales de cada
    grupo. Si un grupo aparece en mas de una ruta gana la primera en orden de
    ruta_id (determinista, no depende del orden en que SQL devolvio las filas).

    `grupo_de_num_tienda`: {num_tienda: grupo} de la plantilla vigente.
    """
    salida = {}
    for rid in sorted(rutas_sucursales, key=str):
        for s in rutas_sucursales[rid] or []:
            nt = _int(s.get("num_tienda"))
            if nt is None:
                continue
            grupo = grupo_de_num_tienda.get(nt)
            if grupo is not None and grupo not in salida:
                salida[grupo] = rid
    return salida


def ruta_fija(mayorista: dict, grupo_fijo: dict, rid_de_grupo: dict) -> "str | None":
    """
    Ruta fijada para este mayorista, o None si no aplica y debe seguir el
    camino normal (sin pin, o el grupo fijado no viaja esta semana).
    """
    if not grupo_fijo or not rid_de_grupo:
        return None
    idc = _int(mayorista.get("id_cliente"))
    if idc is None:
        return None
    grupo = grupo_fijo.get(idc)
    if grupo is None:
        return None
    return rid_de_grupo.get(grupo)


def grupo_de_num_tienda_vigente() -> dict:
    """{num_tienda: grupo} de la plantilla vigente; {} si no se puede leer."""
    try:
        from logic.plantilla_canonica import obtener_grupos
        salida = {}
        for g in obtener_grupos():
            for nt in g.get("sucursales", []):
                n = _int(nt)
                if n is not None:
                    salida[n] = _int(g["grupo"])
        return salida
    except Exception as e:  # noqa: BLE001
        print(f"[mayoristas] no se pudo leer la plantilla para los pines de "
              f"grupo: {type(e).__name__}: {e}")
        return {}
