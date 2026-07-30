import json
from datetime import datetime

from bson import ObjectId
from bson.errors import InvalidId
from sqlalchemy import select, insert, update, delete, or_

from db import get_db, get_table

# ── Helpers ────────────────────────────────────────
ID_CAMPO = {
    "productos":           "clave_sae",
    "sucursales":          "num_tienda",
    "clientes_mayoristas": "id_cliente",
    "productos_bimbo":     "codigo_barra",
}

# Tablas cuyo campo ID se almacena como entero (las demás se tratan como string)
_ID_NUMERICO = {"sucursales", "clientes_mayoristas"}

_LIKE_ESPECIALES = str.maketrans({"\\": "\\\\", "%": "\\%", "_": "\\_", "[": "\\["})


def _like(valor: str) -> str:
    """Escapa comodines de LIKE para que una búsqueda de texto se comporte
    como substring literal (igual que el $regex de Mongo trataba estos
    caracteres como texto normal, no como comodín)."""
    return f"%{valor.translate(_LIKE_ESPECIALES)}%"


def _prefijo_like(valor: str) -> str:
    return f"{valor.translate(_LIKE_ESPECIALES)}%"


def _nuevo_id() -> str:
    return str(ObjectId())


def _id_valido(doc_id: str) -> "str | None":
    """Devuelve el mongo_id validado, o None si el string es inválido."""
    try:
        return str(ObjectId(doc_id))
    except (InvalidId, TypeError):
        return None


def _verificar_id_unico(nombre_tabla: str, datos: dict, excluir_id=None) -> "str | None":
    """
    Devuelve un mensaje de error si el campo ID ya existe en otra fila.
    Retorna None si la validación pasa (incluye cuando el campo es nulo/vacío).
    Soporta IDs numéricos (tablas en _ID_NUMERICO) y string (resto).
    """
    campo = ID_CAMPO.get(nombre_tabla)
    if not campo:
        return None

    valor = datos.get(campo)

    # Permitir nulo, None o string vacío sin validar unicidad
    if valor is None or valor == "" or valor != valor:  # NaN check
        return None

    if nombre_tabla in _ID_NUMERICO:
        try:
            valor_norm = int(valor)
        except (ValueError, TypeError):
            return None
    else:
        valor_norm = str(valor).strip()

    db    = get_db()
    tabla = get_table(nombre_tabla)
    condicion = tabla.c[campo] == valor_norm
    consulta = select(tabla.c.mongo_id).where(condicion)
    if excluir_id:
        consulta = consulta.where(tabla.c.mongo_id != excluir_id)

    if db.execute(consulta).first():
        return f"Ya existe un registro con {campo} = {valor_norm}"
    return None


def _serialize(row) -> dict:
    """Convierte mongo_id -> _id. Opera sobre copia para no mutar el original."""
    d = dict(row)
    d["_id"] = d.pop("mongo_id")
    return d


def _fecha_completa() -> str:
    """Genera la estampa de tiempo actual para el sistema."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ── Config general ─────────────────────────────────────────
def obtener_configuracion() -> dict:
    db    = get_db()
    tabla = get_table("configuracion")
    fila  = db.execute(select(tabla)).mappings().first()
    if not fila:
        return {}
    cfg = dict(fila)
    cfg["_id"] = cfg.pop("mongo_id")
    return cfg


def guardar_configuracion(datos: dict) -> dict:
    """
    Reemplaza la fila única de configuración con `datos` (igual que el
    replace_one({}, datos, upsert=True) de Mongo: los campos ausentes de
    `datos` quedan en NULL, no se conservan valores previos de esos campos).
    """
    db    = get_db()
    tabla = get_table("configuracion")
    datos = dict(datos)
    datos.pop("_id", None)
    datos.pop("mongo_id", None)
    datos["ultima_modificacion"] = _fecha_completa()

    columnas = [c.name for c in tabla.columns if c.name != "mongo_id"]
    valores  = {col: datos.get(col) for col in columnas}
    if isinstance(valores.get("config_dias"), (dict, list)):
        valores["config_dias"] = json.dumps(valores["config_dias"], ensure_ascii=False)

    existente = db.execute(select(tabla.c.mongo_id)).first()
    if existente:
        db.execute(update(tabla).where(tabla.c.mongo_id == existente.mongo_id).values(**valores))
    else:
        db.execute(insert(tabla).values(mongo_id=_nuevo_id(), **valores))
    return {"status": "ok", "mensaje": "Configuración guardada"}


# ── Helpers de dominio ─────────────────────────────────────
def _calcular_volumen_producto(datos: dict) -> float:
    """Calcula volumen (m³) a partir de largo, ancho, alto en metros."""
    try:
        largo = float(datos.get('largo') or 0)
        ancho = float(datos.get('ancho') or 0)
        alto  = float(datos.get('alto')  or 0)
        return round(largo * ancho * alto, 6)
    except (TypeError, ValueError):
        return 0.0

def _calcular_volumen_bimbo(datos: dict) -> float:
    """Calcula volumen (m³) a partir de largo, ancho, altura en metros."""
    try:
        largo  = float(datos.get('largo')  or 0)
        ancho  = float(datos.get('ancho')  or 0)
        altura = float(datos.get('altura') or 0)
        return round(largo * ancho * altura, 6)
    except (TypeError, ValueError):
        return 0.0

def _calcular_volumen_vehiculo(datos: dict) -> float:
    """Calcula volumen_m3 = largo_volumetria × ancho_volumetria × alto_volumetria (en metros)."""
    try:
        largo = float(datos.get('largo_volumetria') or 0)
        ancho = float(datos.get('ancho_volumetria') or 0)
        alto  = float(datos.get('alto_volumetria')  or 0)
        return round(largo * ancho * alto, 6)
    except (TypeError, ValueError):
        return 0.0

# ── Base CRUD ──────────────────────────────────────────────
def _listar(nombre_tabla: str, campo_busqueda, nombre: str = "", fecha: str = "", sort_field: str = "") -> list:
    db    = get_db()
    tabla = get_table(nombre_tabla)
    consulta = select(tabla)

    if nombre:
        campos = campo_busqueda if isinstance(campo_busqueda, list) else [campo_busqueda]
        condiciones = [tabla.c[c].like(_like(nombre), escape="\\") for c in campos]
        consulta = consulta.where(or_(*condiciones))

    if fecha:
        consulta = consulta.where(tabla.c.ultima_modificacion.like(_prefijo_like(fecha), escape="\\"))

    if sort_field:
        consulta = consulta.order_by(tabla.c[sort_field])

    return [_serialize(f) for f in db.execute(consulta).mappings().all()]

def _obtener(nombre_tabla: str, doc_id: str) -> "dict | None":
    did = _id_valido(doc_id)
    if did is None:
        return None
    db    = get_db()
    tabla = get_table(nombre_tabla)
    fila  = db.execute(select(tabla).where(tabla.c.mongo_id == did)).mappings().first()
    return _serialize(fila) if fila else None

def _agregar(nombre_tabla: str, datos: dict) -> dict:
    db    = get_db()
    tabla = get_table(nombre_tabla)
    datos = dict(datos)
    datos.pop("_id", None)
    datos.pop("mongo_id", None)

    # Normalizar campo ID: eliminar si vacío; convertir a int (numérico) o string (resto)
    campo = ID_CAMPO.get(nombre_tabla)
    if campo:
        valor = datos.get(campo)
        if valor == "" or valor is None:
            datos.pop(campo, None)
        elif nombre_tabla in _ID_NUMERICO:
            try:
                datos[campo] = int(valor)
            except ValueError:
                pass
        else:
            datos[campo] = str(valor).strip()

    error = _verificar_id_unico(nombre_tabla, datos)
    if error:
        return {"status": "error", "mensaje": error}

    if nombre_tabla in ("productos", "productos_proalmex"):
        datos['volumen'] = _calcular_volumen_producto(datos)
    elif nombre_tabla == "productos_bimbo":
        datos['volumen'] = _calcular_volumen_bimbo(datos)
    elif nombre_tabla == "vehiculos":
        datos['volumen_m3'] = _calcular_volumen_vehiculo(datos)
        datos.setdefault("activo", True)
        datos["chofer_id"] = _id_valido(datos.get("chofer_id")) if datos.get("chofer_id") else None

    datos["ultima_modificacion"] = _fecha_completa()
    nuevo_id = _nuevo_id()
    db.execute(insert(tabla).values(mongo_id=nuevo_id, **{k: v for k, v in datos.items() if k in tabla.columns}))
    return {"status": "ok", "id": nuevo_id}

def _editar(nombre_tabla: str, doc_id: str, datos: dict) -> dict:
    did = _id_valido(doc_id)
    if did is None:
        return {"status": "error", "mensaje": "ID inválido"}
    db    = get_db()
    tabla = get_table(nombre_tabla)
    datos = dict(datos)
    datos.pop("_id", None)
    datos.pop("mongo_id", None)

    # Normalizar campo ID: eliminar si vacío; convertir a int (numérico) o string (resto)
    campo = ID_CAMPO.get(nombre_tabla)
    if campo:
        valor = datos.get(campo)
        if valor == "" or valor is None:
            datos.pop(campo, None)
        elif nombre_tabla in _ID_NUMERICO:
            try:
                datos[campo] = int(valor)
            except ValueError:
                pass
        else:
            datos[campo] = str(valor).strip()

    error = _verificar_id_unico(nombre_tabla, datos, excluir_id=did)
    if error:
        return {"status": "error", "mensaje": error}

    datos["ultima_modificacion"] = _fecha_completa()
    # Si la llave se eliminó con pop(), no se sobrescribirá si ya existía.
    # Para limpiar un ID existente a vacío, la ponemos explícitamente a NULL.
    if nombre_tabla in ("productos", "productos_proalmex"):
        datos['volumen'] = _calcular_volumen_producto(datos)
    elif nombre_tabla == "productos_bimbo":
        datos['volumen'] = _calcular_volumen_bimbo(datos)
    elif nombre_tabla == "vehiculos":
        datos['volumen_m3'] = _calcular_volumen_vehiculo(datos)
        if "chofer_id" in datos:
            datos["chofer_id"] = _id_valido(datos.get("chofer_id")) if datos.get("chofer_id") else None

    valores = {k: v for k, v in datos.items() if k in tabla.columns}
    if campo and campo not in datos:
        valores[campo] = None

    resultado = db.execute(update(tabla).where(tabla.c.mongo_id == did).values(**valores))
    if resultado.rowcount == 0:
        return {"status": "error", "mensaje": "Documento no encontrado"}
    return {"status": "ok"}

def _eliminar(nombre_tabla: str, doc_id: str) -> dict:
    did = _id_valido(doc_id)
    if did is None:
        return {"status": "error", "mensaje": "ID inválido"}
    db    = get_db()
    tabla = get_table(nombre_tabla)
    resultado = db.execute(delete(tabla).where(tabla.c.mongo_id == did))
    if resultado.rowcount == 0:
        return {"status": "error", "mensaje": "Documento no encontrado"}
    return {"status": "ok"}

# ── Funciones de Dominio (Productos, Sucursales, Vehículos) ──
def listar_productos(nombre: str = "", fecha: str = "") -> list:
    db    = get_db()
    tabla = get_table("productos")
    consulta = select(tabla)
    if nombre:
        condiciones = [
            tabla.c.descripcion.like(_like(nombre), escape="\\"),
            tabla.c.marca.like(_like(nombre), escape="\\"),
            tabla.c.clave_sae.like(_like(nombre), escape="\\"),
        ]
        consulta = consulta.where(or_(*condiciones))
    if fecha:
        consulta = consulta.where(tabla.c.ultima_modificacion.like(_prefijo_like(fecha), escape="\\"))
    consulta = consulta.order_by(tabla.c.marca)
    return [_serialize(f) for f in get_db().execute(consulta).mappings().all()]
def obtener_producto(producto_id: str): return _obtener("productos", producto_id)

def buscar_producto_por_clave(clave_sae) -> "dict | None":
    clave_str = str(clave_sae).strip() if clave_sae else ""
    if not clave_str:
        return None
    db    = get_db()
    tabla = get_table("productos")
    fila = db.execute(select(tabla).where(tabla.c.clave_sae == clave_str)).mappings().first()
    if not fila:
        try:
            fila = db.execute(select(tabla).where(tabla.c.clave_sae == str(int(clave_str)))).mappings().first()
        except (ValueError, TypeError):
            pass
    return _serialize(fila) if fila else None
def agregar_producto(datos: dict): return _agregar("productos", datos)
def editar_producto(producto_id: str, datos: dict): return _editar("productos", producto_id, datos)
def eliminar_producto(producto_id: str): return _eliminar("productos", producto_id)

def buscar_producto_proalmex_por_clave(clave_sae) -> "dict | None":
    clave_str = str(clave_sae).strip() if clave_sae else ""
    if not clave_str:
        return None
    db    = get_db()
    tabla = get_table("productos_proalmex")
    fila = db.execute(select(tabla).where(tabla.c.clave_sae == clave_str)).mappings().first()
    if not fila:
        try:
            fila = db.execute(select(tabla).where(tabla.c.clave_sae == str(int(clave_str)))).mappings().first()
        except (ValueError, TypeError):
            pass
    return _serialize(fila) if fila else None

def listar_productos_proalmex(nombre: str = "", fecha: str = ""): return _listar("productos_proalmex", ["marca", "linea", "tamano"], nombre, fecha, "marca")
def obtener_producto_proalmex(producto_id: str): return _obtener("productos_proalmex", producto_id)
def agregar_producto_proalmex(datos: dict): return _agregar("productos_proalmex", datos)
def editar_producto_proalmex(producto_id: str, datos: dict): return _editar("productos_proalmex", producto_id, datos)
def eliminar_producto_proalmex(producto_id: str): return _eliminar("productos_proalmex", producto_id)

def listar_productos_bimbo(nombre: str = "", fecha: str = ""): return _listar("productos_bimbo", ["descripcion", "codigo_barra"], nombre, fecha, "codigo_barra")
def obtener_producto_bimbo(producto_id: str): return _obtener("productos_bimbo", producto_id)
def agregar_producto_bimbo(datos: dict): return _agregar("productos_bimbo", datos)
def editar_producto_bimbo(producto_id: str, datos: dict): return _editar("productos_bimbo", producto_id, datos)
def eliminar_producto_bimbo(producto_id: str): return _eliminar("productos_bimbo", producto_id)

def listar_sucursales(nombre: str = "", fecha: str = "") -> list:
    db    = get_db()
    tabla = get_table("sucursales")
    consulta = select(tabla)
    if nombre:
        condiciones = [
            tabla.c.nombre_base.like(_like(nombre), escape="\\"),
            tabla.c["nombre_icg-proalmex"].like(_like(nombre), escape="\\"),
            tabla.c.nombre_bimbo.like(_like(nombre), escape="\\"),
        ]
        consulta = consulta.where(or_(*condiciones))
    if fecha:
        consulta = consulta.where(tabla.c.ultima_modificacion.like(_prefijo_like(fecha), escape="\\"))
    consulta = consulta.order_by(tabla.c.num_tienda)
    return [_serialize(f) for f in db.execute(consulta).mappings().all()]
def obtener_sucursal(sucursal_id: str): return _obtener("sucursales", sucursal_id)
def agregar_sucursal(datos: dict): return _agregar("sucursales", datos)
def editar_sucursal(sucursal_id: str, datos: dict): return _editar("sucursales", sucursal_id, datos)
def eliminar_sucursal(sucursal_id: str): return _eliminar("sucursales", sucursal_id)

def listar_vehiculos(nombre: str = "", fecha: str = ""): return _listar("vehiculos", ["placas", "abreviatura", "descripcion"], nombre, fecha, "placas")

def toggle_activo_vehiculo(vehiculo_id: str) -> dict:
    vid = _id_valido(vehiculo_id)
    if vid is None:
        return {"status": "error", "mensaje": "ID inválido"}
    db    = get_db()
    tabla = get_table("vehiculos")
    fila  = db.execute(select(tabla.c.activo).where(tabla.c.mongo_id == vid)).mappings().first()
    if fila is None:
        return {"status": "error", "mensaje": "Vehículo no encontrado"}
    nuevo = not (fila["activo"] if fila["activo"] is not None else True)
    db.execute(update(tabla).where(tabla.c.mongo_id == vid).values(activo=nuevo, ultima_modificacion=_fecha_completa()))
    return {"status": "ok", "activo": nuevo}
def obtener_vehiculo(vehiculo_id: str): return _obtener("vehiculos", vehiculo_id)
def agregar_vehiculo(datos: dict): return _agregar("vehiculos", datos)
def editar_vehiculo(vehiculo_id: str, datos: dict): return _editar("vehiculos", vehiculo_id, datos)
def eliminar_vehiculo(vehiculo_id: str): return _eliminar("vehiculos", vehiculo_id)

def actualizar_chofer_vehiculo(vehiculo_id: str, chofer: str, chofer_id: "str | None" = None) -> dict:
    """
    Actualiza el campo `chofer` (nombre, para PDF/UI existente) y, si se
    provee, `chofer_id` (referencia real a la tabla `choferes`, usada
    por el portal del Conductor para saber con certeza qué rutas son suyas).
    """
    vid = _id_valido(vehiculo_id)
    if vid is None:
        return {"status": "error", "mensaje": "ID inválido"}
    db    = get_db()
    tabla = get_table("vehiculos")
    chofer_vid = _id_valido(chofer_id) if chofer_id else None
    resultado = db.execute(
        update(tabla).where(tabla.c.mongo_id == vid).values(
            chofer=(chofer or "").strip(),
            chofer_id=chofer_vid,
            ultima_modificacion=_fecha_completa(),
        )
    )
    if resultado.rowcount == 0:
        return {"status": "error", "mensaje": "Vehículo no encontrado"}
    return {"status": "ok"}

# ── Choferes ───────────────────────────────────────────────
def listar_choferes(nombre: str = "") -> list:
    choferes = _listar("choferes", "nombre", nombre, "", "nombre")
    for c in choferes:
        c["tiene_acceso"] = bool(c.get("usuario_id"))
        c.pop("usuario_id", None)
    return choferes

def obtener_chofer(chofer_id: str): return _obtener("choferes", chofer_id)

def agregar_chofer(datos: dict) -> dict:
    nombre = (datos.get("nombre") or "").strip()
    if not nombre:
        return {"status": "error", "mensaje": "El nombre del chofer es obligatorio."}
    db    = get_db()
    tabla = get_table("choferes")
    # La columna usa collation *_CI_AS (case-insensitive), así que "==" ya
    # compara sin distinguir mayúsculas/minúsculas — replica el
    # {"$regex": f"^{nombre}$", "$options": "i"} original (coincidencia exacta).
    if db.execute(select(tabla.c.mongo_id).where(tabla.c.nombre == nombre)).first():
        return {"status": "error", "mensaje": "Ya existe un chofer con ese nombre."}
    return _agregar("choferes", {"nombre": nombre})

def eliminar_chofer(chofer_id: str): return _eliminar("choferes", chofer_id)

def listar_clientes_mayoristas(nombre: str = "", fecha: str = ""): return _listar("clientes_mayoristas", ["nombre", "poblacion"], nombre, fecha, "id_cliente")
def obtener_cliente_mayorista(cliente_id: str): return _obtener("clientes_mayoristas", cliente_id)
def agregar_cliente_mayorista(datos: dict): return _agregar("clientes_mayoristas", datos)
def editar_cliente_mayorista(cliente_id: str, datos: dict): return _editar("clientes_mayoristas", cliente_id, datos)
def eliminar_cliente_mayorista(cliente_id: str): return _eliminar("clientes_mayoristas", cliente_id)

def toggle_activo_cliente_mayorista(cliente_id: str) -> dict:
    cid = _id_valido(cliente_id)
    if cid is None:
        return {"status": "error", "mensaje": "ID inválido"}
    db    = get_db()
    tabla = get_table("clientes_mayoristas")
    fila  = db.execute(select(tabla.c.activo).where(tabla.c.mongo_id == cid)).mappings().first()
    if fila is None:
        return {"status": "error", "mensaje": "Cliente no encontrado"}
    nuevo = not (fila["activo"] if fila["activo"] is not None else True)
    db.execute(update(tabla).where(tabla.c.mongo_id == cid).values(activo=nuevo, ultima_modificacion=_fecha_completa()))
    return {"status": "ok", "activo": nuevo}
