# Reorganización de zonas canónicas (grupos LORES → 24 zonas) — diseño

## Contexto

Los jefes de prácticas dieron una nueva lista de 24 "zonas" canónicas, cada
una con sus sucursales propias. Reemplazan a los 42 `grupos` LORES vigentes
(versión 17) que hoy agrupan las sucursales para armar rutas. Es la fuente de
verdad de negocio a partir de ahora: "zona" pasa a ser el nombre y la
numeración con la que el negocio piensa el reparto.

**No cambia**: `plantilla_zona_mayorista` (zonas de mayoristas, nombradas por
población — COSAMALOAPAN, TUXTEPEC, etc.) ni
`datos/mapeo_poblacion_a_zona.csv`. Es un concepto separado (enganche de
clientes mayoristas por cercanía a una ruta), no lo tocó esta lista.

Verificado por grep: el motor ConVRP no tiene números de `grupo` escritos en
la lógica (`logic/*.py`) — toda referencia a un grupo específico vive en CSVs
o en la tabla `plantilla_grupo`. Renumerar de 42 a 24 es seguro a nivel de
código.

## Resolución de nombres

La lista del negocio usa nombres cortos/coloquiales. Se resolvieron contra
`sucursales.nombre_base` (101 filas) así:

| Nombre en la lista | Sucursal real |
|---|---|
| Carrillo 1 | Carlos A. Carrillo (49) |
| Carrillo 2 | Carlos A. Carrillo 2 (86) |
| Rodriguez 1 | Juan Rodríguez Clara (13) |
| Rodriguez 2 | Rodriguez 2 (92) |
| Valle | San Juan Bautista Valle Nacional (19) |
| Jalapa de Diaz 1 | San Felipe Jalapa de Díaz (35) — ya es alias confirmado de "Jalapa de Diaz" en `mapeo_poblacion_a_zona.csv` |
| Vicente | Vicente Camalote (20) |
| Zotavento | Sotavento (67) |
| Amapolas | Veracruz 4 (Col. Amapolas) (72) |
| Oasis | Veracruz 3 (Facc. Oasis) (71) |
| Carrisal | Carrizal (81) |
| Cardel | Jose Cardel (40) |
| Úrsulo | Úrsulo Galvan (82) |
| Ignacio | Ignacio de llave (28) |
| Covarrubias | Juan Díaz Covarrubias (45) |
| Lerdo 1 | Lerdo de Tejada (16) |
| Lerdo 2 | Lerdo de Tejada 2 (88) |
| Santiago 1 / 2 | Santiago Tuxtla 1 (32) / 2 (78) |
| Catemaco 1 / 2 | Catemaco (39) / Catemaco 2 (96) |
| Cabada | Ángel R. Cabada (22) |

**Cobertura verificada por script** (`obtener_grupos()` + tabla
`sucursales`): las 24 zonas cubren **101 de 101** sucursales vigentes, sin
duplicados y sin huecos — salvo la 11ª sucursal de "Tuxtepec (11)", que no
existe todavía en el catálogo (ver Fuera de alcance).

## Restricción de negocio: máximo 6 sucursales por ruta/día

El negocio confirmó que, como regla general, **una ruta no entrega más de 6
sucursales en un día**. Dos zonas superan ese límite y sí necesitan partirse
en más de una ruta:

- **Zona 5 (Tuxtepec)**: 10 sucursales.
- **Zona 11 (Tierra Blanca)**: 8 sucursales.

**Zona 22 (Santiago+San Andrés+Catemaco+Covarrubias, 8 sucursales) es una
excepción de negocio confirmada**: estas 8 sí se entregan en una sola ruta
pese a pasar el límite general — el negocio lo confirmó explícitamente al
revisar el spec. El histórico ya respalda que es viable: 2 de 13 semanas
las 8 viajaron juntas en una sola unidad (F 350_3). El resto de las semanas
se partió en 6+2 (San Andrés/Catemaco/Covarrubias + Santiago), pero eso fue
por conveniencia operativa de esas semanas, no por un límite duro — así que
Zona 22 queda como **un solo grupo de 8**, no como sub-rutas.

El resto de las zonas tiene ≤6 sucursales y cabe en una sola ruta (Zona 1
queda justo en el límite, 6).

Para Tuxtepec y Tierra Blanca, en vez de forzar una sola ruta "ganadora" (que
violaría el límite), **cada zona se compone de 2 o 3 sub-rutas** — el mismo
patrón que ya usa `plantilla_zona_mayorista` (zona → varios `grupos_lores`).
Se analizó el histórico real (`rutas_historicas`, 13 semanas) para decidir
cómo partir cada una:

- **Zona 5 (Tuxtepec)** — patrón estable y consistente semana a semana. Se
  reutilizan tal cual los 3 grupos viejos que ya la componían:
  - **5A** (MARTES, FLEXIBLE, F 350_2, forzada): Tuxtepec 1,2,3,4,7 + Jardines
    del Arroyo (6) — antes grupo 1.
  - **5B** (JUEVES, RIGIDO, F 350_2): Tuxtepec 5,6,8 (3) — antes grupo 8.
  - **5C** (San Bartolo solo, FLEXIBLE, sin día fijo — admite MARTES, JUEVES,
    MIERCOLES — unidad K 16): nunca se estabilizó un día en el histórico
    (45% jueves, 36% martes, 18% miércoles); queda igual que antes (grupo
    37), sin forzar un día que el histórico no respalda.

- **Zona 11 (Tierra Blanca)** — **sin patrón estable**: el día siempre es
  LUNES (12 de 13 semanas), pero qué sucursales viajan juntas y en qué
  camión cambia casi cada semana (se ha visto en T 17_1, T 17_2, T 23, K 16,
  T 20, J 19, K 20, T 25, F 350_3 — nueve unidades distintas repartiéndose el
  hueco disponible cada vez). No hay pareja histórica que heredar. Se parte
  a la mitad **por número de tienda**, ambas LUNES, sin unidad de referencia
  fija (el motor la asigna por capacidad cada semana, igual que ya ocurre de
  facto hoy):
  - **11A**: Tierra Blanca 1,2,3,4.
  - **11B**: Tierra Blanca 5,6,7,8.

## Derivación de rigidez / día / unidad_ref

Varias zonas nuevas fusionan sucursales que antes pertenecían a distintos
`grupo` con distinta calibración (rigidez, día, unidad de referencia — 9
semanas de histórico vía `scripts/calibrar_unidad_ref.py` y
`dias_admisibles_por_grupo.csv`). Regla acordada: **la zona hereda todos los
valores calibrados del grupo viejo que más sucursales le aportó** (rigidez,
día preferido, días admisibles, unidad_ref, unidades_afines, unidad_forzada).
Empate → gana el grupo de número más bajo (determinista, mismo criterio que
usa el resto del motor).

Si el grupo ganador cubre **menos del 60%** de las sucursales de la zona
(mismo umbral que ya usa `confianza_zona()` en `enganche_zona.py` para
"confianza BAJA"), la zona se marca **REVISAR**: se escribe igual, no se
aborta, pero queda documentada para que el negocio la revise después.

### Tabla completa (calculada contra la versión 17 vigente)

| Zona | Sucursales | Grupos origen (conteo) | Gana | % | Rigidez | Día | Unidad ref | Forzada | Días admisibles |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Cosamaloapan 1,2,3; Carrillo 1,2; Amatitlán | g4:4, g19:2 | g4 | 67% | RIGIDO | MARTES | F 350_1 | **Sí** | MARTES |
| 2 | Rodriguez 1,2; Isla 1,2,3 | g26:2, g15:3 | g15 | 60% | RIGIDO | MIERCOLES | F 350_3 | No | MIERCOLES, JUEVES |
| 3 | Loma 1,2; Azueta | g12:3 | g12 | 100% | RIGIDO | MIERCOLES | J 18 | No | MIERCOLES, JUEVES |
| 4 | Chacaltianguis, Tlacojalpan, Otatitlán, Papaloapan | g13:3, g36:1 | g13 | 75% | RIGIDO | JUEVES | K 16 | No | JUEVES, VIERNES |
| 5 | Tuxtepec 1-8, Jardines del Arroyo, San Bartolo (falta 1) | — | — | — | *(3 sub-rutas, ver sección arriba)* | | | | |
| 6 | Playa 1,2; Lombardo | g24:2, g34:1 | g24 | 67% | RIGIDO | MIERCOLES | F 350_2 | No | MARTES, MIERCOLES |
| 7 | Jalapa de Diaz 1,2 | g21:2 | g21 | 100% | RIGIDO | JUEVES | F 350_3 | No | MIERCOLES, JUEVES |
| 8 | Chiltepec, Valle | g20:2 | g20 | 100% | RIGIDO | JUEVES | T 23 | No | JUEVES, VIERNES |
| 9 | Tres Valles 1-4, Gabino Barreda | g7:4, g33:1 | g7 | 80% | RIGIDO | MIERCOLES | K 16 | No | MIERCOLES |
| 10 | Temazcal, Naranjos | g22:2 | g22 | 100% | RIGIDO | JUEVES | J 19 | No | JUEVES, MIERCOLES, VIERNES |
| 11 | Tierra Blanca 1-8 | — | — | — | *(2 sub-rutas, ver sección arriba)* | | | | |
| 12 | Tetela, Vicente, Acatlán | g9:3 | g9 | 100% | FLEXIBLE | MARTES | J 18 | No | MARTES |
| 13 | Omealca, Tezonapa | g23:2 | g23 | 100% | RIGIDO | MARTES | T 17_1 | No | MARTES, MIERCOLES |
| 14 | Yanga, Potrero, Paso del Macho | g16:3 | g16 | 100% | RIGIDO | LUNES | T 17_2 | No | LUNES |
| 15 | Monte Blanco, Chocamán, Coscomatepec, Ixhuatlán | g6:4 | g6 | 100% | RIGIDO | MARTES | T 17_2 | No | MARTES, LUNES |
| 16 | Jamapa, El Tejar, Antón Lizardo, Veracruz | g11:3, g28:1 | g11 | 75% | RIGIDO | JUEVES | T 20 | No | JUEVES, VIERNES |
| **17** | Zotavento, Amapolas, Tejería, Oasis | g18:2, g28:1, g35:1 | g18 | **50%** | FLEXIBLE | JUEVES | K 20 | No | JUEVES | ⚠️ REVISAR |
| 18 | Puente Jula, Tolome, Paso de Ovejas, Soledad, Purga | g17:3, g25:2 | g17 | 60% | FLEXIBLE | VIERNES | T 20 | No | VIERNES |
| 19 | Carrisal, Actopan, Rinconada, Cardel | g10:3, g32:1 | g10 | 75% | RIGIDO | JUEVES | J 18 | No | VIERNES, JUEVES |
| 20 | Úrsulo, Cempoala, Palma Sola, Emilio, Vega | g42:1, g5:4 | g5 | 80% | FLEXIBLE | JUEVES | T 17_2 | No | VIERNES, JUEVES |
| 21 | Alvarado, Tlacotalpan, Lerdo 1,2, Cabada | g3:5 | g3 | 100% | RIGIDO | JUEVES | F 350_1 | No | JUEVES |
| 22 | Santiago 1,2; San Andrés 1,2,3; Catemaco 1,2; Covarrubias | g27:2, g2:6 | g2 | 75% | RIGIDO | MARTES | F 350_3 | No | MARTES, JUEVES |
| 23 | Piedras Negras, Ignacio, Tlalixcoyan | g14:3 | g14 | 100% | RIGIDO | LUNES | T 17_1 | No | LUNES |
| 24 | Amatlán | g31:1 | g31 | 100% | FLEXIBLE | LUNES | T 25 | No | MARTES, LUNES |

Zona 2 pasa el umbral justo al 60% — no se marca REVISAR pero queda anotada
como límite.

## Persistencia

### Esquema: columna `zona` nueva en `plantilla_grupo`

22 de las 24 zonas son 1 zona = 1 `grupo` (misma cardinalidad que antes,
incluida la Zona 22, que queda como grupo único de 8 pese al límite general
— ver excepción de negocio arriba). Las dos zonas que sí se parten por el
límite de 6 (5 y 11) son 1 zona = 2 o 3 `grupo` (sub-rutas). Como
`plantilla_grupo.grupo` es la PK y sigue siendo un INT único por fila, no
puede representar "varios grupos = 1 zona" por sí solo: se agrega una
columna nueva `zona INT NULL` a `plantilla_grupo`, con ALTER idempotente
(mismo patrón ya usado para `unidades_afines` en
`scripts/crear_plantilla_canonica.py::ALTERS`) — no destructivo, no obliga a
recrear la tabla.

Numeración: la sub-ruta más grande de cada zona conserva `grupo = zona`
(24 números 1-24, uno por zona); las sub-rutas adicionales de 5 y 11 toman
los siguientes números libres (25-27):

| grupo | zona | sucursales | rigidez | día | días admisibles | unidad_ref | forzada |
|---|---|---|---|---|---|---|---|
| 5  | 5  | Tuxtepec 1,2,3,4,7 + Jardines del Arroyo (6) | FLEXIBLE | MARTES | MARTES, MIERCOLES | F 350_2 | **Sí** |
| 25 | 5  | Tuxtepec 5,6,8 (3) | RIGIDO | JUEVES | JUEVES | F 350_2 | No |
| 26 | 5  | San Bartolo (1) | FLEXIBLE | MARTES | MARTES, JUEVES, MIERCOLES | K 16 | No |
| 11 | 11 | Tierra Blanca 1,2,3,4 | FLEXIBLE | LUNES | LUNES | *(sin preferencia)* | No |
| 27 | 11 | Tierra Blanca 5,6,7,8 | FLEXIBLE | LUNES | LUNES | *(sin preferencia)* | No |
| 22 | 22 | Santiago 1,2 + San Andrés 1,2,3 + Catemaco 1,2 + Covarrubias (8, excepción — no se parte) | RIGIDO | MARTES | MARTES, JUEVES | F 350_3 | No |

Total: **27 filas en `plantilla_grupo`, 24 valores distintos de `zona`** —
todas ≤6 sucursales salvo la Zona 22 (8, excepción de negocio confirmada).
Verificado por script: las 27 sub-rutas cubren 101/101 sucursales sin
duplicados.

### Función `cargar_zonas_manual()`

Nueva función en `logic/plantilla_canonica.py`:

- Recibe la lista de 27 sub-rutas ya resuelta (grupo, zona, num_tienda,
  campos heredados o construidos) como estructura Python — no hay Excel ni
  bridge que parsear, es dato de negocio capturado a mano una sola vez.
- Escribe una **versión nueva** SOLO en `plantilla_grupo`,
  `plantilla_grupo_sucursal` y `plantilla_grupo_dia` (marca vigente=0 la
  versión anterior de esas tres tablas, inserta la nueva con vigente=1).
  `plantilla_bridge_sucursal`, `plantilla_zona_mayorista` y
  `plantilla_poblacion_zona` **no se tocan** — cada tabla tiene su propio
  flag `vigente` independiente del número de versión, así que pueden quedar
  en versiones distintas sin romper los lectores (`obtener_bridge`,
  `obtener_zona`, `zona_de_poblacion` filtran por `vigente=1` cada una por su
  cuenta).
- Inserta una fila nueva en `plantilla_meta` con `nota` describiendo la
  reorganización, para el rastro de auditoría (mismo patrón no-destructivo
  que ya usa `cargar_plantilla_desde_excel`).

### Consumidores de `grupo` que deben revisarse por el cambio de cardinalidad

Antes, código que agrupaba por `grupo` asumía implícitamente "1 grupo = 1
destino de negocio". Con 3 zonas ahora partidas en sub-rutas, cualquier
lugar que reporte o muestre resultados **por zona de negocio** (no por ruta)
debe agregar sobre `zona`, no sobre `grupo`. Identificar estos puntos es
tarea del plan de implementación, no de este spec — pero el ejemplo conocido
es `plantilla_zona_mayorista.grupos_lores` (el enganche de mayoristas ya
referencia números de grupo LORES por texto libre) y cualquier reporte/UI
que hoy muestre "grupo N" al planeador.

## Fuera de alcance

- **11ª sucursal de Tuxtepec**: no existe en `sucursales` todavía. La Zona 5
  queda con 10 repartidas en sus 3 sub-rutas (grupo 5/25/26). Cuando el
  negocio dé de alta la tienda con su `num_tienda`, falta además decidir a
  cuál de las 3 sub-rutas se suma (probablemente la del MARTES, grupo 5, que
  es la más grande) — se agrega a `plantilla_grupo_sucursal` sin recargar
  todo de nuevo.
- **CSVs de calibración desactualizados**: `dias_admisibles_por_grupo.csv`,
  `unidad_ref_por_grupo.csv` y `grupos_unidad_forzada.csv` siguen
  referenciando la numeración vieja de 42 grupos. Son el input por defecto
  de `cargar_plantilla_desde_excel` (usado por `scripts/cargar_plantilla.py`,
  disparado a mano, no hay ruta de subida automática en la app). Si alguien
  vuelve a correr ese script con un Excel canónico nuevo sin antes migrar
  esos 3 CSV a la numeración de zonas, pisaría esta reorganización. Se deja
  documentado en el docstring del script nuevo; no se resuelve aquí.
- **Zona 17** queda con herencia débil (grupo ganador solo 50%); se escribe
  con la regla acordada pero marcada para revisión del negocio. (Zona 11 ya
  no aplica esta regla: se resolvió con sub-rutas, ver arriba.)
- **Sub-rutas 11A/11B (Tierra Blanca) sin unidad de referencia**: quedan con
  `unidad_ref = NULL` a propósito (sin patrón histórico que justifique fijar
  una). El plan de implementación debe confirmar que el resto del motor
  (`asignar_unidades` en `convrp_logic.py` y el resolver de
  `unidad_ref_por_grupo.csv`) tolera un grupo sin `unidad_ref` — el campo ya
  es `NULL`-able en el esquema, pero el comportamiento en tiempo de reparto
  no se verificó en este spec.

## Testing

- Test unitario de la función de derivación para las 21 zonas 1:1 (conteo
  por grupo origen, desempate determinista, umbral 60% → flag REVISAR) con
  fixtures pequeños, en `tests/test_plantilla_canonica.py`.
- Test unitario de que ninguna sub-ruta resultante (27 filas) supera 6
  sucursales salvo la Zona 22 (8, excepción de negocio confirmada), y de que
  las 3 zonas grandes (5, 11, 22) suman exactamente sus sucursales
  originales sin huecos ni duplicados.
- Script con modo de solo-lectura que imprime la tabla zona → grupo →
  sucursales → rigidez/día/unidad/advertencias sin escribir en BD, para
  revisar antes de comprometer (el cálculo de este documento ya es ese
  modo).
