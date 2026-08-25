# Palanca 5 — Relleno de Capacidad Libre Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agregar una quinta palanca al motor ConVRP que, tras las 4 palancas existentes, rellena rutas con capacidad libre moviendo grupos ya desviados de su unidad/día preferido, priorizando devolverlos a casa.

**Architecture:** Nueva función pura `_rellenar_capacidad_libre()` en `logic/convrp_logic.py`, llamada al final de `construir_groups_desde_plantilla()` (después de la Palanca 4). Reutiliza `_restriccion_violada`, `_compatible_historico`, `_rutas_activas`, `_sids_de_ruta` y `_num` ya existentes en el mismo archivo. Gate obligatorio: `scripts/smoke_convrp.py` contra las 9 semanas canónicas.

**Tech Stack:** Python puro (sin BD, sin dependencias nuevas). Pruebas con `pytest` en `tests/test_convrp_logic.py`.

**Spec:** [docs/superpowers/specs/2026-08-25-relleno-capacidad-libre-design.md](../specs/2026-08-25-relleno-capacidad-libre-design.md)

---

## Contexto para quien implemente

`logic/convrp_logic.py` construye las rutas de la semana AJUSTANDO una plantilla canónica histórica. Cada "grupo" es un conjunto de sucursales que viajan juntas; tiene `unidad_ref` (vehículo preferido) y `dia_preferido` (día preferido), que son la PREFERENCIA histórica, no una asignación dura. Cuando una ruta se satura, las palancas existentes mueven grupos: 1) a otra unidad el mismo día, 2) a otro día admisible, 3) como último recurso, parten el grupo, y 4) ninguna ruta se queda con una sola sucursal si hay capacidad en otra ruta activa.

Ninguna de esas 4 palancas revisa una ruta que YA es válida (ni sobrecargada ni solitaria) para ver si algo más cabría. El resultado observado en producción: un grupo FLEXIBLE puede ceder su lugar durante la resolución de sobrecupo y nadie lo trae de vuelta aunque después haya espacio libre en su unidad/día preferido.

El `asign` interno (dict `{grupo_id: {...}}`) usa estos campos por grupo — memorízalos, se usan en todo el archivo:
- `grupo` (int), `rigidez` ("RIGIDO"/"FLEXIBLE"), `unidad` (str, ACTUAL), `unidad_ref` (str, preferido), `dia` (str, ACTUAL), `dia_preferido` (str, preferido), `dias_admisibles` (list[str]), `miembros` (list[sid]), `unidad_forzada` (bool, opcional, sólo con `.get()`).

Un grupo "desviado" es uno donde `(unidad, dia) != (unidad_ref, dia_preferido)`.

## File Structure

- **Modify:** `logic/convrp_logic.py` — agrega la constante `CONVRP_RELLENO_CAPACIDAD`, la entrada en `cfg_por_defecto()`, la función `_rellenar_capacidad_libre()` (nueva, colocada justo después de `_consolidar_solitarios`), y la llamada dentro de `construir_groups_desde_plantilla()`.
- **Modify:** `tests/test_convrp_logic.py` — todos los tests nuevos van al final del archivo, reutilizando los helpers `_grupo`, `_cfg`, `_sin_tiempo`, `COORDS` ya existentes.
- **Modify:** `README.md` — una línea mencionando la nueva palanca donde ya se describe el orden (unidad → día → partir).

---

### Task 1: Flag `CONVRP_RELLENO_CAPACIDAD` y entrada en `cfg_por_defecto()`

**Files:**
- Modify: `logic/convrp_logic.py:44-64`
- Test: `tests/test_convrp_logic.py`

- [ ] **Step 1: Write the failing test**

Agrega al final de `tests/test_convrp_logic.py`:

```python
# ══ 10. Palanca 5: relleno de capacidad libre ═══════════════════════════════
def test_cfg_por_defecto_incluye_relleno_capacidad_activado():
    assert cfg_por_defecto()["relleno_capacidad"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_convrp_logic.py::test_cfg_por_defecto_incluye_relleno_capacidad_activado -v`
Expected: FAIL con `KeyError: 'relleno_capacidad'`

- [ ] **Step 3: Write minimal implementation**

En `logic/convrp_logic.py`, después de la línea `CONVRP_DEPOT = (18.87, -96.95)` (línea 51):

```python
# Interruptor dedicado de la Palanca 5 (relleno de capacidad libre). Permite
# apagarla sin tocar CONVRP_ACTIVO si algo sale mal en producción -- mismo
# patrón que REBALANCEO_GEOGRAFICO en historico_logic.py.
CONVRP_RELLENO_CAPACIDAD = True
```

Y en `cfg_por_defecto()`, agrega la clave `"relleno_capacidad"`:

```python
def cfg_por_defecto() -> dict:
    return {
        "aviso_paradas": CONVRP_AVISO_PARADAS,
        "max_iteraciones": CONVRP_MAX_ITERACIONES,
        "chequear_tiempo": CONVRP_CHEQUEAR_TIEMPO,
        "hora_salida_min": CONVRP_HORA_SALIDA_MIN,
        "hora_cierre_min": CONVRP_HORA_CIERRE_MIN,
        "velocidad_kmh": CONVRP_VELOCIDAD_KMH,
        "velocidad_por_tramo": CONVRP_VELOCIDAD_POR_TRAMO,
        "depot": CONVRP_DEPOT,
        "relleno_capacidad": CONVRP_RELLENO_CAPACIDAD,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_convrp_logic.py::test_cfg_por_defecto_incluye_relleno_capacidad_activado -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add logic/convrp_logic.py tests/test_convrp_logic.py
git commit -m "feat: agrega flag CONVRP_RELLENO_CAPACIDAD para la Palanca 5"
```

---

### Task 2: Núcleo de `_rellenar_capacidad_libre` — un candidato válido a la vez

**Files:**
- Modify: `logic/convrp_logic.py` (nueva función después de `_consolidar_solitarios`, línea 518)
- Test: `tests/test_convrp_logic.py`

- [ ] **Step 1: Write the failing tests**

Agrega en `tests/test_convrp_logic.py`, después del test de Task 1:

```python
def test_relleno_capacidad_mueve_grupo_compatible_a_ruta_con_espacio():
    from logic.convrp_logic import _rellenar_capacidad_libre
    asign = {
        1: {"grupo": 1, "unidad": "V1", "dia": "LUNES", "miembros": [1, 2],
            "unidad_ref": "V1", "dia_preferido": "LUNES", "rigidez": "RIGIDO",
            "dias_admisibles": ["LUNES"]},
        2: {"grupo": 2, "unidad": "V2", "dia": "LUNES", "miembros": [3, 4],
            "unidad_ref": "V3", "dia_preferido": "MARTES", "rigidez": "FLEXIBLE",
            "dias_admisibles": ["LUNES"]},           # desviado de su hogar V3/MARTES
    }
    pedidos = {1: 100, 2: 100, 3: 100, 4: 100}
    caps = {"V1": 1000, "V2": 1000, "V3": 1000}
    coocurrencia = {frozenset((1, 2)): 1}
    exc = _rellenar_capacidad_libre(
        asign, pedidos, {}, {}, caps, {}, _sin_tiempo(coocurrencia_grupos=coocurrencia), {})
    assert asign[2]["unidad"] == "V1" and asign[2]["dia"] == "LUNES"
    assert any(e["tipo"] == "RELLENO_CAPACIDAD_LIBRE" for e in exc)


def test_relleno_capacidad_no_mueve_sin_coocurrencia():
    from logic.convrp_logic import _rellenar_capacidad_libre
    asign = {
        1: {"grupo": 1, "unidad": "V1", "dia": "LUNES", "miembros": [1, 2],
            "unidad_ref": "V1", "dia_preferido": "LUNES", "rigidez": "RIGIDO",
            "dias_admisibles": ["LUNES"]},
        2: {"grupo": 2, "unidad": "V2", "dia": "LUNES", "miembros": [3, 4],
            "unidad_ref": "V3", "dia_preferido": "MARTES", "rigidez": "FLEXIBLE",
            "dias_admisibles": ["LUNES"]},
    }
    pedidos = {1: 100, 2: 100, 3: 100, 4: 100}
    caps = {"V1": 1000, "V2": 1000, "V3": 1000}
    exc = _rellenar_capacidad_libre(
        asign, pedidos, {}, {}, caps, {}, _sin_tiempo(coocurrencia_grupos={}), {})
    assert asign[2]["unidad"] == "V2" and asign[2]["dia"] == "LUNES"
    assert not exc


def test_relleno_capacidad_no_mueve_grupo_con_unidad_forzada():
    from logic.convrp_logic import _rellenar_capacidad_libre
    asign = {
        1: {"grupo": 1, "unidad": "V1", "dia": "LUNES", "miembros": [1, 2],
            "unidad_ref": "V1", "dia_preferido": "LUNES", "rigidez": "RIGIDO",
            "dias_admisibles": ["LUNES"]},
        2: {"grupo": 2, "unidad": "V2", "dia": "LUNES", "miembros": [3, 4],
            "unidad_ref": "V3", "dia_preferido": "MARTES", "rigidez": "FLEXIBLE",
            "dias_admisibles": ["LUNES"], "unidad_forzada": True},
    }
    pedidos = {1: 100, 2: 100, 3: 100, 4: 100}
    caps = {"V1": 1000, "V2": 1000, "V3": 1000}
    coocurrencia = {frozenset((1, 2)): 1}
    exc = _rellenar_capacidad_libre(
        asign, pedidos, {}, {}, caps, {}, _sin_tiempo(coocurrencia_grupos=coocurrencia), {})
    assert asign[2]["unidad"] == "V2"
    assert not exc


def test_relleno_capacidad_no_mueve_fuera_de_dias_admisibles():
    from logic.convrp_logic import _rellenar_capacidad_libre
    asign = {
        1: {"grupo": 1, "unidad": "V1", "dia": "LUNES", "miembros": [1, 2],
            "unidad_ref": "V1", "dia_preferido": "LUNES", "rigidez": "RIGIDO",
            "dias_admisibles": ["LUNES"]},
        2: {"grupo": 2, "unidad": "V2", "dia": "LUNES", "miembros": [3, 4],
            "unidad_ref": "V3", "dia_preferido": "MARTES", "rigidez": "FLEXIBLE",
            "dias_admisibles": ["MARTES"]},          # LUNES no es admisible para él
    }
    pedidos = {1: 100, 2: 100, 3: 100, 4: 100}
    caps = {"V1": 1000, "V2": 1000, "V3": 1000}
    coocurrencia = {frozenset((1, 2)): 1}
    exc = _rellenar_capacidad_libre(
        asign, pedidos, {}, {}, caps, {}, _sin_tiempo(coocurrencia_grupos=coocurrencia), {})
    assert asign[2]["unidad"] == "V2"
    assert not exc


def test_relleno_capacidad_no_mueve_grupo_que_ya_esta_en_su_hogar():
    from logic.convrp_logic import _rellenar_capacidad_libre
    asign = {
        1: {"grupo": 1, "unidad": "V1", "dia": "LUNES", "miembros": [1, 2],
            "unidad_ref": "V1", "dia_preferido": "LUNES", "rigidez": "RIGIDO",
            "dias_admisibles": ["LUNES"]},
        2: {"grupo": 2, "unidad": "V2", "dia": "LUNES", "miembros": [3, 4],
            "unidad_ref": "V2", "dia_preferido": "LUNES", "rigidez": "FLEXIBLE",
            "dias_admisibles": ["LUNES"]},           # ya está en SU propio hogar
    }
    pedidos = {1: 100, 2: 100, 3: 100, 4: 100}
    caps = {"V1": 1000, "V2": 1000}
    coocurrencia = {frozenset((1, 2)): 1}
    exc = _rellenar_capacidad_libre(
        asign, pedidos, {}, {}, caps, {}, _sin_tiempo(coocurrencia_grupos=coocurrencia), {})
    assert asign[2]["unidad"] == "V2"              # nunca se toca: no está desviado
    assert not exc
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_convrp_logic.py -k relleno_capacidad -v`
Expected: FAIL con `ImportError: cannot import name '_rellenar_capacidad_libre'`

- [ ] **Step 3: Write minimal implementation**

En `logic/convrp_logic.py`, agrega esta función nueva justo después de `_consolidar_solitarios` (después de la línea 518, antes de `def construir_groups_desde_plantilla`):

```python
def _rellenar_capacidad_libre(asign, pedidos, volumenes, coords, vehiculos_cap,
                              vehiculos_vol, cfg, kg_may):
    """
    Palanca 5 (último paso, después de consolidar solitarias): ninguna ruta
    debe quedar con capacidad libre mientras exista, en otra unidad/día
    admisible, un grupo YA DESVIADO de su propio unidad_ref/dia_preferido
    que quepa completo -- regla de negocio del 2026-08-25, encontrada al
    revisar un caso real: el grupo 19 (Amatitlán/Carlos A. Carrillo 2,
    FLEXIBLE, unidad_ref F 350_1) cedía su lugar por sobrecupo en algún
    punto del reparto (los FLEXIBLE ceden primero, ver
    `_candidatos_a_mover`) y nada lo traía de vuelta aunque después quedara
    espacio libre en F 350_1/MARTES, su hogar histórico.

    Sólo son candidatos los grupos que YA ESTÁN DESVIADOS de su propio
    (unidad_ref, dia_preferido) -- un grupo que nunca se movió de su lugar
    preferido no se toca, para no sacarlo de su hogar sólo por rellenar el
    espacio de OTRA ruta.

    Determinista: cada grupo se mueve como máximo UNA vez en toda la
    pasada -- una sola pasada sobre las rutas activas, sin punto fijo,
    mismo criterio que la Palanca 2 y `_consolidar_solitarios` para no
    reproducir el tipo de oscilación ya documentado con mayoristas.
    """
    excepciones: list = []
    coocurrencia = cfg.get("coocurrencia_grupos")
    movidos: set = set()

    def _ocupado(unidad, dia):
        sids = _sids_de_ruta(asign, unidad, dia)
        return sum(_num(pedidos.get(s)) + _num(kg_may.get(s)) for s in sids)

    def _ocupacion_pct(unidad, dia):
        cap = _num(vehiculos_cap.get(unidad))
        return (_ocupado(unidad, dia) / cap) if cap else 1.0

    orden_rutas = sorted(_rutas_activas(asign), key=lambda k: (_ocupacion_pct(*k), k))

    for (unidad, dia) in orden_rutas:
        candidatos = []
        for gid in sorted(asign):
            a = asign[gid]
            if a["grupo"] in movidos:
                continue
            if (a["unidad"], a["dia"]) == (unidad, dia):
                continue
            if a.get("unidad_forzada"):
                continue
            if (a["unidad"], a["dia"]) == (a["unidad_ref"], a["dia_preferido"]):
                continue
            if dia not in a["dias_admisibles"]:
                continue
            if not _compatible_historico(a["grupo"], unidad, dia, asign, coocurrencia):
                continue
            destino = sorted(_sids_de_ruta(asign, unidad, dia) + list(a["miembros"]))
            if _restriccion_violada(destino, unidad, pedidos, volumenes, coords,
                                    vehiculos_cap, vehiculos_vol, cfg, dia=dia,
                                    kg_mayoristas=kg_may) is not None:
                continue
            candidatos.append(a)

        if not candidatos:
            continue

        elegido = candidatos[0]
        excepciones.append({
            "tipo": "RELLENO_CAPACIDAD_LIBRE", "grupo": elegido["grupo"],
            "rigidez": elegido["rigidez"], "restriccion": None,
            "desde_unidad": elegido["unidad"], "desde_dia": elegido["dia"],
            "a_unidad": unidad, "a_dia": dia,
            "motivo_regreso_hogar": False,
            "motivo": f"{unidad}/{dia} con capacidad libre; se acomodó el "
                      f"grupo {elegido['grupo']} desde "
                      f"{elegido['unidad']}/{elegido['dia']}",
        })
        elegido["unidad"] = unidad
        elegido["dia"] = dia
        movidos.add(elegido["grupo"])

    return excepciones
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_convrp_logic.py -k relleno_capacidad -v`
Expected: 5 PASSED

- [ ] **Step 5: Commit**

```bash
git add logic/convrp_logic.py tests/test_convrp_logic.py
git commit -m "feat: implementa el núcleo de la Palanca 5 (relleno de capacidad libre)"
```

---

### Task 3: Priorizar "regreso a casa" sobre maximizar ocupación

**Files:**
- Modify: `logic/convrp_logic.py` (reemplaza el bloque de selección dentro de `_rellenar_capacidad_libre`)
- Test: `tests/test_convrp_logic.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_relleno_capacidad_prioriza_regreso_a_casa_sobre_maximizar_ocupacion():
    from logic.convrp_logic import _rellenar_capacidad_libre
    asign = {
        1: {"grupo": 1, "unidad": "V1", "dia": "LUNES", "miembros": [1],
            "unidad_ref": "V1", "dia_preferido": "LUNES", "rigidez": "RIGIDO",
            "dias_admisibles": ["LUNES"]},
        2: {"grupo": 2, "unidad": "V2", "dia": "LUNES", "miembros": [2],
            "unidad_ref": "V3", "dia_preferido": "MIERCOLES", "rigidez": "FLEXIBLE",
            "dias_admisibles": ["LUNES"]},           # desviado, NO es hogar de V1/LUNES, kg alto
        3: {"grupo": 3, "unidad": "V3", "dia": "MARTES", "miembros": [3],
            "unidad_ref": "V1", "dia_preferido": "LUNES", "rigidez": "FLEXIBLE",
            "dias_admisibles": ["LUNES", "MARTES"]}, # desviado, SÍ es hogar de V1/LUNES, kg bajo
    }
    pedidos = {1: 100, 2: 850, 3: 500}
    caps = {"V1": 1000, "V2": 5000, "V3": 5000}
    coocurrencia = {frozenset((1, 2)): 1, frozenset((1, 3)): 1}
    exc = _rellenar_capacidad_libre(
        asign, pedidos, {}, {}, caps, {}, _sin_tiempo(coocurrencia_grupos=coocurrencia), {})
    assert asign[3]["unidad"] == "V1" and asign[3]["dia"] == "LUNES"
    assert asign[2]["unidad"] == "V2" and asign[2]["dia"] == "LUNES"
    relleno = [e for e in exc if e["tipo"] == "RELLENO_CAPACIDAD_LIBRE"]
    assert len(relleno) == 1 and relleno[0]["grupo"] == 3
    assert relleno[0]["motivo_regreso_hogar"] is True


def test_relleno_capacidad_sin_candidato_en_casa_maximiza_ocupacion():
    from logic.convrp_logic import _rellenar_capacidad_libre
    asign = {
        1: {"grupo": 1, "unidad": "V1", "dia": "LUNES", "miembros": [1],
            "unidad_ref": "V1", "dia_preferido": "LUNES", "rigidez": "RIGIDO",
            "dias_admisibles": ["LUNES"]},
        2: {"grupo": 2, "unidad": "V2", "dia": "LUNES", "miembros": [2],
            "unidad_ref": "V3", "dia_preferido": "MARTES", "rigidez": "FLEXIBLE",
            "dias_admisibles": ["LUNES"]},           # desviado, ninguno es hogar de V1/LUNES
        3: {"grupo": 3, "unidad": "V3", "dia": "MARTES", "miembros": [3],
            "unidad_ref": "V4", "dia_preferido": "JUEVES", "rigidez": "FLEXIBLE",
            "dias_admisibles": ["LUNES", "MARTES"]}, # desviado, kg alto: deja mejor % en V1
    }
    pedidos = {1: 100, 2: 200, 3: 850}
    caps = {"V1": 1000, "V2": 5000, "V3": 5000, "V4": 5000}
    coocurrencia = {frozenset((1, 2)): 1, frozenset((1, 3)): 1}
    exc = _rellenar_capacidad_libre(
        asign, pedidos, {}, {}, caps, {}, _sin_tiempo(coocurrencia_grupos=coocurrencia), {})
    assert asign[3]["unidad"] == "V1" and asign[3]["dia"] == "LUNES"
    relleno = [e for e in exc if e["tipo"] == "RELLENO_CAPACIDAD_LIBRE"]
    assert relleno[0]["grupo"] == 3
    assert relleno[0]["motivo_regreso_hogar"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_convrp_logic.py -k "regreso_a_casa or sin_candidato_en_casa" -v`
Expected: FAIL — `test_relleno_capacidad_prioriza_regreso_a_casa_sobre_maximizar_ocupacion` falla porque el núcleo del Task 2 elige `candidatos[0]` (por `gid` ascendente = grupo 2, no grupo 3).

- [ ] **Step 3: Write minimal implementation**

En `logic/convrp_logic.py`, dentro de `_rellenar_capacidad_libre`, reemplaza este bloque:

```python
        if not candidatos:
            continue

        elegido = candidatos[0]
        excepciones.append({
            "tipo": "RELLENO_CAPACIDAD_LIBRE", "grupo": elegido["grupo"],
            "rigidez": elegido["rigidez"], "restriccion": None,
            "desde_unidad": elegido["unidad"], "desde_dia": elegido["dia"],
            "a_unidad": unidad, "a_dia": dia,
            "motivo_regreso_hogar": False,
            "motivo": f"{unidad}/{dia} con capacidad libre; se acomodó el "
                      f"grupo {elegido['grupo']} desde "
                      f"{elegido['unidad']}/{elegido['dia']}",
        })
        elegido["unidad"] = unidad
        elegido["dia"] = dia
        movidos.add(elegido["grupo"])
```

por:

```python
        if not candidatos:
            continue

        en_casa = [a for a in candidatos
                  if a["unidad_ref"] == unidad and a["dia_preferido"] == dia]
        pool = en_casa or candidatos

        cap = _num(vehiculos_cap.get(unidad))
        ocupado_actual = _ocupado(unidad, dia)

        def _kg_candidato(a):
            return sum(_num(pedidos.get(s)) + _num(kg_may.get(s)) for s in a["miembros"])

        def _pct_resultante(a, _ocupado=ocupado_actual, _cap=cap):
            return ((_ocupado + _kg_candidato(a)) / _cap) if _cap else 0.0

        pool_ordenado = sorted(pool, key=lambda a: (-_pct_resultante(a), a["grupo"]))
        elegido = pool_ordenado[0]
        es_regreso = bool(en_casa)

        excepciones.append({
            "tipo": "RELLENO_CAPACIDAD_LIBRE", "grupo": elegido["grupo"],
            "rigidez": elegido["rigidez"], "restriccion": None,
            "desde_unidad": elegido["unidad"], "desde_dia": elegido["dia"],
            "a_unidad": unidad, "a_dia": dia,
            "motivo_regreso_hogar": es_regreso,
            "motivo": f"{unidad}/{dia} con capacidad libre; se acomodó el "
                      f"grupo {elegido['grupo']} desde "
                      f"{elegido['unidad']}/{elegido['dia']}"
                      + (" (regresa a su unidad/día preferido)" if es_regreso else ""),
        })
        elegido["unidad"] = unidad
        elegido["dia"] = dia
        movidos.add(elegido["grupo"])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_convrp_logic.py -k relleno_capacidad -v`
Expected: 7 PASSED (los 5 del Task 2 + los 2 nuevos)

- [ ] **Step 5: Commit**

```bash
git add logic/convrp_logic.py tests/test_convrp_logic.py
git commit -m "feat: la Palanca 5 prioriza devolver grupos a su unidad/dia preferido"
```

---

### Task 4: Rellenar con más de un grupo por ruta en la misma pasada

**Files:**
- Modify: `logic/convrp_logic.py`
- Test: `tests/test_convrp_logic.py`

- [ ] **Step 1: Write the failing test**

```python
def test_relleno_capacidad_puede_rellenar_con_mas_de_un_grupo():
    from logic.convrp_logic import _rellenar_capacidad_libre
    asign = {
        1: {"grupo": 1, "unidad": "V1", "dia": "LUNES", "miembros": [1],
            "unidad_ref": "V1", "dia_preferido": "LUNES", "rigidez": "RIGIDO",
            "dias_admisibles": ["LUNES"]},
        2: {"grupo": 2, "unidad": "V2", "dia": "LUNES", "miembros": [2],
            "unidad_ref": "V3", "dia_preferido": "MARTES", "rigidez": "FLEXIBLE",
            "dias_admisibles": ["LUNES"]},
        3: {"grupo": 3, "unidad": "V3", "dia": "MARTES", "miembros": [3],
            "unidad_ref": "V4", "dia_preferido": "JUEVES", "rigidez": "FLEXIBLE",
            "dias_admisibles": ["LUNES", "MARTES"]},
    }
    pedidos = {1: 100, 2: 300, 3: 300}
    caps = {"V1": 1000, "V2": 1000, "V3": 1000, "V4": 1000}
    # Los tres pares necesitan precedente: una vez que grupo 2 entra a V1/LUNES,
    # _compatible_historico exige coocurrencia con TODOS los miembros ya
    # presentes (grupo 1 Y grupo 2) para aceptar a grupo 3 después.
    coocurrencia = {frozenset((1, 2)): 1, frozenset((1, 3)): 1, frozenset((2, 3)): 1}
    exc = _rellenar_capacidad_libre(
        asign, pedidos, {}, {}, caps, {}, _sin_tiempo(coocurrencia_grupos=coocurrencia), {})
    assert asign[2]["unidad"] == "V1" and asign[2]["dia"] == "LUNES"
    assert asign[3]["unidad"] == "V1" and asign[3]["dia"] == "LUNES"
    relleno = [e for e in exc if e["tipo"] == "RELLENO_CAPACIDAD_LIBRE"]
    assert len(relleno) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_convrp_logic.py::test_relleno_capacidad_puede_rellenar_con_mas_de_un_grupo -v`
Expected: FAIL — con el `for` simple del Task 2/3, V1/LUNES sólo recibe UN grupo (el de mejor ajuste) y se pasa a la siguiente ruta; sólo se registra 1 excepción, no 2.

- [ ] **Step 3: Write minimal implementation**

En `logic/convrp_logic.py`, dentro de `_rellenar_capacidad_libre`, envuelve el cuerpo del `for (unidad, dia) in orden_rutas:` en un `while True:` para que una ruta destino siga recibiendo grupos mientras haya candidatos válidos. Reemplaza:

```python
    for (unidad, dia) in orden_rutas:
        candidatos = []
        for gid in sorted(asign):
```

por:

```python
    for (unidad, dia) in orden_rutas:
        while True:
            candidatos = []
            for gid in sorted(asign):
```

Y reemplaza el resto del cuerpo del `for` (desde `a = asign[gid]` hasta el final del bloque, incluyendo el `if not candidatos: continue` y todo lo que sigue) indentándolo un nivel más (dentro del `while True:`), y cambia `if not candidatos: continue` por `if not candidatos: break` (rompe el `while`, no el `for` de rutas). El cuerpo completo de la función queda:

```python
def _rellenar_capacidad_libre(asign, pedidos, volumenes, coords, vehiculos_cap,
                              vehiculos_vol, cfg, kg_may):
    """
    Palanca 5 (último paso, después de consolidar solitarias): ninguna ruta
    debe quedar con capacidad libre mientras exista, en otra unidad/día
    admisible, un grupo YA DESVIADO de su propio unidad_ref/dia_preferido
    que quepa completo -- regla de negocio del 2026-08-25, encontrada al
    revisar un caso real: el grupo 19 (Amatitlán/Carlos A. Carrillo 2,
    FLEXIBLE, unidad_ref F 350_1) cedía su lugar por sobrecupo en algún
    punto del reparto (los FLEXIBLE ceden primero, ver
    `_candidatos_a_mover`) y nada lo traía de vuelta aunque después quedara
    espacio libre en F 350_1/MARTES, su hogar histórico.

    Sólo son candidatos los grupos que YA ESTÁN DESVIADOS de su propio
    (unidad_ref, dia_preferido) -- un grupo que nunca se movió de su lugar
    preferido no se toca, para no sacarlo de su hogar sólo por rellenar el
    espacio de OTRA ruta.

    Recorre las rutas activas de MENOR a MAYOR % de ocupación (la más vacía
    elige primero) y, para cada una, rellena repetidamente con el mejor
    grupo candidato disponible hasta que ya no quepa ninguno más:
      1. Si algún candidato tiene a esta ruta como su propio
         unidad_ref/dia_preferido (puede volver a casa), se prefiere
         siempre sobre cualquier otro.
      2. Si ninguno "vuelve a casa", se elige el que deje la ocupación más
         cerca del 100 % sin excederla.

    Determinista: cada grupo se mueve como máximo UNA vez en toda la
    pasada -- una sola pasada sobre las rutas activas, sin punto fijo,
    mismo criterio que la Palanca 2 y `_consolidar_solitarios` para no
    reproducir el tipo de oscilación ya documentado con mayoristas.
    """
    excepciones: list = []
    coocurrencia = cfg.get("coocurrencia_grupos")
    movidos: set = set()

    def _ocupado(unidad, dia):
        sids = _sids_de_ruta(asign, unidad, dia)
        return sum(_num(pedidos.get(s)) + _num(kg_may.get(s)) for s in sids)

    def _ocupacion_pct(unidad, dia):
        cap = _num(vehiculos_cap.get(unidad))
        return (_ocupado(unidad, dia) / cap) if cap else 1.0

    def _kg_candidato(a):
        return sum(_num(pedidos.get(s)) + _num(kg_may.get(s)) for s in a["miembros"])

    orden_rutas = sorted(_rutas_activas(asign), key=lambda k: (_ocupacion_pct(*k), k))

    for (unidad, dia) in orden_rutas:
        cap = _num(vehiculos_cap.get(unidad))
        while True:
            candidatos = []
            for gid in sorted(asign):
                a = asign[gid]
                if a["grupo"] in movidos:
                    continue
                if (a["unidad"], a["dia"]) == (unidad, dia):
                    continue
                if a.get("unidad_forzada"):
                    continue
                if (a["unidad"], a["dia"]) == (a["unidad_ref"], a["dia_preferido"]):
                    continue
                if dia not in a["dias_admisibles"]:
                    continue
                if not _compatible_historico(a["grupo"], unidad, dia, asign, coocurrencia):
                    continue
                destino = sorted(_sids_de_ruta(asign, unidad, dia) + list(a["miembros"]))
                if _restriccion_violada(destino, unidad, pedidos, volumenes, coords,
                                        vehiculos_cap, vehiculos_vol, cfg, dia=dia,
                                        kg_mayoristas=kg_may) is not None:
                    continue
                candidatos.append(a)

            if not candidatos:
                break

            en_casa = [a for a in candidatos
                      if a["unidad_ref"] == unidad and a["dia_preferido"] == dia]
            pool = en_casa or candidatos

            ocupado_actual = _ocupado(unidad, dia)

            def _pct_resultante(a, _ocupado=ocupado_actual, _cap=cap):
                return ((_ocupado + _kg_candidato(a)) / _cap) if _cap else 0.0

            pool_ordenado = sorted(pool, key=lambda a: (-_pct_resultante(a), a["grupo"]))
            elegido = pool_ordenado[0]
            es_regreso = bool(en_casa)

            excepciones.append({
                "tipo": "RELLENO_CAPACIDAD_LIBRE", "grupo": elegido["grupo"],
                "rigidez": elegido["rigidez"], "restriccion": None,
                "desde_unidad": elegido["unidad"], "desde_dia": elegido["dia"],
                "a_unidad": unidad, "a_dia": dia,
                "motivo_regreso_hogar": es_regreso,
                "motivo": f"{unidad}/{dia} con capacidad libre; se acomodó el "
                          f"grupo {elegido['grupo']} desde "
                          f"{elegido['unidad']}/{elegido['dia']}"
                          + (" (regresa a su unidad/día preferido)" if es_regreso else ""),
            })
            elegido["unidad"] = unidad
            elegido["dia"] = dia
            movidos.add(elegido["grupo"])

    return excepciones
```

Nota: esto reemplaza el archivo completo de la función (deja el resto del módulo intacto) — es más simple sustituir toda la función que aplicar el diff línea por línea.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_convrp_logic.py -k relleno_capacidad -v`
Expected: 8 PASSED

- [ ] **Step 5: Commit**

```bash
git add logic/convrp_logic.py tests/test_convrp_logic.py
git commit -m "feat: la Palanca 5 puede rellenar una ruta con varios grupos en una pasada"
```

---

### Task 5: Cada grupo se mueve como máximo una vez

**Files:**
- Test: `tests/test_convrp_logic.py`
- (sin cambios de producción — ya lo garantiza el set `movidos` del Task 2; este task sólo agrega la prueba que lo confirma explícitamente con dos rutas destino compitiendo por el mismo candidato)

- [ ] **Step 1: Write the test**

```python
def test_relleno_capacidad_cada_grupo_se_mueve_como_maximo_una_vez():
    from logic.convrp_logic import _rellenar_capacidad_libre
    asign = {
        1: {"grupo": 1, "unidad": "V1", "dia": "LUNES", "miembros": [1],
            "unidad_ref": "V1", "dia_preferido": "LUNES", "rigidez": "RIGIDO",
            "dias_admisibles": ["LUNES"]},
        2: {"grupo": 2, "unidad": "V2", "dia": "LUNES", "miembros": [2],
            "unidad_ref": "V2", "dia_preferido": "LUNES", "rigidez": "RIGIDO",
            "dias_admisibles": ["LUNES"]},
        3: {"grupo": 3, "unidad": "V3", "dia": "MARTES", "miembros": [3],
            "unidad_ref": "V4", "dia_preferido": "JUEVES", "rigidez": "FLEXIBLE",
            "dias_admisibles": ["LUNES", "MARTES"]},  # el único candidato posible
    }
    pedidos = {1: 100, 2: 100, 3: 300}
    caps = {"V1": 1000, "V2": 1000, "V3": 1000, "V4": 1000}
    coocurrencia = {frozenset((1, 3)): 1, frozenset((2, 3)): 1}
    exc = _rellenar_capacidad_libre(
        asign, pedidos, {}, {}, caps, {}, _sin_tiempo(coocurrencia_grupos=coocurrencia), {})
    relleno = [e for e in exc if e["tipo"] == "RELLENO_CAPACIDAD_LIBRE"]
    assert len(relleno) == 1                          # grupo 3 sólo se mueve una vez
    assert asign[3]["unidad"] == "V1" and asign[3]["dia"] == "LUNES"
```

- [ ] **Step 2: Run test to verify it passes (ya sin cambios de código)**

Run: `pytest tests/test_convrp_logic.py::test_relleno_capacidad_cada_grupo_se_mueve_como_maximo_una_vez -v`
Expected: PASS (V1/LUNES y V2/LUNES empatan en 100/1000 = 10 %; el desempate alfabético de `orden_rutas` procesa V1 primero, así que grupo 3 termina ahí y `movidos` evita que V2/LUNES lo vuelva a tomar)

- [ ] **Step 3: Commit**

```bash
git add tests/test_convrp_logic.py
git commit -m "test: confirma que la Palanca 5 mueve cada grupo como maximo una vez"
```

---

### Task 6: Conectar la Palanca 5 en `construir_groups_desde_plantilla`

**Files:**
- Modify: `logic/convrp_logic.py:689-693` (después de la llamada a `_consolidar_solitarios`)
- Test: `tests/test_convrp_logic.py`

- [ ] **Step 1: Write the failing tests**

```python
# ═══════════════════════════════════════════════════════════════════════════
# Integración: la Palanca 5 corre dentro de construir_groups_desde_plantilla.
# Escenario: grupo 2 (unidad_ref="V1", inválida en esta flota -- no existe)
# cae por defecto a V2; V3 tiene sitio de sobra. La Palanca 5 debe reubicarlo
# y V2/LUNES debe desaparecer por completo al quedar vacía.
# ═══════════════════════════════════════════════════════════════════════════
def test_relleno_capacidad_integrado_rellena_y_vacia_la_ruta_origen():
    plantilla = [
        {"grupo": 1, "rigidez": "RIGIDO", "dia": "LUNES", "unidad_ref": "V3",
         "sucursales": [1, 4], "dias_admisibles": ["LUNES"]},
        {"grupo": 2, "rigidez": "FLEXIBLE", "dia": "LUNES", "unidad_ref": "V1",
         "sucursales": [2, 3], "dias_admisibles": ["LUNES"]},
    ]
    # "V1" no existe en la flota: grupo 2 cae por defecto a otra unidad y
    # queda "desviado" desde el arranque, sin necesidad de simular sobrecupo.
    pedidos = {1: 50, 4: 50, 2: 150, 3: 150}
    caps = {"V2": 1000, "V3": 1000}
    vols = {"V2": 99, "V3": 99}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, caps, vols, _sin_tiempo())
    assert ("V2", "LUNES") not in groups            # la ruta origen se vació
    assert sorted(m["sid"] for m in groups[("V3", "LUNES")]) == [1, 2, 3, 4]
    assert any(e["tipo"] == "RELLENO_CAPACIDAD_LIBRE" for e in exc)


def test_relleno_capacidad_desactivado_no_cambia_nada():
    plantilla = [
        {"grupo": 1, "rigidez": "RIGIDO", "dia": "LUNES", "unidad_ref": "V3",
         "sucursales": [1, 4], "dias_admisibles": ["LUNES"]},
        {"grupo": 2, "rigidez": "FLEXIBLE", "dia": "LUNES", "unidad_ref": "V1",
         "sucursales": [2, 3], "dias_admisibles": ["LUNES"]},
    ]
    pedidos = {1: 50, 4: 50, 2: 150, 3: 150}
    caps = {"V2": 1000, "V3": 1000}
    vols = {"V2": 99, "V3": 99}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, caps, vols,
        _sin_tiempo(relleno_capacidad=False))
    assert ("V2", "LUNES") in groups                 # ya NO se rellena
    assert not any(e["tipo"] == "RELLENO_CAPACIDAD_LIBRE" for e in exc)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_convrp_logic.py -k relleno_capacidad_integrado -v`
Expected: FAIL — `test_relleno_capacidad_integrado_...` falla porque `("V2","LUNES")` sigue en `groups` (la palanca aún no está conectada); `test_relleno_capacidad_desactivado_...` PASA por accidente (nada la desconecta porque nada la conecta todavía) — no importa, se re-confirma en el Step 4.

- [ ] **Step 3: Write minimal implementation**

En `logic/convrp_logic.py`, dentro de `construir_groups_desde_plantilla`, entre la llamada a `_consolidar_solitarios` y el comentario `# ── 4. Salida en el formato...`:

```python
    # ── 3b. Palanca 4: ninguna ruta se queda con una sola sucursal, salvo
    #      que ya esté al límite de su capacidad (peso Lores + mayoristas). ──
    excepciones += _consolidar_solitarios(asign, pedidos, volumenes, coords,
                                          vehiculos_cap, vehiculos_vol, cfg, kg_may)

    # ── 3c. Palanca 5: rellenar capacidad libre con grupos ya desviados de
    #      su unidad/dia preferido, priorizando devolverlos a casa. ──
    if cfg.get("relleno_capacidad", True):
        excepciones += _rellenar_capacidad_libre(asign, pedidos, volumenes,
                                                  coords, vehiculos_cap,
                                                  vehiculos_vol, cfg, kg_may)

    # ── 4. Salida en el formato que consume el resto del motor ──
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_convrp_logic.py -k relleno_capacidad -v`
Expected: 11 PASSED

- [ ] **Step 5: Run the FULL test file to check for regressions**

Run: `pytest tests/test_convrp_logic.py -v`
Expected: todos los tests existentes (los que estaban antes de este plan) siguen PASSED — la Palanca 5 es aditiva y sólo actúa cuando hay grupos ya desviados con capacidad libre real, algo que ningún test previo del archivo deja pendiente.

- [ ] **Step 6: Commit**

```bash
git add logic/convrp_logic.py tests/test_convrp_logic.py
git commit -m "feat: conecta la Palanca 5 en construir_groups_desde_plantilla"
```

---

### Task 7: Test de regresión con datos estilo grupo 19

**Files:**
- Test: `tests/test_convrp_logic.py`

- [ ] **Step 1: Write the test**

```python
# ═══════════════════════════════════════════════════════════════════════════
# Regresión — inspirada en el caso real que motivó esta palanca: la logística
# del 24-28 de agosto 2026 mostraba F 350_1/MARTES con sólo 2,318 de 3,900 kg
# (59 %) mientras el grupo 19 (Amatitlán + Carlos A. Carrillo 2, FLEXIBLE,
# hogar histórico F 350_1) no aparecía ahí. Este fixture usa pesos pequeños
# (no los kg reales del reporte) elegidos para que grupo19 -- con
# `unidad_ref` inválida en esta flota -- se procese ANTES que el grupo ancla
# en la pasada de reparto (más pesado primero) y caiga en la unidad de
# respaldo por desempate alfabético; el ancla reclama F 350_1 normalmente.
# Así queda "desviado" desde el arranque sin necesitar simular sobrecupo, y
# la Palanca 5 debe traerlo de vuelta a F 350_1/MARTES, vaciando el respaldo.
# ═══════════════════════════════════════════════════════════════════════════
def test_relleno_capacidad_regresion_grupo_19_amatitlan_carrillo():
    plantilla = [
        {"grupo": 30, "rigidez": "RIGIDO", "dia": "MARTES", "unidad_ref": "F350_1",
         "sucursales": [101, 102], "dias_admisibles": ["MARTES"]},   # ancla
        {"grupo": 19, "rigidez": "FLEXIBLE", "dia": "MARTES", "unidad_ref": "NO_ASIGNADA",
         "sucursales": [86, 100], "dias_admisibles": ["MARTES"]},    # Amatitlán + Carrillo 2
    ]
    pedidos = {101: 150, 102: 150,      # grupo 30 (ancla) = 300
               86: 250, 100: 250}       # grupo 19 = 500, procesa primero (más pesado)
    caps = {"F350_1": 3900, "AUX20": 3900}   # "AUX20" < "F350_1" alfabéticamente
    vols = {"F350_1": 99, "AUX20": 99}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, caps, vols, _sin_tiempo())
    assert sorted(m["sid"] for m in groups[("F350_1", "MARTES")]) == [86, 100, 101, 102]
    assert ("AUX20", "MARTES") not in groups
    relleno = [e for e in exc if e["tipo"] == "RELLENO_CAPACIDAD_LIBRE"]
    assert relleno and relleno[0]["grupo"] == 19
```

- [ ] **Step 2: Run test to verify it passes**

Run: `pytest tests/test_convrp_logic.py::test_relleno_capacidad_regresion_grupo_19_amatitlan_carrillo -v`
Expected: PASS

Si falla, imprime `groups` y `exc` (`pytest ... -v -s` con un `print` temporal) para ver dónde terminó cada grupo. La causa más probable es que el orden de "otras" en `_asignar_unidades` (consolidar en la unidad ya cargada primero) haya cambiado con estos pesos; ajusta los números manteniendo `peso(grupo19) > peso(grupo30)` (para que grupo19 se procese primero, con ambas unidades todavía en 0) y `"AUX20" < "F350_1"` alfabéticamente, sin cambiar la lógica de producción.

- [ ] **Step 3: Commit**

```bash
git add tests/test_convrp_logic.py
git commit -m "test: agrega regresion de la Palanca 5 con el caso real grupo 19"
```

---

### Task 8: Actualizar README.md

**Files:**
- Modify: `README.md:212-215`

- [ ] **Step 1: Editar el párrafo de "Mayoristas por zona"**

En `README.md`, el párrafo actual dice:

```
`convrp_integracion.construir_rutas_con_mayoristas()` cierra el circuito: la
carga de mayoristas se ancla a una sucursal del grupo destino y **entra a las
restricciones del motor**, así que el sobrecupo que provoca dispara las palancas
(unidad → día → partir) en vez de aparecer al pintar el PDF.
```

Cámbialo a:

```
`convrp_integracion.construir_rutas_con_mayoristas()` cierra el circuito: la
carga de mayoristas se ancla a una sucursal del grupo destino y **entra a las
restricciones del motor**, así que el sobrecupo que provoca dispara las palancas
(unidad → día → partir → consolidar solitarias → rellenar capacidad libre) en
vez de aparecer al pintar el PDF.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: menciona la Palanca 5 (relleno de capacidad libre) en README"
```

---

### Task 9: Verificación final

**Files:** ninguno (solo comandos)

- [ ] **Step 1: Correr toda la suite de pruebas**

Run: `pytest tests/ -v`
Expected: todos PASSED, sin regresiones en otros módulos (`test_convrp_validacion.py`, `test_convrp_integracion.py`, etc.)

- [ ] **Step 2: Gate obligatorio — smoke test contra las 9 semanas canónicas**

Run: `python scripts/smoke_convrp.py`
Expected: corre sin error; en particular, el conteo de `PARTIDO_CAPACIDAD` (rígidos partidos) **no debe aumentar** respecto al último valor conocido (0, según `MIGRACION_STATUS.md`) — la Palanca 5 corre después de partir y nunca parte grupos, así que no debería afectar ese conteo. Si aumenta, hay un bug: detente y revisa antes de continuar.

- [ ] **Step 3: Si todo pasa, informar al usuario**

Resume en el chat: qué palanca se agregó, qué corrobora `smoke_convrp.py` (conteo de `PARTIDO_CAPACIDAD` antes/después, cuántas rutas recibieron `RELLENO_CAPACIDAD_LIBRE` en las 9 semanas canónicas), y que el interruptor `CONVRP_RELLENO_CAPACIDAD` permite apagarla sin tocar `CONVRP_ACTIVO` si algo se ve raro en producción.

No se requiere commit en este paso (es sólo verificación).
