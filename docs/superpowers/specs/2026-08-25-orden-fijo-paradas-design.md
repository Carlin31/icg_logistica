# Diseño: Orden fijo de paradas por regla

**Fecha:** 2026-08-25
**Estado:** Aprobado (diseño) — pendiente plan de implementación.
**Módulos:** nuevo `logic/orden_fijo_paradas.py`; se conecta en `logic/historico_logic.py`
(paso de secuenciado de `generar_rutas_vrp_afinidad`, compartido por los dos motores).

## 1. Problema

Ninguno de los dos motores de generación de rutas asigna un orden de visita
real dentro de una ruta con ConVRP activo (`CONVRP_ACTIVO=True`, el default
actual): `construir_groups_desde_plantilla()` siempre marca cada parada con
`seq=999`. El paso compartido que decide el orden final,
`ordenar_paradas_por_historico()` (`logic/vrp_logic.py`), cuando no hay
ninguna parada con `seq` real cae por completo a vecino-más-cercano por
coordenada — ignorando cualquier orden de operación real.

Caso concreto: la ruta de `F 350_1/MARTES` con Cosamaloapan 1/2/3, Carlos A.
Carrillo 1/2 y Amatitlán (esta última reincorporada por la Palanca 5, ver
`docs/superpowers/specs/2026-08-25-relleno-capacidad-libre-design.md`). El
usuario indicó el orden real de operación: **Cosamaloapan 1 → 2 → 3 →
Carrillo 2 → Carrillo 1 → Amatitlán** (`num_tienda` 4, 27, 75, 86, 49, 100).
Se verificó contra `rutas_historicas_visitas` (9 semanas reales) que el
histórico agregado NO reproduce este orden exacto (ahí Carrillo 1 suele ir
antes que Carrillo 2, con variación semana a semana) — es decir, esto no es
"restaurar el historial", es una regla de operación real que el usuario
quiere fijar explícitamente.

## 2. Objetivo

Permitir fijar, para un conjunto nombrado de sucursales, un orden de visita
explícito que gana sobre el historial y la geografía — sin tocar ninguno de
los dos motores de generación (ConVRP ni afinidad), solo el paso de
secuenciado que ya comparten.

## 3. Decisiones

- **Tabla nueva `orden_fijo_paradas`**: `nombre_regla VARCHAR(100)`,
  `num_tienda INT`, `posicion INT`, `PK(nombre_regla, num_tienda)`. Sin
  versionado/vigente (no es la plantilla canónica) — es una tabla de
  referencia chica y de edición manual poco frecuente.
- **Semilla**: `datos/orden_fijo_paradas.csv` (`nombre_regla,num_tienda,posicion`),
  cargada por `scripts/cargar_orden_fijo.py` con **reemplazo completo por
  `nombre_regla`** (borra las filas de esa regla e inserta las del CSV) —
  mismo criterio de reemplazo no acumulativo que el resto del proyecto.
  `scripts/crear_orden_fijo_paradas.py` crea la tabla si no existe (patrón
  de `crear_plantilla_canonica.py`).
- **Reglas iniciales** (dos, ambas en el mismo CSV/tabla):
  - `cosamaloapan_carrillo_amatitlan` (F 350_1/MARTES): sucursales 4, 27,
    75, 86, 49, 100 → posiciones 1-6, en ese orden.
  - `tuxtepec_f350_2` (F 350_2/MARTES): sucursales 2, 31, 74, 55, 7, 54, 15
    (Tuxtepec 1-Centro, Tuxtepec 4-Hidalgo, San Bartolo, Tuxtepec 7-Hacienda,
    Tuxtepec 2-Boulevar, Jardines del Arroyo, Tuxtepec 3-Independencia) →
    posiciones 1-7, en ese orden. Verificado contra `rutas_historicas_visitas`:
    estas 7 sucursales solo coinciden completas en `F 350_2/MARTES` (ninguna
    otra combinación vehículo/día las junta a las 7), y el orden pedido es
    consistente con el patrón histórico agregado.
- **Aplicación — un único punto, compartido por los dos motores**: en
  `historico_logic.py`, justo antes de llamar a
  `ordenar_paradas_por_historico(miembros, coords_dict)` (dentro del bucle
  `for (veh, dia), miembros in sorted(groups.items())`). Nueva función pura
  `aplicar_orden_fijo(miembros, orden_fijo) -> list[int] | None` en
  `logic/orden_fijo_paradas.py`:
  - `orden_fijo` es `{num_tienda: (nombre_regla, posicion)}`, cargado una
    sola vez por corrida (`obtener_orden_fijo(db)`, antes del bucle de
    rutas — no una consulta por ruta).
  - Si **todas** las `sid` de `miembros` están en `orden_fijo` y comparten
    la **misma** `nombre_regla`: devuelve las `sid` ordenadas por
    `posicion` (ascendente). Las sucursales de la regla que no aparecen esa
    semana simplemente se omiten — no rompe el orden relativo del resto.
  - Si hay **cualquier** sucursal fuera de esa regla (o mezcla de reglas):
    devuelve `None` — la ruta sigue el camino normal
    (`ordenar_paradas_por_historico`, sin cambios). Fuera de alcance de esta
    versión: insertar sucursales ajenas a la regla alrededor de un bloque
    fijo — se deja para una iteración futura si hace falta.
  - En `historico_logic.py`: `ordered = aplicar_orden_fijo(miembros, orden_fijo) or ordenar_paradas_por_historico(miembros, coords_dict)`.
- **`secuencia_visita` persistida**: el resultado de `aplicar_orden_fijo`
  alimenta el mismo `rows`/`detalle_por_dia` que ya arma el paso 7-8 de
  `generar_rutas_vrp_afinidad` — no se toca ese formato, solo el origen del
  orden.

## 4. Casos borde

- **Regla con solo 2 de las 6 sucursales presentes esa semana** (p. ej.
  Carrillo 1 sin pedido): se ordenan las 4 restantes por su `posicion`
  relativa — el hueco no importa, no requiere renumerar.
- **Ruta con una sucursal de la regla + una totalmente ajena**: no se aplica
  el pin, camino normal completo (histórico → geografía).
- **Dos reglas distintas cuyas sucursales terminan en la misma ruta** (no
  debería pasar con los datos actuales, pero por seguridad): no comparten
  `nombre_regla` → no se aplica el pin, camino normal.
- **Tabla vacía / sin filas para ninguna sucursal de la ruta**:
  `aplicar_orden_fijo` devuelve `None` de inmediato — comportamiento
  idéntico a hoy, sin overhead relevante (un solo `SELECT` por corrida, no
  por ruta).

## 5. Fuera de alcance

- Insertar sucursales ajenas a la regla alrededor de un bloque fijo
  (mezcla parcial).
- UI de administración para crear/editar reglas — se editan vía CSV +
  script de carga, igual que `grupos_unidad_forzada.csv`.
- Cualquier cambio a `construir_groups_desde_plantilla` o
  `generar_rutas_vrp_afinidad` más allá del punto de secuenciado.

## 6. Pruebas

1. `aplicar_orden_fijo`: todas las sucursales cubiertas por la misma
   regla → devuelve el orden por `posicion`.
2. `aplicar_orden_fijo`: falta una sucursal de la regla → el resto
   mantiene su orden relativo.
3. `aplicar_orden_fijo`: una sucursal ajena a la regla → devuelve `None`.
4. `aplicar_orden_fijo`: sucursales de dos reglas distintas → devuelve
   `None`.
5. `aplicar_orden_fijo`: `orden_fijo` vacío → devuelve `None`.
6. `obtener_orden_fijo`: lee la tabla real y arma el dict
   `{num_tienda: (nombre_regla, posicion)}` correctamente (prueba de
   integración con BD desechable).
7. Regresión con los datos reales de la regla `cosamaloapan_carrillo_amatitlan`
   (4, 27, 75, 86, 49, 100 → posiciones 1-6) confirmando el orden exacto
   pedido por el usuario.
8. Regresión con los datos reales de la regla `tuxtepec_f350_2`
   (2, 31, 74, 55, 7, 54, 15 → posiciones 1-7) confirmando el orden exacto
   pedido por el usuario, y que ambas reglas conviven sin interferirse en
   la misma tabla/carga.
