# Asignación de vehículos por peso (reemplazo del motor de preferencias)

## Contexto y bug que lo origina

En el PDF real "Logística del 24 al 28 de agosto del 2026", sucursales de
Tierra Blanca (grupos 11 y 27, ambos `unidad_ref=None`/`unidades_afines=None`
por diseño) terminaron en F 350_1 y F 350_2 — viola la regla de negocio "las
sucursales de Tierra Blanca no deben ir en los F350".

Causa raíz: en `_asignar_unidades` (`logic/convrp_logic.py`), cuando un grupo
no tiene `unidad_ref`, el desempate entre unidades igualmente vacías cae en
orden alfabético (`str(u)` en la clave de sort, línea ~354) — "F 350_1" ordena
antes que cualquier otra unidad. Los lunes, con la flota vacía al arrancar,
cualquier grupo sin preferencia (como Tierra Blanca) cae sistemáticamente en
F 350_1.

Al investigar el arreglo puntual, el usuario pidió ir más allá: reemplazar
todo el mecanismo de preferencias (`unidad_ref` / `unidades_afines` /
`unidad_forzada`) por una asignación que decida el vehículo **puramente por
peso**, cada semana, en el momento de generar la logística.

## Objetivo

- La unidad que carga cada grupo se decide por el peso real de esa semana,
  no por una preferencia histórica.
- Tierra Blanca nunca debe poder recibir ninguno de los 3 F 350, bajo ninguna
  circunstancia; si genuinamente no cabe en otra unidad, el sistema debe
  registrar una excepción visible, nunca asignar F350 en silencio.
- Tuxtepec y Cosamaloapan (hoy con `unidad_forzada=True`) se re-evalúan por
  peso como cualquier otro grupo — se quita el pin.
- No importa la continuidad semana a semana: es aceptable que un grupo cambie
  de camión de una semana a otra si el peso lo justifica.

## Principio de selección

Los 3 rangos de peso que dio el usuario (≤1549 → T25/T23/T20; 1550–2549 →
J18/J19/K16/K20/T17_1/T17_2; 2550–3900 → F350_1/2/3) **coinciden exactamente**
con ordenar la flota real por capacidad ascendente (de
`obtener_capacidades_vehiculos()`: T25=1300, T23=T20=1500, J18=J19=K16=K20=
T17_1=T17_2=2500, F350_1=F350_2=F350_3=3900). Ordenar por capacidad ascendente
y tomar la primera unidad que alcance produce el mismo resultado que buscar
el "rango" correcto, sin mantener una tabla de rangos aparte que se pueda
desincronizar de las capacidades reales dadas de alta en la BD.

**Regla de selección nueva** (reemplaza la preferencia por `unidad_ref`):
para cada grupo, entre las unidades no excluidas y compatibles por
coocurrencia que alcanzan a admitirlo, elegir ordenando por
`(capacidad ascendente, ya_cargada_ese_día descendente, nombre alfabético)`.

- **Capacidad ascendente primero:** nunca manda un grupo chico a una unidad
  grande de más si una chica ya le alcanza (el problema exacto que reportó
  el usuario: la ruta del jueves de F350_1 pesaba 2410 kg, cabía en una
  camioneta de 2.5 t).
- **Ya cargada, como desempate dentro de la misma capacidad:** conserva la
  consolidación existente (preferir sumar a una unidad que ya lleva algo ese
  día antes que abrir una vacía) — evidencia histórica ya validada
  (~1.4 grupos por viaje, no 1.0).
- **Alfabético, último desempate:** determinismo, igual que hoy.

## Qué cambia en `logic/convrp_logic.py`

### `_asignar_unidades` (líneas ~254–420)

- **Se quita** el pin de `unidad_forzada` (líneas ~318–327): ningún grupo se
  salta el reparto normal; Tuxtepec/Cosamaloapan se re-evalúan por peso.
- **Se quita** el intento de usar `unidad_ref` primero (líneas ~312, 328–337):
  todo grupo entra directo a la búsqueda de candidatos.
- **Se quita** la protección de "unidad reservada para otro grupo pendiente"
  (líneas ~355–361): esa protección sólo existía para no pisar la
  `unidad_ref` de otro grupo; sin preferencias no hay nada que reservar.
- **Se reemplaza** el sort de candidatos "otras" (líneas ~349–354, hoy
  `[-carga_ya_puesta, -afinidad, alfabético]`) por
  `[capacidad_ascendente, -carga_ya_puesta, alfabético]`.
- **Se mantiene sin cambios:** el filtro de compatibilidad histórica /
  coocurrencia (líneas ~362–369), y el orden de procesamiento por peso
  descendente del grupo (first-fit-decreasing, líneas ~309–311).
- **Se agrega** el filtro de `unidades_excluidas` del grupo, aplicado antes
  del sort, en la lista "otras" **y** en el último recurso (línea ~392).
- **Último recurso (línea ~392):** hoy es "la unidad con más espacio libre,
  sin mirar exclusión ni reserva". Pasa a filtrar primero por
  `unidades_excluidas`. Si la lista filtrada queda vacía (sólo puede pasar
  si un grupo excluye toda la flota — no es el caso de Tierra Blanca, que
  sólo excluye 3 de 13 unidades), se asigna un sentinel `"SIN_UNIDAD"` (nunca
  el nombre de una unidad real) y se registra una excepción nueva
  `SIN_UNIDAD_DISPONIBLE`. Si la lista filtrada NO queda vacía, el
  comportamiento es el de siempre (más espacio libre primero) — la partición
  que corre después sigue encargándose de lo que no quepa, respetando la
  misma exclusión (ver abajo), así que en la práctica un grupo con exclusión
  casi nunca llega a este caso degenerado: la partición reparte lo que sobra
  entre el resto de la flota no excluida en vez de fallar.

### `_unidad_alternativa` (líneas ~240–251)

Usada por la partición para reubicar la parte que se separa de un grupo.
Hoy recorre `sorted(vehiculos_cap)` alfabético. Pasa a filtrar por
`unidades_excluidas` del grupo y a recorrer en capacidad ascendente, para
quedar consistente con la regla nueva (nunca reubica un pedazo de Tierra
Blanca partido en un F350, y prefiere la unidad más chica que le alcance).

### `_dia_alternativo` (líneas ~423–447)

Hoy prueba `[a["unidad_ref"]] + sorted(vehiculos_cap)` (alfabético). Se quita
el `unidad_ref` inicial y se filtra + ordena igual que `_unidad_alternativa`
(exclusión primero, luego capacidad ascendente).

### Nueva excepción `SIN_UNIDAD_DISPONIBLE`

Mismo formato que las excepciones existentes (`MOVIDO_UNIDAD`,
`AVISO_RUTA_SOLITARIA`, etc.): `{"tipo": "SIN_UNIDAD_DISPONIBLE", "grupo":
..., "dia": ..., "motivo": "..."}`. Aparece en el reporte/PDF igual que las
demás, para revisión manual — nunca se resuelve asignando una unidad
excluida.

### Lo que NO cambia

- `_restriccion_violada` (peso/volumen/tiempo) y la partición como último
  recurso (Palanca 3) — intactos.
- `_consolidar_solitarios` y `_rellenar_capacidad_libre` — intactos.
- Rigidez de composición vs. flexibilidad de día — dimensiones
  independientes, sin tocar.
- El orden por peso descendente del grupo dentro del día (first-fit-
  decreasing) — se mantiene: sigue siendo la mejor evidencia para decidir
  quién tiene primera opción sobre el espacio libre.

## Columna nueva: `plantilla_grupo.unidades_excluidas`

Mismo patrón que la columna `zona` agregada en la reorganización anterior:
`ALTERS` en `scripts/crear_plantilla_canonica.py` +
`("plantilla_grupo", "unidades_excluidas", "NVARCHAR(200) NULL")`.

Formato: lista separada por `|`, igual estilo que `unidades_afines`
(`"F 350_1|F 350_2|F 350_3"`). `NULL`/vacío = sin exclusión. Se lee en
`convrp_integracion.py` (donde hoy se arma `afinidad_unidad` desde
`unidades_afines`) y se agrega al diccionario de cada grupo en `asign` dentro
de `construir_groups_desde_plantilla`, para que `_asignar_unidades`,
`_unidad_alternativa` y `_dia_alternativo` lo puedan filtrar.

## Campos que quedan vestigiales (no se borran)

`unidad_ref`, `unidades_afines`, `unidad_forzada` se quedan en el esquema —
más barato de revertir que borrar columnas. El motor de generación
(`_asignar_unidades` y compañía) deja de leerlos para decidir unidad.

**Importante — dos consumidores existentes que NO se tocan:**

- `logic/tiempo_reubicacion.py`: herramienta de reubicación manual
  post-generación, usa `unidades_afines` para restringir destinos a
  vehículos con afinidad histórica real. Es una función distinta (relocalizar
  paradas ya generadas, no generar la ruta inicial) — fuera de alcance de
  este cambio, sigue funcionando igual.
- `logic/convrp_validacion.py` / `scripts/calibrar_unidad_ref.py`: pipeline
  de calibración/backtesting que DERIVA `unidad_ref`/`afinidad_unidad` desde
  el histórico. Sigue corriendo igual; simplemente ya no influye en la
  generación semanal en vivo, porque `_asignar_unidades` deja de leer esos
  campos.

## Tierra Blanca: split de 2 grupos (4+4) a 3 (3+3+2)

Grupos actuales (versión vigente): grupo 11 = sucursales [1, 24, 25, 36],
grupo 27 = [63, 76, 77, 101]. Las 8 son "Tierra Blanca 1..8", todas en el
mismo pueblo. Agrupadas por cercanía real de coordenadas:

| Grupo nuevo         | Sucursales (num_tienda)      | Tamaño |
|---------------------|-------------------------------|--------|
| Tierra Blanca Norte  | 24, 25, 77                    | 3      |
| Tierra Blanca Centro | 1, 36, 101                    | 3      |
| Tierra Blanca Sur    | 63, 76                        | 2      |

Cada cluster queda geográficamente compacto (~1–2 km de dispersión interna),
así que partir por peso no sacrifica eficiencia de ruta.

Los 3 grupos nuevos: `rigidez=FLEXIBLE`, `dia=LUNES`,
`dias_admisibles=["LUNES"]`, `unidad_ref=None`, `unidades_afines=None`,
`unidad_forzada=False`, `unidades_excluidas="F 350_1|F 350_2|F 350_3"`.
Reemplazan a los grupos 11 y 27 (que se retiran) en la misma migración
no destructiva ya usada (nueva versión vigente de `plantilla_grupo` /
`plantilla_grupo_sucursal` / `plantilla_grupo_dia`).

## Tuxtepec / Cosamaloapan

Grupo 5 (hoy `unidad_forzada=True`, `unidad_ref="F 350_2"`) pasa a
`unidad_forzada=False`. Sin exclusión adicional — se re-evalúa por peso como
cualquier otro grupo, sin ningún vehículo prohibido.

## Migración de datos

Nuevo script (o extensión de `scripts/reorganizar_zonas_2026.py`) que:
1. Corre el `ALTER` de `unidades_excluidas`.
2. Publica una nueva versión vigente de `plantilla_grupo` con: los grupos 11
   y 27 reemplazados por los 3 nuevos de Tierra Blanca; grupo 5 con
   `unidad_forzada=False`; el resto de los 24 grupos sin cambios de
   contenido (mismas sucursales/rigidez/día), pero pueden quedar con
   `unidad_ref`/`unidades_afines` intactos (vestigiales, no afectan nada).
3. No toca `plantilla_zona_mayorista` / `plantilla_poblacion_zona` /
   `plantilla_bridge_sucursal` (mismo alcance que la migración anterior).

## Regenerar el PDF del 24–28 de agosto de 2026

Una vez implementado y probado, se vuelve a correr la generación de esa
semana para reemplazar el PDF ya emitido.

## Testing

- Tests unitarios de `_asignar_unidades` con la regla nueva: grupo sin
  preferencia cae en la unidad más chica que le alcanza, no en la primera
  alfabética.
- Test específico de exclusión: un grupo con `unidades_excluidas` que
  incluya toda la flota salvo una unidad, que esa unidad ya esté saturada
  ese día → produce `SIN_UNIDAD_DISPONIBLE`, nunca asigna la excluida.
- Test de que Tierra Blanca (las 3 sub-rutas nuevas) nunca resuelve en F350
  en ningún escenario de peso razonable, ni siquiera en el último recurso.
- Test de que `unidad_forzada=True` ya no bloquea el reparto (Tuxtepec/
  Cosamaloapan se re-evalúan).
- Regresión: los tests existentes de coocurrencia, partición, rigidez/día,
  consolidación de solitarios y relleno de capacidad siguen pasando sin
  modificarlos (esas rutas de código no cambian).
- Test de integración: `construir_groups_convrp` (ya arreglado para tolerar
  `unidad_ref=None`) sigue funcionando con las 3 sub-rutas nuevas de Tierra
  Blanca.

## Fuera de alcance

- `logic/tiempo_reubicacion.py` (reubicación manual post-generación).
- `logic/convrp_validacion.py` / `scripts/calibrar_unidad_ref.py`
  (calibración/backtesting histórico).
- Cualquier cambio a rigidez, días admisibles, o la lógica de partición en
  sí (Palanca 3) — sólo cambia CUÁL unidad se prueba primero, no cuándo se
  parte un grupo.
