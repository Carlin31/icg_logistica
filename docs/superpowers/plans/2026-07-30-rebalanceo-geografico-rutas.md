# Rebalanceo Geográfico de Rutas — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que las rutas queden geográficamente compactas (la cercanía gana sobre el histórico) reacomodando sucursales entre rutas del mismo día, respetando peso y volumen y sin cambiar el número de rutas ni el día de cada cliente.

**Architecture:** Módulo puro y aislado (`rebalanceo_geografico.py`) con búsqueda local (reubicar/intercambiar) que minimiza la dispersión al centroide. Se engancha como paso posterior dentro de `generar_rutas_vrp_afinidad`, tras `_consolidar_aisladas` y antes de armar `report_rows`, detrás de un interruptor y con degradación segura.

**Tech Stack:** Python 3.11, pandas/SQLAlchemy (solo en el wiring, no en el módulo), pytest.

---

## Estructura de archivos

- **Crear** `logic/vrp_afinidad/rebalanceo_geografico.py` — el módulo puro de rebalanceo (sin BD, sin OSRM).
- **Crear** `tests/test_rebalanceo_geografico.py` — pruebas unitarias del módulo.
- **Modificar** `logic/vrp_logic.py` — agregar `obtener_volumenes_vehiculos()` (lectura de `vehiculos.volumen_m3`).
- **Modificar** `logic/historico_logic.py` — interruptor `REBALANCEO_GEOGRAFICO`, imports, y el enganche envuelto en `try/except` tras la línea de `_consolidar_aisladas`.

Referencias del punto de enganche (estado actual):
- `groups` se finaliza en `logic/historico_logic.py:1130` (`_consolidar_aisladas`).
- `report_rows` empieza a construirse en `logic/historico_logic.py:1144`.
- Datos ya disponibles ahí: `coords_dict` (`{sid:(lat,lon)}`), `pedidos_dict` (`{sid:kg}`), `volumenes_dict` (`{sid:m³}`), `vehiculos_cap` (`{abrev:kg}`).

---

## Task 1: Exponer volumen de vehículos (`obtener_volumenes_vehiculos`)

**Files:**
- Modify: `logic/vrp_logic.py` (agregar función junto a `obtener_capacidades_vehiculos`, ~línea 181)
- Test: `tests/test_rebalanceo_geografico.py`

- [ ] **Step 1: Escribir la función**

En `logic/vrp_logic.py`, inmediatamente después de `obtener_capacidades_vehiculos` (después de su `return` en la línea ~181), agregar:

```python
def obtener_volumenes_vehiculos() -> dict:
    """
    Lee el volumen (m³) de cada vehículo desde SQL Server.
    Retorna: {abreviatura: volumen_m3 (float)} — solo vehículos con volumen > 0.
    Espejo de obtener_capacidades_vehiculos() pero para el límite volumétrico.
    """
    try:
        db    = get_db()
        tabla = get_table("vehiculos")
        vols  = {}
        for v in db.execute(select(tabla)).mappings():
            abrev = (v.get("abreviatura") or v.get("descripcion") or "").strip()
            vol   = float(v.get("volumen_m3") or 0)
            if abrev and vol > 0:
                vols[abrev] = vol
        return vols
    except Exception:
        return {}
```

- [ ] **Step 2: Verificar que importa sin error**

Run: `./env/Scripts/python.exe -c "import ast; ast.parse(open('logic/vrp_logic.py',encoding='utf-8').read()); print('PY OK')"`
Expected: `PY OK`

- [ ] **Step 3: Commit**

```bash
git add logic/vrp_logic.py
git commit -m "feat(vrp): exponer volumen_m3 de vehiculos (obtener_volumenes_vehiculos)"
```

---

## Task 2: Módulo — funciones de costo (haversine, centroide, costo_ruta)

**Files:**
- Create: `logic/vrp_afinidad/rebalanceo_geografico.py`
- Test: `tests/test_rebalanceo_geografico.py`

- [ ] **Step 1: Escribir la prueba del costo**

Crear `tests/test_rebalanceo_geografico.py`:

```python
from logic.vrp_afinidad.rebalanceo_geografico import (
    _haversine, _costo_ruta,
)


def test_costo_ruta_un_punto_es_cero():
    # Una sola sucursal no tiene dispersión.
    coords = {1: (18.90, -96.95)}
    assert _costo_ruta([1], coords) == 0.0


def test_costo_ruta_crece_con_dispersion():
    coords = {1: (18.90, -96.95), 2: (18.91, -96.95), 99: (18.30, -96.10)}
    compacto = _costo_ruta([1, 2], coords)
    disperso = _costo_ruta([1, 2, 99], coords)
    assert disperso > compacto > 0
```

- [ ] **Step 2: Correr la prueba y verificar que falla**

Run: `./env/Scripts/python.exe -m pytest tests/test_rebalanceo_geografico.py -q`
Expected: FAIL con `ModuleNotFoundError` / `ImportError`.

- [ ] **Step 3: Crear el módulo con las funciones de costo**

Crear `logic/vrp_afinidad/rebalanceo_geografico.py`:

```python
"""
logic/vrp_afinidad/rebalanceo_geografico.py

Rebalanceo geográfico de rutas por búsqueda local. La cercanía entre
sucursales gana sobre el patrón histórico: reacomoda sucursales entre rutas
del MISMO día para minimizar la dispersión al centroide, respetando peso y
volumen del vehículo y sin cambiar el número de rutas ni el día de cada
sucursal.

Módulo puro: sin BD, sin OSRM. Entrada/salida son estructuras en memoria.
"""
import math
from collections import defaultdict

_EPS = 1e-9


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _coords_validas(sids: list, coords: dict) -> list:
    return [coords[s] for s in sids if s in coords]


def _centroide(pts: list) -> tuple:
    n = len(pts)
    return (sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n)


def _costo_ruta(sids: list, coords: dict) -> float:
    """Dispersión = suma de distancias de cada sucursal al centroide de la ruta.
    Sucursales sin coordenadas se ignoran en el cálculo. <2 puntos -> 0."""
    pts = _coords_validas(sids, coords)
    if len(pts) < 2:
        return 0.0
    clat, clon = _centroide(pts)
    return sum(_haversine(lat, lon, clat, clon) for lat, lon in pts)
```

- [ ] **Step 4: Correr la prueba y verificar que pasa**

Run: `./env/Scripts/python.exe -m pytest tests/test_rebalanceo_geografico.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add logic/vrp_afinidad/rebalanceo_geografico.py tests/test_rebalanceo_geografico.py
git commit -m "feat(vrp): modulo rebalanceo geografico — funciones de costo"
```

---

## Task 3: Módulo — chequeo de capacidad (peso + volumen)

**Files:**
- Modify: `logic/vrp_afinidad/rebalanceo_geografico.py`
- Test: `tests/test_rebalanceo_geografico.py`

- [ ] **Step 1: Escribir la prueba de capacidad**

Agregar a `tests/test_rebalanceo_geografico.py`:

```python
from logic.vrp_afinidad.rebalanceo_geografico import _cabe


def test_cabe_respeta_peso_y_volumen():
    pesos = {1: 100, 2: 100}
    vols  = {1: 1.0, 2: 1.0}
    # cabe: peso 200<=200 y vol 2.0<=3.0
    assert _cabe([1, 2], "V", pesos, vols, {"V": 200}, {"V": 3.0}) is True
    # no cabe por peso: 200>150
    assert _cabe([1, 2], "V", pesos, vols, {"V": 150}, {"V": 3.0}) is False
    # no cabe por volumen: 2.0>1.5
    assert _cabe([1, 2], "V", pesos, vols, {"V": 200}, {"V": 1.5}) is False


def test_cabe_sin_capacidad_definida_no_bloquea():
    # Vehículo sin capacidad registrada -> no impone límite (inf).
    assert _cabe([1], "DESCONOCIDO", {1: 999999}, {1: 999999}, {}, {}) is True
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `./env/Scripts/python.exe -m pytest tests/test_rebalanceo_geografico.py::test_cabe_respeta_peso_y_volumen -q`
Expected: FAIL con `ImportError` (`_cabe` no existe).

- [ ] **Step 3: Implementar `_cabe`**

Agregar a `logic/vrp_afinidad/rebalanceo_geografico.py` (después de `_costo_ruta`):

```python
def _peso_sids(sids: list, pesos: dict) -> float:
    return sum(pesos.get(s, 0) for s in sids)


def _vol_sids(sids: list, volumenes: dict) -> float:
    return sum(volumenes.get(s, 0.0) for s in sids)


def _cabe(sids: list, veh: str, pesos: dict, volumenes: dict,
          cap_peso: dict, cap_vol: dict) -> bool:
    """True si el conjunto `sids` no excede ni el peso ni el volumen del
    vehículo `veh`. Un vehículo sin capacidad registrada no impone límite."""
    return (_peso_sids(sids, pesos) <= cap_peso.get(veh, float("inf"))
            and _vol_sids(sids, volumenes) <= cap_vol.get(veh, float("inf")))
```

- [ ] **Step 4: Correr y verificar que pasa**

Run: `./env/Scripts/python.exe -m pytest tests/test_rebalanceo_geografico.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add logic/vrp_afinidad/rebalanceo_geografico.py tests/test_rebalanceo_geografico.py
git commit -m "feat(vrp): rebalanceo — chequeo de capacidad peso+volumen"
```

---

## Task 4: Módulo — función principal `rebalancear_por_geografia`

**Files:**
- Modify: `logic/vrp_afinidad/rebalanceo_geografico.py`
- Test: `tests/test_rebalanceo_geografico.py`

- [ ] **Step 1: Escribir las pruebas de comportamiento**

Agregar a `tests/test_rebalanceo_geografico.py`:

```python
from logic.vrp_afinidad.rebalanceo_geografico import rebalancear_por_geografia


def _sids(grupo):
    return sorted(m["sid"] for m in grupo)


# Cluster V1 compacto en (18.90,-96.95); outlier 99 lejos, cerca de V2.
COORDS = {
    1:  (18.90, -96.95),
    2:  (18.91, -96.95),
    99: (18.30, -96.10),   # lejos de V1, cerca de V2
    3:  (18.31, -96.11),
}


def test_outlier_se_mueve_a_ruta_cercana():
    groups = {
        ("V1", "LUNES"): [{"sid": 1, "seq": 1}, {"sid": 2, "seq": 2}, {"sid": 99, "seq": 3}],
        ("V2", "LUNES"): [{"sid": 3, "seq": 1}],
    }
    pesos = {1: 100, 2: 100, 99: 100, 3: 100}
    vols  = {1: 1.0, 2: 1.0, 99: 1.0, 3: 1.0}
    out = rebalancear_por_geografia(
        groups, COORDS, pesos, vols, {"V1": 10000, "V2": 10000}, {"V1": 999, "V2": 999})
    assert 99 in _sids(out[("V2", "LUNES")])
    assert 99 not in _sids(out[("V1", "LUNES")])


def test_no_mueve_si_excede_peso():
    groups = {
        ("V1", "LUNES"): [{"sid": 1, "seq": 1}, {"sid": 2, "seq": 2}, {"sid": 99, "seq": 3}],
        ("V2", "LUNES"): [{"sid": 3, "seq": 1}],
    }
    pesos = {1: 100, 2: 100, 99: 100, 3: 100}
    vols  = {1: 1.0, 2: 1.0, 99: 1.0, 3: 1.0}
    # V2 solo aguanta 150: 3(100)+99(100)=200 > 150 -> 99 se queda en V1
    out = rebalancear_por_geografia(
        groups, COORDS, pesos, vols, {"V1": 10000, "V2": 150}, {"V1": 999, "V2": 999})
    assert 99 in _sids(out[("V1", "LUNES")])


def test_no_mueve_si_excede_volumen():
    groups = {
        ("V1", "LUNES"): [{"sid": 1, "seq": 1}, {"sid": 2, "seq": 2}, {"sid": 99, "seq": 3}],
        ("V2", "LUNES"): [{"sid": 3, "seq": 1}],
    }
    pesos = {1: 100, 2: 100, 99: 100, 3: 100}
    vols  = {1: 1.0, 2: 1.0, 99: 1.0, 3: 1.0}
    # V2 aguanta peso pero volumen 1.5: 3(1.0)+99(1.0)=2.0 > 1.5 -> se queda
    out = rebalancear_por_geografia(
        groups, COORDS, pesos, vols, {"V1": 10000, "V2": 10000}, {"V1": 999, "V2": 1.5})
    assert 99 in _sids(out[("V1", "LUNES")])


def test_intercambio_cuando_ambas_llenas():
    # A y B llenas (200 c/u). Un swap 1-a-1 mantiene el peso y compacta.
    coords = {
        11: (0.0, 0.0), 12: (0.0, 10.0),     # A: un punto lejos
        21: (0.0, 10.1), 22: (0.0, 0.1),     # B: un punto lejos (cruzado)
    }
    groups = {
        ("A", "LUNES"): [{"sid": 11, "seq": 1}, {"sid": 12, "seq": 2}],
        ("B", "LUNES"): [{"sid": 21, "seq": 1}, {"sid": 22, "seq": 2}],
    }
    pesos = {11: 100, 12: 100, 21: 100, 22: 100}
    vols  = {11: 1.0, 12: 1.0, 21: 1.0, 22: 1.0}
    out = rebalancear_por_geografia(
        groups, coords, pesos, vols, {"A": 200, "B": 200}, {"A": 999, "B": 999})
    a = _sids(out[("A", "LUNES")])
    b = _sids(out[("B", "LUNES")])
    # Tras el swap: A agrupa los cercanos a 0 (11,22) y B los cercanos a 10 (12,21)
    assert a == [11, 22]
    assert b == [12, 21]


def test_sucursal_sin_coords_se_queda_fija():
    groups = {
        ("V1", "LUNES"): [{"sid": 1, "seq": 1}, {"sid": 2, "seq": 2}, {"sid": 77, "seq": 3}],
        ("V2", "LUNES"): [{"sid": 3, "seq": 1}],
    }
    # 77 no tiene coordenadas
    coords = {1: (18.90, -96.95), 2: (18.91, -96.95), 3: (18.31, -96.11)}
    pesos = {1: 100, 2: 100, 77: 100, 3: 100}
    vols  = {1: 1.0, 2: 1.0, 77: 1.0, 3: 1.0}
    out = rebalancear_por_geografia(
        groups, coords, pesos, vols, {"V1": 10000, "V2": 10000}, {"V1": 999, "V2": 999})
    assert 77 in _sids(out[("V1", "LUNES")])


def test_invariantes_y_determinismo():
    groups = {
        ("V1", "LUNES"): [{"sid": 1, "seq": 1}, {"sid": 2, "seq": 2}, {"sid": 99, "seq": 3}],
        ("V2", "LUNES"): [{"sid": 3, "seq": 1}],
    }
    pesos = {1: 100, 2: 100, 99: 100, 3: 100}
    vols  = {1: 1.0, 2: 1.0, 99: 1.0, 3: 1.0}
    caps_p, caps_v = {"V1": 10000, "V2": 10000}, {"V1": 999, "V2": 999}
    out1 = rebalancear_por_geografia(groups, COORDS, pesos, vols, caps_p, caps_v)
    out2 = rebalancear_por_geografia(out1, COORDS, pesos, vols, caps_p, caps_v)
    # Mismo número de rutas y mismos días
    assert set(out1.keys()) == {("V1", "LUNES"), ("V2", "LUNES")}
    # Mismo conjunto total de sucursales (nada se pierde ni duplica)
    todas = sorted(m["sid"] for g in out1.values() for m in g)
    assert todas == [1, 2, 3, 99]
    # Ninguna ruta queda vacía
    assert all(len(g) >= 1 for g in out1.values())
    # Idempotente: correr de nuevo no cambia nada
    assert {k: _sids(v) for k, v in out2.items()} == {k: _sids(v) for k, v in out1.items()}
```

- [ ] **Step 2: Correr y verificar que fallan**

Run: `./env/Scripts/python.exe -m pytest tests/test_rebalanceo_geografico.py -q`
Expected: FAIL con `ImportError` (`rebalancear_por_geografia` no existe).

- [ ] **Step 3: Implementar `rebalancear_por_geografia`**

Agregar al final de `logic/vrp_afinidad/rebalanceo_geografico.py`:

```python
def rebalancear_por_geografia(groups: dict, coords: dict, pesos: dict,
                              volumenes: dict, cap_peso: dict, cap_vol: dict,
                              max_iter: int = 500) -> dict:
    """
    Reacomoda sucursales entre rutas del MISMO día para minimizar la dispersión
    geográfica, respetando peso y volumen. No crea/elimina rutas ni cambia días.

    groups     : {(vehiculo, dia): [{"sid": int, "seq": int}, ...]}
    coords     : {sid: (lat, lon)}
    pesos      : {sid: kg}
    volumenes  : {sid: m3}
    cap_peso   : {vehiculo: kg}
    cap_vol    : {vehiculo: m3}

    Retorna un `groups` nuevo con la misma forma.
    """
    # sids por ruta (copia mutable) + metadatos para reconstruir
    rutas = {k: [m["sid"] for m in v] for k, v in groups.items()}
    seq_original: dict = {}
    ruta_original: dict = {}
    for k, v in groups.items():
        for m in v:
            seq_original[m["sid"]] = m.get("seq", 999)
            ruta_original[m["sid"]] = k

    por_dia: dict = defaultdict(list)
    for k in rutas:
        por_dia[k[1]].append(k)
    for dia in por_dia:
        por_dia[dia].sort()  # orden estable -> determinismo

    def costo_par(a, b) -> float:
        return _costo_ruta(rutas[a], coords) + _costo_ruta(rutas[b], coords)

    for _ in range(max_iter):
        mejor_delta = -_EPS
        mejor_mov = None  # ("reloc", a, b, sid) | ("swap", a, b, sa, sb)

        for dia in sorted(por_dia):
            keys = por_dia[dia]
            if len(keys) < 2:
                continue

            # REUBICAR: mover un sid de a -> b
            for a in keys:
                if len(rutas[a]) <= 1:
                    continue  # no vaciar una ruta (conserva el número de rutas)
                for sid in list(rutas[a]):
                    if sid not in coords:
                        continue
                    for b in keys:
                        if b == a:
                            continue
                        destino = rutas[b] + [sid]
                        if not _cabe(destino, b[0], pesos, volumenes, cap_peso, cap_vol):
                            continue
                        antes = costo_par(a, b)
                        origen = [s for s in rutas[a] if s != sid]
                        despues = _costo_ruta(origen, coords) + _costo_ruta(destino, coords)
                        delta = despues - antes
                        if delta < mejor_delta:
                            mejor_delta = delta
                            mejor_mov = ("reloc", a, b, sid, None)

            # INTERCAMBIAR: sa de a <-> sb de b
            for ia in range(len(keys)):
                for ib in range(ia + 1, len(keys)):
                    a, b = keys[ia], keys[ib]
                    for sa in list(rutas[a]):
                        if sa not in coords:
                            continue
                        for sb in list(rutas[b]):
                            if sb not in coords:
                                continue
                            nueva_a = [s for s in rutas[a] if s != sa] + [sb]
                            nueva_b = [s for s in rutas[b] if s != sb] + [sa]
                            if not _cabe(nueva_a, a[0], pesos, volumenes, cap_peso, cap_vol):
                                continue
                            if not _cabe(nueva_b, b[0], pesos, volumenes, cap_peso, cap_vol):
                                continue
                            antes = costo_par(a, b)
                            despues = _costo_ruta(nueva_a, coords) + _costo_ruta(nueva_b, coords)
                            delta = despues - antes
                            if delta < mejor_delta:
                                mejor_delta = delta
                                mejor_mov = ("swap", a, b, sa, sb)

        if mejor_mov is None:
            break

        if mejor_mov[0] == "reloc":
            _, a, b, sid, _ = mejor_mov
            rutas[a].remove(sid)
            rutas[b].append(sid)
        else:
            _, a, b, sa, sb = mejor_mov
            rutas[a].remove(sa); rutas[a].append(sb)
            rutas[b].remove(sb); rutas[b].append(sa)

    # Reconstruir groups. A las sucursales que cambiaron de ruta se les pone
    # seq=999 (no tienen orden histórico válido en su nueva ruta; el
    # re-secuenciado posterior las coloca por proximidad).
    nuevo: dict = {}
    for k, sids in rutas.items():
        miembros = []
        for s in sids:
            seq = seq_original.get(s, 999) if ruta_original.get(s) == k else 999
            miembros.append({"sid": s, "seq": seq})
        nuevo[k] = miembros
    return nuevo
```

- [ ] **Step 4: Correr y verificar que pasan**

Run: `./env/Scripts/python.exe -m pytest tests/test_rebalanceo_geografico.py -q`
Expected: PASS (todas, ~10 passed).

- [ ] **Step 5: Commit**

```bash
git add logic/vrp_afinidad/rebalanceo_geografico.py tests/test_rebalanceo_geografico.py
git commit -m "feat(vrp): rebalanceo geografico — busqueda local (reubicar/intercambiar)"
```

---

## Task 5: Enganchar en `generar_rutas_vrp_afinidad` (interruptor + degradación segura)

**Files:**
- Modify: `logic/historico_logic.py` (imports; constante; enganche tras línea ~1133)

- [ ] **Step 1: Agregar imports y el interruptor**

En `logic/historico_logic.py`, en la sección de imports desde `logic.vrp_logic` (alrededor de la línea 42-48, donde ya se importan `obtener_capacidades_vehiculos`, `capacidad_efectiva_kg`), agregar `obtener_volumenes_vehiculos` a esa lista de import. Luego, cerca del inicio del módulo (después de los imports, junto a otras constantes de módulo), agregar:

```python
from logic.vrp_afinidad.rebalanceo_geografico import rebalancear_por_geografia

# Interruptor del rebalanceo geográfico de rutas. True = las rutas se compactan
# geográficamente (la cercanía gana sobre el histórico). False = comportamiento
# anterior idéntico.
REBALANCEO_GEOGRAFICO = True
```

Y agregar `obtener_volumenes_vehiculos` al import existente de `logic.vrp_logic` (misma línea/lista donde está `obtener_capacidades_vehiculos`).

- [ ] **Step 2: Insertar el enganche**

En `logic/historico_logic.py`, justo DESPUÉS del bloque `_consolidar_aisladas` que termina en la línea ~1133 (la asignación `groups = _consolidar_aisladas(...)`) y ANTES del comentario `# ── 7. Estadísticas históricas ...` (línea ~1135), insertar:

```python
    # ── 6.6. Rebalanceo geográfico: compactar rutas por cercanía (por día),
    # respetando peso y volumen. Degradación segura: ante cualquier error se
    # conservan las rutas sin rebalancear.
    if REBALANCEO_GEOGRAFICO:
        try:
            vehiculos_vol = obtener_volumenes_vehiculos()
            groups = rebalancear_por_geografia(
                groups,
                coords_dict,
                pedidos_dict,
                volumenes_dict,
                vehiculos_cap,
                vehiculos_vol,
            )
        except Exception as e:  # noqa: BLE001
            print(f"[rebalanceo_geografico] omitido por error: {e}")
```

- [ ] **Step 3: Verificar sintaxis**

Run: `./env/Scripts/python.exe -c "import ast; ast.parse(open('logic/historico_logic.py',encoding='utf-8').read()); print('PY OK')"`
Expected: `PY OK`

- [ ] **Step 4: Verificar que el módulo importa en contexto de app**

Run: `./env/Scripts/python.exe -c "import sys; sys.path.insert(0,'.'); import logic.historico_logic as h; print('import OK', h.REBALANCEO_GEOGRAFICO)"`
Expected: `import OK True`

- [ ] **Step 5: Commit**

```bash
git add logic/historico_logic.py
git commit -m "feat(vrp): enganchar rebalanceo geografico en generar_rutas_vrp_afinidad"
```

---

## Task 6: Verificación de regresión y humo

**Files:** (ninguno — solo verificación)

- [ ] **Step 1: Correr toda la suite**

Run: `./env/Scripts/python.exe -m pytest tests/ -q`
Expected: PASS — 34 previas + las nuevas de `test_rebalanceo_geografico.py`, 0 fallas.

- [ ] **Step 2: Humo end-to-end (opcional, requiere BD)**

Con la BD levantada, generar el VRP de una logística y confirmar que sigue produciendo rutas sin error y con el mismo número de rutas por día. Ejemplo (script temporal en scratchpad, NO en el repo):

```python
# app.test_request_context + generar_rutas_vrp_afinidad(lid)
# Verificar: status == "ok" y total_rutas igual que con REBALANCEO_GEOGRAFICO=False.
```

Comparar `total_rutas` con el flag en `True` vs `False` para confirmar que el número de rutas no cambió y que las rutas quedaron más compactas (menor distancia intra-ruta).

- [ ] **Step 3: Commit final (si hubo ajustes)**

```bash
git add -A
git commit -m "test(vrp): verificacion de regresion del rebalanceo geografico"
```

---

## Self-Review (cobertura del spec)

- Geografía gana sobre histórico → Task 4 (búsqueda local que compacta). ✓
- Conservar el día de cada sucursal → sólo se mueve entre rutas del mismo `dia` (Task 4, `por_dia`). ✓
- Mismo número de rutas por día → no se crean/eliminan rutas; guarda anti-vaciado (`len(rutas[a]) <= 1`). ✓
- Peso y volumen duros → `_cabe` (Task 3) usado en cada movimiento (Task 4); volumen expuesto en Task 1. ✓
- Módulo aislado + paso posterior + interruptor + degradación segura → Task 5. ✓
- Pruebas (outlier, peso, volumen, swap, sin-coords, idempotente, invariantes) → Task 4. ✓
- Fuera de alcance (días, número de rutas, núcleo VRP, mayoristas/OSRM/guardado) → no se tocan. ✓
