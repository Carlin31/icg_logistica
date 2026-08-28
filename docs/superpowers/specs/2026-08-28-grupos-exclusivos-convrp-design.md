# Diseño: grupos exclusivos (nunca comparten camión) en ConVRP

**Fecha:** 2026-08-28
**Estado:** Aprobado (diseño) — pendiente plan de implementación.
**Módulos:** `logic/convrp_logic.py` (motor activo en producción, `CONVRP_ACTIVO=True`),
`logic/plantilla_canonica.py` (esquema `plantilla_grupo`),
`scripts/crear_plantilla_canonica.py` (ALTER idempotente).

## 1. Problema

El negocio reportó (2026-08-28) que la ruta `T 17_2/jueves` generada por
ConVRP combina en un solo camión dos zonas que deben quedar separadas:
Zona 4 (Chacaltianguis, Tlacojalpan, Otatitlán, Papaloapan — `grupo=4`) y el
sub-grupo del jueves de Tuxtepec (Tuxtepec 5/6/8 — `grupo=25`, una de las 3
sub-rutas en que se partió la Zona 5 por el límite de 6 sucursales/ruta, ver
`docs/superpowers/specs/2026-08-26-reorganizacion-zonas-canonicas-design.md`).
Cada una pesa por debajo de un camión de 1.5 ton (Zona 4: 1,189 kg; grupo 25:
1,109 kg), así que juntas (2,298 kg) caben cómodas en un camión de 2.5 ton
(`T 17_2`) sin violar ninguna restricción — exactamente el comportamiento que
`_asignar_unidades()` busca a propósito (`docstring`: "desempatando por
CONSOLIDACIÓN... para no abrir un viaje nuevo").

En la misma sesión se identificó un caso relacionado: **Zona 24 (Amatlán,
`grupo=31`→ahora `grupo=24`)** debe viajar siempre sola, porque casi siempre
se acompaña de pedidos de mayoristas y mezclarla con otro grupo Lores le
quita el margen de peso que necesita para eso (confirmado con los jefes de
prácticas). Hoy no existe ningún mecanismo para marcar un grupo como "nunca
se combina con otro" — solo existen `unidad_forzada` (fija la unidad de
referencia) y `unidades_excluidas` (prohíbe camiones concretos), ninguno de
los dos evita compartir camión con otro grupo.

## 2. Alcance

Regla **por grupo**, no general: se marcan como exclusivos únicamente los
tres grupos identificados — **4** (Zona 4), **24** (Zona 24, Amatlán) y **25**
(sub-grupo jueves de Tuxtepec). El resto de los 24 grupos/zonas sigue
consolidándose entre sí exactamente igual que hoy; no se toca su
comportamiento.

## 3. Modelo de datos

Columna nueva `exclusivo BIT NOT NULL DEFAULT 0` en `plantilla_grupo`,
agregada al arreglo `ALTERS` de `scripts/crear_plantilla_canonica.py` (mismo
patrón idempotente ya usado para `unidades_afines`/`zona`/`unidades_excluidas`
— no destructivo, no obliga a recrear la tabla).

Un script de una sola corrida, `scripts/marcar_grupos_exclusivos.py`, hace
`UPDATE plantilla_grupo SET exclusivo=1 WHERE grupo IN (4, 24, 25) AND
vigente=1`. No crea una versión nueva de la plantilla — es una corrección
puntual sobre la vigente, mismo criterio ya usado para cargar
`grupos_unidad_forzada.csv` sobre `unidad_forzada`.

`obtener_grupos()` (logic/plantilla_canonica.py) agrega `exclusivo:
bool(r.get("exclusivo"))` a cada dict que devuelve, y
`construir_groups_desde_plantilla()` lo copia al armar `asign[...]` (junto a
`unidad_forzada`/`unidades_excluidas`, que ya siguen ese patrón).

## 4. Dónde se hace cumplir la regla

La restricción tiene que respetarse en los 4 puntos donde el motor decide
meter un grupo en una ruta ya existente — si solo se tocara uno, otro camino
volvería a juntar los grupos exclusivos:

- `_asignar_unidades` (Palanca 1: asignación inicial por peso)
- `_dia_alternativo` (Palanca 2: mover de día por sobrecupo)
- `_unidad_alternativa` (reubicación de una sub-parte tras partir un grupo)
- `_consolidar_solitarios` (Palanca 4: consolidar rutas de una sola parada)

Función pura compartida, nueva:

```python
def _respeta_exclusividad(asign, a, unidad, dia) -> bool:
    """True si `a` puede entrar a (unidad, dia) sin violar exclusividad:
    - si `a` es exclusivo, esa (unidad, dia) debe estar vacía (sin ningún
      otro grupo ya asignado ahí).
    - si `a` NO es exclusivo, esa (unidad, dia) no debe tener ya un grupo
      exclusivo distinto de `a`.
    """
```

Se agrega como filtro adicional junto al ya existente `_excluida()` en cada
uno de los 4 puntos (mismo patrón: una comprobación más sobre la lista de
candidatos, sin reemplazar la lógica existente).

## 5. Preferencia de día para grupos exclusivos (`_asignar_exclusivos`)

La Palanca 2 (`_dia_alternativo`) solo se activa por SOBRECUPO — nunca corre
solo para conseguir un camión más chico cuando el grande igual alcanza. Sin
una pieza nueva, un grupo exclusivo que un jueves ya no tiene camión chico
libre terminaría en un F350 aunque el viernes sí hubiera uno chico
disponible.

Nueva función, `_asignar_exclusivos(asign, pedidos, volumenes, coords,
vehiculos_cap, vehiculos_vol, cfg)`, que corre **antes** de la Palanca 1
general y solo procesa los grupos marcados `exclusivo` (orden determinista:
por `grupo` ascendente):

1. Para cada uno de sus `dias_admisibles` (en el orden ya definido, preferido
   primero), busca la unidad **vacía** (sin ningún otro grupo asignado ese
   día — ni siquiera de otro grupo exclusivo) de menor capacidad que lo
   admita sin violar restricciones (mismo `_restriccion_violada` que ya usa
   el resto del módulo).
2. Entre todas las combinaciones (día, unidad) encontradas en sus distintos
   días admisibles, elige la de **menor capacidad de camión**. Empate se
   rompe prefiriendo el día preferido del grupo, luego por orden de
   `dias_admisibles`, luego por nombre de unidad.
3. Fija `a["dia"]` y `a["unidad"]` a esa combinación de una vez. Como la
   Palanca 1 general (`_asignar_unidades`) resetea `a["unidad"] = None` para
   TODOS los grupos al iniciar, `_asignar_exclusivos` debe correr **después**
   de ese reset y `_asignar_unidades` debe saltarse los grupos ya fijados
   por él (no los reprocesa, ni siquiera para grupos exclusivos que
   quedaron sin combinación viable — ver casos borde).

Un grupo exclusivo **rígido de un solo día admisible** (como el 25) no tiene
a dónde moverse: si ese día no queda ningún camión vacío que le alcance sin
violar restricciones, `_asignar_exclusivos` cae al mismo criterio de último
recurso que ya usa `_asignar_unidades` (la unidad no excluida con más
espacio libre), y el grupo simplemente ocupa la más grande necesaria —
nunca comparte camión con otro grupo, pero tampoco se le garantiza un
camión de 1.5 si la flota no da para tanto ese día.

## 6. Casos borde

- **Grupo exclusivo sin ninguna unidad viable en ningún día admisible**
  (`unidades_excluidas` deja la flota entera afuera, o ninguna vacía
  alcanza su peso): mismo comportamiento ya existente para
  `SIN_UNIDAD_DISPONIBLE` — se registra la excepción y el grupo queda con
  el sentinel `"SIN_UNIDAD"`.
- **Dos grupos exclusivos con el mismo día preferido y solo una unidad
  vacía chica ese día**: el primero en procesarse (orden por `grupo`
  ascendente) se la queda; el segundo repite la búsqueda entre SUS propios
  días admisibles y puede terminar en otro día, en una unidad más grande
  el mismo día, o — si ninguno de sus días tiene alternativa — comparte
  día con el primero pero en una unidad DISTINTA (nunca la misma, por la
  regla de exclusividad).
- **Grupo exclusivo partido por sobrecupo** (Palanca 3, `_unidad_alternativa`
  para la sub-parte separada): la sub-parte hereda la exclusividad de su
  grupo de origen — nunca se reubica en una unidad que ya tenga otro grupo.
- **`_consolidar_solitarios` con un grupo exclusivo como única parada de su
  ruta**: nunca se ofrece como destino para consolidar OTRA solitaria, ni se
  mueve él mismo a consolidarse en otra ruta activa.

## 7. Fuera de alcance

- Marcar más grupos como exclusivos (solo 4, 24 y 25 por ahora — se puede
  extender después agregando filas al script de marcado, sin tocar código).
  Nota: Zona 24 corresponde al `grupo=24` en la numeración vigente (antes
  `grupo=31` en la plantilla vieja de 42 grupos — ver
  `docs/superpowers/specs/2026-08-26-reorganizacion-zonas-canonicas-design.md`
  tabla de grupo→zona).
- Cambiar el criterio general de consolidación (`_asignar_unidades`) para el
  resto de los grupos no marcados.
- UI para marcar/desmarcar grupos exclusivos — se administra por script,
  igual que `unidad_forzada`.

## 8. Pruebas

En `tests/test_convrp_logic.py` (ya tiene fixtures para `unidad_forzada`/
`unidades_excluidas`), casos puros nuevos:

1. Dos grupos exclusivos con demanda que cabría junta en un solo camión
   nunca terminan en la misma (unidad, día).
2. Un grupo exclusivo con dos días admisibles y solo camión chico libre en
   el segundo día termina ahí, no en un camión grande el primer día.
3. Un grupo exclusivo rígido de un solo día, sin camión chico libre ese
   día, usa el que le alcance (grande) sin fallar ni compartirlo.
4. Un grupo NO exclusivo se sigue consolidando con otros no-exclusivos
   exactamente igual que antes de este cambio (regresión — no debe cambiar
   nada para las 21 zonas restantes).
5. `_consolidar_solitarios` nunca mete una solitaria en una ruta con un
   grupo exclusivo, ni mueve un grupo exclusivo solitario a otra ruta.

Además, correr `scripts/smoke_convrp.py` contra las 9 semanas históricas
después del cambio, para confirmar que ninguna otra ruta se ve afectada
(el gate ya existente detecta dispersión anómala de viajes o pérdida de
determinismo).
