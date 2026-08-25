"""
logic/convrp_logic.py

ConVRP — el VRP como AJUSTADOR sobre la plantilla canónica (Fase 2).

En vez de generar rutas desde cero, parte de la plantilla histórica y sólo
reoptimiza donde la demanda de la semana no cabe.

Modelo (decisiones fijas del negocio):
  - El GRUPO son las sucursales del grupo canónico CON PEDIDO esa semana, no el
    roster completo: un rígido de 6 con demanda en 4 viaja de 4.
  - La UNIDAD no es identidad del grupo, pero tampoco es libre: `unidad_ref` es
    la preferencia y desviarse tiene penalización (se registra como excepción).
  - El DÍA es atributo del GRUPO (se mueve en bloque completo, nunca parcial) y
    sólo dentro de sus `dias_admisibles`; fuera de ese conjunto no se mueve.
  - Rigidez de COMPOSICIÓN y flexibilidad de DÍA son dimensiones independientes:
    un rígido puede cambiar de día si su conjunto admisible lo permite.

Orden de palancas ante sobrecupo (de evidencia más débil a más fuerte):
    1) mover de UNIDAD dentro del mismo día
    2) mover de DÍA dentro de los admisibles
    3) PARTIR el grupo — último recurso, determinista y siempre registrado

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

# Interruptor dedicado de la Palanca 5 (relleno de capacidad libre). Permite
# apagarla sin tocar CONVRP_ACTIVO si algo sale mal en producción -- mismo
# patrón que REBALANCEO_GEOGRAFICO en historico_logic.py.
CONVRP_RELLENO_CAPACIDAD = True


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


def _candidatos_a_mover(asign, unidad, dia, pedidos):
    """Grupos de la ruta, ordenados: FLEXIBLE antes que RIGIDO, luego el de mayor
    peso (alivia más), desempate por id de grupo ascendente."""
    en_ruta = [asign[g] for g in sorted(asign)
               if asign[g]["unidad"] == unidad and asign[g]["dia"] == dia]
    return sorted(en_ruta, key=lambda a: (0 if a["rigidez"] != "RIGIDO" else 1,
                                          -_kg_grupo(a, pedidos), a["grupo"]))


def _unidad_alternativa(asign, a, pedidos, volumenes, coords,
                        vehiculos_cap, vehiculos_vol, cfg):
    """Otra unidad, MISMO día, donde el grupo quepa sin saturarla."""
    for unidad in sorted(vehiculos_cap):
        if unidad == a["unidad"]:
            continue
        destino = _sids_de_ruta(asign, unidad, a["dia"]) + list(a["miembros"])
        if _restriccion_violada(sorted(destino), unidad, pedidos, volumenes,
                                coords, vehiculos_cap, vehiculos_vol, cfg,
                                dia=a["dia"]) is None:
            return unidad
    return None


def _asignar_unidades(asign, pedidos, volumenes, coords,
                      vehiculos_cap, vehiculos_vol, cfg):
    """
    Reparte los grupos de cada día entre las unidades: una sola pasada por
    peso descendente (first-fit decreasing) — cada grupo intenta primero su
    propia `unidad_ref` y, si no cabe, cede a otra de la flota.

    Al ceder, un grupo NUNCA puede tomar la `unidad_ref` de OTRO grupo que
    todavía no tuvo su turno esta pasada (protegida como "reservada" hasta
    que a ese otro grupo le toque decidir) — salvo que sea la ÚNICA opción
    viable, igual que ya ocurre con la compatibilidad por coocurrencia.

    Por qué esta protección — hallado en producción 2026-08-12: un grupo
    cediendo podía "ocupar" de buena fe una unidad vacía que en realidad
    era la `unidad_ref` de OTRO grupo que esa semana pesaba un poco menos y
    todavía no le tocaba su turno (grupo 19, Amatitlán/Carlos A. Carrillo 2,
    se coló en T 20 antes que grupo 11, El Tejar/Antón Lizardo/Jamapa,
    RIGIDO, dueño legítimo de T 20). Como un grupo usando SU PROPIA
    unidad_ref nunca pasa por el filtro de coocurrencia, los dos quedaban
    juntos sin ningún precedente histórico entre ellos, y ninguna de las
    reglas existentes lo detectaba. Reservando el turno de cada grupo
    todavía pendiente, un grupo que cede no pisa por accidente la unidad de
    otro que aún no decidió lo suyo — salvo en el último recurso (ningún
    destino admite al grupo en absoluto), donde la reserva también se
    ignora: ahí ya no hay una unidad "mejor" que proteger, sólo la de más
    espacio libre para que la partición pele lo mínimo.

    (Se probó primero separar esto en dos fases -- TODOS reclaman su propia
    referencia antes de que NADIE ceda -- pero eso rompía la garantía de
    "el más pesado tiene primera opción sobre el espacio libre" que ya
    tenía su propio test desde el incidente del 6-10 abril: un grupo más
    pesado que cualquier unidad podía terminar en una unidad más chica de
    lo necesario porque, para cuando llegaba su turno de ceder, grupos más
    livianos ya habían reclamado con toda legitimidad las unidades grandes
    en la fase previa. La protección por reserva, en cambio, corre dentro
    de la MISMA pasada de siempre y sólo bloquea el destino puntual que le
    pertenece a otro grupo, sin tocar el orden ni el resto del reparto.)

    El resto del contrato es el mismo que antes: `unidad_ref` es
    PREFERENCIA con penalización; se cede por carga descendente (consolidar
    antes que dispersar) con desempate por afinidad histórica: si varios
    grupos comparten `unidad_ref` el mismo día, se distribuyen en la flota
    libre en vez de saturar una unidad y terminar partiendo grupos.

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
        ref_de = {g: (asign[g]["unidad_ref"] if asign[g]["unidad_ref"] in vehiculos_cap
                      else None) for g in gids}

        for idx, gid in enumerate(gids):
            a = asign[gid]
            ref = ref_de[gid]
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
            if ref:
                destino = _sids_de_ruta(asign, ref, dia) + list(a["miembros"])
                restr_ref = _restriccion_violada(
                    sorted(destino), ref, pedidos, volumenes, coords,
                    vehiculos_cap, vehiculos_vol, cfg, dia=dia)
                if restr_ref is None:
                    a["unidad"] = ref
                    continue
            else:
                restr_ref = None
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
            # Al ceder la preferida, ninguna unidad que sea la `unidad_ref` de
            # OTRO grupo todavía pendiente (más liviano, sin su turno aún) es
            # un destino válido -- salvo que sea la única opción. Así un grupo
            # que cede nunca se cuela en el lugar de otro que aún no decidió.
            reservadas = {ref_de[g2] for g2 in gids[idx + 1:] if ref_de[g2]}
            otras_sin_reservar = [u for u in otras if u not in reservadas]
            otras = otras_sin_reservar or otras
            # Al ceder la preferida no cualquier consolidación sirve: si la
            # unidad candidata ya lleva ese día un grupo con el que nunca
            # compartió camión en el histórico, se descarta primero — salvo
            # que sea la única opción (ver `_compatible_historico`).
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
                # día (sin restricción de reserva -- es el último recurso), para
                # que la partición posterior pele lo mínimo.
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
                # No hace falta reintentar `ref` aquí: el chequeo de la línea
                # ~319 ya probó esta misma condición (ocupación combinada)
                # contra los mismos datos, y nada en `asign` cambió desde
                # entonces — si no cupo ahí, sigue sin caber.
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


def _dia_alternativo(asign, a, pedidos, volumenes, coords,
                     vehiculos_cap, vehiculos_vol, cfg):
    """Otro día ADMISIBLE (en orden de preferencia) donde el grupo quepa.
    El grupo se mueve completo — el día es atributo del bloque.

    Dos pasadas: primero sólo destinos compatibles por historial (mismo
    criterio que `_asignar_unidades`), y sólo si ninguno sirve se repite sin
    ese filtro — mejor un destino sin precedente que un grupo sin día."""
    coocurrencia = cfg.get("coocurrencia_grupos")
    for exigir_compat in (True, False):
        for dia in a["dias_admisibles"]:
            if dia == a["dia"]:
                continue
            for unidad in [a["unidad_ref"]] + sorted(vehiculos_cap):
                if unidad not in vehiculos_cap:
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
        # candidatas: unidades YA ACTIVAS ese día (nunca abrir una vacía sólo
        # para esto), compatibles por historial, ordenadas por carga
        # descendente (consolidar en la más llena que todavía quepa).
        activas_ese_dia = sorted({u for (u, d) in _rutas_activas(asign)
                                  if d == dia and u != unidad})
        candidatas = [u for u in activas_ese_dia
                     if _compatible_historico(a["grupo"], u, dia, asign, coocurrencia)]
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

    orden_rutas = sorted(_rutas_activas(asign), key=lambda k: (_ocupacion_pct(*k), k))

    for (unidad, dia) in orden_rutas:
        candidatos = []
        for gid in sorted(asign):
            a = asign[gid]
            if a["grupo"] in movidos:
                continue
            if (a["unidad"], a["dia"]) == (unidad, dia):
                continue
            if a.get("unidad_forzada"):
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
            continue

        elegido = candidatos[0]
        excepciones.append({
            "tipo": "RELLENO_CAPACIDAD_LIBRE", "grupo": elegido["grupo"],
            "rigidez": elegido["rigidez"], "restriccion": None,
            "desde_unidad": elegido["unidad"], "desde_dia": elegido["dia"],
            "a_unidad": unidad, "a_dia": dia,
            "motivo_regreso_hogar": False,
            "motivo": f"{unidad}/{dia} con capacidad libre; se acomodó el "
                      f"grupo {elegido['grupo']} desde "
                      f"{elegido['unidad']}/{elegido['dia']}",
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
      excepciones : [{tipo, grupo, restriccion, ...}] — MOVIDO_UNIDAD,
                    MOVIDO_DIA, PARTIDO_CAPACIDAD, AVISO_RUTA_LARGA.
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
        unidad = unidad_ref if unidad_ref in vehiculos_cap else (
            sorted(vehiculos_cap)[0] if vehiculos_cap else "VEHICULO")
        asign[int(g["grupo"])] = dict(
            grupo=int(g["grupo"]), rigidez=str(g.get("rigidez", "")).upper(),
            unidad=unidad, unidad_ref=unidad_ref, dia=dia, dia_preferido=dia,
            dias_admisibles=adm, miembros=activos,
            unidad_forzada=bool(g.get("unidad_forzada")))

    # ── 2. Palanca 1: repartir en la flota (unidad_ref = preferencia) ──
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
                unidad_ref=a["unidad_ref"],
                dia=(destino[1] if destino else dia), dia_preferido=a["dia_preferido"],
                dias_admisibles=a["dias_admisibles"], miembros=sorted(separadas))
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
