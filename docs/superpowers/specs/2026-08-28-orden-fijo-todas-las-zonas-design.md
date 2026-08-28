# Diseño: orden fijo de paradas para las 24 zonas canónicas

**Fecha:** 2026-08-28
**Estado:** Aprobado (diseño).
**Módulos:** ninguno nuevo — usa `logic/orden_fijo_paradas.py`,
`scripts/cargar_orden_fijo.py` y `scripts/crear_orden_fijo_paradas.py`, ya
implementados en `docs/superpowers/specs/2026-08-25-orden-fijo-paradas-design.md`.
Solo se agrega el dato: `datos/orden_fijo_paradas.csv`.

## 1. Problema

El negocio dio la lista de las 24 zonas canónicas
(`docs/superpowers/specs/2026-08-26-reorganizacion-zonas-canonicas-design.md`)
con las sucursales de cada una **en un orden de operación real** (p. ej. Zona
1: "Cosamaloapan 1, 2, 3, Carrillo 2 y 1, Amatitlán"). Ese orden no se
capturó como tal en su momento: `ZONAS_SIMPLES` en
`scripts/reorganizar_zonas_2026.py` guarda las sucursales de cada zona solo
como *membresía* (de qué zona es cada una), no como secuencia de visita —
y el único mecanismo que sí fija una secuencia de visita real,
`orden_fijo_paradas`, quedó diseñado en la Fase anterior pero **sin ninguna
fila cargada** (el CSV semilla `datos/orden_fijo_paradas.csv` nunca se creó).

Resultado: cuando una ruta reúne exactamente las sucursales de una zona, el
orden de visita lo decide `ordenar_paradas_por_historico()` (histórico
agregado o vecino-más-cercano) en vez del orden real que el negocio ya dio.

## 2. Verificación contra el dato ya cargado

Se comparó la lista de 24 zonas (nombre → orden) contra `ZONAS_SIMPLES`
(vía `datos/mapeo_no_a_numtienda.csv`, que da nombre real por `num_tienda`):

- **21 zonas coinciden exactamente** entre el orden dado y `ZONAS_SIMPLES`
  (1, 2, 3, 4, 6-10, 12-21, 23).
- **Zona 22 no coincide**: orden dado
  `32,78,90,91,98,39,96,45` (Santiago 1,2, San Andrés 1,2,3, Catemaco 1,2,
  Covarrubias) vs. `ZONAS_SIMPLES` `39,45,90,91,96,98,32,78`.
- **Zona 24** (Amatlán) es una sola sucursal: no hay orden que fijar.
- **Zonas 5 (Tuxtepec) y 11 (Tierra Blanca)**: el negocio las dio solo como
  conteo (`Tuxtepec (11)`, `Tierra Blanca (8)`), sin secuencia interna, y ya
  están partidas en sub-rutas de ≤6 paradas por el límite general de
  negocio (ver spec de reorganización). Fuera de alcance de este cambio —
  se puede agregar después si el negocio da el orden interno de cada
  sub-ruta.

Esto confirma que la discrepancia no es un problema del dato de zonas ya
cargado (que en 21/24 casos ya está bien), sino que falta el paso que
convierte ese orden en una regla de `orden_fijo_paradas` que el motor
realmente respete al imprimir/generar la ruta.

## 3. Decisión

Crear `datos/orden_fijo_paradas.csv` con una regla por zona de un solo
grupo — **21 reglas**, nombradas `zona_1`, `zona_2`, ... `zona_23` (se
omiten 5, 11 y 24 por lo explicado arriba) — usando el orden dado por el
negocio, con la Zona 22 corregida al orden real:

| Regla | num_tienda en orden |
|---|---|
| zona_1  | 4, 27, 75, 86, 49, 100 |
| zona_2  | 92, 13, 3, 73, 85 |
| zona_3  | 5, 37, 12 |
| zona_4  | 64, 65, 70, 18 |
| zona_6  | 11, 93, 42 |
| zona_7  | 35, 97 |
| zona_8  | 33, 19 |
| zona_9  | 6, 10, 69, 79, 89 |
| zona_10 | 14, 47 |
| zona_12 | 23, 20, 58 |
| zona_13 | 59, 9 |
| zona_14 | 17, 26, 34 |
| zona_15 | 56, 95, 51, 50 |
| zona_16 | 87, 62, 66, 8 |
| zona_17 | 67, 72, 99, 71 |
| zona_18 | 94, 80, 83, 60, 41 |
| zona_19 | 81, 52, 53, 40 |
| zona_20 | 82, 61, 48, 43, 44 |
| zona_21 | 21, 68, 16, 88, 22 |
| zona_22 | 32, 78, 90, 91, 98, 39, 96, 45 |
| zona_23 | 29, 28, 30 |

No se reutilizan los nombres `cosamaloapan_carrillo_amatitlan` ni
`tuxtepec_f350_2` del spec anterior: esas dos reglas quedan **reemplazadas**
por `zona_1` (mismo contenido, 4/27/75/86/49/100) y no se toca el caso de
Tuxtepec (fuera de alcance, ver §2) — como ninguna de las dos se había
cargado todavía (el CSV no existía), no hay reemplazo destructivo real, es
la primera carga.

Sin cambios de código: `aplicar_orden_fijo` ya maneja correctamente que
falte una sucursal esa semana (mantiene el orden relativo del resto) y que
una ruta mezcle sucursales ajenas a la regla (no aplica el pin, camino
normal). Esto cubre el caso de Zona 5/11 sin reglas: sus paradas
simplemente no están en `orden_fijo` y siguen el camino normal sin cambios.

## 4. Aplicación

`scripts/cargar_orden_fijo.py datos/orden_fijo_paradas.csv` — reemplazo
completo por `nombre_regla` (ya implementado, sin cambios). Requiere que
exista la tabla `orden_fijo_paradas` (`scripts/crear_orden_fijo_paradas.py`,
idempotente, se corre primero si hace falta).

## 5. Pruebas

Se reutilizan las pruebas ya escritas en `tests/test_orden_fijo_paradas.py`
(`test_regresion_orden_fijo_cosamaloapan_carrillo_amatitlan`,
que valida por `num_tienda` presente — no por nombre de regla — así que
sigue pasando con la regla renombrada a `zona_1`). No se agregan pruebas
nuevas: la lógica de aplicación no cambia, solo el dato cargado.

## 6. Fuera de alcance

- Orden interno de Zona 5 (Tuxtepec) y Zona 11 (Tierra Blanca): pendiente
  de que el negocio dé la secuencia exacta de cada sub-ruta.
- Cualquier cambio a `logic/orden_fijo_paradas.py`,
  `logic/historico_logic.py` o al esquema de la tabla.
