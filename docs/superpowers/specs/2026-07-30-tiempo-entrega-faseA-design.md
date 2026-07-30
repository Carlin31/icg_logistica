# Diseño: Tiempo de entrega — Fase A (modelo correcto + detección)

**Fecha:** 2026-07-30
**Estado:** Aprobado (diseño). Fase A de 2. La Fase B (reubicar a otra ruta/día
la parada no entregable) queda pospuesta.

## Problema

La regla real de operación: **la hora límite del día (p. ej. 20:00) es cuando
las tiendas CIERRAN sus puertas** y ya no reciben producto; hay que **llegar a
cada parada antes del cierre**. Además, **cada parada toma un tiempo
considerable** (trámites + descarga), no los minutos que hoy calcula el sistema.

El modelo de tiempo actual (`calcular_tiempos_ruta`) no refleja esto:
1. Compara la **hora de regreso a la matriz** contra el límite, no la **llegada
   a cada parada**.
2. Calcula la descarga como `peso × 0.1 min/kg` (tope 120), que subestima mucho
   (Amatitlán, 165 kg → 16.5 min, cuando en la práctica son ~1-2 h).

Resultado: rutas que la operación descarta "por tiempo" el sistema las aprueba
(caso F 350_1 martes con Amatitlán: el sistema dice regreso 19:46 OK, pero con
el modelo real no se alcanza a entregar Amatitlán antes del cierre).

## Objetivo (Fase A)

Calcular el tiempo con el modelo real y **detectar/marcar** las paradas cuya
**hora de llegada supere la hora de cierre**. Fase A **solo detecta y avisa**;
no mueve paradas (eso es Fase B).

## Decisiones (confirmadas con el usuario)

- **Descarga por parada**: proporcional al peso con piso y techo.
  `descarga = clamp(piso + peso_kg × TASA, piso, techo)`.
  - Sucursales: **piso 60 min, techo 120 min**.
  - Mayoristas: **piso 90 min, techo 120 min** (valor literal indicado; constante
    fácil de ajustar si se quería 60).
  - `TASA = 0.05 min/kg` (constante ajustable; con piso 60 un pedido de ~1200 kg
    llega al techo).
- **Deadline = llegada a cada parada** antes de `hora_limite` (cierre de tienda).
- **Acción (Fase A)**: marcar cada parada como `entregable_por_tiempo = False` si
  su hora de llegada > cierre; exponer la marca y la hora de llegada. No se
  mueve ni se elimina nada.
- **Interruptor** `TIEMPO_ENTREGA_ESTRICTO` (constante de módulo). Apagado =
  comportamiento anterior. **Degradación segura** ante errores.

## Diseño

**Módulo nuevo y puro** `logic/logistica_tiempo.py` (sin BD ni OSRM):

- `tiempo_descarga_min(peso_kg, es_mayorista) -> float`
  Aplica el modelo piso+peso×tasa con clamp por tipo.
- `evaluar_llegadas(paradas, tramos_min, hora_salida_min, hora_limite_min) -> list`
  Recibe las paradas en orden, la duración de cada tramo (matriz→p1, p1→p2, …)
  y las horas en minutos. Devuelve, por parada, la **hora de llegada** acumulada
  (`salida + Σ tramos hasta ella + Σ descargas de las previas`) y el flag
  `entregable_por_tiempo` (`hora_llegada <= cierre`). Función pura y determinista.

**OSRM por tramos:** extender `consultar_osrm` para incluir
`"tramos_min": [dur_por_leg,…]` leyendo `routes[0].legs[i].duration` (OSRM ya lo
devuelve; no cambia la query). Retrocompatible: quien no lo use, lo ignora.

**Enganche:** al calcular los tiempos de una ruta, si `TIEMPO_ENTREGA_ESTRICTO`,
usar el nuevo modelo de descarga y `evaluar_llegadas` para anexar
`hora_llegada` y `entregable_por_tiempo` a cada parada, y marcar la ruta con el
número de paradas no entregables. Se expone en el reporte/PDF/Modificación como
señal (sin mover nada).

## Fuera de alcance (Fase B)

- Mover/reubicar la parada no entregable a otra ruta o día con cupo y tiempo.
- Cambiar la formación de rutas por tiempo.

## Pruebas

`logistica_tiempo.py` es puro → pruebas con datos sintéticos:

1. **Descarga clamp**: peso 0 → piso; peso enorme → techo; intermedio →
   piso+peso×tasa. Sucursal vs mayorista usan piso distinto.
2. **Llegada acumulada**: 3 paradas con tramos y descargas conocidas → horas de
   llegada correctas.
3. **Detección**: una parada cuya llegada cae después del cierre → flag False;
   las anteriores → True.
4. **Determinismo**: mismos datos → mismo resultado.
