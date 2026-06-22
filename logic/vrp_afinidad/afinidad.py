"""
logic/vrp_afinidad/afinidad.py
Construcción de la matriz de co-ocurrencia ponderada por recencia.
"""
from collections import defaultdict
from typing import Any


def _sort_key(hist: dict) -> tuple:
    """Mismo criterio que vrp_logic: CSV primero (grupo 0), confirmadas después (grupo 1);
    dentro de cada grupo por fecha ascendente (más antiguo = peso menor)."""
    grupo = 1 if hist.get("confirmada") else 0
    fi = hist.get("fecha_inicio", "")
    fecha = fi[:10] if fi else (hist.get("cargado_en", "") or "")[:10]
    return (grupo, fecha)


def _peso_total_historial(hist: dict) -> float:
    """Suma de kg_entrega de todas las filas de un historial (una semana/CSV)."""
    total = 0.0
    for fila in hist.get("filas", []):
        try:
            total += float(fila.get("kg_entrega", 0) or 0)
        except (ValueError, TypeError):
            continue
    return total


def construir_afinidad(historiales: list[dict], pedidos_dict: dict | None = None) -> dict:
    """
    Construye la matriz de afinidad entre nodos a partir de historiales de rutas.

    Parámetros
    ----------
    historiales : list[dict]
        Cada dict tiene: nombre, confirmada (bool), fecha_inicio, filas[].
        Cada fila: id_sucursal, vehiculo, dia_semana, secuencia_visita, kg_entrega.
        Opcionalmente: tipo ('sucursal' | 'mayorista').
    pedidos_dict : dict | None
        {id_sucursal: kg} del pedido actual. Si se da, la semana histórica cuyo
        peso TOTAL se parezca más al peso total del pedido actual recibe más
        peso en la votación (afinidad, secuencia, vehículo preferido) que las
        demás — el peso manda sobre la antigüedad pura. Sin este parámetro se
        usa solo la ponderación por recencia (comportamiento anterior).

    Retorna
    -------
    dict con claves:
        'afinidad'         : dict {(i,j): float}  — simétrica, normalizada a [0,1]
        'afinidad_raw'     : dict {(i,j): float}  — acumulados sin normalizar
        'kg_promedio'      : dict {id_nodo: float}
        'pref_vehiculo_dia': dict {id_nodo: (vehiculo, dia)}
        'capacidad_vehiculo': dict {abrev: int}  — vacío aquí, se rellena desde fuera
        'pesos_usados'     : list[float]
        'nodos'            : list  — todos los ids de nodo vistos
    """
    if not historiales:
        return {
            "afinidad": {},
            "afinidad_raw": {},
            "kg_promedio": {},
            "pref_vehiculo_dia": {},
            "capacidad_vehiculo": {},
            "pesos_usados": [],
            "nodos": [],
            "orden_par": {},
        }

    ordenados = sorted(historiales, key=_sort_key)
    n = len(ordenados)
    pesos_recencia = [float(i + 1) for i in range(n)]  # más reciente → peso n

    # Factor de similitud de peso: la semana histórica con peso total más
    # parecido al pedido actual pesa hasta 5× más que la recencia sola.
    factor_similitud = [1.0] * n
    if pedidos_dict:
        peso_actual = sum(pedidos_dict.values())
        pesos_doc = [_peso_total_historial(h) for h in ordenados]
        if peso_actual > 0 and any(p > 0 for p in pesos_doc):
            diffs = [abs(p - peso_actual) / peso_actual for p in pesos_doc]
            max_diff = max(diffs) or 1.0
            factor_similitud = [1.0 + 4.0 * (1.0 - min(d / max_diff, 1.0)) for d in diffs]

    pesos = [r * f for r, f in zip(pesos_recencia, factor_similitud)]

    afinidad_raw: dict[tuple, float] = defaultdict(float)
    orden_par_raw: dict[tuple, float] = defaultdict(float)
    kg_records: dict[Any, list] = defaultdict(list)
    vd_votos: dict[Any, dict] = defaultdict(lambda: defaultdict(float))

    for peso, hist in zip(pesos, ordenados):
        filas = hist.get("filas", [])
        # Agrupar por (vehiculo, dia): lista de (sid, secuencia_visita)
        grupos: dict[tuple, list] = defaultdict(list)
        grupos_seq: dict[tuple, list] = defaultdict(list)
        for fila in filas:
            veh = str(fila.get("vehiculo", "")).strip()
            dia = str(fila.get("dia_semana", "")).strip().upper()
            if not veh or not dia:
                continue
            try:
                sid = int(fila["id_sucursal"])
            except (KeyError, ValueError, TypeError):
                continue
            grupos[(veh, dia)].append(sid)

            try:
                seq = float(fila.get("secuencia_visita") or 0)
            except (ValueError, TypeError):
                seq = 0.0
            grupos_seq[(veh, dia)].append((sid, seq))

            try:
                kg = float(fila.get("kg_entrega", 0) or 0)
            except (ValueError, TypeError):
                kg = 0.0
            kg_records[sid].append((kg, peso))
            vd_votos[sid][(veh, dia)] += peso

        # Sumar afinidad para pares que comparten la misma ruta
        for (veh, dia), miembros in grupos.items():
            miembros_uniq = list(dict.fromkeys(miembros))  # preservar orden, eliminar dups
            for idx_a in range(len(miembros_uniq)):
                for idx_b in range(idx_a + 1, len(miembros_uniq)):
                    a = miembros_uniq[idx_a]
                    b = miembros_uniq[idx_b]
                    par = (min(a, b), max(a, b))
                    afinidad_raw[par] += peso

        # Acumular orden de visita: por cada par que comparte ruta,
        # votar quién viene primero según secuencia_visita, ponderado por recencia.
        # orden_par_raw[(min,max)] > 0 → min visita antes que max.
        for (veh, dia), items in grupos_seq.items():
            items_sorted = sorted(dict.fromkeys(
                (sid for sid, _ in items),  # dedup preservando primer seq visto
            ) if False else items, key=lambda x: x[1])
            # dedup por sid conservando el de menor seq
            seen: dict = {}
            for sid, seq in items_sorted:
                if sid not in seen:
                    seen[sid] = seq
            ordenados_seq = [sid for sid, _ in sorted(seen.items(), key=lambda x: x[1])]
            for idx_a in range(len(ordenados_seq)):
                for idx_b in range(idx_a + 1, len(ordenados_seq)):
                    a = ordenados_seq[idx_a]  # a visita antes que b
                    b = ordenados_seq[idx_b]
                    par = (min(a, b), max(a, b))
                    signo = 1.0 if a < b else -1.0  # +1 = min va primero
                    orden_par_raw[par] += signo * peso

    # Normalizar afinidad a [0,1]
    max_val = max(afinidad_raw.values()) if afinidad_raw else 1.0
    if max_val == 0:
        max_val = 1.0
    afinidad_norm = {par: v / max_val for par, v in afinidad_raw.items()}

    # Normalizar orden_par a [-1,1]: +1 = min visita primero, -1 = max visita primero
    max_ord = max((abs(v) for v in orden_par_raw.values()), default=1.0) or 1.0
    orden_par = {par: v / max_ord for par, v in orden_par_raw.items()}

    # kg promedio ponderado por nodo
    kg_promedio = {}
    for sid, records in kg_records.items():
        total_w = sum(w for _, w in records)
        kg_promedio[sid] = sum(kg * w for kg, w in records) / total_w if total_w > 0 else 0.0

    # Preferencia (vehiculo, dia) por nodo
    pref_vehiculo_dia = {}
    for sid, votos in vd_votos.items():
        pref_vehiculo_dia[sid] = max(votos, key=votos.get)

    todos_nodos = sorted(set(kg_records.keys()))

    return {
        "afinidad": afinidad_norm,
        "afinidad_raw": dict(afinidad_raw),
        "kg_promedio": kg_promedio,
        "pref_vehiculo_dia": pref_vehiculo_dia,
        "capacidad_vehiculo": {},
        "pesos_usados": pesos,
        "nodos": todos_nodos,
        "orden_par": orden_par,
    }
