# Tiempo de entrega — Fase B (reubicar FUERA DE HORARIO) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que las paradas que Fase A marca FUERA DE HORARIO se reubiquen solas, al generar el PDF, en otra ruta con afinidad histórica real, cupo (≤85 % de utilización) y tiempo — y que ese movimiento se guarde en la base de datos, no solo en el PDF impreso.

**Architecture:** Módulo nuevo y mayormente puro `logic/tiempo_reubicacion.py` (recibe `consultar_osrm_fn` inyectado en vez de importar OSRM directamente, para poder probarse sin red). Se engancha como paso posterior dentro de `generar_pdf()` (`pdf_logic.py`), después de construir `rutas` y antes de renderizar. La persistencia reutiliza `modificacion_logic.guardar_modificacion()` (reemplazo completo de `modificaciones_rutas`, ya probado). Se extrae primero la evaluación de horario de Fase A (hoy duplicada dentro de `_tabla_vehiculo`) a una función reutilizable, para que Fase B decida con el mismo criterio que finalmente se imprime.

**Tech Stack:** Python 3.11, SQLAlchemy Core (solo en el wiring de `pdf_logic.py`/`historico_logic.py`, no en el módulo puro), pytest.

**Continúa:** [docs/superpowers/specs/2026-08-06-tiempo-entrega-faseB-design.md](../specs/2026-08-06-tiempo-entrega-faseB-design.md), que a su vez continúa [2026-07-30-tiempo-entrega-faseA-design.md](../specs/2026-07-30-tiempo-entrega-faseA-design.md).

---

## Estructura de archivos

- **Crear** `logic/tiempo_reubicacion.py` — módulo de Fase B: evaluación de horario reutilizable, selección de ruta destino por afinidad histórica, inserción/remoción de paradas, orquestador `resolver_fuera_de_horario`.
- **Crear** `tests/test_tiempo_reubicacion.py` — pruebas unitarias del módulo.
- **Modificar** `logic/historico_logic.py` — nueva función pública `afinidad_historica_por_sucursal()` (wrapper de dos funciones privadas ya existentes).
- **Modificar** `logic/pdf_logic.py` — refactor de `_tabla_vehiculo` para reusar `evaluar_ruta_completa`; enganche de `resolver_fuera_de_horario` + persistencia dentro de `generar_pdf()`.

Referencias del estado actual (línea aproximada, puede desplazarse ±unas líneas si el archivo cambió desde que se escribió este plan — ubicar por contenido, no por número):

- `pdf_logic._tabla_vehiculo`: evaluación de horario inline en líneas 361-393.
- `pdf_logic.generar_pdf`: construcción de `rutas` en líneas 704-720; enriquecimiento de mayoristas 722-734; config de tiempo (a mover) en 794-812.
- `historico_logic._extraer_secuencias_historicas` en línea 390; `_historiales_crudos_sucursales` en línea 346.
- `modificacion_logic.guardar_modificacion` en línea 2091; `obtener_modificacion_previa` en línea 2191.
- `mayoristas_logic._insertar_pos_proxima` en línea 74 (ya usada por `pdf_logic.py`, mismo patrón de import).

---

## Task 1: Extraer la evaluación de horario a una función reutilizable

Hoy `_tabla_vehiculo` calcula `entregable_por_tiempo` inline (intenta OSRM real, si falla usa haversine). Fase B necesita el mismo cálculo para decidir candidatas — extraerlo evita duplicar lógica y garantiza que Fase B decide con el mismo criterio que termina impreso en el PDF.

**Files:**
- Create: `logic/tiempo_reubicacion.py`
- Modify: `logic/pdf_logic.py:30-33` (imports), `logic/pdf_logic.py:361-393` (bloque a reemplazar dentro de `_tabla_vehiculo`)
- Test: `tests/test_tiempo_reubicacion.py`

- [ ] **Step 1: Escribir las pruebas de `evaluar_ruta_completa`**

Crear `tests/test_tiempo_reubicacion.py`:

```python
from logic.tiempo_reubicacion import evaluar_ruta_completa


CFG = {
    "activo": True,
    "depot": (18.87, -96.94),
    "velocidad": 35.0,
    "dias": {"martes": {"hora_salida": "07:00", "hora_limite": "20:00"}},
}


def test_evaluar_ruta_completa_usa_osrm_cuando_disponible():
    paradas = [
        {"latitud": 18.90, "longitud": -96.95, "peso_kg": 100, "_tipo": "sucursal"},
        {"latitud": 18.91, "longitud": -96.95, "peso_kg": 100, "_tipo": "sucursal"},
    ]

    def osrm_fake(pts):
        return {"tramos_min": [10.0, 10.0, 10.0]}

    out = evaluar_ruta_completa(paradas, "martes", CFG, osrm_fake)
    assert out[0]["hora_llegada_min"] == 430.0  # 07:00 (420) + 10
    assert out[0]["entregable_por_tiempo"] is True


def test_evaluar_ruta_completa_respaldo_haversine_si_osrm_falla():
    paradas = [{"latitud": 18.90, "longitud": -96.95, "peso_kg": 100, "_tipo": "sucursal"}]

    def osrm_roto(pts):
        raise RuntimeError("sin red")

    out = evaluar_ruta_completa(paradas, "martes", CFG, osrm_roto)
    assert out[0]["entregable_por_tiempo"] is True


def test_evaluar_ruta_completa_sin_funcion_osrm_usa_haversine():
    paradas = [{"latitud": 18.90, "longitud": -96.95, "peso_kg": 100, "_tipo": "sucursal"}]
    out = evaluar_ruta_completa(paradas, "martes", CFG, None)
    assert out[0]["entregable_por_tiempo"] is True


def test_evaluar_ruta_completa_detecta_fuera_de_horario():
    # Cierre a las 08:00 (480 min); con tramos de 100 min entre paradas, la
    # segunda llega bien pasado el cierre.
    cfg = {
        "activo": True, "depot": (18.87, -96.94), "velocidad": 35.0,
        "dias": {"martes": {"hora_salida": "07:00", "hora_limite": "08:00"}},
    }
    paradas = [
        {"latitud": 18.90, "longitud": -96.95, "peso_kg": 100, "_tipo": "sucursal"},
        {"latitud": 19.90, "longitud": -97.95, "peso_kg": 100, "_tipo": "sucursal"},
    ]
    out = evaluar_ruta_completa(paradas, "martes", cfg, None)
    assert out[1]["entregable_por_tiempo"] is False
```

- [ ] **Step 2: Correr las pruebas y verificar que fallan**

Run: `./env/Scripts/python.exe -m pytest tests/test_tiempo_reubicacion.py -q`
Expected: FAIL con `ModuleNotFoundError: No module named 'logic.tiempo_reubicacion'`.

- [ ] **Step 3: Crear el módulo con `evaluar_ruta_completa`**

Crear `logic/tiempo_reubicacion.py`:

```python
"""
logic/tiempo_reubicacion.py

Fase B — tiempo de entrega: reubica automáticamente las paradas que Fase A
marca FUERA DE HORARIO hacia otra ruta con afinidad histórica real, cupo
(≤85 % de utilización) y tiempo. Fase A solo detecta; Fase B mueve.

Continúa docs/superpowers/specs/2026-08-06-tiempo-entrega-faseB-design.md.

Módulo mayormente puro: no importa OSRM ni BD directamente. Quien llama
inyecta `consultar_osrm_fn` (típicamente `logic.asignacion_logic.consultar_osrm`)
para poder evaluar con datos reales cuando hay red, con haversine como
respaldo — mismo criterio que ya usa Fase A. La persistencia (guardar en
`modificaciones_rutas`) queda a cargo de quien llama a
`resolver_fuera_de_horario`, no de este módulo.
"""
import math

from logic.logistica_tiempo import evaluar_llegadas, evaluar_ruta_por_tiempo, hhmm_a_min
from logic.mayoristas_logic import _insertar_pos_proxima

UMBRAL_PCT_DESTINO = 85.0
# Salvaguarda anti-bucle: tope de movimientos por ruta origen en una sola
# resolución (una ruta real rara vez tiene más de un puñado de paradas
# FUERA DE HORARIO).
MAX_MOVIMIENTOS_POR_RUTA = 20


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _normalizar_veh(s) -> str:
    """Mayúsculas y sin espacios, para comparar nombres de vehículo entre el
    histórico y la ruta actual sin caer en el bug ya confirmado del proyecto
    ('F350_2' != 'F 350_2', ver MIGRACION_STATUS.md)."""
    return str(s or "").strip().upper().replace(" ", "")


def evaluar_ruta_completa(paradas: list, dia: str, cfg_tiempo: dict,
                          consultar_osrm_fn=None) -> list:
    """
    Evalúa la hora de llegada a cada parada de una ruta YA ORDENADA, contra
    el horario configurado de `dia`. Intenta tramos reales vía
    `consultar_osrm_fn` primero; si no hay función, falla, o no trae
    'tramos_min', usa haversine (evaluar_ruta_por_tiempo) — mismo criterio
    que usaba `pdf_logic._tabla_vehiculo` en Fase A, ahora factorizado para
    que Fase B decida con el mismo criterio que termina impreso.

    paradas: en orden, dicts con latitud/longitud/peso_kg y _tipo
             ('sucursal'|'mayorista') o es_mayorista (bool).
    cfg_tiempo: {'depot': (lat,lon), 'velocidad': kmh,
                 'dias': {dia: {'hora_salida': 'HH:MM', 'hora_limite': 'HH:MM'}}}.
    Retorna copias de `paradas` con 'hora_llegada_min' y
    'entregable_por_tiempo' (ver logistica_tiempo.evaluar_llegadas).
    """
    dcfg  = cfg_tiempo.get("dias", {}).get(dia, {})
    h_sal = hhmm_a_min(dcfg.get("hora_salida"), 420)
    h_lim = hhmm_a_min(dcfg.get("hora_limite"), 1080)
    depot = cfg_tiempo.get("depot")

    paradas_t = [{
        "latitud": p.get("latitud"), "longitud": p.get("longitud"),
        "peso_kg": p.get("peso_kg", 0),
        "es_mayorista": p.get("es_mayorista", p.get("_tipo") == "mayorista"),
    } for p in paradas]

    tramos = None
    if consultar_osrm_fn is not None:
        try:
            pts, prev = [depot], depot
            for p in paradas:
                la, lo = p.get("latitud"), p.get("longitud")
                if la is not None and lo is not None:
                    prev = (float(la), float(lo))
                pts.append(prev)
            pts.append(depot)
            r = consultar_osrm_fn(pts)
            if "error" not in r and r.get("tramos_min"):
                tramos = r["tramos_min"]
        except Exception:
            tramos = None

    return (evaluar_llegadas(paradas_t, tramos, h_sal, h_lim) if tramos
            else evaluar_ruta_por_tiempo(paradas_t, depot, h_sal, h_lim,
                                         cfg_tiempo.get("velocidad", 35.0)))
```

- [ ] **Step 4: Correr las pruebas y verificar que pasan**

Run: `./env/Scripts/python.exe -m pytest tests/test_tiempo_reubicacion.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Refactorizar `pdf_logic._tabla_vehiculo` para reusar la función**

En `logic/pdf_logic.py`, reemplazar el import de `logic.logistica_tiempo` (línea 30-32):

```python
from logic.logistica_tiempo import (
    TIEMPO_ENTREGA_ESTRICTO, evaluar_ruta_por_tiempo, evaluar_llegadas, hhmm_a_min,
)
```

por:

```python
from logic.logistica_tiempo import TIEMPO_ENTREGA_ESTRICTO
from logic.tiempo_reubicacion import evaluar_ruta_completa
```

Luego, dentro de `_tabla_vehiculo`, reemplazar el bloque completo (líneas 361-393, desde `# Fase A — tiempo de entrega...` hasta el `for p, e in zip(paradas, evals): p["_entregable"] = e["entregable_por_tiempo"]`):

```python
        # Fase A — tiempo de entrega: sobre las paradas ordenadas (antes de
        # agrupar), marcar las que no se alcanzan a entregar antes del cierre.
        if cfg_tiempo and cfg_tiempo.get("activo"):
            dcfg  = cfg_tiempo.get("dias", {}).get(dia, {})
            h_sal = hhmm_a_min(dcfg.get("hora_salida"), 420)
            h_lim = hhmm_a_min(dcfg.get("hora_limite"), 1080)
            depot = cfg_tiempo.get("depot")
            paradas_t = [{
                "latitud": p.get("latitud"), "longitud": p.get("longitud"),
                "peso_kg": p.get("peso_kg", 0),
                "es_mayorista": p["_tipo"] == "mayorista",
            } for p in paradas]
            # Traslado real por OSRM (cacheado): matriz→p1→…→pn→matriz. Una parada
            # sin coords repite el punto previo (tramo 0). Si OSRM falla, haversine.
            tramos = None
            try:
                pts, prev = [depot], depot
                for p in paradas:
                    la, lo = p.get("latitud"), p.get("longitud")
                    if la is not None and lo is not None:
                        prev = (float(la), float(lo))
                    pts.append(prev)
                pts.append(depot)
                r = consultar_osrm(pts)
                if "error" not in r and r.get("tramos_min"):
                    tramos = r["tramos_min"]
            except Exception:
                tramos = None
            evals = (evaluar_llegadas(paradas_t, tramos, h_sal, h_lim) if tramos
                     else evaluar_ruta_por_tiempo(paradas_t, depot, h_sal, h_lim,
                                                  cfg_tiempo.get("velocidad", 35.0)))
            for p, e in zip(paradas, evals):
                p["_entregable"] = e["entregable_por_tiempo"]
```

por:

```python
        # Fase A — tiempo de entrega: sobre las paradas ordenadas (antes de
        # agrupar), marcar las que no se alcanzan a entregar antes del cierre.
        # (Fase B, en generar_pdf, ya intentó reubicar las que no cabían.)
        if cfg_tiempo and cfg_tiempo.get("activo"):
            evals = evaluar_ruta_completa(paradas, dia, cfg_tiempo, consultar_osrm)
            for p, e in zip(paradas, evals):
                p["_entregable"] = e["entregable_por_tiempo"]
```

`consultar_osrm` ya está importado en `pdf_logic.py` (línea 33, `from logic.asignacion_logic import consultar_osrm`) — no cambia.

- [ ] **Step 6: Verificar sintaxis de `pdf_logic.py`**

Run: `./env/Scripts/python.exe -c "import ast; ast.parse(open('logic/pdf_logic.py',encoding='utf-8').read()); print('PY OK')"`
Expected: `PY OK`

- [ ] **Step 7: Correr toda la suite para confirmar que no hay regresión**

Run: `./env/Scripts/python.exe -m pytest tests/ -q`
Expected: PASS — todas las pruebas previas siguen en verde (el refactor no cambia comportamiento, solo lo reubica).

- [ ] **Step 8: Commit**

```bash
git add logic/tiempo_reubicacion.py tests/test_tiempo_reubicacion.py logic/pdf_logic.py
git commit -m "refactor(tiempo): extraer evaluar_ruta_completa (Fase A) para reusar en Fase B"
```

---

## Task 2: Exponer la afinidad histórica real (`historico_logic.afinidad_historica_por_sucursal`)

**Files:**
- Modify: `logic/historico_logic.py` (agregar función pública después de `_extraer_secuencias_historicas`, ~línea 412)
- Test: `tests/test_tiempo_reubicacion.py`

- [ ] **Step 1: Escribir la prueba**

Agregar a `tests/test_tiempo_reubicacion.py`:

```python
import logic.historico_logic as historico_logic


def test_afinidad_historica_por_sucursal_compone_helpers(monkeypatch):
    historiales_falsos = [{"filas": [
        {"id_sucursal": 42, "vehiculo": "F 350_1", "dia_semana": "martes", "secuencia_visita": 1},
        {"id_sucursal": 42, "vehiculo": "F 350_1", "dia_semana": "martes", "secuencia_visita": 3},
    ]}]
    monkeypatch.setattr(historico_logic, "_historiales_crudos_sucursales", lambda: historiales_falsos)
    out = historico_logic.afinidad_historica_por_sucursal()
    assert out[42][("F 350_1", "MARTES")] == 2  # mediana de 1 y 3
```

- [ ] **Step 2: Correr y verificar que falla**

Run: `./env/Scripts/python.exe -m pytest tests/test_tiempo_reubicacion.py::test_afinidad_historica_por_sucursal_compone_helpers -q`
Expected: FAIL con `AttributeError: module 'logic.historico_logic' has no attribute 'afinidad_historica_por_sucursal'`.

- [ ] **Step 3: Agregar la función**

En `logic/historico_logic.py`, inmediatamente después del `return result` de `_extraer_secuencias_historicas` (línea ~412, antes del comentario `# Umbrales para decidir si una ruta histórica...`), agregar:

```python
def afinidad_historica_por_sucursal() -> dict:
    """
    {num_tienda: {(vehiculo, DIA): secuencia_mediana}} — con qué vehículo/día
    viajó cada sucursal en las 9 semanas canónicas confirmadas de
    `rutas_historicas`. Wrapper público de dos funciones ya usadas por
    `generar_rutas_vrp_afinidad`, para que Fase B
    (`logic/tiempo_reubicacion.py`, fuera de este módulo) consulte la
    afinidad histórica real sin duplicar la lectura ni tocar las funciones
    privadas existentes.
    """
    return _extraer_secuencias_historicas(_historiales_crudos_sucursales())
```

- [ ] **Step 4: Correr y verificar que pasa**

Run: `./env/Scripts/python.exe -m pytest tests/test_tiempo_reubicacion.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add logic/historico_logic.py tests/test_tiempo_reubicacion.py
git commit -m "feat(tiempo): exponer afinidad_historica_por_sucursal (para Fase B)"
```

---

## Task 3: Helpers puros — normalización, % utilización, cupo por peso

**Files:**
- Modify: `logic/tiempo_reubicacion.py`
- Test: `tests/test_tiempo_reubicacion.py`

- [ ] **Step 1: Escribir las pruebas**

Agregar a `tests/test_tiempo_reubicacion.py`:

```python
from logic.tiempo_reubicacion import _normalizar_veh, _pct_utilizacion, _cabe_por_peso


def test_normalizar_veh_ignora_espacios_y_mayusculas():
    assert _normalizar_veh("F 350_2") == _normalizar_veh("F350_2") == "F350_2"
    assert _normalizar_veh(None) == ""


def test_pct_utilizacion():
    assert _pct_utilizacion(1750, 3.5) == 50.0
    assert _pct_utilizacion(100, 0) == 0.0  # sin capacidad registrada -> 0, no división por cero
    assert _pct_utilizacion(100, None) == 0.0


def test_cabe_por_peso_respeta_umbral():
    ruta = {"peso_kg": 2000, "capacidad_ton": 2.5}  # 80% ya usado
    assert _cabe_por_peso(ruta, 100, 85.0) is True    # 2100/2500=84% <= 85%
    assert _cabe_por_peso(ruta, 200, 85.0) is False   # 2200/2500=88% > 85%
```

- [ ] **Step 2: Correr y verificar que fallan**

Run: `./env/Scripts/python.exe -m pytest tests/test_tiempo_reubicacion.py -q`
Expected: FAIL — `ImportError: cannot import name '_normalizar_veh'` (ya existe `_normalizar_veh` del Task 1; fallará en `_pct_utilizacion`/`_cabe_por_peso`, que aún no existen).

- [ ] **Step 3: Implementar `_pct_utilizacion` y `_cabe_por_peso`**

Agregar al final de `logic/tiempo_reubicacion.py`:

```python
def _pct_utilizacion(peso_kg: float, capacidad_ton) -> float:
    cap_kg = float(capacidad_ton or 0) * 1000
    return round(float(peso_kg) / cap_kg * 100, 1) if cap_kg > 0 else 0.0


def _cabe_por_peso(ruta: dict, peso_extra: float, umbral_pct: float) -> bool:
    """True si, tras sumar `peso_extra` al peso ya cargado de `ruta`, la
    utilización resultante no supera `umbral_pct`."""
    peso_total = float(ruta.get("peso_kg", 0)) + float(peso_extra)
    return _pct_utilizacion(peso_total, ruta.get("capacidad_ton")) <= umbral_pct
```

- [ ] **Step 4: Correr y verificar que pasan**

Run: `./env/Scripts/python.exe -m pytest tests/test_tiempo_reubicacion.py -q`
Expected: PASS (8 passed).

- [ ] **Step 5: Commit**

```bash
git add logic/tiempo_reubicacion.py tests/test_tiempo_reubicacion.py
git commit -m "feat(tiempo): helpers de cupo por peso para Fase B"
```

---

## Task 4: Insertar/quitar una parada de una ruta (posición geográfica + recálculo)

**Files:**
- Modify: `logic/tiempo_reubicacion.py`
- Test: `tests/test_tiempo_reubicacion.py`

- [ ] **Step 1: Escribir las pruebas**

Agregar a `tests/test_tiempo_reubicacion.py`:

```python
from logic.tiempo_reubicacion import (
    _paradas_ordenadas, _insertar_en_ruta, _quitar_de_ruta, _recalcular_peso_ruta,
)


def _ruta_ejemplo():
    return {
        "id": "R1", "dia": "martes", "vehiculo_abrev": "F 350_1",
        "capacidad_ton": 3.5, "peso_kg": 1000, "pct_utilizacion": 28.6,
        "sucursales": [
            {"num_tienda": 1, "nombre": "A", "orden": 1, "peso_kg": 500,
             "latitud": 18.90, "longitud": -96.95},
            {"num_tienda": 2, "nombre": "B", "orden": 2, "peso_kg": 500,
             "latitud": 18.91, "longitud": -96.95},
        ],
        "mayoristas": [],
    }


def test_paradas_ordenadas_combina_e_intercala_por_orden():
    ruta = _ruta_ejemplo()
    ruta["mayoristas"] = [{"id_cliente": 9, "documento": "BB1", "orden": 3,
                            "peso_kg": 50, "latitud": 18.92, "longitud": -96.95}]
    combinado = _paradas_ordenadas(ruta)
    assert [p.get("_tipo") for p in combinado] == ["sucursal", "sucursal", "mayorista"]


def test_insertar_en_ruta_posicion_geografica_y_reindexa():
    ruta = _ruta_ejemplo()
    nueva_sucursal = {"num_tienda": 3, "nombre": "C", "peso_kg": 300,
                       "latitud": 18.902, "longitud": -96.95}  # mas cerca de A(1) que de B(2), sin ambiguedad de float
    _insertar_en_ruta(ruta, nueva_sucursal, "sucursal")
    ordenes = [(s["num_tienda"], s["orden"]) for s in ruta["sucursales"]]
    assert ordenes == [(1, 1), (3, 2), (2, 3)]


def test_quitar_de_ruta_reindexa_lo_restante():
    ruta = _ruta_ejemplo()
    _quitar_de_ruta(ruta, {"num_tienda": 1}, "sucursal")
    assert [s["num_tienda"] for s in ruta["sucursales"]] == [2]
    assert ruta["sucursales"][0]["orden"] == 1


def test_recalcular_peso_ruta_suma_sucursales_y_mayoristas():
    ruta = _ruta_ejemplo()
    ruta["mayoristas"] = [{"id_cliente": 9, "documento": "BB1", "orden": 3,
                            "peso_kg": 50, "latitud": 18.92, "longitud": -96.95}]
    _recalcular_peso_ruta(ruta)
    assert ruta["peso_kg"] == 1050.0
    assert ruta["pct_utilizacion"] == _pct_utilizacion(1050.0, 3.5)
```

`_pct_utilizacion` (usado en la última aserción) ya quedó importado en el
Task 3, Step 1 — no hace falta repetir el import.

- [ ] **Step 2: Correr y verificar que fallan**

Run: `./env/Scripts/python.exe -m pytest tests/test_tiempo_reubicacion.py -q`
Expected: FAIL — `ImportError` (`_paradas_ordenadas`, `_insertar_en_ruta`, `_quitar_de_ruta`, `_recalcular_peso_ruta` no existen).

- [ ] **Step 3: Implementar**

Agregar al final de `logic/tiempo_reubicacion.py`:

```python
def _paradas_ordenadas(ruta: dict) -> list:
    """Sucursales + mayoristas de `ruta`, cada uno con `_tipo`, ordenados por
    `orden` (mismo criterio que pdf_logic._tabla_vehiculo)."""
    sucs = [dict(p, _tipo="sucursal")  for p in ruta.get("sucursales", [])]
    mays = [dict(p, _tipo="mayorista") for p in ruta.get("mayoristas",  [])]
    return sorted(sucs + mays, key=lambda p: p.get("orden") if p.get("orden") is not None else 9999)


def _reindexar(ruta: dict, combinado: list) -> None:
    """Reescribe ruta['sucursales']/['mayoristas'] a partir de `combinado`
    (lista con '_tipo'), renumerando 'orden' 1..N en el orden dado."""
    sucursales, mayoristas = [], []
    for i, p in enumerate(combinado, start=1):
        q = dict(p)
        q["orden"] = i
        tipo = q.pop("_tipo", "sucursal")
        (mayoristas if tipo == "mayorista" else sucursales).append(q)
    ruta["sucursales"] = sucursales
    ruta["mayoristas"] = mayoristas


def _insertar_en_ruta(ruta: dict, parada: dict, tipo: str) -> None:
    """Inserta `parada` en `ruta`, en la posición geográficamente más
    cercana a sus vecinos actuales (mismo criterio que ya usa el proyecto
    para insertar mayoristas por proximidad)."""
    combinado = _paradas_ordenadas(ruta)
    idx = _insertar_pos_proxima(combinado, parada)
    nueva = dict(parada)
    nueva["_tipo"] = tipo
    combinado.insert(idx, nueva)
    _reindexar(ruta, combinado)


def _misma_parada(p: dict, parada: dict, tipo: str) -> bool:
    if p.get("_tipo") != tipo:
        return False
    if tipo == "mayorista":
        return ((p.get("id_cliente"), p.get("documento"))
                == (parada.get("id_cliente"), parada.get("documento")))
    return p.get("num_tienda") == parada.get("num_tienda")


def _quitar_de_ruta(ruta: dict, parada: dict, tipo: str) -> None:
    """Elimina `parada` de `ruta` (por num_tienda si es sucursal, por
    id_cliente/documento si es mayorista) y renumera el orden restante."""
    combinado = [p for p in _paradas_ordenadas(ruta) if not _misma_parada(p, parada, tipo)]
    _reindexar(ruta, combinado)


def _recalcular_peso_ruta(ruta: dict) -> None:
    """Recalcula peso_kg y pct_utilizacion de `ruta` desde sus paradas
    actuales — misma fórmula que agregar/quitar_sucursal_a_asignacion en
    modificacion_logic.py."""
    peso = sum(float(p.get("peso_kg") or 0) for p in ruta.get("sucursales", []))
    peso += sum(float(p.get("peso_kg") or 0) for p in ruta.get("mayoristas", []))
    ruta["peso_kg"] = peso
    ruta["pct_utilizacion"] = _pct_utilizacion(peso, ruta.get("capacidad_ton"))
```

- [ ] **Step 4: Correr y verificar que pasan**

Run: `./env/Scripts/python.exe -m pytest tests/test_tiempo_reubicacion.py -q`
Expected: PASS (12 passed).

- [ ] **Step 5: Commit**

```bash
git add logic/tiempo_reubicacion.py tests/test_tiempo_reubicacion.py
git commit -m "feat(tiempo): insertar/quitar parada de una ruta para Fase B"
```

---

## Task 5: Selección de ruta destino por afinidad histórica

**Files:**
- Modify: `logic/tiempo_reubicacion.py`
- Test: `tests/test_tiempo_reubicacion.py`

- [ ] **Step 1: Escribir las pruebas**

Agregar a `tests/test_tiempo_reubicacion.py`:

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


def _ruta_destino(id_, dia="martes", peso_kg=0, capacidad_ton=3.5):
    # El peso de "Vecina" debe IGUALAR peso_kg (no un valor fijo aparte):
    # _menos_mala compara vía _simular_insercion -> _recalcular_peso_ruta,
    # que sobreescribe peso_kg sumando las paradas reales. Si "Vecina" fuera
    # un peso fijo distinto del peso_kg declarado, la simulacion perderia la
    # diferencia entre rutas y el test de _menos_mala compararia valores
    # identicos sin importar el peso_kg pedido.
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

- [ ] **Step 2: Correr y verificar que fallan**

Run: `./env/Scripts/python.exe -m pytest tests/test_tiempo_reubicacion.py -q`
Expected: FAIL — `ImportError` (`_clave_afinidad_para`, `_candidatas_con_afinidad`, `_mejor_candidata`, `_menos_mala` no existen).

- [ ] **Step 3: Implementar**

Agregar al final de `logic/tiempo_reubicacion.py` (necesita `import copy` al inicio del archivo, junto a `import math`):

```python
def _clave_afinidad_para(parada: dict, tipo: str, ruta: dict, afinidad: dict) -> "int | None":
    """
    Llave de búsqueda en `afinidad` para `parada`:
    - Sucursal: su propio num_tienda.
    - Mayorista: el num_tienda de la sucursal de la MISMA ruta geográficamente
      más cercana que sí tenga afinidad histórica registrada — ancla
      conceptualmente igual a como enganche_zona ancla mayoristas a una
      sucursal del grupo destino, sin activar ese motor.
    None si no hay coordenadas o no se encuentra ancla.
    """
    if tipo == "sucursal":
        nt = parada.get("num_tienda")
        return int(nt) if nt is not None else None

    lat, lon = parada.get("latitud"), parada.get("longitud")
    if lat is None or lon is None:
        return None
    mejor_sid, mejor_dist = None, float("inf")
    for s in ruta.get("sucursales", []):
        nt = s.get("num_tienda")
        if nt is None or int(nt) not in afinidad:
            continue
        la, lo = s.get("latitud"), s.get("longitud")
        if la is None or lo is None:
            continue
        d = _haversine_km(float(lat), float(lon), float(la), float(lo))
        if d < mejor_dist:
            mejor_dist, mejor_sid = d, int(nt)
    return mejor_sid


def _candidatas_con_afinidad(num_tienda, rutas: list, afinidad: dict, ruta_origen_id,
                             mismo_dia: bool, dia_origen: str) -> list:
    """Rutas de `rutas` (excluye la de origen) con las que `num_tienda`
    tiene afinidad histórica real. `mismo_dia=True` sólo día == dia_origen;
    False sólo días distintos. Orden estable por id (determinismo)."""
    if num_tienda is None:
        return []
    prefs = afinidad.get(int(num_tienda), {})
    if not prefs:
        return []
    vehs_dias = {(_normalizar_veh(v), d) for (v, d) in prefs}
    candidatas = []
    for r in rutas:
        if r.get("id") == ruta_origen_id:
            continue
        es_mismo_dia = (r.get("dia", "") == dia_origen)
        if es_mismo_dia != mismo_dia:
            continue
        clave = (_normalizar_veh(r.get("vehiculo_abrev")), str(r.get("dia", "")).upper())
        if clave in vehs_dias:
            candidatas.append(r)
    return sorted(candidatas, key=lambda r: str(r.get("id", "")))


def _simular_insercion(ruta: dict, parada: dict, tipo: str) -> dict:
    """Copia profunda de `ruta` con `parada` insertada y peso recalculado —
    para evaluar el efecto de un movimiento sin mutar la ruta real todavía."""
    ruta_sim = copy.deepcopy(ruta)
    _insertar_en_ruta(ruta_sim, parada, tipo)
    _recalcular_peso_ruta(ruta_sim)
    return ruta_sim


def _sin_fuera_de_horario(ruta: dict, cfg_tiempo: dict, consultar_osrm_fn) -> bool:
    combinado = _paradas_ordenadas(ruta)
    if not combinado:
        return True
    evals = evaluar_ruta_completa(combinado, ruta.get("dia", ""), cfg_tiempo, consultar_osrm_fn)
    return all(e["entregable_por_tiempo"] for e in evals)


def _mejor_candidata(candidatas: list, parada: dict, tipo: str, peso_extra: float,
                     cfg_tiempo: dict, consultar_osrm_fn, umbral_pct: float) -> "dict | None":
    """Primera candidata (orden estable) que, tras insertar la parada, queda
    ≤ umbral_pct de utilización Y no genera un nuevo FUERA DE HORARIO."""
    for ruta in candidatas:
        if not _cabe_por_peso(ruta, peso_extra, umbral_pct):
            continue
        ruta_sim = _simular_insercion(ruta, parada, tipo)
        if _sin_fuera_de_horario(ruta_sim, cfg_tiempo, consultar_osrm_fn):
            return ruta
    return None


def _menos_mala(candidatas: list, parada: dict, tipo: str,
                cfg_tiempo: dict, consultar_osrm_fn) -> "dict | None":
    """Último recurso: entre TODAS las candidatas con afinidad (aunque no
    cumplan 85 %/tiempo), la que quede con menor % de utilización tras
    insertar la parada. Nunca sale de `candidatas` (siempre con afinidad)."""
    mejor, mejor_pct = None, float("inf")
    for ruta in candidatas:
        ruta_sim = _simular_insercion(ruta, parada, tipo)
        if ruta_sim["pct_utilizacion"] < mejor_pct:
            mejor, mejor_pct = ruta, ruta_sim["pct_utilizacion"]
    return mejor
```

- [ ] **Step 4: Correr y verificar que pasan**

Run: `./env/Scripts/python.exe -m pytest tests/test_tiempo_reubicacion.py -q`
Expected: PASS (20 passed).

- [ ] **Step 5: Commit**

```bash
git add logic/tiempo_reubicacion.py tests/test_tiempo_reubicacion.py
git commit -m "feat(tiempo): seleccion de ruta destino por afinidad historica"
```

---

## Task 6: Orquestador `resolver_fuera_de_horario`

**Files:**
- Modify: `logic/tiempo_reubicacion.py`
- Test: `tests/test_tiempo_reubicacion.py`

- [ ] **Step 1: Escribir las pruebas**

Agregar a `tests/test_tiempo_reubicacion.py`:

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


def test_resolver_fuera_de_horario_mueve_a_ruta_con_afinidad_y_cupo():
    # 42 está lejos del depot. La ruta ORIGEN visita antes una sucursal
    # cercana (con su descarga) y llega tarde a 42. La ruta DESTINO (vacía,
    # mismo día, con afinidad histórica para 42) llega a tiempo yendo directo.
    afinidad = {42: {("DEST", "MARTES"): 1}}
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
    destino = {
        "id": "DEST", "dia": "martes", "vehiculo_abrev": "DEST",
        "capacidad_ton": 3.5, "peso_kg": 0, "pct_utilizacion": 0.0,
        "sucursales": [], "mayoristas": [],
    }
    rutas = [origen, destino]

    movio = resolver_fuera_de_horario(rutas, _cfg_cierre_08_30(), afinidad,
                                      consultar_osrm_fn=None)

    assert movio is True
    assert [s["num_tienda"] for s in origen["sucursales"]] == [1]
    assert 42 in [s["num_tienda"] for s in destino["sucursales"]]
    assert destino["peso_kg"] == 100.0


def test_resolver_fuera_de_horario_sin_historial_no_mueve_nada():
    ruta = {
        "id": "ORIGEN", "dia": "martes", "vehiculo_abrev": "ORIGEN",
        "capacidad_ton": 3.5, "peso_kg": 0, "pct_utilizacion": 0.0,
        "sucursales": [
            {"num_tienda": 1, "nombre": "Cercana", "orden": 1, "peso_kg": 100,
             "latitud": 0.05, "longitud": 0.05},
            {"num_tienda": 999, "nombre": "Sin historial", "orden": 2, "peso_kg": 100,
             "latitud": 0.5, "longitud": 0.5},
        ],
        "mayoristas": [],
    }
    movio = resolver_fuera_de_horario([ruta], _cfg_cierre_08_30(), {}, consultar_osrm_fn=None)
    assert movio is False
    assert [s["num_tienda"] for s in ruta["sucursales"]] == [1, 999]


def test_resolver_fuera_de_horario_interruptor_apagado_no_hace_nada():
    ruta = {"id": "R1", "dia": "martes", "sucursales": [], "mayoristas": []}
    assert resolver_fuera_de_horario([ruta], {"activo": False}, {}) is False


def test_resolver_fuera_de_horario_mismo_dia_falla_cae_a_otro_dia():
    # SAMEDIA (mismo dia que ORIGEN) tiene afinidad para 42 pero ya esta casi
    # llena (3400/3500=97%): tras sumar los 100kg de 42 quedaria en 100%,
    # por lo que _mejor_candidata la descarta por peso sin llegar a evaluar
    # tiempo. OTRODIA (jueves) tiene afinidad para 42, esta vacia, y llega a
    # tiempo yendo directo desde el depot (mismo calculo que el test anterior:
    # depot->(0.5,0.5) = 78.63 min, llegada 498.63 <= 510).
    afinidad = {42: {("SAMEDIA", "MARTES"): 1, ("OTRODIA", "JUEVES"): 1}}
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
    samedia = {
        "id": "SAMEDIA", "dia": "martes", "vehiculo_abrev": "SAMEDIA",
        "capacidad_ton": 3.5, "peso_kg": 3400, "pct_utilizacion": 97.1,
        "sucursales": [
            {"num_tienda": 50, "nombre": "Llena", "orden": 1, "peso_kg": 3400,
             "latitud": 0.05, "longitud": 0.05},
        ],
        "mayoristas": [],
    }
    otrodia = {
        "id": "OTRODIA", "dia": "jueves", "vehiculo_abrev": "OTRODIA",
        "capacidad_ton": 3.5, "peso_kg": 0, "pct_utilizacion": 0.0,
        "sucursales": [], "mayoristas": [],
    }
    rutas = [origen, samedia, otrodia]

    movio = resolver_fuera_de_horario(rutas, _cfg_cierre_08_30(), afinidad,
                                      consultar_osrm_fn=None)

    assert movio is True
    assert [s["num_tienda"] for s in origen["sucursales"]] == [1]
    assert [s["num_tienda"] for s in samedia["sucursales"]] == [50]  # sin tocar, descartada por cupo
    assert 42 in [s["num_tienda"] for s in otrodia["sucursales"]]


def test_resolver_fuera_de_horario_procesa_varias_paradas_en_la_misma_ruta():
    # ORIGEN tiene DOS paradas fuera de horario (42 y 43). Cada una tiene
    # afinidad con una ruta destino distinta, vacia, del mismo dia. Deben
    # resolverse una por una (re-evaluando ORIGEN tras cada movimiento), sin
    # detenerse en la primera.
    afinidad = {
        42: {("DEST1", "MARTES"): 1},
        43: {("DEST2", "MARTES"): 1},
    }
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
    dest1 = {
        "id": "DEST1", "dia": "martes", "vehiculo_abrev": "DEST1",
        "capacidad_ton": 3.5, "peso_kg": 0, "pct_utilizacion": 0.0,
        "sucursales": [], "mayoristas": [],
    }
    dest2 = {
        "id": "DEST2", "dia": "martes", "vehiculo_abrev": "DEST2",
        "capacidad_ton": 3.5, "peso_kg": 0, "pct_utilizacion": 0.0,
        "sucursales": [], "mayoristas": [],
    }
    rutas = [origen, dest1, dest2]

    movio = resolver_fuera_de_horario(rutas, _cfg_cierre_08_30(), afinidad,
                                      consultar_osrm_fn=None)

    assert movio is True
    assert [s["num_tienda"] for s in origen["sucursales"]] == [1]
    assert [s["num_tienda"] for s in dest1["sucursales"]] == [42]
    assert [s["num_tienda"] for s in dest2["sucursales"]] == [43]
```

**Nota (encontrada en la revisión de calidad):** las dos pruebas anteriores
se agregaron después de la revisión inicial de Task 6 para cerrar dos huecos
de cobertura reales en la función de mayor riesgo del plan: la cascada
mismo-día→otro-día nunca llegaba a probar el segundo salto, y ninguna prueba
verificaba que una ruta con VARIAS paradas fuera de horario se resuelve una
por una. Las coordenadas fueron verificadas a mano (haversine real) antes de
confiarlas — igual que en las Tasks 4-5, donde aparecieron errores de
precisión/fixture reales.

- [ ] **Step 2: Correr y verificar que fallan**

Run: `./env/Scripts/python.exe -m pytest tests/test_tiempo_reubicacion.py -q`
Expected: FAIL — `ImportError: cannot import name 'resolver_fuera_de_horario'`.

- [ ] **Step 3: Implementar**

Agregar al final de `logic/tiempo_reubicacion.py`:

```python
def resolver_fuera_de_horario(rutas: list, cfg_tiempo: dict, afinidad: dict,
                              umbral_pct: float = UMBRAL_PCT_DESTINO,
                              consultar_osrm_fn=None) -> bool:
    """
    Reubica, mutando `rutas` in-place, toda parada FUERA DE HORARIO hacia
    otra ruta con afinidad histórica real, cupo (<=umbral_pct) y tiempo.
    Procesa las paradas de cada ruta en orden de secuencia, re-evaluando la
    ruta origen tras cada movimiento (quitar una parada solo puede adelantar
    la llegada de las que quedan). Sin destino con afinidad -> se queda
    marcada, igual que en Fase A. Devuelve True si movió algo.

    rutas: [{id, dia, vehiculo_abrev, capacidad_ton, peso_kg,
             pct_utilizacion, sucursales:[...], mayoristas:[...]}, ...] —
           misma forma que arma pdf_logic.generar_pdf().
    afinidad: historico_logic.afinidad_historica_por_sucursal().
    """
    if not (cfg_tiempo and cfg_tiempo.get("activo")):
        return False

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
            peso_extra = float(parada.get("peso_kg") or 0)
            clave = _clave_afinidad_para(parada, tipo, ruta, afinidad)

            candidatas_mismo_dia = _candidatas_con_afinidad(
                clave, rutas, afinidad, ruta.get("id"), True, ruta.get("dia", ""))
            destino = _mejor_candidata(candidatas_mismo_dia, parada, tipo, peso_extra,
                                       cfg_tiempo, consultar_osrm_fn, umbral_pct)

            candidatas_otro_dia = []
            if destino is None:
                candidatas_otro_dia = _candidatas_con_afinidad(
                    clave, rutas, afinidad, ruta.get("id"), False, ruta.get("dia", ""))
                destino = _mejor_candidata(candidatas_otro_dia, parada, tipo, peso_extra,
                                           cfg_tiempo, consultar_osrm_fn, umbral_pct)

            if destino is None:
                destino = _menos_mala(candidatas_mismo_dia + candidatas_otro_dia, parada, tipo,
                                      cfg_tiempo, consultar_osrm_fn)

            if destino is None:
                # Sin afinidad histórica (sucursal nueva, o mayorista sin
                # ancla): se queda FUERA DE HORARIO, igual que Fase A.
                break

            _quitar_de_ruta(ruta, parada, tipo)
            _recalcular_peso_ruta(ruta)
            _insertar_en_ruta(destino, parada, tipo)
            _recalcular_peso_ruta(destino)
            cambio = True

    return cambio
```

- [ ] **Step 4: Correr y verificar que pasan**

Run: `./env/Scripts/python.exe -m pytest tests/test_tiempo_reubicacion.py -q`
Expected: PASS (23 passed).

- [ ] **Step 5: Commit**

```bash
git add logic/tiempo_reubicacion.py tests/test_tiempo_reubicacion.py
git commit -m "feat(tiempo): resolver_fuera_de_horario — orquestador de Fase B"
```

---

## Task 7: Enganchar Fase B en `generar_pdf()` con persistencia real

**Files:**
- Modify: `logic/pdf_logic.py`

- [ ] **Step 1: Actualizar imports**

En `logic/pdf_logic.py`, reemplazar la línea de import de `modificacion_logic` (línea 28):

```python
from logic.modificacion_logic import obtener_modificacion_previa
```

por:

```python
from logic.modificacion_logic import obtener_modificacion_previa, guardar_modificacion
from logic.historico_logic import afinidad_historica_por_sucursal
```

Y reemplazar la línea agregada por el Task 1 (Step 5):

```python
from logic.tiempo_reubicacion import evaluar_ruta_completa
```

por:

```python
from logic.tiempo_reubicacion import evaluar_ruta_completa, resolver_fuera_de_horario, _recalcular_peso_ruta
```

**Nota importante (encontrada en la revisión de calidad del Task 5):** el paso 3 de
`generar_pdf` ("Enriquecer peso_kg de mayoristas desde extraccion") parchea
`m["peso_kg"]` de cada mayorista **en el dict de la parada**, pero nunca
vuelve a sumar `ruta["peso_kg"]`/`ruta["pct_utilizacion"]` a nivel de ruta
después de ese parche — son campos que se calcularon ANTES de que los
mayoristas tuvieran su peso real (a menudo llegan en 0 y se corrigen recién
en ese paso). Si Fase B evalúa cupo (`_cabe_por_peso`, dentro de
`_mejor_candidata`) contra ese `ruta["peso_kg"]` desactualizado, el tope de
85 % podría violarse en silencio. Por eso el Step 3 de abajo agrega un
recálculo explícito con `_recalcular_peso_ruta` sobre TODAS las rutas,
después del enriquecimiento de mayoristas y antes de llamar a
`resolver_fuera_de_horario` — ver el bloque de código del Step 3.

- [ ] **Step 2: Rastrear `mod_doc` fuera del `if`**

En `generar_pdf`, reemplazar:

```python
    # ── 0. Rutas inyectadas (previsualización sin persistir) ──────
    rutas: list = list(rutas_inyectadas) if rutas_inyectadas else []

    # ── 1. Intentar leer desde modificaciones_rutas ───────────────
    if not rutas:
        mod_doc = obtener_modificacion_previa(oid)
        rutas = mod_doc.get("rutas_confirmadas", []) if mod_doc else []
```

por:

```python
    # ── 0. Rutas inyectadas (previsualización sin persistir) ──────
    rutas: list = list(rutas_inyectadas) if rutas_inyectadas else []
    mod_doc: "dict | None" = None

    # ── 1. Intentar leer desde modificaciones_rutas ───────────────
    if not rutas:
        mod_doc = obtener_modificacion_previa(oid)
        rutas = mod_doc.get("rutas_confirmadas", []) if mod_doc else []
```

- [ ] **Step 3: Mover la construcción de `cfg_tiempo` antes de `vol_map` y agregar el enganche de Fase B**

Reemplazar:

```python
    _filtrar_mayoristas_con_pedidos(rutas)

    vol_map = _volumenes_suc(db, oid)
```

por:

```python
    _filtrar_mayoristas_con_pedidos(rutas)

    # Recalcular peso_kg/pct_utilizacion a nivel de ruta: el paso anterior
    # (enriquecer peso_kg de mayoristas) los deja desactualizados, y Fase B
    # necesita el peso real de cada ruta para su chequeo de cupo (85 %).
    for _r in rutas:
        _recalcular_peso_ruta(_r)

    # Fase A — config de tiempo de entrega (para marcar y, desde Fase B,
    # reubicar paradas fuera de horario). Degradación segura: ante
    # cualquier error, no se marca ni reubica nada.
    cfg_tiempo = None
    if TIEMPO_ENTREGA_ESTRICTO:
        try:
            cfg_row = db.execute(select(get_table("configuracion"))).mappings().first() or {}
            depot = (float(cfg_row.get("matriz_lat") or 18.87329315661368),
                     float(cfg_row.get("matriz_lon") or -96.9491574270346))
            cd = cfg_row.get("config_dias")
            cd = json.loads(cd) if isinstance(cd, str) else (cd or {})
            cfg_tiempo = {
                "activo":    True,
                "depot":     depot,
                "velocidad": float(cfg_row.get("velocidad_kmh") or 35.0),
                "dias":      cd,
            }
        except Exception as e:  # noqa: BLE001
            print(f"[generar_pdf] tiempo de entrega desactivado por error: {e}")
            cfg_tiempo = None

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
    # agregar_sucursal_a_asignacion/quitar_sucursal_de_asignacion de
    # modificacion_logic.py, que tampoco recalculan esos campos. La lista de
    # paradas que ve el conductor SÍ queda correcta (es lo que importa para
    # la entrega); las estimaciones de hora de salida/regreso pueden quedar
    # desactualizadas hasta el siguiente guardado completo de Modificación.
    if cfg_tiempo and not rutas_inyectadas:
        try:
            afinidad = afinidad_historica_por_sucursal()
            movio_algo = resolver_fuera_de_horario(rutas, cfg_tiempo, afinidad,
                                                    consultar_osrm_fn=consultar_osrm)
            if movio_algo:
                payload = {
                    "fecha_modificacion": (mod_doc.get("fecha_modificacion") if mod_doc else None)
                                           or datetime.now().isoformat(),
                    "rutas_confirmadas": rutas,
                }
                resultado_guardado = guardar_modificacion(payload, logistica_id)
                if resultado_guardado.get("status") != "ok":
                    print(f"[generar_pdf] guardar_modificacion falló tras reubicar "
                          f"fuera de horario: {resultado_guardado.get('mensaje')}")
        except Exception as e:  # noqa: BLE001
            print(f"[generar_pdf] reubicación fuera de horario omitida por error: {e}")

    vol_map = _volumenes_suc(db, oid)
```

- [ ] **Step 4: Eliminar el bloque de `cfg_tiempo` ahora duplicado, más adelante en la función**

Buscar en `generar_pdf` (más abajo, justo antes de `# Agrupar por (vehículo, chofer efectivo)...`) el bloque:

```python
    # Fase A — config de tiempo de entrega (para marcar paradas fuera de horario).
    # Degradación segura: ante cualquier error, no se marca nada.
    cfg_tiempo = None
    if TIEMPO_ENTREGA_ESTRICTO:
        try:
            cfg_row = db.execute(select(get_table("configuracion"))).mappings().first() or {}
            depot = (float(cfg_row.get("matriz_lat") or 18.87329315661368),
                     float(cfg_row.get("matriz_lon") or -96.9491574270346))
            cd = cfg_row.get("config_dias")
            cd = json.loads(cd) if isinstance(cd, str) else (cd or {})
            cfg_tiempo = {
                "activo":    True,
                "depot":     depot,
                "velocidad": float(cfg_row.get("velocidad_kmh") or 35.0),
                "dias":      cd,
            }
        except Exception as e:  # noqa: BLE001
            print(f"[generar_pdf] tiempo de entrega desactivado por error: {e}")
            cfg_tiempo = None

    # Agrupar por (vehículo, chofer efectivo): una ruta con chofer
```

y eliminar el bloque `cfg_tiempo` (ya se calculó en el Step 3), dejando solo:

```python
    # Agrupar por (vehículo, chofer efectivo): una ruta con chofer
```

(el resto del comentario y el código que sigue no cambian).

- [ ] **Step 5: Verificar sintaxis**

Run: `./env/Scripts/python.exe -c "import ast; ast.parse(open('logic/pdf_logic.py',encoding='utf-8').read()); print('PY OK')"`
Expected: `PY OK`

- [ ] **Step 6: Verificar que `cfg_tiempo` ya no aparece duplicado**

Run: `grep -c "cfg_tiempo = None" logic/pdf_logic.py`
Expected: `1` — el Step 3 agregó una construcción nueva (más temprano en la
función) y el Step 4 quitó la original (más tardía); debe quedar una sola.

- [ ] **Step 7: Commit**

```bash
git add logic/pdf_logic.py
git commit -m "feat(tiempo): enganchar Fase B (reubicacion fuera de horario) en generar_pdf"
```

---

## Task 8: Verificación de regresión completa

**Files:** (ninguno — solo verificación)

- [ ] **Step 1: Correr toda la suite**

Run: `./env/Scripts/python.exe -m pytest tests/ -q`
Expected: PASS — todas las pruebas previas (incluidas Fase A, rebalanceo geográfico, mayoristas) más las ~23 nuevas de `test_tiempo_reubicacion.py`, 0 fallas.

- [ ] **Step 2: Humo end-to-end (opcional, requiere BD)**

Con la BD levantada, generar el PDF de una logística real que hoy tenga paradas FUERA DE HORARIO (p. ej. la del 27-31 jul 2026, caso `Amatitlán` en `F 350_1` martes) y confirmar:
- El PDF ya no marca esa parada en la ruta original (o si no encontró destino con afinidad, sigue marcada — comportamiento esperado de Fase A intacto).
- `modificaciones_rutas` (tablas `modificacion_rutas`/`modificacion_ruta_sucursales`) refleja la nueva ruta tras generar el PDF.
- Generar el PDF una segunda vez no vuelve a mover nada que ya quedó resuelto (idempotencia práctica).

Script temporal de verificación (scratchpad, NO en el repo):

```python
# app.test_request_context + generar_pdf(datos_sesion) sobre una logística real
# Verificar: el PDF se genera sin error, y obtener_modificacion_previa(oid)
# refleja el movimiento tras la llamada.
```

- [ ] **Step 3: Commit final (si hubo ajustes)**

```bash
git add -A
git commit -m "test(tiempo): verificacion de regresion de Fase B"
```

---

---

## Task 9: Interruptor dedicado de Fase B + prueba de idempotencia

**Encontrado en la revisión final de todo el feature completo.** Dos hallazgos
"Important": (1) Fase B es la única de las funciones "geográficas" de este
proyecto (`REBALANCEO_GEOGRAFICO`, `MAYORISTAS_GEOGRAFICO`, `CONVRP_ACTIVO`)
sin su propio interruptor dedicado — solo hereda `TIEMPO_ENTREGA_ESTRICTO`
de Fase A, así que apagar Fase B en producción hoy también apagaría el
marcado de Fase A. Dado que Fase B es la primera de estas fases que
**escribe** en la base de datos (vía `guardar_modificacion`), merece su
propio interruptor. (2) El escenario de idempotencia (correr
`resolver_fuera_de_horario` dos veces sobre el mismo resultado no debe
mover nada la segunda vez) no tenía prueba automatizada — quedaba solo como
paso manual opcional en el Task 8.

**Files:**
- Modify: `logic/tiempo_reubicacion.py`
- Modify: `logic/pdf_logic.py` (un comentario, sin cambio de comportamiento)
- Test: `tests/test_tiempo_reubicacion.py`

- [ ] **Step 1: Escribir las pruebas**

Agregar a `tests/test_tiempo_reubicacion.py`:

```python
def test_resolver_fuera_de_horario_es_idempotente():
    # Mismo fixture que test_resolver_fuera_de_horario_mueve_a_ruta_con_afinidad_y_cupo:
    # correr la resolución dos veces sobre el mismo resultado no debe volver
    # a mover nada la segunda vez.
    afinidad = {42: {("DEST", "MARTES"): 1}}
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
    destino = {
        "id": "DEST", "dia": "martes", "vehiculo_abrev": "DEST",
        "capacidad_ton": 3.5, "peso_kg": 0, "pct_utilizacion": 0.0,
        "sucursales": [], "mayoristas": [],
    }
    rutas = [origen, destino]

    primera = resolver_fuera_de_horario(rutas, _cfg_cierre_08_30(), afinidad, consultar_osrm_fn=None)
    segunda = resolver_fuera_de_horario(rutas, _cfg_cierre_08_30(), afinidad, consultar_osrm_fn=None)

    assert primera is True
    assert segunda is False


def test_resolver_fuera_de_horario_flag_dedicado_apagado_no_hace_nada(monkeypatch):
    import logic.tiempo_reubicacion as tr
    monkeypatch.setattr(tr, "TIEMPO_REUBICACION_ACTIVA", False)
    ruta = {"id": "R1", "dia": "martes", "sucursales": [], "mayoristas": []}
    assert resolver_fuera_de_horario([ruta], _cfg_cierre_08_30(), {}) is False
```

- [ ] **Step 2: Correr y verificar que fallan**

Run: `./env/Scripts/python.exe -m pytest tests/test_tiempo_reubicacion.py -q`
Expected: `test_resolver_fuera_de_horario_es_idempotente` PASA de entrada (no depende de código nuevo); `test_resolver_fuera_de_horario_flag_dedicado_apagado_no_hace_nada` FALLA con `AttributeError: <module 'logic.tiempo_reubicacion'> does not have the attribute 'TIEMPO_REUBICACION_ACTIVA'` (el monkeypatch de un atributo inexistente falla así).

- [ ] **Step 3: Agregar el interruptor dedicado**

En `logic/tiempo_reubicacion.py`, junto a `UMBRAL_PCT_DESTINO`/`MAX_MOVIMIENTOS_POR_RUTA`, agregar:

```python
# Interruptor dedicado de Fase B (reubicación + persistencia). Independiente
# de TIEMPO_ENTREGA_ESTRICTO (Fase A, en logistica_tiempo.py — solo marca,
# no mueve ni persiste): apagar este interruptor deja el marcado de Fase A
# intacto pero desactiva la reubicación de Fase B, sin tocar Fase A. Mismo
# patrón que REBALANCEO_GEOGRAFICO/MAYORISTAS_GEOGRAFICO/CONVRP_ACTIVO en
# este proyecto — Fase B es la primera de estas fases que escribe en BD,
# por eso necesita su propio apagador.
TIEMPO_REUBICACION_ACTIVA = True
```

Y en `resolver_fuera_de_horario`, cambiar la primera línea:

```python
    if not (cfg_tiempo and cfg_tiempo.get("activo")):
        return False
```

por:

```python
    if not (TIEMPO_REUBICACION_ACTIVA and cfg_tiempo and cfg_tiempo.get("activo")):
        return False
```

- [ ] **Step 4: Correr y verificar que pasan**

Run: `./env/Scripts/python.exe -m pytest tests/test_tiempo_reubicacion.py -q`
Expected: PASS (27 passed — 25 previas + 2 nuevas).

- [ ] **Step 5: Documentar la falta de caché de afinidad (hallazgo Minor de la revisión final)**

En `logic/pdf_logic.py`, en la línea `afinidad = afinidad_historica_por_sucursal()` dentro del bloque de Fase B, agregar el comentario (sin cambiar la línea en sí):

```python
            # Sin caché: relee y reprocesa las 9 semanas del corpus histórico
            # en cada generación de PDF. Aceptado por ahora (misma decisión
            # que la falta de recálculo de hora_salida/hora_regreso arriba);
            # revisar si la frecuencia de generación de PDF crece.
            afinidad = afinidad_historica_por_sucursal()
```

- [ ] **Step 6: Verificar sintaxis y correr toda la suite**

Run: `./env/Scripts/python.exe -c "import ast; ast.parse(open('logic/tiempo_reubicacion.py',encoding='utf-8').read()); ast.parse(open('logic/pdf_logic.py',encoding='utf-8').read()); print('PY OK')"`
Run: `./env/Scripts/python.exe -m pytest tests/ -q`
Expected: `PY OK`; suite completa en verde (202 previas + 2 nuevas = 204).

- [ ] **Step 7: Commit**

```bash
git add logic/tiempo_reubicacion.py logic/pdf_logic.py tests/test_tiempo_reubicacion.py
git commit -m "feat(tiempo): interruptor dedicado TIEMPO_REUBICACION_ACTIVA + prueba de idempotencia"
```

Usar `git add -p` en `logic/pdf_logic.py` si `git diff` muestra más de un hunk (el hunk ajeno de ConVRP en `_formatear_docs_agrupados` debe quedar sin commitear, igual que en las Tasks 1 y 7).

**Nota sobre alcance:** esta Task NO implementa memoización de `consultar_osrm`
(hallazgo Important #3 de la revisión final) ni separa el commit de Task 7
del parámetro `rutas_inyectadas` de ConVRP (hallazgo Important #1) — ambos
quedan documentados como seguimiento pendiente, no como bloqueo, según lo
decidido al cerrar la revisión final.

---

## Self-Review (cobertura del spec)

- Punto de enganche dentro de `generar_pdf`, antes de renderizar, sin tocar el motor VRP/rebalanceo/mayoristas → Task 7. ✓
- Persistencia real (no solo el PDF impreso) vía `guardar_modificacion()` → Task 7, Step 3. ✓
- Referencia "canon" = histórico real (`rutas_historicas`), no `plantilla_canonica.py` → Task 2 (`afinidad_historica_por_sucursal`, basada en `_extraer_secuencias_historicas`/`_historiales_crudos_sucursales`). ✓
- Normalización de nombre de vehículo (bug `'F350_2' != 'F 350_2'`) → Task 1 (`_normalizar_veh`), usado en Task 5 (`_candidatas_con_afinidad`). ✓
- Mismo día primero, otro día como respaldo → Task 5 (`_candidatas_con_afinidad(..., mismo_dia)`) + Task 6 (orquestador llama primero con `True`, luego `False`). ✓
- Tope de 85 % de utilización (por peso, igual que el `% RUTA` que ya imprime el PDF) → Task 3 (`_cabe_por_peso`), Task 5 (`_mejor_candidata`). ✓
- Verificación de tiempo en la ruta destino tras insertar → Task 5 (`_sin_fuera_de_horario`, `_simular_insercion`), usado en `_mejor_candidata`. ✓
- Inserción en la posición geográficamente más cercana → Task 4 (`_insertar_en_ruta`, reusa `_insertar_pos_proxima`). ✓
- Último recurso solo entre candidatas con afinidad (nunca fuera de ella) → Task 5 (`_menos_mala`), Task 6 (recibe `candidatas_mismo_dia + candidatas_otro_dia`, nunca la lista completa de rutas). ✓
- Sin destino con afinidad → se queda FUERA DE HORARIO como Fase A → Task 6 (`if destino is None: break`). ✓
- Mayoristas anclados a la sucursal más cercana de la misma ruta → Task 5 (`_clave_afinidad_para`, rama mayorista). ✓
- Sin trazabilidad especial (ni nota en PDF ni auditoría) → ningún task agrega ninguna. ✓
- `rutas_historicas` nunca se escribe → Fase B solo LEE vía `afinidad_historica_por_sucursal()` (Task 2); ningún task inserta/actualiza esa tabla. ✓
- Procesar paradas en orden de secuencia, re-evaluando origen tras cada movimiento → Task 6 (`for _ in range(MAX_MOVIMIENTOS_POR_RUTA): ... recalcula combinado y evals en cada vuelta`). ✓
- Degradación segura (try/except) → Task 7, Step 3 (`try/except` alrededor de todo el enganche). ✓
- Consistencia entre lo que decide Fase B y lo que finalmente imprime el PDF → Task 1 (`evaluar_ruta_completa` factorizada y reusada por ambos). ✓
