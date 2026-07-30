# Diseño: Rebalanceo geográfico de rutas (la geografía gana sobre el histórico)

**Fecha:** 2026-07-30
**Estado:** Aprobado (diseño) — pendiente plan de implementación

## 1. Problema

El VRP actual (`generar_rutas_vrp_afinidad`) forma rutas **reproduciendo el histórico**
de rutas confirmadas. Como consecuencia, una ruta puede agrupar sucursales
geográficamente lejanas entre sí (ejemplo real observado: la ruta F 350_1 del
martes agrupa `Cosamaloapan 1/2/3` — un clúster compacto — junto con
`Tierra Blanca` y `Amatitlán`, que quedan lejos). Estos "outliers" alargan el
recorrido y desplazan a otros pedidos (p. ej. mayoristas de Cosamaloapan que no
caben y se reparten a otro día).

Se quiere que, a partir de ahora, **la cercanía geográfica entre sucursales tenga
prioridad sobre el patrón histórico** al armar las rutas.

## 2. Objetivo y requisitos (decididos con el usuario)

- **Geografía gana sobre histórico:** las rutas deben quedar geográficamente
  compactas, aunque eso rompa el patrón histórico de agrupación.
- **Conservar el día de cada sucursal:** cada cliente sigue recibiendo en su
  mismo día de la semana. El rebalanceo ocurre **dentro de cada día**.
- **Mismo número de rutas por día** que hoy: solo se reacomoda *qué sucursal va
  en cuál* ruta; no se crean ni eliminan rutas.
- **Peso y volumen como límites duros:** una ruta nunca puede exceder ni los kg
  ni los m³ del vehículo asignado.
- **Mantener el sistema funcionando:** cambio acotado, reversible, con
  degradación segura.

## 3. Enfoque elegido

**Enfoque A — Búsqueda local** sobre las rutas que ya genera el VRP.
Se parte de las rutas actuales y se aplican movimientos que reducen la dispersión
geográfica sin violar capacidad, hasta que ningún movimiento mejore.

Descartados: (B) re-clustering desde cero por día — más disruptivo y menos
estable; (C) híbrido B+A — más complejo. A encaja con "mantener el mismo número
de rutas y solo reacomodar sucursales", es de bajo riesgo y es fácil de probar.

## 4. Arquitectura e integración

- **Módulo nuevo y aislado:** `logic/vrp_afinidad/rebalanceo_geografico.py`,
  con una única función pública:

  ```
  rebalancear_por_geografia(rutas, coords, pesos, volumenes,
                            cap_peso_por_ruta, cap_vol_por_ruta) -> rutas
  ```

- **No reescribe el VRP.** Se engancha como **paso posterior** dentro de
  `generar_rutas_vrp_afinidad`, después de formar las rutas y resolver
  sobrecargas, y **antes** de secuenciar/guardar.
- **Opera por día:** agrupa las rutas por día y rebalancea cada día por
  separado. Una sucursal **nunca** cambia de día.
- **Nunca crea ni elimina rutas** → el número de rutas por día se conserva por
  construcción.
- **Interruptor de activación** (constante/parámetro `REBALANCEO_GEOGRAFICO`):
  apagarlo restaura el comportamiento actual idéntico y permite comparar.

## 5. Algoritmo (búsqueda local)

**Función de costo (compactación) a minimizar:**

```
costo = Σ_rutas Σ_sucursales  haversine(sucursal, centroide(ruta))
```

Menor costo = rutas más apretadas. Un outlier lejano infla el costo de su ruta;
sacarlo lo reduce.

**Movimientos (siempre respetando peso+volumen y sin cambiar de día):**

1. **Reubicar:** mover una sucursal de la ruta A a la B si (a) B no excede peso
   ni volumen al recibirla y (b) el costo total baja.
2. **Intercambiar:** permutar una sucursal de A con una de B si ambas siguen
   cabiendo (peso y volumen) y el costo total baja. Resuelve casos donde las
   rutas están llenas y una reubicación simple no cabe, pero un swap sí mejora.

**Ciclo:** en cada iteración se evalúan los movimientos posibles y se aplica el
que más reduce el costo; se repite hasta que ningún movimiento mejore o se
alcance un tope de iteraciones (salvaguarda anti-bucle, p. ej. 500).

**Garantías por construcción:**

- El costo nunca sube (solo se aceptan movimientos que mejoran) → resultado ≥
  tan compacto como la entrada; en el peor caso, idéntico a la entrada.
- Nunca viola peso ni volumen (se valida antes de aceptar cada movimiento).
- Nunca crea/elimina rutas ni cambia días.
- **Determinista:** recorrido en orden estable, desempate por índice → mismo
  input produce mismo output (no varía entre corridas).

Encuentra un óptimo **local** (no garantiza el global), suficiente para sacar los
outliers evidentes, que es el objetivo.

## 6. Flujo de datos

**Entradas** (todas ya disponibles en `generar_rutas_vrp_afinidad` en el punto
de enganche):

- `rutas`: rutas ya formadas (cada una con sus `num_tienda` y su día).
- `coords`: `{num_tienda: (lat, lon)}` — ya cargado.
- `pesos`: `pedidos_dict` `{num_tienda: kg}` — ya cargado.
- `volumenes`: `volumenes_dict` `{num_tienda: m³}` — ya cargado.
- `cap_peso_por_ruta`: capacidad efectiva en kg del vehículo de cada ruta
  (`capacidad_efectiva_kg`, ya se calcula).
- `cap_vol_por_ruta`: volumen m³ del vehículo. **Dato nuevo a exponer:** hoy
  `obtener_capacidades_vehiculos` devuelve solo peso; se leerá también
  `volumen_m3` del vehículo (columna existente en `vehiculos`). Lectura extra,
  sin alterar la existente.

**Salida:** la misma estructura de `rutas`, con las sucursales reasignadas entre
rutas del mismo día. El resto del pipeline (asignación de vehículo,
secuenciación por OSRM, distribución de mayoristas, guardado) queda **igual**.

**Efecto colateral positivo:** como los mayoristas se distribuyen después por
cercanía al centroide de la ruta, al quedar las rutas más compactas los
mayoristas caerán mejor, sin tocar ese módulo.

## 7. Casos borde y seguridad

- **Sucursal sin coordenadas:** no se puede medir distancia → se queda fija en
  su ruta actual; no bloquea el rebalanceo de las demás; se loguea.
- **Día con una sola ruta:** no hay a dónde mover → no-op.
- **Rutas ya llenas:** si ninguna reubicación cabe, se intenta intercambio; si
  nada mejora sin violar capacidad, se dejan como están. Nunca fuerza
  sobrecarga.
- **Empeorar el resultado:** imposible por construcción (solo movimientos que
  bajan el costo).
- **Falla del módulo:** el paso va envuelto en `try/except`; ante cualquier
  error se devuelven las rutas **originales sin rebalancear** y se loguea → el
  VRP sigue como hoy (degradación segura).
- **Interruptor apagado:** retorna las rutas tal cual (comportamiento actual).

## 8. Pruebas

Módulo puro (sin BD, sin OSRM) → probado con datos sintéticos, integrado a
`tests/` (hoy 34/34 en verde):

1. **Saca el outlier:** clúster compacto + 1 sucursal lejana que cabe en ruta
   vecina → se mueve; el costo baja.
2. **Respeta peso:** el movimiento excede kg del destino → NO se aplica.
3. **Respeta volumen:** cabe en peso pero excede m³ → NO se aplica.
4. **Intercambio:** dos rutas llenas donde un swap mejora y ambas caben → se
   aplica.
5. **Sin coordenadas:** sucursal sin lat/lon → se queda fija, no rompe.
6. **No empeora / idempotente:** correr dos veces da el mismo resultado; costo
   final ≤ inicial.
7. **Invariantes:** mismo número de rutas, mismos días, mismo conjunto total de
   sucursales (nada se pierde ni duplica).

## 9. Fuera de alcance

- Reasignar días por geografía (se conserva el día histórico de cada sucursal).
- Cambiar el número de rutas por día.
- Reescribir el núcleo del VRP de afinidad o el algoritmo Clarke-Wright.
- Cambiar la distribución de mayoristas, la secuenciación OSRM o el guardado.
- Optimización global garantizada (se acepta óptimo local).
