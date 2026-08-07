"""
logic/convrp_validacion.py

Arnés de validación del ConVRP por ORIGEN MÓVIL (rolling origin). Fuera de
producción: no escribe nada, sólo mide.

Por qué origen móvil: la plantilla de producción se construyó con las 9 semanas,
así que medir fidelidad contra esas mismas 9 sería medir sobre el entrenamiento.
Aquí se entrena con las semanas 1..k y se mide contra la k+1, avanzando k de 4 a
8 → 5 mediciones fuera de muestra.

SIN FUGA TEMPORAL: la ventana de "últimas 4 semanas" que fija el día de cada
grupo son las últimas 4 del ENTRENAMIENTO de ese pliegue, no del histórico
completo. Usar las últimas 4 globales inflaría el número.

El builder se invoca directo (`convrp_logic`), nunca a través del motor: si por
dentro corriera el motor de afinidad, el número no significaría nada.
"""
from collections import Counter
from itertools import combinations

UMBRAL_COVIAJE = 0.60      # dos sucursales se agrupan si viajaron juntas ≥60 %
VENTANA_DIA = 4            # semanas (las últimas del entrenamiento) que fijan el día


# ── reconstrucción de grupos de co-viaje desde el histórico ────────────────
def _viajes_de_semana(filas: list) -> dict:
    """{(vehiculo, dia): {num_tienda, …}} de una semana (sólo sucursales)."""
    viajes: dict = {}
    for f in filas or []:
        if f.get("tipo") == "mayorista" or f.get("id_sucursal") is None:
            continue
        clave = (str(f.get("vehiculo")), str(f.get("dia_semana")).upper())
        viajes.setdefault(clave, set()).add(int(f["id_sucursal"]))
    return viajes


def _kg_por_semana(semanas: list) -> list:
    """[{num_tienda: kg}] — una entrada por semana."""
    salida = []
    for filas in semanas or []:
        kg: dict = {}
        for f in filas or []:
            if f.get("tipo") == "mayorista" or f.get("id_sucursal") is None:
                continue
            sid = int(f["id_sucursal"])
            kg[sid] = kg.get(sid, 0.0) + float(f.get("kg_entrega") or 0)
        salida.append(kg)
    return salida


def construir_plantilla_desde(semanas: list, umbral: float = UMBRAL_COVIAJE,
                              ventana_dia: int = VENTANA_DIA,
                              ventana_unidad: int = -1,
                              vehiculos_cap: dict = None) -> list:
    """
    Deriva la plantilla canónica de una lista de semanas (cada una = lista de
    filas). Mismo método que el análisis offline: co-viaje ≥`umbral` con enlace
    completo (todos los pares del grupo deben cumplirlo).

    El DÍA y la UNIDAD de cada grupo salen de las ÚLTIMAS `ventana_dia` semanas
    de ESTA lista — nunca del histórico completo (evita fuga temporal).
    """
    viajes_por_semana = [_viajes_de_semana(s) for s in semanas]

    presencia, cooc = Counter(), Counter()
    for viajes in viajes_por_semana:
        presentes = set().union(*viajes.values()) if viajes else set()
        for s in presentes:
            presencia[s] += 1
        for grupo in viajes.values():
            for a, b in combinations(sorted(grupo), 2):
                cooc[(a, b)] += 1

    ady: dict = {}
    for (a, b), n in cooc.items():
        ambas = min(presencia[a], presencia[b])
        if ambas and n / ambas >= umbral:
            ady.setdefault(a, set()).add(b)
            ady.setdefault(b, set()).add(a)

    # componentes conexas → cliques completos (enlace completo), determinista
    nodos = set(presencia) | set(ady)
    vistos, componentes = set(), []
    for n in sorted(nodos):
        if n in vistos:
            continue
        pila, comp = [n], set()
        while pila:
            x = pila.pop()
            if x in vistos:
                continue
            vistos.add(x); comp.add(x)
            pila.extend(sorted(ady.get(x, set()) - vistos))
        componentes.append(comp)

    def cliques(comp):
        restantes, salida = set(comp), []
        while restantes:
            semilla = max(sorted(restantes),
                          key=lambda x: len(ady.get(x, set()) & restantes))
            cl = {semilla}
            for cand in sorted(restantes - {semilla},
                               key=lambda x: (-len(ady.get(x, set()) & restantes), x)):
                if all(cand in ady.get(m, ()) for m in cl):
                    cl.add(cand)
            salida.append(cl); restantes -= cl
        return salida

    grupos_sids = []
    for comp in componentes:
        grupos_sids.extend([comp] if len(comp) == 1 else cliques(comp))

    # día y unidad: voto sobre las últimas `ventana_*` semanas del entrenamiento.
    # Son parámetros SEPARADOS a propósito: son dos preguntas distintas ("¿qué
    # día opera este grupo?" vs "¿en qué camión?") y midiéndolas juntas no se
    # puede saber cuál de las dos movió el resultado. `ventana_unidad=-1`
    # significa "la misma que el día" (comportamiento previo).
    if ventana_unidad == -1:
        ventana_unidad = ventana_dia
    rec_dia = viajes_por_semana[-ventana_dia:] if ventana_dia else viajes_por_semana
    rec_uni = viajes_por_semana[-ventana_unidad:] if ventana_unidad else viajes_por_semana
    plantilla = []
    for i, sids in enumerate(sorted(grupos_sids, key=lambda g: sorted(g)), 1):
        votos_dia, votos_uni, dias_vistos = Counter(), Counter(), Counter()
        for viajes in rec_dia:
            for (_, dia), miembros in viajes.items():
                if sids & miembros:
                    votos_dia[dia] += 1
        for viajes in rec_uni:
            for (veh, _), miembros in viajes.items():
                if sids & miembros:
                    votos_uni[veh] += 1
        for viajes in viajes_por_semana:      # días admisibles: TODO el entrenamiento
            for (veh, dia), miembros in viajes.items():
                if sids & miembros:
                    dias_vistos[dia] += 1
        if not votos_dia:
            continue
        dia = sorted(votos_dia.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        uni = sorted(votos_uni.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        total = sum(dias_vistos.values()) or 1
        adm = [d for d, n in sorted(dias_vistos.items(), key=lambda kv: (-kv[1], kv[0]))
               if n / total >= 0.15]
        if dia not in adm:
            adm = [dia] + adm
        else:
            adm = [dia] + [d for d in adm if d != dia]
        plantilla.append(dict(
            grupo=i, rigidez=("RIGIDO" if len(sids) > 1 else "FLEXIBLE"),
            dia=dia, dia_preferido=dia, unidad_ref=uni,
            sucursales=sorted(sids), dias_admisibles=adm))

    # `unidad_ref` como asignación GLOBAL por día, si se pasa la flota. Sin ella
    # queda la moda por grupo (comportamiento previo).
    if vehiculos_cap:
        kg_sem = _kg_por_semana(semanas)
        afinidad, kg_tipico, kg_minimo = {}, {}, {}
        for g in plantilla:
            sids = set(g["sucursales"])
            votos = Counter()
            for viajes in viajes_por_semana:
                por_uni = Counter()
                for (veh, _), miembros in viajes.items():
                    if sids & miembros:
                        por_uni[veh] += len(sids & miembros)
                if por_uni:
                    votos[por_uni.most_common(1)[0][0]] += 1
            afinidad[g["grupo"]] = dict(votos)
            totales = [sum(kg.get(s, 0.0) for s in sids) for kg in kg_sem]
            kg_tipico[g["grupo"]] = kg_representativo(totales, percentil=0.5)
            kg_minimo[g["grupo"]] = kg_representativo(totales)
        # viajes por día que hace la operación, promediados sobre el
        # ENTRENAMIENTO (nunca sobre la semana que se va a medir).
        por_dia = Counter()
        for viajes in viajes_por_semana:
            for (_, dia) in viajes:
                por_dia[str(dia).upper()] += 1
        n_semanas = max(len(viajes_por_semana), 1)
        objetivo = {d: max(1, round(n / n_semanas)) for d, n in por_dia.items()}
        ref = asignar_unidad_ref(plantilla, afinidad, kg_tipico, vehiculos_cap,
                                 viajes_objetivo=objetivo, kg_minimo=kg_minimo)
        for g in plantilla:
            g["unidad_ref"] = ref.get(g["grupo"], g["unidad_ref"])
            # el motor la usa para desempatar cuando cede la unidad de referencia
            g["afinidad_unidad"] = dict(afinidad.get(g["grupo"]) or {})
    return plantilla


PERCENTIL_CAPACIDAD = 0.90


def kg_representativo(totales, percentil: float = PERCENTIL_CAPACIDAD) -> float:
    """
    Cuántos kg hay que reservarle a un grupo para elegirle camión.

    NO es la mediana. La mediana describe la semana típica, pero la unidad de
    referencia tiene que aguantar las semanas normales-altas: el g38 (Tierra
    Blanca 1) tiene mediana 1,091 kg y con ella quedó referenciado a un T 23 de
    1,500 — su semana pico son 1,529 y no cabe, así que el motor lo cedía a un
    F 350 y le abría a esa unidad un día de trabajo que la operación no hace.

    Tampoco es el máximo: una sola semana atípica haría reservar un tráiler todo
    el año. Percentil 90 de las semanas CON demanda.
    """
    v = sorted(float(x) for x in (totales or []) if float(x) > 0)
    if not v:
        return 0.0
    i = max(0, min(len(v) - 1, int(round(len(v) * float(percentil))) - 1))
    return v[i]


def asignar_unidad_ref(grupos: list, afinidad: dict, kg_tipico: dict,
                       vehiculos_cap: dict, viajes_objetivo: dict = None,
                       kg_minimo: dict = None) -> dict:
    """
    Resuelve `unidad_ref` como una ASIGNACIÓN GLOBAL por día, no como la moda
    de cada grupo por separado.

    La moda por grupo ignora que la unidad es un recurso compartido: si T 17_2
    es la unidad más frecuente de 8 grupos, los 8 la piden y sólo uno la tiene,
    mientras J 18, J 19 y K 20 no son referencia de ninguno y el motor sólo las
    toca por desbordamiento. Medido sobre 9 semanas eso da T 17_1 con +17 días
    de trabajo contra la realidad y K 16 con −12.

    Aquí la restricción se aplica donde existe: **por día**. Dos grupos de días
    distintos pueden compartir unidad; dos del mismo día sólo si caben juntos.

    `viajes_objetivo` ({dia: nº de viajes}) acota cuántas unidades se abren por
    día. Sin él, la afinidad sola abre **un viaje por grupo**: fuera de muestra
    eso subía de 29.4 a 34.6 viajes/semana contra 29.8 reales y hundía la
    utilización de 65 % a 58 %. La empresa hace ~6 viajes por día llevando ~1.4
    grupos cada uno; el objetivo reproduce esa consolidación. Es preferencia, no
    tope: si la carga del día no cabe, se abre otra unidad.

    `kg_tipico` y `kg_minimo` son DOS cantidades distintas y mezclarlas rompe:
      - `kg_tipico` (mediana) es cuánto OCUPA el grupo cuando se empacan varios
        en el mismo camión-día. Usar aquí el pico reservaría capacidad que casi
        nunca se usa y abriría viajes de más.
      - `kg_minimo` (percentil alto) es cuánto tiene que AGUANTAR el camión.
        Filtra candidatos, no reserva. Sin él, el g38 (mediana 1,091 kg) quedó
        referenciado a un T 23 de 1,500 y su semana alta de 1,529 no cabía: el
        motor lo cedía a un F 350 y le abría a esa unidad un día de trabajo que
        la operación no hace.

    grupos        : [{grupo, dia_preferido}]
    afinidad      : {grupo: {unidad: nº de semanas que viajó ahí}}
    kg_tipico     : {grupo: kg de una semana típica}
    kg_minimo     : {grupo: kg que la unidad debe poder cargar}
    vehiculos_cap : {unidad: kg}

    Determinista: los grupos se procesan por kg descendente (los que menos
    opciones tienen primero) y los empates se rompen por nombre de unidad.
    """
    ocupado: dict = {}          # (unidad, dia) -> kg ya comprometidos
    abiertas: dict = {}         # dia -> {unidades ya en uso}
    ref: dict = {}
    objetivo = {str(d).upper(): int(n) for d, n in (viajes_objetivo or {}).items()}
    orden = sorted(grupos, key=lambda g: (-float(kg_tipico.get(g["grupo"], 0) or 0),
                                          int(g["grupo"])))
    for g in orden:
        gid = int(g["grupo"])
        dia = str(g.get("dia_preferido") or g.get("dia") or "").upper()
        kg = float(kg_tipico.get(gid, 0) or 0)
        af = {str(u): n for u, n in (afinidad.get(gid) or {}).items()}
        minimo = float((kg_minimo or {}).get(gid, 0) or 0)
        en_uso = abiertas.setdefault(dia, set())
        tope = objetivo.get(dia)
        # Con el cupo del día agotado sólo se consideran las unidades ya
        # abiertas: consolidar en vez de estrenar camión.
        universo = (sorted(en_uso) if (tope is not None and len(en_uso) >= tope)
                    else sorted(vehiculos_cap))
        # el camión tiene que poder con la semana alta del grupo
        aptas = [u for u in universo if float(vehiculos_cap[u]) + 1e-6 >= minimo]
        universo = aptas or universo
        # más afinidad primero; sin historia, la unidad menos comprometida ese
        # día (reparte en vez de apilar); desempate final por nombre.
        cands = sorted(universo,
                       key=lambda u: (-af.get(str(u), 0),
                                      ocupado.get((u, dia), 0.0), str(u)))
        elegida = None
        for u in cands:
            if ocupado.get((u, dia), 0.0) + kg <= float(vehiculos_cap[u]) + 1e-6:
                elegida = u
                break
        if elegida is None and set(universo) != set(vehiculos_cap):
            # el cupo del día no alcanza: se cede el objetivo antes que
            # sobrecargar (es preferencia, no tope duro)
            libres = [u for u in vehiculos_cap
                      if float(vehiculos_cap[u]) + 1e-6 >= minimo] or list(vehiculos_cap)
            cands = sorted(libres,
                           key=lambda u: (-af.get(str(u), 0),
                                          ocupado.get((u, dia), 0.0), str(u)))
            for u in cands:
                if ocupado.get((u, dia), 0.0) + kg <= float(vehiculos_cap[u]) + 1e-6:
                    elegida = u
                    break
        if elegida is None:
            # no cabe en ninguna: la de más espacio libre (el motor la partirá)
            elegida = min(cands,
                          key=lambda u: (-(float(vehiculos_cap[u])
                                           - ocupado.get((u, dia), 0.0)), str(u)))
        ref[gid] = elegida
        ocupado[(elegida, dia)] = ocupado.get((elegida, dia), 0.0) + kg
        en_uso.add(elegida)
    return ref


# ── medición de fidelidad de un pliegue ────────────────────────────────────
def medir_fidelidad(groups: dict, filas_reales: list,
                    admisibles_por_sucursal: dict = None) -> dict:
    """
    Compara las rutas producidas contra lo que REALMENTE pasó esa semana.

    - `dia_correcto`: % de sucursales cuyo día coincide con el real. Métrica
      DURA, para análisis interno: castiga al motor por no acertar algo que es
      variable por naturaleza (21 de 42 grupos tienen techo <70 % porque sus
      días alternan de verdad; el oráculo retrospectivo tope es 71.5 %).
    - `dia_admisible`: % de sucursales cuyo día cae DENTRO del conjunto de días
      en que la empresa realmente opera ese grupo. Es la métrica para presentar:
      mide si el plan es algo que la empresa hace, no si adivinó la semana.
    - `grupo_correcto`: % de sucursales cuyos compañeros de viaje son los mismos
      que en la realidad (coincidencia exacta del conjunto de co-viajeros).
    - `compañeros_jaccard`: solape medio del conjunto de co-viajeros (métrica
      suave: penaliza menos una ruta que agrega un grupo extra).
    """
    reales = _viajes_de_semana(filas_reales)
    dia_real, comp_real = {}, {}
    for (_, dia), miembros in reales.items():
        for s in miembros:
            dia_real[s] = dia
            comp_real[s] = set(miembros) - {s}

    dia_mio, comp_mio = {}, {}
    for (_, dia), ms in groups.items():
        sids = {m["sid"] for m in ms}
        for s in sids:
            dia_mio[s] = str(dia).upper()
            comp_mio[s] = sids - {s}

    evaluables = sorted(set(dia_real) & set(dia_mio))
    if not evaluables:
        return {"n": 0}
    n_dia = sum(1 for s in evaluables if dia_mio[s] == dia_real[s])
    n_grp = sum(1 for s in evaluables if comp_mio[s] == comp_real[s])
    jac = []
    for s in evaluables:
        a, b = comp_mio[s], comp_real[s]
        jac.append(1.0 if not a and not b else len(a & b) / max(len(a | b), 1))
    salida = {
        "n": len(evaluables),
        "dia_correcto_pct": round(n_dia / len(evaluables) * 100, 1),
        "grupo_correcto_pct": round(n_grp / len(evaluables) * 100, 1),
        "companeros_jaccard": round(sum(jac) / len(jac) * 100, 1),
        "dia_admisible_pct": None,
        "sin_evaluar": sorted(set(dia_real) ^ set(dia_mio)),
    }
    if admisibles_por_sucursal:
        con_adm = [s for s in evaluables if admisibles_por_sucursal.get(s)]
        if con_adm:
            ok = sum(1 for s in con_adm
                     if dia_mio[s] in {str(d).upper()
                                       for d in admisibles_por_sucursal[s]})
            salida["dia_admisible_pct"] = round(ok / len(con_adm) * 100, 1)
    return salida
