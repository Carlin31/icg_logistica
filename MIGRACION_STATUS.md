# Estado de la migración MongoDB → SQL Server

Migrando el acceso a datos de esta app (Flask) de MongoDB Atlas a SQL Server
local, módulo por módulo, preservando el comportamiento actual. Tarea de
prácticas — cada fase se prueba contra la base real antes de pasar a la
siguiente. **Lee este archivo primero en cualquier sesión nueva.**

## ConVRP — Plantilla canónica (iniciativa aparte, en curso)

Convertir el VRP de GENERADOR a AJUSTADOR sobre una plantilla histórica. **No
es parte de la migración**; convive con ella. Detalle vivo del avance por fase
se lleva en la conversación; aquí quedan las decisiones de datos permanentes:

- **Tablas nuevas (versionadas, no destructivas)** — creadas por
  `scripts/crear_plantilla_canonica.py`, cargadas por `scripts/cargar_plantilla.py`
  (usa `logic/plantilla_canonica.py`): `plantilla_meta` (1 fila por versión),
  `plantilla_bridge_sucursal`, `plantilla_grupo`, `plantilla_grupo_sucursal`,
  `plantilla_grupo_dia` (días admisibles por grupo: el grupo sólo se mueve dentro
  de este set, con el día LORES como preferido; rigidez de composición y
  flexibilidad de día son dimensiones independientes), `plantilla_zona_mayorista`,
  `plantilla_poblacion_zona`. Cada carga inserta una
  **versión nueva** y marca la anterior `vigente=0`; **nunca borra**. Los
  lectores devuelven la versión vigente. Semilla del bridge revisada:
  `datos/mapeo_no_a_numtienda.csv`.
- **Llave crítica:** `No. SUCURSAL` (Excel canónico) ≠ `num_tienda` (BD) — son
  numeraciones independientes (sólo 59/101 coinciden por índice). El puente se
  resolvió por coordenada (biyección 101/101, mediana 28 m) y se validó
  reconstruyendo el co-viaje: los **23 grupos rígidos reaparecen (100 %)**. Dos
  pares (`Cd. Isla 1`, `San Andrés 1`) difieren >2 km entre fuentes pero sus
  hermanos coinciden a <50 m → **error de captura de coordenada, no de mapeo**;
  quedan marcados `estado_revision='revisado_ok'` para escalarse con la empresa
  (no tocar la BD de `sucursales`).

- **Motor ConVRP tras flag** — `CONVRP_ACTIVO` en `logic/historico_logic.py`
  (**False = default**, motor de afinidad intacto). Builder puro en
  `logic/convrp_logic.py`, puente a BD en `logic/convrp_integracion.py`,
  excepciones en `convrp_excepciones`. Gate obligatorio tras tocar el builder,
  la plantilla o el catálogo de vehículos: `python scripts/smoke_convrp.py`.

> ⚠️ **Decisiones ABIERTAS antes de invertir el flag** (no cerradas):
> 1. **RESUELTO — el modelo de tiempo queda absuelto; era la estimación de
>    traslado.** El builder calculaba el viaje con haversine a **35 km/h fijos**.
>    Medido contra OSRM sobre **875 tramos reales**, la velocidad
>    haversine-equivalente no es constante: **55.5 km/h** en tramos largos
>    (>50 km, matriz→clúster) y **37.8** en cortos. Con 35 para todo, el tramo
>    matriz→Los Tuxtlas daba 343 min contra **195 min reales** (+2.5 h), lo que
>    hacía violar TIEMPO a un grupo que cabía de sobra. Las palancas 1 y 2 no
>    podían ayudar porque la violación era idéntica en toda unidad y todo día
>    admisible — **el orden unidad→día→partir siempre se respetó**.
>    Corregido con `velocidad_para_km()` en `logistica_tiempo` (dos regímenes
>    calibrados; `por_tramo=True`, sin cambiar el camino del PDF).
>    Efecto: **rígidos partidos 9 → 0**; influencia del chequeo de tiempo de
>    +26 viajes/9 particiones a **+6 viajes/0 particiones**. Viajes/semana
>    27.0 contra 30.8 reales (antes 29.2, pero con 9 particiones espurias).
>    Es la MISMA clase de bug que ya se había corregido en el PDF esta sesión
>    (haversine sobreestimaba matriz→clúster → se pasó a tramos OSRM).
>
> 1-bis. **[HISTÓRICO, ya resuelto]** El modelo de tiempo parte rígidos. El rígido
>    g2 se parte las 9 semanas, pero en el histórico real viajó **completo en 8
>    de 9**. Aislado: el chequeo de tiempo añade +26 viajes (263 vs 237) y causa
>    **el 100 % de los rígidos partidos** (9 vs 0). Que el conteo de viajes
>    (29.2) quede cerca del real (30.8) **no prueba** que parta donde la realidad
>    parte. Traza concreta (1-5 jun): g2 pesa 3438 kg y su `unidad_ref`
>    (`F 350_3`) aguanta 3900 — **cabe de sobra**. Sin chequeo de tiempo viaja
>    completo en `F 350_3/MARTES`, que es exactamente lo que ocurrió en la
>    realidad; con chequeo se parte en `T 23` + `F 350_3`. Ojo: la excepción
>    queda etiquetada `restriccion: PESO`, que es la causa PRÓXIMA (lo que ataba
>    al pelar) y no la raíz — el tiempo actúa **indirectamente**, rechazando al
>    grupo completo en la asignación. No leer esa etiqueta como causa raíz.
>    Corregir el horario del lunes (11:00) **no cambió nada**: los 49 viajes de
>    lunes no tocan el límite de tiempo en ninguno de los tres escenarios.
> 2. **La calibración de tiempo se reabre en Fase 3.** Hoy las rutas del ConVRP
>    topan en 7 paradas (sólo Lores); al enganchar mayoristas crecerán hacia las
>    ~23 paradas de las rutas reales y el piso de 38 min/parada sí va a morder.
>    Volver a correr el aislamiento con-tiempo/sin-tiempo después de Fase 3.
> 3. **Piso de 38 min y coordenadas** (`Cd. Isla 1`, `San Andrés 1`): escalados
>    con la empresa, sin tocar la BD.

> ⚠️ **Llaves: verificar siempre, nunca adivinar.** Dos joins fallaron en
> silencio y ambos se descubrieron de casualidad: `No. SUCURSAL` ≠ `num_tienda`
> (numeraciones independientes) y `unidad_ref` `'F350_2'` ≠ `'F 350_2'` del
> catálogo (**9 de 10 unidades no empataban y la preferencia de unidad se
> ignoraba**). `scripts/smoke_convrp.py` ahora verifica los cuatro joins
> (sucursal, unidad, día, población) al 100 % esperado y falla ruidosamente.
> Ojo con `configuracion.config_dias`: usa minúsculas (`miercoles`) y **el lunes
> sale 11:00, no 07:00** — asumir 07:00 regalaba 4 h.

> ⚠️ **`asignaciones` de "Logística del 1 al 5 de junio del 2026"
> (`6a2c9928ba526866fcad54b3`) NO refleja lo que se operó esa semana.** El
> 2026-08-03 se corrió el motor con `CONVRP_ACTIVO=True` contra ese registro de
> producción para probar el flujo completo; eso sobreescribió sus asignaciones y
> después se regeneró con el motor de afinidad. O sea: ese registro contiene
> **lo que el botón "Generar Rutas VRP" produce hoy**, no la operación real de
> esa semana. No leerlo como histórico.
> Intactas y válidas como verdad de referencia: `rutas_historicas` +
> `rutas_historicas_visitas` (140 filas, confirmada, `cargado_en=2026-06-01`) y
> `modificaciones_rutas` (31 rutas manuales, 101 sucursales + 39 mayoristas,
> `guardado_en=2026-06-23`, que es además la fuente primaria del PDF).
> **Regla:** nunca correr el motor con el flag encendido contra producción; para
> probar, `construir_groups_convrp(...)` corre en memoria y no persiste.

> ⚠️ **`rutas_historicas_visitas` NO es una tabla huérfana — NO limpiar.** Aunque
> ningún endpoint la consulta hoy, es la fuente normalizada (`id_sucursal`=
> `num_tienda`, `vehiculo`, `dia_semana`, `secuencia_visita`, `kg_entrega`) desde
> la que se reconstruyen los grupos de co-viaje y es la **base de la validación
> por origen móvil** del ConVRP (Fase 2/4). Ya tiene las **9 semanas** feb–jun
> con detalle (15-19 jun se cargó desde el `filas` JSON de su registro
> confirmado).

## Estado por fase

| Fase | Archivos | Estado |
|---|---|---|
| 0 — Infraestructura | `db.py`, `db_mongo.py`, `config.py`, `.env.example`, `requirements.txt` | ✅ Completa y probada |
| 1 — Módulos aislados | `logic/auth_logic.py`, `logic/auditoria_logic.py`, `logic/menu_logic.py`, `logic/configuracion_logic.py` | ✅ Completa y probada |
| 2 — Extracción | `router/extraccion_router.py` (+ `logic/extraccion_logic.py`, fix menor) | ✅ Completa y probada |
| 3/4 — Mayoristas + Modificación (fusionadas, ver "Por qué 3+4 se fusionaron") | `logic/mayoristas_logic.py`, `logic/modificacion_logic.py` (2066 líneas) | ✅ Completa y probada |
| 4 — PDF | `logic/pdf_logic.py` | ✅ Completa y probada |
| 5 — Conductor/Seguimiento | `logic/conductor_logic.py` | ✅ Completa y probada |
| 6 — Asignación | `logic/asignacion_logic.py` | ✅ Completa y probada |
| 7 — Histórico (CRUD) | `logic/historico_logic.py` (parte no-VRP) | ✅ Completa y probada |
| 8 — VRP (motor crítico, al final) | `logic/vrp_logic.py`, resto de `historico_logic.py` | ✅ Completa y probada |
| 9 — Scripts | `scripts/*.py` | ✅ Completa y probada |

## 🎉 Migración completa

Las 10 fases (0-9) están terminadas y probadas contra SQL Server real.
`grep -rl "from db_mongo import" --include="*.py" .` en la raíz del
proyecto ya no devuelve **ningún** archivo salvo `db_mongo.py` en sí
mismo — toda la app (`logic/`, `router/`, `scripts/`) corre 100 % sobre
SQL Server. `db_mongo.py` se conserva intacto para rollback, tal como se
pidió al inicio, pero no lo importa ningún módulo real de la app.
`scripts/arreglar_indices_db.py` (que se va a retirar en Fase 9, no
migrar). `db_mongo.py` en sí se conserva intacto para rollback, sin usarse
en el flujo real de la app.

## Cómo quedó `historico_logic.py` (Fase 7 + Fase 8)

Este archivo se migró en dos tandas, a diferencia de los demás (que se
migraron completos de una vez): Fase 7 cubrió el CRUD/reportes simples de
`rutas_historicas` y las geometrías OSRM; Fase 8 cubrió el motor de
generación VRP con histórico (`generar_rutas_vrp_afinidad` + su algoritmo
de afinidad). Durante Fase 7 el archivo tuvo temporalmente **dos
conexiones a la vez** (`get_db` SQL + `get_db_mongo` Mongo, ver commits de
esa fase) — con Fase 8 completa, el import Mongo se eliminó por completo y
el archivo quedó 100 % SQL, como cualquier otro módulo ya migrado.

- **Fase 7**: `listar_rutas_historicas`, `cargar_csv_historico`,
  `eliminar_historico`, `obtener_historicos_como_dfs`, `guardar_en_historico`,
  `sugerir_vehiculos_optimos`, `resumen_historial`, `_consultar_osrm_geometria`
  + `obtener_geometrias_historico` + `stream_geometrias_historico` (usan
  `cache_osrm`, mismo esquema hash que Fase 6). También adelantada en esa
  fase: `_historiales_crudos_sucursales()` (la usa Fase 8, pero como todos
  sus escritores ya eran SQL desde Fase 7, dejarla en Mongo la habría
  vuelto una lectura congelada).
- **Fase 8**: `obtener_reporte_vrp` y `generar_rutas_vrp_afinidad`
  (el algoritmo de afinidad histórica en sí —
  `_dia_preferido_por_nodo`/`_extraer_secuencias_historicas`/
  `_detectar_copias_exactas`/`_elegir_destino_por_peso`/
  `_resolver_sobrecarga_con_afinidad`/`_detectar_historicamente_solos`/
  `_consolidar_aisladas` — es puro, sin `get_db()`, no necesitó cambios).
  `_id_valido(doc_id)` (string) reemplazó por completo a `_parse_oid`
  (`ObjectId`), que se eliminó del archivo por quedar sin llamadores.
- **Arreglo quirúrgico de Fase 7 completado**: el write de `asignaciones`
  ya se había redirigido a SQL en Fase 7 (`_guardar_detalle_vrp_en_asignaciones`,
  ver commits de esa fase). En Fase 8 se completó lo que faltaba: la
  lectura de `extraccion`/`sucursales` y el write de `vrp_reportes`, que
  hasta ahora seguían en Mongo.

## Cómo quedó `vrp_reportes` (Fase 8)

Al mapear el write de `vrp_reportes` encontré que existían más tablas de
las que el código Mongo real llegó a usar. Confirmado contra el historial
de git (commit `468275c`, pre-migración): el único documento real que
`generar_rutas_vrp_afinidad()` escribía era `vrp_reportes` con los campos
`reporte` (lista plana de stats por ruta) + `consolidaciones` (siempre
`[]`) + `lambda_afinidad` + `generado_en` — **sin** `detalle_por_dia`. El
comentario en el código que mencionaba escribir también en "colecciones
_preview para comparar con el algoritmo anterior" nunca se implementó
(confirmado: cero referencias en todo el historial pre-migración).

- **Poblada de verdad**: `vrp_reportes` (fila base) + `vrp_reportes_resumen`
  (fuente='vrp_reportes', normaliza la lista `reporte` — reemplazo completo
  en `transaccion()`, igual patrón que las demás fases). `obtener_reporte_vrp()`
  reconstruye la respuesta desde ahí; la columna JSON `vrp_reportes.reporte`
  queda sin usar (superada por la tabla normalizada, mismo criterio de
  siempre).
- **Creadas en la planeación original pero sin código real que las
  poblara, ni en Mongo ni ahora** (quedan vacías, mismo estado que
  `cache_osrm` antes de Fase 6): `vrp_reportes_rutas`, `_sucursales`,
  `_mayoristas` (no hay `detalle_por_dia` en el documento Mongo real del
  que normalizarlas), `vrp_reportes_afinidad`, `asignaciones_vrp_afinidad_preview`,
  `vrp_reportes_json_invalido` (cero referencias en todo el código
  Mongo pre-migración). Si en el futuro se implementa la comparación
  "algoritmo nuevo vs anterior" que sugiere el nombre de estas tablas,
  ahí es donde debe escribirse.

## Código muerto encontrado en `vrp_logic.py` (Fase 8, no eliminado)

`generate_routes_vrp()` y sus helpers exclusivos (`_consolidate_underloaded`,
`_resolver_sobrecarga_historica`, `_centroide_miembros`, `_vrp_status`,
`_centroid`) están **confirmados sin ningún llamador en todo el repo** —
`generar_rutas_vrp_afinidad()` (el motor real, en `historico_logic.py`) lo
reemplazó por completo, según su propio comentario ("Reemplaza a
generar_rutas_vrp()"). No tienen ninguna llamada a `get_db()`, así que no
bloqueaban la migración ni necesitaban traducirse. No se eliminaron (a
diferencia de `pdf_logic._mayoristas_por_ruta_db()` en Fase 4) porque no se
pidió limpieza de código muerto en esta fase — queda anotado por si se
decide borrar después. `build_template_from_history()` SÍ sigue en uso real
(la llaman `resumen_historial()` y `generar_rutas_vrp_afinidad()`), no es
código muerto.

## ⚠️ Riesgo latente de Fase 7 — resuelto en Fase 8

`vrp_logic.py` leía la colección Mongo `vehiculos` (SQL-only desde Fase 1)
— quedó documentado como riesgo pendiente al cerrar Fase 7. Corregido
ahora junto con el resto del archivo: `obtener_capacidades_vehiculos()`,
`obtener_placas_por_abrev()`, `obtener_info_vehiculos()` ya leen
`vehiculos` desde SQL Server. Se preservó una asimetría del original que
vale la pena recordar: `obtener_capacidades_vehiculos()` lee TODOS los
vehículos (sin filtrar `activo`), mientras que las otras dos sí filtran
`activo=True` — así era también en el Mongo original, no se corrigió.

## Por qué Fase 3 y 4 se fusionaron

`modificacion_logic.py` (Fase 3) importa `calcular_distribucion_mayoristas()`
de `mayoristas_logic.py` (Fase 4 original). Si Fase 3 se migraba a SQL antes
que Fase 4, esa función seguiría leyendo de Mongo colecciones
(`extraccion`, `clientes_mayoristas`, `vehiculos`, `rutas_config`) que Fase
1-2 ya escriben *solo* en SQL Server — quedaría leyendo datos congelados.
Se decidió (con el usuario) adelantar `mayoristas_logic.py` y migrarlo junto
con `modificacion_logic.py`. **Este mismo problema puede repetirse en fases
futuras** — revisar imports cruzados entre módulos antes de migrar cada uno.

## Decisiones de arquitectura

- **SQLAlchemy Core, no ORM.** El patrón actual del código es
  "colección + filtro" fila por fila; Core mapea más directo que declarar
  modelos ORM.
- **`db.py`**: `MetaData().reflect(bind=engine)` una sola vez al arrancar la
  app — refleja las ~45 tablas automáticamente, no hay que declarar objetos
  `Table` a mano. Engine + MetaData se cachean en `app.extensions` (mismo
  patrón que antes con `mongo_client`). API: `get_engine()`, `get_metadata()`,
  `get_table(nombre)` (equivalente a `db['coleccion']` de Mongo), `get_db()`
  (Connection por request vía `flask.g`, aislamiento **AUTOCOMMIT** — cada
  sentencia se confirma sola, igual que cada op de Mongo era atómica).
- **Transacciones explícitas — `db.transaccion()`**: para los patrones
  "borrar todo + insertar todo" (que en Mongo eran un solo `$set` atómico de
  un documento), `get_db()` con AUTOCOMMIT no basta — dos sentencias
  separadas dejarían una ventana de datos a medias y sin rollback si algo
  truena entre medio. `transaccion()` es un context manager
  (`with transaccion() as conn:`) que abre una **conexión propia** del
  Engine dentro de una transacción real (`engine.begin()`): commit
  automático si el bloque termina sin excepción, rollback automático si
  lanza. Todas las lecturas/escrituras de esa unidad atómica deben pasar por
  el `conn` que entrega el `with`, no por `get_db()` (son conexiones
  distintas). Verificado con un `DataError` real forzado a mitad de una
  transacción: confirmó que revierte TODO, no solo la sentencia que falló.
  **Ya aplicado en**: `router/extraccion_router.py::guardar()` y en casi
  todas las funciones de escritura de `logic/modificacion_logic.py`
  (`crear_ruta_manual`, `eliminar_ruta_manual`, `cambiar_dia_ruta`,
  `quitar/agregar_sucursal_a_asignacion`, `quitar/agregar_mayorista_a_ruta`,
  `actualizar_orden_paradas`, `actualizar_rutas_confirmadas`,
  `actualizar_chofer_en_asignacion`, `guardar_modificacion`).
  `actualizar_vehiculo_en_asignacion` es la única excepción a propósito: es
  un solo `UPDATE` de una fila, no necesita transacción.
- **IDs nuevos**: se generan con `str(bson.ObjectId())` — mismo formato de
  24 hex que los `mongo_id` ya migrados. `bson` no necesita conexión Mongo
  real, solo se usa como generador/validador de formato
  (`ObjectId(x)` lanza `InvalidId` si el string no tiene formato válido).
  Patrón repetido en cada archivo migrado: `_id_valido(doc_id)` devuelve el
  string validado o `None`.
- **Búsquedas de texto**: `LIKE` con escape manual de `%`, `_`, `[`
  (helper `_like()`/`_prefijo_like()` en `configuracion_logic.py`) para que
  un carácter literal en la búsqueda no se comporte como comodín. Las
  columnas de texto usan collation `*_CI_AS` (case-insensitive) — no hace
  falta `LOWER()`/`UPPER()` para comparar sin distinguir mayúsculas.
- **Columnas JSON-como-texto**: el script de migración original volcó
  *cualquier* campo dict/list de Mongo como texto JSON crudo en una columna
  `NVARCHAR(MAX)`/`VARCHAR(MAX)`, y **además** creó tablas normalizadas para
  los campos que se pidió normalizar explícitamente (quedan ambas cosas en
  paralelo). Regla seguida en todo el código: **usar siempre la tabla
  normalizada cuando existe**; los campos que se dejaron a propósito como
  JSON de texto (`geometria_osrm_json`, `via_points_json`,
  `puntos_evitar_json` en `modificacion_rutas`; `rutas_historicas.filas` y
  `.dias`) se leen/escriben con `json.loads`/`json.dumps` en Python.
- **`asignaciones_mayoristas_overrides.clave` (texto) vs tipo original
  (documento str / id_cliente int legado)**: SQL solo guarda texto, así que
  se perdió la distinción de tipo que Mongo preservaba de forma nativa.
  Se reconstituye con una heurística verificada contra datos reales (302
  filas inspeccionadas): `clave.isdigit()` → era `int(id_cliente)` legado;
  si no → era `documento` (str). Ver `_clave_a_python()` en
  `modificacion_logic.py`. Se sigue el mismo criterio al escribir
  (`documento if documento else str(int(id_cliente))`).
- **`entregas` + `entregas_historial` (Fase 5)**: en Mongo, el historial de
  una entrega era un array embebido dentro del propio documento de la
  entrega (`$push`); en SQL son dos tablas separadas (`entregas_historial`
  tiene FK lógica `entrega_id → entregas.mongo_id`). Consecuencia práctica:
  borrar filas de `entregas` sin borrar también sus filas de
  `entregas_historial` dejaría huérfanos — algo que en Mongo era imposible
  (un solo documento). Por eso `conductor_logic.cancelar_autorizacion()`
  borra explícitamente ambas tablas en la misma `transaccion()`. Nota: el
  `eliminar_logistica()` de `menu_logic.py` (Fase 1) **no** borra
  `entregas`/`entregas_historial` al eliminar una logística — ese hueco ya
  existía en el Mongo original (confirmado en el historial de git, commit
  `468275c`) y se preservó tal cual, no se corrigió de paso.
- **Escritura atómica de un solo Mongo-op que en SQL son varias
  sentencias** (mismo criterio que motivó `transaccion()` en Fase 3/4):
  `conductor_logic.marcar_entrega()` traduce un único `update_one` con
  `$set`+`$push` (atómico en Mongo) a un SELECT + INSERT/UPDATE de
  `entregas` + INSERT de `entregas_historial` — se envuelve todo en
  `transaccion()`. En cambio `cancelar_autorizacion()` ya era, en el Mongo
  original, dos operaciones separadas (`delete_many` + `update_one`, no un
  solo `$set`) — se tradujo igual, sin forzar atomicidad artificial entre
  ambas (aunque el borrado interno de `entregas`+`entregas_historial` sí se
  agrupa, por la razón normalizada arriba).
- **`asignacion_logic.guardar_asignacion()` (Fase 6) — merge parcial de
  Mongo, preservado sin tabla nueva**: el `$set` original de Mongo era
  `{"$set": {"logistica_id": oid, **payload}}` — un merge de nivel
  superior. El payload real que manda `asignacion.js` NUNCA trae
  `chofer_overrides`/`orden_overrides`/`mayoristas_overrides`/
  `sucursales_pendientes`/`rutas_confirmadas` (esas claves las escribe
  solo `modificacion_logic.py`), así que sobrevivían intactas a cada
  re-guardado de Asignación. Se preserva exactamente: `guardar_asignacion()`
  solo hace DELETE+INSERT (en `transaccion()`) de `asignaciones_rutas`/
  `asignaciones_sucursales`/`asignaciones_mayoristas` — nunca toca las
  tablas de overrides/pendientes/confirmadas. Verificado insertando un
  override falso antes de llamar `guardar_asignacion()` y confirmando que
  sigue ahí después.
- **`asignacion_logic.obtener_asignaciones_previas()` (Fase 6) —
  reconstrucción en vez de columna JSON**: la tabla `asignaciones` no tiene
  columnas para `asignaciones_por_dia`/`dias_programados`/`util_min`/
  `util_max`/`reprogramadas`/`fecha_generacion` (solo tiene la columna
  muerta `detalle_por_dia`, superada por las tablas normalizadas). Se
  comprobó en `asignacion.js` que el frontend solo lee de vuelta
  `asignaciones_por_dia` y `detalle_por_dia` — ambas se reconstruyen leyendo
  `asignaciones_rutas`/`asignaciones_sucursales`; el resto de campos nunca
  se leen de vuelta en ningún flujo real, ni en Mongo ni ahora. Decisión
  confirmada con el usuario antes de implementar.

## Tablas/columnas nuevas creadas sobre la marcha (no estaban en el plan original)

- **`config_dias`** (`config_dias_id BIGINT IDENTITY PK, logistica_id VARCHAR(50), dia VARCHAR(20), habilitado BIT, hora_salida VARCHAR(10), hora_limite VARCHAR(10), UNIQUE(logistica_id, dia)`).
  Único punto de escritura real: `guardar_config_dias()` en
  `asignacion_logic.py` (Fase 6, ya migrada — reemplazo completo dentro de
  `transaccion()`: borra todas las filas de la logística e inserta las
  nuevas). Usada también por `logic/menu_logic.py` (cascade-delete).
- **`cache_osrm`** (`clave_hash CHAR(64)` — SHA-256 hex de la clave original,
  calculado en Python con `hashlib.sha256(clave.encode()).hexdigest()`,
  nunca en SQL —, `clave NVARCHAR(MAX)` — valor original, solo lectura/debug
  —, `tipo VARCHAR(20)`, `resultado NVARCHAR(MAX)` JSON,
  `actualizado_en DATETIME2`, `PK(clave_hash, tipo)`).
  La clave original (concatenación de coordenadas de toda la ruta) podía
  superar el límite de ~900 bytes de un índice de SQL Server — de ahí el
  hash. Primer código real que la usa: `asignacion_logic.py` (Fase 6) —
  `_cargar_cache()`/`_guardar_cache()`, con `tipo="tiempos"` (cálculo de
  tiempos de ruta) y `tipo="geometria"` (polilínea para el mapa). Verificado
  con llamadas reales a OSRM: primera llamada `origen="osrm"` y escribe
  cache; segunda llamada lee el mismo resultado desde `cache_osrm`.
- **`configuracion.radio_mayoristas_km`** (`ALTER TABLE configuracion ADD
  radio_mayoristas_km FLOAT NULL`). Agregada al migrar Fase 6 para poder
  arreglar un bug real (ver abajo) — antes no existía ni en Mongo ni en SQL.
- **`vehiculos.chofer_id`** (`ALTER TABLE vehiculos ADD chofer_id VARCHAR(50) NULL`).
  No es tabla nueva sino columna agregada a una existente — el campo nunca
  tuvo dato real en Mongo así que el script de migración no la creó, pero es
  funcionalidad real usada en 10 archivos. Verificada con
  `configuracion_logic.actualizar_chofer_vehiculo()` y en
  `modificacion_logic.py` (chofer por defecto del vehículo).

## Bugs reales de Mongo corregidos durante la migración (con permiso explícito del usuario)

- **`asignacion_logic._leer_config_volumen()` — `RADIO_MAYORISTAS_KM_DEFAULT`
  nunca estuvo definida en ningún archivo del repo** (ni en el `except` de
  respaldo, que también la referenciaba). Confirmado contra Mongo real que
  `configuracion.radio_mayoristas_km` tampoco existía nunca en el
  documento. Resultado: **`_leer_config_volumen()` lanzaba `NameError` sin
  capturar en cada llamada**, y como se invoca desde
  `generar_asignacion_optimizada()`, el botón "Generar Asignación
  Automática" devolvía 500 siempre — reproducido contra Mongo real antes
  de corregirlo. Se agregó la constante (`RADIO_MAYORISTAS_KM_DEFAULT =
  5.0` km) y la columna `configuracion.radio_mayoristas_km` para que sea
  configurable a futuro. Verificado que `generar_asignacion_optimizada()`
  ya no truena y produce asignaciones válidas.

## Bugs/comportamientos heredados de Mongo, preservados a propósito (no arreglar sin pedir permiso)

- **`asignacion_logic.obtener_geometria_ruta()` → `pesos_may`**: se calcula
  leyendo `extraccion.mayoristas` pero nunca se usa después en la función
  — código muerto ya en el Mongo original (verificado). Se preserva tal
  cual (se sigue calculando, solo que no afecta el resultado).
- **`mayoristas_logic._cargar_historico_mayoristas()`**: condición de carrera
  real en el Mongo original (`orden_por_ruta[ruta_id].get(...)` en el lado
  derecho de la asignación se evalúa ANTES que el `.setdefault(...)` de la
  izquierda → `KeyError` en la primera aparición de cualquier `ruta_id` →
  capturado por un `except Exception: return {}` genérico). Resultado: esa
  función **siempre devolvió `{}` en producción**, el "orden histórico de
  mayoristas" nunca funcionó. El usuario pidió preservarlo tal cual.
- **`mayoristas_logic._persistir_historico_mayoristas()`**: lee un campo
  `asignaciones.asignaciones` (distinto de `detalle_por_dia`) que nunca tuvo
  dato real en ningún documento — no existe columna SQL para él. Se
  preservó como `asignaciones = {}` fijo: la función nunca persiste nada en
  la práctica, ni en Mongo ni ahora.
- **`obtener_rutas_para_modificar()` → `coords_por_ruta`**: en el Mongo
  original está keyed por `rutas_config._id`, pero se compara contra
  `ruta_id` de `detalle_por_dia` (otro espacio de IDs, tipo
  `"vrpaf_..."`/`"manual_..."`) — nunca hace match en la práctica, es un
  fallback muerto. Se preserva tal cual.
- **`pdf_logic._rutas_desde_asignaciones()` → `chofer_override`**: guarda el
  dict crudo `{"nombre":.., "chofer_id":..}` sin extraer `.get("nombre")`;
  si `generar_pdf()` lo usa (`r.get("chofer_override") or ...`), asignaría el
  dict entero como "nombre del chofer" en vez del string. Bug latente del
  original, ruta de código poco frecuente (solo cuando se genera el PDF sin
  pasar por Modificación Y esa ruta tiene un chofer personalizado). Se
  preserva tal cual, no se corrigió.

## Código muerto eliminado (confirmado sin llamadores en todo el repo antes de borrar)

- **`pdf_logic._mayoristas_por_ruta_db()`** — leía la colección
  `distribucion_mayoristas` (que a su vez nunca tuvo escritor real, ver
  hallazgo de Fase 4 en el historial de la conversación). Sin ninguna
  llamada en todo el repo. Eliminada en la migración; queda solo una nota
  en el código explicando por qué no está.

## Qué se probó y cómo (resumen por fase)

- **Fase 0**: conexión real verificada (45 tablas reflejadas), `SELECT` real
  contra `configuracion`, y el mecanismo `transaccion()` verificado con un
  `DataError` real forzado a mitad de una transacción multi-tabla
  (confirmó rollback completo, no parcial).
- **Fase 1**: cada función probada contra datos reales (lecturas) y datos
  desechables limpiados después (escrituras) — nunca se tocaron filas de
  producción. `menu_logic.eliminar_logistica()` probado insertando filas
  sintéticas en las 13 tablas relacionadas y confirmando cascade-delete
  completo sin huérfanos. `actualizar_chofer_vehiculo()` reprobado tras
  agregar la columna `chofer_id`.
- **Fase 2**: `router/extraccion_router.py` probado de extremo a extremo con
  `app.test_client()` (sesión autenticada + logística desechable), incluido
  que el desglose se *reemplaza* (no acumula) en cada guardado, y que
  `guardar()` es atómico (probado con un `DataError` real forzado a mitad
  de la operación).
- **Fase 3/4 (`mayoristas_logic.py`)**: `calcular_distribucion_mayoristas()`
  probada con datos sintéticos aislados (logística, extracción, cliente
  mayorista y ruta de prueba, todo desechable) — confirmado que el mayorista
  se asigna e integra correctamente a la ruta. `_guardar_historico_mayoristas()`
  y `_cargar_historico_mayoristas()` probadas directamente (inserción real +
  lectura), confirmando también que el bug heredado se preserva
  correctamente (siempre devuelve `{}`).
- **Fase 3/4 (`modificacion_logic.py`)**: 13 bloques de prueba con
  logística + 2-3 vehículos desechables, cubriendo: `obtener_rutas_para_modificar`
  (vacío y con datos), `crear_ruta_manual`, `actualizar_vehiculo_en_asignacion`,
  `actualizar_chofer_en_asignacion` (override + limpieza), `quitar/agregar_sucursal_a_asignacion`
  (con recálculo de peso/% utilización), `actualizar_orden_paradas`,
  `cambiar_dia_ruta`, `actualizar_rutas_confirmadas`, `quitar/agregar_mayorista_a_ruta`
  (incluido el caso de sobrecarga que escala automáticamente a un vehículo
  mayor), `guardar_modificacion`/`obtener_modificacion_previa` (round-trip
  completo con geometría OSRM, via_points, sucursales y mayoristas
  anidados, y confirmando que un segundo guardado *reemplaza* el anterior,
  no acumula), y `eliminar_ruta_manual` (cascade-delete de las 3 tablas
  hijas). Todo limpiado al final, verificado sin huérfanos.
- **Fase 4 (`pdf_logic.py`)**: `generar_pdf()` probado por los DOS caminos
  de datos con logística + vehículo + cliente mayorista desechables: (1)
  fallback vía `asignaciones` (sin Modificación guardada) y (2) vía
  `modificaciones_rutas` (con `guardar_modificacion`), este último
  confirmando también el enriquecimiento de peso de mayoristas desde
  `extraccion.mayoristas`. Verificado que el archivo PDF generado existe y
  tiene un tamaño no trivial (>500 bytes) en ambos casos. Reutiliza
  `modificacion_logic.obtener_modificacion_previa()` en vez de duplicar la
  reconstrucción de `rutas_confirmadas` desde las tablas normalizadas.
- **Fase 5 (`conductor_logic.py`)**: 22 aserciones con una logística
  desechable + una `modificacion_rutas`/`_sucursales`/`_mayoristas`
  sintética insertada directamente (simulando una Modificación ya
  guardada), cubriendo: estado sin_autorizar → autorizado → cancelada,
  visibilidad de rutas por chofer (`chofer_id` exacto y rechazo por
  chofer distinto), `obtener_detalle_ruta_chofer` (paradas + rechazo por
  chofer equivocado), `marcar_entrega` con las 3 transiciones reales
  (entregado → cancelada → re-entregado) verificando el historial
  completo en orden cronológico (3 eventos), cancelar una parada nunca
  entregada (error), ruta completa (3/3, `completada`/
  `logistica_completada`), `obtener_seguimiento_logistica`,
  `obtener_historial_entregas_ruta` (nombres resueltos),
  `contar_entregas_logistica`, y `cancelar_autorizacion` confirmando
  tanto el borrado de las 3 entregas como CERO filas huérfanas en
  `entregas_historial` tras el borrado. Todo limpiado al final.
- **Fase 6 (`asignacion_logic.py`)**: 20 bloques de prueba con logística +
  `rutas_config`/`rutas_config_sucursales`/`vehiculos`/`extraccion`
  desechables, cubriendo: `_leer_config_volumen()` sin `NameError` (bug
  corregido), `obtener_rutas()`, `obtener_vehiculos()` (filtro `activo`),
  `obtener_pesos()`/`obtener_volumenes()`, round-trip de `cache_osrm`
  (insert + update), `obtener_config_dias()`/`guardar_config_dias()`
  (override por logística + reemplazo completo, no acumula),
  `guardar_asignacion()` + `obtener_asignaciones_previas()` incluyendo la
  prueba clave del merge parcial (override de Modificación preexistente
  insertado antes de guardar Asignación, confirmado intacto después) y de
  que un segundo guardado reemplaza el detalle de rutas (no acumula),
  `generar_asignacion_optimizada()` end-to-end (Fase 1 + Fase 2 mayoristas
  + Fase 3 reacomodamiento) sin la excepción del bug corregido,
  `obtener_geometria_ruta()` con llamada real a OSRM y verificación de
  cache en la segunda llamada, `calcular_tiempos_ruta()`/
  `calcular_tiempos_multiples_rutas()`, y `obtener_mayoristas_por_ruta()`.
  Todo limpiado al final (incluidas las filas de `cache_osrm` generadas).
- **Fase 7 (`historico_logic.py`, CRUD)**: 10 bloques de prueba con un CSV
  histórico real cargado (`cargar_csv_historico`) usando `num_tienda`
  reales con coordenadas ya existentes en `sucursales`, cubriendo:
  `listar_rutas_historicas` (excluye columna `filas`, encuentra el nuevo),
  `obtener_historicos_como_dfs`, `resumen_historial`, `guardar_en_historico`
  con una logística desechable — confirmando el upsert real (2 llamadas →
  1 sola fila confirmada, no acumula), `sugerir_vehiculos_optimos`,
  `obtener_geometrias_historico` con llamada real a OSRM + verificación de
  cache en la segunda llamada, `stream_geometrias_historico` (SSE,
  eventos start→ruta→done), y `eliminar_historico`. **Prueba de
  integración clave**: se llamó `_guardar_detalle_vrp_en_asignaciones()`
  (el arreglo quirúrgico) directamente con datos sintéticos y se confirmó
  que `asignacion_logic.obtener_asignaciones_previas()` (Fase 6) lee el
  resultado correctamente — prueba real de que el puente Fase 6 ↔ Fase 7
  funciona. Todo limpiado al final (incluidas filas de `cache_osrm`).
- **Fase 8 (`vrp_logic.py` + `generar_rutas_vrp_afinidad`)**: 8 bloques de
  prueba, el más importante de toda la migración por ser el motor
  crítico. Verificado `obtener_capacidades_vehiculos()`/
  `obtener_placas_por_abrev()`/`obtener_info_vehiculos()` contra vehículos
  reales. **Prueba end-to-end completa**: CSV histórico real (3
  sucursales/2 vehículos/2 días) + logística y extracción sintéticas →
  `generar_rutas_vrp_afinidad()` corrido de verdad (algoritmo de afinidad,
  co-ocurrencia, resolución de sobrecarga, todo el pipeline) → confirmado
  que `asignacion_logic.obtener_asignaciones_previas()` (Fase 6) Y
  `obtener_reporte_vrp()` (Fase 8) ven el resultado correcto por sus dos
  caminos de escritura (`asignaciones_rutas`/`_sucursales` y
  `vrp_reportes_resumen`). Corrido dos veces con `lambda_afinidad`
  distinto para confirmar reemplazo completo (no acumulación) en ambos
  destinos. Todo limpiado al final.
- **Fase 9 (`scripts/*.py`, última fase)**: `scripts/crear_usuarios_iniciales.py`
  no tocaba `db`/`db_mongo` directamente — delega por completo en
  `logic/auth_logic.crear_usuarios_iniciales()`, ya SQL desde Fase 1. Solo
  se corrigió el docstring (decía "MongoDB"/"colección", ahora dice "SQL
  Server"/"tabla"). Ejecutado de verdad contra la base real: la tabla
  `usuarios` ya tenía 15 filas, así que corrió el camino "no hace nada"
  (`Ya existen usuarios en la base de datos.`) — comportamiento correcto y
  esperado, no se tocaron datos reales. El camino de creación en sí
  (`crear_usuario`, `generar_password_segura`) ya estaba probado a fondo en
  Fase 1. `scripts/demo_comparativa.py` confirmado sin ninguna referencia a
  `db`/`db_mongo`/mongo — usa solo `logic/vrp_afinidad/*` (puro) y datos
  sintéticos en memoria, no necesitó cambios. `scripts/arreglar_indices_db.py`
  confirmado 100 % específico de Mongo (`drop_index`/`create_index`/
  `partialFilterExpression`, sin equivalente relacional que migrar) —
  **eliminado del repo** (`git rm`, no commiteado — la decisión de retirarlo
  ya estaba pre-aprobada desde el plan original, repetida en cada
  actualización de este archivo).

## Migración terminada — no hay siguiente paso

Las 10 fases están completas. Ideas para después, **no pedidas todavía**:

- Decidir si se quiere popular `vrp_reportes_rutas`/`_sucursales`/`_mayoristas`/
  `vrp_reportes_afinidad`/`asignaciones_vrp_afinidad_preview`/
  `vrp_reportes_json_invalido` (creadas pero sin código que las use — ver
  sección "Cómo quedó `vrp_reportes`") si se retoma la idea original de
  comparar el algoritmo de afinidad contra uno anterior.
  - Revisar y opcionalmente eliminar el código muerto confirmado en
  `vrp_logic.py` (`generate_routes_vrp` y helpers exclusivos — ver sección
  correspondiente).
- Decidir si el proyecto usará un venv formal (ver nota de entorno abajo).
- Cuando se tenga confianza en producción, planear el retiro de `db_mongo.py`
  y las credenciales de MongoDB Atlas (se conservan hoy a propósito, para
  rollback).

## Nota sobre el entorno de pruebas

`sqlalchemy`, `pyodbc` y el resto de `requirements.txt` se instalaron en el
Python global de este entorno (no había venv del proyecto) para poder correr
las verificaciones contra la base real durante la migración. Falta decidir
si el proyecto va a usar un venv formal — no es parte de esta migración pero
quedó pendiente como detalle de entorno.
