# Orden Fijo de Paradas Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permitir fijar, por regla nombrada, un orden de visita explícito para un conjunto de sucursales que gana sobre el histórico y la geografía, sin tocar los motores de generación de rutas.

**Architecture:** Tabla nueva `orden_fijo_paradas` (nombre_regla, num_tienda, posicion) + módulo puro `logic/orden_fijo_paradas.py` (`obtener_orden_fijo`, `aplicar_orden_fijo`) conectado en el único punto de secuenciado compartido por los dos motores, dentro de `generar_rutas_vrp_afinidad` (`logic/historico_logic.py`).

**Tech Stack:** Python puro para la lógica; SQLAlchemy Core + SQL Server para la tabla nueva (mismo patrón que `plantilla_grupo_dia`/`grupos_unidad_forzada`). Pruebas con `pytest`.

**Spec:** [docs/superpowers/specs/2026-08-25-orden-fijo-paradas-design.md](../specs/2026-08-25-orden-fijo-paradas-design.md)

---

## Contexto para quien implemente

Ninguno de los dos motores de rutas (ConVRP activo por `CONVRP_ACTIVO=True`, o
el motor de afinidad) asigna hoy un orden de visita real dentro de una ruta.
El paso que decide el orden final para CUALQUIERA de los dos —
`ordenar_paradas_por_historico()` en `logic/vrp_logic.py`, llamado desde
`generar_rutas_vrp_afinidad()` en `logic/historico_logic.py` — usa
`seq` histórico si existe, y si no, cae a vecino-más-cercano por coordenada.
Con ConVRP activo, `seq` siempre es `999` (nunca hay historial), así que hoy
CUALQUIER ruta de ConVRP se ordena solo por geografía.

Este plan agrega una capa que se evalúa **antes** de ese paso: si todas las
sucursales de una ruta pertenecen a la misma "regla" de orden fijo (una
tabla chica y editable a mano), se usa ese orden exacto en vez de historial o
geografía. Si la ruta mezcla sucursales de la regla con otras que no son
parte de ella, no se aplica nada — la ruta sigue el camino normal sin
cambios (fuera de alcance de esta versión, ver spec §5).

Dos reglas reales para esta primera carga:
- `cosamaloapan_carrillo_amatitlan` (F 350_1/MARTES): sucursales 4, 27, 75,
  86, 49, 100 → posiciones 1-6.
- `tuxtepec_f350_2` (F 350_2/MARTES): sucursales 2, 31, 74, 55, 7, 54, 15 →
  posiciones 1-7.

## File Structure

- **Create:** `logic/orden_fijo_paradas.py` — `obtener_orden_fijo(db)` (lee la
  tabla), `aplicar_orden_fijo(miembros, orden_fijo)` (lógica pura de decisión).
- **Create:** `scripts/crear_orden_fijo_paradas.py` — DDL idempotente de la
  tabla nueva (mismo patrón que `scripts/crear_plantilla_canonica.py`).
- **Create:** `scripts/cargar_orden_fijo.py` — carga
  `datos/orden_fijo_paradas.csv` con reemplazo completo por `nombre_regla`.
- **Create:** `datos/orden_fijo_paradas.csv` — semilla con las dos reglas.
- **Create:** `tests/test_orden_fijo_paradas.py`.
- **Modify:** `logic/historico_logic.py` — importa y conecta las dos
  funciones nuevas en `generar_rutas_vrp_afinidad`.
- **Modify:** `README.md` — una mención del mecanismo nuevo.

---

### Task 1: `aplicar_orden_fijo()` — lógica pura (TDD)

**Files:**
- Create: `logic/orden_fijo_paradas.py`
- Create: `tests/test_orden_fijo_paradas.py`

- [ ] **Step 1: Write the failing tests**

Crea `tests/test_orden_fijo_paradas.py`:

```python
"""
tests/test_orden_fijo_paradas.py
"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from logic.orden_fijo_paradas import aplicar_orden_fijo


def test_aplica_orden_fijo_cuando_toda_la_ruta_pertenece_a_la_regla():
    orden_fijo = {4: ("regla_a", 1), 27: ("regla_a", 2), 75: ("regla_a", 3)}
    miembros = [{"sid": 75}, {"sid": 4}, {"sid": 27}]
    assert aplicar_orden_fijo(miembros, orden_fijo) == [4, 27, 75]


def test_aplica_orden_fijo_con_sucursales_faltantes_esa_semana():
    orden_fijo = {4: ("regla_a", 1), 27: ("regla_a", 2), 75: ("regla_a", 3)}
    miembros = [{"sid": 75}, {"sid": 4}]          # sin la 27 esta semana
    assert aplicar_orden_fijo(miembros, orden_fijo) == [4, 75]


def test_no_aplica_si_hay_una_sucursal_ajena_a_la_regla():
    orden_fijo = {4: ("regla_a", 1), 27: ("regla_a", 2)}
    miembros = [{"sid": 4}, {"sid": 27}, {"sid": 999}]   # 999 no está en ninguna regla
    assert aplicar_orden_fijo(miembros, orden_fijo) is None


def test_no_aplica_si_mezcla_dos_reglas_distintas():
    orden_fijo = {4: ("regla_a", 1), 100: ("regla_b", 1)}
    miembros = [{"sid": 4}, {"sid": 100}]
    assert aplicar_orden_fijo(miembros, orden_fijo) is None


def test_no_aplica_con_orden_fijo_vacio():
    miembros = [{"sid": 4}, {"sid": 27}]
    assert aplicar_orden_fijo(miembros, {}) is None


def test_no_aplica_con_miembros_vacios():
    assert aplicar_orden_fijo([], {4: ("regla_a", 1)}) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_orden_fijo_paradas.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'logic.orden_fijo_paradas'`

- [ ] **Step 3: Write minimal implementation**

Crea `logic/orden_fijo_paradas.py`:

```python
"""
logic/orden_fijo_paradas.py

Orden de visita fijo por regla nombrada. Cuando TODAS las sucursales de una
ruta pertenecen a la misma regla, ese orden gana sobre el histórico y la
geografía (ver ordenar_paradas_por_historico en vrp_logic.py). Si la ruta
mezcla sucursales de la regla con otras que no son parte de ella, no se
aplica ningún pin -- la ruta sigue el camino normal sin cambios.
"""
from sqlalchemy import select

from db import get_table


def obtener_orden_fijo(db) -> dict:
    """
    Lee orden_fijo_paradas y arma {num_tienda: (nombre_regla, posicion)}.

    Se llama UNA sola vez por corrida de generar_rutas_vrp_afinidad (antes
    del bucle de rutas), no por ruta -- es una tabla chica de referencia.
    """
    t = get_table("orden_fijo_paradas")
    filas = db.execute(select(t.c.num_tienda, t.c.nombre_regla, t.c.posicion)).mappings().all()
    return {f["num_tienda"]: (f["nombre_regla"], f["posicion"]) for f in filas}


def aplicar_orden_fijo(miembros: list, orden_fijo: dict):
    """
    Devuelve las sids de `miembros` ordenadas por su posición fija, o None
    si la ruta no aplica: falta alguna sucursal en `orden_fijo`, o mezcla
    sucursales de más de una regla.

    miembros: [{"sid": id, ...}, ...] -- solo se usa la clave "sid".
    """
    if not miembros or not orden_fijo:
        return None

    entradas = []
    reglas = set()
    for m in miembros:
        sid = m["sid"]
        if sid not in orden_fijo:
            return None
        nombre_regla, posicion = orden_fijo[sid]
        reglas.add(nombre_regla)
        entradas.append((posicion, sid))

    if len(reglas) != 1:
        return None

    entradas.sort(key=lambda e: e[0])
    return [sid for _, sid in entradas]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_orden_fijo_paradas.py -v`
Expected: 6 PASSED

- [ ] **Step 5: Commit**

```bash
git add logic/orden_fijo_paradas.py tests/test_orden_fijo_paradas.py
git commit -m "feat: agrega aplicar_orden_fijo (logica pura de orden fijo de paradas)"
```

---

### Task 2: Tabla `orden_fijo_paradas` en SQL Server

**Files:**
- Create: `scripts/crear_orden_fijo_paradas.py`

- [ ] **Step 1: Escribir el script**

Crea `scripts/crear_orden_fijo_paradas.py`:

```python
"""
crear_orden_fijo_paradas.py

Script de un solo uso (idempotente): crea la tabla orden_fijo_paradas en
SQL Server si no existe. No borra ni modifica datos.

Uso:
    python scripts/crear_orden_fijo_paradas.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy import text
from app import create_app
from db import get_engine

DDL = """
    CREATE TABLE orden_fijo_paradas (
        nombre_regla  NVARCHAR(100) NOT NULL,
        num_tienda    INT           NOT NULL,
        posicion      INT           NOT NULL,
        CONSTRAINT PK_orden_fijo_paradas PRIMARY KEY (nombre_regla, num_tienda)
    )
"""


def main():
    app = create_app()
    with app.app_context():
        eng = get_engine()
        with eng.begin() as conn:
            existe = conn.execute(
                text("SELECT OBJECT_ID(:t, 'U')"), {"t": "dbo.orden_fijo_paradas"}
            ).scalar()
            if existe is None:
                conn.execute(text(DDL))
                print("Tabla creada: orden_fijo_paradas")
            else:
                print("Ya existía: orden_fijo_paradas")
        print("\nNota: reinicia el proceso Flask para que la reflexión de "
              "MetaData recoja la tabla nueva (db.py refleja una sola vez "
              "por proceso).")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Ejecutar contra la base real**

Run: `python scripts/crear_orden_fijo_paradas.py`
Expected: `Tabla creada: orden_fijo_paradas` (o `Ya existía` si se corre dos veces — correr dos veces seguidas para confirmar idempotencia).

- [ ] **Step 3: Commit**

```bash
git add scripts/crear_orden_fijo_paradas.py
git commit -m "feat: crea la tabla orden_fijo_paradas"
```

---

### Task 3: Semilla CSV + carga + `obtener_orden_fijo()`

**Files:**
- Create: `datos/orden_fijo_paradas.csv`
- Create: `scripts/cargar_orden_fijo.py`
- Test: `tests/test_orden_fijo_paradas.py`

- [ ] **Step 1: Crear el CSV semilla**

Crea `datos/orden_fijo_paradas.csv`:

```csv
nombre_regla,num_tienda,posicion
cosamaloapan_carrillo_amatitlan,4,1
cosamaloapan_carrillo_amatitlan,27,2
cosamaloapan_carrillo_amatitlan,75,3
cosamaloapan_carrillo_amatitlan,86,4
cosamaloapan_carrillo_amatitlan,49,5
cosamaloapan_carrillo_amatitlan,100,6
tuxtepec_f350_2,2,1
tuxtepec_f350_2,31,2
tuxtepec_f350_2,74,3
tuxtepec_f350_2,55,4
tuxtepec_f350_2,7,5
tuxtepec_f350_2,54,6
tuxtepec_f350_2,15,7
```

(`datos/*.csv` está en `.gitignore` — este archivo NO se commitea, igual que
`grupos_unidad_forzada.csv`. Es una semilla local que alimenta la tabla vía
el script de carga; la tabla en SQL Server es la fuente de verdad real.)

- [ ] **Step 2: Escribir el script de carga**

Crea `scripts/cargar_orden_fijo.py`:

```python
"""
cargar_orden_fijo.py

Carga datos/orden_fijo_paradas.csv en la tabla orden_fijo_paradas con
reemplazo completo por nombre_regla: borra las filas de cada regla presente
en el CSV e inserta las del CSV -- nunca acumula, mismo criterio que el
resto del proyecto (ver db.transaccion()).

Uso:
    python scripts/cargar_orden_fijo.py [ruta_csv]
"""
import sys, os, csv
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import create_app
from db import get_table, transaccion

CSV_DEFAULT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "datos", "orden_fijo_paradas.csv")


def cargar(csv_path: str = CSV_DEFAULT):
    with open(csv_path, newline="", encoding="utf-8") as f:
        filas = [
            {"nombre_regla": r["nombre_regla"].strip(),
             "num_tienda": int(r["num_tienda"]),
             "posicion": int(r["posicion"])}
            for r in csv.DictReader(f)
        ]
    if not filas:
        print("CSV vacío, nada que cargar.")
        return

    reglas = sorted({f["nombre_regla"] for f in filas})
    t = get_table("orden_fijo_paradas")
    with transaccion() as conn:
        for regla in reglas:
            conn.execute(t.delete().where(t.c.nombre_regla == regla))
        conn.execute(t.insert(), filas)

    print(f"Reglas cargadas ({len(reglas)}): {', '.join(reglas)}")
    print(f"Filas insertadas: {len(filas)}")


def main():
    app = create_app()
    with app.app_context():
        csv_path = sys.argv[1] if len(sys.argv) > 1 else CSV_DEFAULT
        cargar(csv_path)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Ejecutar la carga contra la base real**

Run: `python scripts/cargar_orden_fijo.py`
Expected: `Reglas cargadas (2): cosamaloapan_carrillo_amatitlan, tuxtepec_f350_2` y `Filas insertadas: 13`. Correr dos veces seguidas y confirmar que la segunda corrida sigue insertando exactamente 13 filas (reemplazo completo, no acumula).

- [ ] **Step 4: Escribir el test de integración de `obtener_orden_fijo`**

Agrega en `tests/test_orden_fijo_paradas.py`, al final:

```python
# ── Integración con BD real (se salta si no hay SQL Server) ────────────────
import pytest


@pytest.fixture(scope="module")
def app_ctx():
    try:
        from app import create_app
        app = create_app()
        ctx = app.app_context(); ctx.push()
        from db import get_db, get_table
        get_db().execute  # fuerza apertura de conexión
        get_table("orden_fijo_paradas")
    except Exception as e:  # sin BD o sin la tabla
        pytest.skip(f"BD no disponible: {e}")
        return
    yield app
    ctx.pop()


def test_obtener_orden_fijo_lee_la_tabla_real(app_ctx):
    from db import get_table, transaccion, get_db
    from logic.orden_fijo_paradas import obtener_orden_fijo

    t = get_table("orden_fijo_paradas")
    regla_prueba = "prueba_test_orden_fijo_paradas"
    # num_tienda con centinelas fuera de rango (nunca 1-101, el rango real de
    # sucursales): con IDs pequeños (1, 2) el insert desechable coexiste en
    # silencio con la fila real num_tienda=2 de tuxtepec_f350_2 (la PK es
    # (nombre_regla, num_tienda), no solo num_tienda) y el dict de
    # obtener_orden_fijo -- keyed solo por num_tienda -- queda con un ganador
    # indefinido para esa clave (hallazgo real durante la implementación).
    sid_a, sid_b = 999901, 999902
    with transaccion() as conn:
        conn.execute(t.delete().where(t.c.nombre_regla == regla_prueba))
        conn.execute(t.insert(), [
            {"nombre_regla": regla_prueba, "num_tienda": sid_a, "posicion": 1},
            {"nombre_regla": regla_prueba, "num_tienda": sid_b, "posicion": 2},
        ])
    try:
        orden_fijo = obtener_orden_fijo(get_db())
        assert orden_fijo.get(sid_a) == (regla_prueba, 1)
        assert orden_fijo.get(sid_b) == (regla_prueba, 2)
    finally:
        with transaccion() as conn:
            conn.execute(t.delete().where(t.c.nombre_regla == regla_prueba))


# ── Regresión con las dos reglas reales cargadas por scripts/cargar_orden_fijo.py ──
def test_regresion_orden_fijo_cosamaloapan_carrillo_amatitlan(app_ctx):
    from db import get_db
    from logic.orden_fijo_paradas import obtener_orden_fijo

    orden_fijo = obtener_orden_fijo(get_db())
    if 4 not in orden_fijo:
        pytest.skip("regla cosamaloapan_carrillo_amatitlan no está cargada todavía")
    miembros = [{"sid": s} for s in [100, 49, 86, 4, 75, 27]]   # orden mezclado a propósito
    assert aplicar_orden_fijo(miembros, orden_fijo) == [4, 27, 75, 86, 49, 100]


def test_regresion_orden_fijo_tuxtepec_f350_2(app_ctx):
    from db import get_db
    from logic.orden_fijo_paradas import obtener_orden_fijo

    orden_fijo = obtener_orden_fijo(get_db())
    if 2 not in orden_fijo:
        pytest.skip("regla tuxtepec_f350_2 no está cargada todavía")
    miembros = [{"sid": s} for s in [15, 54, 7, 55, 74, 31, 2]]  # orden mezclado a propósito
    assert aplicar_orden_fijo(miembros, orden_fijo) == [2, 31, 74, 55, 7, 54, 15]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_orden_fijo_paradas.py -v`
Expected: 9 PASSED (los 6 del Task 1 + los 3 nuevos). Si no hay conexión a
SQL Server disponible en este entorno, los tres tests con `app_ctx` deben
aparecer como `SKIPPED`, no `FAILED` — confirma que el `pytest.skip` del
fixture funciona. Si SÍ hay conexión pero la carga del Step 3 todavía no
corrió, los dos tests de regresión deben `SKIPPED` (por su propio
`pytest.skip` interno) sin fallar — vuelve a correrlos después del Step 3
para verlos `PASSED` de verdad.

- [ ] **Step 6: Commit**

```bash
git add scripts/cargar_orden_fijo.py tests/test_orden_fijo_paradas.py
git commit -m "feat: agrega carga de orden_fijo_paradas y obtener_orden_fijo()"
```

(`datos/orden_fijo_paradas.csv` NO se agrega a git — está en `.gitignore`.)

---

### Task 4: Conectar en `generar_rutas_vrp_afinidad`

**Files:**
- Modify: `logic/historico_logic.py`

- [ ] **Step 1: Agregar el import**

En `logic/historico_logic.py`, junto a los demás imports de `logic.vrp_afinidad`
(cerca de la línea 100-103, donde ya está
`from logic.vrp_afinidad.clarke_wright import haversine as _haversine_cw, Ruta as _Ruta_CW`),
agrega:

```python
from logic.orden_fijo_paradas import obtener_orden_fijo, aplicar_orden_fijo
```

- [ ] **Step 2: Cargar `orden_fijo` una sola vez por corrida**

Dentro de `generar_rutas_vrp_afinidad`, justo antes del bucle
`for (veh, dia), miembros in sorted(groups.items()):` (sección "7. Estadísticas
históricas..."), agrega:

```python
    orden_fijo = obtener_orden_fijo(db)
```

(`db` ya existe en ese punto de la función — es la misma conexión usada en
todo `generar_rutas_vrp_afinidad`.)

- [ ] **Step 3: Usar el orden fijo antes que el histórico/geografía**

Dentro del mismo bucle, reemplaza:

```python
        # Orden: prioriza la secuencia histórica de cada parada. Las paradas
        # sin historial válido (nuevas o reasignadas) se insertan por
        # proximidad geográfica, sin descartar el orden histórico del resto.
        ordered = ordenar_paradas_por_historico(miembros, coords_dict)
```

por:

```python
        # Orden: si TODA la ruta pertenece a la misma regla de orden fijo,
        # ese orden gana sobre historial y geografía (ver
        # logic/orden_fijo_paradas.py). Si no aplica, sigue el camino normal:
        # prioriza la secuencia histórica; las paradas sin historial válido
        # se insertan por proximidad geográfica.
        ordered = (aplicar_orden_fijo(miembros, orden_fijo)
                  or ordenar_paradas_por_historico(miembros, coords_dict))
```

- [ ] **Step 4: Run the full relevant test suite**

Run: `pytest tests/test_orden_fijo_paradas.py tests/test_convrp_logic.py tests/test_logistica_tiempo.py -v`
Expected: todos PASSED, sin regresiones (este cambio es aditivo: `aplicar_orden_fijo`
solo devuelve algo distinto de `None` cuando TODA la ruta coincide con una
regla cargada — con `orden_fijo` recién creado y sin datos previos que lo
disparen accidentalmente en ningún test existente).

- [ ] **Step 5: Commit**

```bash
git add logic/historico_logic.py
git commit -m "feat: conecta el orden fijo de paradas en generar_rutas_vrp_afinidad"
```

---

### Task 5: Actualizar README.md

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Agregar una mención breve del mecanismo**

En `README.md`, en la sección donde se describe el flujo de generación de
rutas (busca el párrafo que menciona `ordenar_paradas_por_historico` o el
orden de paradas; si no existe un párrafo así, agrega uno nuevo cerca de la
descripción del motor ConVRP, después del párrafo que ya menciona las
palancas):

```markdown
**Orden fijo de paradas:** `logic/orden_fijo_paradas.py` permite fijar, por
regla nombrada (tabla `orden_fijo_paradas`, cargada vía
`scripts/cargar_orden_fijo.py` desde `datos/orden_fijo_paradas.csv`), un
orden de visita explícito para un conjunto de sucursales. Cuando TODA una
ruta generada coincide con una regla, ese orden gana sobre el histórico y la
geografía en `ordenar_paradas_por_historico()`; si la ruta mezcla sucursales
ajenas a la regla, no se aplica ningún pin.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: documenta el orden fijo de paradas en README"
```
