# Asignación de vehículos por peso — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reemplazar la selección de camión por preferencia (`unidad_ref` /
`unidades_afines` / `unidad_forzada`) en `logic/convrp_logic.py` por selección
pura por peso (capacidad ascendente, consolidación como desempate), agregar
una exclusión dura de vehículos por grupo (`unidades_excluidas`, usada para
prohibir F350 en Tierra Blanca), y partir Tierra Blanca en 3 sub-rutas.
||||||||
**Architecture:** El cambio vive casi todo en `logic/convrp_logic.py`
(`_asignar_unidades`, `_unidad_alternativa`, `_dia_alternativo`) — puro, sin
BD, ya cubierto por `tests/test_convrp_logic.py`. Una columna nueva
(`plantilla_grupo.unidades_excluidas`) se agrega con el mismo patrón ALTER ya
usado para `zona`, se lee/escribe en `logic/plantilla_canonica.py` igual que
`dias_admisibles`, y fluye a través de `logic/convrp_integracion.py` sin
cambios (ya pasa la plantilla completa al builder). Una migración de datos
nueva reemplaza los grupos de Tierra Blanca y quita el pin de Tuxtepec.

**Tech Stack:** Python, SQLAlchemy Core, SQL Server (pyodbc), pytest.

**Spec:** `docs/superpowers/specs/2026-08-26-asignacion-vehiculos-por-peso-design.md`

---

## Contexto que todo implementador debe conocer antes de empezar

- `logic/convrp_logic.py` es un módulo PURO (sin BD): toda su lógica se
  prueba con datos sintéticos en `tests/test_convrp_logic.py`. NO importa
  Flask ni SQLAlchemy. Mantenlo así.
- `logic/convrp_integracion.py` es el puente con la BD: carga la plantilla
  vigente (`obtener_grupos()`), arma `cfg`, llama al builder puro, y
  opcionalmente persiste excepciones.
- El repo NO es un git repo por sí solo al nivel raíz mostrado en el entorno;
  trabaja dentro de `c:\Users\carli\Documents\ICG\logistica_icg`, que SÍ es
  el repo git real (confirma con `git status` antes de tocar nada).
- Commits van directo a `main` en local; no se hace push salvo pedido
  explícito (preferencia ya establecida del usuario).
- Corre pytest siempre desde `logistica_icg/`: `python -m pytest tests/ -v`.
- El módulo tiene una CULTURA fuerte de verificar empíricamente (correr el
  código real, no adivinar) — varios bugs de producción reales están
  documentados en comentarios largos dentro de `convrp_logic.py`. Sigue esa
  disciplina: cuando este plan pide "verifica corriendo pytest", hazlo de
  verdad, no asumas el resultado.

---

## Task 1: Columna `unidades_excluidas` — esquema y lectura/escritura

**Files:**
- Modify: `scripts/crear_plantilla_canonica.py` (`ALTERS`, línea ~36-38)
- Modify: `logic/plantilla_canonica.py` (`obtener_grupos`, línea ~630-660;
  `cargar_zonas_manual`, línea ~554-577)
- Test: `tests/test_plantilla_canonica.py`

- [ ] **Step 1: Agregar el ALTER**

En `scripts/crear_plantilla_canonica.py`, en la lista `ALTERS` (línea ~36):

```python
ALTERS = [
    ("plantilla_grupo", "unidades_afines", "NVARCHAR(400) NULL"),
    ("plantilla_grupo", "zona", "INT NULL"),
    ("plantilla_grupo", "unidades_excluidas", "NVARCHAR(200) NULL"),
]
```

- [ ] **Step 2: Correr el ALTER contra la BD de dev**

```bash
python scripts/crear_plantilla_canonica.py
```

Verifica en la salida que reporta la columna `unidades_excluidas` agregada
(o ya existente, si se corre dos veces — el script es idempotente, mismo
patrón que las otras dos columnas).

- [ ] **Step 3: Escribir el test de `cargar_zonas_manual` + `obtener_grupos` roundtrip**

En `tests/test_plantilla_canonica.py`, agrega (junto a los tests existentes
de `cargar_zonas_manual`/`obtener_grupos`):

```python
def test_unidades_excluidas_roundtrip(app_ctx):
    sub_rutas = [dict(
        grupo=9001, zona=9001, rigidez="FLEXIBLE", dia="LUNES",
        dias_admisibles=["LUNES"], unidad_ref=None, unidades_afines=None,
        unidad_forzada=False, unidades_excluidas=["F 350_1", "F 350_2", "F 350_3"],
        sucursales=[1])]
    cargar_zonas_manual(sub_rutas, nota="test unidades_excluidas")
    grupos = {g["grupo"]: g for g in obtener_grupos()}
    assert grupos[9001]["unidades_excluidas"] == ["F 350_1", "F 350_2", "F 350_3"]


def test_unidades_excluidas_vacio_es_lista_vacia(app_ctx):
    sub_rutas = [dict(
        grupo=9002, zona=9002, rigidez="FLEXIBLE", dia="LUNES",
        dias_admisibles=["LUNES"], unidad_ref=None, unidades_afines=None,
        unidad_forzada=False, sucursales=[2])]        # sin unidades_excluidas
    cargar_zonas_manual(sub_rutas, nota="test unidades_excluidas vacio")
    grupos = {g["grupo"]: g for g in obtener_grupos()}
    assert grupos[9002]["unidades_excluidas"] == []
```

Revisa el fixture `app_ctx` (o el que uses para `app.app_context()`) contra
los tests existentes del mismo archivo — usa el mismo patrón que ya usan
`test_zona_roundtrip` u otro test de `cargar_zonas_manual` en este archivo
para no duplicar setup.

- [ ] **Step 4: Correr el test para verificar que falla**

```bash
python -m pytest tests/test_plantilla_canonica.py -k unidades_excluidas -v
```

Esperado: FAIL (`KeyError: 'unidades_excluidas'` o similar — el campo no se
lee/escribe todavía).

- [ ] **Step 5: Implementar la escritura en `cargar_zonas_manual`**

En `logic/plantilla_canonica.py`, dentro de `cargar_zonas_manual` (donde se
arma el insert de `plantilla_grupo`, cerca de donde ya se escribe
`unidad_forzada=bool(r.get("unidad_forzada"))`, línea ~576):

```python
                unidad_forzada=bool(r.get("unidad_forzada")),
                unidades_excluidas=("|".join(r.get("unidades_excluidas") or []) or None),
```

- [ ] **Step 6: Implementar la lectura en `obtener_grupos`**

En `logic/plantilla_canonica.py`, dentro de `obtener_grupos` (donde se arma
cada dict de salida, línea ~652-659, junto a `unidades_afines=r.get(...)`):

```python
                        unidades_afines=r.get("unidades_afines"),
                        unidades_excluidas=[u.strip() for u in
                            str(r.get("unidades_excluidas") or "").split("|")
                            if u.strip()],
```

- [ ] **Step 7: Correr el test para verificar que pasa**

```bash
python -m pytest tests/test_plantilla_canonica.py -k unidades_excluidas -v
```

Esperado: PASS.

- [ ] **Step 8: Correr toda la suite de `test_plantilla_canonica.py`**

```bash
python -m pytest tests/test_plantilla_canonica.py -v
```

Esperado: todos PASS (no debió romper nada existente — es un campo nuevo,
aditivo).

- [ ] **Step 9: Commit**

```bash
git add scripts/crear_plantilla_canonica.py logic/plantilla_canonica.py tests/test_plantilla_canonica.py
git commit -m "$(cat <<'EOF'
Agrega columna unidades_excluidas a plantilla_grupo

Exclusion dura de vehiculos por grupo (p. ej. prohibir F350 para Tierra
Blanca), mismo patron ALTER no destructivo que zona/unidades_afines.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `_asignar_unidades` — selección por peso, sin preferencia

Este es el corazón del cambio. Reemplaza por completo la lógica de
"intentar `unidad_ref` primero, ceder con desempate por afinidad, proteger
la reserva de otros grupos pendientes, respetar `unidad_forzada`" por una
selección directa: cada grupo, sin excepción, elige entre las unidades no
excluidas y compatibles la de MENOR capacidad que le alcance, desempatando
por consolidación (ya cargada) y luego alfabético.

**Files:**
- Modify: `logic/convrp_logic.py:254-420` (función `_asignar_unidades`)
- Test: `tests/test_convrp_logic.py`

- [ ] **Step 1: Escribir los tests nuevos (deben fallar contra el código actual)**

Agrega en `tests/test_convrp_logic.py`, después de los tests de la sección 2
("La unidad es preferencia, no libre" — ese título deja de ser cierto, pero
no lo renombres todavía, eso es la Task 6):

```python
# ══ 2b. Selección por peso: la más chica que alcanza, no el abecedario ═════
def test_sin_preferencia_elige_la_unidad_mas_chica_que_alcanza():
    # Sin unidad_ref, dos unidades de igual capacidad ordenan por nombre HOY
    # (comportamiento viejo) -- el nuevo contrato debe elegir por CAPACIDAD
    # ascendente cuando las capacidades difieren, no caer siempre en la
    # primera alfabética.
    plantilla = [_grupo(1, "FLEXIBLE", "LUNES", [1, 2], unidad_ref=None)]
    pedidos = {1: 200, 2: 200}                      # 400 kg
    caps = {"Z_CHICA": 500, "A_GRANDE": 5000}        # Z ordena después de A
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, caps, {"Z_CHICA": 99, "A_GRANDE": 99},
        _sin_tiempo())
    assert ("Z_CHICA", "LUNES") in groups, \
        "debió elegir la unidad más chica que alcanza (400<=500), no la más grande"


def test_grupo_no_cabe_en_la_mas_chica_escala_a_la_siguiente():
    plantilla = [_grupo(1, "FLEXIBLE", "LUNES", [1, 2], unidad_ref=None)]
    pedidos = {1: 800, 2: 800}                      # 1600 kg
    caps = {"CHICA": 1000, "MEDIANA": 2500, "GRANDE": 3900}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, caps,
        {"CHICA": 99, "MEDIANA": 99, "GRANDE": 99}, _sin_tiempo())
    assert ("MEDIANA", "LUNES") in groups, \
        "no cabe en CHICA (1600>1000); debe escalar a MEDIANA, no saltar a GRANDE"


def test_entre_misma_capacidad_gana_la_ya_cargada_ese_dia():
    # Dos grupos, dos unidades de igual capacidad: el segundo grupo debe
    # sumarse a la que ya lleva carga (consolidar), no abrir la vacía.
    plantilla = [_grupo(1, "FLEXIBLE", "LUNES", [1], unidad_ref=None),
                 _grupo(2, "FLEXIBLE", "LUNES", [2], unidad_ref=None)]
    pedidos = {1: 1000, 2: 100}
    caps = {"V1": 5000, "V2": 5000}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, caps, {"V1": 99, "V2": 99}, _sin_tiempo())
    assert len(groups) == 1, "el segundo grupo debió consolidar, no abrir otra unidad"


# ══ 2c. `unidades_excluidas`: prohibición dura, nunca se asigna en silencio ═
def test_grupo_con_exclusion_nunca_recibe_la_unidad_excluida():
    plantilla = [_grupo(1, "FLEXIBLE", "LUNES", [1, 2], unidad_ref=None)]
    plantilla[0]["unidades_excluidas"] = ["CHICA"]
    pedidos = {1: 50, 2: 50}                        # 100 kg: cabría en CHICA
    caps = {"CHICA": 1000, "GRANDE": 5000}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, caps, {"CHICA": 99, "GRANDE": 99},
        _sin_tiempo())
    assert ("CHICA", "LUNES") not in groups
    assert ("GRANDE", "LUNES") in groups


def test_exclusion_tambien_aplica_en_el_ultimo_recurso():
    # El grupo no cabe en NINGUNA unidad no excluida (pesa más que cualquiera);
    # el último recurso (más espacio libre) debe seguir respetando la exclusión.
    plantilla = [_grupo(1, "FLEXIBLE", "LUNES", [1, 2], unidad_ref=None)]
    plantilla[0]["unidades_excluidas"] = ["GRANDE"]
    pedidos = {1: 3000, 2: 3000}                    # 6000 kg: no cabe en nadie
    caps = {"CHICA": 1000, "GRANDE": 9000}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, caps, {"CHICA": 99, "GRANDE": 99},
        _sin_tiempo())
    assert ("GRANDE", "LUNES") not in groups, \
        "nunca debe asignar la unidad excluida, ni siquiera en el último recurso"
    assert ("CHICA", "LUNES") in groups            # se parte después, pero arranca aquí


def test_sin_unidad_disponible_cuando_la_exclusion_deja_la_flota_vacia():
    from logic.convrp_logic import _asignar_unidades
    asign = {1: {"grupo": 1, "unidad": None, "dia": "LUNES", "miembros": [1],
                "unidad_ref": None, "rigidez": "FLEXIBLE",
                "dias_admisibles": ["LUNES"], "unidades_excluidas": ["UNICA"]}}
    pedidos = {1: 100}
    caps = {"UNICA": 5000}                          # la única unidad de la flota, excluida
    exc = _asignar_unidades(asign, pedidos, {}, {}, caps, {}, _sin_tiempo())
    assert asign[1]["unidad"] == "SIN_UNIDAD"
    assert any(e["tipo"] == "SIN_UNIDAD_DISPONIBLE" for e in exc)
    assert asign[1]["unidad"] != "UNICA", \
        "nunca debe caer en la unidad excluida, ni en este caso degenerado"


# ══ 2d. `unidad_forzada` ya no existe como concepto ════════════════════════
def test_unidad_forzada_ya_no_bloquea_el_reparto():
    # Antes `unidad_forzada=True` anclaba la unidad sin importar sobrecupo.
    # Ahora el grupo se re-evalúa por peso como cualquier otro -- si no cabe,
    # cede (y si de verdad no cabe en nadie, cae al último recurso normal).
    plantilla = [_grupo(1, "FLEXIBLE", "LUNES", [1], unidad_ref=None)]
    plantilla[0]["unidad_forzada"] = True
    pedidos = {1: 400}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, {"V1": 1000, "V2": 5000},
        {"V1": 99, "V2": 99}, _sin_tiempo(), kg_mayoristas={1: 700})  # 1100>1000: no cabe en V1
    assert ("V2", "LUNES") in groups, \
        "unidad_forzada ya no debe impedir que el grupo ceda cuando no cabe"
```

- [ ] **Step 2: Correr los tests nuevos y confirmar que fallan**

```bash
python -m pytest tests/test_convrp_logic.py -k "sin_preferencia or no_cabe_en_la_mas_chica or misma_capacidad or con_exclusion or exclusion_tambien or sin_unidad_disponible or forzada_ya_no" -v
```

Esperado: FAIL — varios (`KeyError: 'unidades_excluidas'`, resultados en
unidad equivocada, `SIN_UNIDAD_DISPONIBLE` no existe, etc.)

- [ ] **Step 3: Reescribir `_asignar_unidades`**

Reemplaza la función completa (líneas 254-420 de `logic/convrp_logic.py`)
por:

```python
def _asignar_unidades(asign, pedidos, volumenes, coords,
                      vehiculos_cap, vehiculos_vol, cfg):
    """
    Reparte los grupos de cada día entre las unidades: una sola pasada por
    peso descendente (first-fit decreasing). Cada grupo elige, entre las
    unidades NO excluidas (`unidades_excluidas` del grupo) y compatibles por
    coocurrencia que le alcanzan, la de MENOR capacidad -- nunca manda un
    grupo chico a una unidad grande de más si una chica ya le alcanza --
    desempatando por CONSOLIDACIÓN (la que ya lleva carga ese día, para no
    abrir un viaje nuevo: en el histórico un viaje lleva ~1.4 grupos, no 1.0)
    y por último por nombre.

    No hay preferencia de unidad: todo grupo pasa por el mismo criterio,
    sin importar `unidad_ref` (vestigial, ya no se lee para decidir) ni
    `unidad_forzada` (ídem). `unidades_excluidas` es la única prohibición
    dura -- se aplica también en el ÚLTIMO RECURSO (ninguna unidad admite al
    grupo completo): ahí se elige la no excluida con más espacio libre, para
    que la partición posterior pele lo mínimo, pero jamás una excluida.

    Si `unidades_excluidas` deja la flota entera afuera para un grupo (no
    debería pasar en operación normal -- ver spec), se registra una
    excepción SIN_UNIDAD_DISPONIBLE y el grupo queda con el sentinel
    "SIN_UNIDAD" (nunca el nombre de una unidad real), para revisión manual.

    Devuelve la lista de excepciones SIN_UNIDAD_DISPONIBLE. Es idempotente:
    se puede volver a llamar tras mover un día.
    """
    for a in asign.values():
        a["unidad"] = None
    por_dia: dict = {}
    for gid in sorted(asign):
        por_dia.setdefault(asign[gid]["dia"], []).append(gid)

    excepciones: list = []
    coocurrencia = cfg.get("coocurrencia_grupos")
    for dia in sorted(por_dia, key=_orden_dia):
        # los grupos más pesados primero (first-fit decreasing), desempate por id
        gids = sorted(por_dia[dia],
                      key=lambda g: (-_kg_grupo(asign[g], pedidos), g))

        for gid in gids:
            a = asign[gid]
            excluidas = set(a.get("unidades_excluidas") or [])
            candidatas = [u for u in vehiculos_cap if u not in excluidas]

            compat = [u for u in candidatas if _compatible_historico(
                a["grupo"], u, dia, asign, coocurrencia)]
            compat = compat or candidatas

            ordenadas = sorted(
                compat,
                key=lambda u: (_num(vehiculos_cap.get(u)),
                               -sum(_num(pedidos.get(s))
                                    for s in _sids_de_ruta(asign, u, dia)),
                               str(u)))

            elegido = None
            for unidad in ordenadas:
                destino = _sids_de_ruta(asign, unidad, dia) + list(a["miembros"])
                if _restriccion_violada(
                        sorted(destino), unidad, pedidos, volumenes, coords,
                        vehiculos_cap, vehiculos_vol, cfg, dia=dia) is None:
                    elegido = unidad
                    break

            if elegido is None and candidatas:
                # Ningún destino no excluido admite el grupo completo (p. ej.
                # pesa más que cualquiera de ellos). Va a la no excluida con
                # MÁS ESPACIO LIBRE del día, para que la partición posterior
                # pele lo mínimo -- "más vacía" es capacidad menos lo ya
                # cargado (incluida la carga de mayoristas anclada), NO menos
                # kilos encima (ver incidente 6-10 abril en el histórico git).
                kg_may = cfg.get("kg_mayoristas") or {}

                def _libre(u):
                    sids_u = _sids_de_ruta(asign, u, dia)
                    ocupado = sum(_num(pedidos.get(s)) + _num(kg_may.get(s))
                                  for s in sids_u)
                    return _num(vehiculos_cap.get(u)) - ocupado

                elegido = min(candidatas, key=lambda u: (-_libre(u), str(u)))
            elif elegido is None:
                # unidades_excluidas dejó la flota entera afuera: no hay
                # ninguna unidad válida. Nunca se asigna una excluida.
                elegido = "SIN_UNIDAD"
                excepciones.append({
                    "tipo": "SIN_UNIDAD_DISPONIBLE", "grupo": a["grupo"],
                    "rigidez": a["rigidez"], "dia": dia,
                    "motivo": f"ninguna unidad no excluida disponible para "
                              f"el grupo {a['grupo']} el {dia}",
                })
            a["unidad"] = elegido
    return excepciones
```

- [ ] **Step 4: Correr los tests nuevos, confirmar que pasan**

```bash
python -m pytest tests/test_convrp_logic.py -k "sin_preferencia or no_cabe_en_la_mas_chica or misma_capacidad or con_exclusion or exclusion_tambien or sin_unidad_disponible or forzada_ya_no" -v
```

Esperado: PASS.

- [ ] **Step 5: Commit**

No corras la suite COMPLETA todavía — varios tests viejos van a fallar a
propósito (prueban el contrato viejo). Eso se resuelve en la Task 6. Por
ahora, commitea sólo lo de esta task:

```bash
git add logic/convrp_logic.py tests/test_convrp_logic.py
git commit -m "$(cat <<'EOF'
Reemplaza _asignar_unidades: seleccion por peso, sin preferencia

Cada grupo elige la unidad no excluida y compatible de menor capacidad
que le alcanza, desempatando por consolidacion y luego alfabetico. Quita
el intento de usar unidad_ref, el pin de unidad_forzada y la reserva de
unidad pendiente (ya no aplican sin preferencia). Agrega la excepcion
SIN_UNIDAD_DISPONIBLE para el caso degenerado en que unidades_excluidas
deja la flota entera afuera.

Rompe a proposito varios tests del contrato viejo (preferencia/afinidad/
forzada/reserva) -- se resuelven en la siguiente task.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2b: Palancas 4 y 5 (`_consolidar_solitarios`/`_rellenar_capacidad_libre`) respetan `unidades_excluidas`

**Encontrado por el revisor de calidad de la Task 2, verificado de forma
independiente antes de escribir esta task:** ninguna de las dos "palancas"
que corren DESPUÉS de `_asignar_unidades` (Palanca 4: nunca dejar una ruta
con una sola parada; Palanca 5: rellenar capacidad libre con grupos ya
desviados) mira `unidades_excluidas`. Un grupo que `_asignar_unidades` puso
correctamente en una unidad chica (respetando su exclusión) puede terminar
igual en su unidad excluida vía consolidación posterior -- reproduciendo EL
MISMO bug de producción (Tierra Blanca en F350) que motivó todo este
proyecto, por una puerta que ningún test de la Task 2 cubría.

Además, el sentinel `"SIN_UNIDAD"` (nunca una unidad real, usado sólo para
marcar "sin unidad disponible, revisar a mano") se comporta como capacidad
INFINITA dentro de `_restriccion_violada` (línea ~151:
`cap = _num(vehiculos_cap.get(unidad)) or float("inf")`, porque
`"SIN_UNIDAD"` nunca está en `vehiculos_cap`). Sin un filtro explícito, la
Palanca 5 puede tratarlo como una ruta con espacio libre "ilimitado" y
empezar a rellenarlo con grupos normales -- lo opuesto de lo que el
sentinel debe significar (una bandera de revisión manual, no una ruta real).

**Files:**
- Modify: `logic/convrp_logic.py` (`_consolidar_solitarios`, línea ~376-450;
  `_rellenar_capacidad_libre`, línea ~453 en adelante)
- Test: `tests/test_convrp_logic.py`

- [ ] **Step 1: Escribir los tests nuevos**

```python
def test_consolidar_solitarios_nunca_mueve_a_unidad_excluida():
    from logic.convrp_logic import _consolidar_solitarios
    asign = {
        1: {"grupo": 1, "unidad": "CHICA", "dia": "LUNES", "miembros": [1],
            "unidad_ref": None, "rigidez": "FLEXIBLE",
            "dias_admisibles": ["LUNES"], "unidades_excluidas": ["GRANDE"]},
        2: {"grupo": 2, "unidad": "GRANDE", "dia": "LUNES", "miembros": [2, 3],
            "unidad_ref": None, "rigidez": "RIGIDO",
            "dias_admisibles": ["LUNES"], "unidades_excluidas": []},
    }
    pedidos = {1: 100, 2: 200, 3: 200}
    caps = {"CHICA": 1000, "GRANDE": 5000}
    coocurrencia = {frozenset((1, 2)): 1}      # compatibles: aisla SOLO la exclusion
    exc = _consolidar_solitarios(asign, pedidos, {}, {}, caps, {},
                                 _sin_tiempo(coocurrencia_grupos=coocurrencia), {})
    assert asign[1]["unidad"] == "CHICA", \
        "nunca debe consolidar en GRANDE (excluida para el grupo 1)"
    assert not any(e["tipo"] == "CONSOLIDADO_SOLITARIA" for e in exc)


def test_relleno_capacidad_nunca_mueve_a_unidad_excluida():
    from logic.convrp_logic import _rellenar_capacidad_libre
    asign = {
        1: {"grupo": 1, "unidad": "CHICA", "dia": "LUNES", "miembros": [1, 2],
            "unidad_ref": None, "dia_preferido": "LUNES", "rigidez": "FLEXIBLE",
            "dias_admisibles": ["LUNES"], "unidades_excluidas": ["GRANDE"]},
        2: {"grupo": 2, "unidad": "GRANDE", "dia": "LUNES", "miembros": [3],
            "unidad_ref": "GRANDE", "dia_preferido": "LUNES", "rigidez": "RIGIDO",
            "dias_admisibles": ["LUNES"], "unidades_excluidas": []},
    }
    pedidos = {1: 100, 2: 100, 3: 100}
    caps = {"CHICA": 1000, "GRANDE": 5000}      # GRANDE con mucho espacio libre
    coocurrencia = {frozenset((1, 2)): 3}       # compatibles: aisla SOLO la exclusion
    exc = _rellenar_capacidad_libre(asign, pedidos, {}, {}, caps, {},
                                    _sin_tiempo(coocurrencia_grupos=coocurrencia), {})
    assert asign[1]["unidad"] == "CHICA", \
        "nunca debe rellenar GRANDE con el grupo 1 (excluida para ese grupo)"


def test_relleno_capacidad_nunca_ofrece_sin_unidad_como_destino():
    from logic.convrp_logic import _rellenar_capacidad_libre
    asign = {
        1: {"grupo": 1, "unidad": "SIN_UNIDAD", "dia": "LUNES", "miembros": [1],
            "unidad_ref": None, "dia_preferido": "LUNES", "rigidez": "FLEXIBLE",
            "dias_admisibles": ["LUNES"], "unidades_excluidas": []},
        2: {"grupo": 2, "unidad": "CHICA", "dia": "LUNES", "miembros": [2],
            "unidad_ref": None, "dia_preferido": "MARTES", "rigidez": "FLEXIBLE",
            "dias_admisibles": ["LUNES", "MARTES"], "unidades_excluidas": []},
    }
    pedidos = {1: 100, 2: 100}
    caps = {"CHICA": 1000}                      # "SIN_UNIDAD" no está en caps a propósito
    exc = _rellenar_capacidad_libre(asign, pedidos, {}, {}, caps, {}, _sin_tiempo(), {})
    assert asign[2]["unidad"] != "SIN_UNIDAD", \
        "SIN_UNIDAD nunca debe tratarse como una ruta real con espacio libre"
```

- [ ] **Step 2: Correr y confirmar que fallan**

```bash
python -m pytest tests/test_convrp_logic.py -k "consolidar_solitarios_nunca_mueve or relleno_capacidad_nunca_mueve or relleno_capacidad_nunca_ofrece" -v
```

Esperado: los 3 FAIL (hoy ninguna de las dos funciones mira `unidades_excluidas`
ni excluye `"SIN_UNIDAD"` como destino).

- [ ] **Step 3: Arreglar `_consolidar_solitarios`**

Dentro de la función, donde se arma `activas_ese_dia` (línea ~417-418),
agrega el filtro de exclusión y de `"SIN_UNIDAD"`:

```python
        activas_ese_dia = sorted({u for (u, d) in _rutas_activas(asign)
                                  if d == dia and u != unidad
                                  and u != "SIN_UNIDAD"
                                  and u not in (a.get("unidades_excluidas") or ())})
```

(Reemplaza la línea existente `activas_ese_dia = sorted({u for (u, d) in
_rutas_activas(asign) if d == dia and u != unidad})` por esta versión.)

- [ ] **Step 4: Arreglar `_rellenar_capacidad_libre`**

Dos cambios:

1. Nunca tratar `"SIN_UNIDAD"` como ruta destino a rellenar. Donde se arma
   `orden_rutas` (línea ~500):

```python
    orden_rutas = sorted((k for k in _rutas_activas(asign) if k[0] != "SIN_UNIDAD"),
                        key=lambda k: (_ocupacion_pct(*k), k))
```

2. Nunca proponer como candidato a un grupo cuya `unidades_excluidas`
   prohíba la unidad destino. Dentro del loop que arma `candidatos` (cerca
   de la línea 512, junto al chequeo existente de `unidad_forzada`):

```python
                if a.get("unidad_forzada"):
                    continue
                if unidad in (a.get("unidades_excluidas") or ()):
                    continue
                if (a["unidad"], a["dia"]) == (a["unidad_ref"], a["dia_preferido"]):
                    continue
```

- [ ] **Step 5: Correr los 3 tests nuevos, confirmar que pasan**

```bash
python -m pytest tests/test_convrp_logic.py -k "consolidar_solitarios_nunca_mueve or relleno_capacidad_nunca_mueve or relleno_capacidad_nunca_ofrece" -v
```

- [ ] **Step 6: Correr los tests de Palanca 4/5 YA EXISTENTES para confirmar que no se rompieron**

```bash
python -m pytest tests/test_convrp_logic.py -k "solitaria or relleno_capacidad or cfg_por_defecto_incluye_relleno" -v
```

Esperado: todos PASS (estos filtros nuevos son aditivos -- sólo excluyen
opciones que antes de esta task ya no debían tomarse; ningún test existente
de Palanca 4/5 usa `unidades_excluidas` ni `"SIN_UNIDAD"`, así que su
comportamiento no cambia).

- [ ] **Step 7: Commit**

```bash
git add logic/convrp_logic.py tests/test_convrp_logic.py
git commit -m "$(cat <<'EOF'
Palancas 4 y 5 respetan unidades_excluidas; SIN_UNIDAD nunca es destino real

Encontrado por el revisor de calidad de la Task 2: _consolidar_solitarios y
_rellenar_capacidad_libre podian reasignar un grupo correctamente excluido
de vuelta a su propia unidad prohibida (mismo bug de produccion que motivo
este proyecto), y el sentinel SIN_UNIDAD se comportaba como capacidad
infinita en _restriccion_violada, atrayendo grupos normales hacia el.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: `_unidad_alternativa` — respeta exclusión, capacidad ascendente

**Encontrado por el revisor de calidad de la Task 2b:** ya hay 3 copias casi
idénticas del chequeo "¿esta unidad está en `unidades_excluidas` del grupo?"
(en `_asignar_unidades`, `_consolidar_solitarios`, `_rellenar_capacidad_libre`).
Esta task agrega una 4a (en `_unidad_alternativa`) y la Task 4 una 5a (en
`_dia_alternativo`) — 5 copias ya no es tolerable. Antes de tocar
`_unidad_alternativa`, extrae un helper compartido y refactoriza las 3
copias existentes para usarlo (sin cambiar su comportamiento).

**Files:**
- Modify: `logic/convrp_logic.py` (nuevo helper cerca de `_num`/`_kg_grupo`,
  línea ~227-229; `_asignar_unidades`; `_consolidar_solitarios`;
  `_rellenar_capacidad_libre`; `_unidad_alternativa:240-251`)
- Test: `tests/test_convrp_logic.py`

- [ ] **Step 0: Extraer el helper `_excluida` y refactorizar las 3 copias existentes**

En `logic/convrp_logic.py`, junto a `_kg_grupo` (línea ~227-229), agrega:

```python
def _excluida(a, unidad) -> bool:
    """True si `unidad` está en las `unidades_excluidas` del grupo -- nunca
    es un destino válido para él, ni siquiera en el último recurso."""
    return unidad in (a.get("unidades_excluidas") or ())
```

Reemplaza las 3 apariciones inline existentes por llamadas a este helper
(comportamiento idéntico, sólo refactor):

- En `_asignar_unidades`: `candidatas = [u for u in vehiculos_cap if u not in excluidas]`
  → `candidatas = [u for u in vehiculos_cap if not _excluida(a, u)]` (y quita
  la línea `excluidas = set(a.get("unidades_excluidas") or [])`, ya no hace falta).
- En `_consolidar_solitarios`: `... and u not in (a.get("unidades_excluidas") or ())`
  → `... and not _excluida(a, u)`.
- En `_rellenar_capacidad_libre`: `if unidad in (a.get("unidades_excluidas") or ()): continue`
  → `if _excluida(a, unidad): continue`.

Corre la suite completa de estas 3 funciones para confirmar que el refactor
no cambió nada de comportamiento:

```bash
python -m pytest tests/test_convrp_logic.py -k "sin_preferencia or no_cabe_en_la_mas_chica or misma_capacidad or con_exclusion or sin_unidad_disponible or forzada_ya_no or consolidar_solitarios_nunca_mueve or relleno_capacidad_nunca_mueve or relleno_capacidad_nunca_ofrece" -v
```

Esperado: los mismos 9 PASS de antes (6 de la Task 2 + 3 de la Task 2b) —
si alguno cambia de resultado, el refactor no fue neutral, revisa antes de
seguir.

- [ ] **Step 1: Escribir los tests nuevos**

Además de los 2 tests nuevos de `_unidad_alternativa`, re-agrega el test que
la Task 2 dejó fuera a propósito (`test_exclusion_tambien_aplica_en_el_ultimo_recurso`
— necesitaba que `_unidad_alternativa` respetara la exclusión, que es
justamente lo que esta task implementa):

```python
def test_exclusion_tambien_aplica_en_el_ultimo_recurso():
    # El grupo no cabe en NINGUNA unidad no excluida (pesa más que
    # cualquiera); el último recurso (más espacio libre) debe seguir
    # respetando la exclusión, y la pieza que la partición separa también
    # debe reubicarse sin caer en la excluida (vía _unidad_alternativa).
    plantilla = [_grupo(1, "FLEXIBLE", "LUNES", [1, 2], unidad_ref=None)]
    plantilla[0]["unidades_excluidas"] = ["GRANDE"]
    pedidos = {1: 3000, 2: 3000}                    # 6000 kg: no cabe en nadie
    caps = {"CHICA": 1000, "GRANDE": 9000}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, caps, {"CHICA": 99, "GRANDE": 99},
        _sin_tiempo())
    assert ("GRANDE", "LUNES") not in groups, \
        "nunca debe asignar la unidad excluida, ni siquiera en el último recurso"
    assert ("CHICA", "LUNES") in groups            # se parte después, pero arranca aquí


def test_unidad_alternativa_nunca_ofrece_una_excluida():
    from logic.convrp_logic import _unidad_alternativa
    asign = {1: {"grupo": 1, "unidad": "ORIGEN", "dia": "LUNES", "miembros": [1],
                "unidades_excluidas": ["PROHIBIDA"]}}
    a = dict(grupo=2, unidad="ORIGEN", dia="LUNES", miembros=[9],
             unidades_excluidas=["PROHIBIDA"])
    pedidos = {9: 100}
    caps = {"ORIGEN": 1000, "PROHIBIDA": 5000}
    resultado = _unidad_alternativa(asign, a, pedidos, {}, {}, caps, {}, _sin_tiempo())
    assert resultado is None, "PROHIBIDA no debió ofrecerse aunque sea la única con cupo"


def test_unidad_alternativa_prefiere_la_de_menor_capacidad():
    from logic.convrp_logic import _unidad_alternativa
    asign = {}
    a = dict(grupo=1, unidad="ORIGEN", dia="LUNES", miembros=[9],
             unidades_excluidas=None)
    pedidos = {9: 400}
    caps = {"ORIGEN": 100, "CHICA": 500, "GRANDE": 5000}
    resultado = _unidad_alternativa(asign, a, pedidos, {}, {}, caps, {}, _sin_tiempo())
    assert resultado == "CHICA"
```

- [ ] **Step 2: Correr y confirmar que fallan**

```bash
python -m pytest tests/test_convrp_logic.py -k "unidad_alternativa or exclusion_tambien_aplica" -v
```

Esperado: los 3 tests de `_unidad_alternativa`/último-recurso FAIL; los 9
del Step 0 siguen en PASS (el refactor ya se confirmó neutral).

- [ ] **Step 3: Reescribir `_unidad_alternativa`**

Reemplaza (líneas ~240-251, la ubicación exacta puede haber cambiado tras el
Step 0) por:

```python
def _unidad_alternativa(asign, a, pedidos, volumenes, coords,
                        vehiculos_cap, vehiculos_vol, cfg):
    """Otra unidad, MISMO día, donde el grupo quepa sin saturarla. Nunca una
    de `unidades_excluidas` del grupo; entre las que le alcanzan, prueba
    primero la de menor capacidad."""
    candidatas = sorted((u for u in vehiculos_cap if not _excluida(a, u)),
                        key=lambda u: (_num(vehiculos_cap.get(u)), str(u)))
    for unidad in candidatas:
        if unidad == a["unidad"]:
            continue
        destino = _sids_de_ruta(asign, unidad, a["dia"]) + list(a["miembros"])
        if _restriccion_violada(sorted(destino), unidad, pedidos, volumenes,
                                coords, vehiculos_cap, vehiculos_vol, cfg,
                                dia=a["dia"]) is None:
            return unidad
    return None
```

- [ ] **Step 4: Correr y confirmar que pasan**

```bash
python -m pytest tests/test_convrp_logic.py -k "unidad_alternativa or exclusion_tambien_aplica" -v
```

Esperado: los 3 PASS ahora. Corre también el Step 0's filtro una vez más
para confirmar que nada se rompió:

```bash
python -m pytest tests/test_convrp_logic.py -k "sin_preferencia or no_cabe_en_la_mas_chica or misma_capacidad or con_exclusion or sin_unidad_disponible or forzada_ya_no or consolidar_solitarios_nunca_mueve or relleno_capacidad_nunca_mueve or relleno_capacidad_nunca_ofrece" -v
```

- [ ] **Step 5: Commit**

```bash
git add logic/convrp_logic.py tests/test_convrp_logic.py
git commit -m "$(cat <<'EOF'
_unidad_alternativa respeta unidades_excluidas y prueba capacidad ascendente

Extrae el helper _excluida() y refactoriza las 3 copias inline existentes
(_asignar_unidades, _consolidar_solitarios, _rellenar_capacidad_libre) para
usarlo -- eran ya 3 copias del mismo chequeo antes de agregar esta 4a.
Re-agrega test_exclusion_tambien_aplica_en_el_ultimo_recurso (dejado fuera
a proposito en la Task 2: necesitaba este fix para pasar).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3b: el pedazo separado al partir un grupo hereda `unidades_excluidas`

**Encontrado por el revisor de calidad de la Task 3, verificado de forma
reproducible antes de escribir esta task.** Cuando un grupo se parte por
sobrecupo (`PARTIDO_CAPACIDAD`, en `construir_groups_desde_plantilla`), el
pedazo que se separa se registra como una entrada NUEVA en `asign` (línea
~720-725) que copia `unidad_ref`/`unidad_forzada`/`dias_admisibles`/etc. del
grupo original, pero **nunca copia `unidades_excluidas`**. `_unidad_alternativa`
recibe un dict aparte (`sub = dict(a, ...)`, que sí hereda el campo) sólo
para decidir el destino INMEDIATO, así que esa primera colocación es segura
-- pero la entrada permanente que queda en `asign` no tiene la llave, así
que cualquier pasada posterior que la lea (`_excluida`, usada por
`_consolidar_solitarios` y `_rellenar_capacidad_libre`, las mismas dos
funciones que la Task 2b ya endureció) ve `None` y trata el pedazo como si
no tuviera ninguna exclusión. Mismo bug de fondo (una unidad prohibida
terminando con la carga prohibida), por una tercera puerta.

**Files:**
- Modify: `logic/convrp_logic.py:720-725` (dentro de
  `construir_groups_desde_plantilla`, bloque "último recurso: partir")
- Test: `tests/test_convrp_logic.py`

- [ ] **Step 1: Escribir el test nuevo**

```python
def test_pedazo_partido_hereda_unidades_excluidas():
    # El grupo (excluido de GRANDE) pesa más que CHICA y se parte. El pedazo
    # separado, tras partir, debe seguir sin poder consolidarse ni rellenar
    # en GRANDE aunque GRANDE tenga cupo y sea compatible -- si no heredó
    # unidades_excluidas, Palancas 4/5 lo mandarían ahí de todos modos.
    plantilla = [
        _grupo(1, "FLEXIBLE", "LUNES", [1, 2], unidad_ref=None,
               dias_admisibles=["LUNES"]),
        _grupo(2, "FLEXIBLE", "LUNES", [3], unidad_ref=None,
               dias_admisibles=["LUNES"]),
    ]
    plantilla[0]["unidades_excluidas"] = ["GRANDE"]
    pedidos = {1: 900, 2: 900, 3: 100}          # g1 = 1800 > CHICA(1000): se parte
    caps = {"CHICA": 1000, "GRANDE": 5000}
    coocurrencia = {frozenset((1, 2)): 1}       # compatibles: aisla SOLO la exclusion
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, caps, {"CHICA": 99, "GRANDE": 99},
        _sin_tiempo(coocurrencia_grupos=coocurrencia))
    assert not any(e["tipo"] == "CONSOLIDADO_SOLITARIA" and e["a_unidad"] == "GRANDE"
                  for e in exc), \
        "el pedazo partido nunca debe consolidarse en GRANDE (excluida para el grupo 1)"
    for (unidad, dia), miembros in groups.items():
        if unidad == "GRANDE":
            sids = {m["sid"] for m in miembros}
            assert not (sids & {1, 2}), \
                "ninguna sucursal del grupo 1 (excluido de GRANDE) debe terminar ahí"
```

- [ ] **Step 2: Correr y confirmar que falla**

```bash
python -m pytest tests/test_convrp_logic.py -k pedazo_partido_hereda -v
```

- [ ] **Step 3: Agregar `unidades_excluidas` a la entrada del pedazo partido**

En `logic/convrp_logic.py`, dentro de `construir_groups_desde_plantilla`
(línea ~720-725), agrega el campo:

```python
            asign[clave] = dict(
                grupo=a["grupo"], rigidez=a["rigidez"],
                unidad=(destino[0] if destino else unidad),
                unidad_ref=a["unidad_ref"], unidad_forzada=a.get("unidad_forzada", False),
                dia=(destino[1] if destino else dia), dia_preferido=a["dia_preferido"],
                dias_admisibles=a["dias_admisibles"], miembros=sorted(separadas),
                unidades_excluidas=list(a.get("unidades_excluidas") or []))
```

- [ ] **Step 4: Correr y confirmar que pasa**

```bash
python -m pytest tests/test_convrp_logic.py -k pedazo_partido_hereda -v
```

- [ ] **Step 5: Correr los 9+3 tests de exclusión de las tasks anteriores para confirmar que nada se rompió**

```bash
python -m pytest tests/test_convrp_logic.py -k "sin_preferencia or no_cabe_en_la_mas_chica or misma_capacidad or con_exclusion or sin_unidad_disponible or forzada_ya_no or consolidar_solitarios_nunca_mueve or relleno_capacidad_nunca_mueve or relleno_capacidad_nunca_ofrece or unidad_alternativa or exclusion_tambien_aplica" -v
```

Esperado: los 12 anteriores + el nuevo, todos PASS.

- [ ] **Step 6: Commit**

```bash
git add logic/convrp_logic.py tests/test_convrp_logic.py
git commit -m "$(cat <<'EOF'
El pedazo partido de un grupo hereda unidades_excluidas

Encontrado por el revisor de calidad de la Task 3: al partir un grupo por
sobrecupo, la entrada nueva que representa el pedazo separado no copiaba
unidades_excluidas, asi que Palancas 4/5 podian consolidarlo de vuelta en
su propia unidad prohibida -- mismo bug de origen, una tercera puerta.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: `_dia_alternativo` — quita el intento de `unidad_ref`, exclusión + capacidad ascendente

**Files:**
- Modify: `logic/convrp_logic.py:423-447`
- Test: `tests/test_convrp_logic.py`

- [ ] **Step 1: Escribir el test nuevo**

```python
def test_dia_alternativo_nunca_ofrece_una_excluida():
    from logic.convrp_logic import _dia_alternativo
    asign = {}
    a = {"grupo": 1, "unidad": "ORIGEN", "dia": "LUNES", "miembros": [1],
         "unidad_ref": "PROHIBIDA", "rigidez": "FLEXIBLE",
         "dias_admisibles": ["LUNES", "MARTES"], "unidades_excluidas": ["PROHIBIDA"]}
    pedidos = {1: 100}
    caps = {"ORIGEN": 1000, "PROHIBIDA": 5000}
    resultado = _dia_alternativo(asign, a, pedidos, {}, {}, caps, {}, _sin_tiempo())
    assert resultado is None or resultado[1] != "PROHIBIDA"
```

- [ ] **Step 2: Correr y confirmar que falla**

```bash
python -m pytest tests/test_convrp_logic.py -k dia_alternativo_nunca_ofrece -v
```

Esperado: FAIL (hoy `_dia_alternativo` prueba `a["unidad_ref"]` primero sin
filtrar exclusión, así que devolvería `("MARTES", "PROHIBIDA")`).

- [ ] **Step 3: Reescribir `_dia_alternativo`**

Reemplaza (líneas 423-447) por:

```python
def _dia_alternativo(asign, a, pedidos, volumenes, coords,
                     vehiculos_cap, vehiculos_vol, cfg):
    """Otro día ADMISIBLE (en orden de preferencia) donde el grupo quepa.
    El grupo se mueve completo -- el día es atributo del bloque.

    Nunca prueba una unidad de `unidades_excluidas` del grupo; entre las que
    le alcanzan, prueba primero la de menor capacidad.

    Dos pasadas: primero sólo destinos compatibles por historial (mismo
    criterio que `_asignar_unidades`), y sólo si ninguno sirve se repite sin
    ese filtro -- mejor un destino sin precedente que un grupo sin día."""
    coocurrencia = cfg.get("coocurrencia_grupos")
    candidatas = sorted((u for u in vehiculos_cap if not _excluida(a, u)),
                        key=lambda u: (_num(vehiculos_cap.get(u)), str(u)))
    for exigir_compat in (True, False):
        for dia in a["dias_admisibles"]:
            if dia == a["dia"]:
                continue
            for unidad in candidatas:
                if exigir_compat and not _compatible_historico(
                        a["grupo"], unidad, dia, asign, coocurrencia):
                    continue
                destino = _sids_de_ruta(asign, unidad, dia) + list(a["miembros"])
                if _restriccion_violada(sorted(destino), unidad, pedidos, volumenes,
                                        coords, vehiculos_cap, vehiculos_vol,
                                        cfg, dia=dia) is None:
                    return dia, unidad
    return None
```

- [ ] **Step 4: Correr y confirmar que pasa**

```bash
python -m pytest tests/test_convrp_logic.py -k dia_alternativo -v
```

Nota: `test_dia_alternativo_prefiere_destino_compatible_por_coocurrencia` y
`test_dia_alternativo_coocurrencia_cede_si_es_la_unica_opcion` (ya
existentes) deben seguir pasando sin tocarlos — verificalo en este mismo
comando. Si alguno falla, no adivines: lee el escenario exacto (están cerca
de la línea 682 y 702 de `tests/test_convrp_logic.py`) y confirma si el
resultado nuevo sigue siendo correcto por el criterio de compatibilidad
histórica antes de tocar la aserción.

- [ ] **Step 5: Commit**

```bash
git add logic/convrp_logic.py tests/test_convrp_logic.py
git commit -m "$(cat <<'EOF'
_dia_alternativo quita el intento de unidad_ref, respeta unidades_excluidas

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Propagar `unidades_excluidas` desde la plantilla hasta `asign`

**Actualización:** los Steps 1-4 de abajo (el wiring en
`construir_groups_desde_plantilla`) ya se hicieron dentro de la Task 2 --
se adelantó esa línea porque 2 de los tests de la Task 2 la necesitaban
(ver el commit `99f806a`). Esta task ahora empieza directo en el Step 5
(quitar el guard en `convrp_integracion.py`); los Steps 1-4 quedan abajo
sólo como referencia/verificación de que el wiring sigue ahí.

**Files:**
- Verify only: `logic/convrp_logic.py` (`construir_groups_desde_plantilla`
  ya tiene `unidades_excluidas` en el `dict(...)` de `asign` -- no hay nada
  que tocar aquí)
- Modify: `logic/convrp_integracion.py:100-111` (`construir_groups_convrp`)
- Test: `tests/test_convrp_logic.py`, `tests/test_convrp_integracion.py`

- [ ] **Step 0: Verificar que el wiring de la Task 2 sigue funcionando**

```bash
python -m pytest tests/test_convrp_logic.py -k "plantilla_con_unidades_excluidas or plantilla_sin_unidades_excluidas or con_exclusion" -v
```

Si estos tests no existen todavía en el archivo, agrégalos ahora (son el
Step 1 de abajo) y confirma que YA pasan sin cambios de código (el wiring
ya está ahí desde la Task 2) -- si alguno falla, no asumas que hace falta
repetir el Step 3, investiga primero por qué el wiring existente no cubre
ese caso.

<details>
<summary>Steps 1-4 originales (referencia -- ya aplicados en la Task 2)</summary>

- [ ] **Step 1: Escribir el test end-to-end en `test_convrp_logic.py`**

```python
def test_plantilla_con_unidades_excluidas_las_respeta_en_construir_groups():
    plantilla = [{"grupo": 1, "rigidez": "FLEXIBLE", "dia": "LUNES",
                  "unidad_ref": None, "sucursales": [1, 2],
                  "dias_admisibles": ["LUNES"],
                  "unidades_excluidas": ["GRANDE"]}]
    pedidos = {1: 50, 2: 50}
    caps = {"CHICA": 1000, "GRANDE": 5000}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, caps, {"CHICA": 99, "GRANDE": 99},
        _sin_tiempo())
    assert ("CHICA", "LUNES") in groups
    assert ("GRANDE", "LUNES") not in groups


def test_plantilla_sin_unidades_excluidas_no_rompe():
    # La mayoría de los grupos NO trae unidades_excluidas -- debe comportarse
    # como lista vacía (sin restricción), no explotar con KeyError/TypeError.
    plantilla = [_grupo(1, "FLEXIBLE", "LUNES", [1, 2], unidad_ref=None)]
    pedidos = {1: 50, 2: 50}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, {"V1": 5000}, {"V1": 99}, _sin_tiempo())
    assert ("V1", "LUNES") in groups
```

- [ ] **Step 2: Correr y confirmar que fallan**

```bash
python -m pytest tests/test_convrp_logic.py -k "plantilla_con_unidades_excluidas or plantilla_sin_unidades_excluidas" -v
```

- [ ] **Step 3: Wire en `construir_groups_desde_plantilla`**

En `logic/convrp_logic.py`, dentro del loop de construcción de `asign`
(línea ~671-675), agrega el campo:

```python
        asign[int(g["grupo"])] = dict(
            grupo=int(g["grupo"]), rigidez=str(g.get("rigidez", "")).upper(),
            unidad=unidad, unidad_ref=unidad_ref, dia=dia, dia_preferido=dia,
            dias_admisibles=adm, miembros=activos,
            unidad_forzada=bool(g.get("unidad_forzada")),
            unidades_excluidas=list(g.get("unidades_excluidas") or []))
```

- [ ] **Step 4: Correr y confirmar que pasan**

```bash
python -m pytest tests/test_convrp_logic.py -k "plantilla_con_unidades_excluidas or plantilla_sin_unidades_excluidas" -v
```

</details>

- [ ] **Step 5: Quitar el guard de `unidad_ref` sin resolver en `convrp_integracion.py`**

`unidad_ref` queda vestigial (ya no se lee para decidir unidad) — el guard
que hace `raise ValueError` cuando `unidad_ref` no resuelve contra el
catálogo de vehículos (línea ~104-111) ya NO protege nada real: antes evitaba
que una preferencia inválida se ignorara en silencio, pero ahora NINGUNA
preferencia se usa, válida o no. Dejarlo bloquearía la generación por un
`unidad_ref` viejo/desactualizado que ya no le importa a nadie.

En `logic/convrp_integracion.py`, dentro de `construir_groups_convrp` (línea
~100-111), borra estas líneas:

```python
    sin_unidad = sorted({g["grupo"] for g in plantilla
                         if g["unidad_ref"] is not None
                         and g["unidad_ref"] not in (vehiculos_cap or {})})
    if sin_unidad:
        raise ValueError(
            f"unidad_ref sin resolver contra el catálogo de vehículos en los "
            f"grupos {sin_unidad}: la preferencia de unidad se ignoraría en "
            f"silencio. Recarga la plantilla (scripts/cargar_plantilla.py).")
```

Deja el resto de la función intacta (la construcción de `cfg`, incluida
`afinidad_unidad=_afinidad_de_plantilla(plantilla)`, se queda tal cual —
vestigial pero inofensiva, no hace falta tocarla).

- [ ] **Step 6: Actualizar `tests/test_convrp_integracion.py`**

El guard que se acaba de quitar tenía dos tests dedicados
(`test_unidad_ref_none_no_dispara_el_guard_de_sin_resolver` y
`test_unidad_ref_no_resuelto_contra_catalogo_si_dispara_el_guard`, líneas
~79-111). Bórralos junto con su bloque de comentario explicativo (líneas
~52-77) — el guard que documentaban ya no existe. El resto del archivo
(`_pasada`, tests de `_elegir_mejor_pasada`) no se toca.

- [ ] **Step 7: Correr las dos suites completas**

```bash
python -m pytest tests/test_convrp_integracion.py -v
python -m pytest tests/test_convrp_logic.py -k "unidades_excluidas or plantilla_con or plantilla_sin" -v
```

Esperado: todos PASS. (La suite completa de `test_convrp_logic.py` todavía
tiene fallos esperados del contrato viejo — eso se resuelve en la Task 6.)

- [ ] **Step 8: Commit**

```bash
git add logic/convrp_logic.py logic/convrp_integracion.py tests/test_convrp_logic.py tests/test_convrp_integracion.py
git commit -m "$(cat <<'EOF'
Propaga unidades_excluidas hasta asign; quita el guard de unidad_ref sin resolver

unidad_ref es vestigial (ya no decide unidad); el guard que fallaba duro
si no resolvia contra el catalogo de vehiculos ya no protege nada real y
bloquearia la generacion por datos viejos sin importancia.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Reconciliar la suite existente de `test_convrp_logic.py`

El nuevo contrato (sin preferencia, sin `unidad_forzada`, sin reserva, sin
afinidad como desempate) hace que varios tests EXISTENTES prueben un
mecanismo que ya no existe. Esta task corre la suite completa, borra los
tests obsoletos (con justificación), y ajusta los que sólo necesitan una
aserción distinta -- sin adivinar: cada cambio se verifica corriendo pytest.

**Files:**
- Modify: `tests/test_convrp_logic.py`

- [ ] **Step 1: Correr la suite completa y ver el estado real**

```bash
python -m pytest tests/test_convrp_logic.py -v 2>&1 | tee /tmp/resultado_task6.txt
```

(En Windows/PowerShell: `python -m pytest tests/test_convrp_logic.py -v | Tee-Object -FilePath resultado_task6.txt`.)

- [ ] **Step 2: Borrar los tests que prueban un mecanismo removido (DELETE, no reescribir)**

Estos tests verifican específicamente preferencia/afinidad/`unidad_forzada`/
reserva de `unidad_ref` pendiente -- mecanismos que la Task 2 quitó a
propósito. Bórralos enteros (incluido cualquier comentario de sección que
sólo los introduzca a ellos):

- `test_grupo_usa_su_unidad_de_referencia_cuando_cabe`
- `test_grupo_conserva_unidad_ref_cuando_cabe_aunque_haya_otras_libres`
- La sección completa "3b. `unidad_forzada`: regla de negocio, nunca cede"
  (comentario + 3 tests: `test_unidad_forzada_se_queda_fija_aunque_no_quepa_ni_se_pueda_partir`,
  `test_sin_forzar_el_mismo_caso_si_cede`,
  `test_unidad_forzada_no_cambia_nada_cuando_ya_cabria_de_todos_modos`)
- `test_excepcion_registra_restriccion_peso` (sólo probaba el campo
  `restriccion` de `MOVIDO_UNIDAD`, que ya no existe como exception type)
- `test_excepcion_registra_restriccion_volumen` (ídem)
- `test_excepcion_registra_origen_lores` (ídem, vía `MOVIDO_UNIDAD`)
- `test_excepcion_registra_origen_mayoristas` (ídem)
- `test_excepcion_registra_origen_ambas` (ídem)
- La sección completa "Al ceder la unidad de referencia, manda la AFINIDAD,
  no el abecedario" (comentario + 3 tests:
  `test_al_ceder_la_unidad_gana_la_afinidad_historica`,
  `test_sin_afinidad_el_reparto_no_cambia`,
  `test_la_afinidad_no_rompe_la_consolidacion`) -- la afinidad ya no es
  desempate; el desempate ahora es capacidad + consolidación, ya cubierto
  por los tests nuevos de la Task 2.
- La sección completa "Reserva de unidad_ref pendiente" (comentario + 2
  tests: `test_reserva_de_unidad_pendiente_evita_que_otro_grupo_la_ocupe_cediendo`,
  `test_reserva_de_unidad_pendiente_cede_si_es_la_unica_opcion_viable`) --
  no hay más `unidad_ref` que reservar. La protección real que este
  mecanismo daba (dos grupos sin coocurrencia histórica no deben compartir
  camión si hay alternativa) sigue cubierta por
  `test_al_ceder_unidad_prefiere_la_compatible_por_coocurrencia`, que NO se
  toca.

- [ ] **Step 3: Ajustar `test_sobrecupo_mueve_flexible_a_otra_unidad_del_mismo_dia`**

Este test sigue siendo válido (el escenario de sobrecupo con movimiento de
unidad sigue existiendo), pero su última aserción prueba el exception type
`MOVIDO_UNIDAD`, que ya no se emite (no hay más "preferencia" de la que
desviarse -- la unidad elegida por peso ES la asignación, no una desviación
de nada). Quita SÓLO estas dos líneas del final del test:

```python
    tipos = [e["tipo"] for e in exc]
    assert "MOVIDO_UNIDAD" in tipos
```

El resto del test (que el rígido quede en V1 con `[1,2]` y el flexible en
V2 con `[3,4]`) se queda igual.

- [ ] **Step 4: Correr la suite de nuevo**

```bash
python -m pytest tests/test_convrp_logic.py -v
```

- [ ] **Step 5: Para CUALQUIER otro test que siga fallando, diagnostica antes de tocar nada**

No debería quedar ninguno fuera de los ya listados arriba, pero si aparece
alguno (por ejemplo en la sección "Palanca 4: ningún viaje se queda con una
sola sucursal", que usa `unidad_ref` sólo como scaffolding y podría
comportarse distinto ahora que la selección inicial ya no está anclada a
una preferencia):

1. Lee el test completo y el comentario que lo introduce.
2. Corre sólo ese test con `-v` y lee el mensaje de fallo exacto (qué
   esperaba vs. qué devolvió el motor).
3. Pregúntate: ¿la aserción que falla depende del NOMBRE de una unidad
   específica (p. ej. `groups[("V2","LUNES")]`) que dependía del viejo
   orden alfabético/de preferencia, mientras el COMPORTAMIENTO de negocio
   que el test protege (consolidación, coocurrencia, capacidad, rigidez de
   composición, tope de barridos) sigue intacto? Si sí: es un cambio de
   aserción legítimo -- ajusta el nombre de unidad esperado al que el motor
   nuevo produce DE VERDAD (corrido, no adivinado), sin cambiar lo que el
   test verifica.
4. Si en cambio el comportamiento de negocio protegido genuinamente se
   rompió (p. ej. dos grupos sin ningún precedente histórico ahora
   comparten camión pudiendo evitarlo, o una sucursal se pierde, o la
   partición ya no es determinista) -- **no lo arregles adivinando**:
   repórtalo como BLOCKED con el test exacto, el mensaje de fallo, y tu
   hipótesis de causa. Esto sería un bug real en la Task 2/3/4, no un test
   desactualizado.

- [ ] **Step 6: Confirmar la suite completa en verde**

```bash
python -m pytest tests/test_convrp_logic.py -v
```

Esperado: 100% PASS.

- [ ] **Step 6b: Actualizar los docstrings desactualizados (encontrado por el revisor de calidad de la Task 4)**

Dos docstrings en `logic/convrp_logic.py` todavía describen el contrato
VIEJO y quedaron sin actualizar durante las Tasks 2-4:

1. **Docstring del módulo** (líneas ~1-33, la sección que dice algo como
   *"La UNIDAD no es identidad del grupo, pero tampoco es libre: `unidad_ref`
   es la preferencia y desviarse tiene penalización (se registra como
   excepción)"* y la lista "Orden de palancas ante sobrecupo" que sólo
   menciona 3 palancas). Reescríbelo para que diga: la unidad se elige por
   peso (capacidad ascendente + consolidación + `unidades_excluidas`, sin
   preferencia ni penalización); `unidad_ref`/`unidades_afines`/
   `unidad_forzada` son vestigiales (se guardan pero no se leen para
   decidir); y actualiza la lista de palancas para incluir las 5 reales
   (unidad → día → partir → consolidar solitarias → rellenar capacidad
   libre), no sólo 3.
2. **Docstring de `construir_groups_desde_plantilla`** (la línea que lista
   los tipos de excepción, algo como *"MOVIDO_UNIDAD, MOVIDO_DIA,
   PARTIDO_CAPACIDAD, AVISO_RUTA_LARGA"*): `MOVIDO_UNIDAD` ya no se emite
   (confirma con `grep -n "MOVIDO_UNIDAD" logic/convrp_logic.py` -- no debe
   quedar ninguna aparición fuera de este docstring antes de tu cambio, y
   cero después). Reemplázalo en la lista por `SIN_UNIDAD_DISPONIBLE`,
   `CONSOLIDADO_SOLITARIA`, `AVISO_RUTA_SOLITARIA` (los tipos reales que sí
   se emiten), y menciona `unidades_excluidas` como la restricción dura que
   ninguna de las palancas puede violar.

No hace falta un test para esto (son comentarios/docstrings, no
comportamiento) -- sólo confirma que la suite sigue en verde después:

```bash
python -m pytest tests/test_convrp_logic.py -v
```

- [ ] **Step 6c: Quitar la línea muerta de `unidad_ref` en `construir_groups_desde_plantilla`**

Cerca de la línea ~611-613, este código calcula `unidad` a partir de
`unidad_ref` pero el valor se descarta un momento después (`_asignar_unidades`
resetea `a["unidad"] = None` para todos los grupos antes de decidir nada):

```python
        unidad_ref = g.get("unidad_ref")
        unidad = unidad_ref if unidad_ref in vehiculos_cap else (
            sorted(vehiculos_cap)[0] if vehiculos_cap else "VEHICULO")
```

Simplifica a:

```python
        unidad_ref = g.get("unidad_ref")
        unidad = None          # se decide en _asignar_unidades; este es sólo un placeholder
```

Y el comentario justo abajo (línea ~621, algo como *"# ── 2. Palanca 1:
repartir en la flota (unidad_ref = preferencia) ──"*) pásalo a algo como
*"# ── 2. Palanca 1: repartir en la flota por peso ──"* (sin la mención a
`unidad_ref` como preferencia, que ya no aplica).

Corre la suite completa para confirmar que el simplificado no cambió nada
(el valor de `unidad` calculado ahí nunca se leía de todos modos):

```bash
python -m pytest tests/test_convrp_logic.py -v
```

Esperado: mismo resultado que el Step 6 (100% PASS), ni un test más ni
menos -- si algo cambia, esa línea no era tan muerta como parecía; para y
reporta antes de seguir.

- [ ] **Step 6d: Test end-to-end de exclusión multi-fase (encontrado por el revisor de calidad de la Task 4)**

Los tests de las Tasks 2-4 verifican la exclusión función por función,
pero ningún test corre un escenario realista a través de TODAS las fases
(asignación inicial → mover de día → partir → consolidar solitarias →
rellenar capacidad) en una sola corrida con un grupo excluido. Agrega este
test a `tests/test_convrp_logic.py`:

```python
def test_exclusion_se_mantiene_a_traves_de_todas_las_fases():
    # Escenario con varios grupos, un día saturado, y un grupo excluido de
    # GRANDE que fuerza pasar por movimiento de dia, particion, y deja a
    # otro grupo listo para consolidacion/relleno el mismo dia -- GRANDE
    # nunca debe aparecer como destino de ninguna sucursal del grupo 1 en
    # ninguna fase.
    plantilla = [
        _grupo(1, "FLEXIBLE", "LUNES", [1, 2, 3], unidad_ref=None,
               dias_admisibles=["LUNES", "MARTES"]),
        _grupo(2, "FLEXIBLE", "LUNES", [4], unidad_ref=None,
               dias_admisibles=["LUNES"]),
    ]
    plantilla[0]["unidades_excluidas"] = ["GRANDE"]
    pedidos = {1: 600, 2: 600, 3: 600, 4: 100}   # g1 = 1800 (> CHICA), g2 = 100
    caps = {"CHICA": 1000, "GRANDE": 5000}
    coocurrencia = {frozenset((1, 2)): 1}         # compatibles: aisla SOLO la exclusion
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, caps, {"CHICA": 99, "GRANDE": 99},
        _sin_tiempo(coocurrencia_grupos=coocurrencia))
    for (unidad, dia), miembros in groups.items():
        if unidad == "GRANDE":
            sids = {m["sid"] for m in miembros}
            assert not (sids & {1, 2, 3}), \
                f"sucursal del grupo excluido terminó en GRANDE: {sids}"
    for e in exc:
        assert e.get("a_unidad") != "GRANDE" and e.get("destino_unidad") != "GRANDE", \
            f"ninguna excepcion debe mover el grupo excluido a GRANDE: {e}"
```

Corre y confirma que pasa:

```bash
python -m pytest tests/test_convrp_logic.py -k exclusion_se_mantiene_a_traves -v
```

- [ ] **Step 6e: Dos docstrings más (encontrado por el revisor de calidad de la Task 5)**

En `logic/convrp_integracion.py`:
1. `construir_groups_convrp`'s docstring dice "Lanza si la plantilla o sus
   llaves no están sanas" -- eso describía el guard de `unidad_ref` que la
   Task 5 quitó; ahora sólo lanza si la plantilla está vacía. Ajusta la
   frase para no sobre-prometer validación que ya no existe.
2. `_afinidad_de_plantilla` (función) y la línea
   `afinidad_unidad=_afinidad_de_plantilla(plantilla)` dentro de
   `construir_groups_convrp`: agrega una línea de comentario marcándolas
   como vestigiales (el motor dejó de leer `afinidad_unidad` para decidir
   unidad desde la Task 2) -- no las borres, sólo deja claro para el
   próximo que las lea que ya no influyen en nada.

No hace falta test nuevo; corre `python -m pytest tests/test_convrp_integracion.py -v`
para confirmar que sigue en verde (son sólo comentarios).

- [ ] **Step 7: Commit**

```bash
git add logic/convrp_logic.py logic/convrp_integracion.py tests/test_convrp_logic.py
git commit -m "$(cat <<'EOF'
Reconcilia test_convrp_logic.py con el contrato de seleccion por peso

Borra los tests de preferencia/unidad_forzada/afinidad/reserva (mecanismos
removidos a proposito); ajusta la asercion de MOVIDO_UNIDAD en el test de
sobrecupo. El resto de la suite (coocurrencia, particion, rigidez/dia,
consolidacion de solitarios, relleno de capacidad, determinismo) sigue
verificando el mismo comportamiento de negocio sin cambios.

Ademas (hallazgos del revisor de calidad de la Task 4): actualiza los 2
docstrings que todavia describian el contrato viejo (preferencia con
penalizacion, MOVIDO_UNIDAD), quita la ultima linea muerta que calculaba
unidad desde unidad_ref sin usarla, y agrega un test end-to-end que
verifica la exclusion a traves de las 5 fases en una sola corrida.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Migración de datos — Tierra Blanca (3+3+2) y Tuxtepec sin forzar

**Files:**
- Modify: `scripts/reorganizar_zonas_2026.py`
- Test: manual (`--dry-run` + inspección), más un test de integridad en
  `tests/test_plantilla_canonica.py`

- [ ] **Step 1: Reemplazar `SUB_RUTAS_ESPECIALES` en `scripts/reorganizar_zonas_2026.py`**

Sustituye la entrada del grupo 5 (Tuxtepec, quita `unidad_forzada`) y las
entradas de los grupos 11/27 (Tierra Blanca, reemplazadas por 3 grupos
nuevos con `unidades_excluidas` puesto a los 3 F350):

```python
SUB_RUTAS_ESPECIALES = [
    dict(grupo=5, zona=5, rigidez="FLEXIBLE", dia="MARTES",
         dias_admisibles=["MARTES", "MIERCOLES"], unidad_ref="F 350_2",
         unidad_forzada=False, sucursales=[2, 7, 15, 31, 54, 55]),
    dict(grupo=25, zona=5, rigidez="RIGIDO", dia="JUEVES",
         dias_admisibles=["JUEVES"], unidad_ref="F 350_2",
         unidad_forzada=False, sucursales=[38, 46, 57]),
    dict(grupo=26, zona=5, rigidez="FLEXIBLE", dia="MARTES",
         dias_admisibles=["MARTES", "JUEVES", "MIERCOLES"], unidad_ref="K 16",
         unidad_forzada=False, sucursales=[74]),
    dict(grupo=11, zona=11, rigidez="FLEXIBLE", dia="LUNES",
         dias_admisibles=["LUNES"], unidad_ref=None, unidad_forzada=False,
         unidades_excluidas=["F 350_1", "F 350_2", "F 350_3"],
         sucursales=[24, 25, 77]),          # Tierra Blanca Norte
    dict(grupo=27, zona=11, rigidez="FLEXIBLE", dia="LUNES",
         dias_admisibles=["LUNES"], unidad_ref=None, unidad_forzada=False,
         unidades_excluidas=["F 350_1", "F 350_2", "F 350_3"],
         sucursales=[1, 36, 101]),          # Tierra Blanca Centro
    dict(grupo=28, zona=11, rigidez="FLEXIBLE", dia="LUNES",
         dias_admisibles=["LUNES"], unidad_ref=None, unidad_forzada=False,
         unidades_excluidas=["F 350_1", "F 350_2", "F 350_3"],
         sucursales=[63, 76]),              # Tierra Blanca Sur
]
```

(Grupo 28 es nuevo -- el siguiente número libre; los 22 grupos de
`ZONAS_SIMPLES` no usan ese id, verifícalo con `grep -n "^ *[0-9]*:" ` sobre
ese diccionario antes de asumirlo si el archivo cambió desde que se escribió
este plan.)

- [ ] **Step 2: Actualizar el docstring del módulo**

En el docstring de `scripts/reorganizar_zonas_2026.py` (línea ~1-25), agrega
una nota sobre esta segunda corrida:

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

ACTUALIZACIÓN 2026-08 (ver
docs/superpowers/specs/2026-08-26-asignacion-vehiculos-por-peso-design.md):
Tierra Blanca (Zona 11) pasó de 2 grupos (11, 27; 4+4 sucursales) a 3 (11,
27, 28; 3+3+2) para que cada uno quede dentro del rango medio de peso y
nunca necesite F350 (prohibido para Tierra Blanca vía `unidades_excluidas`).
Tuxtepec (grupo 5) perdió `unidad_forzada=True`: se re-evalúa por peso como
cualquier otro grupo.
...
"""
```

- [ ] **Step 3: Correr en `--dry-run` y revisar la salida a mano**

```bash
python scripts/reorganizar_zonas_2026.py --dry-run
```

Verifica en la salida:
- `28 grupos, 24 zonas` (uno más que antes: 27→28, porque Tierra Blanca pasó
  de 2 a 3 grupos).
- Ninguna sucursal duplicada (`ABORTADO` no debe aparecer).
- Las filas de zona 11 muestran 3 grupos (11, 27, 28) sumando 8 sucursales
  (3+3+2), sin `unidad_ref` (columna en blanco/`None`).
- La fila del grupo 5 ya no debe mostrar `forzada=True`.

- [ ] **Step 3b: Actualizar los tests existentes que hardcodean "27 grupos" / "zona 11 = 2 grupos"**

Estos 4 tests en `tests/test_plantilla_canonica.py` asumen el conteo VIEJO
(27 grupos totales, Tierra Blanca en 2 grupos) y van a fallar apenas corra
la migración de esta task. Ajusta cada uno:

`test_roundtrip_lectura_bd` (línea ~144-185, lee la BD real — sólo pasa
DESPUÉS del Step 6 de esta task, cuando la migración ya corrió):
```python
    assert len(grupos) == 28
    assert sum(1 for g in grupos if g["rigidez"] == "RIGIDO") == 18
    assert sum(1 for g in grupos if g["rigidez"] == "FLEXIBLE") == 10
```
y más abajo:
```python
    assert len(por_zona[11]) == 3
```
(`total_zona11 == 8` no cambia — siguen siendo las mismas 8 sucursales,
sólo repartidas en 3 grupos en vez de 2).

`test_construir_sub_rutas_produce_27_filas_validas` (línea ~272): renombra
la función a `test_construir_sub_rutas_produce_28_filas_validas` y cambia
`assert len(sub_rutas) == 27` a `assert len(sub_rutas) == 28`. El resto del
test (101 sucursales, zona 17 con 50%) no cambia.

`test_sub_rutas_especiales_grupo_y_zona` (línea ~256-259):
```python
    assert por_grupo == {5: 5, 25: 5, 26: 5, 11: 11, 27: 11, 28: 11}
```

`test_construir_sub_rutas_agrega_24_zonas` (línea ~262-269):
```python
    assert len(grupos_simples) + len(grupos_especiales) == 28
```

Corre estos 4 tests después de cada ajuste con, por ejemplo:
```bash
python -m pytest tests/test_plantilla_canonica.py -k "roundtrip_lectura_bd or produce_28_filas or sub_rutas_especiales_grupo_y_zona or agrega_24_zonas" -v
```
`test_roundtrip_lectura_bd` seguirá fallando hasta el Step 6 (corre contra
la BD real) — es esperado; los otros 3 (puramente estáticos, sin BD) deben
pasar de inmediato tras el Step 1 de esta task.

- [ ] **Step 4: Escribir un test de integridad para la nueva forma de Tierra Blanca**

En `tests/test_plantilla_canonica.py`, junto al test existente que verifica
zona 5/zona 11 (el que se corrigió en la reorganización anterior — búscalo
por `zona 11` o `SUB_RUTAS_ESPECIALES` para ubicarlo), agrega:

```python
def test_tierra_blanca_queda_en_3_grupos_3_3_2_sin_f350():
    from scripts.reorganizar_zonas_2026 import construir_sub_rutas
    sub_rutas, _ = construir_sub_rutas()
    tb = [r for r in sub_rutas if r["zona"] == 11]
    assert len(tb) == 3
    assert sorted(len(r["sucursales"]) for r in tb) == [2, 3, 3]
    todas = sorted(s for r in tb for s in r["sucursales"])
    assert todas == [1, 24, 25, 36, 63, 76, 77, 101]
    for r in tb:
        assert r["unidad_ref"] is None
        assert set(r["unidades_excluidas"]) == {"F 350_1", "F 350_2", "F 350_3"}


def test_tuxtepec_ya_no_tiene_unidad_forzada():
    from scripts.reorganizar_zonas_2026 import construir_sub_rutas
    sub_rutas, _ = construir_sub_rutas()
    g5 = next(r for r in sub_rutas if r["grupo"] == 5)
    assert g5["unidad_forzada"] is False
```

- [ ] **Step 5: Correr el test y confirmar que pasa**

```bash
python -m pytest tests/test_plantilla_canonica.py -k "tierra_blanca_queda_en_3 or tuxtepec_ya_no_tiene" -v
```

- [ ] **Step 6: Ejecutar la migración real (no dry-run) contra la BD de dev**

Confirma primero con el usuario que se debe correr contra la BD real de
desarrollo (mismo criterio que la reorganización de zonas anterior — es
no-destructivo/versionado, pero sigue siendo una escritura real). Luego:

```bash
python scripts/reorganizar_zonas_2026.py
```

Verifica que la salida reporta una versión nueva vigente con 28 grupos, 24
zonas.

- [ ] **Step 7: Commit**

```bash
git add scripts/reorganizar_zonas_2026.py tests/test_plantilla_canonica.py
git commit -m "$(cat <<'EOF'
Parte Tierra Blanca en 3 grupos (3+3+2) con F350 excluido; quita forzada de Tuxtepec

Grupos 11/27 (4+4 sucursales) se reemplazan por 11/27/28 (Norte 3, Centro
3, Sur 2), agrupados por cercania real de coordenadas, cada uno con
unidades_excluidas=[F 350_1,F 350_2,F 350_3]. Grupo 5 (Tuxtepec) pierde
unidad_forzada: se re-evalua por peso como cualquier otro grupo.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Regresión completa y regeneración del PDF

**Files:** ninguno (verificación + operación).

- [ ] **Step 1: Correr toda la suite del proyecto**

```bash
python -m pytest tests/ -v
```

Esperado: 100% PASS. Si algo fuera de `test_convrp_logic.py`,
`test_convrp_integracion.py` o `test_plantilla_canonica.py` falla,
diagnostica igual que en la Task 6 (no adivines) antes de tocarlo — podría
ser el archivo corrupto no relacionado (`logic/consolidacion_mayoristas.py`)
ya reportado en la sesión anterior; confírmalo con `git status`/`git diff`
antes de asumir que es tuyo.

- [ ] **Step 2: Smoke test manual del motor completo**

Si existe `scripts/smoke_convrp.py` (referenciado en el grep de esta
sesión), córrelo contra la plantilla ya migrada para confirmar que
`construir_groups_convrp`/`construir_rutas_con_mayoristas` no explotan con
datos reales:

```bash
python scripts/smoke_convrp.py
```

- [ ] **Step 3: Regenerar la logística del 24 al 28 de agosto de 2026**

Usa el flujo normal de la aplicación (UI o el endpoint/script que ya se usa
en producción) para volver a generar esa semana con el motor nuevo, y
confirma en el PDF resultante que ninguna sucursal de Tierra Blanca aparece
en F 350_1, F 350_2 ni F 350_3. Este paso es manual/operativo — no hay
código nuevo que escribir aquí; su único propósito es reemplazar el PDF ya
emitido que motivó todo este trabajo.

**Chequeo adicional obligatorio (riesgo conocido, sin arreglar a propósito
-- ver memoria de proyecto "KANGOO inactive-vehicle-risk"):** revisa el PDF
completo (las 28 rutas, no sólo Tierra Blanca/Tuxtepec) buscando
específicamente si alguna ruta quedó asignada a `KANGOO`. Es una unidad
`activo=False` sin chofer (`chofer=''`) que el nuevo criterio de capacidad
ascendente puede elegir primero para cualquier grupo chico/liviano, porque
`obtener_capacidades_vehiculos()` no filtra por `activo` (asimetría
documentada, no se toca en este plan). Si aparece, **no la ignores ni la
edites a mano en el PDF** -- repórtalo de inmediato como un hallazgo
bloqueante antes de dar por bueno el resultado; es exactamente el tipo de
bug (unidad equivocada en una ruta real) que motivó todo este proyecto.

- [ ] **Step 4: Reporte final**

Nota: Task 9 (abajo) se agregó DESPUÉS de correr este Step 3 por primera
vez -- una vez que la Task 9 esté commiteada, hay que volver a correr este
Step 3 (regenerar el PDF) para confirmar el resultado final con la
afinidad ya restaurada.

---

## Task 9: Restaurar afinidad histórica como desempate entre unidades empatadas en capacidad

**Encontrado por el usuario al revisar el PDF real de la Task 8, root-cause
confirmado empíricamente (comparación directa old-vs-new corriendo el
motor viejo y el nuevo contra los mismos datos reales de la semana
24-28 agosto):**

Grupo 1 (Cosamaloapan/Carlos A. Carrillo/Amatitlán) tiene
`unidad_ref='F 350_1'`, `unidades_afines='F 350_1:9'` (9 semanas
históricas). Grupo 22 (San Andrés/Catemaco/Santiago Tuxtla/Juan Díaz
Covarrubias) tiene `unidad_ref='F 350_3'`, `unidades_afines='F 350_3:9'`.
Los 3 F350 tienen la MISMA capacidad (3900 kg) -- el peso no puede
distinguir entre ellos. Antes de la Task 2, `unidad_ref` decidía esto
directo. Después de la Task 2, ningún criterio distingue entre F350_1/2/3
cuando dos grupos genuinamente necesitan ese nivel de camión, así que el
desempate cae en orden alfabético/consolidación puro -- mezclando qué
camión/chofer específico atiende cada zona, algo que el negocio conoce de
memoria y que no tiene que ver con el bug de Tierra Blanca (Tierra Blanca
sigue prohibida de los 3 F350 sin excepción; este fix no toca eso).

Esto también explica el caso de Santiago Tuxtla 1: al no caer de forma
confiable en su F350 histórico (grupo 22 es RIGIDO, 8 sucursales, 3402 kg
esta semana -- cabe en un solo F350 por peso, pero viola TIEMPO con las 8
paradas juntas, un problema pre-existente y ya documentado del modelo de
tiempo). La partición resultante (Palanca 3) interactúa distinto con los
demás grupos en cada corrida sin un ancla de afinidad consistente, y la
pieza partida puede terminar en la unidad más chica disponible en vez de
quedarse en el nivel F350 con sus hermanas.

**El fix NO revierte la Task 2**: el peso sigue decidiendo el NIVEL de
camión (chico/mediano/F350) y `unidades_excluidas` sigue siendo la
prohibición dura de Tierra Blanca. `unidades_afines` (ya wireado en
`cfg["afinidad_unidad"]` desde `convrp_integracion.py`, vestigial desde la
Task 2) vuelve a leerse, pero SÓLO como desempate cuando dos o más
candidatos quedan empatados en capacidad -- nunca como preferencia que
pueda ganarle a una capacidad distinta que ajusta mejor.

**Files:**
- Modify: `logic/convrp_logic.py` (`_asignar_unidades`, `_unidad_alternativa`,
  `_dia_alternativo`)
- Test: `tests/test_convrp_logic.py`

- [ ] **Step 1: Escribir los tests nuevos**

```python
def test_entre_capacidad_empatada_gana_la_afinidad_historica():
    plantilla = [_grupo(1, "FLEXIBLE", "LUNES", [1, 2], unidad_ref=None)]
    pedidos = {1: 1500, 2: 1500}          # 3000 kg: cabe en cualquiera de las dos
    caps = {"A_GRANDE": 3900, "Z_GRANDE": 3900}     # empatadas en capacidad
    cfg = dict(cfg_por_defecto(), chequear_tiempo=False,
               afinidad_unidad={1: {"Z_GRANDE": 9}})
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, caps, {"A_GRANDE": 99, "Z_GRANDE": 99}, cfg)
    assert ("Z_GRANDE", "LUNES") in groups, \
        "debio ganar la afinidad historica (Z_GRANDE), no el abecedario (A_GRANDE)"


def test_afinidad_no_gana_sobre_capacidad_distinta():
    # La afinidad es SOLO desempate entre empatadas -- si las capacidades
    # difieren, sigue mandando la mas chica que alcanza, aunque la afinidad
    # apunte a la mas grande.
    plantilla = [_grupo(1, "FLEXIBLE", "LUNES", [1, 2], unidad_ref=None)]
    pedidos = {1: 400, 2: 400}             # 800 kg: cabe en CHICA y en GRANDE
    caps = {"CHICA": 1000, "GRANDE": 5000}
    cfg = dict(cfg_por_defecto(), chequear_tiempo=False,
               afinidad_unidad={1: {"GRANDE": 9}})
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, caps, {"CHICA": 99, "GRANDE": 99}, cfg)
    assert ("CHICA", "LUNES") in groups, \
        "la capacidad ascendente sigue mandando cuando no hay empate real"


def test_sin_afinidad_el_desempate_sigue_siendo_alfabetico():
    # Sin datos de afinidad (grupo nuevo, sin historial) el comportamiento
    # no cambia: sigue siendo capacidad -> consolidacion -> alfabetico.
    plantilla = [_grupo(1, "FLEXIBLE", "LUNES", [1, 2], unidad_ref=None)]
    pedidos = {1: 1500, 2: 1500}
    caps = {"A_GRANDE": 3900, "Z_GRANDE": 3900}
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, caps, {"A_GRANDE": 99, "Z_GRANDE": 99},
        _sin_tiempo())
    assert ("A_GRANDE", "LUNES") in groups
```

- [ ] **Step 2: Correr y confirmar que fallan**

```bash
python -m pytest tests/test_convrp_logic.py -k "empatada_gana_la_afinidad or afinidad_no_gana_sobre_capacidad or sin_afinidad_el_desempate" -v
```

Esperado: el primero FAIL (hoy cae en A_GRANDE por alfabético); los otros
dos ya PASAN sin cambios (confírmalo, son la línea base).

- [ ] **Step 3: Agregar el desempate por afinidad en las 3 funciones**

En `logic/convrp_logic.py`, dentro de `_asignar_unidades`, en el sort key
de `ordenadas` (donde hoy dice
`key=lambda u: (_num(vehiculos_cap.get(u)), -sum(...), str(u))`), agrega la
afinidad como tercer criterio, antes del nombre:

```python
            af = (cfg.get("afinidad_unidad") or {}).get(a["grupo"]) or {}
            ordenadas = sorted(
                compat,
                key=lambda u: (_num(vehiculos_cap.get(u)),
                               -sum(_num(pedidos.get(s))
                                    for s in _sids_de_ruta(asign, u, dia)),
                               -_num(af.get(u)), str(u)))
```

En `_unidad_alternativa`, mismo criterio (agrega `af` leído de `cfg` y
`a["grupo"]`, insértalo antes de `str(u)` en el sort key existente).

En `_dia_alternativo`, mismo criterio (mismo patrón: `af` leído una vez
antes del loop, insertado en el sort key de `candidatas`).

Actualiza el docstring de cada una de las 3 funciones para mencionar que
la afinidad histórica desempata entre unidades de igual capacidad.

- [ ] **Step 4: Correr y confirmar que pasan**

```bash
python -m pytest tests/test_convrp_logic.py -k "empatada_gana_la_afinidad or afinidad_no_gana_sobre_capacidad or sin_afinidad_el_desempate" -v
```

- [ ] **Step 5: Correr toda la suite de `test_convrp_logic.py` para confirmar que nada se rompió**

```bash
python -m pytest tests/test_convrp_logic.py tests/test_convrp_integracion.py -v
```

Esperado: 100% PASS (esto es aditivo -- un criterio de desempate nuevo que
sólo actúa cuando hay empate real de capacidad; ningún test existente
depende de ganar ese empate por alfabético a propósito, verifícalo si algo
falla en vez de asumir que es esperado).

- [ ] **Step 6: Commit**

```bash
git add logic/convrp_logic.py tests/test_convrp_logic.py
git commit -m "$(cat <<'EOF'
Restaura afinidad historica como desempate entre unidades empatadas en capacidad

Encontrado por el usuario en el PDF real: los 3 F350 tienen la misma
capacidad, asi que el peso no podia distinguir entre ellos, y el
desempate alfabetico mezclaba que camion especifico atiende cada zona
(Cosamaloapan=F350_1, San Andres/Santiago Tuxtla=F350_3 historicamente,
9 de 9 semanas cada uno). unidades_afines (vestigial desde la Task 2,
pero ya wireada en cfg desde convrp_integracion.py) vuelve a leerse, solo
como desempate entre capacidades empatadas -- el peso sigue decidiendo el
nivel de camion y unidades_excluidas sigue siendo la prohibicion dura de
Tierra Blanca, sin cambios.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 6b: El "último recurso" también debe consultar afinidad (encontrado por el revisor de calidad -- el fix de arriba no alcanza)**

**Esto es lo que de verdad decide el caso real que motivó la Task 9.** Grupo
22 (8 sucursales, 3402 kg) viola TIEMPO en los 3 F350 por igual (ninguno de
los 3 "cabe" de verdad con las 8 paradas juntas) -- así que NUNCA llega al
sort normal de la Step 3; cae directo al bloque "último recurso" (línea
~347-362), que hoy desempata por `(-_libre(u), str(u))` sin mirar afinidad
en absoluto. Por eso el fix de la Step 3, aunque correcto en aislamiento
(los 3 tests nuevos pasan), no cambia el resultado real: grupo 22 sigue
cayendo en F 350_1 (alfabético) en vez de F 350_3 (su afinidad real), y
eso además bloquea a grupo 1 de reclamar F 350_1 después (por
incompatibilidad histórica con lo que ya quedó en esa unidad).

- [ ] **Step 6b.1: Escribir el test que reproduce el caso real**

```python
def test_ultimo_recurso_tambien_desempata_por_afinidad():
    # Ningun candidato "cabe" (fuerza el bloque de ultimo recurso, no el
    # sort normal) -- entre F350_1/2/3, todas vacias y de igual capacidad,
    # debe ganar la afinidad, no el abecedario.
    from logic.convrp_logic import _asignar_unidades
    asign = {1: {"grupo": 1, "unidad": None, "dia": "LUNES", "miembros": [1],
                "unidad_ref": None, "rigidez": "RIGIDO",
                "dias_admisibles": ["LUNES"], "unidades_excluidas": []}}
    pedidos = {1: 5000}      # mas pesado que cualquier unidad: fuerza ultimo recurso
    caps = {"A_GRANDE": 3900, "Z_GRANDE": 3900}
    cfg = dict(cfg_por_defecto(), chequear_tiempo=False,
               afinidad_unidad={1: {"Z_GRANDE": 9}})
    _asignar_unidades(asign, pedidos, {}, {}, caps, {}, cfg)
    assert asign[1]["unidad"] == "Z_GRANDE", \
        "el ultimo recurso tambien debe desempatar por afinidad, no solo por espacio libre/abecedario"
```

- [ ] **Step 6b.2: Correr y confirmar que falla**

```bash
python -m pytest tests/test_convrp_logic.py -k ultimo_recurso_tambien_desempata -v
```

- [ ] **Step 6b.3: Agregar afinidad al desempate del último recurso**

En `logic/convrp_logic.py`, dentro de `_asignar_unidades` (línea ~362),
`af` ya está calculado más arriba (Step 3) -- sólo hace falta usarlo aquí
también:

```python
                elegido = min(candidatas, key=lambda u: (-_libre(u), -_num(af.get(u)), str(u)))
```

- [ ] **Step 6b.4: Correr y confirmar que pasa**

```bash
python -m pytest tests/test_convrp_logic.py -k ultimo_recurso_tambien_desempata -v
```

- [ ] **Step 6b.5: Correr toda la suite otra vez**

```bash
python -m pytest tests/test_convrp_logic.py tests/test_convrp_integracion.py -v
```

Esperado: 74 passed (los 73 de antes + este nuevo), 0 failed.

- [ ] **Step 6b.6: Verificar contra el escenario REAL antes de commitear**

No te conformes con que los tests unitarios pasen -- ese fue exactamente el
error que dejó pasar el problema la primera vez. Corre:

```bash
python scripts/pdf_convrp_preview.py "24 al 28 de agosto"
```

Lee el PDF resultante (usa el tool de lectura de PDF si está disponible, o
al menos confirma con un script que inspeccione `groups`/`por_ruta`
directamente) y confirma explícitamente:
- Cosamaloapan/Carlos A. Carrillo/Amatitlán → F 350_1
- San Andrés/Catemaco/Santiago Tuxtla/Juan Díaz Covarrubias → F 350_3
- Tierra Blanca sigue sin tocar ningún F350

Si el resultado real TODAVÍA no coincide, NO commitees -- reporta BLOCKED
con el detalle exacto de qué pasó (igual que hizo el revisor de calidad),
no seas la segunda persona en confiar solo en el test unitario.

- [ ] **Step 6b.7: Commit**

```bash
git add logic/convrp_logic.py tests/test_convrp_logic.py
git commit -m "$(cat <<'EOF'
El ultimo recurso de _asignar_unidades tambien desempata por afinidad

El fix anterior de esta misma task (sort normal) no alcanzaba: el caso
real que la motivo (grupo 22, 8 paradas, viola TIEMPO en los 3 F350 por
igual) nunca llega al sort normal -- cae directo al bloque de ultimo
recurso, que desempataba solo por espacio libre y alfabetico. Verificado
contra el PDF real de la semana 24-28 agosto, no solo con tests sinteticos.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 6c: Reserva de afinidad -- un grupo pesado sin afinidad no debe ocupar el camión que es el reclamo más fuerte de un grupo pendiente**

**Encontrado verificando el Step 6b.6 contra el PDF real (no bloqueante
para el fix del Step 6b, que ya funciona para grupo 22 -- este es un
hallazgo NUEVO, la tercera vez que aparece la misma tensión de fondo).**

Grupo 22 ahora cae correctamente en F 350_3. Pero eso deja F 350_1 libre, y
grupo 5 (Tuxtepec, 3383 kg, SIN datos de afinidad) se procesa ANTES que
grupo 1 (Cosamaloapan, 2801 kg, afinidad real `F 350_1:9`) por ser más
pesado (first-fit-decreasing) -- y lo ocupa por simple desempate alfabético
(sin afinidad que lo distinga). Cuando por fin le toca su turno a grupo 1,
F 350_1 ya no tiene espacio, y cede a F 350_2.

Es el MISMO bug que ya existió una vez con `unidad_ref` (incidente
2026-08-12, documentado en el docstring de `_asignar_unidades`): un grupo
cediendo/procesándose primero puede ocupar de buena fe la unidad que en
realidad es el reclamo real de OTRO grupo que todavía no tuvo su turno. La
Task 2 quitó la protección de "reserva" que existía para `unidad_ref`
porque ya no había preferencia que proteger -- pero ahora que la afinidad
es una señal real de nuevo, hace falta la misma protección, aplicada a
afinidad en vez de a `unidad_ref`.

**Fix**: dentro de `_asignar_unidades`, antes de elegir unidad para un
grupo, calcula qué unidad es el reclamo de afinidad MÁS FUERTE de cada
grupo que todavía no tuvo su turno esta pasada (mismo día, `gids[idx+1:]`)
y exclúyela de los candidatos de este grupo -- salvo que sea la única
opción viable (mismo patrón "cede si es la única opción" que ya usa la
coocurrencia en esta misma función).

**Files:**
- Modify: `logic/convrp_logic.py` (`_asignar_unidades`)
- Test: `tests/test_convrp_logic.py`

- [ ] **Step 6c.1: Escribir los tests nuevos**

```python
def test_grupo_pesado_sin_afinidad_no_ocupa_la_reservada_de_uno_pendiente():
    # grupo 1 (mas pesado, SIN afinidad) se procesa primero; grupo 2 (mas
    # liviano, CON afinidad fuerte a Z_GRANDE) todavia no tuvo su turno.
    # grupo 1 no debe tomar Z_GRANDE si A_GRANDE (misma capacidad) tambien
    # le sirve -- debe dejarla reservada para grupo 2.
    plantilla = [
        _grupo(1, "FLEXIBLE", "LUNES", [1, 2], unidad_ref=None),
        _grupo(2, "FLEXIBLE", "LUNES", [3, 4], unidad_ref=None),
    ]
    pedidos = {1: 1600, 2: 1600, 3: 1000, 4: 1000}   # g1=3200 (mas pesado), g2=2000
    caps = {"A_GRANDE": 3900, "Z_GRANDE": 3900}
    cfg = dict(cfg_por_defecto(), chequear_tiempo=False,
               afinidad_unidad={2: {"Z_GRANDE": 9}})
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, caps, {"A_GRANDE": 99, "Z_GRANDE": 99}, cfg)
    assert sorted(m["sid"] for m in groups[("Z_GRANDE", "LUNES")]) == [3, 4], \
        "grupo 2 (afinidad fuerte, aun no tenia turno) debe quedarse con Z_GRANDE"
    assert sorted(m["sid"] for m in groups[("A_GRANDE", "LUNES")]) == [1, 2], \
        "grupo 1 (sin afinidad, mas pesado) debe ceder Z_GRANDE y usar A_GRANDE"


def test_reserva_de_afinidad_cede_si_es_la_unica_opcion():
    # Solo existe una unidad -- reservarla para el grupo pendiente dejaria
    # a grupo 1 sin ningun destino, asi que la reserva cede.
    plantilla = [
        _grupo(1, "FLEXIBLE", "LUNES", [1, 2], unidad_ref=None),
        _grupo(2, "FLEXIBLE", "LUNES", [3, 4], unidad_ref=None),
    ]
    pedidos = {1: 1600, 2: 1600, 3: 500, 4: 500}
    caps = {"Z_GRANDE": 5000}          # unica unidad de toda la flota
    cfg = dict(cfg_por_defecto(), chequear_tiempo=False,
               afinidad_unidad={2: {"Z_GRANDE": 9}})
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, caps, {"Z_GRANDE": 99}, cfg)
    assert len(groups) == 1 and ("Z_GRANDE", "LUNES") in groups
    sids = sorted(m["sid"] for ms in groups.values() for m in ms)
    assert sids == [1, 2, 3, 4]
```

- [ ] **Step 6c.2: Correr y confirmar que fallan**

```bash
python -m pytest tests/test_convrp_logic.py -k "grupo_pesado_sin_afinidad_no_ocupa or reserva_de_afinidad_cede" -v
```

Esperado: el primero FAIL (hoy grupo 1 ocupa Z_GRANDE por peso/alfabético);
el segundo ya PASA (única unidad, no hay nada que reservar).

- [ ] **Step 6c.3: Implementar la reserva de afinidad**

Dentro de `_asignar_unidades`, el loop es `for idx, gid in enumerate(gids):`
(ya existe el `idx` desde antes). Justo después de armar `compat` (la
lista ya filtrada por exclusión+coocurrencia, antes de calcular `af`/
`ordenadas`), agrega:

```python
            # Reserva de afinidad: el camión que es el reclamo MÁS FUERTE de
            # un grupo que aún no tuvo su turno esta pasada (más liviano, se
            # procesa después) no es destino válido para éste -- salvo que
            # sea la única opción viable. Mismo patrón que la reserva de
            # unidad_ref que existió antes de la Task 2 (incidente
            # 2026-08-12): sin esto, un grupo pesado sin afinidad real puede
            # ocupar de buena fe el camión que sí es el hogar histórico de
            # otro grupo más liviano que todavía no le tocaba su turno
            # (hallazgo real: Tuxtepec ocupaba F 350_1 antes que Cosamaloapan,
            # que tiene afinidad 9/9 semanas ahí).
            reservadas = set()
            for g2 in gids[idx + 1:]:
                af2 = (cfg.get("afinidad_unidad") or {}).get(asign[g2]["grupo"]) or {}
                if af2:
                    reservadas.add(max(af2, key=lambda u: af2[u]))
            compat_sin_reservar = [u for u in compat if u not in reservadas]
            compat = compat_sin_reservar or compat
```

Y en el bloque de último recurso (línea ~347-362, el que ya toca el Step
6b), filtra también por `reservadas` sin perder el chequeo de exclusión
total (`candidatas` original, sin filtrar, sigue siendo lo que decide si
cae en `SIN_UNIDAD_DISPONIBLE`):

```python
            if elegido is None and candidatas:
                kg_may = cfg.get("kg_mayoristas") or {}

                def _libre(u):
                    sids_u = _sids_de_ruta(asign, u, dia)
                    ocupado = sum(_num(pedidos.get(s)) + _num(kg_may.get(s))
                                  for s in sids_u)
                    return _num(vehiculos_cap.get(u)) - ocupado

                candidatas_ur = [u for u in candidatas if u not in reservadas] or candidatas
                elegido = min(candidatas_ur, key=lambda u: (-_libre(u), -_num(af.get(u)), str(u)))
            elif elegido is None:
```

(El resto del bloque `elif elegido is None:` -- el caso `SIN_UNIDAD_DISPONIBLE`
-- no cambia.)

- [ ] **Step 6c.4: Correr y confirmar que pasan**

```bash
python -m pytest tests/test_convrp_logic.py -k "grupo_pesado_sin_afinidad_no_ocupa or reserva_de_afinidad_cede" -v
```

- [ ] **Step 6c.5: Correr toda la suite**

```bash
python -m pytest tests/test_convrp_logic.py tests/test_convrp_integracion.py -v
```

Esperado: 76 passed (los 74 de antes + estos 2), 0 failed.

- [ ] **Step 6c.6: Verificar contra el escenario REAL antes de commitear (obligatorio, no te conformes con el test unitario)**

```bash
python scripts/pdf_convrp_preview.py "24 al 28 de agosto"
```

(Recuerda la danza de `git stash` para `logic/consolidacion_mayoristas.py`
si sigue corrupto sin commitear -- apártalo, corre, y restáuralo de
inmediato con `git stash pop`.)

Confirma en el resultado real:
- Cosamaloapan/Carlos A. Carrillo/Amatitlán → F 350_1
- San Andrés/Catemaco/Santiago Tuxtla/Juan Díaz Covarrubias → F 350_3
- Tierra Blanca sigue sin tocar ningún F350
- Ningún grupo quedó con `SIN_UNIDAD_DISPONIBLE` que no debiera (revisa las
  excepciones)

Si el resultado real todavía no coincide, NO commitees -- reporta BLOCKED
con el detalle exacto (qué unidad quedó cada grupo, tu hipótesis) para que
se decida el siguiente paso, en vez de intentar un cuarto parche por tu
cuenta.

- [ ] **Step 6c.7: Commit**

```bash
git add logic/convrp_logic.py tests/test_convrp_logic.py
git commit -m "$(cat <<'EOF'
Reserva de afinidad: protege el camion de un grupo pendiente con reclamo mas fuerte

Mismo patron que la reserva de unidad_ref que existio antes de la Task 2
(incidente 2026-08-12), aplicado ahora a afinidad: un grupo mas pesado sin
afinidad no puede ocupar el camion que es el reclamo mas fuerte de otro
grupo mas liviano que aun no tuvo su turno, salvo que sea la unica opcion
viable. Verificado contra el PDF real de la semana 24-28 agosto: ahora
Cosamaloapan cae en F 350_1 y San Andres/Santiago Tuxtla en F 350_3, como
corresponde historicamente.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

- [x] **Step 6d: Refinamiento post-revisión (ya hecho, commit `fd8cd14`)**

El revisor de calidad de la Task 9 (rango completo) encontró 2 huecos en la
reserva de afinidad, ninguno crítico:

1. **Arreglado**: la reserva no verificaba que el grupo pendiente pudiera
   usar su propio reclamo -- si su unidad de mayor afinidad estaba en SUS
   PROPIAS `unidades_excluidas` (mismo riesgo que KANGOO: dato histórico
   que ya no aplica), igual se reservaba y bloqueaba al grupo pesado sin
   proteger nada real. Se filtra `af2` por `not _excluida(a2, u)` antes de
   tomar el máximo. Test agregado y verificado que discrimina de verdad
   (falla contra el código sin este fix, con 3 excepciones de
   partición/relleno de más; pasa con 0 excepciones).
2. **Aceptado como trade-off, no arreglado**: la reserva no reintenta si
   deja al grupo pesado sin ninguna opción que pase las restricciones
   (podría empujarlo al último recurso, que no revisa restricciones). El
   revisor no encontró un caso real que lo dispare -- decisión explícita
   del usuario de documentarlo en vez de perseguir una 4ª iteración sin
   evidencia real. Si aparece en producción, tratarlo como una task nueva
   con su propio caso real, no como una extensión especulativa de ésta.

- [ ] **Step 7: Re-generar el PDF de la Task 8 Step 3 con el fix aplicado**

```bash
python scripts/pdf_convrp_preview.py "24 al 28 de agosto"
```

Confirma en el PDF resultante (ya se hizo, ver Task 9 -- este step queda
como referencia): Cosamaloapan/Carrillo/Amatitlán en F 350_1;
San Andrés/Catemaco/Santiago Tuxtla/Covarrubias en F 350_3, las 8
sucursales de grupo 22 juntas en la medida de lo posible (o, si la
partición por TIEMPO sigue siendo necesaria, que la pieza partida quede en
un F350 en vez de saltar a una unidad chica no relacionada); Tierra Blanca
sigue sin tocar ningún F350.

Resume en la conversación (no hace falta un documento nuevo): qué tests
quedaron, cuántos se borraron y por qué, y confirma que el PDF regenerado ya
no muestra Tierra Blanca en ningún F350.

---

## Task 10: TIEMPO ya no fuerza partir un grupo RIGIDO (decisión explícita del usuario)

**Pedido por el usuario tras revisar el resultado final**: Santiago Tuxtla 1
(sucursal 8 de grupo 22, RIGIDO) se sigue partiendo a J 18 porque el modelo
de TIEMPO calcula que las 8 paradas juntas no caben en la ventana horaria de
un solo día en un F350 -- confirmado con `_restriccion_violada` directo
contra los datos reales: peso (3402 kg) y volumen sobran de holgura, sólo
TIEMPO ata. El propio docstring del módulo ya documenta que este modelo
"sobrestima en rutas de muchas paradas chicas" -- exactamente este caso.

**Decisión del usuario (confirmada explícitamente, alcance sistema
completo)**: de aquí en adelante, una violación de **sólo TIEMPO** (sin PESO
ni VOLUMEN de por medio) **ya no fuerza partir un grupo RIGIDO** en ningún
lugar del sistema -- sólo PESO/VOLUMEN pueden. Cuando esto pasa, la ruta
queda con la composición intacta y se registra una excepción visible nueva
(`AVISO_TIEMPO_RIGIDO_NO_PARTIDO`) para que el despachador lo revise a
mano, en vez de fragmentar la ruta en silencio. Los grupos FLEXIBLE no
cambian: TIEMPO sigue pudiendo partirlos como hasta ahora (ceden más fácil,
no rompen una composición que el negocio espera junta).

**Alcance**: esto toca el bloque "Último recurso: partir" (Palanca 3) en
`construir_groups_desde_plantilla`, que NINGUNA task anterior de este plan
había tocado (siempre se documentó como "problema pre-existente, fuera de
alcance" -- hasta ahora, por pedido explícito del usuario).

**Files:**
- Modify: `logic/convrp_logic.py` (`construir_groups_desde_plantilla`,
  bloque "Último recurso: partir", línea ~732-762)
- Test: `tests/test_convrp_logic.py`

- [ ] **Step 1: Escribir los tests nuevos**

```python
def test_tiempo_solo_no_parte_un_rigido():
    # RIGIDO con 8 paradas chicas: peso y volumen sobran de holgura: la
    # ventana horaria es tan corta que SOLO el modelo de tiempo ata. No
    # debe partirse -- composición intacta, aviso registrado en vez de
    # PARTIDO_CAPACIDAD.
    plantilla = [_grupo(1, "RIGIDO", "LUNES", list(range(1, 9)),
                        unidad_ref="V1", dias_admisibles=["LUNES"])]
    pedidos = {i: 10 for i in range(1, 9)}          # peso irrelevante
    cfg = _cfg(chequear_tiempo=True, hora_salida_min=420, hora_cierre_min=430)
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, COORDS, plantilla, {"V1": 99999}, {"V1": 9999}, cfg)
    assert not any(e["tipo"] == "PARTIDO_CAPACIDAD" for e in exc), \
        "TIEMPO solo no debe partir un RIGIDO"
    assert len(groups[("V1", "LUNES")]) == 8, \
        "la composición del RIGIDO debe seguir completa"
    assert any(e["tipo"] == "AVISO_TIEMPO_RIGIDO_NO_PARTIDO" for e in exc)
```

- [ ] **Step 2: Correr y confirmar que falla**

```bash
python -m pytest tests/test_convrp_logic.py -k tiempo_solo_no_parte_un_rigido -v
```

Esperado: FAIL (hoy el grupo SÍ se parte por TIEMPO sin importar rigidez).

- [ ] **Step 3: Implementar el cambio**

En `logic/convrp_logic.py`, dentro del bloque "Último recurso: partir"
(línea ~732-762), justo después de `a = candidatos[0]` y antes de calcular
`metrica`/`orden`, agrega:

```python
            a = candidatos[0]
            if restr == "TIEMPO" and a["rigidez"] == "RIGIDO":
                # El modelo de tiempo sobrestima en rutas de muchas paradas
                # chicas (ver docstring del módulo) -- decisión de negocio
                # 2026-08-27: TIEMPO solo (sin PESO/VOLUMEN) ya no fuerza
                # partir un RIGIDO. Queda como aviso visible, composición
                # intacta, para que el despachador lo revise a mano.
                excepciones.append({
                    "tipo": "AVISO_TIEMPO_RIGIDO_NO_PARTIDO", "grupo": a["grupo"],
                    "rigidez": a["rigidez"], "restriccion": "TIEMPO",
                    "unidad": unidad, "dia": dia,
                    "motivo": f"{unidad}/{dia} excede el tiempo estimado pero "
                              f"es RIGIDO; no se parte (el modelo de tiempo "
                              f"sobrestima en rutas de muchas paradas chicas) "
                              f"-- revisar a mano si hace falta.",
                })
                break
            metrica = volumenes if restr == "VOLUMEN" else pedidos
```

(El resto del bloque, desde `orden = sorted(...)` en adelante, no cambia.)

- [ ] **Step 4: Correr y confirmar que pasa**

```bash
python -m pytest tests/test_convrp_logic.py -k tiempo_solo_no_parte_un_rigido -v
```

- [ ] **Step 5: Confirmar que las particiones existentes (PESO/VOLUMEN, y TIEMPO sobre FLEXIBLE) siguen intactas**

```bash
python -m pytest tests/test_convrp_logic.py -k "parte_rigido or particion or partido_capacidad or pedazo_partido" -v
```

Esperado: todos PASS sin cambios -- `test_parte_rigido_solo_si_ninguna_palanca_alcanza` y
`test_particion_de_rigido_es_determinista_pela_el_mayor` son PESO, no
TIEMPO, así que no deben verse afectados; confírmalo leyendo cada uno antes
de asumirlo. Si alguno usa TIEMPO sobre un RIGIDO como parte de su
escenario, ese SÍ necesita actualizarse (esperado, no un bug) -- ajusta su
aserción al nuevo comportamiento, no adivines.

- [ ] **Step 6: Correr toda la suite del proyecto**

```bash
python -m pytest tests/test_convrp_logic.py tests/test_convrp_integracion.py tests/test_plantilla_canonica.py -v
```

Esperado: 100% PASS.

- [ ] **Step 7: Verificar contra el escenario REAL antes de commitear (obligatorio)**

```bash
python scripts/pdf_convrp_preview.py "24 al 28 de agosto"
```

(Recuerda la danza de `git stash` para `logic/consolidacion_mayoristas.py`
si sigue corrupto sin commitear.)

Confirma en el resultado real:
- Las 8 sucursales de grupo 22 (Catemaco, Catemaco 2, Juan Díaz Covarrubias,
  San Andrés 1/2/3, Santiago Tuxtla 1/2) quedan TODAS juntas en F 350_3 el
  martes -- ninguna se va a J 18.
- Aparece una excepción `AVISO_TIEMPO_RIGIDO_NO_PARTIDO` para ese grupo
  (revisar, no silencioso).
- Cosamaloapan/Carrillo/Amatitlán sigue en F 350_1; Tierra Blanca sigue sin
  tocar ningún F350 (esto NO debería cambiar con este fix, pero verifícalo
  de todos modos -- mismo patrón de esta task: no te conformes con el test
  unitario).

Si el resultado real no coincide, no commitees -- reporta BLOCKED con el
detalle exacto.

- [ ] **Step 8: Commit**

```bash
git add logic/convrp_logic.py tests/test_convrp_logic.py
git commit -m "$(cat <<'EOF'
TIEMPO solo ya no fuerza partir un grupo RIGIDO (decision de negocio)

Pedido explicito del usuario: el modelo de tiempo sobrestima en rutas de
muchas paradas chicas (ya documentado en el modulo), y forzaba partir
grupo 22 (Santiago Tuxtla/San Andres/Catemaco, RIGIDO, 8 sucursales,
3402 kg -- peso y volumen sobran de holgura) separando Santiago Tuxtla 1
del resto. De aqui en adelante solo PESO/VOLUMEN pueden partir un RIGIDO;
TIEMPO solo queda como aviso visible (AVISO_TIEMPO_RIGIDO_NO_PARTIDO),
composicion intacta. FLEXIBLE no cambia. Verificado contra el PDF real:
las 8 sucursales de grupo 22 quedan juntas en F 350_3.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

- [x] **Step 9: Verificación end-to-end con Fase B (ya hecha)**

El revisor de calidad de la Task 10 marcó como riesgo no probado la
interacción con Fase B (`logic/tiempo_reubicacion.py`, `resolver_fuera_de_horario`)
-- corre en el flujo real (no en `pdf_convrp_preview.py`, que la salta) y
tiene su propia lógica de reubicación por tiempo, nunca antes tocada por
este plan.

Se verificó corriendo el flujo real completo (`generar_rutas_vrp_afinidad`
+ `generar_pdf` sin `rutas_inyectadas`) contra la logística real "24 al 28
de agosto". El primer intento pareció mostrar que Fase B separaba Santiago
Tuxtla 1 de nuevo -- investigado a fondo: era una fila vieja en
`modificaciones_rutas` (guardada antes de los fixes de las Tasks 9/10, sin
autorizar) que `generar_pdf` prefiere leer por encima de `asignaciones`
fresco. Se borró esa fila (con confirmación explícita del usuario) y se
volvió a generar: **grupo 22 queda con sus 8 sucursales juntas en F 350_3,
Cosamaloapan en F 350_1, Tierra Blanca sin tocar ningún F350** -- Fase B
NO deshace el fix. Sin cambios de código adicionales necesarios.
