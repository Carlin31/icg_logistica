# Grupos exclusivos en ConVRP — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Marcar los grupos 4 (Zona 4), 24 (Zona 24/Amatlán) y 25 (sub-grupo
jueves de Tuxtepec) como `exclusivo` en la plantilla canónica, y hacer que
ConVRP nunca los combine con otro grupo en el mismo camión — incluso cuando
el peso combinado cabría cómodo — prefiriendo otro día admisible antes que
aceptar un camión más grande de lo necesario.

**Architecture:** Un flag nuevo (`exclusivo`) por grupo en `plantilla_grupo`,
una función pura compartida (`_respeta_exclusividad`) aplicada en los 4
puntos de `logic/convrp_logic.py` donde el motor podría juntar grupos, y una
pasada nueva (`_asignar_exclusivos`) que corre antes que la asignación
general por peso y fija día/unidad de los grupos exclusivos eligiendo,
entre todos sus días admisibles, la combinación de menor capacidad de
camión.

**Tech Stack:** Python puro (sin dependencias nuevas), SQLAlchemy (ALTER
idempotente sobre SQL Server), pytest.

Spec: `docs/superpowers/specs/2026-08-28-grupos-exclusivos-convrp-design.md`

---

### Task 1: Columna `exclusivo` en `plantilla_grupo` + script de marcado

**Files:**
- Modify: `scripts/crear_plantilla_canonica.py:36-40`
- Create: `scripts/marcar_grupos_exclusivos.py`

- [ ] **Step 1: Agregar la columna al arreglo `ALTERS`**

En `scripts/crear_plantilla_canonica.py`, el arreglo `ALTERS` (línea 36) es:

```python
ALTERS = [
    ("plantilla_grupo", "unidades_afines", "NVARCHAR(400) NULL"),
    ("plantilla_grupo", "zona", "INT NULL"),
    ("plantilla_grupo", "unidades_excluidas", "NVARCHAR(200) NULL"),
]
```

Cámbialo a:

```python
ALTERS = [
    ("plantilla_grupo", "unidades_afines", "NVARCHAR(400) NULL"),
    ("plantilla_grupo", "zona", "INT NULL"),
    ("plantilla_grupo", "unidades_excluidas", "NVARCHAR(200) NULL"),
    ("plantilla_grupo", "exclusivo", "BIT NOT NULL DEFAULT 0"),
]
```

- [ ] **Step 2: Correr el script contra la BD real (idempotente)**

Run: `python scripts/crear_plantilla_canonica.py`
Expected: la salida incluye `plantilla_grupo.exclusivo` en la lista de
"Columnas nuevas". Si ya se corrió antes, dirá "(ninguna)" — también es
correcto (idempotente).

- [ ] **Step 3: Crear el script de marcado**

Crea `scripts/marcar_grupos_exclusivos.py`:

```python
"""
marcar_grupos_exclusivos.py

Marca exclusivo=1 en los grupos que nunca deben compartir camión con otro
grupo, aunque el peso combinado quepa. Corrige la plantilla VIGENTE in
place -- no crea una versión nueva, mismo criterio ya usado para cargar
grupos_unidad_forzada.csv sobre `unidad_forzada`.

Ver docs/superpowers/specs/2026-08-28-grupos-exclusivos-convrp-design.md.

Uso:
    python scripts/marcar_grupos_exclusivos.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy import update
from app import create_app
from db import get_table, transaccion

# 4  = Zona 4 (Chacaltianguis, Tlacojalpan, Otatitlán, Papaloapan)
# 24 = Zona 24 (Amatlán) -- casi siempre acompañada de mayoristas
# 25 = sub-grupo jueves de Tuxtepec (Tuxtepec 5, 6, 8)
GRUPOS_EXCLUSIVOS = [4, 24, 25]


def main():
    app = create_app()
    with app.app_context():
        t = get_table("plantilla_grupo")
        with transaccion() as conn:
            for grupo in GRUPOS_EXCLUSIVOS:
                res = conn.execute(
                    update(t).where(t.c.grupo == grupo, t.c.vigente == 1)
                    .values(exclusivo=1))
                print(f"grupo {grupo}: {res.rowcount} fila(s) marcada(s) exclusivo=1")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Correr el script y verificar**

Run: `python scripts/marcar_grupos_exclusivos.py`
Expected: tres líneas, cada una con "1 fila(s) marcada(s) exclusivo=1".

Verificar con una consulta directa:

```bash
python - <<'EOF'
from app import create_app
app = create_app()
with app.app_context():
    from sqlalchemy import select
    from db import get_db, get_table
    db = get_db()
    t = get_table("plantilla_grupo")
    for r in db.execute(select(t.c.grupo, t.c.zona, t.c.exclusivo)
                        .where(t.c.vigente == 1)).mappings():
        if r["exclusivo"]:
            print(dict(r))
EOF
```

Expected: exactamente 3 filas impresas, grupos 4, 24 y 25, todas con
`exclusivo=True` (o `1`).

- [ ] **Step 5: Commit**

```bash
git add scripts/crear_plantilla_canonica.py scripts/marcar_grupos_exclusivos.py
git commit -m "Agrega columna exclusivo a plantilla_grupo y marca grupos 4/24/25"
```

---

### Task 2: Propagar el campo `exclusivo` hasta `construir_groups_desde_plantilla`

**Files:**
- Modify: `logic/plantilla_canonica.py:659` (dentro de `obtener_grupos`)
- Modify: `logic/convrp_logic.py:760` (dentro de `construir_groups_desde_plantilla`)

- [ ] **Step 1: `obtener_grupos()` debe devolver `exclusivo`**

En `logic/plantilla_canonica.py`, dentro de `obtener_grupos()`, el
`dict(...)` que arma cada grupo (línea 653-663) es:

```python
        out.append(dict(grupo=g, zona=r.get("zona"), rigidez=r["rigidez"], dia=r["dia"],
                        tam=r["tam"], cohesion=r["cohesion"], unidad_ref=r["unidad_ref"],
                        unidades_afines=r.get("unidades_afines"),
                        unidades_excluidas=[u.strip() for u in
                            str(r.get("unidades_excluidas") or "").split("|")
                            if u.strip()],
                        unidad_forzada=bool(r.get("unidad_forzada")),
                        que_hace_vrp=r["que_hace_vrp"],
                        sucursales=sorted(miembros.get(g, [])),
                        dias_admisibles=[d for _, d, _ in sorted(dias.get(g, []))],
                        dia_preferido=preferido.get(g, r["dia"])))
```

Agrega `exclusivo=bool(r.get("exclusivo")),` justo después de la línea de
`unidad_forzada`:

```python
        out.append(dict(grupo=g, zona=r.get("zona"), rigidez=r["rigidez"], dia=r["dia"],
                        tam=r["tam"], cohesion=r["cohesion"], unidad_ref=r["unidad_ref"],
                        unidades_afines=r.get("unidades_afines"),
                        unidades_excluidas=[u.strip() for u in
                            str(r.get("unidades_excluidas") or "").split("|")
                            if u.strip()],
                        unidad_forzada=bool(r.get("unidad_forzada")),
                        exclusivo=bool(r.get("exclusivo")),
                        que_hace_vrp=r["que_hace_vrp"],
                        sucursales=sorted(miembros.get(g, [])),
                        dias_admisibles=[d for _, d, _ in sorted(dias.get(g, []))],
                        dia_preferido=preferido.get(g, r["dia"])))
```

- [ ] **Step 2: `construir_groups_desde_plantilla` debe copiar `exclusivo` a `asign`**

En `logic/convrp_logic.py`, dentro de `construir_groups_desde_plantilla()`,
el bloque que arma `asign[...]` (línea 756-761) es:

```python
        asign[int(g["grupo"])] = dict(
            grupo=int(g["grupo"]), rigidez=str(g.get("rigidez", "")).upper(),
            unidad=unidad, unidad_ref=unidad_ref, dia=dia, dia_preferido=dia,
            dias_admisibles=adm, miembros=activos,
            unidad_forzada=bool(g.get("unidad_forzada")),
            unidades_excluidas=list(g.get("unidades_excluidas") or []))
```

Agrega `exclusivo=bool(g.get("exclusivo")),`:

```python
        asign[int(g["grupo"])] = dict(
            grupo=int(g["grupo"]), rigidez=str(g.get("rigidez", "")).upper(),
            unidad=unidad, unidad_ref=unidad_ref, dia=dia, dia_preferido=dia,
            dias_admisibles=adm, miembros=activos,
            unidad_forzada=bool(g.get("unidad_forzada")),
            exclusivo=bool(g.get("exclusivo")),
            unidades_excluidas=list(g.get("unidades_excluidas") or []))
```

- [ ] **Step 3: El pedazo separado al partir (Palanca 3) también debe heredar `exclusivo`**

Un grupo exclusivo que no cabe en ninguna unidad (caso raro con los 3
grupos actuales, pero posible) se parte igual que cualquier otro — ver
Palanca 3 en `construir_groups_desde_plantilla`. El pedazo separado se
reubica llamando a `_unidad_alternativa`/`_dia_alternativo` sobre una copia
temporal (`sub = dict(a, grupo=a["grupo"], miembros=sorted(separadas))`,
que sí copia `exclusivo` porque parte de `dict(a, ...)`), pero la entrada
que de verdad queda **persistida** en `asign` se arma aparte, clave por
clave, y hoy no incluye `exclusivo` — si no se agrega, el pedazo perdería
la protección en cualquier pasada posterior que vuelva a mirar `asign`
(Palancas 4/5).

El bloque (línea 880-887) es:

```python
            clave = max(asign) + 1
            asign[clave] = dict(
                grupo=a["grupo"], rigidez=a["rigidez"],
                unidad=(destino[0] if destino else unidad),
                unidad_ref=a["unidad_ref"], unidad_forzada=a.get("unidad_forzada", False),
                dia=(destino[1] if destino else dia), dia_preferido=a["dia_preferido"],
                dias_admisibles=a["dias_admisibles"], miembros=sorted(separadas),
                unidades_excluidas=list(a.get("unidades_excluidas") or []))
```

Cámbialo a:

```python
            clave = max(asign) + 1
            asign[clave] = dict(
                grupo=a["grupo"], rigidez=a["rigidez"],
                unidad=(destino[0] if destino else unidad),
                unidad_ref=a["unidad_ref"], unidad_forzada=a.get("unidad_forzada", False),
                exclusivo=a.get("exclusivo", False),
                dia=(destino[1] if destino else dia), dia_preferido=a["dia_preferido"],
                dias_admisibles=a["dias_admisibles"], miembros=sorted(separadas),
                unidades_excluidas=list(a.get("unidades_excluidas") or []))
```

- [ ] **Step 4: Verificar que nada se rompe**

Run: `python -m pytest tests/test_convrp_logic.py tests/test_plantilla_canonica.py -q`
Expected: todos los tests que ya existían siguen en verde (este cambio solo
agrega una clave nueva con default `False`; ningún test existente marca
`exclusivo`, así que el comportamiento no cambia para nadie todavía).

- [ ] **Step 5: Commit**

```bash
git add logic/plantilla_canonica.py logic/convrp_logic.py
git commit -m "Propaga el campo exclusivo desde la plantilla hasta ConVRP"
```

---

### Task 3: Función `_respeta_exclusividad()` + pruebas puras

**Files:**
- Modify: `logic/convrp_logic.py:257-260` (justo después de `_excluida`)
- Test: `tests/test_convrp_logic.py`

- [ ] **Step 1: Escribir las pruebas (van a fallar: la función no existe)**

Agrega al final de `tests/test_convrp_logic.py`:

```python
# ══ Grupos exclusivos: nunca comparten camión con otro grupo ═══════════════
def test_respeta_exclusividad_bloquea_unidad_ocupada_para_grupo_exclusivo():
    from logic.convrp_logic import _respeta_exclusividad
    asign = {1: {"grupo": 1, "unidad": "V1", "dia": "LUNES"},
             2: {"grupo": 2, "unidad": None, "dia": "LUNES", "exclusivo": True}}
    a = asign[2]
    assert _respeta_exclusividad(asign, a, "V1", "LUNES") is False, \
        "V1 ya tiene al grupo 1: un exclusivo no puede entrar"
    assert _respeta_exclusividad(asign, a, "V2", "LUNES") is True


def test_respeta_exclusividad_bloquea_sumarse_a_ruta_con_exclusivo():
    from logic.convrp_logic import _respeta_exclusividad
    asign = {1: {"grupo": 1, "unidad": "V1", "dia": "LUNES", "exclusivo": True},
             2: {"grupo": 2, "unidad": None, "dia": "LUNES"}}
    a = asign[2]
    assert _respeta_exclusividad(asign, a, "V1", "LUNES") is False, \
        "V1 ya tiene un grupo exclusivo: nadie más puede sumarse"
    assert _respeta_exclusividad(asign, a, "V2", "LUNES") is True


def test_respeta_exclusividad_no_bloquea_consolidacion_normal_entre_no_exclusivos():
    from logic.convrp_logic import _respeta_exclusividad
    asign = {1: {"grupo": 1, "unidad": "V1", "dia": "LUNES"},
             2: {"grupo": 2, "unidad": None, "dia": "LUNES"}}
    a = asign[2]
    assert _respeta_exclusividad(asign, a, "V1", "LUNES") is True
```

- [ ] **Step 2: Correr las pruebas y confirmar que fallan**

Run: `python -m pytest tests/test_convrp_logic.py -k respeta_exclusividad -q`
Expected: FAIL — `ImportError: cannot import name '_respeta_exclusividad'`

- [ ] **Step 3: Implementar la función**

En `logic/convrp_logic.py`, justo después de `_excluida` (línea 257-260):

```python
def _excluida(a, unidad) -> bool:
    """True si `unidad` está en las `unidades_excluidas` del grupo -- nunca
    es un destino válido para él, ni siquiera en el último recurso."""
    return unidad in (a.get("unidades_excluidas") or ())


def _respeta_exclusividad(asign, a, unidad, dia) -> bool:
    """
    True si `a` puede entrar a (unidad, dia) sin violar exclusividad:
      - si `a` es exclusivo, esa (unidad, dia) debe estar VACÍA (sin ningún
        otro grupo ya asignado ahí).
      - si `a` NO es exclusivo, esa (unidad, dia) no debe tener ya un grupo
        exclusivo (distinto de `a`).

    Grupos marcados `exclusivo` nunca comparten camión con otro grupo, sin
    importar cuánto margen de peso quede (ver
    docs/superpowers/specs/2026-08-28-grupos-exclusivos-convrp-design.md).
    """
    ocupantes = [g for g in asign
                if asign[g]["unidad"] == unidad and asign[g]["dia"] == dia
                and g != a["grupo"]]
    if a.get("exclusivo"):
        return not ocupantes
    return not any(asign[g].get("exclusivo") for g in ocupantes)
```

- [ ] **Step 4: Correr las pruebas y confirmar que pasan**

Run: `python -m pytest tests/test_convrp_logic.py -k respeta_exclusividad -q`
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add logic/convrp_logic.py tests/test_convrp_logic.py
git commit -m "Agrega _respeta_exclusividad: helper puro para grupos que nunca comparten camion"
```

---

### Task 4: Aplicar `_respeta_exclusividad` en los 4 puntos donde el motor junta grupos

**Files:**
- Modify: `logic/convrp_logic.py` (`_asignar_unidades`, `_dia_alternativo`, `_unidad_alternativa`, `_consolidar_solitarios`)

Este task es mecánico: agrega un filtro más, junto al ya existente
`_excluida()`, en cada uno de los 4 lugares. Con ningún grupo marcado
`exclusivo=True` todavía en ningún test existente, este cambio no debe
alterar ningún resultado — se verifica corriendo la suite completa al final.

- [ ] **Step 1: `_asignar_unidades`**

En `logic/convrp_logic.py`, dentro de `_asignar_unidades()`, la línea:

```python
            candidatas = [u for u in vehiculos_cap if not _excluida(a, u)]
```

Cámbiala a:

```python
            candidatas = [u for u in vehiculos_cap if not _excluida(a, u)
                         and _respeta_exclusividad(asign, a, u, dia)]
```

- [ ] **Step 2: `_dia_alternativo`**

En `_dia_alternativo()`, el bucle:

```python
            for unidad in candidatas:
                if exigir_compat and not _compatible_historico(
                        a["grupo"], unidad, dia, asign, coocurrencia):
                    continue
                destino = _sids_de_ruta(asign, unidad, dia) + list(a["miembros"])
```

Cámbialo a:

```python
            for unidad in candidatas:
                if not _respeta_exclusividad(asign, a, unidad, dia):
                    continue
                if exigir_compat and not _compatible_historico(
                        a["grupo"], unidad, dia, asign, coocurrencia):
                    continue
                destino = _sids_de_ruta(asign, unidad, dia) + list(a["miembros"])
```

- [ ] **Step 3: `_unidad_alternativa`**

En `_unidad_alternativa()`, el bucle:

```python
    for unidad in candidatas:
        if unidad == a["unidad"]:
            continue
        destino = _sids_de_ruta(asign, unidad, a["dia"]) + list(a["miembros"])
```

Cámbialo a:

```python
    for unidad in candidatas:
        if unidad == a["unidad"]:
            continue
        if not _respeta_exclusividad(asign, a, unidad, a["dia"]):
            continue
        destino = _sids_de_ruta(asign, unidad, a["dia"]) + list(a["miembros"])
```

- [ ] **Step 4: `_consolidar_solitarios`**

En `_consolidar_solitarios()`, el bloque:

```python
        gid = next(g for g in asign
                  if asign[g]["unidad"] == unidad and asign[g]["dia"] == dia)
        a = asign[gid]
        # candidatas: unidades YA ACTIVAS ese día (nunca abrir una vacía sólo
        # para esto), compatibles por historial, ordenadas por carga
        # descendente (consolidar en la más llena que todavía quepa).
        activas_ese_dia = sorted({u for (u, d) in _rutas_activas(asign)
                                  if d == dia and u != unidad
                                  and u != "SIN_UNIDAD"
                                  and not _excluida(a, u)})
        candidatas = [u for u in activas_ese_dia
                     if _compatible_historico(a["grupo"], u, dia, asign, coocurrencia)]
```

Cámbialo a:

```python
        gid = next(g for g in asign
                  if asign[g]["unidad"] == unidad and asign[g]["dia"] == dia)
        a = asign[gid]
        if a.get("exclusivo"):
            continue    # nunca se mueve a consolidarse en otra ruta
        # candidatas: unidades YA ACTIVAS ese día (nunca abrir una vacía sólo
        # para esto), compatibles por historial, ordenadas por carga
        # descendente (consolidar en la más llena que todavía quepa).
        activas_ese_dia = sorted({u for (u, d) in _rutas_activas(asign)
                                  if d == dia and u != unidad
                                  and u != "SIN_UNIDAD"
                                  and not _excluida(a, u)})
        candidatas = [u for u in activas_ese_dia
                     if _compatible_historico(a["grupo"], u, dia, asign, coocurrencia)
                     and _respeta_exclusividad(asign, a, u, dia)]
```

- [ ] **Step 5: Prueba dirigida al guard de `_consolidar_solitarios`**

Los 3 grupos de arriba son mecánicos y se verifican con la suite existente,
pero conviene una prueba dirigida a la Palanca 4 específicamente: un grupo
NO exclusivo que sea compatible por historial con un grupo exclusivo nunca
debe terminar consolidado en su ruta, ni siquiera cuando esa ruta tiene
cupo de sobra. Agrega a `tests/test_convrp_logic.py`:

```python
def test_consolidar_solitarios_nunca_ofrece_una_ruta_con_grupo_exclusivo():
    # grupo 3 (solitaria) es compatible por historial SOLO con el grupo 1
    # (exclusivo) -- sin el guard de exclusividad, Palanca 4 lo consolidaría
    # ahí (hay cupo de sobra y coocurrencia real). Debe quedar como aviso.
    plantilla = [
        _grupo(1, "FLEXIBLE", "LUNES", [1], unidad_ref=None),
        _grupo(2, "FLEXIBLE", "LUNES", [2, 3], unidad_ref=None),
        _grupo(3, "FLEXIBLE", "LUNES", [4], unidad_ref=None),
    ]
    plantilla[0]["exclusivo"] = True
    pedidos = {1: 100, 2: 200, 3: 200, 4: 100}
    caps = {"V1": 1000, "V2": 1000, "V3": 1000}
    coocurrencia = {frozenset((1, 3)): 1}     # el 3 solo coincidió antes con el 1
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, caps, {"V1": 99, "V2": 99, "V3": 99},
        _sin_tiempo(coocurrencia_grupos=coocurrencia))
    ruta_1 = next(k for k, ms in groups.items() if any(m["sid"] == 1 for m in ms))
    ruta_3 = next(k for k, ms in groups.items() if any(m["sid"] == 4 for m in ms))
    assert sorted(m["sid"] for m in groups[ruta_1]) == [1], \
        "el grupo exclusivo debe quedar solo en su ruta"
    assert ruta_3 != ruta_1, \
        "el grupo 3 nunca debió consolidarse en la ruta del grupo exclusivo"
    assert any(e["tipo"] == "AVISO_RUTA_SOLITARIA" for e in exc)
```

- [ ] **Step 6: Correr toda la suite de ConVRP**

Run: `python -m pytest tests/test_convrp_logic.py tests/test_convrp_integracion.py tests/test_convrp_validacion.py -q`
Expected: todos los tests anteriores siguen en verde (ningún test previo a
este task marca `exclusivo=True`, así que `_respeta_exclusividad` les
devuelve `True` siempre y no cambia nada) más la prueba nueva del Step 5.

- [ ] **Step 7: Commit**

```bash
git add logic/convrp_logic.py tests/test_convrp_logic.py
git commit -m "Aplica _respeta_exclusividad en los 4 puntos donde ConVRP junta grupos"
```

---

### Task 5: `_asignar_exclusivos` — fija día/unidad de los grupos exclusivos antes de la Palanca 1

**Files:**
- Modify: `logic/convrp_logic.py` (nueva función antes de `_asignar_unidades`, cambios dentro de `_asignar_unidades`, llamada nueva en `construir_groups_desde_plantilla`)
- Test: `tests/test_convrp_logic.py`

- [ ] **Step 1: Escribir las pruebas (van a fallar)**

Agrega al final de `tests/test_convrp_logic.py`:

```python
def test_dos_grupos_exclusivos_nunca_comparten_camion_aunque_el_peso_alcance():
    plantilla = [_grupo(1, "FLEXIBLE", "JUEVES", [1, 2], unidad_ref=None),
                 _grupo(2, "FLEXIBLE", "JUEVES", [3, 4], unidad_ref=None)]
    plantilla[0]["exclusivo"] = True
    plantilla[1]["exclusivo"] = True
    # 1000 + 1000 = 2000 kg: cabrían juntos en V_GRANDE (5000), pero al ser
    # ambos exclusivos deben terminar en camiones distintos.
    pedidos = {1: 500, 2: 500, 3: 500, 4: 500}
    caps = {"V_CHICA": 1200, "V_GRANDE": 5000}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, caps, {"V_CHICA": 99, "V_GRANDE": 99},
        _sin_tiempo())
    claves_con_paradas = [k for k, ms in groups.items() if ms]
    assert len(claves_con_paradas) == 2, \
        "cada grupo exclusivo debe quedar en su propia ruta"
    sids_por_clave = sorted(sorted(m["sid"] for m in groups[k])
                            for k in claves_con_paradas)
    assert sids_por_clave == [[1, 2], [3, 4]]


def test_exclusivo_prefiere_otro_dia_admisible_a_camion_grande_en_su_dia_preferido():
    # Dos grupos exclusivos, mismo día preferido, un solo camión chico libre
    # ese día. El grupo 2 (que también admite VIERNES) debe moverse ahí --
    # donde el camión chico SÍ está libre -- en vez de tomar GRANDE el jueves.
    plantilla = [
        _grupo(1, "FLEXIBLE", "JUEVES", [1], unidad_ref=None),
        _grupo(2, "FLEXIBLE", "JUEVES", [2], unidad_ref=None,
               dias_admisibles=["JUEVES", "VIERNES"]),
    ]
    plantilla[0]["exclusivo"] = True
    plantilla[1]["exclusivo"] = True
    pedidos = {1: 500, 2: 500}
    caps = {"CHICA": 1200, "GRANDE": 5000}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, caps, {"CHICA": 99, "GRANDE": 99},
        _sin_tiempo())
    assert sorted(m["sid"] for m in groups[("CHICA", "JUEVES")]) == [1]
    assert ("CHICA", "VIERNES") in groups, \
        "el grupo 2 debió moverse a VIERNES para tomar el camión chico libre"
    assert sorted(m["sid"] for m in groups[("CHICA", "VIERNES")]) == [2]
    assert ("GRANDE", "JUEVES") not in groups


def test_exclusivo_rigido_de_un_solo_dia_usa_camion_grande_si_no_hay_chico():
    # Sin otro día a donde moverse (rígido, un solo día admisible), si no
    # queda camión chico vacío debe usar el que le alcance -- sin fallar y
    # sin compartirlo con nadie.
    plantilla = [
        _grupo(1, "FLEXIBLE", "JUEVES", [1], unidad_ref=None),   # ocupa CHICA
        _grupo(2, "RIGIDO", "JUEVES", [2], unidad_ref=None,
               dias_admisibles=["JUEVES"]),
    ]
    plantilla[1]["exclusivo"] = True
    pedidos = {1: 500, 2: 500}
    caps = {"CHICA": 1200, "GRANDE": 5000}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, caps, {"CHICA": 99, "GRANDE": 99},
        _sin_tiempo())
    assert sorted(m["sid"] for m in groups[("GRANDE", "JUEVES")]) == [2]
    assert not any(e["tipo"] == "SIN_UNIDAD_DISPONIBLE" for e in exc)


def test_exclusivo_sin_unidad_disponible_si_la_exclusion_deja_la_flota_vacia():
    from logic.convrp_logic import _asignar_exclusivos
    asign = {1: {"grupo": 1, "unidad": None, "dia": "LUNES", "miembros": [1],
                "rigidez": "FLEXIBLE", "dias_admisibles": ["LUNES"],
                "exclusivo": True, "unidades_excluidas": ["UNICA"]}}
    pedidos = {1: 100}
    caps = {"UNICA": 5000}          # la única unidad de la flota, excluida
    exc = _asignar_exclusivos(asign, pedidos, {}, {}, caps, {}, _sin_tiempo())
    assert asign[1]["unidad"] == "SIN_UNIDAD"
    assert any(e["tipo"] == "SIN_UNIDAD_DISPONIBLE" for e in exc)
```

- [ ] **Step 2: Correr las pruebas y confirmar que fallan**

Run: `python -m pytest tests/test_convrp_logic.py -k exclusivo -q`
Expected: FAIL en las 4 nuevas — hoy los grupos exclusivos se consolidan
igual que cualquier otro (todavía no existe `_asignar_exclusivos`, y
`_respeta_exclusividad` del Task 4 solo actúa DESPUÉS de que alguien ya
esté asignado; sin una pasada que los procese primero, `_asignar_unidades`
los trata como grupos normales). El último test (`_sin_unidad_disponible`)
falla con `ImportError` porque `_asignar_exclusivos` todavía no existe.

- [ ] **Step 3: Implementar `_asignar_exclusivos`**

En `logic/convrp_logic.py`, justo antes de `_asignar_unidades` (línea 294):

```python
def _asignar_exclusivos(asign, pedidos, volumenes, coords, vehiculos_cap,
                        vehiculos_vol, cfg):
    """
    Corre ANTES que `_asignar_unidades` (Palanca 1): fija día y unidad de
    los grupos marcados `exclusivo` -- nunca comparten camión con otro
    grupo, sin importar cuánto margen de peso quede.

    Para cada uno (orden determinista: `grupo` ascendente), prueba TODOS
    sus `dias_admisibles` (preferido primero) y en cada uno busca la unidad
    VACÍA (sin ningún otro grupo asignado ese día -- ni siquiera de otro
    exclusivo ya procesado) de menor capacidad que lo admita sin violar
    restricciones. Entre las combinaciones encontradas en sus distintos
    días, se queda con la de MENOR capacidad de camión; empate por orden de
    `dias_admisibles`, luego por nombre de unidad.

    Si ningún día ofrece una unidad vacía viable (p. ej. un rígido de un
    solo día sin ninguna unidad libre que le alcance), cae al mismo
    criterio de último recurso que `_asignar_unidades`: la unidad no
    excluida y vacía con más espacio libre en su día preferido. Si ni eso
    hay (`unidades_excluidas` deja la flota entera afuera), registra
    SIN_UNIDAD_DISPONIBLE, igual que `_asignar_unidades`.

    Devuelve la lista de excepciones SIN_UNIDAD_DISPONIBLE.
    """
    excepciones: list = []
    for gid in sorted(g for g in asign if asign[g].get("exclusivo")):
        a = asign[gid]
        mejor = None   # (capacidad, idx_dia_admisible, unidad, dia)
        for idx, dia in enumerate(a["dias_admisibles"]):
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
        if mejor is not None:
            _, _, unidad, dia = mejor
            a["dia"] = dia
            a["unidad"] = unidad
            continue

        # último recurso: ninguna unidad vacía en ningún día admisible
        # admite el grupo completo -- se queda en su día preferido, en la
        # unidad no excluida y vacía con más espacio libre.
        dia = a["dia"]
        kg_may = cfg.get("kg_mayoristas") or {}

        def _libre(u):
            ocupado = sum(_num(pedidos.get(s)) + _num(kg_may.get(s))
                         for s in _sids_de_ruta(asign, u, dia))
            return _num(vehiculos_cap.get(u)) - ocupado

        candidatas = [u for u in vehiculos_cap if not _excluida(a, u)
                     and _respeta_exclusividad(asign, a, u, dia)]
        if not candidatas:
            a["unidad"] = "SIN_UNIDAD"
            excepciones.append({
                "tipo": "SIN_UNIDAD_DISPONIBLE", "grupo": a["grupo"],
                "rigidez": a["rigidez"], "dia": dia,
                "motivo": f"ninguna unidad no excluida y vacía disponible "
                          f"para el grupo exclusivo {a['grupo']} el {dia}",
            })
            continue
        a["unidad"] = min(candidatas, key=lambda u: (-_libre(u), str(u)))
    return excepciones
```

- [ ] **Step 4: Llamarla antes de la Palanca 1, en `construir_groups_desde_plantilla`**

El bloque (línea 763-765):

```python
    # ── 2. Palanca 1: repartir en la flota por peso ──
    desviaciones = _asignar_unidades(asign, pedidos, volumenes, coords,
                                     vehiculos_cap, vehiculos_vol, cfg)
```

Cámbialo a:

```python
    # ── 1.5. Palanca 0: fijar día/unidad de los grupos exclusivos ──
    excepciones += _asignar_exclusivos(asign, pedidos, volumenes, coords,
                                       vehiculos_cap, vehiculos_vol, cfg)

    # ── 2. Palanca 1: repartir en la flota por peso ──
    desviaciones = _asignar_unidades(asign, pedidos, volumenes, coords,
                                     vehiculos_cap, vehiculos_vol, cfg)
```

- [ ] **Step 5: Hacer que `_asignar_unidades` respete lo ya fijado**

En `_asignar_unidades`, el reset y la construcción de `por_dia` (línea
351-355) son:

```python
    for a in asign.values():
        a["unidad"] = None
    por_dia: dict = {}
    for gid in sorted(asign):
        por_dia.setdefault(asign[gid]["dia"], []).append(gid)
```

Cámbialos a:

```python
    for a in asign.values():
        if not a.get("exclusivo"):
            a["unidad"] = None
    por_dia: dict = {}
    for gid in sorted(asign):
        if asign[gid].get("exclusivo"):
            continue      # ya lo fijó _asignar_exclusivos antes de esta pasada
        por_dia.setdefault(asign[gid]["dia"], []).append(gid)
```

- [ ] **Step 6: Correr las pruebas nuevas y confirmar que pasan**

Run: `python -m pytest tests/test_convrp_logic.py -k exclusivo -q`
Expected: `8 passed` (las 3 de `_respeta_exclusividad` del Task 3 + la de
`_consolidar_solitarios` del Task 4 + las 4 nuevas de este task).

- [ ] **Step 7: Commit**

```bash
git add logic/convrp_logic.py tests/test_convrp_logic.py
git commit -m "Agrega _asignar_exclusivos: los grupos exclusivos nunca comparten camion"
```

---

### Task 6: Regresión completa

**Files:** ninguno (solo verificación)

- [ ] **Step 1: Suite completa de ConVRP**

Run: `python -m pytest tests/test_convrp_logic.py tests/test_convrp_integracion.py tests/test_convrp_validacion.py tests/test_plantilla_canonica.py -q`
Expected: todos pasan, cero regresiones.

- [ ] **Step 2: Smoke test contra las 9 semanas históricas**

Run: `python scripts/smoke_convrp.py`
Expected: `RESULTADO: OK` — sin sucursales perdidas/duplicadas, sin pérdida
de determinismo, capacidad respetada. Presta atención a
`RÍGIDOS partidos` y a `VIAJES/semana`: no deberían dispararse muy por
encima de la línea base ya documentada en el docstring del script (~31
viajes/semana) -- si sube mucho, revisar si algún grupo quedó marcado
`exclusivo` sin querer o si `_asignar_exclusivos` está escalando a camiones
más grandes de lo esperado.

- [ ] **Step 3: Si algo falla**

No hay step de código aquí a propósito: si la Step 1 o 2 fallan, hay que
volver a los Tasks 3-5 con systematic-debugging (root cause antes que
parche) en vez de forzar un ajuste rápido sobre este task de verificación.

---

### Task 7: Verificar contra datos reales (Zona 4, 24 y sub-grupo de Tuxtepec)

**Files:** ninguno (solo verificación, sin persistir nada)

- [ ] **Step 1: Confirmar que los 3 grupos quedaron marcados en la BD real**

Run:
```bash
python - <<'EOF'
from app import create_app
app = create_app()
with app.app_context():
    from logic.plantilla_canonica import obtener_grupos
    for g in obtener_grupos():
        if g["exclusivo"]:
            print(g["grupo"], g["zona"], g["dia"], g["sucursales"])
EOF
```
Expected: 3 líneas, grupos 4, 24 y 25.

- [ ] **Step 2: Reconstruir (sin persistir) los grupos de una semana real y confirmar la separación**

Usa el mismo patrón que `scripts/pdf_convrp_preview.py` para correr el
builder en memoria contra una semana histórica real y confirmar que el
grupo 4 y el grupo 25 (Tuxtepec jueves) ya no comparten unidad/día:

```bash
python - <<'EOF'
from app import create_app
app = create_app()
with app.app_context():
    import json
    from sqlalchemy import select
    from db import get_db, get_table
    from logic.plantilla_canonica import obtener_grupos
    from logic.convrp_logic import construir_groups_desde_plantilla, cfg_por_defecto
    from logic.vrp_logic import obtener_capacidades_vehiculos, obtener_volumenes_vehiculos
    from logic.asignacion_logic import MATRIZ_LAT_DEFAULT, MATRIZ_LON_DEFAULT

    db = get_db()
    cb = db.execute(select(get_table("configuracion"))).mappings().first() or {}
    depot = (float(cb.get("matriz_lat") or MATRIZ_LAT_DEFAULT),
             float(cb.get("matriz_lon") or MATRIZ_LON_DEFAULT))
    coords = {int(s.num_tienda): (float(s.latitud), float(s.longitud))
              for s in db.execute(select(get_table("sucursales"))).mappings()
              if s.get("latitud") is not None and s.get("num_tienda") is not None}
    caps = obtener_capacidades_vehiculos()
    vols = obtener_volumenes_vehiculos()
    plantilla = obtener_grupos()

    # cualquier semana real de sucursales sirve para esta verificacion; se
    # usa la primera disponible.
    h = get_table("rutas_historicas")
    reg = next(r for r in db.execute(select(h.c.nombre, h.c.filas, h.c.tipo_registro)).mappings()
              if r["tipo_registro"] == "sucursales")
    filas = json.loads(reg["filas"]) if reg["filas"] else []
    pedidos = {}
    for f in filas:
        if f.get("tipo") != "mayorista" and f.get("id_sucursal") is not None:
            sid = int(f["id_sucursal"])
            pedidos[sid] = pedidos.get(sid, 0) + float(f.get("kg_entrega") or 0)

    cfg = dict(cfg_por_defecto(), depot=depot)
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, coords, plantilla, caps, vols, cfg)

    grupo_de_sucursal = {s: g["grupo"] for g in plantilla for s in g["sucursales"]}
    for (veh, dia), miembros in sorted(groups.items()):
        grupos_en_ruta = sorted({grupo_de_sucursal.get(m["sid"]) for m in miembros})
        if any(g in (4, 24, 25) for g in grupos_en_ruta) and len(grupos_en_ruta) > 1:
            print(f"FALLA: {veh}/{dia} mezcla grupos {grupos_en_ruta}")
    print("Revision terminada.")
EOF
```
Expected: "Revision terminada." sin ninguna línea "FALLA": ningún viaje que
incluya el grupo 4, 24 o 25 comparte camión con otro grupo distinto.

- [ ] **Step 3: Informar al usuario**

Este task es de solo lectura -- no se persiste nada. Si el usuario quiere
ver el efecto en la semana activa actual, debe regenerar Asignación desde
la app (mismo paso pendiente que ya quedó abierto para los arreglos de
orden de zonas 22/9/7).
