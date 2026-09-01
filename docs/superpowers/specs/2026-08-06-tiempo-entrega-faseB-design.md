# Diseño: Tiempo de entrega — Fase B (reubicar paradas FUERA DE HORARIO)

**Fecha:** 2026-08-06
**Estado:** Aprobado (diseño) — pendiente plan de implementación.
**Continúa:** [2026-07-30-tiempo-entrega-faseA-design.md](2026-07-30-tiempo-entrega-faseA-design.md),
cuyo "Fuera de alcance" dejaba explícitamente pospuesta esta fase: *"Mover/reubicar
la parada no entregable a otra ruta o día con cupo y tiempo"*.

## 1. Problema

Fase A (ya en producción, commit `b657ced` y siguientes) calcula la hora de
llegada real a cada parada y marca `entregable_por_tiempo = False` cuando la
llegada supera el cierre del día — el PDF la imprime en rojo como
`· FUERA DE HORARIO`. Pero Fase A **solo detecta y avisa**: la parada se queda
en su ruta original, marcada, y el planeador tiene que arreglarla a mano en
Modificación cada semana.

Caso real observado (logística 27–31 jul 2026): 7 paradas marcadas FUERA DE
HORARIO repartidas en 4 rutas (`F 350_1` martes, `F 350_2` jueves, `K 16`
jueves ×3, `T 17_2` jueves ×2) — algunas en rutas casi al límite de peso/volumen
(97–99 %), otras con margen de sobra (75–76 %), confirmando que el problema es
de **tiempo**, no solo de capacidad.

## 2. Objetivo (Fase B)

Al generar el PDF, cada parada FUERA DE HORARIO se **reubica sola** en otra
ruta que sí tenga cupo y tiempo, guiándose por la afinidad histórica real de
esa sucursal (con qué vehículos/días viajó en las 9 semanas canónicas). El
cambio se **persiste** (no es solo cosmético del PDF impreso), para que el
Portal del Conductor y el seguimiento en tiempo real queden en sync con lo
impreso.

## 3. Decisiones (confirmadas con el usuario)

- **Punto de enganche:** dentro de `generar_pdf()` (`pdf_logic.py`), después de
  construir las rutas y evaluar tiempos (Fase A), **antes** de renderizar.
  Deliberadamente **no** dentro del motor VRP (`generar_rutas_vrp_afinidad`):
  el motor sigue generando/afinando como hoy (incluyendo el rebalanceo
  geográfico y la expulsión de mayoristas por lejanía, ya en producción); Fase
  B es una **pasada de corrección final** sobre el resultado ya armado,
  específica del problema de horario.
- **Persistencia:** Fase B opera sobre la misma lista `rutas` en memoria que ya
  arma `generar_pdf()` (venga de `modificaciones_rutas` o del fallback
  `asignaciones` — ambos exponen la misma forma de diccionario). Tras
  corregir esa lista, se persiste llamando a
  `modificacion_logic.guardar_modificacion()` (reemplazo completo de
  `modificaciones_rutas`, ya usado y probado como snapshot atómico) — si la
  logística venía del fallback `asignaciones` (nunca pasó por Modificación),
  esta llamada crea su primer `modificaciones_rutas`, que pasa a ser la
  fuente que lee `generar_pdf()` la próxima vez. No se reinventa el cálculo
  de `pct_utilizacion`/`peso_kg`: Fase B los recalcula en memoria con la
  misma fórmula que ya usa el resto del sistema antes de guardar.
- **Referencia "canon":** histórico real (`rutas_historicas`, las 9 semanas
  confirmadas), vía las funciones ya existentes en `historico_logic.py`
  (`_historiales_crudos_sucursales()` + `_extraer_secuencias_historicas()`),
  que dan `{num_tienda: {(vehiculo, dia): secuencia_mediana}}`. Se expone un
  wrapper público nuevo, `historico_logic.afinidad_historica_por_sucursal()`,
  para que Fase B (fuera de `historico_logic.py`) pueda usarlas sin tocar las
  funciones privadas existentes. Los nombres de vehículo del histórico y de
  la ruta actual se normalizan (mayúsculas, sin espacios) antes de comparar
  — el proyecto ya tiene un caso confirmado de este mismo bug
  (`'F350_2' != 'F 350_2'`, ver `MIGRACION_STATUS.md`). **No** se usa
  `plantilla_canonica.py` (iniciativa ConVRP, apagada en producción,
  `CONVRP_ACTIVO=False`) — queda fuera de alcance.
- **Elegibilidad de ruta destino**, en este orden:
  1. Candidatas del **mismo día**: tras sumar el peso de la parada movida, la
     ruta destino debe quedar **≤ 85 %** de `capacidad_ton` (`pct_utilizacion
     = peso_kg / capacidad_ton / 1000 × 100`) — el mismo cálculo, por peso,
     que ya usan `agregar_sucursal_a_asignacion`/`quitar_sucursal_de_asignacion`
     y que se imprime como "% RUTA" (total) en el PDF. Este flujo
     (`modificaciones_rutas`/`asignaciones`, el que lee `generar_pdf`) no
     lleva un tope de volumen independiente — el volumen por parada se
     muestra en el PDF solo informativamente. **Y** al re-evaluar tiempo
     (`evaluar_ruta_por_tiempo`, insertando la parada en la posición
     geográficamente más cercana a sus vecinos en esa ruta) **no debe generar
     un nuevo FUERA DE HORARIO** en ninguna parada de la ruta destino.
  2. Si ninguna candidata del mismo día cumple, se repite el mismo chequeo
     contra rutas de **otro día** que el histórico respalde para esa sucursal
     (aparece en `_extraer_secuencias_historicas()` con ese vehículo en otro
     día) — es simplemente ampliar el conjunto de `ruta_key` candidatas a
     otros días; la parada se agrega a las listas `sucursales`/`mayoristas`
     de esa ruta ya existente, sin usar `cambiar_dia_ruta` (esa función mueve
     una ruta **completa** de día, no aplica aquí).
  3. **Último recurso** (nada cumple 85 %/tiempo ni mismo día ni día de
     respaldo): se relaja el tope de 85 % y el chequeo de tiempo, pero
     **sin salir de la lista de candidatas con afinidad histórica** — se
     elige la que quede menos sobrecargada/menos tarde. Nunca se manda la
     parada a una ruta sin relación histórica/geográfica con ella.
- **Inserción:** en la posición de la ruta destino geográficamente más cercana
  a sus vecinos (no simplemente al final), para minimizar el impacto en el
  tiempo de la ruta destino.
- **Mayoristas** (renglones `BB####_CTES...`): siguen el mismo flujo,
  anclados a la afinidad histórica de la sucursal geográficamente más cercana
  dentro de esa misma ruta (mismo criterio de anclaje que usa conceptualmente
  `enganche_zona.py`, sin activar ese motor).
- **Trazabilidad:** ninguna especial — el PDF y la base de datos reflejan
  directamente el resultado final, igual que cualquier edición manual en
  Modificación. No se agrega nota en el PDF ni registro de auditoría nuevo.
- **`rutas_historicas` es de solo lectura** en todo este flujo — Fase B nunca
  escribe ahí (respeta la regla dura ya documentada en `MIGRACION_STATUS.md`
  sobre el corpus canónico).

## 4. Algoritmo

Por cada ruta con paradas FUERA DE HORARIO, procesar las paradas marcadas **en
orden de secuencia**:

1. Tomar la primera parada FUERA DE HORARIO de la ruta.
2. Buscar destino según el orden de elegibilidad (§3).
3. Quitarla de la ruta origen, insertarla en la ruta destino en la posición
   geográficamente más cercana.
4. Re-evaluar tiempo en la ruta **origen** (quitar una parada solo puede
   adelantar la llegada de las que quedan después — nunca la empeora) antes de
   procesar la siguiente parada FUERA DE HORARIO de esa misma ruta.
5. Repetir hasta agotar las paradas FUERA DE HORARIO de todas las rutas de esa
   logística.

**Determinismo:** mismo input (rutas + histórico) produce mismo output —
recorrido en orden estable (orden de secuencia dentro de cada ruta, rutas en
orden estable), sin aleatoriedad.

## 5. Casos borde

- **Sucursal sin historial** (nueva, sin apariciones en las 9 semanas): no hay
  candidatas con afinidad → cae directo al comportamiento de Fase A (se queda
  marcada FUERA DE HORARIO en su ruta original, igual que hoy). No se inventa
  un destino sin base histórica.
- **Ruta origen con una sola parada FUERA DE HORARIO que es además la única
  parada de la ruta:** se mueve igual (no hay razón especial para tratarla
  distinto); si la ruta destino queda vacía no aplica aquí (mover una parada de
  una ruta multi-parada no vacía la ruta origen salvo que fuera la única
  parada — en ese caso la ruta origen queda con 0 paradas, lo cual ya es un
  estado válido que el sistema maneja hoy en Modificación).
- **Empate entre varias candidatas igualmente buenas:** desempate determinista
  por índice/orden estable (mismo criterio que usa `rebalanceo_geografico.py`).
- **Falla del módulo** (excepción inesperada, datos incompletos): envuelto en
  `try/except`; ante cualquier error se conserva el comportamiento de Fase A
  sin mover nada — degradación segura, el PDF se sigue generando.

## 6. Fuera de alcance

- Tocar el motor VRP (`generar_rutas_vrp_afinidad`), el rebalanceo geográfico
  o la expulsión de mayoristas por lejanía — Fase B corre después, sobre el
  resultado ya armado.
- Usar `plantilla_canonica.py` / ConVRP como referencia de afinidad.
- Notas visibles en el PDF o registro de auditoría del movimiento.
- Cambiar el número de vehículos/rutas — solo se mueven paradas entre rutas
  que ya existen esa semana.

## 7. Pruebas

Módulo nuevo, aislado y puro donde sea posible (igual criterio que
`rebalanceo_geografico.py` y `logistica_tiempo.py`):

1. **Reubica dentro del mismo día:** parada FUERA DE HORARIO + ruta hermana del
   mismo día con cupo y tiempo → se mueve; la ruta origen deja de tener esa
   parada marcada.
2. **Respeta el tope de 85 %:** una candidata cabría en tiempo pero superaría
   85 % de peso/volumen tras sumar la parada → NO se elige.
3. **Respeta el tiempo en destino:** una candidata cabe en peso/volumen pero
   al insertar la parada generaría un nuevo FUERA DE HORARIO ahí → NO se
   elige.
4. **Cae a día de respaldo:** ninguna candidata del mismo día cumple, pero una
   de otro día (con afinidad histórica) sí → se mueve ahí.
5. **Último recurso dentro de afinidad:** nada cumple 85 %/tiempo → se elige la
   candidata con afinidad menos mala, nunca una ruta sin relación histórica.
6. **Sin historial → se queda igual que Fase A:** sucursal nueva sin datos en
   `rutas_historicas` → permanece FUERA DE HORARIO en su ruta original.
7. **Persistencia:** tras la reubicación, `modificaciones_rutas`/`asignaciones`
   reflejan el cambio (round-trip real, no solo en memoria para el PDF).
8. **Determinismo/idempotencia:** correr la resolución dos veces sobre el
   mismo resultado ya corregido no genera un segundo movimiento innecesario.
9. **`rutas_historicas` nunca se escribe:** verificado explícitamente (mismo
   criterio de prueba que ya usa el proyecto para proteger el corpus
   canónico).
