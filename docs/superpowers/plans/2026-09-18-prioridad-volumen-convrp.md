# Prioridad por volumen (antes que peso) en ConVRP — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Invertir el criterio de selección de unidad en la asignación "normal" de ConVRP (grupos regulares y grupos exclusivos) de peso-primero a volumen-primero, manteniendo el peso como límite duro y como desempate, sin tocar los caminos de rescate.

**Architecture:** Cambio quirúrgico dentro de `logic/convrp_logic.py`. Se modifican las claves de ordenamiento en dos funciones (`_asignar_unidades`, `_asignar_exclusivos`) y se sincroniza la simulación de "reserva de afinidad" para que siga prediciendo correctamente bajo la nueva regla. No hay cambios de esquema de datos ni de firma de funciones públicas.

**Tech Stack:** Python 3, pytest. Sin dependencias nuevas.

**Spec:** `docs/superpowers/specs/2026-09-18-prioridad-volumen-convrp-design.md`

---

## Contexto para quien ejecute este plan

`logic/convrp_logic.py` arma automáticamente la logística semanal repartiendo grupos de sucursales entre camiones. Hoy, cuando un grupo tiene que elegir entre varios camiones que le alcanzan, el código ordena los camiones candidatos por **capacidad de peso ascendente** y toma el primero que cumple TODAS las restricciones (peso, volumen, tiempo) — el volumen ya se valida como límite duro, pero nunca decide CUÁL camión se prueba primero. Con productos nuevos livianos pero voluminosos, eso ya no basta: hay que probar primero por **volumen ascendente**, y usar el peso solo como desempate.

Las pruebas viven en `tests/test_convrp_logic.py` y llaman en su mayoría a la función pública `construir_groups_desde_plantilla(pedidos, volumenes, coords, plantilla, vehiculos_cap, vehiculos_vol, cfg, ...)`. Ojo con el orden de argumentos: `volumenes` va ANTES que `plantilla`, y `vehiculos_cap` ANTES que `vehiculos_vol`. Algunas pruebas llaman directo a funciones privadas (`_asignar_unidades`, `_unidad_alternativa`) importándolas con `from logic.convrp_logic import _nombre`.

Dato importante ya verificado: casi todas las pruebas existentes pasan `volumenes={}` (vacío) y valores de `vehiculos_vol` iguales o generosos entre unidades — es decir, el volumen no discrimina en esas pruebas, así que con la nueva regla (volumen primero, peso como desempate) el resultado no cambia cuando el volumen queda empatado en 0 o en un valor alto. Ya se verificó a mano, tarea por tarea, que ninguna prueba existente debería romperse por este cambio — si alguna se rompe al correr la suite, es señal de un error en la implementación, no un cambio de contrato esperado.

Corre la suite completa así, desde la raíz del proyecto (`logistica_icg`):
```
pytest tests/test_convrp_logic.py -v
```

---

## Task 1: Helper `_volumen_grupo`

**Files:**
- Modify: `logic/convrp_logic.py:261-263`
- Test: `tests/test_convrp_logic.py` (agregar al final del archivo)

Hoy existe `_kg_grupo(a, pedidos)` que suma el peso de los miembros de un grupo. Falta su equivalente en volumen, que se usará en las Tareas 3 y 4.

- [ ] **Step 1: Escribir la prueba (debe fallar: la función no existe todavía)**

Agrega al final de `tests/test_convrp_logic.py`:

```python
def test_volumen_grupo_suma_el_volumen_de_los_miembros():
    from logic.convrp_logic import _volumen_grupo
    a = {"miembros": [1, 2, 3]}
    volumenes = {1: 2.5, 2: 1.0, 3: 0.5}
    assert _volumen_grupo(a, volumenes) == 4.0


def test_volumen_grupo_ignora_sucursales_sin_dato_de_volumen():
    from logic.convrp_logic import _volumen_grupo
    a = {"miembros": [1, 2]}
    volumenes = {1: 3.0}     # sucursal 2 sin dato
    assert _volumen_grupo(a, volumenes) == 3.0
```

- [ ] **Step 2: Correr las pruebas y confirmar que fallan**

Run: `pytest tests/test_convrp_logic.py -k test_volumen_grupo -v`
Expected: FAIL con `ImportError: cannot import name '_volumen_grupo'`

- [ ] **Step 3: Implementar el helper**

En `logic/convrp_logic.py`, justo después de `_kg_grupo` (línea 261-263 hoy):

```python
def _kg_grupo(a, pedidos):
    return sum(_num(pedidos.get(s)) for s in a["miembros"])


def _volumen_grupo(a, volumenes):
    return sum(_num(volumenes.get(s)) for s in a["miembros"])
```

- [ ] **Step 4: Correr las pruebas y confirmar que pasan**

Run: `pytest tests/test_convrp_logic.py -k test_volumen_grupo -v`
Expected: PASS (2 pruebas)

- [ ] **Step 5: Correr toda la suite para confirmar que nada se rompió**

Run: `pytest tests/test_convrp_logic.py -v`
Expected: todas las pruebas existentes siguen en PASS (el helper nuevo no se usa todavía en ningún lado).

- [ ] **Step 6: Commit**

```bash
git add logic/convrp_logic.py tests/test_convrp_logic.py
git commit -m "feat: agrega helper _volumen_grupo en ConVRP"
```

---

## Task 2: `_asignar_unidades` elige camión por volumen, luego por peso

**Files:**
- Modify: `logic/convrp_logic.py:550-556`
- Test: `tests/test_convrp_logic.py`

Este es el cambio principal: la función interna `_ordenar()`, dentro de `_asignar_unidades`, decide en qué orden se prueban los camiones candidatos para un grupo. Hoy la clave es `(peso_capacidad, -consolidación, -afinidad, nombre)`. Pasa a ser `(volumen_capacidad, peso_capacidad, -consolidación, -afinidad, nombre)`.

- [ ] **Step 1: Escribir las pruebas (deben fallar con el código actual)**

Agrega al final de `tests/test_convrp_logic.py`:

```python
def test_selecciona_por_volumen_aunque_el_peso_diga_lo_contrario():
    # A tiene menos capacidad de PESO (ganaría hoy); B tiene menos capacidad
    # de VOLUMEN (debe ganar con la nueva regla). Ambas le alcanzan al grupo
    # en las dos dimensiones -- no es un caso de "solo una cabe".
    plantilla = [_grupo(1, "FLEXIBLE", "LUNES", [1, 2], unidad_ref=None)]
    pedidos = {1: 400, 2: 400}          # 800 kg: cabe en A (1000) y en B (5000)
    volumenes = {1: 4, 2: 4}            # 8 m3: cabe en A (50) y en B (10)
    caps = {"A": 1000, "B": 5000}
    vols = {"A": 50, "B": 10}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, volumenes, COORDS, plantilla, caps, vols, _sin_tiempo())
    assert ("B", "LUNES") in groups, \
        "debio elegir B (menor volumen), no A (menor peso, criterio viejo)"
    assert ("A", "LUNES") not in groups


def test_empate_en_volumen_desempata_por_peso():
    # A y B EMPATADOS en volumen (30): el peso sigue siendo el desempate.
    plantilla = [_grupo(1, "FLEXIBLE", "LUNES", [1, 2], unidad_ref=None)]
    pedidos = {1: 400, 2: 400}          # 800 kg
    volumenes = {1: 10, 2: 10}          # 20 m3: cabe en ambas (30, 30)
    caps = {"A": 1000, "B": 5000}
    vols = {"A": 30, "B": 30}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, volumenes, COORDS, plantilla, caps, vols, _sin_tiempo())
    assert ("A", "LUNES") in groups, \
        "con volumen empatado, debe ganar el menor peso (A)"
```

- [ ] **Step 2: Correr las pruebas y confirmar que fallan**

Run: `pytest tests/test_convrp_logic.py -k "selecciona_por_volumen or empate_en_volumen" -v`
Expected: `test_selecciona_por_volumen_aunque_el_peso_diga_lo_contrario` FALLA (hoy elige "A", el de menor peso). `test_empate_en_volumen_desempata_por_peso` puede pasar ya de casualidad (A también gana por peso hoy) — no importa, lo confirmamos en passing después del cambio igual.

- [ ] **Step 3: Cambiar la clave de orden en `_ordenar()`**

En `logic/convrp_logic.py`, dentro de `_asignar_unidades` (líneas 550-556 hoy):

Antes:
```python
            def _ordenar(candidatos, af=af):
                return sorted(
                    candidatos,
                    key=lambda u: (_num(vehiculos_cap.get(u)),
                                   -sum(_num(pedidos.get(s))
                                        for s in _sids_de_ruta(asign, u, dia)),
                                   -_num(af.get(u)), str(u)))
```

Después:
```python
            def _ordenar(candidatos, af=af):
                return sorted(
                    candidatos,
                    key=lambda u: (_num(vehiculos_vol.get(u)),
                                   _num(vehiculos_cap.get(u)),
                                   -sum(_num(pedidos.get(s))
                                        for s in _sids_de_ruta(asign, u, dia)),
                                   -_num(af.get(u)), str(u)))
```

- [ ] **Step 4: Correr las pruebas nuevas y confirmar que pasan**

Run: `pytest tests/test_convrp_logic.py -k "selecciona_por_volumen or empate_en_volumen" -v`
Expected: PASS (2 pruebas)

- [ ] **Step 5: Correr toda la suite**

Run: `pytest tests/test_convrp_logic.py -v`
Expected: todas PASS. Si algo falla, es una prueba que dependía implícitamente de que el peso decidiera con volúmenes distintos entre unidades — revisa esa prueba puntual antes de continuar (no se detectó ningún caso así al revisar la suite completa, pero confírmalo).

- [ ] **Step 6: Commit**

```bash
git add logic/convrp_logic.py tests/test_convrp_logic.py
git commit -m "feat: _asignar_unidades elige camion por volumen, luego por peso"
```

---

## Task 3: Orden de turno de grupos y reserva de afinidad, sincronizados por volumen

**Files:**
- Modify: `logic/convrp_logic.py:476-479` (orden de turno)
- Modify: `logic/convrp_logic.py:535-540` (predicción de reserva de afinidad)
- Test: `tests/test_convrp_logic.py`

Estos dos cambios van juntos a propósito (ver spec, sección 4): el orden en que los grupos del día "toman turno" (first-fit decreasing) pasa de peso-descendente a volumen-descendente (con peso como desempate), y la simulación de "qué camión elegiría de verdad un grupo pendiente" (usada para reservarle ese camión) tiene que usar la MISMA regla que la decisión real (Task 2) — si no, la reserva predice mal y puede proteger un camión que el grupo pendiente nunca iba a elegir, o dejar sin protección el que sí iba a elegir.

- [ ] **Step 1: Escribir la prueba (debe fallar con el código actual)**

Agrega al final de `tests/test_convrp_logic.py`:

```python
def test_reserva_de_afinidad_predice_por_volumen_no_por_peso():
    # X e Y estan EMPATADAS en peso (3000 cada una) pero X tiene menos
    # volumen (12) que Y (25). Grupo 2 (pendiente, procesa despues por tener
    # menos volumen) tiene mas afinidad a Y (9) que a X (3) -- pero la
    # decision real (Task 2) manda por volumen, asi que grupo 2 en realidad
    # terminaria en X pase lo que pase con la afinidad. La reserva debe
    # predecir X (no Y) para no reservarle a grupo 2 un camion que no va a
    # usar y dejar a grupo 1 sin motivo real para ceder X.
    plantilla = [
        _grupo(1, "FLEXIBLE", "LUNES", [1, 2], unidad_ref=None),
        _grupo(2, "FLEXIBLE", "LUNES", [3, 4], unidad_ref=None),
    ]
    pedidos = {1: 200, 2: 200, 3: 250, 4: 250}      # peso irrelevante: sobra en ambas
    volumenes = {1: 5, 2: 5, 3: 4, 4: 4}            # grupo1 vol=10 (procesa primero), grupo2 vol=8
    caps = {"X": 3000, "Y": 3000}                   # EMPATADAS en peso
    vols = {"X": 12, "Y": 25}                       # X mas chica en volumen
    cfg = dict(cfg_por_defecto(), chequear_tiempo=False,
               afinidad_unidad={2: {"X": 3, "Y": 9}})   # mas afinidad a Y, la GRANDE
    groups, exc = construir_groups_desde_plantilla(
        pedidos, volumenes, COORDS, plantilla, caps, vols, cfg)
    assert sorted(m["sid"] for m in groups[("X", "LUNES")]) == [3, 4], \
        "grupo 2 debe terminar en X (menor volumen): la afinidad no debe torcer lo que en la realidad decide el volumen"
    assert sorted(m["sid"] for m in groups[("Y", "LUNES")]) == [1, 2], \
        "grupo 1 (procesa primero por tener mas volumen, sin afinidad) debe ceder X porque quedo reservada para el grupo 2 pendiente"
```

- [ ] **Step 2: Correr la prueba y confirmar que falla**

Run: `pytest tests/test_convrp_logic.py -k test_reserva_de_afinidad_predice_por_volumen_no_por_peso -v`
Expected: FAIL. Con el código actual, grupo 1 (que hoy procesa primero por ser el MÁS PESADO, ver Step 3 de este task) predice mal la reserva (usa peso para elegir entre X/Y empatadas en peso, así que cae en afinidad y reserva Y) y termina quedándose con X; grupo 2 termina en Y. El assert espera lo contrario.

- [ ] **Step 3: Cambiar el orden de turno (first-fit decreasing) a volumen**

En `logic/convrp_logic.py`, dentro de `_asignar_unidades` (líneas 476-479 hoy):

Antes:
```python
    for dia in sorted(por_dia, key=_orden_dia):
        # los grupos más pesados primero (first-fit decreasing), desempate por id
        gids = sorted(por_dia[dia],
                      key=lambda g: (-_kg_grupo(asign[g], pedidos), g))
```

Después:
```python
    for dia in sorted(por_dia, key=_orden_dia):
        # los grupos mas voluminosos primero (first-fit decreasing),
        # desempate por peso descendente y luego por id.
        gids = sorted(por_dia[dia],
                      key=lambda g: (-_volumen_grupo(asign[g], volumenes),
                                     -_kg_grupo(asign[g], pedidos), g))
```

- [ ] **Step 4: Sincronizar la predicción de la reserva de afinidad**

En `logic/convrp_logic.py`, dentro del mismo bucle, la simulación de "qué camión elegiría de verdad" el grupo pendiente (líneas 535-540 hoy):

Antes:
```python
                    kg2 = _kg_grupo(a2, pedidos)
                    elegibles = {u: v for u, v in af2_usable.items()
                                 if _num(vehiculos_cap.get(u)) >= kg2}
                    elegibles = elegibles or af2_usable
                    claim = min(elegibles, key=lambda u: (
                        _num(vehiculos_cap.get(u)), -af2_usable[u], u))
```

Después:
```python
                    vol2 = _volumen_grupo(a2, volumenes)
                    elegibles = {u: v for u, v in af2_usable.items()
                                 if _num(vehiculos_vol.get(u)) >= vol2}
                    elegibles = elegibles or af2_usable
                    claim = min(elegibles, key=lambda u: (
                        _num(vehiculos_vol.get(u)), _num(vehiculos_cap.get(u)),
                        -af2_usable[u], u))
```

- [ ] **Step 5: Correr la prueba nueva y confirmar que pasa**

Run: `pytest tests/test_convrp_logic.py -k test_reserva_de_afinidad_predice_por_volumen_no_por_peso -v`
Expected: PASS

- [ ] **Step 6: Correr toda la suite**

Run: `pytest tests/test_convrp_logic.py -v`
Expected: todas PASS, incluyendo `test_grupo_pesado_sin_afinidad_no_ocupa_la_reservada_de_uno_pendiente` y las demás pruebas de reserva de afinidad (usan `volumenes={}`, así que el volumen queda empatado en 0 para todos los grupos y el desempate cae en peso descendente, exactamente como antes).

- [ ] **Step 7: Commit**

```bash
git add logic/convrp_logic.py tests/test_convrp_logic.py
git commit -m "feat: orden de turno y reserva de afinidad usan volumen antes que peso"
```

---

## Task 4: `_asignar_exclusivos` elige camión por volumen, luego por peso

**Files:**
- Modify: `logic/convrp_logic.py:365-368` (orden de candidatas)
- Modify: `logic/convrp_logic.py:373` (criterio de "mejor opción" entre días)
- Test: `tests/test_convrp_logic.py`

Mismo cambio que la Tarea 2, para los grupos marcados `exclusivo` (nunca comparten camión con otro grupo).

- [ ] **Step 1: Escribir la prueba (debe fallar con el código actual)**

Agrega al final de `tests/test_convrp_logic.py`:

```python
def test_exclusivo_elige_por_volumen_aunque_el_peso_diga_lo_contrario():
    # Mismo caso que test_selecciona_por_volumen_aunque_el_peso_diga_lo_contrario
    # pero para un grupo exclusivo (pasa por _asignar_exclusivos, no por
    # _asignar_unidades).
    plantilla = [_grupo(1, "FLEXIBLE", "JUEVES", [1, 2], unidad_ref=None)]
    plantilla[0]["exclusivo"] = True
    pedidos = {1: 400, 2: 400}          # 800 kg: cabe en A (1000) y en B (5000)
    volumenes = {1: 4, 2: 4}            # 8 m3: cabe en A (50) y en B (10)
    caps = {"A": 1000, "B": 5000}
    vols = {"A": 50, "B": 10}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, volumenes, COORDS, plantilla, caps, vols, _sin_tiempo())
    assert sorted(m["sid"] for m in groups[("B", "JUEVES")]) == [1, 2], \
        "el grupo exclusivo debio elegir B (menor volumen), no A (menor peso)"
    assert ("A", "JUEVES") not in groups or not groups[("A", "JUEVES")]
```

- [ ] **Step 2: Correr la prueba y confirmar que falla**

Run: `pytest tests/test_convrp_logic.py -k test_exclusivo_elige_por_volumen -v`
Expected: FAIL (hoy elige "A", el de menor peso).

- [ ] **Step 3: Cambiar el orden de candidatas y el criterio de "mejor opción"**

En `logic/convrp_logic.py`, dentro de `_asignar_exclusivos` (líneas 365-373 hoy):

Antes:
```python
            candidatas = sorted(
                (u for u in vehiculos_cap if not _excluida(a, u)
                 and _respeta_exclusividad(asign, a, u, dia)),
                key=lambda u: (_num(vehiculos_cap.get(u)), str(u)))
            for unidad in candidatas:
                if _restriccion_violada(sorted(a["miembros"]), unidad, pedidos,
                                        volumenes, coords, vehiculos_cap,
                                        vehiculos_vol, cfg, dia=dia) is None:
                    opcion = (_num(vehiculos_cap.get(unidad)), idx, unidad, dia)
                    if mejor is None or opcion < mejor:
                        mejor = opcion
                    break   # candidatas ya viene ordenada por capacidad: la
                            # primera viable de este día es la más chica
```

Después:
```python
            candidatas = sorted(
                (u for u in vehiculos_cap if not _excluida(a, u)
                 and _respeta_exclusividad(asign, a, u, dia)),
                key=lambda u: (_num(vehiculos_vol.get(u)),
                               _num(vehiculos_cap.get(u)), str(u)))
            for unidad in candidatas:
                if _restriccion_violada(sorted(a["miembros"]), unidad, pedidos,
                                        volumenes, coords, vehiculos_cap,
                                        vehiculos_vol, cfg, dia=dia) is None:
                    opcion = (_num(vehiculos_vol.get(unidad)),
                              _num(vehiculos_cap.get(unidad)), idx, unidad, dia)
                    if mejor is None or opcion < mejor:
                        mejor = opcion
                    break   # candidatas ya viene ordenada por volumen (y
                            # peso como desempate): la primera viable de
                            # este dia es la mas chica
```

- [ ] **Step 4: Correr la prueba nueva y confirmar que pasa**

Run: `pytest tests/test_convrp_logic.py -k test_exclusivo_elige_por_volumen -v`
Expected: PASS

- [ ] **Step 5: Correr toda la suite**

Run: `pytest tests/test_convrp_logic.py -v`
Expected: todas PASS, incluyendo `test_dos_grupos_exclusivos_nunca_comparten_camion_aunque_el_peso_alcance` y `test_exclusivo_prefiere_otro_dia_admisible_a_camion_grande_en_su_dia_preferido` (usan `volumenes={}` y `vehiculos_vol` parejo en 99, así que no cambian).

- [ ] **Step 6: Commit**

```bash
git add logic/convrp_logic.py tests/test_convrp_logic.py
git commit -m "feat: _asignar_exclusivos elige camion por volumen, luego por peso"
```

---

## Task 5: Actualizar la documentación en código

**Files:**
- Modify: `logic/convrp_logic.py:9-27` (docstring del módulo)
- Modify: `logic/convrp_logic.py:410-420` (docstring de `_asignar_unidades`)
- Modify: `logic/convrp_logic.py:343-349` (docstring de `_asignar_exclusivos`)

No hay pruebas automatizadas para comentarios, pero este archivo tiene un historial largo de decisiones de negocio documentadas inline (afinidad, exclusividad, particiones) que otros cambios futuros van a leer y confiar — dejar la referencia a "se elige por PESO" ahí, ahora que ya no es cierto para la asignación normal, generaría confusión real.

- [ ] **Step 1: Actualizar el docstring del módulo**

En `logic/convrp_logic.py`, líneas 12-18:

Antes:
```
  - La UNIDAD se elige por PESO: cada grupo, sin excepción y sin preferencia,
    toma entre las unidades no excluidas y compatibles la de MENOR capacidad
    que le alcanza, desempatando por CONSOLIDACIÓN (la que ya lleva carga ese
    día) y luego por nombre. `unidad_ref` / `unidades_afines` / `unidad_forzada`
    son vestigiales: se guardan y se propagan, pero ya no se leen para decidir
    unidad. `unidades_excluidas` es la única prohibición dura -- ninguna
    palanca puede violarla, ni siquiera en el último recurso.
```

Después:
```
  - La UNIDAD se elige por VOLUMEN, luego por PESO (decisión de negocio
    2026-09-18: con productos livianos pero voluminosos, el peso dejó de ser
    buen indicador de si algo cabe): cada grupo, sin excepción y sin
    preferencia, toma entre las unidades no excluidas y compatibles la de
    MENOR volumen que le alcanza, desempatando por MENOR PESO cuando el
    volumen empata, luego por CONSOLIDACIÓN (la que ya lleva carga ese día) y
    luego por nombre. El peso sigue siendo límite duro -- nunca se manda a un
    grupo más peso del que el camión aguanta. `unidad_ref` / `unidades_afines`
    / `unidad_forzada` son vestigiales: se guardan y se propagan, pero ya no
    se leen para decidir unidad. `unidades_excluidas` es la única prohibición
    dura -- ninguna palanca puede violarla, ni siquiera en el último recurso.
```

- [ ] **Step 2: Actualizar "Orden de palancas ante sobrecupo"**

En `logic/convrp_logic.py`, líneas 25-27:

Antes:
```
    1) asignación de UNIDAD por peso dentro del mismo día (sin preferencia
       que mover: cada grupo elige directo la unidad no excluida que le
       alcanza, ver arriba)
```

Después:
```
    1) asignación de UNIDAD por volumen (luego peso) dentro del mismo día
       (sin preferencia que mover: cada grupo elige directo la unidad no
       excluida que le alcanza, ver arriba)
```

- [ ] **Step 3: Actualizar el docstring de `_asignar_unidades`**

En `logic/convrp_logic.py`, líneas 410-420:

Antes:
```
    Reparte los grupos de cada día entre las unidades: una sola pasada por
    peso descendente (first-fit decreasing). Cada grupo elige, entre las
    unidades NO excluidas (`unidades_excluidas` del grupo) y compatibles por
    coocurrencia que le alcanzan, la de MENOR capacidad -- nunca manda un
    grupo chico a una unidad grande de más si una chica ya le alcanza --
    desempatando por CONSOLIDACIÓN (la que ya lleva carga ese día, para no
    abrir un viaje nuevo: en el histórico un viaje lleva ~1.4 grupos, no 1.0),
    luego por AFINIDAD HISTÓRICA (`cfg["afinidad_unidad"]`: cuántas semanas
    ese grupo cayó en esa unidad -- sólo decide cuando capacidad y
    consolidación ya quedaron empatadas entre dos o más candidatos) y por
    último por nombre.
```

Después:
```
    Reparte los grupos de cada día entre las unidades: una sola pasada por
    volumen descendente, con peso descendente como desempate (first-fit
    decreasing; decisión de negocio 2026-09-18 -- ver módulo). Cada grupo
    elige, entre las unidades NO excluidas (`unidades_excluidas` del grupo) y
    compatibles por coocurrencia que le alcanzan, la de MENOR volumen --
    nunca manda un grupo chico a una unidad grande de más si una chica ya le
    alcanza -- desempatando por MENOR PESO cuando el volumen empata, luego
    por CONSOLIDACIÓN (la que ya lleva carga ese día, para no abrir un viaje
    nuevo: en el histórico un viaje lleva ~1.4 grupos, no 1.0), luego por
    AFINIDAD HISTÓRICA (`cfg["afinidad_unidad"]`: cuántas semanas ese grupo
    cayó en esa unidad -- sólo decide cuando volumen, peso y consolidación ya
    quedaron empatados entre dos o más candidatos) y por último por nombre.
```

- [ ] **Step 4: Actualizar el docstring de `_asignar_exclusivos`**

En `logic/convrp_logic.py`, líneas 343-349:

Antes:
```
    Para cada uno (orden determinista: `grupo` ascendente), prueba TODOS
    sus `dias_admisibles` (preferido primero) y en cada uno busca la unidad
    VACÍA (sin ningún otro grupo asignado ese día -- ni siquiera de otro
    exclusivo ya procesado) de menor capacidad que lo admita sin violar
    restricciones. Entre las combinaciones encontradas en sus distintos
    días, se queda con la de MENOR capacidad de camión; empate por orden de
    `dias_admisibles`, luego por nombre de unidad.
```

Después:
```
    Para cada uno (orden determinista: `grupo` ascendente), prueba TODOS
    sus `dias_admisibles` (preferido primero) y en cada uno busca la unidad
    VACÍA (sin ningún otro grupo asignado ese día -- ni siquiera de otro
    exclusivo ya procesado) de menor volumen que lo admita sin violar
    restricciones. Entre las combinaciones encontradas en sus distintos
    días, se queda con la de MENOR volumen; empate por MENOR PESO, luego por
    orden de `dias_admisibles`, luego por nombre de unidad.
```

- [ ] **Step 5: Correr toda la suite (los docstrings no afectan comportamiento, pero confirma que no se rompió nada al editar el archivo)**

Run: `pytest tests/test_convrp_logic.py -v`
Expected: todas PASS.

- [ ] **Step 6: Commit**

```bash
git add logic/convrp_logic.py
git commit -m "docs: actualiza comentarios de ConVRP a la prioridad volumen-luego-peso"
```

---

## Task 6: Regresión completa y cierre

**Files:**
- Modify: `docs/superpowers/specs/2026-09-18-prioridad-volumen-convrp-design.md` (estado)

- [ ] **Step 1: Correr toda la suite de ConVRP una vez más, de punta a punta**

Run: `pytest tests/test_convrp_logic.py -v`
Expected: todas las pruebas PASS (las preexistentes sin cambios de resultado, más las 7 pruebas nuevas de las Tareas 1-4).

- [ ] **Step 2: Correr también las pruebas de integración que tocan ConVRP, por si acaso**

Run: `pytest tests/test_integracion.py -v`
Expected: todas PASS. Si algo falla aquí, es probable que use una plantilla/fixture con pesos y volúmenes que antes empataban por peso y ahora empatan distinto por volumen -- revisa el caso puntual, no ajustes el código de producción para "hacer pasar" la prueba sin entender por qué cambió.

- [ ] **Step 3: Actualizar el estado del spec**

En `docs/superpowers/specs/2026-09-18-prioridad-volumen-convrp-design.md`, línea 4:

Antes:
```
**Estado:** Aprobado (diseño) — pendiente plan de implementación.
```

Después:
```
**Estado:** Implementado (2026-09-18) — ver plan `docs/superpowers/plans/2026-09-18-prioridad-volumen-convrp.md`.
```

- [ ] **Step 4: Commit final**

```bash
git add docs/superpowers/specs/2026-09-18-prioridad-volumen-convrp-design.md
git commit -m "docs: marca implementado el spec de prioridad por volumen en ConVRP"
```

---

## Self-review (para quien complete este plan)

- **Cobertura del spec:** Sección 3 (funciones que se tocan) → Tasks 2-4. Sección 4 (consistencia con reserva de afinidad) → Task 3. Sección 5 (qué no cambia) → ningún task toca `_unidad_alternativa`, `_dia_alternativo`, `_consolidar_solitarios`, `_espacio_libre` ni `_restriccion_violada`; verificado que ninguna de las tareas anteriores los modifica. Sección 6 (validación) → pruebas nuevas en cada task + Task 6 corre la suite completa.
- **Consistencia de tipos/nombres:** `_volumen_grupo(a, volumenes)` (Task 1) se usa con esa firma exacta en Task 3, sin variantes de nombre.
- **Nada queda pendiente:** no hay TODOs ni "ver después" en el código — los caminos de rescate quedan explícitamente sin tocar (decisión de alcance, no pendiente).
