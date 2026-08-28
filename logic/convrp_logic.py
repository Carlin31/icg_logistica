"""
logic/convrp_logic.py

ConVRP — el VRP como AJUSTADOR sobre la plantilla canónica (Fase 2).

En vez de generar rutas desde cero, parte de la plantilla histórica y sólo
reoptimiza donde la demanda de la semana no cabe.

Modelo (decisiones fijas del negocio):
  - El GRUPO son las sucursales del grupo canónico CON PEDIDO esa semana, no el
    roster completo: un rígido de 6 con demanda en 4 viaja de 4.
  - La UNIDAD se elige por PESO: cada grupo, sin excepción y sin preferencia,
    toma entre las unidades no excluidas y compatibles la de MENOR capacidad
    que le alcanza, desempatando por CONSOLIDACIÓN (la que ya lleva carga ese
    día) y luego por nombre. `unidad_ref` / `unidades_afines` / `unidad_forzada`
    son vestigiales: se guardan y se propagan, pero ya no se leen para decidir
    unidad. `unidades_excluidas` es la única prohibición dura -- ninguna
    palanca puede violarla, ni siquiera en el último recurso.
  - El DÍA es atributo del GRUPO (se mueve en bloque completo, nunca parcial) y
    sólo dentro de sus `dias_admisibles`; fuera de ese conjunto no se mueve.
  - Rigidez de COMPOSICIÓN y flexibilidad de DÍA son dimensiones independientes:
    un rígido puede cambiar de día si su conjunto admisible lo permite.

Orden de palancas ante sobrecupo (de evidencia más débil a más fuerte):
    1) asignación de UNIDAD por peso dentro del mismo día (sin preferencia
       que mover: cada grupo elige directo la unidad no excluida que le
       alcanza, ver arriba)
    2) mover de DÍA dentro de los admisibles
    3) PARTIR el grupo — último recurso, determinista y siempre registrado.
       Excepción: un RIGIDO que sólo viola TIEMPO (nunca si también viola
       PESO/VOLUMEN) ya no se parte -- el modelo de tiempo sobrestima en
       rutas de muchas paradas chicas (decisión de negocio 2026-08-27);
       queda con la composición intacta y un aviso visible
       (AVISO_TIEMPO_RIGIDO_NO_PARTIDO) en vez de fragmentarse en silencio.
    4) CONSOLIDAR solitarias — ninguna ruta se queda con una sola sucursal
       pudiendo sumarse a una activa compatible con cupo
    5) RELLENAR capacidad libre — grupos ya desviados regresan a su unidad/día
       preferido si hay una ruta con espacio y precedente histórico.
       DESACTIVADA por defecto desde 2026-08-27 (ver `CONVRP_RELLENO_CAPACIDAD`):
       su premisa de "hogar preferido" ya no aplica sin preferencia, y
       terminaba arrastrando grupos bien puestos a camiones sobredimensionados
       sólo por tener espacio libre.

Determinismo: todo recorrido es sobre claves ordenadas; sin aleatoriedad ni
dependencia del orden de los diccionarios de entrada. Cada grupo se mueve a lo
sumo UNA vez por corrida y los barridos están acotados por `max_iteraciones`:
eso impide el encadenamiento infinito (mover un grupo satura el día destino y
dispara otro movimiento).

Cada excepción registra QUÉ restricción ató (PESO / VOLUMEN / TIEMPO), para
poder distinguir después alivio real de alivio fantasma — el modelo de tiempo
sobrestima en rutas de muchas paradas chicas (ver nota de calibración).
"""
from logic.logistica_tiempo import evaluar_ruta_por_tiempo

DIAS_ORDEN = ["LUNES", "MARTES", "MIERCOLES", "JUEVES", "VIERNES", "SABADO", "DOMINGO"]

# ── configuración (constantes; el llamador puede sobreescribir vía cfg) ─────
CONVRP_AVISO_PARADAS = 10        # sólo AVISA rutas largas; nunca bloquea ni parte
CONVRP_MAX_ITERACIONES = 3       # tope de barridos: acota la cascada de días
CONVRP_CHEQUEAR_TIEMPO = True
CONVRP_HORA_SALIDA_MIN = 420     # 07:00
CONVRP_HORA_CIERRE_MIN = 1200    # 20:00 (cierre de tiendas)
CONVRP_VELOCIDAD_KMH = 35.0
# True = velocidad haversine-equivalente calibrada por longitud de tramo
# (55.5 km/h en tramos largos, 37.8 en cortos; medido contra OSRM sobre 875
# tramos reales). Con la constante única de 35 km/h el trayecto matriz→clúster
# se inflaba ~2.5 h y provocaba particiones de rígidos por una violación de
# tiempo inexistente.
CONVRP_VELOCIDAD_POR_TRAMO = True
CONVRP_DEPOT = (18.87, -96.95)

# Interruptor dedicado de la Palanca 5 (relleno de capacidad libre).
# DESACTIVADO desde 2026-08-27: su premisa (grupo "desviado" de su
# unidad_ref/dia_preferido regresa a rellenar espacio libre) ya no aplica
# sin preferencia -- desde la Task 2, casi todo grupo bien asignado por
# peso "parece desviado" ante este chequeo, asi que la palanca terminaba
# arrastrando grupos bien puestos (p. ej. T 25, 92% de uso) a camiones
# mucho mas grandes con espacio libre (F350, ~38% de uso), exactamente lo
# opuesto al objetivo de este proyecto. Se deja el codigo y el interruptor
# (no se borra `_rellenar_capacidad_libre`) por si hace falta revertir.
CONVRP_RELLENO_CAPACIDAD = False


def cfg_por_defecto() -> dict:
    return {
        "aviso_paradas": CONVRP_AVISO_PARADAS,
        "max_iteraciones": CONVRP_MAX_ITERACIONES,
        "chequear_tiempo": CONVRP_CHEQUEAR_TIEMPO,
        "hora_salida_min": CONVRP_HORA_SALIDA_MIN,
        "hora_cierre_min": CONVRP_HORA_CIERRE_MIN,
        "velocidad_kmh": CONVRP_VELOCIDAD_KMH,
        "velocidad_por_tramo": CONVRP_VELOCIDAD_POR_TRAMO,
        "depot": CONVRP_DEPOT,
        "relleno_capacidad": CONVRP_RELLENO_CAPACIDAD,
    }


def _orden_dia(dia: str) -> int:
    try:
        return DIAS_ORDEN.index(str(dia).upper())
    except ValueError:
        return len(DIAS_ORDEN)


def _num(x) -> float:
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def _grupos_de_ruta(asign, unidad, dia):
    return [a["grupo"] for a in asign.values() if a["unidad"] == unidad and a["dia"] == dia]


def _compatible_historico(gid, unidad, dia, asign, coocurrencia) -> bool:
    """
    True si `gid` puede compartir camión/día con lo que ya está asignado en
    (unidad, dia), según si ALGUNA vez compartieron viaje en el histórico real
    (`coocurrencia`, ver `convrp_validacion.coocurrencia_grupos`). Sin datos
    de coocurrencia, no bloquea (degradación segura).

    Por qué coocurrencia y no distancia: se probó con centroide/60 km primero
    y la distancia no es una señal confiable para esta decisión — hay pares
    reales que SÍ viajaron juntos a 84 km (grupos 9/19) y una pareja que NUNCA
    coincidió a sólo 58 km (grupos 19/22, hallado el 2026-08-10: al ceder
    `unidad_ref` por TIEMPO, el grupo 19 caía a J 19, que esa semana ya
    llevaba Temascal/Los Naranjos — ningún precedente en 11 semanas, aunque la
    distancia por sí sola no lo hubiera bloqueado). El histórico de
    coocurrencia responde la pregunta correcta directamente.
    """
    if coocurrencia is None:
        return True
    for gid2 in _grupos_de_ruta(asign, unidad, dia):
        if gid2 != gid and coocurrencia.get(frozenset((gid, gid2)), 0) == 0:
            return False
    return True


# ── evaluación de una ruta (qué restricción ata, si alguna) ─────────────────
def _horario_del_dia(dia, cfg):
    """
    (salida, cierre) en minutos para ese día. `config_dias` NO es uniforme: el
    lunes sale 11:00 y el resto 07:00 — cablear 07:00 regalaría 4 h los lunes
    justo en la restricción que más decide. Cae al default si no hay entrada.
    """
    por_dia = cfg.get("horarios_por_dia") or {}
    par = por_dia.get(str(dia).upper())
    if par:
        return float(par[0]), float(par[1])
    return (float(cfg.get("hora_salida_min", CONVRP_HORA_SALIDA_MIN)),
            float(cfg.get("hora_cierre_min", CONVRP_HORA_CIERRE_MIN)))


_SIN_MAY = object()   # sentinela: "evaluar SIN la carga de mayoristas"


def _restriccion_violada(sids, unidad, pedidos, volumenes, coords,
                         vehiculos_cap, vehiculos_vol, cfg, dia=None,
                         kg_mayoristas=None):
    """Devuelve 'PESO' | 'VOLUMEN' | 'TIEMPO' — la PRIMERA que satura — o None.

    `kg_mayoristas` es la carga de mayoristas enganchada a cada sucursal-ancla:
    al concentrar los mayoristas en la ruta de su zona, esa carga puede saturar
    la ruta que la recibe (AYOZINTEPEC mete 877 kg/sem sobre un grupo que en
    Lores lleva 310 — 2.8x)."""
    if not sids:
        return None
    if kg_mayoristas is None:                 # por defecto: la del cfg
        kg_mayoristas = cfg.get("kg_mayoristas") or {}
    elif kg_mayoristas is _SIN_MAY:
        kg_mayoristas = {}
    kg = sum(_num(pedidos.get(s)) for s in sids)
    if kg_mayoristas:
        kg += sum(_num(kg_mayoristas.get(s)) for s in sids)
    cap = _num(vehiculos_cap.get(unidad)) or float("inf")
    if kg > cap:
        return "PESO"
    m3 = sum(_num(volumenes.get(s)) for s in sids)
    cap_v = vehiculos_vol.get(unidad)
    if cap_v is not None and m3 > _num(cap_v):
        return "VOLUMEN"
    if cfg.get("chequear_tiempo"):
        paradas = []
        for s in sids:
            c = coords.get(s)
            if c is None:
                continue
            paradas.append({"latitud": c[0], "longitud": c[1],
                            "peso_kg": _num(pedidos.get(s))})
        if paradas:
            salida, cierre = _horario_del_dia(dia, cfg)
            out = evaluar_ruta_por_tiempo(
                paradas, cfg.get("depot", CONVRP_DEPOT), salida, cierre,
                velocidad_kmh=cfg.get("velocidad_kmh", CONVRP_VELOCIDAD_KMH),
                por_tramo=cfg.get("velocidad_por_tramo", True))
            if any(not p["entregable_por_tiempo"] for p in out):
                return "TIEMPO"
    return None


def _origen_de_carga(sids, unidad, pedidos, volumenes, coords,
                     vehiculos_cap, vehiculos_vol, cfg, dia, kg_mayoristas):
    """
    'LORES' | 'MAYORISTAS' | 'AMBAS' | None — qué empujó la ruta sobre el límite.

    Se evalúa quitando cada fuente por separado: si sin mayoristas la ruta cabe,
    los mayoristas la saturaron; si sin la demanda Lores cabe, fue Lores. Sin
    este campo, tras el enganche por zona se verían rígidos partiéndose sin
    poder decir por qué.
    """
    if not kg_mayoristas:
        return "LORES"
    solo_lores = _restriccion_violada(sids, unidad, pedidos, volumenes, coords,
                                      vehiculos_cap, vehiculos_vol, cfg, dia,
                                      kg_mayoristas=_SIN_MAY)
    vacio = {s: 0 for s in sids}
    solo_may = _restriccion_violada(sids, unidad, vacio, volumenes, coords,
                                    vehiculos_cap, vehiculos_vol, cfg, dia,
                                    kg_mayoristas=kg_mayoristas)
    if solo_lores and solo_may:
        return "AMBAS"
    if solo_may:
        return "MAYORISTAS"
    if solo_lores:
        return "LORES"
    return "AMBAS"      # ninguna sola satura, pero juntas sí


def _sids_de_ruta(asign, unidad, dia):
    sids = []
    for gid in sorted(asign):
        a = asign[gid]
        if a["unidad"] == unidad and a["dia"] == dia:
            sids.extend(a["miembros"])
    return sorted(sids)


def _rutas_activas(asign):
    """Claves (unidad, dia) presentes, en orden determinista (día, unidad)."""
    claves = {(a["unidad"], a["dia"]) for a in asign.values()}
    return sorted(claves, key=lambda k: (_orden_dia(k[1]), str(k[0])))


def _evaluar(asign, unidad, dia, pedidos, volumenes, coords,
             vehiculos_cap, vehiculos_vol, cfg):
    return _restriccion_violada(_sids_de_ruta(asign, unidad, dia), unidad,
                                pedidos, volumenes, coords,
                                vehiculos_cap, vehiculos_vol, cfg, dia=dia)


def _kg_grupo(a, pedidos):
    return sum(_num(pedidos.get(s)) for s in a["miembros"])


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


def _candidatos_a_mover(asign, unidad, dia, pedidos):
    """Grupos de la ruta, ordenados: FLEXIBLE antes que RIGIDO, luego el de mayor
    peso (alivia más), desempate por id de grupo ascendente."""
    en_ruta = [asign[g] for g in sorted(asign)
               if asign[g]["unidad"] == unidad and asign[g]["dia"] == dia]
    return sorted(en_ruta, key=lambda a: (0 if a["rigidez"] != "RIGIDO" else 1,
                                          -_kg_grupo(a, pedidos), a["grupo"]))


def _unidad_alternativa(asign, a, pedidos, volumenes, coords,
                        vehiculos_cap, vehiculos_vol, cfg):
    """Otra unidad, MISMO día, donde el grupo quepa sin saturarla. Nunca una
    de `unidades_excluidas` del grupo; entre las que le alcanzan, prueba
    primero la de menor capacidad, desempatando por afinidad histórica
    (`cfg["afinidad_unidad"]`) cuando dos o más quedan empatadas en
    capacidad."""
    af = (cfg.get("afinidad_unidad") or {}).get(a["grupo"]) or {}
    candidatas = sorted((u for u in vehiculos_cap if not _excluida(a, u)),
                        key=lambda u: (_num(vehiculos_cap.get(u)),
                                       -_num(af.get(u)), str(u)))
    for unidad in candidatas:
        if unidad == a["unidad"]:
            continue
        if not _respeta_exclusividad(asign, a, unidad, a["dia"]):
            continue
        destino = _sids_de_ruta(asign, unidad, a["dia"]) + list(a["miembros"])
        if _restriccion_violada(sorted(destino), unidad, pedidos, volumenes,
                                coords, vehiculos_cap, vehiculos_vol, cfg,
                                dia=a["dia"]) is None:
            return unidad
    return None


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


def _asignar_unidades(asign, pedidos, volumenes, coords,
                      vehiculos_cap, vehiculos_vol, cfg):
    """
    Reparte los grupos de cada día entre las unidades: una sola pasada por
    peso descendente (first-fit decreasing). Cada grupo elige, entre las
    unidades NO excluidas (`unidades_excluidas` del grupo) y compatibles por
    coocurrencia que le alcanzan, la de MENOR capacidad -- nunca manda un
    grupo chico a una unidad grande de más si una chica ya le alcanza --
    desempatando por CONSOLIDACIÓN (la que ya lleva carga ese día, para no
    abrir un viaje nuevo: en el histórico un viaje lleva ~1.4 grupos, no 1.0),
    luego por AFINIDAD HISTÓRICA (`cfg["afinidad_unidad"]`: cuántas semanas
    ese grupo cayó en esa unidad -- sólo decide cuando capacidad y
    consolidación ya quedaron empatadas entre dos o más candidatos) y por
    último por nombre.

    No hay preferencia de unidad: todo grupo pasa por el mismo criterio,
    sin importar `unidad_ref` (vestigial, ya no se lee para decidir) ni
    `unidad_forzada` (ídem). `unidades_excluidas` es la única prohibición
    dura -- se aplica también en el ÚLTIMO RECURSO (ninguna unidad admite al
    grupo completo): ahí se elige la no excluida con más espacio libre, para
    que la partición posterior pele lo mínimo, pero jamás una excluida.

    RESERVA DE AFINIDAD: antes de elegir, cada grupo excluye de sus
    candidatos al camión que es el reclamo de afinidad MÁS FUERTE de
    cualquier grupo que todavía no tuvo su turno esta pasada (mismo día, más
    liviano, se procesa después) -- salvo que sea la única opción viable
    (mismo patrón "cede si es la única opción" que ya usa la coocurrencia
    arriba). Sin esto, un grupo pesado sin afinidad real puede ocupar de
    buena fe el camión que es el hogar histórico de otro grupo más liviano
    que todavía no le tocaba su turno (mismo tipo de bug que el incidente de
    `unidad_ref` del 2026-08-12, ahora aplicado a afinidad).

    La reserva sólo aplica si el reclamo ajeno es ESTRICTAMENTE más fuerte
    que el reclamo propio del grupo actual a esa misma unidad -- un empate o
    un reclamo más débil no le quita nada (hallazgo real: grupo con afinidad
    4.0 a T 23 perdía esa unidad ante un reclamo ajeno de sólo 2.0, porque
    la reserva no comparaba fuerza, sólo existencia).

    TOPE MÁXIMO DE LA FLOTA: si respetar la reserva de afinidad empuja a un
    grupo SIN afinidad propia hacia la unidad más grande de la flota (p. ej.
    F350) -- aunque una unidad chica/mediana reservada para otro grupo siga
    vacía y le alcance de sobra --, se cede la reserva para esa elección
    puntual, siempre que ignorarla ofrezca algo por DEBAJO del tope máximo
    (nunca se "roba" una reservada que también es del tope máximo -- eso
    rompería el caso F350-vs-F350 de la reserva de arriba). Hallazgo real:
    grupo Tuxtepec (1109 kg, sin afinidad) caía en F 350_1/JUEVES con T 20 y
    T 23 vacíos al lado, sólo por estar reservados para otros grupos del
    mismo día que, en la práctica, casi siempre tenían también otra opción.

    Si `unidades_excluidas` deja la flota entera afuera para un grupo (no
    debería pasar en operación normal -- ver spec), se registra una
    excepción SIN_UNIDAD_DISPONIBLE y el grupo queda con el sentinel
    "SIN_UNIDAD" (nunca el nombre de una unidad real), para revisión manual.

    Devuelve la lista de excepciones SIN_UNIDAD_DISPONIBLE. Es idempotente:
    se puede volver a llamar tras mover un día.
    """
    for a in asign.values():
        if not a.get("exclusivo"):
            a["unidad"] = None
    por_dia: dict = {}
    for gid in sorted(asign):
        if asign[gid].get("exclusivo"):
            continue      # ya lo fijó _asignar_exclusivos antes de esta pasada
        por_dia.setdefault(asign[gid]["dia"], []).append(gid)

    excepciones: list = []
    coocurrencia = cfg.get("coocurrencia_grupos")
    tope_maximo = max(vehiculos_cap.values(), default=0)
    for dia in sorted(por_dia, key=_orden_dia):
        # los grupos más pesados primero (first-fit decreasing), desempate por id
        gids = sorted(por_dia[dia],
                      key=lambda g: (-_kg_grupo(asign[g], pedidos), g))

        for idx, gid in enumerate(gids):
            a = asign[gid]
            candidatas = [u for u in vehiculos_cap if not _excluida(a, u)
                         and _respeta_exclusividad(asign, a, u, dia)]

            compat = [u for u in candidatas if _compatible_historico(
                a["grupo"], u, dia, asign, coocurrencia)]
            compat = compat or candidatas

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
            #
            # El reclamo sólo cuenta si el propio grupo pendiente PODRÍA
            # usarlo -- si su unidad de mayor afinidad está en SU PROPIA
            # unidades_excluidas (dato histórico que ya no aplica, mismo
            # riesgo documentado que KANGOO), reservarla no protege nada
            # real, sólo le quita una opción de más al grupo que sí puede
            # usarla.
            af = (cfg.get("afinidad_unidad") or {}).get(a["grupo"]) or {}

            reservadas = set()
            for g2 in gids[idx + 1:]:
                a2 = asign[g2]
                af2 = (cfg.get("afinidad_unidad") or {}).get(a2["grupo"]) or {}
                af2_usable = {u: v for u, v in af2.items() if not _excluida(a2, u)}
                if af2_usable:
                    # Predice la MISMA unidad que g2 elegiria de verdad en su
                    # turno -- no solo "el valor de afinidad mas alto": la
                    # decision real siempre prueba capacidad ascendente
                    # primero y sólo desempata por afinidad DENTRO de un
                    # mismo nivel de capacidad. Sin esto, un reclamo empatado
                    # en varias unidades de capacidad distinta (p. ej. 2.0 en
                    # T 25, K 16 Y T 20) reservaba la que aparecía primero en
                    # el dato histórico (orden arbitrario de texto), aunque
                    # g2 -- al decidir de verdad -- terminara en otra (la de
                    # MENOR capacidad entre las empatadas, por nombre) --
                    # dejando la reservada vacía sin necesidad (hallazgo
                    # real: T 25 reservada y vacía todo el día, mientras el
                    # grupo "protegido" terminaba en T 20 igual de chica).
                    # Aproximación de mejor esfuerzo, no garantía: no puede
                    # saber qué habrá cargado cada unidad para cuando le
                    # toque su turno a g2 (eso depende de decisiones que
                    # todavía no pasan), así que ignora el desempate por
                    # consolidación de la decisión real -- si ninguna de las
                    # unidades con afinidad le alcanza sola por peso, usa el
                    # dato completo tal cual, sin poder predecir mejor.
                    kg2 = _kg_grupo(a2, pedidos)
                    elegibles = {u: v for u, v in af2_usable.items()
                                 if _num(vehiculos_cap.get(u)) >= kg2}
                    elegibles = elegibles or af2_usable
                    claim = min(elegibles, key=lambda u: (
                        _num(vehiculos_cap.get(u)), -af2_usable[u], u))
                    # Estrictamente mayor a proposito: un empate NO reserva
                    # (ver docstring "RESERVA DE AFINIDAD" -- si el grupo
                    # actual tiene el mismo reclamo, no hay razon para que
                    # ceda ante uno que no es mas fuerte).
                    if af2_usable[claim] > _num(af.get(claim)):
                        reservadas.add(claim)
            compat_sin_reservar = [u for u in compat if u not in reservadas]
            compat_con_reserva = compat_sin_reservar or compat

            def _ordenar(candidatos, af=af):
                return sorted(
                    candidatos,
                    key=lambda u: (_num(vehiculos_cap.get(u)),
                                   -sum(_num(pedidos.get(s))
                                        for s in _sids_de_ruta(asign, u, dia)),
                                   -_num(af.get(u)), str(u)))

            def _primer_ajuste(candidatos):
                for unidad in _ordenar(candidatos):
                    destino = _sids_de_ruta(asign, unidad, dia) + list(a["miembros"])
                    if _restriccion_violada(
                            sorted(destino), unidad, pedidos, volumenes, coords,
                            vehiculos_cap, vehiculos_vol, cfg, dia=dia) is None:
                        return unidad
                return None

            elegido = _primer_ajuste(compat_con_reserva)

            # Ver docstring "TOPE MAXIMO DE LA FLOTA": se cede la reserva
            # SOLO cuando respetarla forzo el tope maximo Y ignorarla ofrece
            # algo por debajo de ese tope -- nunca se "roba" una reservada
            # que tambien es del tope maximo.
            if elegido is not None and _num(vehiculos_cap.get(elegido)) >= tope_maximo:
                alterno = _primer_ajuste(compat)
                if alterno is not None and _num(vehiculos_cap.get(alterno)) < tope_maximo:
                    elegido = alterno

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

                candidatas_ur = [u for u in candidatas if u not in reservadas] or candidatas
                elegido = min(candidatas_ur, key=lambda u: (-_libre(u), -_num(af.get(u)), str(u)))
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


def _dia_alternativo(asign, a, pedidos, volumenes, coords,
                     vehiculos_cap, vehiculos_vol, cfg):
    """Otro día ADMISIBLE (en orden de preferencia) donde el grupo quepa.
    El grupo se mueve completo -- el día es atributo del bloque.

    Nunca prueba una unidad de `unidades_excluidas` del grupo; entre las que
    le alcanzan, prueba primero la de menor capacidad, desempatando por
    afinidad histórica (`cfg["afinidad_unidad"]`) cuando dos o más quedan
    empatadas en capacidad.

    Dos pasadas: primero sólo destinos compatibles por historial (mismo
    criterio que `_asignar_unidades`), y sólo si ninguno sirve se repite sin
    ese filtro -- mejor un destino sin precedente que un grupo sin día."""
    coocurrencia = cfg.get("coocurrencia_grupos")
    af = (cfg.get("afinidad_unidad") or {}).get(a["grupo"]) or {}
    candidatas = sorted((u for u in vehiculos_cap if not _excluida(a, u)),
                        key=lambda u: (_num(vehiculos_cap.get(u)),
                                       -_num(af.get(u)), str(u)))
    for exigir_compat in (True, False):
        for dia in a["dias_admisibles"]:
            if dia == a["dia"]:
                continue
            for unidad in candidatas:
                if not _respeta_exclusividad(asign, a, unidad, dia):
                    continue
                if exigir_compat and not _compatible_historico(
                        a["grupo"], unidad, dia, asign, coocurrencia):
                    continue
                destino = _sids_de_ruta(asign, unidad, dia) + list(a["miembros"])
                if _restriccion_violada(sorted(destino), unidad, pedidos, volumenes,
                                        coords, vehiculos_cap, vehiculos_vol,
                                        cfg, dia=dia) is None:
                    return dia, unidad
    return None


def _consolidar_solitarios(asign, pedidos, volumenes, coords, vehiculos_cap,
                           vehiculos_vol, cfg, kg_may):
    """
    Palanca 4 (último paso, después de partir): ningún viaje debe quedar con
    una sola sucursal salvo que el vehículo ya esté al límite de su
    capacidad (peso Lores + mayoristas anclados) — regla de negocio explícita
    del 2026-08-11.

    Si hay margen, la sucursal solitaria se mueve a OTRA ruta YA ACTIVA ese
    mismo día (nunca se estrena una unidad vacía sólo para esto), compatible
    por historial real (mismo criterio que las palancas 1 y 2:
    `_compatible_historico`) y con cupo. Si dos solitarias del mismo día son
    compatibles entre sí, se juntan (cada una queda disponible como destino
    de la otra en el mismo barrido).

    "Al límite de su capacidad" se mide en PESO (kg Lores + kg de
    mayoristas ya anclados a esa sucursal) contra `vehiculos_cap` — el mismo
    sentido en que el resto del motor usa "capacidad". No repite el error de
    contar sólo Lores: la carga de mayoristas es carga real de la ruta.

    Determinista: una sola pasada sobre las rutas activas en el orden fijo
    de `_rutas_activas`; fusionar nunca crea un solitario nuevo, sólo alarga
    uno existente, así que no hace falta iterar.
    """
    excepciones: list = []
    coocurrencia = cfg.get("coocurrencia_grupos")
    for unidad, dia in _rutas_activas(asign):
        sids = _sids_de_ruta(asign, unidad, dia)
        if len(sids) != 1:
            continue
        sid = sids[0]
        cap = _num(vehiculos_cap.get(unidad))
        kg_actual = _num(pedidos.get(sid)) + _num(kg_may.get(sid))
        if cap and kg_actual >= cap - 1e-6:
            continue          # ya al límite: la excepción de una sola parada es válida
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
        candidatas.sort(key=lambda u: (
            -sum(_num(pedidos.get(s)) + _num(kg_may.get(s))
                for s in _sids_de_ruta(asign, u, dia)), u))
        elegido = None
        for u in candidatas:
            destino = _sids_de_ruta(asign, u, dia) + [sid]
            if _restriccion_violada(sorted(destino), u, pedidos, volumenes, coords,
                                    vehiculos_cap, vehiculos_vol, cfg, dia=dia,
                                    kg_mayoristas=kg_may) is None:
                elegido = u
                break
        if elegido:
            excepciones.append({
                "tipo": "CONSOLIDADO_SOLITARIA", "grupo": a["grupo"],
                "rigidez": a["rigidez"], "restriccion": None,
                "desde_unidad": unidad, "a_unidad": elegido, "dia": dia,
                "motivo": f"{unidad}/{dia} quedaba con una sola sucursal sin "
                          f"llegar al límite de capacidad; se consolidó en {elegido}",
            })
            a["unidad"] = elegido
        else:
            excepciones.append({
                "tipo": "AVISO_RUTA_SOLITARIA", "grupo": a["grupo"],
                "rigidez": a["rigidez"], "restriccion": None,
                "unidad": unidad, "dia": dia,
                "motivo": f"{unidad}/{dia} quedó con una sola sucursal sin llegar "
                          f"al límite de capacidad, pero ninguna ruta activa ese "
                          f"día tuvo precedente histórico y cupo para recibirla",
            })
    return excepciones


def _rellenar_capacidad_libre(asign, pedidos, volumenes, coords, vehiculos_cap,
                              vehiculos_vol, cfg, kg_may):
    """
    Palanca 5 (último paso, después de consolidar solitarias): ninguna ruta
    debe quedar con capacidad libre mientras exista, en otra unidad/día
    admisible, un grupo YA DESVIADO de su propio unidad_ref/dia_preferido
    que quepa completo -- regla de negocio del 2026-08-25, encontrada al
    revisar un caso real: el grupo 19 (Amatitlán/Carlos A. Carrillo 2,
    FLEXIBLE, unidad_ref F 350_1) cedía su lugar por sobrecupo en algún
    punto del reparto (los FLEXIBLE ceden primero, ver
    `_candidatos_a_mover`) y nada lo traía de vuelta aunque después quedara
    espacio libre en F 350_1/MARTES, su hogar histórico.

    Sólo son candidatos los grupos que YA ESTÁN DESVIADOS de su propio
    (unidad_ref, dia_preferido) -- un grupo que nunca se movió de su lugar
    preferido no se toca, para no sacarlo de su hogar sólo por rellenar el
    espacio de OTRA ruta.

    Recorre las rutas activas de MENOR a MAYOR % de ocupación (la más vacía
    elige primero) y, para cada una, rellena repetidamente con el mejor
    grupo candidato disponible hasta que ya no quepa ninguno más:
      1. Si algún candidato tiene a esta ruta como su propio
         unidad_ref/dia_preferido (puede volver a casa), se prefiere
         siempre sobre cualquier otro.
      2. Si ninguno "vuelve a casa", se elige el que deje la ocupación más
         cerca del 100 % sin excederla.

    Determinista: cada grupo se mueve como máximo UNA vez en toda la
    pasada -- una sola pasada sobre las rutas activas, sin punto fijo,
    mismo criterio que la Palanca 2 y `_consolidar_solitarios` para no
    reproducir el tipo de oscilación ya documentado con mayoristas.
    """
    excepciones: list = []
    coocurrencia = cfg.get("coocurrencia_grupos")
    movidos: set = set()

    def _ocupado(unidad, dia):
        sids = _sids_de_ruta(asign, unidad, dia)
        return sum(_num(pedidos.get(s)) + _num(kg_may.get(s)) for s in sids)

    def _ocupacion_pct(unidad, dia):
        cap = _num(vehiculos_cap.get(unidad))
        return (_ocupado(unidad, dia) / cap) if cap else 1.0

    def _kg_candidato(a):
        return sum(_num(pedidos.get(s)) + _num(kg_may.get(s)) for s in a["miembros"])

    orden_rutas = sorted((k for k in _rutas_activas(asign) if k[0] != "SIN_UNIDAD"),
                        key=lambda k: (_ocupacion_pct(*k), k))

    for (unidad, dia) in orden_rutas:
        cap = _num(vehiculos_cap.get(unidad))
        for _ in range(len(asign) + 1):
            candidatos = []
            for gid in sorted(asign):
                a = asign[gid]
                if a["grupo"] in movidos:
                    continue
                if (a["unidad"], a["dia"]) == (unidad, dia):
                    continue
                if a.get("unidad_forzada"):
                    continue
                if _excluida(a, unidad):
                    continue
                if (a["unidad"], a["dia"]) == (a["unidad_ref"], a["dia_preferido"]):
                    continue
                if dia not in a["dias_admisibles"]:
                    continue
                if not _compatible_historico(a["grupo"], unidad, dia, asign, coocurrencia):
                    continue
                destino = sorted(_sids_de_ruta(asign, unidad, dia) + list(a["miembros"]))
                if _restriccion_violada(destino, unidad, pedidos, volumenes, coords,
                                        vehiculos_cap, vehiculos_vol, cfg, dia=dia,
                                        kg_mayoristas=kg_may) is not None:
                    continue
                candidatos.append(a)

            if not candidatos:
                break

            en_casa = [a for a in candidatos
                      if a["unidad_ref"] == unidad and a["dia_preferido"] == dia]
            pool = en_casa or candidatos

            ocupado_actual = _ocupado(unidad, dia)

            def _pct_resultante(a, _ocupado=ocupado_actual, _cap=cap):
                return ((_ocupado + _kg_candidato(a)) / _cap) if _cap else 0.0

            pool_ordenado = sorted(pool, key=lambda a: (-_pct_resultante(a), a["grupo"]))
            elegido = pool_ordenado[0]
            es_regreso = bool(en_casa)

            excepciones.append({
                "tipo": "RELLENO_CAPACIDAD_LIBRE", "grupo": elegido["grupo"],
                "rigidez": elegido["rigidez"], "restriccion": None,
                "desde_unidad": elegido["unidad"], "desde_dia": elegido["dia"],
                "a_unidad": unidad, "a_dia": dia,
                "motivo_regreso_hogar": es_regreso,
                "motivo": f"{unidad}/{dia} con capacidad libre; se acomodó el "
                          f"grupo {elegido['grupo']} desde "
                          f"{elegido['unidad']}/{elegido['dia']}"
                          + (" (regresa a su unidad/día preferido)" if es_regreso else ""),
            })
            elegido["unidad"] = unidad
            elegido["dia"] = dia
            movidos.add(elegido["grupo"])

    return excepciones


def construir_groups_desde_plantilla(pedidos: dict, volumenes: dict, coords: dict,
                                     plantilla: list, vehiculos_cap: dict,
                                     vehiculos_vol: dict, cfg: dict = None,
                                     kg_mayoristas: dict = None):
    """
    Construye las rutas de la semana AJUSTANDO la plantilla canónica.

    plantilla : [{grupo, rigidez, dia|dia_preferido, unidad_ref, sucursales,
                  dias_admisibles}]
    Retorna (groups, excepciones):
      groups      : {(vehiculo, dia): [{"sid","seq"}]}  — mismo formato que
                    consume el resto del motor (reporte, secuencia, PDF).
      excepciones : [{tipo, grupo, restriccion, ...}] — MOVIDO_DIA,
                    PARTIDO_CAPACIDAD, AVISO_RUTA_LARGA, SIN_UNIDAD_DISPONIBLE,
                    CONSOLIDADO_SOLITARIA, AVISO_RUTA_SOLITARIA,
                    RELLENO_CAPACIDAD_LIBRE, AVISO_TIEMPO_RIGIDO_NO_PARTIDO
                    (RIGIDO que sólo viola TIEMPO: no se parte, queda como
                    aviso). `unidades_excluidas` es la restricción dura que
                    ninguna de estas palancas puede violar.
    """
    cfg = dict(cfg_por_defecto(), **(cfg or {}))
    if kg_mayoristas is not None:
        cfg["kg_mayoristas"] = kg_mayoristas
    kg_may = cfg.get("kg_mayoristas") or {}
    vehiculos_vol = vehiculos_vol or {}
    excepciones: list = []

    # ── 1. Rutas base: el grupo son las sucursales CON PEDIDO esta semana ──
    asign: dict = {}
    for g in sorted(plantilla, key=lambda x: int(x["grupo"])):
        activos = sorted(s for s in g.get("sucursales", [])
                         if _num(pedidos.get(s)) > 0)
        if not activos:
            continue                      # grupo sin demanda: no genera ruta
        dia = str(g.get("dia_preferido") or g.get("dia") or "LUNES").upper()
        adm = [str(d).upper() for d in (g.get("dias_admisibles") or [dia])]
        if dia not in adm:
            adm = [dia] + adm
        unidad_ref = g.get("unidad_ref")
        unidad = None          # se decide en _asignar_unidades; este es sólo un placeholder
        asign[int(g["grupo"])] = dict(
            grupo=int(g["grupo"]), rigidez=str(g.get("rigidez", "")).upper(),
            unidad=unidad, unidad_ref=unidad_ref, dia=dia, dia_preferido=dia,
            dias_admisibles=adm, miembros=activos,
            unidad_forzada=bool(g.get("unidad_forzada")),
            exclusivo=bool(g.get("exclusivo")),
            unidades_excluidas=list(g.get("unidades_excluidas") or []))

    # ── 1.5. Palanca 0: fijar día/unidad de los grupos exclusivos ──
    excepciones += _asignar_exclusivos(asign, pedidos, volumenes, coords,
                                       vehiculos_cap, vehiculos_vol, cfg)

    # ── 2. Palanca 1: repartir en la flota por peso ──
    desviaciones = _asignar_unidades(asign, pedidos, volumenes, coords,
                                     vehiculos_cap, vehiculos_vol, cfg)

    # ── 3. Palanca 2: mover de DÍA dentro de los admisibles. Cada grupo se
    #      mueve a lo sumo UNA vez y los barridos tienen tope: eso acota la
    #      cascada (mover un grupo satura el día destino y dispara otro). ──
    movidos: set = set()
    for iteracion in range(1, int(cfg["max_iteraciones"]) + 1):
        progreso = False
        for unidad, dia in _rutas_activas(asign):
            restr = _evaluar(asign, unidad, dia, pedidos, volumenes, coords,
                             vehiculos_cap, vehiculos_vol, cfg)
            if restr is None:
                continue
            for a in _candidatos_a_mover(asign, unidad, dia, pedidos):
                if a["grupo"] in movidos:
                    continue
                alt = _dia_alternativo(asign, a, pedidos, volumenes, coords,
                                       vehiculos_cap, vehiculos_vol, cfg)
                if alt:
                    dia_destino, unidad_destino = alt
                    excepciones.append({
                        "tipo": "MOVIDO_DIA", "grupo": a["grupo"],
                        "rigidez": a["rigidez"], "restriccion": restr,
                        "origen_carga": _origen_de_carga(
                            _sids_de_ruta(asign, unidad, dia), unidad, pedidos,
                            volumenes, coords, vehiculos_cap, vehiculos_vol,
                            cfg, dia, kg_may),
                        "desde_dia": a["dia"], "a_dia": dia_destino,
                        "desde_unidad": a["unidad"], "a_unidad": unidad_destino,
                        "iteracion": iteracion,
                        "motivo": f"{unidad}/{dia} saturada por {restr}; "
                                  f"día dentro del conjunto admisible",
                    })
                    a["dia"] = dia_destino
                    a["unidad"] = unidad_destino
                    movidos.add(a["grupo"]); progreso = True
                    break
        if progreso:
            # el reparto de unidades se recalcula tras cambiar un día
            desviaciones = _asignar_unidades(asign, pedidos, volumenes, coords,
                                             vehiculos_cap, vehiculos_vol, cfg)
        else:
            break
    excepciones = desviaciones + excepciones

    # ── 3. Último recurso: partir. Determinista y siempre registrado. ──
    for unidad, dia in _rutas_activas(asign):
        for _ in range(len(asign)):       # cota dura: nunca bucle infinito
            restr = _evaluar(asign, unidad, dia, pedidos, volumenes, coords,
                             vehiculos_cap, vehiculos_vol, cfg)
            if restr is None:
                break
            candidatos = _candidatos_a_mover(asign, unidad, dia, pedidos)
            candidatos = [a for a in candidatos if len(a["miembros"]) > 1]
            if not candidatos:
                break
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
            # pelar primero lo que más reduce el sobrecupo; desempate por
            # num_tienda ascendente (nunca "la que caiga primero")
            orden = sorted(a["miembros"], key=lambda s: (-_num(metrica.get(s)), s))
            separadas: list = []
            # Se registra TODA restricción que ató durante el pelado: si el
            # pelado siguió por TIEMPO (modelo conocido como sobreestimado en
            # rutas de muchas paradas chicas) queda asentado, para poder
            # distinguir después alivio real de alivio fantasma.
            restricciones: list = [restr]
            for sid in orden:
                if len(a["miembros"]) - len(separadas) <= 1:
                    break
                separadas.append(sid)
                restantes = [s for s in a["miembros"] if s not in separadas]
                otros = [s for s in _sids_de_ruta(asign, unidad, dia)
                         if s not in a["miembros"]]
                aun = _restriccion_violada(sorted(otros + restantes), unidad,
                                           pedidos, volumenes, coords,
                                           vehiculos_cap, vehiculos_vol, cfg,
                                           dia=dia)
                if aun is None:
                    break
                if aun not in restricciones:
                    restricciones.append(aun)
            if not separadas:
                break
            a["miembros"] = [s for s in a["miembros"] if s not in separadas]
            # reubicar la parte separada: otra unidad del mismo día, si no otro
            # día admisible; si no hay destino, se queda y queda documentado.
            sub = dict(a, grupo=a["grupo"], miembros=sorted(separadas))
            destino_u = _unidad_alternativa(asign, sub, pedidos, volumenes,
                                            coords, vehiculos_cap,
                                            vehiculos_vol, cfg)
            destino = None
            if destino_u:
                destino = (destino_u, dia)
            else:
                alt = _dia_alternativo(asign, sub, pedidos, volumenes, coords,
                                       vehiculos_cap, vehiculos_vol, cfg)
                if alt:
                    destino = (alt[1], alt[0])
            clave = max(asign) + 1
            asign[clave] = dict(
                grupo=a["grupo"], rigidez=a["rigidez"],
                unidad=(destino[0] if destino else unidad),
                unidad_ref=a["unidad_ref"], unidad_forzada=a.get("unidad_forzada", False),
                exclusivo=a.get("exclusivo", False),
                dia=(destino[1] if destino else dia), dia_preferido=a["dia_preferido"],
                dias_admisibles=a["dias_admisibles"], miembros=sorted(separadas),
                unidades_excluidas=list(a.get("unidades_excluidas") or []))
            excepciones.append({
                "tipo": "PARTIDO_CAPACIDAD", "grupo": a["grupo"],
                "rigidez": a["rigidez"], "restriccion": restr,
                "origen_carga": _origen_de_carga(
                    _sids_de_ruta(asign, unidad, dia), unidad, pedidos,
                    volumenes, coords, vehiculos_cap, vehiculos_vol, cfg,
                    dia, kg_may),
                "restricciones_durante_particion": list(restricciones),
                "sucursales_separadas": sorted(separadas),
                "sucursales_restantes": sorted(a["miembros"]),
                "destino_unidad": (destino[0] if destino else None),
                "destino_dia": (destino[1] if destino else None),
                "motivo": f"{unidad}/{dia} excede {restr} y no hubo unidad ni "
                          f"día admisible con cupo para el grupo completo",
            })
            if destino is None:
                break                      # sin destino: no seguir partiendo

    # ── 3b. Palanca 4: ninguna ruta se queda con una sola sucursal, salvo
    #      que ya esté al límite de su capacidad (peso Lores + mayoristas). ──
    excepciones += _consolidar_solitarios(asign, pedidos, volumenes, coords,
                                          vehiculos_cap, vehiculos_vol, cfg, kg_may)

    # ── 3c. Palanca 5: rellenar capacidad libre con grupos ya desviados de
    #      su unidad/dia preferido, priorizando devolverlos a casa. ──
    if cfg.get("relleno_capacidad", False):
        excepciones += _rellenar_capacidad_libre(asign, pedidos, volumenes,
                                                  coords, vehiculos_cap,
                                                  vehiculos_vol, cfg, kg_may)

    # ── 4. Salida en el formato que consume el resto del motor ──
    groups: dict = {}
    for gid in sorted(asign):
        a = asign[gid]
        for sid in a["miembros"]:
            groups.setdefault((a["unidad"], a["dia"]), []).append(
                {"sid": sid, "seq": 999, "grupo": a["grupo"]})
    for clave in groups:
        groups[clave].sort(key=lambda m: m["sid"])

    # ── 5. Aviso de rutas largas (informativo, nunca bloquea) ──
    tope = cfg.get("aviso_paradas")
    if tope:
        for (unidad, dia) in sorted(groups, key=lambda k: (_orden_dia(k[1]), str(k[0]))):
            n = len(groups[(unidad, dia)])
            if n > int(tope):
                excepciones.append({
                    "tipo": "AVISO_RUTA_LARGA", "grupo": None, "restriccion": None,
                    "unidad": unidad, "dia": dia, "paradas": n,
                    "motivo": f"ruta con {n} paradas (> {tope}); sólo aviso",
                })
    return groups, excepciones
