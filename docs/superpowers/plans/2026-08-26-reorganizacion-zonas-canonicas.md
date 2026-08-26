# Reorganización de zonas canónicas (grupos LORES → 24 zonas) — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reemplazar los 42 `grupos` LORES vigentes (versión 17) por las 24
zonas de negocio dadas por los jefes de prácticas, respetando el límite de 6
sucursales/día (con la excepción confirmada de la Zona 22), sin tocar el
enganche de mayoristas por población.

**Architecture:** Una columna nueva `zona` en `plantilla_grupo` (ALTER
idempotente) permite que varias filas de `grupo` compartan una misma `zona`
de negocio. Una función pura `derivar_grupo_zona()` hereda calibración
(rigidez/día/unidad) del grupo viejo con más peso dentro de cada zona nueva,
para las 22 zonas que quedan como una sola ruta. Las Zonas 5 (Tuxtepec) y 11
(Tierra Blanca) se parten en sub-rutas explícitas, construidas a mano a
partir del histórico real (documentado en el spec). Una función nueva
`cargar_zonas_manual()` persiste el resultado como una versión nueva y
no-destructiva de `plantilla_grupo`/`plantilla_grupo_sucursal`/
`plantilla_grupo_dia`, sin tocar bridge/mayoristas/población.

**Tech Stack:** Python 3, Flask, SQLAlchemy Core, SQL Server (pyodbc),
pytest.

**Spec:** `docs/superpowers/specs/2026-08-26-reorganizacion-zonas-canonicas-design.md`

---

## Task 1: Columna `zona` en `plantilla_grupo`

**Files:**
- Modify: `scripts/crear_plantilla_canonica.py:36-38`

- [ ] **Step 1: Agregar el ALTER**

En `scripts/crear_plantilla_canonica.py`, la lista `ALTERS` (línea 36) ya
tiene un ejemplo (`unidades_afines`). Agrega la columna `zona`:

```python
ALTERS = [
    ("plantilla_grupo", "unidades_afines", "NVARCHAR(400) NULL"),
    ("plantilla_grupo", "zona", "INT NULL"),
]
```

- [ ] **Step 2: Correr el script contra la BD local**

```bash
cd /c/Users/carli/Documents/ICG/logistica_icg
python scripts/crear_plantilla_canonica.py
```

Expected (la tabla ya existe, sólo agrega la columna):
```
Tablas creadas:   (ninguna)
Ya existían:      ['plantilla_meta', 'plantilla_bridge_sucursal', 'plantilla_grupo', ...]
Columnas nuevas:  ['plantilla_grupo.zona']

Nota: reinicia el proceso Flask para que la reflexión de MetaData recoja las tablas nuevas...
```

- [ ] **Step 3: Verificar la columna en SQL Server**

```bash
python -c "
from app import create_app
from db import get_db, get_table
from sqlalchemy import select
app = create_app()
with app.app_context():
    t = get_table('plantilla_grupo')
    print([c.name for c in t.columns])
"
```
Expected: la lista de columnas incluye `'zona'`.

- [ ] **Step 4: Commit**

```bash
git add scripts/crear_plantilla_canonica.py
git commit -m "feat: agregar columna zona a plantilla_grupo (ALTER idempotente)"
```

---

## Task 2: `derivar_grupo_zona()` — función pura de derivación

**Files:**
- Modify: `logic/plantilla_canonica.py` (insertar después de la línea 490,
  entre el final de `cargar_plantilla_desde_excel` y `_hash_archivo`)
- Test: `tests/test_plantilla_canonica.py`

- [ ] **Step 1: Escribir los tests que fallan**

Agrega al final de `tests/test_plantilla_canonica.py`:

```python
# ── derivación de rigidez/día/unidad para zonas que fusionan grupos viejos ──
def _grupo_info(rigidez, dia, unidad_ref, dias_admisibles, forzada=False):
    return dict(rigidez=rigidez, dia=dia, dia_preferido=dia,
                unidad_ref=unidad_ref, unidades_afines=None,
                unidad_forzada=forzada, dias_admisibles=dias_admisibles)


def test_derivar_grupo_zona_gana_mayoria_clara():
    from logic.plantilla_canonica import derivar_grupo_zona
    grupo_de_sucursal = {1: 10, 2: 10, 3: 10, 4: 10, 5: 20, 6: 20}
    grupos_por_id = {
        10: _grupo_info("RIGIDO", "MARTES", "F 350_1", ["MARTES"], forzada=True),
        20: _grupo_info("FLEXIBLE", "JUEVES", "F 350_2", ["JUEVES"]),
    }
    d = derivar_grupo_zona([1, 2, 3, 4, 5, 6], grupo_de_sucursal, grupos_por_id)
    assert d["grupo_origen"] == 10
    assert d["pct"] == pytest.approx(4 / 6)
    assert d["revisar"] is False
    assert d["rigidez"] == "RIGIDO"
    assert d["unidad_ref"] == "F 350_1"
    assert d["unidad_forzada"] is True


def test_derivar_grupo_zona_empate_gana_menor_numero():
    from logic.plantilla_canonica import derivar_grupo_zona
    grupo_de_sucursal = {1: 20, 2: 20, 3: 20, 4: 10, 5: 10, 6: 10}
    grupos_por_id = {
        10: _grupo_info("RIGIDO", "LUNES", "K 16", ["LUNES"]),
        20: _grupo_info("FLEXIBLE", "MARTES", "T 20", ["MARTES"]),
    }
    d = derivar_grupo_zona([1, 2, 3, 4, 5, 6], grupo_de_sucursal, grupos_por_id)
    assert d["grupo_origen"] == 10          # empate 3-3, gana el numero mas bajo
    assert d["pct"] == pytest.approx(0.5)


def test_derivar_grupo_zona_bajo_umbral_marca_revisar():
    from logic.plantilla_canonica import derivar_grupo_zona
    grupo_de_sucursal = {1: 10, 2: 20, 3: 30, 4: 40}
    grupos_por_id = {
        10: _grupo_info("RIGIDO", "LUNES", "K 16", ["LUNES"]),
        20: _grupo_info("RIGIDO", "LUNES", "T 20", ["LUNES"]),
        30: _grupo_info("FLEXIBLE", "LUNES", "T 23", ["LUNES"]),
        40: _grupo_info("FLEXIBLE", "LUNES", "T 25", ["LUNES"]),
    }
    d = derivar_grupo_zona([1, 2, 3, 4], grupo_de_sucursal, grupos_por_id)
    assert d["grupo_origen"] == 10
    assert d["pct"] == 0.25
    assert d["revisar"] is True
```

- [ ] **Step 2: Correr los tests, verificar que fallan**

```bash
python -m pytest tests/test_plantilla_canonica.py -k derivar_grupo_zona -v
```
Expected: `ImportError: cannot import name 'derivar_grupo_zona'` (o
`ModuleNotFoundError`) en las 3 pruebas — la función no existe todavía.

- [ ] **Step 3: Implementar `derivar_grupo_zona()`**

En `logic/plantilla_canonica.py`, inserta esto entre el `return` final de
`cargar_plantilla_desde_excel` (línea ~490) y `def _hash_archivo` (línea
~493):

```python
def derivar_grupo_zona(sucursales: list, grupo_de_sucursal: dict,
                       grupos_por_id: dict, umbral: float = 0.60) -> dict:
    """
    Deriva rigidez/día/unidad_ref de una zona nueva que fusiona sucursales de
    varios `grupo` LORES viejos: hereda TODOS los valores calibrados del
    grupo que más sucursales le aportó a la zona. Empate -> gana el grupo de
    número más bajo (determinista).

    Si el grupo ganador cubre menos del `umbral` (60% por defecto, mismo
    criterio que `confianza_zona()` usa para "confianza BAJA" en
    enganche_zona.py) de las sucursales de la zona, `revisar=True`: se
    devuelve igual, no se aborta, pero queda marcada para que el negocio la
    revise.

    sucursales        : [num_tienda,...] de la zona nueva.
    grupo_de_sucursal : {num_tienda: grupo_id} del catálogo VIEJO vigente.
    grupos_por_id     : {grupo_id: {rigidez, dia, dia_preferido, unidad_ref,
                         unidades_afines, unidad_forzada, dias_admisibles}}
                        -- p. ej. {g["grupo"]: g for g in obtener_grupos()}.
    """
    from collections import Counter
    cnt = Counter(grupo_de_sucursal.get(s) for s in sucursales)
    total = len(sucursales) or 1
    ganador, veces = sorted(
        cnt.items(), key=lambda kv: (-kv[1], kv[0] if kv[0] is not None else 10**9)
    )[0]
    pct = veces / total
    info = grupos_por_id.get(ganador, {})
    return dict(
        grupo_origen=ganador, pct=pct, revisar=pct < umbral,
        rigidez=info.get("rigidez"), dia=info.get("dia"),
        dia_preferido=info.get("dia_preferido"), unidad_ref=info.get("unidad_ref"),
        unidades_afines=info.get("unidades_afines"),
        unidad_forzada=bool(info.get("unidad_forzada")),
        dias_admisibles=info.get("dias_admisibles") or [],
    )
```

- [ ] **Step 4: Correr los tests, verificar que pasan**

```bash
python -m pytest tests/test_plantilla_canonica.py -k derivar_grupo_zona -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add logic/plantilla_canonica.py tests/test_plantilla_canonica.py
git commit -m "feat: derivar_grupo_zona -- hereda calibracion del grupo viejo con mas peso"
```

---

## Task 3: `cargar_zonas_manual()` — persistencia versionada

**Files:**
- Modify: `logic/plantilla_canonica.py` (justo después de
  `derivar_grupo_zona`, agregada en la Task 2)

No lleva test automatizado de escritura contra BD real (mismo criterio que
`cargar_plantilla_desde_excel`, que tampoco lo tiene en este repo): se
verifica corriendo el script real en la Task 7-8. `derivar_grupo_zona` (Task
2) ya cubre en aislamiento la parte que sí es pura.

- [ ] **Step 1: Implementar `cargar_zonas_manual()`**

Agrega esto justo después de `derivar_grupo_zona` en
`logic/plantilla_canonica.py`:

```python
def cargar_zonas_manual(sub_rutas: list, nota: str = None) -> dict:
    """
    Escribe una VERSIÓN NUEVA de `plantilla_grupo`/`plantilla_grupo_sucursal`/
    `plantilla_grupo_dia` a partir de una lista de sub-rutas ya resuelta (no
    hay Excel que parsear: es la reorganización manual de zonas 2026-08, ver
    docs/superpowers/specs/2026-08-26-reorganizacion-zonas-canonicas-design.md).

    NO toca `plantilla_bridge_sucursal`, `plantilla_zona_mayorista` ni
    `plantilla_poblacion_zona` -- cada tabla filtra su propio `vigente=1`
    independiente del número de versión, así que pueden quedar en versiones
    distintas sin romper los lectores (`obtener_bridge`, `obtener_zona`,
    `zona_de_poblacion`). Nunca borra: mismo patrón no-destructivo que
    `cargar_plantilla_desde_excel`.

    sub_rutas: [{grupo, zona, rigidez, dia, dia_preferido (opcional),
                 unidad_ref (opcional, None = sin preferencia),
                 unidades_afines (opcional), unidad_forzada (opcional, bool),
                 dias_admisibles: [dia,...], sucursales: [num_tienda,...]}]
    """
    ahora = datetime.now().isoformat()
    with transaccion() as conn:
        t_meta = get_table("plantilla_meta")
        ver = (conn.execute(select(func.max(t_meta.c.version))).scalar() or 0) + 1

        for tn in ["plantilla_grupo", "plantilla_grupo_sucursal", "plantilla_grupo_dia"]:
            t = get_table(tn)
            conn.execute(update(t).where(t.c.vigente == 1).values(vigente=0))
        conn.execute(update(t_meta).where(t_meta.c.vigente == 1).values(vigente=0))

        tg = get_table("plantilla_grupo")
        conn.execute(insert(tg), [dict(
            version=ver, grupo=int(r["grupo"]), zona=int(r["zona"]),
            rigidez=r["rigidez"], dia=r.get("dia"),
            tam=len(r.get("sucursales", [])), cohesion=None,
            unidad_ref=r.get("unidad_ref"), que_hace_vrp=r.get("que_hace_vrp"),
            unidades_afines=r.get("unidades_afines"),
            unidad_forzada=bool(r.get("unidad_forzada")),
            vigente_desde=ahora, vigente=1) for r in sub_rutas])

        tgs = get_table("plantilla_grupo_sucursal")
        conn.execute(insert(tgs), [dict(
            version=ver, grupo=int(r["grupo"]), num_tienda=nt,
            vigente_desde=ahora, vigente=1)
            for r in sub_rutas for nt in r.get("sucursales", [])])

        filas_dia = []
        for r in sub_rutas:
            adm = r.get("dias_admisibles") or ([r["dia"]] if r.get("dia") else [])
            preferido = r.get("dia_preferido") or r.get("dia")
            for i, d in enumerate(adm):
                filas_dia.append(dict(
                    version=ver, grupo=int(r["grupo"]), dia=d,
                    es_canonico=1 if d == preferido else 0, orden=i,
                    vigente_desde=ahora, vigente=1))
        if filas_dia:
            conn.execute(insert(get_table("plantilla_grupo_dia")), filas_dia)

        conn.execute(insert(t_meta).values(
            version=ver, cargado_en=ahora, excel_archivo=None, excel_hash=None,
            semanas_analisis=None, n_grupos=len(sub_rutas), n_zonas=None,
            n_poblaciones=None, n_flags=None, vigente=1, nota=nota))

    return dict(
        status="ok", version=ver, grupos=len(sub_rutas),
        zonas=len({r["zona"] for r in sub_rutas}),
        sucursales=sum(len(r.get("sucursales", [])) for r in sub_rutas))
```

- [ ] **Step 2: Verificar que el módulo importa sin errores de sintaxis**

```bash
python -c "from logic.plantilla_canonica import cargar_zonas_manual, derivar_grupo_zona; print('ok')"
```
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add logic/plantilla_canonica.py
git commit -m "feat: cargar_zonas_manual -- version nueva de plantilla_grupo sin Excel"
```

---

## Task 4: `obtener_grupos()` expone `zona`

**Files:**
- Modify: `logic/plantilla_canonica.py:534-545` (función `obtener_grupos`)
- Modify: `tests/test_plantilla_canonica.py:144-161` (`test_roundtrip_lectura_bd`)

- [ ] **Step 1: Agregar `zona` al dict de salida**

En `logic/plantilla_canonica.py`, dentro de `obtener_grupos()`, el bloque
`out.append(dict(...))` queda así (agrega la línea `zona=r.get("zona"),`):

```python
    out = []
    for r in db.execute(sg).mappings():
        g = int(r["grupo"])
        out.append(dict(grupo=g, zona=r.get("zona"), rigidez=r["rigidez"], dia=r["dia"],
                        tam=r["tam"], cohesion=r["cohesion"], unidad_ref=r["unidad_ref"],
                        unidades_afines=r.get("unidades_afines"),
                        unidad_forzada=bool(r.get("unidad_forzada")),
                        que_hace_vrp=r["que_hace_vrp"],
                        sucursales=sorted(miembros.get(g, [])),
                        dias_admisibles=[d for _, d, _ in sorted(dias.get(g, []))],
                        dia_preferido=preferido.get(g, r["dia"])))
    return sorted(out, key=lambda g: g["grupo"])
```

- [ ] **Step 2: Verificar contra la BD (versión 17 vigente, `zona` aún NULL)**

```bash
python -c "
from app import create_app
app = create_app()
with app.app_context():
    from logic.plantilla_canonica import obtener_grupos
    g = obtener_grupos()[0]
    print('zona' in g, g['zona'])
"
```
Expected: `True None` -- el campo existe, todavía en NULL porque la
migración de zonas (Task 8) no ha corrido.

- [ ] **Step 3: Commit**

```bash
git add logic/plantilla_canonica.py
git commit -m "feat: obtener_grupos expone el campo zona"
```

(La prueba `test_roundtrip_lectura_bd` se actualiza en la Task 9, después de
que la migración real haya corrido -- hoy sigue viendo la versión 17 con 42
grupos y pasa sin cambios.)

---

## Task 5: Script `scripts/reorganizar_zonas_2026.py`

**Files:**
- Create: `scripts/reorganizar_zonas_2026.py`
- Test: `tests/test_plantilla_canonica.py`

- [ ] **Step 1: Escribir los tests que fallan (datos estáticos, sin BD)**

Agrega al final de `tests/test_plantilla_canonica.py`:

```python
# ── datos estaticos de la reorganizacion de zonas 2026-08 (sin BD) ─────────
def test_zonas_cubren_101_sucursales_sin_duplicados():
    from scripts.reorganizar_zonas_2026 import ZONAS_SIMPLES, SUB_RUTAS_ESPECIALES
    todos = [s for sucs in ZONAS_SIMPLES.values() for s in sucs]
    todos += [s for r in SUB_RUTAS_ESPECIALES for s in r["sucursales"]]
    assert len(todos) == 101
    assert len(set(todos)) == 101


def test_solo_zona_22_supera_6_sucursales():
    from scripts.reorganizar_zonas_2026 import ZONAS_SIMPLES, SUB_RUTAS_ESPECIALES
    for zona, sucs in ZONAS_SIMPLES.items():
        limite = 8 if zona == 22 else 6
        assert len(sucs) <= limite, f"zona {zona} tiene {len(sucs)} sucursales"
    for r in SUB_RUTAS_ESPECIALES:
        assert len(r["sucursales"]) <= 6, r


def test_sub_rutas_especiales_grupo_y_zona():
    from scripts.reorganizar_zonas_2026 import SUB_RUTAS_ESPECIALES
    por_grupo = {r["grupo"]: r["zona"] for r in SUB_RUTAS_ESPECIALES}
    assert por_grupo == {5: 5, 25: 5, 26: 5, 11: 11, 27: 11}


def test_construir_sub_rutas_agrega_24_zonas():
    from scripts.reorganizar_zonas_2026 import ZONAS_SIMPLES, SUB_RUTAS_ESPECIALES
    zonas = set(ZONAS_SIMPLES) | {r["zona"] for r in SUB_RUTAS_ESPECIALES}
    assert zonas == set(range(1, 25))
    grupos_simples = set(ZONAS_SIMPLES)          # grupo == zona para las simples
    grupos_especiales = {r["grupo"] for r in SUB_RUTAS_ESPECIALES}
    assert len(grupos_simples) + len(grupos_especiales) == 27
    assert not (grupos_simples & grupos_especiales)   # sin colision de numero
```

- [ ] **Step 2: Correr los tests, verificar que fallan**

```bash
python -m pytest tests/test_plantilla_canonica.py -k "zonas_cubren or zona_22 or sub_rutas_especiales or construir_sub_rutas" -v
```
Expected: `ModuleNotFoundError: No module named 'scripts.reorganizar_zonas_2026'`
en las 4 pruebas.

- [ ] **Step 3: Crear el script**

Crea `scripts/reorganizar_zonas_2026.py`:

```python
"""
reorganizar_zonas_2026.py

Reorganiza los `grupos` LORES canónicos (42, versión 17) en las 24 zonas de
negocio dadas por los jefes de prácticas en agosto 2026. Reemplaza
plantilla_grupo/plantilla_grupo_sucursal/plantilla_grupo_dia con una versión
nueva; NO toca plantilla_zona_mayorista/plantilla_poblacion_zona/
plantilla_bridge_sucursal.

Ver docs/superpowers/specs/2026-08-26-reorganizacion-zonas-canonicas-design.md
para la resolución de nombres, la evidencia histórica y la excepción de la
Zona 22 (8 sucursales en un solo grupo, pese al límite general de 6).

PENDIENTE (no lo resuelve este script): los CSV
datos/dias_admisibles_por_grupo.csv, datos/unidad_ref_por_grupo.csv y
datos/grupos_unidad_forzada.csv siguen referenciando la numeración VIEJA de
42 grupos. Son el input por defecto de `cargar_plantilla_desde_excel`
(scripts/cargar_plantilla.py). Si alguien vuelve a correr ese script con un
Excel canónico nuevo sin migrar antes esos 3 CSV a la numeración de zonas,
pisaría esta reorganización.

Uso:
    python scripts/reorganizar_zonas_2026.py --dry-run   # solo muestra
    python scripts/reorganizar_zonas_2026.py              # escribe la version nueva
"""
import sys, os, argparse
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import create_app
from logic.plantilla_canonica import obtener_grupos, cargar_zonas_manual, derivar_grupo_zona

# 22 zonas que quedan como una sola ruta (num_tienda). La Zona 22 tiene 8
# sucursales -- excepcion de negocio confirmada, no se parte (ver spec).
ZONAS_SIMPLES = {
    1:  [4, 27, 75, 86, 49, 100],
    2:  [92, 13, 3, 73, 85],
    3:  [5, 37, 12],
    4:  [64, 65, 70, 18],
    6:  [11, 93, 42],
    7:  [35, 97],
    8:  [33, 19],
    9:  [6, 10, 69, 79, 89],
    10: [14, 47],
    12: [23, 20, 58],
    13: [59, 9],
    14: [17, 26, 34],
    15: [56, 95, 51, 50],
    16: [87, 62, 66, 8],
    17: [67, 72, 99, 71],
    18: [94, 80, 83, 60, 41],
    19: [81, 52, 53, 40],
    20: [82, 61, 48, 43, 44],
    21: [21, 68, 16, 88, 22],
    22: [39, 45, 90, 91, 96, 98, 32, 78],
    23: [29, 28, 30],
    24: [84],
}

# Zona 5 (Tuxtepec) y Zona 11 (Tierra Blanca) superan el limite de 6
# sucursales/dia: se parten en sub-rutas fijas, tomadas del historico real
# (ver spec) en vez de la regla generica de derivar_grupo_zona.
SUB_RUTAS_ESPECIALES = [
    dict(grupo=5, zona=5, rigidez="FLEXIBLE", dia="MARTES",
         dias_admisibles=["MARTES", "MIERCOLES"], unidad_ref="F 350_2",
         unidad_forzada=True, sucursales=[2, 7, 15, 31, 54, 55]),
    dict(grupo=25, zona=5, rigidez="RIGIDO", dia="JUEVES",
         dias_admisibles=["JUEVES"], unidad_ref="F 350_2",
         unidad_forzada=False, sucursales=[38, 46, 57]),
    dict(grupo=26, zona=5, rigidez="FLEXIBLE", dia="MARTES",
         dias_admisibles=["MARTES", "JUEVES", "MIERCOLES"], unidad_ref="K 16",
         unidad_forzada=False, sucursales=[74]),
    dict(grupo=11, zona=11, rigidez="FLEXIBLE", dia="LUNES",
         dias_admisibles=["LUNES"], unidad_ref=None,
         unidad_forzada=False, sucursales=[1, 24, 25, 36]),
    dict(grupo=27, zona=11, rigidez="FLEXIBLE", dia="LUNES",
         dias_admisibles=["LUNES"], unidad_ref=None,
         unidad_forzada=False, sucursales=[63, 76, 77, 101]),
]


def construir_sub_rutas():
    """Arma las 27 sub-rutas: 22 derivadas del grupo viejo con mas peso +
    5 especiales (Zona 5 y 11). Devuelve (sub_rutas, alertas_revisar)."""
    grupos_viejos = obtener_grupos()
    grupos_por_id = {g["grupo"]: g for g in grupos_viejos}
    grupo_de_sucursal = {s: g["grupo"] for g in grupos_viejos for s in g["sucursales"]}

    sub_rutas = list(SUB_RUTAS_ESPECIALES)
    revisar = []
    for zona, sucursales in ZONAS_SIMPLES.items():
        d = derivar_grupo_zona(sucursales, grupo_de_sucursal, grupos_por_id)
        sub_rutas.append(dict(
            grupo=zona, zona=zona, rigidez=d["rigidez"], dia=d["dia"],
            dia_preferido=d["dia_preferido"], dias_admisibles=d["dias_admisibles"],
            unidad_ref=d["unidad_ref"], unidades_afines=d["unidades_afines"],
            unidad_forzada=d["unidad_forzada"], sucursales=sucursales))
        if d["revisar"]:
            revisar.append((zona, d["grupo_origen"], d["pct"]))
    return sub_rutas, revisar


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--nota", default="Reorganizacion de zonas canonicas 2026-08 "
                                      "(24 zonas de negocio, ver spec)")
    a = ap.parse_args()

    app = create_app()
    with app.app_context():
        sub_rutas, revisar = construir_sub_rutas()

        todas = [s for r in sub_rutas for s in r["sucursales"]]
        dups = sorted({s for s in todas if todas.count(s) > 1})

        print(f"{len(sub_rutas)} grupos, {len({r['zona'] for r in sub_rutas})} zonas, "
              f"{len(todas)} sucursales ({len(set(todas))} unicas)")
        if dups:
            print("ABORTADO: sucursales duplicadas entre grupos:", dups)
            return 1
        for zona, grupo_origen, pct in revisar:
            print(f"  REVISAR zona {zona}: hereda de grupo {grupo_origen} solo al {pct:.0%}")

        for r in sorted(sub_rutas, key=lambda r: (r["zona"], r["grupo"])):
            print(f"  grupo {r['grupo']:>3} zona {r['zona']:>2}  {r['rigidez']:<8} "
                  f"{str(r['dia']):<10} {str(r['unidad_ref']):<10} "
                  f"forzada={r['unidad_forzada']}  sucursales={r['sucursales']}")

        if a.dry_run:
            print("\n--dry-run: no se escribio nada en la BD.")
            return 0

        rep = cargar_zonas_manual(sub_rutas, nota=a.nota)
        print(f"\nOK version={rep['version']}  grupos={rep['grupos']}  "
              f"zonas={rep['zonas']}  sucursales={rep['sucursales']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Correr los tests, verificar que pasan**

```bash
python -m pytest tests/test_plantilla_canonica.py -k "zonas_cubren or zona_22 or sub_rutas_especiales or construir_sub_rutas" -v
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/reorganizar_zonas_2026.py tests/test_plantilla_canonica.py
git commit -m "feat: script de reorganizacion de zonas 2026 (24 zonas, 27 sub-rutas)"
```

---

## Task 6: Revisar el dry-run contra la versión 17 vigente

**Files:** ninguno (verificación manual contra la BD real).

- [ ] **Step 1: Correr en modo dry-run**

```bash
cd /c/Users/carli/Documents/ICG/logistica_icg
python scripts/reorganizar_zonas_2026.py --dry-run
```

Expected (primera línea):
```
27 grupos, 24 zonas, 101 sucursales (101 unicas)
```
Sin línea `ABORTADO`. Puede aparecer una línea `REVISAR zona 17: hereda de
grupo 18 solo al 50%` (documentada y esperada, ver spec) — ninguna otra.

- [ ] **Step 2: Comparar contra la tabla del spec**

Abre `docs/superpowers/specs/2026-08-26-reorganizacion-zonas-canonicas-design.md`
y compara al menos 5 filas de la salida (grupo, zona, rigidez, día,
unidad_ref) contra la tabla "Derivación de rigidez / día / unidad_ref" y la
tabla de la sección "Persistencia" del spec. Deben coincidir exactamente.

- [ ] **Step 3: Si algo no coincide, corregir `ZONAS_SIMPLES` o
      `SUB_RUTAS_ESPECIALES` en `scripts/reorganizar_zonas_2026.py` y repetir
      Step 1.** (No hay código nuevo que escribir aquí a priori — es un
      checkpoint de verificación, no una implementación.)

- [ ] **Step 4: Confirmar que el motor tolera `unidad_ref=None`**

El spec deja pendiente confirmar que las sub-rutas 11 y 27 (Tierra Blanca,
sin unidad de referencia) no rompen el reparto. Ya está confirmado por
lectura de código -- deja esto como constancia, no hace falta investigar de
nuevo:

```bash
grep -n "unidad_ref = g.get" logic/convrp_logic.py
```
Expected:
```
668:        unidad_ref = g.get("unidad_ref")
```
Y la línea siguiente (669) hace
`unidad = unidad_ref if unidad_ref in vehiculos_cap else (sorted(vehiculos_cap)[0] if vehiculos_cap else "VEHICULO")`
-- con `unidad_ref=None`, `None in vehiculos_cap` es `False`, así que cae al
fallback (primera unidad disponible en orden alfabético) sin lanzar
excepción. Confirmado seguro; no se requiere cambio en `convrp_logic.py`.

No hay commit en esta tarea (no se modificó código, salvo que el Step 3
haya corregido algo — en ese caso, commitea igual que en la Task 5).

---

## Task 7: Ejecutar la migración real

**Files:** ninguno (escribe en la BD real).

- [ ] **Step 1: Correr el script sin `--dry-run`**

```bash
cd /c/Users/carli/Documents/ICG/logistica_icg
python scripts/reorganizar_zonas_2026.py
```

Expected (última línea):
```
OK version=18  grupos=27  zonas=24  sucursales=101
```
(La versión puede no ser exactamente 18 si corrieron más cargas desde que
se escribió este plan -- usa el número real que imprima.)

- [ ] **Step 2: Verificar la versión vigente**

```bash
python -c "
from app import create_app
app = create_app()
with app.app_context():
    from logic.plantilla_canonica import version_vigente, obtener_grupos
    v = version_vigente()
    grupos = obtener_grupos()
    print('version vigente:', v)
    print('total grupos:', len(grupos))
    print('zonas distintas:', len({g[\"zona\"] for g in grupos}))
    print('RIGIDO:', sum(1 for g in grupos if g['rigidez'] == 'RIGIDO'))
    print('FLEXIBLE:', sum(1 for g in grupos if g['rigidez'] == 'FLEXIBLE'))
"
```
Expected:
```
version vigente: 18
total grupos: 27
zonas distintas: 24
RIGIDO: 18
FLEXIBLE: 9
```
(Usa los conteos RIGIDO/FLEXIBLE reales que imprima -- si difieren de 18/9,
son los correctos: úsalos tal cual en la Task 8, Step 1.)

No hay commit en esta tarea (no se modificó código; es un cambio de datos en
la BD).

---

## Task 8: Actualizar `test_roundtrip_lectura_bd` a la versión nueva

**Files:**
- Modify: `tests/test_plantilla_canonica.py:144-161`

- [ ] **Step 1: Actualizar los conteos esperados**

Reemplaza el cuerpo de `test_roundtrip_lectura_bd` (usa los conteos reales
observados en la Task 7, Step 2 -- 27/18/9 si no difirieron):

```python
def test_roundtrip_lectura_bd(app_ctx):
    from logic.plantilla_canonica import (
        version_vigente, obtener_grupos, grupo_de_sucursal, zona_de_poblacion,
    )
    assert version_vigente() is not None
    grupos = obtener_grupos()
    assert len(grupos) == 27
    assert sum(1 for g in grupos if g["rigidez"] == "RIGIDO") == 18
    assert sum(1 for g in grupos if g["rigidez"] == "FLEXIBLE") == 9
    # zona: 24 zonas de negocio distintas, todo grupo tiene una asignada
    assert len({g["zona"] for g in grupos}) == 24
    assert all(g["zona"] is not None for g in grupos)
    # Zona 22 es la unica excepcion al limite de 6 sucursales/dia
    por_zona = {}
    for g in grupos:
        por_zona.setdefault(g["zona"], 0)
        por_zona[g["zona"]] += len(g["sucursales"])
    assert por_zona[22] == 8
    assert all(n <= 6 for z, n in por_zona.items() if z != 22)
    # grupo_de_sucursal round-trip sobre un miembro real
    algun = next(g for g in grupos if g["sucursales"])
    nt = algun["sucursales"][0]
    assert grupo_de_sucursal(nt)["grupo"] == algun["grupo"]
    # población desconocida → None (cae a fallback global, no se adivina)
    assert zona_de_poblacion("POBLACION_QUE_NO_EXISTE_XYZ") is None
    # días admisibles: cada grupo tiene ≥1 y el preferido está en el set
    for g in grupos:
        assert g["dias_admisibles"], f"grupo {g['grupo']} sin días admisibles"
        assert g["dia_preferido"] in g["dias_admisibles"]
```

- [ ] **Step 2: Correr toda la suite de `test_plantilla_canonica.py`**

```bash
python -m pytest tests/test_plantilla_canonica.py -v
```
Expected: todas las pruebas pasan (las que dependen de BD no se saltan,
porque ya confirmamos que hay conexión local).

- [ ] **Step 3: Commit**

```bash
git add tests/test_plantilla_canonica.py
git commit -m "test: actualizar roundtrip a la version 27-grupos/24-zonas"
```

---

## Task 9: Refrescar el núcleo de las zonas de mayoristas

Los `grupo_nucleo` guardados en `plantilla_zona_mayorista` (enganche de
clientes mayoristas, Fase 3) todavía apuntan a la numeración VIEJA de
grupos (1-42). Con la renumeración de la Task 7, esos números ya no
existen en el `plantilla_grupo` vigente -- el enganche por "NUCLEO"
(`resolver_destino_enganche` en `logic/enganche_zona.py`) dejaría de
encontrar ruta para casi todas las zonas de mayoristas hasta que se
recalculen. `scripts/recalcular_zonas.py` ya existe exactamente para esto
(se documenta en su propio docstring: "Hay que correrlo después de cada
`cargar_plantilla.py`") -- aplica igual después de esta carga manual.

**Files:** ninguno (script ya existe, sólo se ejecuta).

- [ ] **Step 1: Correr `recalcular_zonas.py`**

```bash
cd /c/Users/carli/Documents/ICG/logistica_icg
python scripts/recalcular_zonas.py
```

Expected: imprime algo como
```
plantilla v18: N zonas vigentes
  clientes con zona resuelta : N
  zonas con evidencia        : N
  zonas con grupo núcleo     : N
  confianza                  : {...}
```
sin `ABORTADO`. Si aparece `ABORTADO: ningún cliente resolvió zona` o
`ABORTADO: la plantilla vigente no tiene sucursales por grupo`, algo salió
mal en la Task 7 -- revisa antes de seguir.

- [ ] **Step 2: Confirmar que los núcleos ahora usan la numeración 1-27**

```bash
python -c "
from app import create_app
app = create_app()
with app.app_context():
    from db import get_db, get_table
    from sqlalchemy import select
    db = get_db()
    t = get_table('plantilla_zona_mayorista')
    nucleos = {r.grupo_nucleo for r in db.execute(
        select(t.c.grupo_nucleo).where(t.c.vigente == 1)) if r.grupo_nucleo is not None}
    fuera_de_rango = sorted(n for n in nucleos if not (1 <= n <= 27))
    print('nucleos:', sorted(nucleos))
    print('fuera de rango (deberian ser 0):', fuera_de_rango)
"
```
Expected: `fuera de rango (deberian ser 0): []`

No hay commit en esta tarea (no se modificó código; es un refresco de datos
derivados en la BD, ya cubierto por el script existente).

---

## Task 10: Documentar el riesgo de recarga futura por Excel

**Files:**
- Modify: `logic/plantilla_canonica.py:1-22` (docstring del módulo) y
  `scripts/cargar_plantilla.py:1-13` (docstring del script)

- [ ] **Step 1: Agregar la advertencia al docstring del módulo**

En `logic/plantilla_canonica.py`, al final del docstring del módulo (antes
de `import os`, alrededor de la línea 21-22), agrega un párrafo:

```python
NOTA IMPORTANTE 2026-08 — reorganización de zonas: `cargar_plantilla_desde_excel`
sigue usando la numeración VIEJA de 42 `grupo` (datos/dias_admisibles_por_grupo.csv,
datos/unidad_ref_por_grupo.csv, datos/grupos_unidad_forzada.csv). Los 27
`grupo` vigentes desde la reorganización manual (scripts/reorganizar_zonas_2026.py,
ver docs/superpowers/specs/2026-08-26-reorganizacion-zonas-canonicas-design.md)
usan una numeración nueva de 24 zonas. Si se vuelve a cargar un Excel canónico
con `cargar_plantilla_desde_excel` sin migrar antes esos 3 CSV a la numeración
de zonas, se pisa esta reorganización sin aviso.
"""
```

(Este párrafo se agrega DENTRO del docstring existente del módulo, antes del
`"""` de cierre.)

- [ ] **Step 2: Agregar la misma advertencia al script de carga por Excel**

En `scripts/cargar_plantilla.py`, dentro del docstring del módulo (líneas
1-13), agrega antes de la sección "Uso:":

```
ADVERTENCIA 2026-08: los CSV por defecto (datos/dias_admisibles_por_grupo.csv,
datos/unidad_ref_por_grupo.csv, datos/grupos_unidad_forzada.csv) usan la
numeración VIEJA de 42 grupos. Desde la reorganización de zonas
(scripts/reorganizar_zonas_2026.py) la plantilla vigente usa 27 grupos bajo
una numeración de 24 zonas. Correr este script sin migrar antes esos 3 CSV
pisaría la reorganización sin aviso.
```

- [ ] **Step 3: Commit**

```bash
git add logic/plantilla_canonica.py scripts/cargar_plantilla.py
git commit -m "docs: advertir sobre numeracion vieja en CSVs de calibracion tras la reorganizacion de zonas"
```

---

## Task 11: Suite completa y cierre

**Files:** ninguno.

- [ ] **Step 1: Correr toda la suite de pruebas del proyecto relacionadas**

```bash
cd /c/Users/carli/Documents/ICG/logistica_icg
python -m pytest tests/test_plantilla_canonica.py tests/test_enganche_zona.py tests/test_convrp_logic.py tests/test_convrp_validacion.py tests/test_convrp_integracion.py -v
```
Expected: todas pasan. Si `test_convrp_logic.py` o `test_convrp_validacion.py`
tienen fixtures que hardcodean números de `grupo` de la plantilla vigente
(no de fixtures propias), revísalos -- la mayoría de las pruebas de ese
módulo usan plantillas de prueba propias (`plantilla = [...]` inline) y no
deberían verse afectadas por el cambio de versión vigente.

- [ ] **Step 2: Confirmar el estado de git**

```bash
git log --oneline -12
git status --short
```
Expected: 8-9 commits de esta implementación (Tasks 1,2,3,4,5,8,10), árbol de
trabajo limpio salvo archivos no relacionados con esta tarea que ya estaban
modificados antes de empezar.
