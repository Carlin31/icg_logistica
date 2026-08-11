# Conectar el sistema completo de enganche de mayoristas — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Conectar `construir_rutas_con_mayoristas()` (el motor completo de enganche por zona, ya escrito, nunca invocado en producción) a `generar_rutas_vrp_afinidad()`, persistir su resultado, y hacer que Asignación/PDF/Modificación lean lo persistido en vez de recalcular en vivo con el parche liviano actual — que se conserva intacto como fallback.

**Architecture:** Flag nuevo `ENGANCHE_ZONA_ACTIVO` (junto a `CONVRP_ACTIVO`, del que depende). Cuando está activo, `generar_rutas_vrp_afinidad()` llama `construir_rutas_con_mayoristas()` en vez de `construir_groups_convrp()`, corre el mismo pipeline de secuenciación (histórico → proximidad) que ya usa `calcular_distribucion_mayoristas()`, y persiste en una tabla nueva `convrp_mayoristas` (mismo patrón `DELETE`+insert que `convrp_excepciones`). Los 9 sitios de lectura pasan a `obtener_mayoristas_guardados(...) or calcular_distribucion_mayoristas(...)`.

**Tech Stack:** Python 3, SQLAlchemy Core, SQL Server, pytest.

**Spec de referencia:** [2026-08-10-enganche-zona-mayoristas-design.md](../specs/2026-08-10-enganche-zona-mayoristas-design.md)

---

## Mapa de archivos

- **Crear:** ninguno (todo se agrega a archivos existentes).
- **Modificar:**
  - `scripts/crear_plantilla_canonica.py` — DDL de la tabla `convrp_mayoristas`.
  - `logic/mayoristas_logic.py` — `guardar_mayoristas_convrp()` (reutiliza `_ordenar_mayoristas_en_ruta`/`_insertar_pos_proxima`/`_ruta_centroid`/`_cargar_historico_mayoristas`, todas privadas de este módulo) y `obtener_mayoristas_guardados()`.
  - `logic/historico_logic.py` — flag `ENGANCHE_ZONA_ACTIVO`, wiring dentro de `generar_rutas_vrp_afinidad()`.
  - `logic/asignacion_logic.py` — 4 sitios de lectura.
  - `logic/modificacion_logic.py` — 1 sitio de lectura.
  - `logic/pdf_logic.py` — 1 sitio de lectura.
  - `router/asignacion_router.py` — 1 sitio de lectura.
- **Test:** `tests/test_mayoristas_logic.py` (ya existe, se le agregan casos).

---

### Task 1: Tabla `convrp_mayoristas`

**Files:**
- Modify: `scripts/crear_plantilla_canonica.py:36-166`

- [ ] **Step 1: Agregar el DDL a la lista `DDL`**

En `scripts/crear_plantilla_canonica.py`, dentro de la lista `DDL` (línea ~166, justo después de la tupla `"plantilla_poblacion_zona"` y antes del cierre `]`), agregar:

```python
    # ── mayoristas del ConVRP completo, por logística (las lee Asignación/PDF/Modificación) ──
    ("convrp_mayoristas", """
        CREATE TABLE convrp_mayoristas (
            logistica_id  NVARCHAR(40)  NOT NULL,
            generado_en   NVARCHAR(40)  NOT NULL,
            unidad        NVARCHAR(60)  NOT NULL,
            dia           NVARCHAR(20)  NOT NULL,
            orden         INT           NOT NULL,
            id_cliente    INT           NOT NULL,
            nombre        NVARCHAR(200) NULL,
            peso_kg       FLOAT         NOT NULL,
            via_zona      NVARCHAR(20)  NULL,
            via_destino   NVARCHAR(20)  NULL,
            CONSTRAINT PK_convrp_mayoristas PRIMARY KEY (logistica_id, unidad, dia, id_cliente)
        )
    """),
```

- [ ] **Step 2: Correr el script contra la base de datos**

Run: `python scripts/crear_plantilla_canonica.py`
Expected: la salida incluye `convrp_mayoristas` en "Tablas creadas". Si ya existe (segunda corrida), aparece en "Ya existían" — nunca falla.

- [ ] **Step 3: Reiniciar el proceso Flask** (si hay uno corriendo) para que `db.py` refleje la tabla nueva (la reflexión de `MetaData` ocurre una sola vez por proceso).

- [ ] **Step 4: Commit**

```bash
git add scripts/crear_plantilla_canonica.py
git commit -m "feat: tabla convrp_mayoristas para persistir el enganche completo de mayoristas"
```

---

### Task 2: `guardar_mayoristas_convrp()` — persistir con la MISMA secuenciación que el parche en vivo

**Contexto:** `construir_rutas_con_mayoristas()` devuelve `por_ruta: {(unidad,dia): [mayorista,...]}` SIN interleaving con sucursales — sólo dice a qué ruta va cada mayorista, no en qué posición dentro del recorrido. `calcular_distribucion_mayoristas()` sí calcula esa posición (histórico propio → proximidad al centroide, `_ordenar_mayoristas_en_ruta` + `_insertar_pos_proxima`). Para que lo persistido tenga el mismo `orden` que produciría el parche (y así los 9 sitios de lectura no vean ninguna diferencia de comportamiento, sólo de origen del dato), `guardar_mayoristas_convrp()` reutiliza esas mismas funciones privadas — por eso vive en `mayoristas_logic.py`, no en `convrp_integracion.py`.

**Files:**
- Modify: `logic/mayoristas_logic.py` (agregar función nueva, cerca de `calcular_distribucion_mayoristas`, línea ~987 en adelante)
- Test: `tests/test_mayoristas_logic.py`

- [ ] **Step 1: Escribir la prueba que falla**

Agregar a `tests/test_mayoristas_logic.py` (después de las pruebas existentes de `_construir_cache_zonas`):

```python
# ── guardar_mayoristas_convrp / obtener_mayoristas_guardados ───────────────
def test_guardar_mayoristas_convrp_secuencia_por_proximidad():
    from logic.mayoristas_logic import guardar_mayoristas_convrp
    # ruta con 2 sucursales; el mayorista sin histórico se inserta junto a
    # la más cercana (Sur, sid=2), no al final por default.
    por_ruta = {
        ("V1", "LUNES"): [
            {"id_cliente": 900, "nombre": "ABARROTES X", "peso_kg": 50.0,
             "latitud": 19.001, "longitud": -96.001, "poblacion": "PRUEBA"},
        ],
    }
    detalle = [
        {"id_cliente": 900, "via_zona": "HISTORIA", "via_destino": "NUCLEO"},
    ]
    rutas = [
        {"_id": "vrpaf_v1_lunes", "sucursales": [
            {"num_tienda": 1, "latitud": 19.500, "longitud": -96.500, "orden": 1},
            {"num_tienda": 2, "latitud": 19.000, "longitud": -96.000, "orden": 2},
        ]},
    ]
    n = guardar_mayoristas_convrp("507f1f77bcf86cd799439011", por_ruta, detalle, rutas)
    assert n == 1


def test_guardar_mayoristas_convrp_reemplaza_corrida_anterior():
    from logic.mayoristas_logic import guardar_mayoristas_convrp
    from db import get_db, get_table
    from sqlalchemy import select
    lid = "507f1f77bcf86cd799439012"
    rutas = [{"_id": "vrpaf_v1_lunes", "sucursales": [
        {"num_tienda": 1, "latitud": 19.0, "longitud": -96.0, "orden": 1}]}]
    por_ruta_1 = {("V1", "LUNES"): [
        {"id_cliente": 1, "nombre": "A", "peso_kg": 10.0, "latitud": 19.0, "longitud": -96.0}]}
    por_ruta_2 = {("V1", "LUNES"): [
        {"id_cliente": 2, "nombre": "B", "peso_kg": 20.0, "latitud": 19.0, "longitud": -96.0}]}
    guardar_mayoristas_convrp(lid, por_ruta_1, [], rutas)
    guardar_mayoristas_convrp(lid, por_ruta_2, [], rutas)
    db = get_db()
    t = get_table("convrp_mayoristas")
    filas = list(db.execute(select(t).where(t.c.logistica_id == lid)).mappings())
    assert len(filas) == 1
    assert filas[0]["id_cliente"] == 2


def test_guardar_mayoristas_convrp_sin_mayoristas_devuelve_cero():
    from logic.mayoristas_logic import guardar_mayoristas_convrp
    n = guardar_mayoristas_convrp("507f1f77bcf86cd799439013", {}, [], [])
    assert n == 0
```

Estas pruebas necesitan BD (mismo criterio que `test_construir_cache_zonas_resuelve_grupo_nucleo_real` en este archivo — usan la fixture `app_ctx` ya definida más abajo en el mismo archivo). Moverlas después de la definición de `app_ctx` en el archivo, y agregar el parámetro `app_ctx` a cada una:

```python
def test_guardar_mayoristas_convrp_secuencia_por_proximidad(app_ctx):
    ...
def test_guardar_mayoristas_convrp_reemplaza_corrida_anterior(app_ctx):
    ...
def test_guardar_mayoristas_convrp_sin_mayoristas_devuelve_cero(app_ctx):
    ...
```

- [ ] **Step 2: Correr las pruebas y verificar que fallan**

Run: `python -m pytest tests/test_mayoristas_logic.py -k guardar_mayoristas_convrp -v`
Expected: `ImportError: cannot import name 'guardar_mayoristas_convrp'` (o se salta con "BD no disponible" si no hay conexión — en ese caso continuar de todos modos, se verificarán al final contra BD real).

- [ ] **Step 3: Implementar `guardar_mayoristas_convrp()`**

Agregar a `logic/mayoristas_logic.py`, después de `calcular_distribucion_mayoristas` (línea ~987):

```python
def guardar_mayoristas_convrp(logistica_id: str, por_ruta: dict, detalle: list,
                              rutas: list) -> int:
    """
    Persiste el resultado del enganche COMPLETO (`construir_rutas_con_mayoristas`)
    en `convrp_mayoristas`. Reemplaza la corrida anterior de esa logística
    (mismo criterio que `convrp_integracion.guardar_excepciones_convrp`).

    `por_ruta` : {(unidad, dia): [mayorista, ...]} -- salida de
                 `construir_rutas_con_mayoristas` / `enganchar_mayoristas_por_zona`.
    `detalle`  : lista con una fila por cliente (id_cliente, via_zona,
                 via_destino, ...) -- mismo `detalle` que esas funciones.
    `rutas`    : [{_id, sucursales:[{num_tienda, latitud, longitud, orden}]}]
                 -- para calcular la posición del mayorista dentro de la ruta
                 con el MISMO criterio que `calcular_distribucion_mayoristas`
                 (histórico propio -> proximidad), así lo persistido no se
                 nota distinto para quien lo lee.

    Devuelve el nº de filas escritas, o -1 si falló.
    """
    if not logistica_id:
        return 0
    ahora = datetime.now().isoformat()
    via_por_cliente = {d.get("id_cliente"): d for d in (detalle or [])}
    sucursales_por_rid = {r.get("_id"): r.get("sucursales", []) for r in (rutas or [])}
    historico_orden = _cargar_historico_mayoristas(str(logistica_id))
    try:
        db = get_db()
        t = get_table("convrp_mayoristas")
        db.execute(delete(t).where(t.c.logistica_id == logistica_id))
        filas = []
        for (unidad, dia), mayoristas in (por_ruta or {}).items():
            rid = f"vrpaf_{str(unidad).replace(' ', '_').lower()}_{str(dia).lower()}"
            sucursales = sucursales_por_rid.get(rid, [])
            ordenados = _ordenar_mayoristas_en_ruta(
                list(mayoristas), rid, historico_orden, sucursales)
            for orden, m in enumerate(ordenados, start=1):
                d = via_por_cliente.get(m.get("id_cliente")) or {}
                filas.append({
                    "logistica_id": logistica_id, "generado_en": ahora,
                    "unidad": unidad, "dia": dia, "orden": orden,
                    "id_cliente": m.get("id_cliente"),
                    "nombre": (m.get("nombre") or "")[:200],
                    "peso_kg": float(m.get("peso_kg") or 0),
                    "via_zona": d.get("via_zona"), "via_destino": d.get("via_destino"),
                })
        if filas:
            db.execute(insert(t), filas)
        return len(filas)
    except Exception as exc:  # noqa: BLE001
        import traceback
        print("=" * 70)
        print(f"[convrp] NO SE GUARDARON LOS MAYORISTAS: {type(exc).__name__}: {exc}")
        print("[convrp] las vistas caerán al cálculo en vivo para esta logística.")
        print(traceback.format_exc())
        print("=" * 70)
        return -1
```

En `logic/mayoristas_logic.py:19`, el import actual es `from sqlalchemy import select, insert` — falta `delete`. Cambiar a:
```python
from sqlalchemy import select, insert, delete
```
(`datetime` ya está importado en la línea 15, no hace falta tocarlo.)

- [ ] **Step 4: Correr las pruebas y verificar que pasan**

Run: `python -m pytest tests/test_mayoristas_logic.py -k guardar_mayoristas_convrp -v`
Expected: 3 `PASSED` (o `SKIPPED` si no hay BD — en ese caso, correrlas manualmente contra la BD real antes de continuar).

- [ ] **Step 5: Commit**

```bash
git add logic/mayoristas_logic.py tests/test_mayoristas_logic.py
git commit -m "feat: guardar_mayoristas_convrp — persiste el enganche completo con la misma secuenciación que el parche en vivo"
```

---

### Task 3: `obtener_mayoristas_guardados()` — reconstruye la misma forma que `calcular_distribucion_mayoristas`

**Files:**
- Modify: `logic/mayoristas_logic.py`
- Test: `tests/test_mayoristas_logic.py`

- [ ] **Step 1: Escribir la prueba que falla**

```python
def test_obtener_mayoristas_guardados_reconstruye_forma_esperada(app_ctx):
    from logic.mayoristas_logic import guardar_mayoristas_convrp, obtener_mayoristas_guardados
    lid = "507f1f77bcf86cd799439014"
    rutas = [{"_id": "vrpaf_v1_lunes", "sucursales": [
        {"num_tienda": 1, "nombre_base": "Suc 1", "latitud": 19.0, "longitud": -96.0,
         "orden": 1, "peso_kg": 500}]}]
    por_ruta = {("V1", "LUNES"): [
        {"id_cliente": 5, "nombre": "ABARROTES Y", "peso_kg": 30.0,
         "latitud": 19.001, "longitud": -96.001}]}
    guardar_mayoristas_convrp(lid, por_ruta, [], rutas)
    dist = obtener_mayoristas_guardados(lid, rutas)
    assert dist is not None
    assert dist["mayoristas_por_ruta"]["vrpaf_v1_lunes"][0]["id_cliente"] == 5
    assert dist["mayoristas_por_ruta"]["vrpaf_v1_lunes"][0]["peso_kg"] == 30.0
    paradas = dist["paradas_integradas"]["vrpaf_v1_lunes"]
    assert any(p["tipo"] == "mayorista" and p["id_cliente"] == 5 for p in paradas)
    assert any(p["tipo"] == "sucursal" and p["num_tienda"] == 1 for p in paradas)
    assert dist["orden_sucursales"]["vrpaf_v1_lunes"]["1"] is not None


def test_obtener_mayoristas_guardados_sin_filas_es_none(app_ctx):
    from logic.mayoristas_logic import obtener_mayoristas_guardados
    dist = obtener_mayoristas_guardados("507f1f77bcf86cd799439015", [])
    assert dist is None
```

- [ ] **Step 2: Correr las pruebas y verificar que fallan**

Run: `python -m pytest tests/test_mayoristas_logic.py -k obtener_mayoristas_guardados -v`
Expected: `ImportError: cannot import name 'obtener_mayoristas_guardados'`.

- [ ] **Step 3: Implementar `obtener_mayoristas_guardados()`**

> **Nota (agregada tras la revisión de Task 2):** `_vrpaf_id(unidad, dia)` ya
> existe en `logic/mayoristas_logic.py` (extraída durante Task 2 para evitar
> duplicar el formato `f"vrpaf_..."` que también usa `logic/historico_logic.py:1311`).
> Reutilízala aquí — NO vuelvas a escribir el f-string inline.

Agregar a `logic/mayoristas_logic.py`, justo después de `guardar_mayoristas_convrp`:

```python
def obtener_mayoristas_guardados(logistica_id: str, rutas: list) -> "dict | None":
    """
    Reconstruye la MISMA forma de respuesta que `calcular_distribucion_mayoristas`
    a partir de lo guardado en `convrp_mayoristas` -- así los sitios que ya
    consumen esa forma no cambian, sólo la llamada.

    Devuelve None si no hay filas guardadas para esa logística (nunca un dict
    vacío, que sería indistinguible de "esta corrida no tuvo mayoristas") --
    el llamador debe caer a `calcular_distribucion_mayoristas` en ese caso.
    """
    oid = _id_valido(logistica_id)
    if not oid:
        return None
    db = get_db()
    t = get_table("convrp_mayoristas")
    filas = list(db.execute(
        select(t).where(t.c.logistica_id == logistica_id).order_by(
            t.c.unidad, t.c.dia, t.c.orden)).mappings())
    if not filas:
        return None

    sucursales_por_rid = {r.get("_id"): list(r.get("sucursales", [])) for r in (rutas or [])}
    mayoristas_por_ruta: dict = {}
    paradas_integradas: dict = {}
    orden_sucursales: dict = {}
    todos_mayoristas: list = []

    por_rid: dict = {}
    for f in filas:
        rid = _vrpaf_id(f['unidad'], f['dia'])
        por_rid.setdefault(rid, []).append(dict(f))

    for rid, mays in por_rid.items():
        sucursales_raw = sucursales_por_rid.get(rid, [])
        sucursales = [dict(s, tipo="sucursal") for s in sucursales_raw]
        for idx, suc in enumerate(sucursales, start=1):
            suc["orden"] = idx

        entradas = []
        paradas = list(sucursales)
        for f in sorted(mays, key=lambda x: x["orden"]):
            entrada = {
                "id_cliente": f["id_cliente"], "nombre": f["nombre"] or "",
                "peso_kg": float(f["peso_kg"]), "ruta_id": rid, "orden": f["orden"],
            }
            entradas.append(entrada)
            todos_mayoristas.append(dict(entrada))
            paradas.append({
                "tipo": "mayorista", "id_cliente": f["id_cliente"],
                "nombre_base": f["nombre"] or "", "peso_kg": float(f["peso_kg"]),
                "orden": f["orden"],
            })
        mayoristas_por_ruta[rid] = entradas

        orden_map: dict = {}
        for p in paradas:
            if p.get("tipo") != "sucursal":
                continue
            nt = p.get("num_tienda")
            if nt is not None:
                orden_map[str(nt)] = p.get("orden")
        orden_sucursales[rid] = orden_map
        paradas_integradas[rid] = paradas

    return {
        "mayoristas_por_ruta": mayoristas_por_ruta,
        "paradas_integradas": paradas_integradas,
        "orden_sucursales": orden_sucursales,
        "todos_mayoristas": todos_mayoristas,
        "sin_asignar": [],
        "sin_coords": [],
        "pendientes": [],
        "actualizado_en": filas[0]["generado_en"] if filas else None,
    }
```

> **Nota:** `paradas_integradas` aquí NO re-intercala por posición geográfica
> (`_insertar_pos_proxima`) -- el `orden` ya viene resuelto desde el guardado
> (Task 2), así que sólo se listan sucursales primero y mayoristas después
> dentro de la misma ruta. Si algún consumidor depende de que la lista esté
> físicamente intercalada en el orden de recorrido (no sólo que cada `orden`
> individual sea correcto), agregar un `sorted(paradas, key=lambda p:
> p["orden"])` al final del bucle antes de asignar `paradas_integradas[rid]`.
> Revisar esto contra el uso real en Task 9 (verificación en sandbox) antes
> de dar la tarea por cerrada.

- [ ] **Step 4: Correr las pruebas y verificar que pasan**

Run: `python -m pytest tests/test_mayoristas_logic.py -k obtener_mayoristas_guardados -v`
Expected: 2 `PASSED`.

- [ ] **Step 5: Commit**

```bash
git add logic/mayoristas_logic.py tests/test_mayoristas_logic.py
git commit -m "feat: obtener_mayoristas_guardados — lee convrp_mayoristas con la forma que ya consumen las vistas"
```

---

### Task 4: Flag `ENGANCHE_ZONA_ACTIVO` y wiring en `generar_rutas_vrp_afinidad`

**Files:**
- Modify: `logic/historico_logic.py:72` (junto a `CONVRP_ACTIVO`) y `logic/historico_logic.py:1095-1350` (bloque ConVRP + guardado)

- [ ] **Step 1: Agregar el flag**

En `logic/historico_logic.py`, línea 72 (junto a `CONVRP_ACTIVO = True`), agregar debajo:

```python
# Enganche completo de mayoristas (Fase 3, enganche_zona.py). Depende de
# CONVRP_ACTIVO -- construir_rutas_con_mayoristas() llama internamente a
# construir_groups_convrp() en cada pasada de su punto fijo. Apagado hasta
# probar contra una logística sandbox y correr scripts/smoke_convrp.py.
ENGANCHE_ZONA_ACTIVO = False
```

- [ ] **Step 2: Sustituir la llamada dentro del bloque `CONVRP_ACTIVO`**

En `logic/historico_logic.py`, dentro del bloque `if CONVRP_ACTIVO:` (línea ~1104-1133), el `try` actual llama `construir_groups_convrp(...)` y guarda sólo `convrp_groups, convrp_excepciones, convrp_meta`. Reemplazar ese `try` completo por:

```python
    convrp_groups = None
    convrp_excepciones: list = []
    convrp_meta: dict = {}
    convrp_mayoristas_por_ruta: dict = {}
    convrp_mayoristas_detalle: list = []
    if CONVRP_ACTIVO:
        try:
            from logic.convrp_integracion import (
                construir_groups_convrp, construir_rutas_con_mayoristas,
                guardar_excepciones_convrp)
            _cfgm = db.execute(select(get_table("configuracion"))).mappings().first() or {}
            _depot = (float(_cfgm.get("matriz_lat") or MATRIZ_LAT_DEFAULT),
                      float(_cfgm.get("matriz_lon") or MATRIZ_LON_DEFAULT))
            if ENGANCHE_ZONA_ACTIVO:
                from logic.mayoristas_logic import _leer_pesos_mayoristas, _leer_coords_mayoristas
                pedidos_may, nombres_may = _leer_pesos_mayoristas(db, oid)
                ids_may = {p["id_cliente"] for p in pedidos_may}
                coords_may = _leer_coords_mayoristas(db, ids_may)
                kg_por_cliente: dict = {}
                for p in pedidos_may:
                    kg_por_cliente[p["id_cliente"]] = kg_por_cliente.get(p["id_cliente"], 0.0) + float(p["peso"])
                lista_mayoristas = [
                    {"id_cliente": cid, "nombre": (coords_may.get(cid) or {}).get("nombre") or nombres_may.get(cid, ""),
                     "poblacion": (coords_may.get(cid) or {}).get("poblacion"),
                     "latitud": (coords_may.get(cid) or {}).get("latitud"),
                     "longitud": (coords_may.get(cid) or {}).get("longitud"),
                     "peso_kg": kg}
                    for cid, kg in kg_por_cliente.items() if kg > 0
                ]
                (convrp_groups, convrp_mayoristas_por_ruta, convrp_excepciones,
                 convrp_mayoristas_detalle, convrp_meta) = construir_rutas_con_mayoristas(
                    pedidos_dict, volumenes_dict, coords_dict,
                    vehiculos_cap, obtener_volumenes_vehiculos(), _depot, lista_mayoristas)
            else:
                convrp_groups, convrp_excepciones, convrp_meta = construir_groups_convrp(
                    pedidos_dict, volumenes_dict, coords_dict,
                    vehiculos_cap, obtener_volumenes_vehiculos(), _depot)
            guardar_excepciones_convrp(oid, convrp_excepciones)
            print(f"[convrp] plantilla v{convrp_meta.get('version_plantilla')}: "
                  f"{convrp_meta.get('viajes')} viajes, "
                  f"{len(convrp_excepciones)} excepciones"
                  + (f", {sum(len(v) for v in convrp_mayoristas_por_ruta.values())} mayoristas"
                     if ENGANCHE_ZONA_ACTIVO else ""))
        except Exception as e:  # noqa: BLE001
            import traceback
            convrp_groups = None
            convrp_mayoristas_por_ruta = {}
            print("=" * 70)
            print(f"[convrp] ERROR: {type(e).__name__}: {e}")
            print("[convrp] SE USÓ EL MOTOR DE AFINIDAD, no la plantilla canónica.")
            print(traceback.format_exc())
            print("=" * 70)
            if CONVRP_ESTRICTO:
                raise
```

`pedidos_dict`/`volumenes_dict`/`coords_dict`/`vehiculos_cap` ya existen en scope en ese punto de la función (se usan más abajo, línea 1112 en la versión actual) — no hace falta recalcularlos.

- [ ] **Step 3: Persistir los mayoristas después de que `detalle_por_dia` ya tiene los `ruta_id` reales**

En `logic/historico_logic.py`, justo después de `_guardar_detalle_vrp_en_asignaciones(oid, detalle_por_dia, now_iso)` (línea 1349), agregar:

> **Nota (agregada tras la revisión de Task 4):** el bloque de abajo YA
> incluye dos correcciones encontradas en revisión de código, sobre la
> versión original de este paso: (1) el `if` ya NO exige
> `convrp_mayoristas_por_ruta` truthy -- exigirlo se saltaba el `DELETE`
> dentro de `guardar_mayoristas_convrp` en una semana sin mayoristas y
> dejaba mayoristas viejos persistidos; (2) las sucursales que se pasan a
> `guardar_mayoristas_convrp` ahora llevan `latitud`/`longitud` (desde
> `coords_dict`, ya en scope) -- sin esto, `_ordenar_mayoristas_en_ruta`
> nunca podía calcular el centroide de la ruta y caía siempre al
> desempate más débil.

```python
    if ENGANCHE_ZONA_ACTIVO:
        from logic.mayoristas_logic import guardar_mayoristas_convrp
        rutas_para_guardar = [
            {"_id": rid, "sucursales": [
                dict(s, latitud=coords_dict.get(s["num_tienda"], (None, None))[0],
                     longitud=coords_dict.get(s["num_tienda"], (None, None))[1])
                for s in info["sucursales"]
            ]}
            for dia_key, rutas_dia in detalle_por_dia.items()
            for rid, info in rutas_dia.items()
        ]
        n_may = guardar_mayoristas_convrp(
            oid, convrp_mayoristas_por_ruta, convrp_mayoristas_detalle, rutas_para_guardar)
        print(f"[convrp] mayoristas guardados: {n_may}")
```

- [ ] **Step 4: Verificar que las pruebas existentes de `historico_logic` siguen pasando**

Run: `python -m pytest tests/ -k historico -v`
Expected: todas `PASSED` (el flag está en `False` por defecto, así que el camino existente no cambia).

- [ ] **Step 5: Commit**

```bash
git add logic/historico_logic.py
git commit -m "feat: ENGANCHE_ZONA_ACTIVO — conecta construir_rutas_con_mayoristas a Generar Rutas VRP (apagado por defecto)"
```

---

### Task 5: Sitios de lectura — `logic/asignacion_logic.py`

**Files:**
- Modify: `logic/asignacion_logic.py:529`, `:1036`, `:1676`, `:1741`

- [ ] **Step 1: Import**

Al inicio de `logic/asignacion_logic.py`, junto al import ya existente de `calcular_distribucion_mayoristas` (línea 54), agregar:

```python
from logic.mayoristas_logic import calcular_distribucion_mayoristas, obtener_mayoristas_guardados
```

- [ ] **Step 2: Línea 529** (`calcular_tiempos_multiples_rutas`)

Reemplazar:
```python
            dist = calcular_distribucion_mayoristas(logistica_id, rutas)
```
por:
```python
            dist = obtener_mayoristas_guardados(logistica_id, rutas) or calcular_distribucion_mayoristas(logistica_id, rutas)
```

- [ ] **Step 3: Línea 1036** (`_cargar_datos_mayoristas`)

Reemplazar:
```python
        dist = calcular_distribucion_mayoristas(logistica_id, rutas)
```
por:
```python
        dist = obtener_mayoristas_guardados(logistica_id, rutas) or calcular_distribucion_mayoristas(logistica_id, rutas)
```

- [ ] **Step 4: Línea 1676** (`obtener_mayoristas_por_ruta`) — aquí no se pasa `rutas`; usar `obtener_rutas()` (ya importable en este módulo, es donde vive) para tener con qué reconstruir:

Reemplazar:
```python
        dist = calcular_distribucion_mayoristas(logistica_id)
```
por:
```python
        dist = (obtener_mayoristas_guardados(logistica_id, obtener_rutas())
                or calcular_distribucion_mayoristas(logistica_id))
```

- [ ] **Step 5: Línea 1741** (`obtener_geometria_ruta`) — mismo criterio:

Reemplazar:
```python
        dist = calcular_distribucion_mayoristas(logistica_id)
```
por:
```python
        dist = (obtener_mayoristas_guardados(logistica_id, obtener_rutas())
                or calcular_distribucion_mayoristas(logistica_id))
```

- [ ] **Step 6: Correr la suite completa**

Run: `python -m pytest tests/ -q`
Expected: todas `PASSED` (sin conexión BD real en el entorno de pruebas, `obtener_mayoristas_guardados` puede lanzar si la tabla no existe en el motor de pruebas -- si eso pasa, envolver la llamada en cada sitio con el mismo `try/except` que ya rodea a `calcular_distribucion_mayoristas` en ese bloque, no agregar uno nuevo).

- [ ] **Step 7: Commit**

```bash
git add logic/asignacion_logic.py
git commit -m "feat: asignacion_logic lee mayoristas persistidos primero, cae al cálculo en vivo si no hay"
```

---

### Task 6: Sitio de lectura — `logic/modificacion_logic.py`

**Files:**
- Modify: `logic/modificacion_logic.py:807-813`

- [ ] **Step 1: Import**

Junto al import existente (línea 30):
```python
from logic.mayoristas_logic import calcular_distribucion_mayoristas, _integrar_paradas, obtener_mayoristas_guardados
```

- [ ] **Step 2: Reemplazar la llamada**

Reemplazar:
```python
    dist = calcular_distribucion_mayoristas(
        logistica_id,
        [
            {"_id": rid, "sucursales": sucs, "cap_ton": meta_por_ruta.get(rid, {}).get("cap_ton")}
            for rid, sucs in sucursales_por_ruta.items()
        ],
    )
```
por:
```python
    _rutas_para_mayoristas = [
        {"_id": rid, "sucursales": sucs, "cap_ton": meta_por_ruta.get(rid, {}).get("cap_ton")}
        for rid, sucs in sucursales_por_ruta.items()
    ]
    dist = (obtener_mayoristas_guardados(logistica_id, _rutas_para_mayoristas)
            or calcular_distribucion_mayoristas(logistica_id, _rutas_para_mayoristas))
```

> Nota: el otro uso en este archivo (línea 672, `dist_vacia = calcular_distribucion_mayoristas(logistica_id)`) **no se toca** — vive en la rama que corre cuando todavía no hay ninguna asignación guardada para la logística, así que tampoco puede haber mayoristas persistidos (`convrp_mayoristas` depende de que `generar_rutas_vrp_afinidad` ya haya corrido).

- [ ] **Step 3: Correr la suite**

Run: `python -m pytest tests/ -q`
Expected: todas `PASSED`.

- [ ] **Step 4: Commit**

```bash
git add logic/modificacion_logic.py
git commit -m "feat: modificacion_logic lee mayoristas persistidos primero, cae al cálculo en vivo si no hay"
```

---

### Task 7: Sitio de lectura — `logic/pdf_logic.py`

**Files:**
- Modify: `logic/pdf_logic.py:591`

- [ ] **Step 1: Import**

Junto al import existente (línea 27):
```python
from logic.mayoristas_logic import calcular_distribucion_mayoristas, _insertar_pos_proxima, obtener_mayoristas_guardados
```

- [ ] **Step 2: Reemplazar la llamada**

Reemplazar:
```python
        dist = calcular_distribucion_mayoristas(str(oid), rutas_base)
```
por:
```python
        dist = (obtener_mayoristas_guardados(str(oid), rutas_base)
                or calcular_distribucion_mayoristas(str(oid), rutas_base))
```

- [ ] **Step 3: Correr la suite**

Run: `python -m pytest tests/ -q`
Expected: todas `PASSED`.

- [ ] **Step 4: Commit**

```bash
git add logic/pdf_logic.py
git commit -m "feat: pdf_logic lee mayoristas persistidos primero, cae al cálculo en vivo si no hay"
```

---

### Task 8: Sitio de lectura — `router/asignacion_router.py`

**Files:**
- Modify: `router/asignacion_router.py:147-149`

- [ ] **Step 1: Reemplazar la llamada**

Reemplazar:
```python
        from logic.mayoristas_logic import calcular_distribucion_mayoristas
        dist = calcular_distribucion_mayoristas(lid, [ruta])
```
por:
```python
        from logic.mayoristas_logic import calcular_distribucion_mayoristas, obtener_mayoristas_guardados
        dist = obtener_mayoristas_guardados(lid, [ruta]) or calcular_distribucion_mayoristas(lid, [ruta])
```

- [ ] **Step 2: Correr la suite**

Run: `python -m pytest tests/ -q`
Expected: todas `PASSED`.

- [ ] **Step 3: Commit**

```bash
git add router/asignacion_router.py
git commit -m "feat: asignacion_router lee mayoristas persistidos primero, cae al cálculo en vivo si no hay"
```

---

### Task 9: Verificación en sandbox y activación

**Files:** ninguno nuevo — verificación manual + posible ajuste de `ENGANCHE_ZONA_ACTIVO`.

- [ ] **Step 1: Correr el smoke test (ya ejercita `construir_rutas_con_mayoristas`, es el gate de fidelidad)**

Run: `python scripts/smoke_convrp.py`
Expected: `RESULTADO: OK`, sin fallas nuevas respecto a la última corrida conocida (revisar `RÍGIDOS partidos`, `PICO de utilización con mayoristas`, y el desglose `NUCLEO/SEGUNDO_GRUPO/GEOGRAFIA_RUTA` de la sección `MAYORISTAS`).

- [ ] **Step 2: Crear una logística sandbox** (nunca contra producción — regla dura ya documentada en `convrp_integracion.py`), con extracción cargada (sucursales + mayoristas).

- [ ] **Step 3: Activar el flag temporalmente**

En `logic/historico_logic.py`, cambiar `ENGANCHE_ZONA_ACTIVO = False` a `True`.

- [ ] **Step 4: Generar rutas para la logística sandbox y verificar**

Correr "Generar Rutas VRP" sobre la logística sandbox. Verificar en la consola del servidor:
- `[convrp] plantilla v...: N viajes, M excepciones, K mayoristas` (K > 0 si había mayoristas con pedido esa semana).
- `[convrp] mayoristas guardados: K` (mismo número).

- [ ] **Step 5: Verificar en las 3 vistas que los mayoristas aparecen igual que antes**

Abrir Asignación, generar el PDF, y abrir Modificación para esa logística sandbox. Confirmar que los mayoristas aparecen en las rutas esperadas, sin errores en consola ni mayoristas faltantes respecto a una corrida de referencia con el flag apagado.

- [ ] **Step 6: Revisar la nota de Task 3 sobre `paradas_integradas`**

Si en el PDF o el mapa los mayoristas aparecen agrupados al final de cada ruta en vez de intercalados junto a la sucursal más cercana, aplicar el ajuste de `sorted(...)` descrito en la nota de Task 3, Step 3, y repetir Steps 4-5.

- [ ] **Step 7: Decidir con el usuario si se activa en producción**

Presentar los resultados de Steps 1-6. Sólo cambiar `ENGANCHE_ZONA_ACTIVO = True` de forma permanente (commit aparte) con aprobación explícita — mismo criterio que se usó para `CONVRP_ACTIVO`.

- [ ] **Step 8: Commit final** (si se aprueba activar)

```bash
git add logic/historico_logic.py
git commit -m "feat: activar ENGANCHE_ZONA_ACTIVO en producción"
```

---

## Self-review (cobertura contra el spec)

- §3 punto de disparo → Task 4.
- §3 flag dedicado → Task 4, Step 1.
- §3 persistencia (tabla, mismo patrón que `convrp_excepciones`) → Task 1, Task 2.
- §3 camino de lectura + fallback → Tasks 5-8.
- §3 `sin_asignar`/`MAYORISTA_SIN_CUPO` vía `convrp_excepciones` → ya cubierto: `construir_rutas_con_mayoristas` devuelve esas excepciones dentro de `convrp_excepciones` (mismo `excepciones` que ya se pasa a `guardar_excepciones_convrp` en Task 4, Step 2) — no hace falta un paso aparte.
- §3 degradación ante error → Task 4, Step 2 (`try/except` alrededor de todo el bloque, incluida la rama `ENGANCHE_ZONA_ACTIVO`).
- §5 casos borde (logística sin mayoristas, dependencia de `CONVRP_ACTIVO`, regeneración, modificación manual) → cubiertos por el diseño de Task 2-4 (DELETE+insert, `if ENGANCHE_ZONA_ACTIVO` anidado dentro de `if CONVRP_ACTIVO`, `modificaciones_rutas` no se toca).
- §7 pruebas 1-5 → Tasks 2-3 (tests unitarios), Task 4 Step 4 (no-regresión). §7 punto 6 (gate de fidelidad) y 7 (sandbox) → Task 9.
