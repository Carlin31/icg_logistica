"""
logic/consolidacion_mayoristas.py

Consolidación de DOCUMENTOS de mayoreo en PARADAS, antes de rutear. Puro: sin BD.

Por qué existe: el sistema venía ruteando documento por documento. Los 4 folios
de PLAZA COMERCIAL RIO —3,152 kg, un solo domicilio— acabaron en tres viajes
distintos (F 350_2/martes, T 25/miércoles, T 17_2/jueves) porque para el solver
eran tres pedidos independientes. La hoja del planeador los manda juntos: es UNA
parada. Lo mismo con Cuitláhuac (AA1430 AL 36 = 315 kg en un solo local) y
Lombardo (BB3297/98/99/00).

La llave es la PROXIMIDAD, no el nombre ni la población, porque los casos
reales lo obligan:
  - Cuitláhuac agrupa CUATRO razones sociales distintas ('SUPER CUITLAHUAC',
    'CRISTIAN SALDAÑA', 'RENE SALDAÑA', 'SUPER LA MICHOACANA'): por nombre no
    se juntan nunca.
  - Lombardo tiene la población escrita de dos formas ('LOMBARDO' y 'MARIA
    LOMBARDO DE CASO'): por texto de población tampoco.
  - Y hay un par que el planeador SÍ escribe en una línea
    ('BB3294/96_CTES.SOCHIAPAN') pero cuyos puntos están a **51 km**. Ese no se
    consolida: la etiqueta de la hoja es una comodidad de escritura, no una
    parada física. La discrepancia se reporta, no se fuerza.
    
Enlace COMPLETO (todos los pares del grupo dentro del radio), el mismo criterio
que ya usan los grupos de co-viaje: evita el encadenamiento que produciría un
enlace simple (A cerca de B, B cerca de C, y A y C lejísimos).

La parada consolidada es CARGA INDIVISIBLE. Si no cabe en la unidad se parte a
propósito con `partir_parada_por_capacidad`, por folios completos y dejando
registro para imprimirlo — nunca en silencio dentro del solver.
"""
import math
import re
import unicodedata

# Radio para considerar que dos documentos se entregan en el mismo punto.
# 500 m cubre los casos reales con margen (Cuitláhuac 180 m, Lombardo 220 m) y
# deja fuera con claridad el par de Sochiapan (51 km).
RADIO_CONSOLIDACION_KM = 0.5


def _norm(s) -> str:
    s = str(s or "").strip().upper()
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


def _num(x) -> float:
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def _etiqueta(folios: list, nombres: list, poblacion: str) -> str:
    """
    'BB3304/05/20/21_PLAZA COMERCIAL RIO'   (un destinatario)
    'AA1430/31/32_CTES. CUITLAHUAC'         (varios destinatarios)

    Mismo criterio que el PDF: con un solo destinatario se nombra al cliente,
    que es más específico que la ciudad; con varios, la población.
    """
    if not folios:
        return ""
    pfx = re.match(r"^([A-Za-z]+)", folios[0])
    pfx = pfx.group(1).upper() if pfx else ""
    partes = [folios[0]]
    for f in folios[1:]:
        digitos = re.sub(r"\D", "", f[len(pfx):]) if pfx and f.upper().startswith(pfx) else ""
        partes.append(digitos[-2:] if len(digitos) >= 2 else f)
    doc_str = "/".join(partes)
    unicos = sorted({_norm(n) for n in nombres if n})
    if len(unicos) == 1:
        return f"{doc_str}_{unicos[0]}"
    return f"{doc_str}_CTES. {_norm(poblacion)}" if poblacion else doc_str


def consolidar_documentos(documentos: list, clientes: dict,
                          radio_km: float = RADIO_CONSOLIDACION_KM) -> list:
    """
    Agrupa documentos que se entregan en el mismo punto en una sola PARADA.

    documentos : [{documento, codigo, nombre, peso_total_kg}]
    clientes   : {id_cliente: {nombre, poblacion, latitud, longitud}}

    Devuelve [{folios, etiqueta, peso_kg, id_clientes, nombres, poblacion,
               latitud, longitud, sin_coordenada, documentos}] — nunca pierde ni
    duplica un documento.

    Determinista: los clientes se recorren por id ascendente y los grupos se
    forman con enlace completo, así que el orden de entrada no altera la salida.
    """
    # ── 1. documentos por cliente ──
    por_cliente: dict = {}
    for d in documentos or []:
        cod = d.get("codigo")
        if cod is None:
            continue
        por_cliente.setdefault(int(cod), []).append(d)

    puntos = []          # (id_cliente, lat, lon) con coordenada válida
    sin_coord = []
    for cid in sorted(por_cliente):
        c = (clientes or {}).get(cid) or {}
        la, lo = _float(c.get("latitud")), _float(c.get("longitud"))
        if la is None or lo is None:
            sin_coord.append(cid)
        else:
            puntos.append((cid, la, lo))

    # ── 2. cúmulos por proximidad, enlace COMPLETO ──
    cumulos: list = []
    for cid, la, lo in puntos:
        destino = None
        mejor = None
        for cum in cumulos:
            dists = [_haversine_km(la, lo, p[1], p[2]) for p in cum]
            if max(dists) <= float(radio_km):
                d = min(dists)
                if mejor is None or d < mejor:
                    mejor, destino = d, cum
        if destino is None:
            cumulos.append([(cid, la, lo)])
        else:
            destino.append((cid, la, lo))

    # un cliente sin coordenada nunca se mezcla: no hay evidencia de que sea el
    # mismo punto, y adivinarlo por nombre es justo lo que hay que evitar
    grupos = [[p[0] for p in cum] for cum in cumulos] + [[cid] for cid in sin_coord]

    # ── 3. armar la parada ──
    paradas = []
    for ids in grupos:
        docs = [d for cid in sorted(ids) for d in
                sorted(por_cliente.get(cid, []), key=lambda x: str(x.get("documento")))]
        docs.sort(key=lambda x: str(x.get("documento")))
        if not docs:
            continue
        info = [(clientes or {}).get(c) or {} for c in sorted(ids)]
        lats = [_float(i.get("latitud")) for i in info if _float(i.get("latitud")) is not None]
        lons = [_float(i.get("longitud")) for i in info if _float(i.get("longitud")) is not None]
        nombres = [d.get("nombre") or (clientes.get(d["codigo"]) or {}).get("nombre")
                   for d in docs]
        poblaciones = [i.get("poblacion") for i in info if i.get("poblacion")]
        folios = [str(d.get("documento")) for d in docs]
        paradas.append({
            "folios": folios,
            "etiqueta": _etiqueta(folios, nombres, poblaciones[0] if poblaciones else ""),
            "peso_kg": round(sum(_num(d.get("peso_total_kg")) for d in docs), 2),
            "id_clientes": sorted(ids),
            "id_cliente": sorted(ids)[0],          # representante, para el enganche
            "nombres": sorted({n for n in nombres if n}),
            "nombre": sorted({n for n in nombres if n})[0] if any(nombres) else "",
            "poblacion": poblaciones[0] if poblaciones else None,
            "latitud": (sum(lats) / len(lats)) if lats else None,
            "longitud": (sum(lons) / len(lons)) if lons else None,
            "sin_coordenada": not lats,
            "documentos": docs,
        })
    return sorted(paradas, key=lambda p: p["folios"][0])


def partir_parada_por_capacidad(parada: dict, capacidad_kg: float):
    """
    Parte una parada consolidada SÓLO si no cabe en la unidad, y a propósito.

    La parada es carga indivisible por defecto. Cuando no cabe se corta por
    FOLIOS COMPLETOS (un folio es un documento: no se parte) de mayor a menor,
    llenando cada parte hasta la capacidad. La división se devuelve como
    excepción para imprimirla; el planeador tiene que verla.

    Devuelve (partes, excepcion). `excepcion` es None si no hizo falta partir.
    Si un solo folio ya excede la unidad, NO se parte: se devuelve entera con
    una excepción SIN_CUPO — el solver no puede resolver eso solo.
    """
    cap = _num(capacidad_kg)
    if not parada or cap <= 0 or _num(parada.get("peso_kg")) <= cap + 1e-6:
        return [parada], None

    docs = sorted(parada.get("documentos") or [],
                  key=lambda d: (-_num(d.get("peso_total_kg")), str(d.get("documento"))))
    if any(_num(d.get("peso_total_kg")) > cap + 1e-6 for d in docs) or len(docs) <= 1:
        return [parada], {
            "tipo": "PARADA_MAYORISTA_SIN_CUPO",
            "etiqueta": parada.get("etiqueta"), "peso_kg": parada.get("peso_kg"),
            "capacidad_kg": cap, "folios": list(parada.get("folios") or []),
            "motivo": (f"la parada pesa {_num(parada.get('peso_kg')):,.0f} kg y un "
                       f"solo folio ya excede los {cap:,.0f} kg de la unidad; no "
                       f"se parte automáticamente"),
        }

    cajones: list = []
    for d in docs:                       # first-fit decreasing, determinista
        kg = _num(d.get("peso_total_kg"))
        for c in cajones:
            if sum(_num(x.get("peso_total_kg")) for x in c) + kg <= cap + 1e-6:
                c.append(d)
                break
        else:
            cajones.append([d])

    partes = []
    for i, c in enumerate(cajones, 1):
        c.sort(key=lambda d: str(d.get("documento")))
        folios = [str(d.get("documento")) for d in c]
        partes.append(dict(
            parada, folios=folios, documentos=c,
            peso_kg=round(sum(_num(d.get("peso_total_kg")) for d in c), 2),
            etiqueta=_etiqueta(folios, [d.get("nombre") for d in c],
                               parada.get("poblacion") or ""),
            parte=i, partes_totales=len(cajones)))
    excepcion = {
        "tipo": "PARADA_MAYORISTA_PARTIDA",
        "etiqueta": parada.get("etiqueta"), "peso_kg": parada.get("peso_kg"),
        "capacidad_kg": cap, "partes": len(partes),
        "reparto": [{"folios": p["folios"], "peso_kg": p["peso_kg"]} for p in partes],
        "motivo": (f"la parada consolidada pesa {_num(parada.get('peso_kg')):,.0f} kg "
                   f"y la unidad admite {cap:,.0f}: se parte en {len(partes)} "
                   f"entregas por folios completos"),
    }
    return partes, excepcion

