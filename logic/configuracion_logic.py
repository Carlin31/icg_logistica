from bson import ObjectId
from bson.errors import InvalidId
from datetime import datetime
from db import get_db

# ── Helpers ────────────────────────────────────────────────
ID_CAMPO = {
    "productos":           "clave_sae",
    "sucursales":          "num_tienda",
    "clientes_mayoristas": "id_cliente",
    "productos_bimbo":     "codigo_barra",
}

# Colecciones cuyo campo ID se almacena como entero (las demás se tratan como string)
_ID_NUMERICO = {"sucursales", "clientes_mayoristas"}

def _verificar_id_unico(coleccion: str, datos: dict, excluir_oid=None) -> str | None:
    """
    Devuelve un mensaje de error si el campo ID ya existe en otra doc.
    Retorna None si la validación pasa (incluye cuando el campo es nulo/vacío).
    Soporta IDs numéricos (colecciones en _ID_NUMERICO) y string (resto).
    """
    campo = ID_CAMPO.get(coleccion)
    if not campo:
        return None

    valor = datos.get(campo)

    # Permitir nulo, None o string vacío sin validar unicidad
    if valor is None or valor == "" or valor != valor:  # NaN check
        return None

    if coleccion in _ID_NUMERICO:
        try:
            valor_norm = int(valor)
        except (ValueError, TypeError):
            return None
    else:
        valor_norm = str(valor).strip()

    db = get_db()
    query = {campo: valor_norm}
    if excluir_oid:
        query["_id"] = {"$ne": excluir_oid}

    if db[coleccion].find_one(query):
        return f"Ya existe un registro con {campo} = {valor_norm}"
    return None

def _serialize(doc: dict) -> dict:
    """Convierte _id ObjectId → str. Opera sobre copia para no mutar el original."""
    doc = dict(doc)
    doc["_id"] = str(doc["_id"])
    return doc

def _parse_oid(doc_id: str) -> ObjectId | None:
    """Devuelve ObjectId o None si el string es inválido."""
    try:
        return ObjectId(doc_id)
    except (InvalidId, TypeError):
        return None

def _fecha_completa() -> str:
    """Genera la estampa de tiempo actual para el sistema."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# ── Config general ─────────────────────────────────────────
def obtener_configuracion() -> dict:
    db  = get_db()
    cfg = db.configuracion.find_one({}) or {}
    if "_id" in cfg:
        cfg["_id"] = str(cfg["_id"])
    return cfg

def guardar_configuracion(datos: dict) -> dict:
    db = get_db()
    datos = dict(datos)
    datos.pop("_id", None)
    # También rastreamos la modificación en la configuración general
    datos["ultima_modificacion"] = _fecha_completa()
    db.configuracion.replace_one({}, datos, upsert=True)
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
def _listar(coleccion: str, campo_busqueda, nombre: str = "", fecha: str = "", sort_field: str = "") -> list:
    db    = get_db()
    query: dict = {}

    if nombre:
        if isinstance(campo_busqueda, list):
            query["$or"] = [{c: {"$regex": nombre, "$options": "i"}} for c in campo_busqueda]
        else:
            query[campo_busqueda] = {"$regex": nombre, "$options": "i"}

    if fecha:
        # Busca por la parte de la fecha en la cadena de 'ultima_modificacion'
        query["ultima_modificacion"] = {"$regex": f"^{fecha}"}

    cursor = db[coleccion].find(query)
    if sort_field:
        cursor = cursor.sort(sort_field, 1)

    return [_serialize(doc) for doc in cursor]

def _obtener(coleccion: str, doc_id: str) -> dict | None:
    oid = _parse_oid(doc_id)
    if oid is None:
        return None
    db  = get_db()
    doc = db[coleccion].find_one({"_id": oid})
    return _serialize(doc) if doc else None

def _agregar(coleccion: str, datos: dict) -> dict:
    db = get_db()
    datos = dict(datos)
    datos.pop("_id", None)

    # Normalizar campo ID: eliminar si vacío; convertir a int (numérico) o string (resto)
    campo = ID_CAMPO.get(coleccion)
    if campo:
        valor = datos.get(campo)
        if valor == "" or valor is None:
            datos.pop(campo, None)
        elif coleccion in _ID_NUMERICO:
            try:
                datos[campo] = int(valor)
            except ValueError:
                pass
        else:
            datos[campo] = str(valor).strip()

    error = _verificar_id_unico(coleccion, datos)
    if error:
        return {"status": "error", "mensaje": error}

    if coleccion in ("productos", "productos_proalmex"):
        datos['volumen'] = _calcular_volumen_producto(datos)
    elif coleccion == "productos_bimbo":
        datos['volumen'] = _calcular_volumen_bimbo(datos)
    elif coleccion == "vehiculos":
        datos['volumen_m3'] = _calcular_volumen_vehiculo(datos)
        datos.setdefault("activo", True)
        datos["chofer_id"] = _parse_oid(datos.get("chofer_id")) if datos.get("chofer_id") else None

    datos["ultima_modificacion"] = _fecha_completa()
    result = db[coleccion].insert_one(datos)
    return {"status": "ok", "id": str(result.inserted_id)}

def _editar(coleccion: str, doc_id: str, datos: dict) -> dict:
    oid = _parse_oid(doc_id)
    if oid is None:
        return {"status": "error", "mensaje": "ID inválido"}
    db = get_db()
    datos = dict(datos)
    datos.pop("_id", None)

    # Normalizar campo ID: eliminar si vacío; convertir a int (numérico) o string (resto)
    campo = ID_CAMPO.get(coleccion)
    if campo:
        valor = datos.get(campo)
        if valor == "" or valor is None:
            datos.pop(campo, None)
        elif coleccion in _ID_NUMERICO:
            try:
                datos[campo] = int(valor)
            except ValueError:
                pass
        else:
            datos[campo] = str(valor).strip()

    error = _verificar_id_unico(coleccion, datos, excluir_oid=oid)
    if error:
        return {"status": "error", "mensaje": error}

    datos["ultima_modificacion"] = _fecha_completa()
    # Si la llave se eliminó con pop(), no se sobrescribirá si ya existía.
    # Para limpiar un ID existente a vacío, usamos $unset
    if coleccion in ("productos", "productos_proalmex"):
        datos['volumen'] = _calcular_volumen_producto(datos)
    elif coleccion == "productos_bimbo":
        datos['volumen'] = _calcular_volumen_bimbo(datos)
    elif coleccion == "vehiculos":
        datos['volumen_m3'] = _calcular_volumen_vehiculo(datos)
        if "chofer_id" in datos:
            datos["chofer_id"] = _parse_oid(datos.get("chofer_id")) if datos.get("chofer_id") else None

    update_query = {"$set": datos}
    if campo and campo not in datos:
        update_query["$unset"] = {campo: ""}

    result = db[coleccion].update_one({"_id": oid}, update_query)
    
    if result.matched_count == 0:
        return {"status": "error", "mensaje": "Documento no encontrado"}
    return {"status": "ok"}

def _eliminar(coleccion: str, doc_id: str) -> dict:
    oid = _parse_oid(doc_id)
    if oid is None:
        return {"status": "error", "mensaje": "ID inválido"}
    db     = get_db()
    result = db[coleccion].delete_one({"_id": oid})
    if result.deleted_count == 0:
        return {"status": "error", "mensaje": "Documento no encontrado"}
    return {"status": "ok"}

# ── Funciones de Dominio (Productos, Sucursales, Vehículos) ──
def listar_productos(nombre: str = "", fecha: str = "") -> list:
    db    = get_db()
    query: dict = {}
    if nombre:
        or_conds = [
            {"descripcion": {"$regex": nombre, "$options": "i"}},
            {"marca":       {"$regex": nombre, "$options": "i"}},
        ]
        or_conds.append({"clave_sae": {"$regex": nombre, "$options": "i"}})
        try:
            or_conds.append({"clave_sae": int(nombre)})
        except (ValueError, TypeError):
            pass
        query["$or"] = or_conds
    if fecha:
        query["ultima_modificacion"] = {"$regex": f"^{fecha}"}
    return [_serialize(doc) for doc in db["productos"].find(query).sort("marca", 1)]
def obtener_producto(producto_id: str): return _obtener("productos", producto_id)

def buscar_producto_por_clave(clave_sae) -> dict | None:
    clave_str = str(clave_sae).strip() if clave_sae else ""
    if not clave_str:
        return None
    db  = get_db()
    doc = db["productos"].find_one({"clave_sae": clave_str})
    if not doc:
        try:
            doc = db["productos"].find_one({"clave_sae": int(clave_str)})
        except (ValueError, TypeError):
            pass
    return _serialize(doc) if doc else None
def agregar_producto(datos: dict): return _agregar("productos", datos)
def editar_producto(producto_id: str, datos: dict): return _editar("productos", producto_id, datos)
def eliminar_producto(producto_id: str): return _eliminar("productos", producto_id)

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

def listar_sucursales(nombre: str = "", fecha: str = ""): return _listar("sucursales", ["nombre_base", "nombre_icg-proalmex", "nombre_bimbo"], nombre, fecha, "num_tienda")
def obtener_sucursal(sucursal_id: str): return _obtener("sucursales", sucursal_id)
def agregar_sucursal(datos: dict): return _agregar("sucursales", datos)
def editar_sucursal(sucursal_id: str, datos: dict): return _editar("sucursales", sucursal_id, datos)
def eliminar_sucursal(sucursal_id: str): return _eliminar("sucursales", sucursal_id)

def listar_vehiculos(nombre: str = "", fecha: str = ""): return _listar("vehiculos", ["placas", "abreviatura", "descripcion"], nombre, fecha, "placas")

def toggle_activo_vehiculo(vehiculo_id: str) -> dict:
    oid = _parse_oid(vehiculo_id)
    if oid is None:
        return {"status": "error", "mensaje": "ID inválido"}
    db  = get_db()
    doc = db.vehiculos.find_one({"_id": oid}, {"activo": 1})
    if doc is None:
        return {"status": "error", "mensaje": "Vehículo no encontrado"}
    nuevo = not doc.get("activo", True)
    db.vehiculos.update_one({"_id": oid}, {"$set": {"activo": nuevo, "ultima_modificacion": _fecha_completa()}})
    return {"status": "ok", "activo": nuevo}
def obtener_vehiculo(vehiculo_id: str): return _obtener("vehiculos", vehiculo_id)
def agregar_vehiculo(datos: dict): return _agregar("vehiculos", datos)
def editar_vehiculo(vehiculo_id: str, datos: dict): return _editar("vehiculos", vehiculo_id, datos)
def eliminar_vehiculo(vehiculo_id: str): return _eliminar("vehiculos", vehiculo_id)

def actualizar_chofer_vehiculo(vehiculo_id: str, chofer: str, chofer_id: str | None = None) -> dict:
    """
    Actualiza el campo `chofer` (nombre, para PDF/UI existente) y, si se
    provee, `chofer_id` (referencia real a la colección `choferes`, usada
    por el portal del Conductor para saber con certeza qué rutas son suyas).
    """
    oid = _parse_oid(vehiculo_id)
    if oid is None:
        return {"status": "error", "mensaje": "ID inválido"}
    db = get_db()
    chofer_oid = _parse_oid(chofer_id) if chofer_id else None
    result = db.vehiculos.update_one(
        {"_id": oid},
        {"$set": {
            "chofer":             (chofer or "").strip(),
            "chofer_id":          chofer_oid,
            "ultima_modificacion": _fecha_completa(),
        }},
    )
    if result.matched_count == 0:
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
    db = get_db()
    if db.choferes.find_one({"nombre": {"$regex": f"^{nombre}$", "$options": "i"}}):
        return {"status": "error", "mensaje": "Ya existe un chofer con ese nombre."}
    return _agregar("choferes", {"nombre": nombre})

def eliminar_chofer(chofer_id: str): return _eliminar("choferes", chofer_id)

def listar_clientes_mayoristas(nombre: str = "", fecha: str = ""): return _listar("clientes_mayoristas", ["nombre", "poblacion"], nombre, fecha, "id_cliente")
def obtener_cliente_mayorista(cliente_id: str): return _obtener("clientes_mayoristas", cliente_id)
def agregar_cliente_mayorista(datos: dict): return _agregar("clientes_mayoristas", datos)
def editar_cliente_mayorista(cliente_id: str, datos: dict): return _editar("clientes_mayoristas", cliente_id, datos)
def eliminar_cliente_mayorista(cliente_id: str): return _eliminar("clientes_mayoristas", cliente_id)

def toggle_activo_cliente_mayorista(cliente_id: str) -> dict:
    oid = _parse_oid(cliente_id)
    if oid is None:
        return {"status": "error", "mensaje": "ID inválido"}
    db  = get_db()
    doc = db.clientes_mayoristas.find_one({"_id": oid}, {"activo": 1})
    if doc is None:
        return {"status": "error", "mensaje": "Cliente no encontrado"}
    nuevo = not doc.get("activo", True)
    db.clientes_mayoristas.update_one({"_id": oid}, {"$set": {"activo": nuevo, "ultima_modificacion": _fecha_completa()}})
    return {"status": "ok", "activo": nuevo}