# Diseño: conectar el sistema completo de enganche de mayoristas (`enganche_zona.py`)

**Fecha:** 2026-08-10
**Estado:** Aprobado (diseño) — pendiente plan de implementación.
**Contexto:** `logic/enganche_zona.py`, `logic/convrp_integracion.construir_rutas_con_mayoristas()`
y todo lo que dependen de él (`logic/convrp_validacion.py` para calibración,
`scripts/smoke_convrp.py` como gate) ya existen, están probados y comiteados
desde el trabajo de ConVRP — pero nunca se invocan en producción. Hoy la
asignación de mayoristas en vivo la hace `mayoristas_logic.calcular_distribucion_mayoristas()`,
un parche más simple (población → zona → grupo núcleo → ruta si existe esta
semana, si no cae a geografía) añadido el 2026-08-10 para corregir un bug de
mala colocación encontrado durante una presentación real (ver
`MIGRACION_STATUS.md` §2). Ese parche funciona pero es reactivo: decide
mayoristas *después* de que las rutas de sucursales ya están fijas, así que
el peso de mayoristas no puede influir en qué vehículo/día le toca a un
grupo — la razón original por la que se construyó el sistema completo.

## 1. Problema

`calcular_distribucion_mayoristas()` se llama **en vivo, en cada lectura**,
desde 9 sitios (`asignacion_logic.py` x4, `modificacion_logic.py` x2,
`pdf_logic.py` x1, `asignacion_router.py` x1, más el propio guardado de
histórico dentro de la función). Cada vista recalcula desde cero. Limitaciones
frente al sistema completo (`enganche_zona.py` + `construir_rutas_con_mayoristas()`):

- **Reactivo, no integrado:** el peso de mayoristas se decide después de que
  ConVRP ya fijó vehículo/día de las sucursales. El sistema completo resuelve
  ambos por punto fijo (`construir_rutas_con_mayoristas`): el motor ve la
  carga de mayoristas *antes* de decidir, así que un grupo con mucho peso de
  mayoristas puede empujar su propia asignación de vehículo.
- **Cascada de destino más corta:** parche = núcleo-o-geografía. Completo =
  núcleo → segundo grupo frecuente de la zona → ruta geográfica más cercana →
  viaje de mayoristas solo (`resolver_destino_enganche`, 4 niveles).
- **Sin garantía dura de cupo:** el parche depende de `_resolver_sobrecarga_mayoristas`
  (heredada de Mongo, su paso de "buscar alternativa histórica" está roto —
  siempre devuelve vacío, conocido y documentado). El sistema completo tiene
  `reubicar_mayoristas_por_cupo`: otra ruta con hueco → viaje solo → partir
  por folio → `MAYORISTA_SIN_CUPO` explícito. Nunca desaparece un cliente en
  silencio.
- **Confianza histórica sin pesar contra geografía:** `resolver_zona_cliente`
  (completo) cede a geografía cuando la zona histórica tiene poca evidencia
  (`confianza_zona`, <3 semanas) y contradice la cercanía real. El parche usa
  la zona tal cual esté registrada.

## 2. Objetivo

Que `generar_rutas_vrp_afinidad()` (el botón "Generar Rutas VRP", mismo punto
donde ya vive `CONVRP_ACTIVO`) calcule mayoristas con el sistema completo
**en el mismo paso** que las sucursales, y **persista** el resultado. Los 9
sitios que hoy recalculan en vivo pasan a **leer lo persistido**; el cálculo
en vivo (`calcular_distribucion_mayoristas`) se conserva intacto como
*fallback* — nunca se borra, nunca se toca su lógica.

## 3. Decisiones (confirmadas con el usuario)

- **Punto de disparo:** dentro de `generar_rutas_vrp_afinidad()`, en el mismo
  bloque donde hoy se llama `construir_groups_convrp()` bajo `CONVRP_ACTIVO`.
  Se sustituye por `construir_rutas_con_mayoristas()` (ya escrita, firma
  `(pedidos, volumenes, coords, vehiculos_cap, vehiculos_vol, depot,
  mayoristas, max_pasadas=4)`, devuelve `(groups, por_ruta, excepciones,
  detalle, meta)` — no persiste nada por sí misma). El `groups` que produce
  reemplaza al de `construir_groups_convrp` exactamente igual que hoy; `por_ruta`
  y `detalle` son los datos nuevos a persistir.
- **Flag dedicado:** `ENGANCHE_ZONA_ACTIVO` en `historico_logic.py`, junto a
  `CONVRP_ACTIVO`, default `False`. Se activa por separado — puede haber
  ConVRP activo con enganche de mayoristas apagado (estado actual), pero no
  al revés (el sistema completo necesita que `CONVRP_ACTIVO` también esté
  encendido, ya que llama a `construir_groups_convrp` internamente en cada
  pasada del punto fijo). Si `ENGANCHE_ZONA_ACTIVO=False`, el flujo de
  mayoristas no cambia en nada — ni se calcula ni se persiste.
- **Persistencia:** tabla nueva `convrp_mayoristas`, mismo patrón que
  `convrp_excepciones` (`guardar_excepciones_convrp` en `convrp_integracion.py`
  es el precedente directo a copiar): `DELETE` de las filas de esa
  `logistica_id` + reinsertar, una corrida de "Generar Rutas VRP" sustituye
  por completo a la anterior. Columnas: `logistica_id, id_cliente, unidad,
  dia, orden, peso_kg, via_zona, via_destino, generado_en`. `orden` es la
  posición del mayorista dentro de su ruta — calculada con el mismo criterio
  que ya usa `calcular_distribucion_mayoristas` (histórico propio si existe,
  si no proximidad al centroide de la ruta / `_insertar_pos_proxima`), para
  que la parada quede en la misma posición razonable que el parche produciría.
- **Camino de lectura:** función nueva `obtener_mayoristas_guardados(logistica_id,
  rutas)` en `mayoristas_logic.py`, que reconstruye **el mismo dict de salida**
  que devuelve `calcular_distribucion_mayoristas()` (`mayoristas_por_ruta,
  paradas_integradas, orden_sucursales, todos_mayoristas, sin_asignar,
  sin_coords`) a partir de las filas de `convrp_mayoristas` — así los 9
  sitios que ya consumen esa forma no cambian su código más allá de la
  llamada. Devuelve `None` (no un dict vacío) si no hay filas para esa
  `logistica_id`, para distinguir "no hay datos guardados" de "no hay
  mayoristas esta semana".
- **Fallback:** en cada uno de los 9 sitios, el patrón pasa a ser: `dist =
  obtener_mayoristas_guardados(...) or calcular_distribucion_mayoristas(...)`.
  Cubre logísticas generadas con el flag apagado, generadas antes de este
  cambio, o donde `ENGANCHE_ZONA_ACTIVO=False`. El parche actual no se
  elimina ni se modifica.
- **`sin_asignar` / `MAYORISTA_SIN_CUPO`:** las excepciones que produce
  `reubicar_mayoristas_por_cupo` (dentro de `construir_rutas_con_mayoristas`)
  se guardan en `convrp_excepciones` (tabla ya existente, mismo mecanismo que
  las excepciones de sucursales) con su `tipo` propio — el planeador las ve
  igual que ve `MOVIDO_UNIDAD`/`PARTIDO_CAPACIDAD` hoy, en el mismo lugar.
- **Degradación ante error:** igual que ya hace ConVRP con el motor de
  afinidad — si `construir_rutas_con_mayoristas()` lanza una excepción, se
  captura, se registra visiblemente (`print` + traceback, mismo patrón que
  el bloque `CONVRP_ACTIVO` ya tiene), y la corrida sigue sin persistir
  mayoristas nuevos (las vistas caen a `calcular_distribucion_mayoristas`
  en vivo, como si el flag estuviera apagado esa corrida). Nunca debe tirar
  abajo la generación de rutas de sucursales por un fallo en mayoristas.

## 4. Algoritmo (flujo de una corrida con el flag activo)

1. `generar_rutas_vrp_afinidad()` llega al bloque `CONVRP_ACTIVO`.
2. Si `ENGANCHE_ZONA_ACTIVO` también está encendido: arma la lista `mayoristas`
   (mismo formato que ya arma `scripts/smoke_convrp.py` para el smoke test:
   `[{id_cliente, nombre, poblacion, latitud, longitud, peso_kg}, ...]` desde
   `clientes_mayoristas` + pedidos de la extracción de esa semana) y llama
   `construir_rutas_con_mayoristas(...)` en vez de `construir_groups_convrp(...)`.
3. `groups` (sucursales) sigue el camino que ya existe (paso 5 en adelante de
   `generar_rutas_vrp_afinidad`, sin cambios).
4. `por_ruta` + `detalle` pasan por un adaptador nuevo que calcula `orden`
   por mayorista (histórico propio → proximidad) y arma las filas de
   `convrp_mayoristas`.
5. Se guarda: `DELETE` + insert en `convrp_mayoristas`; las excepciones de
   `reubicar_mayoristas_por_cupo` se añaden a lo que ya se guarda en
   `convrp_excepciones` (mismo `guardar_excepciones_convrp`, se le pasa la
   lista combinada).
6. Si algo fallá en 2, 4 o 5: capturar, registrar, seguir sin persistir
   mayoristas (degradación descrita en §3).
7. Cualquier vista que lea mayoristas de esa logística después: `obtener_mayoristas_guardados()`
   encuentra filas → las usa. Si `ENGANCHE_ZONA_ACTIVO` estaba apagado, o
   falló, o es una logística vieja: no hay filas → cae a
   `calcular_distribucion_mayoristas()` en vivo, sin que la vista note la
   diferencia.

## 5. Casos borde

- **Logística sin mayoristas esa semana:** `convrp_mayoristas` queda sin
  filas para esa `logistica_id` tras el `DELETE`+insert (0 filas insertadas,
  no es error). `obtener_mayoristas_guardados` debe distinguir esto de "no
  hay datos guardados" — se resuelve guardando también una marca de que SÍ
  corrió el enganche esa vez (p. ej. una fila en `convrp_excepciones` o un
  campo en `convrp_meta` ya existente vía `meta.get('mayoristas_ok')`), no
  solo inferir por ausencia de filas.
- **`ENGANCHE_ZONA_ACTIVO=True` pero `CONVRP_ACTIVO=False`:** no debe
  ocurrir (validar al arrancar / documentar la dependencia); si pasa, se
  ignora `ENGANCHE_ZONA_ACTIVO` y se sigue con el parche en vivo — nunca se
  intenta correr `construir_rutas_con_mayoristas` sin que ConVRP esté activo.
- **Se regenera la logística dos veces seguidas:** el `DELETE`+insert hace
  que la segunda corrida reemplace completamente a la primera — sin filas
  huérfanas.
- **Modificación manual después de generar:** igual que hoy con sucursales —
  una vez que el planeador mueve algo a mano en Modificación y guarda, el
  snapshot de `modificaciones_rutas` manda (ya es la fuente que lee el PDF
  primero); `convrp_mayoristas` sólo alimenta la primera carga de Asignación/Modificación,
  no compite con ediciones manuales ya guardadas.

## 6. Fuera de alcance

- No se toca `calcular_distribucion_mayoristas()` ni su lógica interna — se
  queda exactamente como está, como fallback permanente.
- No se migra `rutas_historicas`/el histórico de secuencia de mayoristas
  (`_persistir_historico_mayoristas`) — sigue funcionando igual, es un
  mecanismo distinto (aprendizaje de orden dentro de ruta, no asignación
  ruta↔mayorista).
- No se decide en este documento el valor final de `max_pasadas` del punto
  fijo (queda el default de la función, `4`, salvo que el smoke test
  extendido muestre que hace falta ajustarlo).
- No se cambia el formato de `modificaciones_rutas` ni cómo el PDF prioriza
  snapshot sobre vivo — ese comportamiento ya existente no cambia.

## 7. Pruebas

1. **`obtener_mayoristas_guardados` con filas:** reconstruye correctamente
   `mayoristas_por_ruta`/`orden_sucursales`/etc. a partir de filas sintéticas
   de `convrp_mayoristas`.
2. **`obtener_mayoristas_guardados` sin filas → `None`:** los 9 sitios caen a
   `calcular_distribucion_mayoristas` (verificar con un mock/spy que se
   llama).
3. **Guardado idempotente:** correr el guardado dos veces con datos distintos
   para el mismo `logistica_id` → sólo quedan las filas de la segunda corrida.
4. **Degradación ante error:** forzar una excepción dentro de
   `construir_rutas_con_mayoristas` (mock) → `generar_rutas_vrp_afinidad`
   sigue completando sucursales, no persiste mayoristas, no revienta.
5. **`ENGANCHE_ZONA_ACTIVO=False`:** el flujo es idéntico al actual, ninguna
   tabla nueva se toca.
6. **Gate de fidelidad (reutilizado):** `scripts/smoke_convrp.py` ya corre
   `construir_rutas_con_mayoristas()` contra las 9 semanas confirmadas y mide
   NUCLEO/SEGUNDO_GRUPO/GEOGRAFIA_RUTA/pico de utilización/viajes-solo-mayoristas
   — se corre antes de activar el flag en producción, mismo criterio que se
   usó para `CONVRP_ACTIVO`.
7. **Verificación con datos reales:** contra una logística sandbox (nunca
   producción directa, regla dura ya documentada en `convrp_integracion.py`),
   generar con el flag activo y confirmar en Asignación/PDF/Modificación que
   los mayoristas aparecen igual que antes (sin regresión visible) y que al
   menos un caso con carga de mayoristas real influye en la elección de
   vehículo de su grupo (verificable comparando contra una corrida con el
   flag apagado).
