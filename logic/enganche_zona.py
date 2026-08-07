"""
logic/enganche_zona.py

Resolución de la ZONA de enganche de un cliente mayorista (Fase 3). Puro: sin BD.

Por qué la geografía y no sólo el diccionario de nombres: **78 de 145 clientes
aparecieron una sola semana**. Los mayoristas nuevos entran constantemente y un
diccionario por nombre nunca los cubriría — cada cliente nuevo caería al
fallback global para siempre. La coordenada los resuelve sin mantenimiento.

El diccionario histórico población→zona no desaparece: pasa a ser la CAPA DE
EXCEPCIONES. Donde el histórico dice algo distinto a lo que sugiere la
geografía, **gana el histórico** (es evidencia de a qué ruta se pegan de
verdad, que puede no ser la más cercana en línea recta).

Vías, en orden de autoridad:
  1. HISTORIA  — la población está en el diccionario y su zona existe.
  2. GEOGRAFIA — zona cuyo centroide (su ruta canónica) queda más cerca.
  3. FALLBACK  — sin coordenada válida, o la zona más cercana excede el umbral
                 de distancia (p. ej. Zongolica, en la sierra, lejos de todo):
                 se deja al comportamiento global anterior en vez de forzar un
                 enganche absurdo.

Cada resolución registra por cuál vía se resolvió, para poder auditarla.
"""
import math
import re
import unicodedata

# Distancia máxima al centroide de una zona para aceptar un enganche por
# geografía. Más allá, fallback global.
MAX_KM_ENGANCHE = 60.0


def _norm(s) -> str:
    s = str(s).strip().upper()
    s = "".join(c for c in unicodedata.normalize("NFD", s)
                if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s)


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _float(x):
    try:
        v = float(x)
        return None if math.isnan(v) else v
    except (TypeError, ValueError):
        return None


def resolver_zona_cliente(poblacion, lat, lon, zonas_por_poblacion: dict,
                          centroides_zona: dict, max_km: float = MAX_KM_ENGANCHE,
                          confianza_por_zona: dict = None) -> dict:
    """
    Devuelve {zona, via, distancia_km, confianza, zona_historica, desacuerdo,
    motivo} para un cliente mayorista.

    La historia manda… salvo cuando es débil. Si la zona histórica es de BAJA
    confianza (vista <3 semanas), preferirla sobre la inferencia geográfica sería
    al revés de lo que corresponde: ahí gana la geografía. El desacuerdo queda
    registrado en ambos sentidos, para poder medir después quién tenía razón
    cuando entren semanas nuevas.

    poblacion           : población/municipio del cliente (puede venir sucia).
    lat, lon            : coordenadas del cliente (None si no las tiene).
    zonas_por_poblacion : {poblacion: zona} del histórico.
    centroides_zona     : {zona: (lat, lon)} — centroide de sus clientes.
    max_km              : umbral de enganche por geografía.
    confianza_por_zona  : {zona: 'ALTA'|'MEDIA'|'BAJA'}; sin él, la historia
                          manda siempre (comportamiento anterior).

    `zona=None` con `via='FALLBACK'` significa: usar el comportamiento global
    anterior (inserción más barata sin restricción de zona).
    """
    centroides_norm = {}
    for z, c in (centroides_zona or {}).items():
        centroides_norm[_norm(z)] = (z, c)
    conf = {_norm(k): str(v).upper() for k, v in (confianza_por_zona or {}).items()}

    base = {"zona": None, "via": None, "distancia_km": None, "confianza": None,
            "zona_historica": None, "desacuerdo": False, "motivo": ""}

    # ── zona histórica (si la hay y sigue existiendo) ──
    clave = _norm(poblacion) if poblacion is not None else ""
    hist = {_norm(k): v for k, v in (zonas_por_poblacion or {}).items()}
    zona_hist = None
    if clave and clave in hist and _norm(hist[clave]) in centroides_norm:
        zona_hist = centroides_norm[_norm(hist[clave])][0]

    # ── zona geográfica ──
    la, lo = _float(lat), _float(lon)
    zona_geo, dist_geo = None, None
    if la is not None and lo is not None and centroides_norm:
        candidatas = sorted(
            ((_haversine_km(la, lo, c[0], c[1]), nombre)
             for nombre, c in centroides_norm.values()),
            key=lambda t: (round(t[0], 6), t[1]))     # desempate determinista
        dist_geo, zona_geo = candidatas[0]
        if dist_geo > float(max_km):
            zona_geo = None

    conf_hist = conf.get(_norm(zona_hist)) if zona_hist else None
    desacuerdo = bool(zona_hist and zona_geo and zona_hist != zona_geo)

    # ── decisión ──
    if zona_hist and not (conf_hist == "BAJA" and zona_geo and desacuerdo):
        return dict(base, zona=zona_hist, via="HISTORIA", confianza=conf_hist,
                    zona_historica=zona_hist, desacuerdo=desacuerdo,
                    distancia_km=(round(dist_geo, 1) if dist_geo is not None else None),
                    motivo=f"población '{poblacion}' con zona histórica"
                           + (f"; la geografía sugería {zona_geo}" if desacuerdo else ""))
    if zona_hist and conf_hist == "BAJA" and desacuerdo:
        return dict(base, zona=zona_geo, via="GEOGRAFIA", confianza=conf_hist,
                    zona_historica=zona_hist, desacuerdo=True,
                    distancia_km=round(dist_geo, 1),
                    motivo=(f"zona histórica {zona_hist} es de BAJA confianza; "
                            f"se prefiere la geográfica {zona_geo} "
                            f"({dist_geo:.0f} km)"))
    if la is None or lo is None:
        return dict(base, via="FALLBACK", motivo="sin coordenada válida del cliente")
    if not centroides_norm:
        return dict(base, via="FALLBACK", motivo="no hay centroides de zona disponibles")
    if zona_geo is None:
        return dict(base, via="FALLBACK",
                    distancia_km=(round(dist_geo, 1) if dist_geo is not None else None),
                    motivo=(f"la zona más cercana queda a más de {max_km:.0f} km"))
    return dict(base, zona=zona_geo, via="GEOGRAFIA",
                confianza=conf.get(_norm(zona_geo)),
                distancia_km=round(dist_geo, 1),
                motivo=f"centroide de zona más cercano ({dist_geo:.0f} km)")


def confianza_zona(semanas, pct_nucleo) -> str:
    """
    Qué tan firme es la regla de enganche de una zona. DOS EJES:

      FRECUENCIA   — en cuántas semanas se vio la zona (cantidad de evidencia).
      CONSISTENCIA — `pct_nucleo`: en qué proporción de sus paradas estuvo
                     presente su GRUPO NÚCLEO (el grupo que más veces la
                     acompaña).

    Consistencia es PRESENCIA DEL NÚCLEO, no coincidencia del conjunto exacto de
    grupos. Que COSAMALOAPAN aparezca a veces con "4" y a veces con "4|19" no es
    duda sobre a dónde va la zona: el g4 está siempre. Lo que varía es qué otros
    grupos suben al mismo camión — composición del viaje, no destino de la zona.
    Medido: 29 de 39 zonas tienen núcleo ≥0.80, contra 18 de 39 por conjunto
    exacto.

    NO se usa la unidad: el modelo la asigna cada semana por capacidad, así que
    su variación es mecánica nuestra (la unidad no es identidad del grupo).
    NO se usa el día: la zona lo hereda de su grupo, y los `dias_admisibles` ya
    modelan esa variabilidad aguas arriba — medirla aquí la contaría dos veces
    (JALAPA DE DIAZ tiene día 58 % sólo porque el g21 alterna entre los suyos).

    ALTA  : ≥5 semanas y núcleo ≥0.80.
    BAJA  : <3 semanas o núcleo <0.60.
    MEDIA : el resto.
    """
    s_ = _float(semanas) or 0
    p_ = _float(pct_nucleo) or 0
    if s_ >= 5 and p_ >= 0.80:
        return "ALTA"
    if s_ < 3 or p_ < 0.60:
        return "BAJA"
    return "MEDIA"


def centroides_desde_clientes(clientes: list, zonas_por_poblacion: dict) -> dict:
    """
    {zona: (lat, lon)} — centroide de los CLIENTES que el histórico ya asignó a
    esa zona. Es dónde está la zona de verdad, que es lo que hay que comparar
    contra un cliente nuevo.

    ES EL MÉTODO A USAR. `centroides_desde_grupos` se probó primero y falla: una
    zona se llama como un pueblo, pero el grupo Lores al que se engancha puede
    estar centrado en otro sitio, así que "el centroide de grupo más cercano"
    elige la zona vecina equivocada. Validado sobre los 132 clientes con
    histórico: **96.2 % de coincidencia con este método contra 24 % con el de
    grupos**. Los pocos desacuerdos son casos genuinamente ambiguos (p. ej.
    SAN LUCAS OJITLAN vs JALAPA DE DIAZ, centroides a 0.1 km).

    clientes : [{poblacion, latitud, longitud}]
    """
    acum: dict = {}
    hist = {_norm(k): v for k, v in (zonas_por_poblacion or {}).items()}
    for c in clientes or []:
        zona = hist.get(_norm(c.get("poblacion")))
        la, lo = _float(c.get("latitud")), _float(c.get("longitud"))
        if not zona or la is None or lo is None:
            continue
        acum.setdefault(zona, []).append((la, lo))
    return {z: (sum(p[0] for p in ps) / len(ps), sum(p[1] for p in ps) / len(ps))
            for z, ps in acum.items()}


def centroides_desde_grupos(zonas: list, grupos: list, coords: dict) -> dict:
    """
    {zona: (lat, lon)} — centroide de las sucursales del/los grupo(s) Lores a los
    que la zona se engancha históricamente. Es "dónde va esa ruta", que es lo que
    importa para decidir si un cliente le queda de paso.

    zonas  : [{zona, grupos_lores}]  (grupos_lores puede ser '19 | 20')
    grupos : [{grupo, sucursales}]
    coords : {num_tienda: (lat, lon)}
    """
    por_grupo = {int(g["grupo"]): g.get("sucursales", []) for g in (grupos or [])}
    salida = {}
    for z in zonas or []:
        gids = [int(x) for x in re.split(r"[^0-9]+", str(z.get("grupos_lores") or ""))
                if x.isdigit()]
        puntos = [coords[s] for g in gids for s in por_grupo.get(g, [])
                  if s in coords]
        if puntos:
            salida[z["zona"]] = (sum(p[0] for p in puntos) / len(puntos),
                                 sum(p[1] for p in puntos) / len(puntos))
    return salida


def elegir_ancla_mayorista(sids_ruta, sids_grupo, lat, lon, coords) -> int:
    """
    A qué SUCURSAL de la ruta se le carga el peso de un mayorista.

    El motor razona en kg por sucursal: para que la carga de mayoristas entre en
    las restricciones (y no aparezca recién al pintar el PDF, con la ruta ya al
    148 %), hay que anclarla a una parada. El ancla se elige entre las
    sucursales del GRUPO al que la zona se enganchó — así el peso **viaja con el
    grupo**: si el grupo cambia de unidad o de día, su carga de mayoristas va
    detrás, que es la regla acordada (la zona sigue al grupo).

    Si el destino no se resolvió por grupo (vía GEOGRAFIA_RUTA) se ancla a la
    sucursal más cercana de la ruta. Sin coordenadas, al `sid` menor: arbitrario
    pero determinista, nunca "la que caiga primero".
    """
    candidatos = sorted(set(sids_grupo or []) & set(sids_ruta or [])) or \
        sorted(set(sids_ruta or []))
    if not candidatos:
        return None
    la, lo = _float(lat), _float(lon)
    con_coord = [s for s in candidatos if s in (coords or {})]
    if la is None or lo is None or not con_coord:
        return candidatos[0]
    return min(con_coord,
               key=lambda s: (round(_haversine_km(la, lo, coords[s][0],
                                                  coords[s][1]), 6), s))


def reubicar_mayoristas_por_cupo(por_ruta: dict, kg_lores: dict,
                                 vehiculos_cap: dict, coords_rutas: dict,
                                 max_km: float = MAX_KM_ENGANCHE):
    """
    Garantía dura: **ninguna ruta queda por encima de su capacidad** tras el
    enganche por zona.

    El anclaje ya mete la carga de mayoristas al motor, así que en general las
    palancas del ConVRP (unidad → día → partir) resuelven el sobrecupo antes de
    llegar aquí. Esto es la red: casos donde un solo cliente pesa más que el
    hueco que queda (PLAZA COMERCIAL RIO, 3,152 kg) y el grupo ya no tiene a
    dónde moverse.

    Orden de reubicación, del destino más barato al más caro:
      1. otra RUTA del mismo día con hueco, la más cercana al cliente;
      2. VIAJE DE MAYORISTAS SOLO en una unidad libre ese día — la más chica que
         alcance (abrir un tráiler para 600 kg desperdicia flota);
      3. si nada admite la carga, se registra MAYORISTA_SIN_CUPO y el pedido se
         queda donde estaba. **Nunca desaparece en silencio**: el planeador ve
         una ruta marcada, que es peor de esconder que de mostrar.

    por_ruta      : {(unidad, dia): [mayorista…]} con `peso_kg`, `latitud`, `longitud`.
    kg_lores      : {(unidad, dia): kg de sucursales} — la carga que ya lleva.
    vehiculos_cap : {unidad: kg}.
    coords_rutas  : {(unidad, dia): (lat, lon)} centroide de cada ruta.

    Devuelve (por_ruta_nuevo, excepciones). Determinista: todo recorrido va
    sobre claves ordenadas y el candidato a mover es el más pesado (desempate
    por id de cliente).
    """
    nuevo = {k: list(v) for k, v in (por_ruta or {}).items()}
    kg_lores = dict(kg_lores or {})
    coords_rutas = dict(coords_rutas or {})
    cap = {u: _float(c) or 0.0 for u, c in (vehiculos_cap or {}).items()}
    excepciones: list = []

    def _cap(clave):
        return cap.get(clave[0], float("inf"))

    def _total(clave):
        return (_float(kg_lores.get(clave)) or 0.0) + sum(
            _float(m.get("peso_kg")) or 0.0 for m in nuevo.get(clave, []))

    def _claves_activas(dia):
        return sorted({k for k in set(nuevo) | set(kg_lores) if k[1] == dia},
                      key=lambda k: str(k[0]))

    sin_cupo: set = set()
    for clave in sorted(set(nuevo), key=lambda k: (str(k[1]), str(k[0]))):
        unidad, dia = clave
        # Cota dura: cada vuelta saca un mayorista, lo parte o lo marca sin
        # cupo. Se deja holgura porque partir una parada AGREGA entradas a la
        # ruta (2 mitades donde había 1) y esas todavía hay que reubicarlas.
        for _ in range(len(nuevo.get(clave, [])) * 3 + 6):
            if _total(clave) <= _cap(clave) + 1e-6:
                break
            movibles = sorted(
                [m for m in nuevo[clave] if id(m) not in sin_cupo],
                key=lambda m: (-(_float(m.get("peso_kg")) or 0.0),
                               str(m.get("id_cliente"))))
            if not movibles:
                break
            may = movibles[0]
            peso = _float(may.get("peso_kg")) or 0.0
            la, lo = _float(may.get("latitud")), _float(may.get("longitud"))

            # ── 1. otra ruta del mismo día con hueco, la más cercana ──
            destino = None
            cand = []
            for otra in _claves_activas(dia):
                if otra == clave:
                    continue
                if _total(otra) + peso > _cap(otra) + 1e-6:
                    continue
                c = coords_rutas.get(otra)
                if la is not None and lo is not None and c:
                    d = _haversine_km(la, lo, c[0], c[1])
                    if d > float(max_km):
                        continue
                    cand.append((round(d, 6), str(otra[0]), otra))
                else:
                    cand.append((float("inf"), str(otra[0]), otra))
            if cand:
                destino = sorted(cand)[0][2]
                via = "MAYORISTA_REUBICADO_CUPO"
            else:
                # ── 2. viaje de mayoristas solo: unidad libre más chica ──
                usadas = {k[0] for k in _claves_activas(dia)}
                libres = sorted(
                    [(cap[u], str(u)) for u in cap
                     if u not in usadas and cap[u] + 1e-6 >= peso])
                if libres:
                    destino = (libres[0][1], dia)
                    via = "VIAJE_MAYORISTAS_SOLO"

            if destino is None and (may.get("documentos") or []):
                # ── 3. parada consolidada sin destino: se parte A PROPÓSITO ──
                # La parada es indivisible por defecto, pero dejar la ruta
                # marcada sobre el 100 % es peor. Se corta por folios completos
                # y la división queda registrada para imprimirla.
                from logic.consolidacion_mayoristas import partir_parada_por_capacidad
                partes, exc_part = partir_parada_por_capacidad(
                    may, _cap(clave) - (_total(clave) - (_float(may.get("peso_kg")) or 0.0)))
                if exc_part and exc_part["tipo"] == "PARADA_MAYORISTA_PARTIDA":
                    nuevo[clave] = [m for m in nuevo[clave] if m is not may] + partes
                    excepciones.append(dict(exc_part, desde_unidad=unidad, dia=dia))
                    continue

            if destino is None:
                # ── 4. no hay destino posible: se registra y se queda ──
                sin_cupo.add(id(may))
                excepciones.append({
                    "tipo": "MAYORISTA_SIN_CUPO", "id_cliente": may.get("id_cliente"),
                    "nombre": may.get("nombre"), "peso_kg": peso,
                    "desde_unidad": unidad, "dia": dia,
                    "motivo": (f"{peso:,.0f} kg no caben en ninguna ruta ni unidad "
                               f"libre del {dia}; queda en {unidad} y la ruta "
                               f"sale marcada por encima de su capacidad"),
                })
                continue

            nuevo[clave] = [m for m in nuevo[clave] if m is not may]
            nuevo.setdefault(destino, []).append(may)
            if destino not in coords_rutas and la is not None and lo is not None:
                coords_rutas[destino] = (la, lo)   # centroide del viaje nuevo
            excepciones.append({
                "tipo": via, "id_cliente": may.get("id_cliente"),
                "nombre": may.get("nombre"), "peso_kg": peso,
                "desde_unidad": unidad, "a_unidad": destino[0], "dia": dia,
                "motivo": (f"{unidad}/{dia} excede su capacidad tras el enganche "
                           f"por zona; el cliente se mueve a {destino[0]}"
                           + ("" if via == "MAYORISTA_REUBICADO_CUPO"
                              else " en viaje de mayoristas solo")),
            })
    return nuevo, excepciones


def resolver_destino_enganche(nucleo, otros_grupos, rutas_por_grupo,
                              lat, lon, coords_rutas,
                              max_km: float = MAX_KM_ENGANCHE) -> dict:
    """
    A qué RUTA concreta de la semana se engancha una zona, cuando su grupo
    núcleo puede no tener ruta.

    El núcleo no siempre viaja: COTAXTLA y CUITLÁHUAC tienen como núcleo el g31
    (Amatlán), un grupo FLEXIBLE de UNA sucursal. Si Amatlán no recibe pedido esa
    semana, no hay ruta a la cual engancharse. En el histórico hay viajes que
    llevaron sólo mayoristas (Cotaxtla tuvo 5), así que el caso es real y el
    sistema debe poder producirlos — pero nunca como primera opción.

    Orden:
      1. NUCLEO              — el grupo núcleo tiene ruta: se engancha ahí.
      2. SEGUNDO_GRUPO       — otro grupo frecuente de la zona con ruta.
      3. GEOGRAFIA_RUTA      — la ruta existente más cercana (dentro de max_km).
      4. VIAJE_MAYORISTAS_SOLO — último recurso; el pedido NUNCA se cae en
                                 silencio.

    nucleo          : id del grupo núcleo (puede ser None).
    otros_grupos    : otros grupos de la zona, en orden de frecuencia.
    rutas_por_grupo : {(unidad, dia): [grupos en esa ruta]}.
    coords_rutas    : {(unidad, dia): (lat, lon)} centroide de cada ruta.
    """
    rutas = rutas_por_grupo or {}

    def _ruta_de(grupo):
        # desempate determinista por (día implícito en la clave, unidad)
        for clave in sorted(rutas, key=lambda k: (str(k[1]), str(k[0]))):
            if grupo in (rutas[clave] or []):
                return clave
        return None

    if nucleo is not None:
        clave = _ruta_de(nucleo)
        if clave:
            return {"destino": clave, "via": "NUCLEO", "grupo": nucleo,
                    "distancia_km": None,
                    "motivo": f"el grupo núcleo {nucleo} viaja esta semana"}

    for g in (otros_grupos or []):
        clave = _ruta_de(g)
        if clave:
            return {"destino": clave, "via": "SEGUNDO_GRUPO", "grupo": g,
                    "distancia_km": None,
                    "motivo": (f"el núcleo {nucleo} no tiene ruta esta semana; "
                               f"se usa el grupo {g}, también frecuente en la zona")}

    la, lo = _float(lat), _float(lon)
    if la is not None and lo is not None and coords_rutas:
        cand = sorted(
            ((_haversine_km(la, lo, c[0], c[1]), clave)
             for clave, c in coords_rutas.items() if clave in rutas),
            key=lambda t: (round(t[0], 6), str(t[1])))
        if cand and cand[0][0] <= float(max_km):
            dist, clave = cand[0]
            return {"destino": clave, "via": "GEOGRAFIA_RUTA", "grupo": None,
                    "distancia_km": round(dist, 1),
                    "motivo": (f"ningún grupo de la zona viaja esta semana; "
                               f"ruta existente más cercana ({dist:.0f} km)")}

    return {"destino": None, "via": "VIAJE_MAYORISTAS_SOLO", "grupo": None,
            "distancia_km": None,
            "motivo": ("ni el núcleo ni otro grupo de la zona viajan, y no hay "
                       "ruta existente dentro del umbral: viaje de mayoristas "
                       "solo (último recurso, como en el histórico)")}
