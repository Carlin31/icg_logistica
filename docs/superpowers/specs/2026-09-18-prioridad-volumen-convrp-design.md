# Diseño: prioridad por volumen (antes que peso) en la asignación de unidad de ConVRP

**Fecha:** 2026-09-18
**Estado:** Aprobado (diseño) — pendiente plan de implementación.
**Módulos:** `logic/convrp_logic.py` (motor activo en producción).

## 1. Problema

La empresa agregó productos nuevos al inventario la semana pasada. Los jefes
reportaron que estos productos, aunque livianos, ocupan más volumen que los
productos históricos — al punto de que un jefe de prácticas, armando una ruta
a mano (el sistema aún no tenía los productos nuevos cargados), cargó el
camión T 25 con 1,039 kg (muy por debajo de su capacidad de 1.5 t) y el
pedido no cupo por volumen.

Hoy ConVRP (el motor que arma automáticamente la logística semanal, partiendo
de la plantilla canónica) decide qué camión le toca a cada grupo ordenando
los candidatos por **capacidad de peso ascendente** y usando el volumen sólo
como validación de sí/no cabe (`_restriccion_violada()`), nunca como criterio
para preferir un camión sobre otro. Con la nueva mezcla de productos, el peso
deja de ser buen indicador de si algo cabe físicamente, así que la prioridad
de selección debe invertirse: primero volumen, después peso.

## 2. Alcance

Cambio **enfocado**, no total: se tocan únicamente los dos puntos que deciden
la asignación "normal" de unidad — los que cubren la gran mayoría de los
grupos cada semana. Los caminos de rescate (mover de día, mover a otra unidad
del mismo día, consolidar sucursales solitarias, último recurso por espacio
libre) **no se tocan** en esta iteración; ya validan volumen como límite
duro, solo no lo usan para preferir entre opciones ya válidas. Se puede
revisar en una iteración futura si en la práctica no basta.

Tampoco se toca la regla de negocio explícita del 2026-08-11 (una sucursal
solitaria sólo se considera "al límite" si el camión está lleno **en peso**,
en `_consolidar_solitarios`) — es una decisión de negocio documentada aparte
y no debe cambiar sin que un jefe lo pida directamente.

El peso sigue siendo **límite duro** en todos lados: nunca se manda a un
grupo más peso del que el camión aguanta, sin importar cuánto volumen le
sobre. Lo único que cambia es cuál camión se prueba primero cuando hay varios
válidos.

## 3. Funciones que se tocan

1. **`_asignar_unidades()`** — asignación principal (Palanca 1).
   - La clave de orden de candidatos (`_ordenar()`, hoy
     `(peso_capacidad, -consolidación, -afinidad, nombre)`) pasa a
     `(volumen_capacidad, peso_capacidad, -consolidación, -afinidad, nombre)`.
   - El orden en que los grupos del día toman turno (first-fit decreasing,
     hoy `sorted(..., key=lambda g: (-_kg_grupo(...), g))`) pasa a ordenar
     primero por volumen del grupo descendente, con el peso como desempate:
     `(-_volumen_grupo(...), -_kg_grupo(...), g)`. Se agrega el helper
     `_volumen_grupo(a, volumenes)` (análogo a `_kg_grupo`, sumando
     `volumenes.get(s)` de los miembros del grupo).
2. **`_asignar_exclusivos()`** — misma decisión para los 3 grupos marcados
   como exclusivos (Zona 4, Zona 24, sub-grupo jueves Tuxtepec). La clave de
   orden de candidatas (`(peso_capacidad, nombre)`) pasa a
   `(volumen_capacidad, peso_capacidad, nombre)`, y el criterio de "mejor
   opción entre días admisibles" (`opcion = (peso_capacidad, idx_dia, unidad, dia)`)
   pasa a `(volumen_capacidad, peso_capacidad, idx_dia, unidad, dia)`.

## 4. Consistencia con la reserva de afinidad

Dentro de `_asignar_unidades()`, el mecanismo de "reserva de afinidad"
predice qué camión elegiría *de verdad* un grupo que todavía no tuvo su
turno esta pasada, para no reservarle un camión que en realidad no iba a
usar. Esa simulación (líneas ~535-540) hoy replica la regla real con
`vehiculos_cap` (peso). Si sólo se cambia la regla real y no esta simulación,
la predicción queda desincronizada — el mismo tipo de bug ya documentado en
el código (reservas que protegen un camión que el grupo real nunca iba a
elegir, ver comentario "T 25 reservada y vacía todo el día").

Por eso este cambio incluye actualizar esa simulación en el mismo commit:
- `kg2 = _kg_grupo(a2, pedidos)` se complementa con
  `vol2 = _volumen_grupo(a2, volumenes)`.
- El filtro `elegibles` pasa de `vehiculos_cap.get(u) >= kg2` a
  `vehiculos_vol.get(u) >= vol2` (con el mismo *fallback* a `af2_usable`
  completo si queda vacío, igual que hoy).
- `claim = min(elegibles, key=lambda u: (vehiculos_cap.get(u), -af2_usable[u], u))`
  pasa a `key=lambda u: (vehiculos_vol.get(u), vehiculos_cap.get(u), -af2_usable[u], u)`.

## 5. Qué no cambia

- Los 3 caminos de rescate (`_unidad_alternativa`, `_dia_alternativo`,
  `_consolidar_solitarios` y el "último recurso por espacio libre" de ambas
  funciones de asignación) — siguen ordenando/midiendo por peso.
- `_restriccion_violada()` — sigue comprobando PESO antes que VOLUMEN como
  "primera restricción que satura" (solo afecta la etiqueta de diagnóstico en
  el caso borde de que ambas se violen a la vez; no cambia si una ruta es
  válida o no).
- La lógica de partición ("pela desde el final" / "pela por PESO/VOLUMEN
  descendente") — ya es adaptativa: usa la métrica (peso o volumen) de la
  restricción que realmente disparó la partición.

## 6. Validación

`tests/test_convrp_logic.py` ya cubre `_asignar_unidades`/`_asignar_exclusivos`.
Se agregan casos nuevos con grupos "livianos pero voluminosos" (inspirados en
el caso real del T 25) para confirmar que ahora eligen camión por volumen, y
se revisan los casos existentes para confirmar que ningún caso donde peso y
volumen coinciden en orden cambia de resultado — en la flota actual (ver
actualización de `volumen_m3` del 2026-09-18) los niveles de peso y volumen
están alineados por familia de camión, así que la mayoría de los casos
existentes no debería cambiar de resultado, sólo los casos borde donde un
grupo cabe por peso en un camión pero no por volumen.

## 7. Riesgos

- Archivo sensible con historial de bugs finos de "predicción vs. decisión
  real" (ver sección 4) — mitigado actualizando ambas piezas en el mismo
  cambio.
- Si en el futuro se agregan camiones cuyo orden por peso y por volumen no
  coincida con el orden actual (p. ej. un camión chico en peso pero grande en
  volumen), el comportamiento cambiará más de lo que cambia hoy con la flota
  actual — es el comportamiento deseado, pero conviene confirmarlo si se
  agregan unidades nuevas a la flota.
