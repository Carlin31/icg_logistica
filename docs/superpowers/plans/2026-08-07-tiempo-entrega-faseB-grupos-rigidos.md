# Fase B v2 — reubicación guiada por grupos rígidos (ConVRP) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reemplazar la fuente de datos y el algoritmo de selección de destino de Fase B (`resolver_fuera_de_horario`) para que se guíe por los grupos de co-viaje ya validados del trabajo de ConVRP (`plantilla_grupo`), en vez de pares sueltos `(vehículo, día)` sin peso histórico — corrigiendo el bug real de producción donde una coincidencia de una sola semana bastaba para mover una parada a una ruta geográficamente sin relación.

**Architecture:** `logic/tiempo_reubicacion.py` cambia su fuente de afinidad de `historico_logic.afinidad_historica_por_sucursal()` a `logic.plantilla_canonica.obtener_grupos()` (ya existente, ya validado). Se sustituyen las funciones internas de selección de destino (`_clave_afinidad_para`, `_candidatas_con_afinidad`, `_mejor_candidata`, `_menos_mala`, `_simular_insercion`) por versiones basadas en grupo que además saben mover un grupo RÍGIDO completo junto. `resolver_fuera_de_horario()` cambia de firma (`afinidad: dict` → `grupos: list`) y gana la mecánica de "conjunto" (una o varias paradas movidas atómicamente). El resto del pipeline (inserción por posición geográfica, persistencia vía `guardar_modificacion()`, degradación segura) no cambia.

**Tech Stack:** Python 3.11, pytest. Sin cambios de esquema de BD — se lee `plantilla_grupo`/`plantilla_grupo_sucursal`/`plantilla_grupo_dia` vía la función ya existente `plantilla_canonica.obtener_grupos()`.

**Spec:** [docs/superpowers/specs/2026-08-07-tiempo-entrega-faseB-grupos-rigidos-design.md](../specs/2026-08-07-tiempo-entrega-faseB-grupos-rigidos-design.md)

**Importante:** `TIEMPO_REUBICACION_ACTIVA` (en `logic/tiempo_reubicacion.py`) queda en `False` durante todo este plan — es el interruptor dedicado de Fase B, ya apagado tras el bug de producción. El último task de este plan reactiva el motor (con toda la suite en verde), pero requiere confirmación explícita del usuario antes de aplicarse — ver Task 10.

---

## Estructura de archivos

- **Modificar** `logic/tiempo_reubicacion.py` — reemplaza las funciones de selección de destino y `resolver_fuera_de_horario`.
- **Modificar** `tests/test_tiempo_reubicacion.py` — reemplaza los tests de las funciones eliminadas y de `resolver_fuera_de_horario`; los tests de mecánica pura (`evaluar_ruta_completa`, `_normalizar_veh`, `_pct_utilizacion`, `_cabe_por_peso`, `_paradas_ordenadas`, `_insertar_en_ruta`, `_quitar_de_ruta`, `_recalcular_peso_ruta`) **no cambian**.
- **Modificar** `logic/pdf_logic.py` — cambia el import y la llamada dentro de `generar_pdf()` (línea ~759) de `afinidad_historica_por_sucursal()` a `obtener_grupos()`.

Referencias del estado actual (línea aproximada — ubicar por contenido si el archivo cambió):

- `logic/tiempo_reubicacion.py`: `_clave_afinidad_para` línea 176, `_candidatas_con_afinidad` línea 207, `_simular_insercion` línea 231, `_mejor_candidata` línea 248, `_menos_mala` línea 261, `resolver_fuera_de_horario` línea 274.
- `logic/plantilla_canonica.py`: `obtener_grupos(version=None)` línea 495 — devuelve `[{grupo, rigidez, dia, tam, cohesion, unidad_ref, unidades_afines, que_hace_vrp, sucursales:[num_tienda], dias_admisibles:[dia] (preferido/canónico primero), dia_preferido}, ...]`.
- `logic/pdf_logic.py`: import línea 29, llamada dentro de `generar_pdf()` líneas 759-761.
- `tests/test_tiempo_reubicacion.py`: bloque de `_clave_afinidad_para`/`_candidatas_con_afinidad`/`_mejor_candidata`/`_menos_mala` líneas 142-234; bloque de `resolver_fuera_de_horario` líneas 236-436.

---

## Task 1: Helpers puros de grupo — parseo de `unidades_afines` e índice por sucursal

**Files:**
- Modify: `logic/tiempo_reubicacion.py` (agregar al final)
- Test: `tests/test_tiempo_reubicacion.py` (agregar al final)

- [ ] **Step 1: Escribir las pruebas**

Agregar al final de `tests/test_tiempo_reubicacion.py`:

```python
from logic.tiempo_reubicacion import _parsear_unidades_afines, _indice_num_tienda_a_grupo


def test_parsear_unidades_afines_ordena_por_conteo_descendente():
    resultado = _parsear_unidades_afines("T 23:3 | K 16:2 | T 20:2 | T 17_1:1 | T 25:1")
    assert resultado[0] == ("T23", 3)
    assert set(resultado[1:3]) == {("K16", 2), ("T20", 2)}
    assert set(resultado[3:]) == {("T17_1", 1), ("T25", 1)}


def test_parsear_unidades_afines_vacio_o_none():
    assert _parsear_unidades_afines(None) == []
    assert _parsear_unidades_afines("") == []
    assert _parsear_unidades_afines("   ") == []


def test_parsear_unidades_afines_ignora_trozos_mal_formados():
    assert _parsear_unidades_afines("T 23:3 | basura | K 16:dos") == [("T23", 3)]


def test_indice_num_tienda_a_grupo_mapea_cada_miembro():
    grupos = [
        {"grupo": 30, "sucursales": [76, 77]},
        {"grupo": 19, "sucursales": [86, 100]},
    ]
    indice = _indice_num_tienda_a_grupo(grupos)
    assert indice[76]["grupo"] == 30
    assert indice[77]["grupo"] == 30
    assert indice[86]["grupo"] == 19
    assert indice[100]["grupo"] == 19
    assert 999 not in indice
```

- [ ] **Step 2: Correr las pruebas y verificar que fallan**

Run: `python -m pytest tests/test_tiempo_reubicacion.py -k "parsear_unidades or indice_num_tienda" -v`
Expected: FAIL con `ImportError: cannot import name '_parsear_unidades_afines'`.

- [ ] **Step 3: Implementar**

Agregar al final de `logic/tiempo_reubicacion.py`:

```python
def _parsear_unidades_afines(s) -> list:
    """
    'T 23:3 | K 16:2 | T 20:2' -> [('T23', 3), ('K16', 2), ('T20', 2)],
    ordenado por conteo descendente (el vehículo dominante primero; sort
    estable, así que empates conservan el orden de aparición en el string).
    Vehículos normalizados (mayúsculas sin espacios, ver `_normalizar_veh`)
    para comparar contra `ruta.vehiculo_abrev` sin caer en el bug ya
    confirmado del proyecto ('F350_2' != 'F 350_2').
    """
    if not s or not str(s).strip():
        return []
    pares = []
    for trozo in str(s).split("|"):
        trozo = trozo.strip()
        if not trozo or ":" not in trozo:
            continue
        veh, _, cnt = trozo.rpartition(":")
        try:
            conteo = int(cnt.strip())
        except ValueError:
            continue
        pares.append((_normalizar_veh(veh), conteo))
    return sorted(pares, key=lambda x: -x[1])


def _indice_num_tienda_a_grupo(grupos: list) -> dict:
    """{num_tienda: grupo} a partir de la lista que devuelve
    `plantilla_canonica.obtener_grupos()` — un grupo por sucursal miembro."""
    indice = {}
    for g in grupos:
        for nt in g.get("sucursales", []):
            indice[int(nt)] = g
    return indice
```

- [ ] **Step 4: Correr las pruebas y verificar que pasan**

Run: `python -m pytest tests/test_tiempo_reubicacion.py -k "parsear_unidades or indice_num_tienda" -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add logic/tiempo_reubicacion.py tests/test_tiempo_reubicacion.py
git commit -m "feat(tiempo): helpers de grupo - parsear unidades_afines e indice por sucursal"
```

---

## Task 2: `_grupo_para` reemplaza `_clave_afinidad_para`

**Files:**
- Modify: `logic/tiempo_reubicacion.py:176-204` (reemplazar `_clave_afinidad_para`)
- Modify: `tests/test_tiempo_reubicacion.py:142-173` (reemplazar el bloque de import y los 3 tests de `_clave_afinidad_para`)

- [ ] **Step 1: Reemplazar las pruebas viejas por las nuevas**

En `tests/test_tiempo_reubicacion.py`, reemplazar desde la línea del import:

```python
from logic.tiempo_reubicacion import (
    _clave_afinidad_para, _candidatas_con_afinidad, _mejor_candidata, _menos_mala,
)

CFG_AMPLIO = {
    "activo": True, "depot": (18.87, -96.94), "velocidad": 35.0,
    "dias": {
        "martes": {"hora_salida": "07:00", "hora_limite": "20:00"},
        "jueves": {"hora_salida": "07:00", "hora_limite": "20:00"},
    },
}

AFINIDAD = {42: {("F 350_1", "MARTES"): 1, ("F 350_2", "JUEVES"): 2}}


def test_clave_afinidad_para_sucursal_es_su_num_tienda():
    parada = {"num_tienda": 42}
    assert _clave_afinidad_para(parada, "sucursal", {"sucursales": []}, AFINIDAD) == 42


def test_clave_afinidad_para_mayorista_ancla_a_sucursal_cercana_con_afinidad():
    ruta = {"sucursales": [
        {"num_tienda": 42, "latitud": 18.90, "longitud": -96.95},
        {"num_tienda": 99, "latitud": 0.0, "longitud": 0.0},  # sin afinidad, lejos
    ]}
    mayorista = {"id_cliente": 7, "latitud": 18.901, "longitud": -96.951}
    assert _clave_afinidad_para(mayorista, "mayorista", ruta, AFINIDAD) == 42


def test_clave_afinidad_para_mayorista_sin_coords_es_none():
    assert _clave_afinidad_para({"id_cliente": 7}, "mayorista", {"sucursales": []}, AFINIDAD) is None
```

por:

```python
from logic.tiempo_reubicacion import _grupo_para

CFG_AMPLIO = {
    "activo": True, "depot": (18.87, -96.94), "velocidad": 35.0,
    "dias": {
        "martes": {"hora_salida": "07:00", "hora_limite": "20:00"},
        "jueves": {"hora_salida": "07:00", "hora_limite": "20:00"},
    },
}

GRUPO_42 = {"grupo": 1, "rigidez": "FLEXIBLE", "sucursales": [42],
            "unidades_afines": "F 350_1:1 | F 350_2:2", "dias_admisibles": ["MARTES", "JUEVES"],
            "dia_preferido": "JUEVES"}
INDICE_GRUPOS = {42: GRUPO_42}


def test_grupo_para_sucursal_es_su_grupo_directo():
    parada = {"num_tienda": 42}
    assert _grupo_para(parada, "sucursal", {"sucursales": []}, INDICE_GRUPOS) is GRUPO_42


def test_grupo_para_sucursal_sin_grupo_es_none():
    parada = {"num_tienda": 999}
    assert _grupo_para(parada, "sucursal", {"sucursales": []}, INDICE_GRUPOS) is None


def test_grupo_para_mayorista_ancla_a_sucursal_cercana_con_grupo():
    ruta = {"sucursales": [
        {"num_tienda": 42, "latitud": 18.90, "longitud": -96.95},
        {"num_tienda": 99, "latitud": 0.0, "longitud": 0.0},  # sin grupo, lejos
    ]}
    mayorista = {"id_cliente": 7, "latitud": 18.901, "longitud": -96.951}
    assert _grupo_para(mayorista, "mayorista", ruta, INDICE_GRUPOS) is GRUPO_42


def test_grupo_para_mayorista_sin_coords_es_none():
    assert _grupo_para({"id_cliente": 7}, "mayorista", {"sucursales": []}, INDICE_GRUPOS) is None
```

- [ ] **Step 2: Correr las pruebas y verificar que fallan**

Run: `python -m pytest tests/test_tiempo_reubicacion.py -k grupo_para -v`
Expected: FAIL con `ImportError: cannot import name '_grupo_para'`.

- [ ] **Step 3: Reemplazar la implementación**

En `logic/tiempo_reubicacion.py`, reemplazar la función `_clave_afinidad_para` completa (línea 176-204) por:

```python
def _grupo_para(parada: dict, tipo: str, ruta: dict, indice_grupos: dict) -> "dict | None":
    """
    Grupo de co-viaje (de `plantilla_canonica.obtener_grupos()`, indexado
    por `_indice_num_tienda_a_grupo`) al que pertenece `parada`:
    - Sucursal: su propio grupo, por `num_tienda`.
    - Mayorista: el grupo de la sucursal de la MISMA ruta geográficamente
      más cercana que sí tenga grupo — mismo criterio de anclaje que usaba
      `_clave_afinidad_para` en v1, ahora resolviendo un grupo completo en
      vez de una llave suelta.
    None si no hay coordenadas, no hay ancla, o la sucursal no está en
    ningún grupo de la plantilla canónica.
    """
    if tipo == "sucursal":
        nt = parada.get("num_tienda")
        return indice_grupos.get(int(nt)) if nt is not None else None

    lat, lon = parada.get("latitud"), parada.get("longitud")
    if lat is None or lon is None:
        return None
    mejor_grupo, mejor_dist = None, float("inf")
    for s in ruta.get("sucursales", []):
        nt = s.get("num_tienda")
        if nt is None or int(nt) not in indice_grupos:
            continue
        la, lo = s.get("latitud"), s.get("longitud")
        if la is None or lo is None:
            continue
        d = _haversine_km(float(lat), float(lon), float(la), float(lo))
        if d < mejor_dist:
            mejor_dist, mejor_grupo = d, indice_grupos[int(nt)]
    return mejor_grupo
```

- [ ] **Step 4: Correr las pruebas y verificar que pasan**

Run: `python -m pytest tests/test_tiempo_reubicacion.py -k grupo_para -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add logic/tiempo_reubicacion.py tests/test_tiempo_reubicacion.py
git commit -m "feat(tiempo): _grupo_para reemplaza _clave_afinidad_para (grupos en vez de pares vehiculo/dia)"
```

---

## Task 3: `_conjunto_a_mover` — decide si se mueve solo la parada o el grupo rígido completo

**Files:**
- Modify: `logic/tiempo_reubicacion.py` (agregar después de `_grupo_para`)
- Test: `tests/test_tiempo_reubicacion.py` (agregar después de los tests de `_grupo_para`)

- [ ] **Step 1: Escribir las pruebas**

```python
from logic.tiempo_reubicacion import _conjunto_a_mover

GRUPO_RIGIDO_76_77 = {"grupo": 30, "rigidez": "RIGIDO", "sucursales": [76, 77]}
GRUPO_FLEXIBLE_86_100 = {"grupo": 19, "rigidez": "FLEXIBLE", "sucursales": [86, 100]}


def _ruta_con_76_y_77():
    return {
        "sucursales": [
            {"num_tienda": 76, "nombre": "Tierra Blanca 7", "orden": 1, "peso_kg": 100,
             "latitud": 18.5, "longitud": -96.5},
            {"num_tienda": 77, "nombre": "Tierra Blanca 8", "orden": 2, "peso_kg": 50,
             "latitud": 18.51, "longitud": -96.51},
        ],
        "mayoristas": [],
    }


def test_conjunto_a_mover_rigido_junta_miembros_presentes_en_la_ruta():
    ruta = _ruta_con_76_y_77()
    parada_76 = ruta["sucursales"][0]
    conjunto = _conjunto_a_mover(GRUPO_RIGIDO_76_77, ruta, parada_76, "sucursal")
    assert {p["num_tienda"] for p in conjunto} == {76, 77}


def test_conjunto_a_mover_rigido_de_un_solo_miembro_presente_es_solo_la_parada():
    ruta = {"sucursales": [
        {"num_tienda": 76, "nombre": "Tierra Blanca 7", "orden": 1, "peso_kg": 100,
         "latitud": 18.5, "longitud": -96.5},
    ], "mayoristas": []}
    parada_76 = ruta["sucursales"][0]
    # 77 no está en esta ruta (viajó aparte esta semana, caso borde real) —
    # no se inventa ni se va a buscar a otra ruta.
    conjunto = _conjunto_a_mover(GRUPO_RIGIDO_76_77, ruta, parada_76, "sucursal")
    assert [p["num_tienda"] for p in conjunto] == [76]


def test_conjunto_a_mover_flexible_solo_la_parada():
    ruta = {"sucursales": [
        {"num_tienda": 86, "nombre": "Carlos A. Carrillo 2", "orden": 1, "peso_kg": 500,
         "latitud": 18.37, "longitud": -95.75},
        {"num_tienda": 100, "nombre": "Amatitlan", "orden": 2, "peso_kg": 165,
         "latitud": 18.43, "longitud": -95.73},
    ], "mayoristas": []}
    parada_100 = ruta["sucursales"][1]
    conjunto = _conjunto_a_mover(GRUPO_FLEXIBLE_86_100, ruta, parada_100, "sucursal")
    assert [p["num_tienda"] for p in conjunto] == [100]


def test_conjunto_a_mover_mayorista_nunca_arrastra_grupo():
    mayorista = {"id_cliente": 7, "peso_kg": 20, "latitud": 18.5, "longitud": -96.5}
    conjunto = _conjunto_a_mover(GRUPO_RIGIDO_76_77, {"sucursales": []}, mayorista, "mayorista")
    assert conjunto == [mayorista]
```

- [ ] **Step 2: Correr las pruebas y verificar que fallan**

Run: `python -m pytest tests/test_tiempo_reubicacion.py -k conjunto_a_mover -v`
Expected: FAIL con `ImportError: cannot import name '_conjunto_a_mover'`.

- [ ] **Step 3: Implementar**

```python
def _conjunto_a_mover(grupo: dict, ruta: dict, parada: dict, tipo: str) -> list:
    """
    Paradas que se mueven juntas, atómicamente, al reubicar `parada`:
    - Mayorista: siempre solo ella misma — un mayorista nunca es miembro de
      un grupo (`plantilla_grupo_sucursal` es solo de sucursales), así que
      nunca arrastra al grupo de su sucursal ancla.
    - Sucursal en grupo FLEXIBLE (o grupo de un solo miembro): solo ella —
      la cohesión <1.0 de un grupo flexible ya dice que históricamente no
      siempre viajaron juntos, no se fuerza a un compañero que hoy sí llega
      a tiempo a moverse también.
    - Sucursal en grupo RÍGIDO con más miembros: todos los miembros del
      grupo que estén presentes en la ruta origen ahora mismo (nunca se
      separa una pareja/trío rígido). Si por algún motivo el resto del
      grupo no está en esta ruta esta semana, se mueve solo lo que sí está
      — no se va a buscar al resto a otras rutas.
    """
    if tipo != "sucursal":
        return [parada]
    if grupo.get("rigidez") != "RIGIDO" or len(grupo.get("sucursales", [])) <= 1:
        return [parada]
    miembros_nt = {int(nt) for nt in grupo.get("sucursales", [])}
    conjunto = [s for s in ruta.get("sucursales", [])
                if s.get("num_tienda") is not None and int(s["num_tienda"]) in miembros_nt]
    return conjunto if conjunto else [parada]
```

- [ ] **Step 4: Correr las pruebas y verificar que pasan**

Run: `python -m pytest tests/test_tiempo_reubicacion.py -k conjunto_a_mover -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add logic/tiempo_reubicacion.py tests/test_tiempo_reubicacion.py
git commit -m "feat(tiempo): _conjunto_a_mover - grupos rigidos se mueven completos, flexibles solo la parada"
```

---

## Task 4: `_rutas_candidatas_por_grupo` reemplaza `_candidatas_con_afinidad`

**Files:**
- Modify: `logic/tiempo_reubicacion.py:207-228` (reemplazar `_candidatas_con_afinidad`)
- Modify: `tests/test_tiempo_reubicacion.py:175-186` (reemplazar el test de `_candidatas_con_afinidad`)

- [ ] **Step 1: Reemplazar la prueba vieja por las nuevas**

En `tests/test_tiempo_reubicacion.py`, reemplazar:

```python
def test_candidatas_con_afinidad_filtra_por_dia_y_afinidad():
    rutas = [
        {"id": "R_ORIGEN", "dia": "martes",  "vehiculo_abrev": "F 350_1"},
        {"id": "R_MISMO_DIA_AFIN", "dia": "martes", "vehiculo_abrev": "F 350_1"},
        {"id": "R_OTRO_VEH", "dia": "martes", "vehiculo_abrev": "F 350_9"},
        {"id": "R_OTRO_DIA_AFIN", "dia": "jueves", "vehiculo_abrev": "F350_2"},
    ]
    mismo_dia = _candidatas_con_afinidad(42, rutas, AFINIDAD, "R_ORIGEN", True, "martes")
    assert [r["id"] for r in mismo_dia] == ["R_MISMO_DIA_AFIN"]

    otro_dia = _candidatas_con_afinidad(42, rutas, AFINIDAD, "R_ORIGEN", False, "martes")
    assert [r["id"] for r in otro_dia] == ["R_OTRO_DIA_AFIN"]  # 'F350_2' normaliza igual que 'F 350_2'
```

por:

```python
from logic.tiempo_reubicacion import _rutas_candidatas_por_grupo


def test_rutas_candidatas_por_grupo_ordena_por_frecuencia_descendente():
    grupo = {"unidades_afines": "T 23:3 | K 16:2 | T 25:1", "dias_admisibles": ["MARTES"]}
    rutas = [
        {"id": "R_ORIGEN", "dia": "martes", "vehiculo_abrev": "T 23"},
        {"id": "R_K16", "dia": "martes", "vehiculo_abrev": "K 16"},
        {"id": "R_T25", "dia": "martes", "vehiculo_abrev": "T 25"},
        {"id": "R_SIN_AFINIDAD", "dia": "martes", "vehiculo_abrev": "T 99"},
    ]
    candidatas = _rutas_candidatas_por_grupo(grupo, rutas, "R_ORIGEN")
    assert [r["id"] for r in candidatas] == ["R_K16", "R_T25"]


def test_rutas_candidatas_por_grupo_incluye_mismo_vehiculo_en_otro_dia_admisible():
    # Solo se excluye la ruta de origen EXACTA (mismo vehiculo Y mismo dia).
    # El mismo vehiculo en OTRO dia admisible (T 23 jueves) SI es candidata
    # valida -- un grupo flexible puede operar varios dias, y forzar un
    # vehiculo distinto solo por coincidir en vehiculo con el origen
    # tiraria al vehiculo dominante por una coincidencia de calendario.
    grupo = {"unidades_afines": "T 23:5", "dias_admisibles": ["MARTES", "JUEVES"]}
    rutas = [
        {"id": "R_ORIGEN", "dia": "martes", "vehiculo_abrev": "T 23"},
        {"id": "R_T23_JUEVES", "dia": "jueves", "vehiculo_abrev": "T 23"},
    ]
    candidatas = _rutas_candidatas_por_grupo(grupo, rutas, "R_ORIGEN")
    assert [r["id"] for r in candidatas] == ["R_T23_JUEVES"]


def test_rutas_candidatas_por_grupo_excluye_solo_la_ruta_de_origen_exacta():
    # Mismo vehiculo Y mismo dia que origen (o sea, la ruta origen misma,
    # aunque aparezca de nuevo en la lista por error) nunca se auto-elige.
    grupo = {"unidades_afines": "T 23:5", "dias_admisibles": ["MARTES"]}
    rutas = [{"id": "R_ORIGEN", "dia": "martes", "vehiculo_abrev": "T 23"}]
    assert _rutas_candidatas_por_grupo(grupo, rutas, "R_ORIGEN") == []


def test_rutas_candidatas_por_grupo_dia_admisible_en_orden_preferido_primero():
    grupo = {"unidades_afines": "F 350_1:7", "dias_admisibles": ["MARTES", "JUEVES"]}
    rutas = [
        {"id": "R_ORIGEN", "dia": "martes", "vehiculo_abrev": "OTRO"},
        {"id": "R_F350_1_JUEVES", "dia": "jueves", "vehiculo_abrev": "F 350_1"},
        {"id": "R_F350_1_MARTES", "dia": "martes", "vehiculo_abrev": "F 350_1"},
    ]
    candidatas = _rutas_candidatas_por_grupo(grupo, rutas, "R_ORIGEN")
    assert [r["id"] for r in candidatas] == ["R_F350_1_MARTES", "R_F350_1_JUEVES"]


def test_rutas_candidatas_por_grupo_sin_ruta_real_para_ese_vehiculo_dia():
    grupo = {"unidades_afines": "T 20:1", "dias_admisibles": ["VIERNES"]}
    rutas = [{"id": "R_ORIGEN", "dia": "lunes", "vehiculo_abrev": "T 23"}]
    assert _rutas_candidatas_por_grupo(grupo, rutas, "R_ORIGEN") == []
```

- [ ] **Step 2: Correr las pruebas y verificar que fallan**

Run: `python -m pytest tests/test_tiempo_reubicacion.py -k rutas_candidatas_por_grupo -v`
Expected: FAIL con `ImportError: cannot import name '_rutas_candidatas_por_grupo'`.

- [ ] **Step 3: Reemplazar la implementación**

En `logic/tiempo_reubicacion.py`, reemplazar la función `_candidatas_con_afinidad` completa (línea 207-228) por:

```python
def _rutas_candidatas_por_grupo(grupo: dict, rutas: list, ruta_origen_id) -> list:
    """
    Rutas reales de `rutas` (existentes esta semana) que son destino válido
    para `grupo`, en orden de preferencia: vehículo dominante primero
    (`unidades_afines`, conteo descendente), y dentro de cada vehículo, día
    admisible en su orden (preferido/canónico primero). Excluye la ruta de
    origen exacta (mismo vehículo Y mismo día) — el resto de los días
    admisibles de ese mismo vehículo SÍ son candidatos válidos (un grupo
    flexible puede operar en varios días; forzarlo a un vehículo distinto
    solo porque el origen coincide en vehículo tira al vehículo dominante
    por una coincidencia de calendario, no de afinidad real). Sin
    duplicados (un vehículo solo tiene una ruta por día esta semana).
    """
    pares_veh = _parsear_unidades_afines(grupo.get("unidades_afines"))
    dias = grupo.get("dias_admisibles") or (
        [grupo["dia_preferido"]] if grupo.get("dia_preferido") else [])

    rutas_por_clave = {}
    for r in rutas:
        if r.get("id") == ruta_origen_id:
            continue
        clave = (_normalizar_veh(r.get("vehiculo_abrev")), str(r.get("dia", "")).upper())
        rutas_por_clave.setdefault(clave, r)

    candidatas = []
    for veh, _conteo in pares_veh:
        for dia in dias:
            r = rutas_por_clave.get((veh, str(dia).upper()))
            if r is not None and r not in candidatas:
                candidatas.append(r)
    return candidatas
```

> **Nota post-implementación (2026-08-07):** las pruebas de regresión de
> Task 7, construidas con datos reales de producción (grupo 19,
> Amatitlán), encontraron que la exclusión de **todo** el vehículo de
> origen descrita arriba era un bug, no una decisión correcta: rompía
> exactamente el escenario que esta migración existe para arreglar (ver
> Task 7 más abajo y el bullet correspondiente en el spec §3). La versión
> corregida — la que quedó implementada — es la que se muestra arriba
> (excluye solo `ruta_origen_id`, ya no recibe `vehiculo_origen`); el test
> `test_rutas_candidatas_por_grupo_excluye_el_vehiculo_de_origen` fue
> reemplazado por
> `test_rutas_candidatas_por_grupo_incluye_mismo_vehiculo_en_otro_dia_admisible`
> y `test_rutas_candidatas_por_grupo_excluye_solo_la_ruta_de_origen_exacta`
> (ver arriba). El call site en `resolver_fuera_de_horario` (Task 6) se
> actualizó para llamar con 3 argumentos, sin `ruta.get("vehiculo_abrev")`.

- [ ] **Step 4: Correr las pruebas y verificar que pasan**

Run: `python -m pytest tests/test_tiempo_reubicacion.py -k rutas_candidatas_por_grupo -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add logic/tiempo_reubicacion.py tests/test_tiempo_reubicacion.py
git commit -m "feat(tiempo): _rutas_candidatas_por_grupo reemplaza _candidatas_con_afinidad"
```

---

## Task 5: `_mejor_candidata_grupo` y `_menos_mala_grupo` (operan sobre "conjunto", reemplazan las versiones de una sola parada)

**Files:**
- Modify: `logic/tiempo_reubicacion.py:231-271` (reemplazar `_simular_insercion`, `_mejor_candidata`, `_menos_mala`)
- Modify: `tests/test_tiempo_reubicacion.py:189-234` (reemplazar `_ruta_destino` y los 4 tests de `_mejor_candidata`/`_menos_mala`)

- [ ] **Step 1: Reemplazar las pruebas viejas por las nuevas**

En `tests/test_tiempo_reubicacion.py`, reemplazar desde el comentario de `_ruta_destino` hasta `test_menos_mala_none_si_no_hay_candidatas` (líneas 189-234):

```python
# El peso de "Vecina" debe IGUALAR peso_kg (no un valor fijo aparte):
# _menos_mala compara vía _simular_insercion -> _recalcular_peso_ruta,
# que sobreescribe peso_kg sumando las paradas reales. Si "Vecina" fuera
# un peso fijo distinto del peso_kg declarado, la simulacion perderia la
# diferencia entre rutas y el test de _menos_mala compararia valores
# identicos sin importar el peso_kg pedido.
def _ruta_destino(id_, dia="martes", peso_kg=0, capacidad_ton=3.5):
    return {
        "id": id_, "dia": dia, "vehiculo_abrev": "F 350_1",
        "capacidad_ton": capacidad_ton, "peso_kg": peso_kg,
        "pct_utilizacion": _pct_utilizacion(peso_kg, capacidad_ton),
        "sucursales": [
            {"num_tienda": 5, "nombre": "Vecina", "orden": 1, "peso_kg": peso_kg,
             "latitud": 18.90, "longitud": -96.95},
        ],
        "mayoristas": [],
    }


def test_mejor_candidata_respeta_umbral_y_tiempo():
    parada = {"num_tienda": 42, "nombre": "Nueva", "peso_kg": 100,
              "latitud": 18.901, "longitud": -96.951}
    llena  = _ruta_destino("LLENA", peso_kg=3300)   # 3400/3500=97% > 85%
    libre  = _ruta_destino("LIBRE", peso_kg=1000)   # 1100/3500=31% <= 85%
    elegida = _mejor_candidata([llena, libre], parada, "sucursal", 100.0,
                               CFG_AMPLIO, None, 85.0)
    assert elegida["id"] == "LIBRE"


def test_mejor_candidata_none_si_ninguna_cumple():
    parada = {"num_tienda": 42, "peso_kg": 100, "latitud": 18.901, "longitud": -96.951}
    llena = _ruta_destino("LLENA", peso_kg=3300)
    assert _mejor_candidata([llena], parada, "sucursal", 100.0, CFG_AMPLIO, None, 85.0) is None


def test_menos_mala_elige_menor_pct_resultante():
    parada = {"num_tienda": 42, "peso_kg": 100, "latitud": 18.901, "longitud": -96.951}
    mas_llena  = _ruta_destino("MAS_LLENA", peso_kg=3300)
    menos_llena = _ruta_destino("MENOS_LLENA", peso_kg=3000)
    elegida = _menos_mala([mas_llena, menos_llena], parada, "sucursal", CFG_AMPLIO, None)
    assert elegida["id"] == "MENOS_LLENA"


def test_menos_mala_none_si_no_hay_candidatas():
    assert _menos_mala([], {}, "sucursal", CFG_AMPLIO, None) is None
```

por:

```python
from logic.tiempo_reubicacion import _mejor_candidata_grupo, _menos_mala_grupo


# El peso de "Vecina" debe IGUALAR peso_kg (no un valor fijo aparte):
# _menos_mala_grupo compara vía _simular_insercion_conjunto -> _recalcular_peso_ruta,
# que sobreescribe peso_kg sumando las paradas reales.
def _ruta_destino(id_, dia="martes", peso_kg=0, capacidad_ton=3.5):
    return {
        "id": id_, "dia": dia, "vehiculo_abrev": "F 350_1",
        "capacidad_ton": capacidad_ton, "peso_kg": peso_kg,
        "pct_utilizacion": _pct_utilizacion(peso_kg, capacidad_ton),
        "sucursales": [
            {"num_tienda": 5, "nombre": "Vecina", "orden": 1, "peso_kg": peso_kg,
             "latitud": 18.90, "longitud": -96.95},
        ],
        "mayoristas": [],
    }


def test_mejor_candidata_grupo_respeta_umbral_y_tiempo():
    conjunto = [{"num_tienda": 42, "nombre": "Nueva", "peso_kg": 100,
                 "latitud": 18.901, "longitud": -96.951}]
    llena  = _ruta_destino("LLENA", peso_kg=3300)   # 3400/3500=97% > 85%
    libre  = _ruta_destino("LIBRE", peso_kg=1000)   # 1100/3500=31% <= 85%
    elegida = _mejor_candidata_grupo([llena, libre], conjunto, "sucursal", 100.0,
                                     CFG_AMPLIO, None, 85.0)
    assert elegida["id"] == "LIBRE"


def test_mejor_candidata_grupo_none_si_ninguna_cumple():
    conjunto = [{"num_tienda": 42, "peso_kg": 100, "latitud": 18.901, "longitud": -96.951}]
    llena = _ruta_destino("LLENA", peso_kg=3300)
    assert _mejor_candidata_grupo([llena], conjunto, "sucursal", 100.0, CFG_AMPLIO, None, 85.0) is None


def test_mejor_candidata_grupo_evalua_el_peso_total_del_conjunto():
    # Dos paradas de 500 kg cada una (grupo rigido): 1000 kg extra.
    conjunto = [
        {"num_tienda": 42, "peso_kg": 500, "latitud": 18.901, "longitud": -96.951},
        {"num_tienda": 43, "peso_kg": 500, "latitud": 18.902, "longitud": -96.952},
    ]
    # 1000 + 1000 = 2000/3500 = 57% <= 85% -> cabe.
    libre = _ruta_destino("LIBRE", peso_kg=1000)
    elegida = _mejor_candidata_grupo([libre], conjunto, "sucursal", 1000.0, CFG_AMPLIO, None, 85.0)
    assert elegida["id"] == "LIBRE"
    # 3000 + 1000 = 4000/3500 = 114% > 85% -> no cabe con las dos.
    llena = _ruta_destino("LLENA", peso_kg=3000)
    assert _mejor_candidata_grupo([llena], conjunto, "sucursal", 1000.0, CFG_AMPLIO, None, 85.0) is None


def test_menos_mala_grupo_elige_menor_pct_resultante():
    conjunto = [{"num_tienda": 42, "peso_kg": 100, "latitud": 18.901, "longitud": -96.951}]
    mas_llena  = _ruta_destino("MAS_LLENA", peso_kg=3300)
    menos_llena = _ruta_destino("MENOS_LLENA", peso_kg=3000)
    elegida = _menos_mala_grupo([mas_llena, menos_llena], conjunto, "sucursal", CFG_AMPLIO, None)
    assert elegida["id"] == "MENOS_LLENA"


def test_menos_mala_grupo_none_si_no_hay_candidatas():
    assert _menos_mala_grupo([], [{}], "sucursal", CFG_AMPLIO, None) is None
```

- [ ] **Step 2: Correr las pruebas y verificar que fallan**

Run: `python -m pytest tests/test_tiempo_reubicacion.py -k "mejor_candidata_grupo or menos_mala_grupo" -v`
Expected: FAIL con `ImportError: cannot import name '_mejor_candidata_grupo'`.

- [ ] **Step 3: Reemplazar la implementación**

En `logic/tiempo_reubicacion.py`, reemplazar `_simular_insercion`, `_mejor_candidata` y `_menos_mala` (línea 231-271) por:

```python
def _simular_insercion_conjunto(ruta: dict, conjunto: list, tipo: str) -> dict:
    """Copia profunda de `ruta` con TODAS las paradas de `conjunto` insertadas
    y el peso recalculado — para evaluar el efecto de mover un grupo
    completo (o una sola parada, si `conjunto` tiene un elemento) sin mutar
    la ruta real todavía."""
    ruta_sim = copy.deepcopy(ruta)
    for parada in conjunto:
        _insertar_en_ruta(ruta_sim, parada, tipo)
    _recalcular_peso_ruta(ruta_sim)
    return ruta_sim


def _sin_fuera_de_horario(ruta: dict, cfg_tiempo: dict, consultar_osrm_fn) -> bool:
    combinado = _paradas_ordenadas(ruta)
    if not combinado:
        return True
    evals = evaluar_ruta_completa(combinado, ruta.get("dia", ""), cfg_tiempo, consultar_osrm_fn)
    return all(e["entregable_por_tiempo"] for e in evals)


def _mejor_candidata_grupo(candidatas: list, conjunto: list, tipo: str, peso_extra: float,
                           cfg_tiempo: dict, consultar_osrm_fn, umbral_pct: float) -> "dict | None":
    """Primera candidata (ya ordenada por `_rutas_candidatas_por_grupo`) que,
    tras insertar TODO `conjunto`, queda ≤ umbral_pct de utilización Y no
    genera un nuevo FUERA DE HORARIO."""
    for ruta in candidatas:
        if not _cabe_por_peso(ruta, peso_extra, umbral_pct):
            continue
        ruta_sim = _simular_insercion_conjunto(ruta, conjunto, tipo)
        if _sin_fuera_de_horario(ruta_sim, cfg_tiempo, consultar_osrm_fn):
            return ruta
    return None


def _menos_mala_grupo(candidatas: list, conjunto: list, tipo: str,
                      cfg_tiempo: dict, consultar_osrm_fn) -> "dict | None":
    """Último recurso: entre las candidatas ya restringidas a
    `unidades_afines` del grupo (nunca una ruta sin relación histórica real
    con el grupo), la que quede con menor % de utilización tras insertar
    `conjunto` completo."""
    mejor, mejor_pct = None, float("inf")
    for ruta in candidatas:
        ruta_sim = _simular_insercion_conjunto(ruta, conjunto, tipo)
        if ruta_sim["pct_utilizacion"] < mejor_pct:
            mejor, mejor_pct = ruta, ruta_sim["pct_utilizacion"]
    return mejor
```

Nota: `_sin_fuera_de_horario` ya existía en el archivo (no cambia) — se incluye arriba solo como referencia de contexto; no la dupliques si ya está presente entre `_simular_insercion` y `_mejor_candidata`.

- [ ] **Step 4: Correr las pruebas y verificar que pasan**

Run: `python -m pytest tests/test_tiempo_reubicacion.py -k "mejor_candidata_grupo or menos_mala_grupo" -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add logic/tiempo_reubicacion.py tests/test_tiempo_reubicacion.py
git commit -m "feat(tiempo): _mejor_candidata_grupo y _menos_mala_grupo operan sobre conjuntos"
```

---

## Task 6: Reescribir `resolver_fuera_de_horario` (firma `grupos`, mecánica de conjunto)

**Files:**
- Modify: `logic/tiempo_reubicacion.py:274-339` (reemplazar `resolver_fuera_de_horario` completo)
- Modify: `tests/test_tiempo_reubicacion.py:236-436` (reemplazar todos los tests de `resolver_fuera_de_horario`)

- [ ] **Step 1: Reemplazar todos los tests de `resolver_fuera_de_horario`**

En `tests/test_tiempo_reubicacion.py`, reemplazar **desde** `from logic.tiempo_reubicacion import resolver_fuera_de_horario` (línea 236) **hasta el final del archivo** por:

```python
from logic.tiempo_reubicacion import resolver_fuera_de_horario


def _cfg_cierre_08_30():
    # Salida 07:00 (420 min), cierre 08:30 (510 min): 90 min de presupuesto.
    # Con estas coordenadas y 60 km/h, alcanza para llegar a (0.5,0.5)
    # DIRECTO desde el depot (~78.6 km, ~78.6 min => llega a 498.6, cabe),
    # pero NO alcanza si antes hay que pasar por (0.05,0.05) + su descarga
    # (42 min): la llegada a (0.5,0.5) cae en ~540.6 min, fuera de horario.
    return {
        "activo": True, "depot": (0.0, 0.0), "velocidad": 60.0,
        "dias": {
            "martes": {"hora_salida": "07:00", "hora_limite": "08:30"},
            "jueves": {"hora_salida": "07:00", "hora_limite": "08:30"},
        },
    }


def _ruta_vacia(id_, dia, vehiculo):
    return {
        "id": id_, "dia": dia, "vehiculo_abrev": vehiculo,
        "capacidad_ton": 3.5, "peso_kg": 0, "pct_utilizacion": 0.0,
        "sucursales": [], "mayoristas": [],
    }


def test_resolver_fuera_de_horario_mueve_a_ruta_con_grupo_y_cupo(monkeypatch):
    import logic.tiempo_reubicacion as tr
    monkeypatch.setattr(tr, "TIEMPO_REUBICACION_ACTIVA", True)
    # 42 está lejos del depot. La ruta ORIGEN visita antes una sucursal
    # cercana (con su descarga) y llega tarde a 42. La ruta DESTINO (vacía,
    # mismo día, con el vehiculo dominante del grupo de 42) llega a tiempo
    # yendo directo.
    grupos = [{"grupo": 1, "rigidez": "FLEXIBLE", "sucursales": [42],
               "unidades_afines": "DEST:5", "dias_admisibles": ["MARTES"],
               "dia_preferido": "MARTES"}]
    origen = {
        "id": "ORIGEN", "dia": "martes", "vehiculo_abrev": "ORIGEN",
        "capacidad_ton": 3.5, "peso_kg": 0, "pct_utilizacion": 0.0,
        "sucursales": [
            {"num_tienda": 1, "nombre": "Cercana", "orden": 1, "peso_kg": 100,
             "latitud": 0.05, "longitud": 0.05},
            {"num_tienda": 42, "nombre": "Lejana", "orden": 2, "peso_kg": 100,
             "latitud": 0.5, "longitud": 0.5},
        ],
        "mayoristas": [],
    }
    destino = _ruta_vacia("DEST", "martes", "DEST")
    rutas = [origen, destino]

    movio = resolver_fuera_de_horario(rutas, _cfg_cierre_08_30(), grupos, consultar_osrm_fn=None)

    assert movio is True
    assert [s["num_tienda"] for s in origen["sucursales"]] == [1]
    assert 42 in [s["num_tienda"] for s in destino["sucursales"]]
    assert destino["peso_kg"] == 100.0


def test_resolver_fuera_de_horario_sin_grupo_no_mueve_nada():
    ruta = {
        "id": "ORIGEN", "dia": "martes", "vehiculo_abrev": "ORIGEN",
        "capacidad_ton": 3.5, "peso_kg": 0, "pct_utilizacion": 0.0,
        "sucursales": [
            {"num_tienda": 1, "nombre": "Cercana", "orden": 1, "peso_kg": 100,
             "latitud": 0.05, "longitud": 0.05},
            {"num_tienda": 999, "nombre": "Sin grupo", "orden": 2, "peso_kg": 100,
             "latitud": 0.5, "longitud": 0.5},
        ],
        "mayoristas": [],
    }
    movio = resolver_fuera_de_horario([ruta], _cfg_cierre_08_30(), [], consultar_osrm_fn=None)
    assert movio is False
    assert [s["num_tienda"] for s in ruta["sucursales"]] == [1, 999]


def test_resolver_fuera_de_horario_interruptor_apagado_no_hace_nada():
    ruta = {"id": "R1", "dia": "martes", "sucursales": [], "mayoristas": []}
    assert resolver_fuera_de_horario([ruta], {"activo": False}, []) is False


def test_resolver_fuera_de_horario_dominante_se_prueba_antes_que_minoritario(monkeypatch):
    import logic.tiempo_reubicacion as tr
    monkeypatch.setattr(tr, "TIEMPO_REUBICACION_ACTIVA", True)
    # MINORITARIO (1 semana de historial) esta vacia y cabria perfecto.
    # DOMINANTE (7 semanas) tambien cabe -- debe elegirse DOMINANTE primero
    # aunque ambas cumplan cupo+tiempo, por ser la de mayor conteo real.
    grupos = [{"grupo": 1, "rigidez": "FLEXIBLE", "sucursales": [42],
               "unidades_afines": "DOMINANTE:7 | MINORITARIO:1",
               "dias_admisibles": ["MARTES"], "dia_preferido": "MARTES"}]
    origen = {
        "id": "ORIGEN", "dia": "martes", "vehiculo_abrev": "ORIGEN",
        "capacidad_ton": 3.5, "peso_kg": 0, "pct_utilizacion": 0.0,
        "sucursales": [
            {"num_tienda": 1, "nombre": "Cercana", "orden": 1, "peso_kg": 100,
             "latitud": 0.05, "longitud": 0.05},
            {"num_tienda": 42, "nombre": "Lejana", "orden": 2, "peso_kg": 100,
             "latitud": 0.5, "longitud": 0.5},
        ],
        "mayoristas": [],
    }
    dominante = _ruta_vacia("DOMINANTE", "martes", "DOMINANTE")
    minoritario = _ruta_vacia("MINORITARIO", "martes", "MINORITARIO")
    rutas = [origen, minoritario, dominante]  # orden de lista no debe importar

    resolver_fuera_de_horario(rutas, _cfg_cierre_08_30(), grupos, consultar_osrm_fn=None)

    assert 42 in [s["num_tienda"] for s in dominante["sucursales"]]
    assert [s["num_tienda"] for s in minoritario["sucursales"]] == []


def test_resolver_fuera_de_horario_dia_canonico_antes_que_otro_dia_admisible(monkeypatch):
    import logic.tiempo_reubicacion as tr
    monkeypatch.setattr(tr, "TIEMPO_REUBICACION_ACTIVA", True)
    # Mismo vehiculo DEST disponible martes y jueves; el grupo admite ambos
    # dias pero MARTES es el preferido/canonico (primero en dias_admisibles)
    # -> se prueba primero, y como cumple, se elige ahi.
    grupos = [{"grupo": 1, "rigidez": "FLEXIBLE", "sucursales": [42],
               "unidades_afines": "DEST:5", "dias_admisibles": ["MARTES", "JUEVES"],
               "dia_preferido": "MARTES"}]
    origen = {
        "id": "ORIGEN", "dia": "martes", "vehiculo_abrev": "ORIGEN",
        "capacidad_ton": 3.5, "peso_kg": 0, "pct_utilizacion": 0.0,
        "sucursales": [
            {"num_tienda": 1, "nombre": "Cercana", "orden": 1, "peso_kg": 100,
             "latitud": 0.05, "longitud": 0.05},
            {"num_tienda": 42, "nombre": "Lejana", "orden": 2, "peso_kg": 100,
             "latitud": 0.5, "longitud": 0.5},
        ],
        "mayoristas": [],
    }
    dest_martes = _ruta_vacia("DEST_MARTES", "martes", "DEST")
    dest_jueves = _ruta_vacia("DEST_JUEVES", "jueves", "DEST")
    rutas = [origen, dest_jueves, dest_martes]

    resolver_fuera_de_horario(rutas, _cfg_cierre_08_30(), grupos, consultar_osrm_fn=None)

    assert 42 in [s["num_tienda"] for s in dest_martes["sucursales"]]
    assert [s["num_tienda"] for s in dest_jueves["sucursales"]] == []


def test_resolver_fuera_de_horario_grupo_rigido_mueve_ambos_miembros_juntos(monkeypatch):
    import logic.tiempo_reubicacion as tr
    monkeypatch.setattr(tr, "TIEMPO_REUBICACION_ACTIVA", True)
    # 76 fuera de horario; 77 (su pareja rigida) SI llega a tiempo en ORIGEN
    # -- de todos modos se mueven juntas al reubicar 76.
    grupos = [{"grupo": 30, "rigidez": "RIGIDO", "sucursales": [76, 77],
               "unidades_afines": "DEST:9", "dias_admisibles": ["MARTES"],
               "dia_preferido": "MARTES"}]
    origen = {
        "id": "ORIGEN", "dia": "martes", "vehiculo_abrev": "ORIGEN",
        "capacidad_ton": 3.5, "peso_kg": 0, "pct_utilizacion": 0.0,
        "sucursales": [
            {"num_tienda": 1, "nombre": "Cercana", "orden": 1, "peso_kg": 100,
             "latitud": 0.05, "longitud": 0.05},
            {"num_tienda": 77, "nombre": "Pareja", "orden": 2, "peso_kg": 50,
             "latitud": 0.06, "longitud": 0.06},
            {"num_tienda": 76, "nombre": "Lejana", "orden": 3, "peso_kg": 100,
             "latitud": 0.5, "longitud": 0.5},
        ],
        "mayoristas": [],
    }
    destino = _ruta_vacia("DEST", "martes", "DEST")
    rutas = [origen, destino]

    movio = resolver_fuera_de_horario(rutas, _cfg_cierre_08_30(), grupos, consultar_osrm_fn=None)

    assert movio is True
    assert [s["num_tienda"] for s in origen["sucursales"]] == [1]
    assert {s["num_tienda"] for s in destino["sucursales"]} == {76, 77}


def test_resolver_fuera_de_horario_grupo_rigido_sin_cupo_perfecto_igual_mueve_el_par_junto(monkeypatch):
    import logic.tiempo_reubicacion as tr
    monkeypatch.setattr(tr, "TIEMPO_REUBICACION_ACTIVA", True)
    # DEST cabe para 76 sola (100kg) pero no para 76+77 juntas (100+3300kg =
    # 97.1% > 85%) -- _mejor_candidata_grupo la descarta por peso. Como DEST
    # es la UNICA candidata dentro de unidades_afines, "menos malo" la elige
    # de todos modos (no hay gate de cupo en el ultimo recurso, mismo
    # criterio que v1) -- pero SIEMPRE mueve 76 Y 77 JUNTAS, nunca una sola
    # separada de su pareja rigida.
    grupos = [{"grupo": 30, "rigidez": "RIGIDO", "sucursales": [76, 77],
               "unidades_afines": "DEST:9", "dias_admisibles": ["MARTES"],
               "dia_preferido": "MARTES"}]
    origen = {
        "id": "ORIGEN", "dia": "martes", "vehiculo_abrev": "ORIGEN",
        "capacidad_ton": 3.5, "peso_kg": 0, "pct_utilizacion": 0.0,
        "sucursales": [
            {"num_tienda": 1, "nombre": "Cercana", "orden": 1, "peso_kg": 100,
             "latitud": 0.05, "longitud": 0.05},
            {"num_tienda": 77, "nombre": "Pareja", "orden": 2, "peso_kg": 3300,
             "latitud": 0.06, "longitud": 0.06},
            {"num_tienda": 76, "nombre": "Lejana", "orden": 3, "peso_kg": 100,
             "latitud": 0.5, "longitud": 0.5},
        ],
        "mayoristas": [],
    }
    destino = {
        "id": "DEST", "dia": "martes", "vehiculo_abrev": "DEST",
        "capacidad_ton": 3.5, "peso_kg": 0, "pct_utilizacion": 0.0,
        "sucursales": [], "mayoristas": [],
    }
    rutas = [origen, destino]

    movio = resolver_fuera_de_horario(rutas, _cfg_cierre_08_30(), grupos, consultar_osrm_fn=None)

    # "menos malo" no tiene gate de cupo (mismo criterio que v1): con DEST
    # como unica candidata dentro de unidades_afines, se elige de todos
    # modos aunque quede sobrecargada -- pero 76 y 77 SIEMPRE juntas.
    assert movio is True
    assert {s["num_tienda"] for s in destino["sucursales"]} == {76, 77}
    assert [s["num_tienda"] for s in origen["sucursales"]] == [1]


def test_resolver_fuera_de_horario_grupo_flexible_no_arrastra_companero_a_tiempo(monkeypatch):
    import logic.tiempo_reubicacion as tr
    monkeypatch.setattr(tr, "TIEMPO_REUBICACION_ACTIVA", True)
    # 100 fuera de horario; 86 (su companero FLEXIBLE) llega a tiempo y NO
    # debe moverse tambien.
    grupos = [{"grupo": 19, "rigidez": "FLEXIBLE", "sucursales": [86, 100],
               "unidades_afines": "DEST:7", "dias_admisibles": ["MARTES"],
               "dia_preferido": "MARTES"}]
    origen = {
        "id": "ORIGEN", "dia": "martes", "vehiculo_abrev": "ORIGEN",
        "capacidad_ton": 3.5, "peso_kg": 0, "pct_utilizacion": 0.0,
        "sucursales": [
            {"num_tienda": 86, "nombre": "Companera", "orden": 1, "peso_kg": 100,
             "latitud": 0.05, "longitud": 0.05},
            {"num_tienda": 100, "nombre": "Lejana", "orden": 2, "peso_kg": 100,
             "latitud": 0.5, "longitud": 0.5},
        ],
        "mayoristas": [],
    }
    destino = _ruta_vacia("DEST", "martes", "DEST")
    rutas = [origen, destino]

    movio = resolver_fuera_de_horario(rutas, _cfg_cierre_08_30(), grupos, consultar_osrm_fn=None)

    assert movio is True
    assert [s["num_tienda"] for s in origen["sucursales"]] == [86]
    assert [s["num_tienda"] for s in destino["sucursales"]] == [100]


def test_resolver_fuera_de_horario_menos_malo_nunca_sale_del_grupo(monkeypatch):
    import logic.tiempo_reubicacion as tr
    monkeypatch.setattr(tr, "TIEMPO_REUBICACION_ACTIVA", True)
    # SIN_AFINIDAD esta vacia (cabria perfecto) pero su vehiculo NO aparece
    # en unidades_afines del grupo -- nunca debe elegirse, ni como "menos
    # malo". CON_AFINIDAD si aparece mas esta llena -- debe preferirse
    # sobre no moverse en absoluto.
    grupos = [{"grupo": 1, "rigidez": "FLEXIBLE", "sucursales": [42],
               "unidades_afines": "CON_AFINIDAD:2", "dias_admisibles": ["MARTES"],
               "dia_preferido": "MARTES"}]
    origen = {
        "id": "ORIGEN", "dia": "martes", "vehiculo_abrev": "ORIGEN",
        "capacidad_ton": 3.5, "peso_kg": 0, "pct_utilizacion": 0.0,
        "sucursales": [
            {"num_tienda": 1, "nombre": "Cercana", "orden": 1, "peso_kg": 100,
             "latitud": 0.05, "longitud": 0.05},
            {"num_tienda": 42, "nombre": "Lejana", "orden": 2, "peso_kg": 100,
             "latitud": 0.5, "longitud": 0.5},
        ],
        "mayoristas": [],
    }
    sin_afinidad = _ruta_vacia("SIN_AFINIDAD", "martes", "SIN_AFINIDAD")
    con_afinidad = {
        "id": "CON_AFINIDAD", "dia": "martes", "vehiculo_abrev": "CON_AFINIDAD",
        "capacidad_ton": 3.5, "peso_kg": 3000, "pct_utilizacion": 85.7,
        "sucursales": [{"num_tienda": 5, "nombre": "Llena", "orden": 1, "peso_kg": 3000,
                        "latitud": 0.05, "longitud": 0.05}],
        "mayoristas": [],
    }
    rutas = [origen, sin_afinidad, con_afinidad]

    resolver_fuera_de_horario(rutas, _cfg_cierre_08_30(), grupos, consultar_osrm_fn=None)

    assert [s["num_tienda"] for s in sin_afinidad["sucursales"]] == []
    assert 42 in [s["num_tienda"] for s in con_afinidad["sucursales"]]


def test_resolver_fuera_de_horario_mayorista_se_mueve_con_grupo_de_su_ancla(monkeypatch):
    import logic.tiempo_reubicacion as tr
    monkeypatch.setattr(tr, "TIEMPO_REUBICACION_ACTIVA", True)
    grupos = [{"grupo": 1, "rigidez": "FLEXIBLE", "sucursales": [1],
               "unidades_afines": "DEST:3", "dias_admisibles": ["MARTES"],
               "dia_preferido": "MARTES"}]
    origen = {
        "id": "ORIGEN", "dia": "martes", "vehiculo_abrev": "ORIGEN",
        "capacidad_ton": 3.5, "peso_kg": 0, "pct_utilizacion": 0.0,
        "sucursales": [
            {"num_tienda": 1, "nombre": "Ancla", "orden": 1, "peso_kg": 50,
             "latitud": 0.49, "longitud": 0.49},
        ],
        "mayoristas": [
            {"id_cliente": 7, "documento": "BB1", "nombre": "Mayorista lejano",
             "orden": 2, "peso_kg": 30, "latitud": 0.5, "longitud": 0.5},
        ],
    }
    destino = _ruta_vacia("DEST", "martes", "DEST")
    rutas = [origen, destino]

    movio = resolver_fuera_de_horario(rutas, _cfg_cierre_08_30(), grupos, consultar_osrm_fn=None)

    assert movio is True
    assert origen["mayoristas"] == []
    assert [m["id_cliente"] for m in destino["mayoristas"]] == [7]
    # El ancla (sucursal 1) NO se mueve -- solo el mayorista era el que
    # estaba fuera de horario.
    assert [s["num_tienda"] for s in origen["sucursales"]] == [1]


def test_resolver_fuera_de_horario_procesa_varias_paradas_en_la_misma_ruta(monkeypatch):
    import logic.tiempo_reubicacion as tr
    monkeypatch.setattr(tr, "TIEMPO_REUBICACION_ACTIVA", True)
    # ORIGEN tiene DOS paradas fuera de horario (42 y 43). Cada una tiene
    # grupo propio con destino distinto, vacio, del mismo dia. Deben
    # resolverse una por una (re-evaluando ORIGEN tras cada movimiento).
    grupos = [
        {"grupo": 1, "rigidez": "FLEXIBLE", "sucursales": [42],
         "unidades_afines": "DEST1:2", "dias_admisibles": ["MARTES"], "dia_preferido": "MARTES"},
        {"grupo": 2, "rigidez": "FLEXIBLE", "sucursales": [43],
         "unidades_afines": "DEST2:2", "dias_admisibles": ["MARTES"], "dia_preferido": "MARTES"},
    ]
    origen = {
        "id": "ORIGEN", "dia": "martes", "vehiculo_abrev": "ORIGEN",
        "capacidad_ton": 3.5, "peso_kg": 0, "pct_utilizacion": 0.0,
        "sucursales": [
            {"num_tienda": 1, "nombre": "Cercana", "orden": 1, "peso_kg": 100,
             "latitud": 0.05, "longitud": 0.05},
            {"num_tienda": 42, "nombre": "Lejana1", "orden": 2, "peso_kg": 100,
             "latitud": 0.5, "longitud": 0.5},
            {"num_tienda": 43, "nombre": "Lejana2", "orden": 3, "peso_kg": 100,
             "latitud": 0.45, "longitud": 0.45},
        ],
        "mayoristas": [],
    }
    dest1 = _ruta_vacia("DEST1", "martes", "DEST1")
    dest2 = _ruta_vacia("DEST2", "martes", "DEST2")
    rutas = [origen, dest1, dest2]

    movio = resolver_fuera_de_horario(rutas, _cfg_cierre_08_30(), grupos, consultar_osrm_fn=None)

    assert movio is True
    assert [s["num_tienda"] for s in origen["sucursales"]] == [1]
    assert [s["num_tienda"] for s in dest1["sucursales"]] == [42]
    assert [s["num_tienda"] for s in dest2["sucursales"]] == [43]


def test_resolver_fuera_de_horario_es_idempotente(monkeypatch):
    import logic.tiempo_reubicacion as tr
    monkeypatch.setattr(tr, "TIEMPO_REUBICACION_ACTIVA", True)
    grupos = [{"grupo": 1, "rigidez": "FLEXIBLE", "sucursales": [42],
               "unidades_afines": "DEST:5", "dias_admisibles": ["MARTES"],
               "dia_preferido": "MARTES"}]
    origen = {
        "id": "ORIGEN", "dia": "martes", "vehiculo_abrev": "ORIGEN",
        "capacidad_ton": 3.5, "peso_kg": 0, "pct_utilizacion": 0.0,
        "sucursales": [
            {"num_tienda": 1, "nombre": "Cercana", "orden": 1, "peso_kg": 100,
             "latitud": 0.05, "longitud": 0.05},
            {"num_tienda": 42, "nombre": "Lejana", "orden": 2, "peso_kg": 100,
             "latitud": 0.5, "longitud": 0.5},
        ],
        "mayoristas": [],
    }
    destino = _ruta_vacia("DEST", "martes", "DEST")
    rutas = [origen, destino]

    primera = resolver_fuera_de_horario(rutas, _cfg_cierre_08_30(), grupos, consultar_osrm_fn=None)
    segunda = resolver_fuera_de_horario(rutas, _cfg_cierre_08_30(), grupos, consultar_osrm_fn=None)

    assert primera is True
    assert segunda is False


def test_resolver_fuera_de_horario_flag_dedicado_apagado_no_hace_nada(monkeypatch):
    import logic.tiempo_reubicacion as tr
    monkeypatch.setattr(tr, "TIEMPO_REUBICACION_ACTIVA", False)
    ruta = {"id": "R1", "dia": "martes", "sucursales": [], "mayoristas": []}
    assert resolver_fuera_de_horario([ruta], _cfg_cierre_08_30(), []) is False
```

- [ ] **Step 2: Correr las pruebas y verificar que fallan**

Run: `python -m pytest tests/test_tiempo_reubicacion.py -k resolver_fuera_de_horario -v`
Expected: FAIL — `resolver_fuera_de_horario` todavía espera `afinidad: dict`, no `grupos: list` (los tests pasan una lista y fallan al llamar `_clave_afinidad_para`/`_candidatas_con_afinidad`, que ya no existen desde tasks anteriores — `NameError` o `AttributeError` según cuál falle primero).

- [ ] **Step 3: Reescribir la implementación**

En `logic/tiempo_reubicacion.py`, reemplazar la función `resolver_fuera_de_horario` completa (línea 274-339) por:

```python
def resolver_fuera_de_horario(rutas: list, cfg_tiempo: dict, grupos: list,
                              umbral_pct: float = UMBRAL_PCT_DESTINO,
                              consultar_osrm_fn=None) -> bool:
    """
    Reubica, mutando `rutas` in-place, toda parada FUERA DE HORARIO hacia
    otra ruta real de esta semana con respaldo histórico sólido (grupo de
    co-viaje de `plantilla_canonica.obtener_grupos()`, nunca un vehículo sin
    presencia real en `unidades_afines` del grupo), cupo (<=umbral_pct) y
    tiempo. Procesa las paradas de cada ruta en orden de secuencia,
    re-evaluando la ruta origen tras cada movimiento (quitar una parada
    solo puede adelantar la llegada de las que quedan). Sin grupo o sin
    destino con respaldo -> se queda marcada, igual que en Fase A. Grupos
    RÍGIDOS con más de un miembro se mueven completos (nunca se separa una
    pareja/trío rígido); grupos FLEXIBLES mueven solo la parada marcada.
    Devuelve True si movió algo.

    rutas: [{id, dia, vehiculo_abrev, capacidad_ton, peso_kg,
             pct_utilizacion, sucursales:[...], mayoristas:[...]}, ...] —
           misma forma que arma pdf_logic.generar_pdf().
    grupos: plantilla_canonica.obtener_grupos().
    """
    if not (TIEMPO_REUBICACION_ACTIVA and cfg_tiempo and cfg_tiempo.get("activo")):
        return False

    indice_grupos = _indice_num_tienda_a_grupo(grupos)
    cambio = False
    for ruta in rutas:
        for _ in range(MAX_MOVIMIENTOS_POR_RUTA):
            combinado = _paradas_ordenadas(ruta)
            if not combinado:
                break
            evals = evaluar_ruta_completa(combinado, ruta.get("dia", ""), cfg_tiempo, consultar_osrm_fn)
            idx_malo = next((i for i, e in enumerate(evals) if not e["entregable_por_tiempo"]), None)
            if idx_malo is None:
                break

            parada = combinado[idx_malo]
            tipo = parada["_tipo"]
            grupo = _grupo_para(parada, tipo, ruta, indice_grupos)
            if grupo is None:
                # Sin grupo en la plantilla canónica (sucursal nueva, o
                # mayorista sin ancla): se queda FUERA DE HORARIO.
                break

            conjunto = _conjunto_a_mover(grupo, ruta, parada, tipo)
            candidatas = _rutas_candidatas_por_grupo(
                grupo, rutas, ruta.get("id"))
            peso_extra = sum(float(p.get("peso_kg") or 0) for p in conjunto)

            destino = _mejor_candidata_grupo(candidatas, conjunto, tipo, peso_extra,
                                             cfg_tiempo, consultar_osrm_fn, umbral_pct)
            if destino is None:
                destino = _menos_mala_grupo(candidatas, conjunto, tipo, cfg_tiempo, consultar_osrm_fn)
            if destino is None:
                # Ningún vehículo de unidades_afines tiene ruta real esta
                # semana, o ninguno cabe -- nunca se inventa un destino.
                break

            for p in conjunto:
                _quitar_de_ruta(ruta, p, tipo)
            _recalcular_peso_ruta(ruta)
            for p in conjunto:
                _insertar_en_ruta(destino, p, tipo)
            _recalcular_peso_ruta(destino)
            cambio = True
        else:
            print(f"[tiempo_reubicacion] ruta {ruta.get('id')} alcanzó el tope de "
                  f"{MAX_MOVIMIENTOS_POR_RUTA} movimientos")

    return cambio
```

- [ ] **Step 4: Correr las pruebas y verificar que pasan**

Run: `python -m pytest tests/test_tiempo_reubicacion.py -k resolver_fuera_de_horario -v`
Expected: PASS (13 passed).

- [ ] **Step 5: Correr TODA la suite de `test_tiempo_reubicacion.py`**

Run: `python -m pytest tests/test_tiempo_reubicacion.py -v`
Expected: todos los tests del archivo en verde (mecánica pura + los reescritos en Tasks 1-6).

- [ ] **Step 6: Commit**

```bash
git add logic/tiempo_reubicacion.py tests/test_tiempo_reubicacion.py
git commit -m "feat(tiempo): resolver_fuera_de_horario usa grupos rigidos en vez de afinidad suelta"
```

---

## Task 7: Pruebas de regresión con los datos reales del incidente (grupo 30 y grupo 19)

**Files:**
- Modify: `tests/test_tiempo_reubicacion.py` (agregar al final)

- [ ] **Step 1: Escribir las pruebas de regresión**

Agregar al final de `tests/test_tiempo_reubicacion.py`. Usa los datos reales verificados
contra `plantilla_grupo` durante la investigación del incidente (ver
`docs/superpowers/specs/2026-08-07-tiempo-entrega-faseB-grupos-rigidos-design.md` §1):

```python
def test_regresion_tierra_blanca_7_no_va_a_ruta_sin_relacion_geografica(monkeypatch):
    import logic.tiempo_reubicacion as tr
    monkeypatch.setattr(tr, "TIEMPO_REUBICACION_ACTIVA", True)
    # Datos reales: grupo 30, RIGIDO, sucursales [76, 77], unidades_afines
    # real "T 23:3 | K 16:2 | T 20:2 | T 17_1:1 | T 25:1". T20 SI aparece
    # (2/9 semanas) -- si T20 no tiene ruta el lunes esta semana, T20 nunca
    # debe elegirse (no hay ruta real que buscar), y si la tiene, se mueven
    # 76 Y 77 juntas, nunca 76 sola separada de su pareja rigida.
    grupos = [{"grupo": 30, "rigidez": "RIGIDO", "sucursales": [76, 77],
               "unidades_afines": "T 23:3 | K 16:2 | T 20:2 | T 17_1:1 | T 25:1",
               "dias_admisibles": ["LUNES"], "dia_preferido": "LUNES"}]
    origen = {
        "id": "ORIGEN_T25_LUNES", "dia": "lunes", "vehiculo_abrev": "T 25",
        "capacidad_ton": 3.5, "peso_kg": 0, "pct_utilizacion": 0.0,
        "sucursales": [
            {"num_tienda": 1, "nombre": "Cercana", "orden": 1, "peso_kg": 100,
             "latitud": 0.05, "longitud": 0.05},
            {"num_tienda": 77, "nombre": "Tierra Blanca 8", "orden": 2, "peso_kg": 50,
             "latitud": 0.06, "longitud": 0.06},
            {"num_tienda": 76, "nombre": "Tierra Blanca 7", "orden": 3, "peso_kg": 100,
             "latitud": 0.5, "longitud": 0.5},
        ],
        "mayoristas": [],
    }
    # T 20 SI tiene ruta el lunes esta semana (candidata real de unidades_afines).
    t20_lunes = _ruta_vacia("T20_LUNES", "lunes", "T 20")
    # Ruta geograficamente ajena, sin ninguna relacion con el grupo 30 --
    # NUNCA debe recibir a 76/77 aunque tuviera cupo de sobra.
    ruta_ajena = _ruta_vacia("AJENA_SIN_AFINIDAD", "lunes", "AJENA")
    rutas = [origen, t20_lunes, ruta_ajena]

    resolver_fuera_de_horario(rutas, _cfg_cierre_08_30(), grupos, consultar_osrm_fn=None)

    # La ruta ajena (sin presencia en unidades_afines) jamas recibe nada.
    assert ruta_ajena["sucursales"] == []
    # Si algo se movio, 76 y 77 se movieron JUNTAS (nunca una sola).
    movidas_a_t20 = {s["num_tienda"] for s in t20_lunes["sucursales"]}
    if movidas_a_t20:
        assert movidas_a_t20 == {76, 77}
    else:
        # Si T20 no cupo para ambas, el grupo se queda completo en origen.
        assert {s["num_tienda"] for s in origen["sucursales"]} == {1, 76, 77}


def test_regresion_amatitlan_prefiere_vehiculo_dominante_sobre_uno_de_una_semana(monkeypatch):
    import logic.tiempo_reubicacion as tr
    monkeypatch.setattr(tr, "TIEMPO_REUBICACION_ACTIVA", True)
    # Datos reales: grupo 19, FLEXIBLE, sucursales [86, 100], cohesion 0.67,
    # unidades_afines real "F 350_1:7 | T 20:1 | T 25:1". F 350_1 es el
    # hogar dominante (7/9 semanas) -- si F 350_1 tiene otra ruta disponible
    # (otro dia admisible) con cupo+tiempo, debe preferirse sobre T 25
    # (1/9 semanas) aunque T 25 tambien cumpla.
    grupos = [{"grupo": 19, "rigidez": "FLEXIBLE", "sucursales": [86, 100],
               "unidades_afines": "F 350_1:7 | T 20:1 | T 25:1",
               "dias_admisibles": ["MARTES", "JUEVES"], "dia_preferido": "JUEVES"}]
    origen = {
        "id": "ORIGEN_F350_1_MARTES", "dia": "martes", "vehiculo_abrev": "F 350_1",
        "capacidad_ton": 3.5, "peso_kg": 0, "pct_utilizacion": 0.0,
        "sucursales": [
            {"num_tienda": 1, "nombre": "Cercana", "orden": 1, "peso_kg": 100,
             "latitud": 0.05, "longitud": 0.05},
            {"num_tienda": 100, "nombre": "Amatitlan", "orden": 2, "peso_kg": 100,
             "latitud": 0.5, "longitud": 0.5},
        ],
        "mayoristas": [],
    }
    f350_1_jueves = _ruta_vacia("F350_1_JUEVES", "jueves", "F 350_1")
    t25_jueves = _ruta_vacia("T25_JUEVES", "jueves", "T 25")
    rutas = [origen, t25_jueves, f350_1_jueves]  # orden de lista no debe importar

    resolver_fuera_de_horario(rutas, _cfg_cierre_08_30(), grupos, consultar_osrm_fn=None)

    # F 350_1 (dominante, 7/9) se prueba antes que T 25 (1/9) -- si F 350_1
    # cumple cupo+tiempo, se elige ahi, no en T 25.
    assert 100 in [s["num_tienda"] for s in f350_1_jueves["sucursales"]]
    assert [s["num_tienda"] for s in t25_jueves["sucursales"]] == []
```

- [ ] **Step 2: Correr las pruebas y verificar que fallan primero por las razones correctas**

Run: `python -m pytest tests/test_tiempo_reubicacion.py -k regresion -v`
Expected: si Task 6 ya está completo, estas pruebas deben **pasar directamente** (no
hay código nuevo que escribir en este task — es la prueba de que el
algoritmo de Task 6 ya resuelve los dos incidentes reales). Si alguna
falla, es una señal real de un caso borde no cubierto en Task 6 — revisar
antes de continuar, no ajustar la prueba para que pase artificialmente.

- [ ] **Step 3: Confirmar que pasan**

Run: `python -m pytest tests/test_tiempo_reubicacion.py -k regresion -v`
Expected: PASS (2 passed).

- [ ] **Step 4: Commit**

```bash
git add tests/test_tiempo_reubicacion.py
git commit -m "test(tiempo): regresion explicita - Tierra Blanca 7 (grupo rigido) y Amatitlan (dominante vs 1 semana)"
```

---

## Task 8: Actualizar `pdf_logic.py` para usar `obtener_grupos()` en vez de `afinidad_historica_por_sucursal()`

**Files:**
- Modify: `logic/pdf_logic.py:29` (import)
- Modify: `logic/pdf_logic.py:754-761` (llamada dentro de `generar_pdf()`)

- [ ] **Step 1: Cambiar el import**

En `logic/pdf_logic.py`, reemplazar la línea 29:

```python
from logic.historico_logic import afinidad_historica_por_sucursal
```

por:

```python
from logic.plantilla_canonica import obtener_grupos
```

- [ ] **Step 2: Cambiar la llamada dentro de `generar_pdf()`**

Reemplazar (líneas 738-761, el bloque completo del comentario "Fase B" hasta el cierre del `try`):

```python
    # Fase B — reubicar paradas FUERA DE HORARIO hacia otra ruta con
    # afinidad histórica, cupo y tiempo. No aplica sobre rutas_inyectadas
    # (previsualización en memoria; la regla dura del proyecto prohíbe
    # persistir ahí). Degradación segura: ante cualquier error, las rutas
    # quedan como Fase A las entregó (sin reubicar).
    #
    # Nota (encontrada en la revisión de calidad del Task 7): tras mover una
    # parada, esta pasada NO recalcula hora_salida/hora_regreso/distancia_km/
    # conduccion_min/total_min de las rutas tocadas (solo peso/%utilización y
    # las paradas en sí) — mismo comportamiento ya aceptado en
    # agregar_sucursal_a_asignacion/quitar_sucursal_a_asignacion de
    # modificacion_logic.py, que tampoco recalculan esos campos. La lista de
    # paradas que ve el conductor SÍ queda correcta (es lo que importa para
    # la entrega); las estimaciones de hora de salida/regreso pueden quedar
    # desactualizadas hasta el siguiente guardado completo de Modificación.
    if cfg_tiempo and not rutas_inyectadas:
        try:
            # Sin caché: relee y reprocesa las 9 semanas del corpus histórico
            # en cada generación de PDF. Aceptado por ahora (misma decisión
            # que la falta de recálculo de hora_salida/hora_regreso arriba);
            # revisar si la frecuencia de generación de PDF crece.
            afinidad = afinidad_historica_por_sucursal()
            movio_algo = resolver_fuera_de_horario(rutas, cfg_tiempo, afinidad,
                                                    consultar_osrm_fn=consultar_osrm)
```

por:

```python
    # Fase B — reubicar paradas FUERA DE HORARIO hacia otra ruta con
    # respaldo real de grupo de co-viaje (plantilla_grupo, ya validada por
    # el trabajo de ConVRP — ver docs/superpowers/specs/2026-08-07-tiempo-
    # entrega-faseB-grupos-rigidos-design.md), cupo y tiempo. No aplica
    # sobre rutas_inyectadas (previsualización en memoria; la regla dura del
    # proyecto prohíbe persistir ahí). Degradación segura: ante cualquier
    # error, las rutas quedan como Fase A las entregó (sin reubicar).
    #
    # Nota (heredada de la v1 de Fase B): tras mover una parada, esta pasada
    # NO recalcula hora_salida/hora_regreso/distancia_km/conduccion_min/
    # total_min de las rutas tocadas (solo peso/%utilización y las paradas
    # en sí) — mismo comportamiento ya aceptado en
    # agregar_sucursal_a_asignacion/quitar_sucursal_a_asignacion de
    # modificacion_logic.py, que tampoco recalculan esos campos. La lista de
    # paradas que ve el conductor SÍ queda correcta (es lo que importa para
    # la entrega); las estimaciones de hora de salida/regreso pueden quedar
    # desactualizadas hasta el siguiente guardado completo de Modificación.
    if cfg_tiempo and not rutas_inyectadas:
        try:
            grupos = obtener_grupos()
            movio_algo = resolver_fuera_de_horario(rutas, cfg_tiempo, grupos,
                                                    consultar_osrm_fn=consultar_osrm)
```

El resto del bloque `try/except` (guardado con `guardar_modificacion` y el
`except Exception as e` final) no cambia — no lo toques.

- [ ] **Step 3: Verificar sintaxis**

Run: `python -c "import ast; ast.parse(open('logic/pdf_logic.py', encoding='utf-8').read()); print('PY OK')"`
Expected: `PY OK`

- [ ] **Step 4: Correr toda la suite para confirmar que no hay regresión**

Run: `python -m pytest tests/ -q`
Expected: PASS — todas las pruebas del proyecto en verde (`afinidad_historica_por_sucursal` sigue existiendo en `historico_logic.py` y sus propios tests, solo dejó de tener consumidor en `pdf_logic.py`).

- [ ] **Step 5: Commit**

```bash
git add logic/pdf_logic.py
git commit -m "refactor(pdf): generar_pdf usa obtener_grupos() en vez de afinidad_historica_por_sucursal()"
```

---

## Task 9: Verificación manual contra la logística real del incidente

**Files:** ninguno (solo verificación, sin cambios de código)

- [ ] **Step 1: Confirmar que la suite completa sigue en verde**

Run: `python -m pytest tests/ -q`
Expected: todos los tests pasan (incluidos los ~150+ preexistentes del resto del proyecto, sin tocar).

- [ ] **Step 2: Probar en memoria contra los grupos reales 30 y 19**

Con la app corriendo (`python .\app.py`) y dentro de un `flask_app.app_context()`
(mismo patrón usado durante la investigación del incidente), confirmar que
`plantilla_canonica.obtener_grupos()` devuelve los grupos 30 y 19 con los
mismos datos usados en las pruebas de regresión del Task 7:

```python
import app as appmod
with appmod.app.app_context():
    from logic.plantilla_canonica import obtener_grupos
    grupos = obtener_grupos()
    g30 = next(g for g in grupos if g["grupo"] == 30)
    g19 = next(g for g in grupos if g["grupo"] == 19)
    assert g30["rigidez"] == "RIGIDO" and set(g30["sucursales"]) == {76, 77}
    assert g19["rigidez"] == "FLEXIBLE" and set(g19["sucursales"]) == {86, 100}
    print("OK -- datos reales coinciden con los fixtures de regresion")
```

Expected: `OK -- datos reales coinciden con los fixtures de regresion`. Si
algo no coincide (la plantilla se recargó con una versión nueva desde la
investigación del incidente), ajustar los fixtures del Task 7 a los datos
reales vigentes antes de continuar — no dejar una prueba de regresión que
no refleje la plantilla actual.

---

## Task 10: Reactivar `TIEMPO_REUBICACION_ACTIVA` (requiere confirmación explícita del usuario)

**Files:**
- Modify: `logic/tiempo_reubicacion.py:36`

**No ejecutar este task automáticamente.** Es el único paso de todo el plan
que cambia comportamiento en producción (Fase B vuelve a mover y persistir
rutas). Antes de aplicarlo:

1. Confirmar con el usuario que quiere reactivar Fase B ahora que v2 está
   implementada y probada (Tasks 1-9 completos, suite en verde).
2. Recordarle que el efecto es inmediato la próxima vez que alguien genere
   un PDF con paradas FUERA DE HORARIO — Fase B empezará a mover y guardar
   de nuevo, ahora guiada por grupos rígidos.

- [ ] **Step 1 (solo tras confirmación explícita): cambiar el flag**

En `logic/tiempo_reubicacion.py`, línea 36:

```python
TIEMPO_REUBICACION_ACTIVA = False
```

por:

```python
TIEMPO_REUBICACION_ACTIVA = True
```

- [ ] **Step 2: Correr la suite completa una última vez**

Run: `python -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add logic/tiempo_reubicacion.py
git commit -m "feat(tiempo): reactivar Fase B (TIEMPO_REUBICACION_ACTIVA=True) con seleccion por grupos rigidos"
```
