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
import copy
import math

from logic.logistica_tiempo import evaluar_llegadas, evaluar_ruta_por_tiempo, hhmm_a_min
from logic.mayoristas_logic import _insertar_pos_proxima

UMBRAL_PCT_DESTINO = 85.0
# Salvaguarda anti-bucle: tope de movimientos por ruta origen en una sola
# resolución (una ruta real rara vez tiene más de un puñado de paradas
# FUERA DE HORARIO).
MAX_MOVIMIENTOS_POR_RUTA = 20

# Interruptor dedicado de Fase B (reubicación + persistencia). Independiente
# de TIEMPO_ENTREGA_ESTRICTO (Fase A, en logistica_tiempo.py — solo marca,
# no mueve ni persiste): apagar este interruptor deja el marcado de Fase A
# intacto pero desactiva la reubicación de Fase B, sin tocar Fase A. Mismo
# patrón que REBALANCEO_GEOGRAFICO/MAYORISTAS_GEOGRAFICO/CONVRP_ACTIVO en
# este proyecto — Fase B es la primera de estas fases que escribe en BD,
# por eso necesita su propio apagador.
TIEMPO_REUBICACION_ACTIVA = True


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


def _pct_utilizacion(peso_kg: float, capacidad_ton) -> float:
    cap_kg = float(capacidad_ton or 0) * 1000
    return round(float(peso_kg) / cap_kg * 100, 1) if cap_kg > 0 else 0.0


def _cabe_por_peso(ruta: dict, peso_extra: float, umbral_pct: float) -> bool:
    """True si, tras sumar `peso_extra` al peso ya cargado de `ruta`, la
    utilización resultante no supera `umbral_pct`."""
    peso_total = float(ruta.get("peso_kg", 0)) + float(peso_extra)
    return _pct_utilizacion(peso_total, ruta.get("capacidad_ton")) <= umbral_pct


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


def _grupo_para(parada: dict, tipo: str, ruta: dict, indice_grupos: dict) -> "dict | None":
    """
    Grupo de co-viaje (de `plantilla_canonica.obtener_grupos()`, indexado
    por `_indice_num_tienda_a_grupo`) al que pertenece `parada`:
    - Sucursal: su propio grupo, por `num_tienda`.
    - Mayorista: el grupo de la sucursal de la MISMA ruta geográficamente
      más cercana que sí tenga grupo — mismo criterio de anclaje que usaba
      `_clave_afinidad_para` en v1, ahora resolviendo un grupo completo en
      vez de una llave suelta.
    None si no hay coordenadas, no hay ancla, o la sucursal no está en
    ningún grupo de la plantilla canónica.
    """
    if tipo == "sucursal":
        nt = parada.get("num_tienda")
        return indice_grupos.get(int(nt)) if nt is not None else None

    lat, lon = parada.get("latitud"), parada.get("longitud")
    if lat is None or lon is None:
        return None
    mejor_grupo, mejor_dist = None, float("inf")
    for s in ruta.get("sucursales", []):
        nt = s.get("num_tienda")
        if nt is None or int(nt) not in indice_grupos:
            continue
        la, lo = s.get("latitud"), s.get("longitud")
        if la is None or lo is None:
            continue
        d = _haversine_km(float(lat), float(lon), float(la), float(lo))
        if d < mejor_dist:
            mejor_dist, mejor_grupo = d, indice_grupos[int(nt)]
    return mejor_grupo


def _conjunto_a_mover(grupo: dict, ruta: dict, parada: dict, tipo: str) -> list:
    """
    Paradas que se mueven juntas, atómicamente, al reubicar `parada`:
    - Mayorista: siempre solo ella misma — un mayorista nunca es miembro de
      un grupo (`plantilla_grupo_sucursal` es solo de sucursales), así que
      nunca arrastra al grupo de su sucursal ancla.
    - Sucursal en grupo FLEXIBLE (o grupo de un solo miembro): solo ella —
      la cohesión <1.0 de un grupo flexible ya dice que históricamente no
      siempre viajaron juntos, no se fuerza a un compañero que hoy sí llega
      a tiempo a moverse también.
    - Sucursal en grupo RÍGIDO con más miembros: todos los miembros del
      grupo que estén presentes en la ruta origen ahora mismo (nunca se
      separa una pareja/trío rígido). Si por algún motivo el resto del
      grupo no está en esta ruta esta semana, se mueve solo lo que sí está
      — no se va a buscar al resto a otras rutas.
    """
    if tipo != "sucursal":
        return [parada]
    if grupo.get("rigidez") != "RIGIDO" or len(grupo.get("sucursales", [])) <= 1:
        return [parada]
    miembros_nt = {int(nt) for nt in grupo.get("sucursales", [])}
    conjunto = [s for s in ruta.get("sucursales", [])
                if s.get("num_tienda") is not None and int(s["num_tienda"]) in miembros_nt]
    return conjunto if conjunto else [parada]


def _rutas_candidatas_por_grupo(grupo: dict, rutas: list, ruta_origen_id, vehiculo_origen) -> list:
    """
    Rutas reales de `rutas` (existentes esta semana) que son destino válido
    para `grupo`, en orden de preferencia: vehículo dominante primero
    (`unidades_afines`, conteo descendente), y dentro de cada vehículo, día
    admisible en su orden (preferido/canónico primero). Excluye la ruta de
    origen y **todo** el vehículo de origen (aunque ese vehículo corra otro
    día) — nunca se le vuelve a asignar al mismo vehículo que ya la tenía
    fuera de horario. Sin duplicados (un vehículo solo tiene una ruta por
    día esta semana).
    """
    veh_origen_norm = _normalizar_veh(vehiculo_origen)
    pares_veh = _parsear_unidades_afines(grupo.get("unidades_afines"))
    dias = grupo.get("dias_admisibles") or (
        [grupo["dia_preferido"]] if grupo.get("dia_preferido") else [])

    rutas_por_clave = {}
    for r in rutas:
        if r.get("id") == ruta_origen_id:
            continue
        clave = (_normalizar_veh(r.get("vehiculo_abrev")), str(r.get("dia", "")).upper())
        rutas_por_clave.setdefault(clave, r)

    candidatas = []
    for veh, _conteo in pares_veh:
        if veh == veh_origen_norm:
            continue
        for dia in dias:
            r = rutas_por_clave.get((veh, str(dia).upper()))
            if r is not None and r not in candidatas:
                candidatas.append(r)
    return candidatas


def _simular_insercion_conjunto(ruta: dict, conjunto: list, tipo: str) -> dict:
    """Copia profunda de `ruta` con TODAS las paradas de `conjunto` insertadas
    y el peso recalculado — para evaluar el efecto de mover un grupo
    completo (o una sola parada, si `conjunto` tiene un elemento) sin mutar
    la ruta real todavía."""
    ruta_sim = copy.deepcopy(ruta)
    for parada in conjunto:
        _insertar_en_ruta(ruta_sim, parada, tipo)
    _recalcular_peso_ruta(ruta_sim)
    return ruta_sim


def _sin_fuera_de_horario(ruta: dict, cfg_tiempo: dict, consultar_osrm_fn) -> bool:
    combinado = _paradas_ordenadas(ruta)
    if not combinado:
        return True
    evals = evaluar_ruta_completa(combinado, ruta.get("dia", ""), cfg_tiempo, consultar_osrm_fn)
    return all(e["entregable_por_tiempo"] for e in evals)


def _mejor_candidata_grupo(candidatas: list, conjunto: list, tipo: str, peso_extra: float,
                           cfg_tiempo: dict, consultar_osrm_fn, umbral_pct: float) -> "dict | None":
    """Primera candidata (ya ordenada por `_rutas_candidatas_por_grupo`) que,
    tras insertar TODO `conjunto`, queda ≤ umbral_pct de utilización Y no
    genera un nuevo FUERA DE HORARIO."""
    for ruta in candidatas:
        if not _cabe_por_peso(ruta, peso_extra, umbral_pct):
            continue
        ruta_sim = _simular_insercion_conjunto(ruta, conjunto, tipo)
        if _sin_fuera_de_horario(ruta_sim, cfg_tiempo, consultar_osrm_fn):
            return ruta
    return None


def _menos_mala_grupo(candidatas: list, conjunto: list, tipo: str,
                      cfg_tiempo: dict, consultar_osrm_fn) -> "dict | None":
    """Último recurso: entre las candidatas ya restringidas a
    `unidades_afines` del grupo (nunca una ruta sin relación histórica real
    con el grupo), la que quede con menor % de utilización tras insertar
    `conjunto` completo."""
    mejor, mejor_pct = None, float("inf")
    for ruta in candidatas:
        ruta_sim = _simular_insercion_conjunto(ruta, conjunto, tipo)
        if ruta_sim["pct_utilizacion"] < mejor_pct:
            mejor, mejor_pct = ruta, ruta_sim["pct_utilizacion"]
    return mejor


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
    if not (TIEMPO_REUBICACION_ACTIVA and cfg_tiempo and cfg_tiempo.get("activo")):
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
        else:
            print(f"[tiempo_reubicacion] ruta {ruta.get('id')} alcanzó el tope de "
                  f"{MAX_MOVIMIENTOS_POR_RUTA} movimientos")

    return cambio


def _parsear_unidades_afines(s) -> list:
    """
    'T 23:3 | K 16:2 | T 20:2' -> [('T23', 3), ('K16', 2), ('T20', 2)],
    ordenado por conteo descendente (el vehículo dominante primero; sort
    estable, así que empates conservan el orden de aparición en el string).
    Vehículos normalizados (mayúsculas sin espacios, ver `_normalizar_veh`)
    para comparar contra `ruta.vehiculo_abrev` sin caer en el bug ya
    confirmado del proyecto ('F350_2' != 'F 350_2').
    """
    if not s or not str(s).strip():
        return []
    pares = []
    for trozo in str(s).split("|"):
        trozo = trozo.strip()
        if not trozo or ":" not in trozo:
            continue
        veh, _, cnt = trozo.rpartition(":")
        try:
            conteo = int(cnt.strip())
        except ValueError:
            continue
        pares.append((_normalizar_veh(veh), conteo))
    return sorted(pares, key=lambda x: -x[1])


def _indice_num_tienda_a_grupo(grupos: list) -> dict:
    """{num_tienda: grupo} a partir de la lista que devuelve
    `plantilla_canonica.obtener_grupos()` — un grupo por sucursal miembro."""
    indice = {}
    for g in grupos:
        for nt in g.get("sucursales", []):
            indice[int(nt)] = g
    return indice
