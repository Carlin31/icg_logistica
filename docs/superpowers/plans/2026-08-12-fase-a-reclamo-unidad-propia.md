# Fase A: reclamo de unidad_ref propia antes de ceder — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reestructurar `_asignar_unidades` (logic/convrp_logic.py) en dos fases —cada grupo reclama primero su propia `unidad_ref`, y sólo después los que no pudieron ceden a otra— para que un grupo de paso nunca ocupe por accidente la unidad de referencia de otro grupo que todavía no le tocaba su turno.

**Architecture:** Hoy `_asignar_unidades` procesa TODOS los grupos de un día en una sola pasada por peso descendente, decidiendo en el mismo paso "¿me quedo con mi `unidad_ref`?" y "si no, ¿a cuál cedo?". Eso deja una ventana: un grupo que cede puede tomar una unidad vacía que en realidad es la referencia RÍGIDA de otro grupo que esa semana pesa un poco menos y aún no fue procesado. Como un grupo usando su PROPIA `unidad_ref` nunca pasa por el filtro de coocurrencia (ese filtro sólo se aplica a quien cede), los dos terminan compartiendo camión sin ningún precedente histórico y sin que ninguna excepción lo registre. La solución: separar en dos fases dentro del mismo bucle por día — Fase A (todos reclaman su propia referencia, en orden de peso para resolver contención entre grupos que comparten la misma unidad) y Fase B (los que quedaron sin unidad ceden, viendo ya el reparto COMPLETO de la Fase A). El resto del motor (mover de día, partir) no cambia.

**Tech Stack:** Python puro (sin BD), pytest. Archivo bajo prueba: `logic/convrp_logic.py`. Caso real de referencia: logística "27 al 31 de julio del 2026", grupo 19 (Amatitlán/Carlos A. Carrillo 2, `unidad_ref=F 350_1`) coló en `T 20` antes que grupo 11 (El Tejar/Antón Lizardo/Jamapa, RIGIDO, `unidad_ref=T 20`).

---

## File Structure

- Modify: `logistica_icg/logic/convrp_logic.py` — función `_asignar_unidades` (única función que cambia; todo lo demás en el archivo queda intacto).
- Modify: `logistica_icg/tests/test_convrp_logic.py` — un test nuevo que reproduce el bug de raíz con datos sintéticos mínimos (sin BD, mismo estilo que el resto del archivo).

No se crean archivos nuevos. No se toca ninguna otra función ni módulo.

---

### Task 1: Test que reproduce el bug de raíz (RED)

**Files:**
- Modify: `logistica_icg/tests/test_convrp_logic.py`

- [ ] **Step 1: Ubicar el punto de inserción**

Abre `logistica_icg/tests/test_convrp_logic.py` y busca el bloque de comentario que empieza en la línea 637:

```python
# ═══════════════════════════════════════════════════════════════════════════
# Coocurrencia al ceder unidad/día — encontrado en producción el 2026-08-10:
```

El test nuevo va **inmediatamente después** de `test_al_ceder_unidad_coocurrencia_cede_si_es_la_unica_opcion` (termina en la línea 711 con `# única unidad: se acepta sin precedente`) y **antes** de la línea 714 (`# ═══...` del bloque "Palanca 4").

- [ ] **Step 2: Escribir el test que falla**

Inserta este bloque completo (respeta las líneas en blanco antes/después):

```python
# ═══════════════════════════════════════════════════════════════════════════
# Fase A: reclamo de unidad_ref propia — encontrado en producción 2026-08-12:
# grupo 19 (Amatitlán/Carlos A. Carrillo 2, ref F 350_1) no cabía por TIEMPO
# en F 350_1 y cedía. Entre las unidades vacías, T 20 resultó "compatible"
# (nada había ahí todavía) y el desempate la eligió. El problema: T 20 era
# la unidad de referencia RÍGIDA de grupo 11 (El Tejar/Antón Lizardo/
# Jamapa), que esa semana pesaba un poco MENOS que grupo 19 y por eso se
# procesaba después. Cuando le tocó su turno, grupo 11 encontró que su
# propia unidad_ref ya tenía cupo y la usó directo — un grupo usando SU
# PROPIA unidad_ref nunca pasa por el filtro de coocurrencia, así que los
# dos quedaron juntos sin ningún precedente histórico entre ellos y sin
# ninguna excepción registrada.
# ═══════════════════════════════════════════════════════════════════════════

def test_fase_a_reclama_unidad_propia_antes_que_otro_grupo_la_ocupe_cediendo():
    # grupo 1 (1800 kg, sin unidad_ref válida en la flota -> cede desde el
    # principio) se procesa ANTES que grupo 2 (100 kg, ref=V2, RIGIDO) por
    # ser más pesado. Sin la Fase A reclamando primero, grupo 1 puede
    # colarse en V2 (vacía en ese momento) antes que grupo 2 llegue a
    # reclamarla -- y como no hay coocurrencia entre 1 y 2, ese apilado no
    # tiene ningún precedente real.
    plantilla = [
        _grupo(1, "FLEXIBLE", "LUNES", [1, 2], unidad_ref="SIN_FLOTA"),
        _grupo(2, "RIGIDO", "LUNES", [3], unidad_ref="V2"),
    ]
    pedidos = {1: 900, 2: 900, 3: 100}   # grupo1 = 1800 kg, grupo2 = 100 kg
    caps = {"V2": 5000, "V3": 5000}
    coocurrencia = {}                      # 1 y 2 nunca compartieron camión-día
    groups, exc = construir_groups_desde_plantilla(
        pedidos, {}, {}, plantilla, caps, {},
        _sin_tiempo(coocurrencia_grupos=coocurrencia))
    # grupo 2 reclama V2 sin obstáculos, sólo con lo suyo
    assert sorted(m["sid"] for m in groups[("V2", "LUNES")]) == [3]
    # grupo 1 nunca se apila en V2 (cero precedente con grupo 2)
    v2_sids = [m["sid"] for m in groups[("V2", "LUNES")]]
    assert 1 not in v2_sids and 2 not in v2_sids
```

- [ ] **Step 3: Correr el test y confirmar que falla por el motivo correcto**

Run: `cd logistica_icg && python -m pytest tests/test_convrp_logic.py::test_fase_a_reclama_unidad_propia_antes_que_otro_grupo_la_ocupe_cediendo -v`

Expected: `FAIL` — el assert de `sorted(m["sid"] for m in groups[("V2", "LUNES")]) == [3]` falla porque `groups[("V2", "LUNES")]` trae también los sids `1` y `2` (grupo 1 se coló). Si falla con `KeyError` o algo distinto a ese assert, revisa que copiaste `plantilla`/`pedidos`/`caps` tal cual — no debe fallar por un typo.

---

### Task 2: Reestructurar `_asignar_unidades` en dos fases (GREEN)

**Files:**
- Modify: `logistica_icg/logic/convrp_logic.py:248-363`

- [ ] **Step 1: Reemplazar la función completa**

Ubica la función `_asignar_unidades` (empieza en la línea 248 con `def _asignar_unidades(...)` y termina en la línea 363 con `return desviaciones`, justo antes de `def _dia_alternativo`). Reemplázala completa por:

```python
def _asignar_unidades(asign, pedidos, volumenes, coords,
                      vehiculos_cap, vehiculos_vol, cfg):
    """
    Reparte los grupos de cada día entre las unidades, en DOS fases:

      Fase A — cada grupo reclama su propia `unidad_ref` (si la tiene y le
      cabe), en orden de peso: así, si varios grupos comparten la misma
      referencia y no caben todos juntos, los más pesados consolidan
      primero y los que sobran quedan pendientes para la Fase B.

      Fase B — los grupos que no reclamaron unidad propia (no tenían, o no
      les cupo) ceden a otra, en orden de peso, viendo YA el reparto
      completo de la Fase A.

    Por qué en dos fases y no en una sola pasada por peso (como antes):
    hallado en producción 2026-08-12 — un grupo cediendo podía "ocupar" de
    buena fe una unidad vacía que en realidad era la `unidad_ref` de OTRO
    grupo que esa semana pesaba un poco menos y todavía no le tocaba su
    turno (grupo 19, Amatitlán/Carlos A. Carrillo 2, se coló en T 20 antes
    que grupo 11, El Tejar/Antón Lizardo/Jamapa, RIGIDO, dueño legítimo de
    T 20). Como un grupo usando SU PROPIA unidad_ref nunca pasa por el
    filtro de coocurrencia, los dos quedaban juntos sin ningún precedente
    histórico entre ellos, y ninguna de las reglas existentes lo detectaba.
    Con la Fase A reclamando primero, el filtro de coocurrencia de la
    Fase B ve siempre a los ocupantes LEGÍTIMOS, nunca a uno accidental.

    El resto del contrato es el mismo que antes: `unidad_ref` es
    PREFERENCIA con penalización; si varios grupos comparten `unidad_ref`
    el mismo día, se distribuyen en la flota libre en vez de saturar una
    unidad y terminar partiendo grupos.

    Devuelve la lista de excepciones MOVIDO_UNIDAD (desviaciones de la
    preferencia). Es idempotente: se puede volver a llamar tras mover un día.
    """
    for a in asign.values():
        a["unidad"] = None
    por_dia: dict = {}
    for gid in sorted(asign):
        por_dia.setdefault(asign[gid]["dia"], []).append(gid)

    desviaciones: list = []
    for dia in sorted(por_dia, key=_orden_dia):
        # los grupos más pesados primero (first-fit decreasing), desempate por id
        gids = sorted(por_dia[dia],
                      key=lambda g: (-_kg_grupo(asign[g], pedidos), g))

        # ── Fase A: cada grupo reclama su propia unidad_ref ──────────────
        pendientes: list = []       # [(gid, restr_ref), ...] para la Fase B
        for gid in gids:
            a = asign[gid]
            ref = a["unidad_ref"] if a["unidad_ref"] in vehiculos_cap else None
            if a.get("unidad_forzada") and ref:
                # Regla de negocio puntual: esta unidad NUNCA se cede, ni por
                # sobrecupo (hallado en producción 2026-08-12: el enganche de
                # mayoristas por zona oscila sin converger, y según en qué
                # pasada se corte intercambiaba Tuxtepec/Cosamaloapan entre
                # F 350_2 y F 350_1). No participa del reparto normal — si de
                # verdad no cabe, la partición de más abajo se encarga, pero
                # nunca se mueve el grupo entero a otra unidad en silencio.
                a["unidad"] = ref
                continue
            if not ref:
                pendientes.append((gid, None))
                continue
            destino = _sids_de_ruta(asign, ref, dia) + list(a["miembros"])
            violacion = _restriccion_violada(
                sorted(destino), ref, pedidos, volumenes, coords,
                vehiculos_cap, vehiculos_vol, cfg, dia=dia)
            if violacion is None:
                a["unidad"] = ref
            else:
                pendientes.append((gid, violacion))

        # ── Fase B: los que no reclamaron unidad propia ceden a otra ─────
        for gid, restr_ref in pendientes:
            a = asign[gid]
            ref = a["unidad_ref"] if a["unidad_ref"] in vehiculos_cap else None
            # Al ceder la preferida se busca CONSOLIDAR: primero las unidades que
            # ya llevan carga ese día (la más llena que todavía admita el grupo),
            # y sólo al final una vacía. Ordenar por carga ASCENDENTE dispersaría
            # —abriría un viaje nuevo por grupo— y en el histórico un viaje
            # (unidad, día) lleva ~1.4 grupos, no 1.0.
            # Al ceder la preferida, entre unidades EMPATADAS en carga decide la
            # AFINIDAD histórica del grupo, no el abecedario. Sin esto, el g24
            # (Playa Vicente) se iba a F 350_1 sobre F 350_2 sólo porque ambas
            # estaban vacías y "F 350_1" ordena antes: le abría a esa unidad un
            # día de trabajo que la operación no hace, dejando libre la que sí
            # lleva esa carga.
            af = (cfg.get("afinidad_unidad") or {}).get(a["grupo"]) or {}
            otras = sorted(
                [u for u in vehiculos_cap if u != ref],
                key=lambda u: (-sum(_num(pedidos.get(s))
                                    for s in _sids_de_ruta(asign, u, dia)),
                               -_num(af.get(u)), str(u)))
            # Al ceder la preferida no cualquier consolidación sirve: si la
            # unidad candidata ya lleva ese día un grupo con el que nunca
            # compartió camión en el histórico, se descarta primero — salvo
            # que sea la única opción (ver `_compatible_historico`). Como la
            # Fase A ya terminó para TODO el día, aquí siempre se ve el
            # reparto final de quién quedó en cada unidad por derecho propio
            # — nunca a un ocupante que llegó ahí por casualidad de orden.
            coocurrencia = cfg.get("coocurrencia_grupos")
            otras_compat = [u for u in otras
                           if _compatible_historico(a["grupo"], u, dia, asign, coocurrencia)]
            otras = otras_compat or otras
            elegido = None
            for unidad in otras:
                destino = _sids_de_ruta(asign, unidad, dia) + list(a["miembros"])
                violacion = _restriccion_violada(
                    sorted(destino), unidad, pedidos, volumenes, coords,
                    vehiculos_cap, vehiculos_vol, cfg, dia=dia)
                if violacion is None:
                    elegido = unidad
                    break
            if elegido is None:
                # Ningún destino admite el grupo completo (p. ej. pesa más que
                # cualquier vehículo). Va a la unidad con MÁS ESPACIO LIBRE del
                # día, para que la partición posterior pele lo mínimo.
                #
                # "Más vacía" NO es "la que lleva menos kilos": con todas las
                # unidades en cero eso desempataba por nombre y mandaba un grupo
                # de 3,981 kg a un T 25 de 1,300 (semana del 6-10 abril). Para
                # cuando corría la partición, las unidades grandes ya estaban
                # ocupadas por otros grupos y lo pelado no encontraba destino:
                # la ruta se quedaba al 306 %. Espacio libre = capacidad menos
                # lo que ya lleva (incluida la carga de mayoristas anclada).
                candidatos = sorted(vehiculos_cap) or ["VEHICULO"]

                kg_may = cfg.get("kg_mayoristas") or {}

                def _libre(u):
                    sids_u = _sids_de_ruta(asign, u, dia)
                    ocupado = sum(_num(pedidos.get(s)) + _num(kg_may.get(s))
                                  for s in sids_u)
                    return _num(vehiculos_cap.get(u)) - ocupado

                elegido = min(candidatos, key=lambda u: (-_libre(u), str(u)))
                if ref and _restriccion_violada(
                        sorted(a["miembros"]), ref, pedidos, volumenes, coords,
                        vehiculos_cap, vehiculos_vol, cfg, dia=dia) is None:
                    elegido = ref     # cabe sola en su preferida: consérvala
            a["unidad"] = elegido
            if ref and elegido != ref:
                desviaciones.append({
                    "tipo": "MOVIDO_UNIDAD", "grupo": a["grupo"],
                    "rigidez": a["rigidez"], "restriccion": restr_ref,
                    "origen_carga": _origen_de_carga(
                        sorted(_sids_de_ruta(asign, ref, dia) + list(a["miembros"])),
                        ref, pedidos, volumenes, coords, vehiculos_cap,
                        vehiculos_vol, cfg, dia, cfg.get("kg_mayoristas")),
                    "desde_unidad": ref, "a_unidad": elegido, "dia": dia,
                    "motivo": f"{ref}/{dia} sin cupo por {restr_ref}; "
                              f"se cede la unidad de referencia",
                })
    return desviaciones
```

Nota para quien implemente: el cuerpo de la Fase B es prácticamente idéntico al bucle único de antes — sólo cambió (1) que ya no intenta `ref` dentro del propio bucle (Fase A ya lo intentó y guardó el motivo en `restr_ref`), y (2) que ahora corre en un `for` separado, después de que la Fase A terminó para TODOS los grupos del día. No inventes lógica nueva más allá de mover el código como se muestra arriba.

- [ ] **Step 2: Correr el test nuevo y confirmar que pasa**

Run: `cd logistica_icg && python -m pytest tests/test_convrp_logic.py::test_fase_a_reclama_unidad_propia_antes_que_otro_grupo_la_ocupe_cediendo -v`

Expected: `PASS`

- [ ] **Step 3: Correr todo `test_convrp_logic.py` — no debe romper nada existente**

Run: `cd logistica_icg && python -m pytest tests/test_convrp_logic.py -v`

Expected: todos los tests `PASS` (49 tests: 48 que ya existían + el nuevo de Task 1). Si algo falla, NO ajustes el test que falló — vuelve a leer el diff de la Fase A/B contra el original y busca qué se movió de más. Los tests existentes describen contratos ya validados (determinismo, partición, coocurrencia, afinidad); si alguno se rompe, el bug está en la reestructuración, no en el test.

- [ ] **Step 4: Correr la suite completa del proyecto**

Run: `cd logistica_icg && python -m pytest tests/ -q`

Expected: todos los tests `PASS` (o `skipped` los que ya se saltaban antes por falta de BD — no debe haber ningún `FAILED` nuevo).

---

### Task 3: Verificar contra los datos reales de producción

**Files:** ninguno (sólo verificación, no escribe código)

- [ ] **Step 1: Correr la vista previa del ConVRP sin persistir nada**

Run: `cd logistica_icg && python scripts/pdf_convrp_preview.py "27 al 31 de julio"`

Expected: termina con `PDF generado (sin persistir nada): /tmp/icg_pdf\<timestamp>.pdf` y una línea `ConVRP: plantilla v17, N viajes, M excepciones, ...`. Anota la ruta del PDF que imprime.

- [ ] **Step 2: Confirmar que grupo 19 y grupo 11 ya NO comparten camión-día**

Run (ajusta la ruta del PDF al valor que imprimió el Step 1):

```bash
python -c "
import pdfplumber
with pdfplumber.open(r'/tmp/icg_pdf/<TIMESTAMP>.pdf') as pdf:
    full = '\n'.join(p.extract_text() or '' for p in pdf.pages)
for pi, l in enumerate(full.splitlines()):
    if 'Amatitl' in l or 'Carrillo 2' in l or 'El Tejar' in l or 'Antón Lizardo' in l or 'Jamapa' in l:
        print(l)
"
```

Expected: revisa manualmente que las líneas de "Amatitlán"/"Carlos A. Carrillo 2" (grupo 19) aparezcan bajo una unidad/día distinto de "El Tejar"/"Antón Lizardo"/"Jamapa" (grupo 11) — o si terminan en la misma unidad, que sea porque genuinamente cupieron con precedente real, no por casualidad de orden. Si siguen juntos sin motivo, no continúes al Task 4: vuelve a Task 2 y revisa la reestructuración.

- [ ] **Step 3: Confirmar que el caso ya arreglado (Tuxtepec/Cosamaloapan) sigue bien**

En el mismo texto extraído, confirma que "Tuxtepec 1 (Centro)" aparece bajo `F 350_2 · SERGIO` y "Carlos A. Carrillo" (sin el "2") bajo `F 350_1 · FELIPE` — el fix de `unidad_forzada` (grupos 1 y 4) no debe haberse movido con esta reestructuración.

---

### Task 4: Commit

**Files:**
- `logistica_icg/logic/convrp_logic.py`
- `logistica_icg/tests/test_convrp_logic.py`

- [ ] **Step 1: Revisar el diff**

Run: `cd logistica_icg && git diff -- logic/convrp_logic.py tests/test_convrp_logic.py`

Expected: sólo cambios dentro de `_asignar_unidades` (restructuración en dos fases) y el test nuevo — nada más en esos dos archivos.

- [ ] **Step 2: Stage y commit**

```bash
cd logistica_icg
git add logic/convrp_logic.py tests/test_convrp_logic.py
git commit -m "$(cat <<'EOF'
fix: _asignar_unidades reclama unidad_ref propia antes de ceder (Fase A/B)

Un grupo cediendo podia ocupar de buena fe una unidad vacia que en
realidad era la unidad_ref de otro grupo mas liviano esta semana que
todavia no le tocaba su turno -- y como un grupo usando su propia
unidad_ref nunca pasaba por el filtro de coocurrencia, terminaban
compartiendo camion sin ningun precedente historico (grupo 19,
Amatitlan/Carrillo 2, colandose en T 20 antes que grupo 11, El Tejar/
Anton Lizardo/Jamapa, RIGIDO, dueno legitimo). Separar en Fase A
(reclamo de unidad propia, todos antes de ceder nadie) y Fase B (cede
viendo ya el reparto final) hace que el filtro de coocurrencia siempre
vea a los ocupantes legitimos.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 3: Confirmar el commit**

Run: `cd logistica_icg && git log --oneline -1`

Expected: el commit nuevo aparece como el más reciente.

---

## Notas para quien ejecute este plan

- No se toca `unidad_forzada`, `_elegir_mejor_pasada`, ni ningún otro fix de esta sesión — este plan es exclusivamente la reestructuración de `_asignar_unidades`.
- Si el Task 1 (test RED) pasa sin fallar contra el código actual, DETENTE — significa que el bug no se reprodujo con esos datos sintéticos, y hay que ajustar `pedidos`/`caps` antes de seguir (no avances a Task 2 sin haber visto el test fallar primero).
- Si el Task 3 (datos reales) muestra que grupo 19/11 siguen mezclados de forma injustificada, el problema puede estar en la afinidad/coocurrencia real de esos grupos específicos, no en la reestructuración — repórtalo en vez de forzar el resultado.
