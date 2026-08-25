# Diseño: Palanca 5 — relleno de capacidad libre (ConVRP)

**Fecha:** 2026-08-25
**Estado:** Aprobado (diseño) — pendiente plan de implementación.
**Módulo:** `logic/convrp_logic.py` (motor activo en producción, `CONVRP_ACTIVO=True`
desde 2026-08-07). No toca el motor de afinidad (`historico_logic.py`, solo
usado si `CONVRP_ACTIVO=False`).

## 1. Problema observado

Logística del 24 al 28 de agosto de 2026, ruta del martes de `F 350_1`:
Cosamaluapan 1/2/3 + Carrillo 1, **2,318 kg** sobre una unidad con tope
efectivo de **3,900 kg** (CAP-4) — 59 % de ocupación. Sobraba capacidad de
sobra para el grupo 19 (Amatitlán + Carlos A. Carrillo 2), que según
`plantilla_grupo`/`unidades_afines` tiene a **F 350_1 martes como hogar
dominante (7 de 9 semanas históricas)**, y aun así no apareció en esa ruta.

Las cuatro palancas existentes en `construir_groups_desde_plantilla()`
(mover de unidad, mover de día, partir, consolidar solitarias — ver
docstring del módulo) solo actúan cuando una ruta está **sobrecargada** o
**solitaria** (una sola parada). Ninguna revisa una ruta que ya es válida
—ni sobrecargada ni solitaria— para ver si algo más cabría. El resultado:
un grupo FLEXIBLE puede ceder su lugar durante la resolución de sobrecupo
(en `_candidatos_a_mover`, los FLEXIBLE se sacrifican antes que los
RÍGIDOS) y **nadie lo trae de vuelta** aunque después quede espacio libre
en su unidad/día preferido — exactamente el patrón que explicaría por qué
el grupo 19 no terminó en `F 350_1/MARTES` pese a ser su hogar dominante.

## 2. Objetivo

Agregar una quinta palanca, `_rellenar_capacidad_libre()`, que corre **al
final** de `construir_groups_desde_plantilla()` (después de la Palanca 4)
y, para cada ruta activa con capacidad libre, busca un grupo compatible que
quepa completo y lo mueve — priorizando devolver a su unidad/día
preferido a los grupos que hoy están desviados de él.

No es una corrección de sobrecupo (no hay violación que resolver); es una
optimización oportunista: si no encuentra nada que mover, la ruta
simplemente se queda con la capacidad libre que ya tenía.

## 3. Decisiones

- **Unidad de movimiento: el GRUPO completo**, nunca una sucursal suelta ni
  una fracción de grupo — mismo modelo que el resto del archivo (el día es
  atributo del grupo, se mueve en bloque). Esta palanca nunca parte un
  grupo; partir es exclusivo de la Palanca 3, que ya corrió antes.
- **Orden de recorrido de rutas destino:** `_rutas_activas(asign)` (orden
  determinista día→unidad), procesadas de **menor a mayor % de ocupación**
  — la ruta más vacía elige primero.
- **Candidatos considerados para cada ruta destino:** todos los grupos
  actualmente asignados a OTRA ruta (cualquier unidad, cualquier día
  dentro de `dias_admisibles` del grupo — igual que la Palanca 2), excepto:
  - grupos con `unidad_forzada=True` (nunca se mueven, misma regla que
    `_asignar_unidades`).
  - el propio grupo si ya está en su ruta destino evaluada.
- **Validación de cada candidato** (idéntica a las demás palancas, mismas
  funciones reutilizadas):
  1. `_compatible_historico(grupo, unidad_destino, dia_destino, asign, coocurrencia)`
     — no junta grupos sin precedente de co-viaje real. Sin datos de
     coocurrencia, no bloquea (degradación segura, igual que hoy).
  2. `_restriccion_violada(...)` sobre `sids_destino_actuales + miembros_candidato`
     — debe devolver `None` (no rompe PESO, VOLUMEN ni TIEMPO). El peso de
     mayoristas ya anclado (`kg_mayoristas`) viaja implícito: se sigue
     sumando vía el mismo parámetro `kg_may` que ya usan las demás palancas,
     tanto para los miembros ya en la ruta como para el candidato.
- **Criterio de selección entre candidatos válidos (dos fases, en orden):**
  1. **Regreso a casa primero.** Entre los candidatos válidos para esta
     ruta destino, si alguno tiene a `(unidad_destino, dia_destino)` como
     su `unidad_ref`/`dia_preferido` (es decir, está desviado de su hogar y
     esta ruta destino ES su hogar), se elige ese. Si compiten varios así
     (poco común), desempate por el que deje la ocupación más cerca del
     100 % sin pasarse.
  2. **Si ninguno está "regresando a casa"**: se elige el candidato válido
     que deje la ruta destino más cerca del 100 % de ocupación sin
     excederla — mismo criterio "peso ante todo" que ya usan
     `_elegir_destino_por_peso` (motor de afinidad) y el resto de este
     archivo.
- **Cada grupo se mueve como máximo UNA vez en esta pasada.** Una sola
  pasada sobre `_rutas_activas`, sin punto fijo/iteración — igual
  justificación que la Palanca 2 y `_consolidar_solitarios`: evita
  reproducir el tipo de oscilación ya documentado con el enganche de
  mayoristas por zona (nota 2026-08-12 en `_asignar_unidades`).
- **Rutas que quedan vacías:** si un grupo era el único contenido de su
  `(unidad, día)` y se mueve, esa combinación desaparece de `groups` de
  forma natural (mismo comportamiento que ya ocurre hoy con las otras
  palancas — no requiere código especial). Consistente con la decisión de
  que un camión de menos ese día es un resultado válido, no un error.
- **Registro/auditoría:** cada movimiento agrega una excepción nueva,
  mismo formato que `MOVIDO_DIA`/`CONSOLIDADO_SOLITARIA`:
  ```
  {"tipo": "RELLENO_CAPACIDAD_LIBRE", "grupo": ..., "rigidez": ...,
   "restriccion": None, "desde_unidad": ..., "desde_dia": ...,
   "a_unidad": ..., "a_dia": ...,
   "motivo_regreso_hogar": bool,   # True si fue el caso "regreso a casa"
   "motivo": "..."}
  ```
- **Interruptor dedicado:** `CONVRP_RELLENO_CAPACIDAD = True` (constante
  de módulo, junto a `CONVRP_AVISO_PARADAS` etc., también expuesta en
  `cfg_por_defecto()`). Permite apagar solo esta palanca sin tocar
  `CONVRP_ACTIVO` si algo sale mal en producción — mismo patrón de
  degradación segura que `REBALANCEO_GEOGRAFICO` en `historico_logic.py`.

## 4. Algoritmo (dentro de `construir_groups_desde_plantilla`, tras la Palanca 4)

```
si no CONVRP_RELLENO_CAPACIDAD: devolver sin cambios

movidos = set()
para (unidad, dia) en _rutas_activas(asign) ordenadas por % ocupación ascendente:
    libre = vehiculos_cap[unidad] - kg_actual(unidad, dia)   # incluye mayoristas
    si libre <= 0: continuar

    candidatos = [
        a para a en asign.values()
        si (a["unidad"], a["dia"]) != (unidad, dia)
        y a["grupo"] not in movidos
        y not a.get("unidad_forzada")
        y dia en a["dias_admisibles"]          # el destino debe ser admisible
        y _compatible_historico(a["grupo"], unidad, dia, asign, coocurrencia)
        y _restriccion_violada(sids_destino_actuales + a["miembros"], unidad,
                                ..., dia=dia) es None
    ]
    si candidatos vacío: continuar

    en_casa = [a para a en candidatos
               si a["unidad_ref"] == unidad y a["dia_preferido"] == dia
               y (a["unidad"], a["dia"]) != (unidad_ref, dia_preferido)]  # desviado hoy
    elegido = mejor_por_ocupacion(en_casa) si en_casa
              si no: mejor_por_ocupacion(candidatos)

    mover elegido a (unidad, dia); movidos.add(elegido["grupo"])
    registrar excepción RELLENO_CAPACIDAD_LIBRE
```

## 5. Casos borde

- **Dos rutas vacías compiten por el mismo candidato "en casa":** no puede
  pasar — un grupo solo tiene un `(unidad_ref, dia_preferido)`, así que
  como mucho una ruta destino lo reclama como "regreso a casa".
- **Grupo candidato ya movido por esta misma palanca en una iteración
  anterior de la pasada:** excluido vía `movidos` — nunca se mueve dos
  veces en la misma corrida.
- **Ninguna ruta con espacio libre:** la palanca no hace nada, `groups`
  sale idéntico a como lo dejó la Palanca 4.
- **`dias_admisibles` del candidato no incluye el día de la ruta destino:**
  excluido — el día sigue siendo atributo del grupo, no se fuerza fuera de
  su conjunto admisible.
- **Falla del módulo:** no se agrega manejo de errores propio — esta
  palanca corre dentro de `construir_groups_desde_plantilla()`, que ya
  vive detrás del `try/except` de `historico_logic.py` (degradación al
  motor de afinidad si `CONVRP_ACTIVO` truena, con el error impreso de
  forma visible). Una excepción aquí se comporta igual que una excepción
  en cualquier otra palanca existente del mismo archivo.

## 6. Fuera de alcance (fase 1)

- **Mayoristas no se reasignan por esta palanca.** Su peso ya viene sumado
  en `kg_mayoristas`/`kg_may` y se respeta al validar si un grupo candidato
  cabe en la ruta destino, pero esta palanca no mueve mayoristas de zona
  (eso sigue siendo trabajo exclusivo de `enganche_zona.py`).
- **El motor de afinidad** (`historico_logic.py`, activo solo si
  `CONVRP_ACTIVO=False`) no se toca.
- **No se re-versiona la plantilla canónica** ni se corrigen sus datos.

## 7. Verificación

Gate obligatorio ya establecido en `CLAUDE.md`/`MIGRACION_STATUS.md` para
cualquier cambio al builder de ConVRP: `python scripts/smoke_convrp.py`
contra las 9 semanas canónicas, confirmando en particular:
- **`PARTIDO_CAPACIDAD` no aumenta** (esta palanca corre después de partir
  y nunca parte grupos, así que el conteo de rígidos partidos no debería
  cambiar).
- El caso real del grupo 19 (Amatitlán + Carlos A. Carrillo 2) queda
  cubierto por un test de regresión explícito con datos reales (ver plan
  de implementación).

## 8. Pruebas (para el plan de implementación)

1. Ruta con espacio libre y un candidato compatible que cabe → se mueve,
   se registra `RELLENO_CAPACIDAD_LIBRE`.
2. Ruta con espacio libre pero ningún candidato compatible (coocurrencia
   nunca vista) → no se mueve nada.
3. Ruta con espacio libre pero el único candidato que cabría tiene
   `unidad_forzada=True` → no se mueve.
4. Ruta con espacio libre pero el candidato no tiene ese día en
   `dias_admisibles` → no se mueve.
5. **Regreso a casa gana sobre maximizar %:** dos candidatos válidos caben,
   uno deja la ruta al 95 % pero NO es su hogar, otro deja la ruta al 80 %
   pero SÍ es su `unidad_ref`/`dia_preferido` → se elige el segundo.
6. **Sin candidato "en casa"**, se elige el que maximiza % de ocupación
   entre los disponibles.
7. **Ruta origen queda vacía tras el movimiento** → esa `(unidad, día)`
   desaparece de `groups`.
8. **Cada grupo se mueve como máximo una vez** en la misma corrida, aunque
   múltiples rutas destino lo consideren candidato.
9. **Regresión con datos reales:** fixture basado en el grupo 19
   (Amatitlán + Carlos A. Carrillo 2, FLEXIBLE, `unidad_ref`/dominante
   `F 350_1` martes) desviado a otra unidad/día por sobrecupo simulado, con
   `F 350_1/MARTES` liberando espacio después → el grupo 19 regresa.
10. **`CONVRP_RELLENO_CAPACIDAD=False`** → `construir_groups_desde_plantilla`
    se comporta idéntico a antes de esta palanca.
11. **`scripts/smoke_convrp.py`** corrido contra las 9 semanas canónicas,
    sin aumento de `PARTIDO_CAPACIDAD`.
