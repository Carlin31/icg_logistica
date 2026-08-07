# Diseño: Fase B v2 — reubicación guiada por grupos rígidos (ConVRP)

**Fecha:** 2026-08-07
**Estado:** Aprobado (diseño) — pendiente plan de implementación.
**Continúa/reemplaza:** [2026-08-06-tiempo-entrega-faseB-design.md](2026-08-06-tiempo-entrega-faseB-design.md).
Esa versión ya está en producción (`logic/tiempo_reubicacion.py`,
`resolver_fuera_de_horario`); este documento describe **cómo cambia su
fuente de datos y su algoritmo de selección de destino**, no un módulo
nuevo desde cero. `TIEMPO_REUBICACION_ACTIVA` (el interruptor dedicado de
Fase B) queda en `False` — ya se apagó el 2026-08-07 tras el bug descrito
abajo — hasta que esta v2 esté implementada y probada.

## 1. Problema encontrado en producción

Fase B v1 elegía destino con `historico_logic.afinidad_historica_por_sucursal()`:
pares sueltos `(vehículo, día)` sacados del histórico, con la posición
mediana como único dato adicional — **sin distinguir si la sucursal viajó
ahí sistemáticamente cada semana o una sola vez, de casualidad**.

Caso real (logística 27–31 jul 2026, `T 20 · NEMECIO`, lunes): Fase B movió
"Tierra Blanca 7" (`num_tienda` 76) a la ruta de T 20 lunes, junto a Monte
Blanco / Chocamán / Coscomatepec — geográficamente sin relación. Verificado
contra `plantilla_grupo` (datos ya validados del trabajo de ConVRP, ver
`MIGRACION_STATUS.md`): Tierra Blanca 7 pertenece al **grupo 30**, RÍGIDO,
cuya única pareja de co-viaje real es la sucursal 77 — nunca viajó con las
sucursales que sí forman la ruta de T 20 lunes. T 20 sí aparece en
`unidades_afines` del grupo (2 de 9 semanas), pero como vehículo — nunca
junto a esas paradas específicas. Fase B v1 no tenía forma de distinguir
esto: trataba una coincidencia de 1 semana igual que una de 7.

Un segundo caso, más sutil, confirmó el patrón durante la corrección manual
de este mismo incidente: al reubicar "Amatitlán" (`num_tienda` 100, grupo
19, FLEXIBLE, cohesión 0.67) a `T 25 · jueves` por tener afinidad histórica
real (1 de 9 semanas) y pasar cupo+tiempo, quedó separada de su pareja real
de grupo (`num_tienda` 86, "Carlos A. Carrillo 2"), que se quedó en
`F 350_1 · martes` — el hogar dominante del grupo (7 de 9 semanas). Un
movimiento "correcto" por las reglas de v1 (afinidad real + cupo + tiempo)
igual reprodujo, a menor escala, el mismo defecto de fondo: preferir
cualquier coincidencia histórica sobre la dominante.

## 2. Objetivo (v2)

Mismo objetivo funcional que v1 (Fase A marca, Fase B reubica y persiste),
pero la fuente de verdad para "¿esta sucursal pertenece a esta ruta?" pasa
de pares sueltos `(vehículo, día)` a los **grupos de co-viaje ya
reconstruidos y verificados al 100 %** del trabajo de ConVRP
(`plantilla_grupo` / `plantilla_grupo_sucursal` / `plantilla_grupo_dia`,
leídos vía `logic.plantilla_canonica.obtener_grupos()`, ya existente — no
se activa `CONVRP_ACTIVO`, solo se lee esa tabla de referencia).

`historico_logic.afinidad_historica_por_sucursal()` deja de ser llamada por
Fase B. No se elimina (podría tener otros usos futuros), pero queda sin
consumidor en este módulo.

## 3. Decisiones (confirmadas con el usuario)

- **Fuente de verdad:** `plantilla_canonica.obtener_grupos()` — devuelve
  `[{grupo, rigidez, dia, tam, cohesion, unidad_ref, unidades_afines,
  sucursales:[num_tienda], dias_admisibles:[dia] (preferido/canónico
  primero), dia_preferido}, ...]`. Se construye una vez por resolución un
  índice inverso `num_tienda -> grupo` (dict) a partir de esa lista.
- **`unidades_afines`** es un string `"VEH:conteo | VEH:conteo | ..."`
  (ej. `"T 23:3 | K 16:2 | T 20:2 | T 17_1:1 | T 25:1"`). Se parsea a
  `[(vehiculo_normalizado, conteo), ...]` ordenado por conteo descendente
  — el vehículo dominante primero. Mismo normalizador ya existente
  (`_normalizar_veh`, mayúsculas sin espacios) para comparar contra
  `ruta.vehiculo_abrev`.
- **Sucursal sin grupo** (no está en `plantilla_grupo_sucursal` — fuera de
  las 101 de la plantilla canónica): mismo comportamiento que "sin
  historial" en v1 — se queda marcada FUERA DE HORARIO, no se mueve.
- **Grupo RÍGIDO:** al reubicar cualquiera de sus miembros fuera de
  horario, se mueve **el grupo completo junto** — nunca se separa una
  pareja/trío rígido. Si el destino elegido no tiene cupo+tiempo para
  **todos** los miembros del grupo (evaluados juntos, no uno por uno), ese
  destino se descarta y se prueba el siguiente candidato de la lista de
  `unidades_afines`; si ninguno alcanza para el grupo completo, el grupo se
  queda donde está, marcado.
- **Grupo FLEXIBLE:** se mueve **solo la parada marcada** — la cohesión
  `<1.0` indica que históricamente no siempre viajaron juntos, así que no
  se fuerza a compañeros que hoy sí llegan a tiempo a moverse también.
- **Orden de búsqueda de destino** (reemplaza el "mismo día → otro día →
  último recurso" de v1):
  1. Recorrer `unidades_afines` del grupo en orden de conteo descendente
     (dominante primero). Para cada vehículo candidato (excluyendo el de
     la ruta origen), recorrer `dias_admisibles` del grupo en su orden
     (preferido/canónico primero).
  2. Para cada combinación `(vehículo, día)`, si existe una ruta real esa
     semana con ese vehículo y día: verificar cupo (`≤ 85 %` de
     `capacidad_ton`, mismo cálculo que v1) **y** que insertar la
     parada (o el grupo completo, si es rígido) no genere un nuevo FUERA
     DE HORARIO en esa ruta destino. Primera combinación que pasa ambos
     chequeos → es el destino, se mueve y se termina.
  3. Si ninguna combinación de `unidades_afines` pasa cupo+tiempo: modo
     "menos malo dentro del grupo" — de las rutas que **sí existen** esta
     semana para algún `(vehículo, día)` de `unidades_afines` (aunque no
     pasen el chequeo perfecto), elegir la que quede con menor `%` de
     utilización resultante tras insertar. **Nunca** se sale de la lista
     de `unidades_afines` del grupo — no hay "última opción" libre como en
     v1 §3.3, que permitía relajar cupo/tiempo pero mantenía una noción más
     laxa de "afinidad"; aquí la limitación es más estricta: solo
     vehículos con presencia real en el histórico del grupo.
  4. Si ningún vehículo de `unidades_afines` tiene ruta real esta semana en
     ningún día admisible: no se mueve nada, se queda marcada FUERA DE
     HORARIO (idéntico al caso "sin grupo").
- **Mayoristas:** sin grupo propio (`plantilla_grupo_sucursal` es solo de
  `num_tienda`). Se anclan a la sucursal geográficamente más cercana **de
  la misma ruta origen** (mismo criterio de anclaje conceptual que v1); si
  esa sucursal ancla tiene grupo, el mayorista sigue el mismo candidato
  `(vehículo, día)` que se elija para ella. Si no hay sucursales en la
  ruta origen o la más cercana no tiene grupo, el mayorista se queda
  marcado, sin mover.
- **Inserción, persistencia, determinismo, degradación segura ante error,
  `rutas_historicas` de solo lectura:** sin cambios respecto a v1 (§3, §4
  del documento anterior) — solo cambia la lógica de **elegibilidad de
  destino**, no el resto del pipeline (quitar/insertar por posición
  geográfica, `guardar_modificacion()`, envoltura `try/except`).

## 4. Algoritmo (resumen de lo que cambia en `resolver_fuera_de_horario`)

Por cada ruta con paradas FUERA DE HORARIO, procesar en orden de secuencia:

1. Tomar la primera parada FUERA DE HORARIO.
2. Resolver su grupo (directo si es sucursal; por ancla si es mayorista).
3. Si no hay grupo → dejar marcada, siguiente parada.
4. Si hay grupo → determinar el conjunto a mover (el grupo completo si es
   RÍGIDO y hay más miembros; solo la parada si es FLEXIBLE o el grupo
   tiene un solo miembro).
5. Buscar destino con el orden de §3 (dominante → menos dominante,
   canónico → resto de días admisibles; luego "menos malo dentro del
   grupo"; si nada, no mover).
6. Si hay destino: quitar el conjunto de la ruta origen, insertarlo en la
   ruta destino (posición geográfica más cercana, igual que v1).
7. Re-evaluar tiempo en la ruta origen antes de procesar la siguiente
   parada FUERA DE HORARIO de esa misma ruta.
8. Repetir hasta agotar todas las rutas de la logística.

## 5. Casos borde

- **Grupo rígido de tamaño 1:** se comporta igual que uno flexible (no hay
  compañero que forzar).
- **Grupo rígido donde el compañero YA está fuera de horario también:** se
  procesan juntos en el mismo movimiento (se evalúa el destino para el
  grupo completo desde el principio, no dos veces).
- **Grupo rígido donde el compañero está en OTRA ruta** (no la de origen):
  no debería ocurrir si el histórico es consistente, pero si pasa, se
  mueve únicamente el/los miembro(s) presentes en la ruta origen — no se
  va a buscar ni tocar al compañero en su ruta actual.
- **`unidades_afines` vacío o mal formado:** se trata como "sin
  candidatos" — se queda marcada (degradación seguro, mismo criterio que
  "sin grupo").
- **Empate de conteo entre dos vehículos en `unidades_afines`:** desempate
  determinista por orden de aparición en el string (estable).
- **Falla del módulo:** igual que v1 — `try/except`, ante cualquier error
  se conserva el comportamiento de Fase A sin mover nada.

## 6. Fuera de alcance

- Igual que v1 (§6): no toca el motor VRP, no usa notas en PDF ni
  auditoría, no cambia número de vehículos/rutas.
- No se activa `CONVRP_ACTIVO` — solo se **lee** `plantilla_grupo` como
  tabla de referencia, igual que ya hace `obtener_grupos()` para otros
  fines dentro del propio trabajo de ConVRP.
- No se corrige ni se re-versiona la plantilla canónica — se usa la
  versión `vigente` tal cual está.
- No se decide aquí qué hacer con `afinidad_historica_por_sucursal()`
  (dejarla o eliminarla) — queda para una limpieza posterior si se
  confirma que no tiene otros consumidores.

## 7. Pruebas

Reescribir `tests/test_tiempo_reubicacion.py` sobre el nuevo modelo
(los tests de mecánica pura ya existentes — `_paradas_ordenadas`,
`_insertar_en_ruta`, `_quitar_de_ruta`, `_recalcular_peso_ruta`,
`evaluar_ruta_completa` — no cambian, se conservan):

1. **Grupo rígido se mueve completo:** dos sucursales del mismo grupo
   rígido, una fuera de horario; el destino con cupo+tiempo para ambas se
   elige y **las dos** aparecen ahí.
2. **Grupo rígido sin cupo para ambas → no se mueve ninguna** a ese
   destino, se prueba el siguiente candidato.
3. **Grupo flexible mueve solo la marcada:** compañero de grupo flexible
   que va a tiempo se queda en su ruta original.
4. **Orden por frecuencia:** con dos vehículos candidatos en
   `unidades_afines` (uno dominante 7/9, otro 1/9), si ambos cumplen
   cupo+tiempo, se elige el dominante.
5. **Día preferido/canónico primero:** con el mismo vehículo disponible en
   dos días admisibles, se prueba primero el canónico.
6. **"Menos malo" nunca sale del grupo:** si ningún `(vehículo, día)` de
   `unidades_afines` cumple cupo+tiempo perfecto, se elige el de menor %
   resultante **entre esos mismos candidatos**, nunca uno ajeno.
7. **Sin grupo → se queda marcada**, igual que "sin historial" en v1.
8. **Mayorista ancla correctamente:** se mueve junto con la sucursal más
   cercana de su ruta origen cuando esa sucursal tiene grupo con destino
   válido.
9. **Regresión explícita con datos reales:** fixtures basados en el grupo
   30 (Tierra Blanca 7 + 77, rígido, `unidades_afines` incluye T 20 pero
   nunca junto a Chocamán/Ixhuatlán/Coscomatepec) y el grupo 19 (Amatitlán
   + Carlos A. Carrillo 2, flexible, dominante F 350_1 7/9) — verificar que
   con estos datos reales v2 ya no reproduce ninguno de los dos
   incidentes descritos en §1.
10. **Determinismo/idempotencia:** igual que v1 — correr la resolución dos
    veces sobre el mismo resultado no genera un segundo movimiento.
