"""
logic/demand_forecast.py
Predicción de demanda (kg por sucursal) para el próximo ciclo logístico.

Modelos disponibles:
  - baseline : Promedio ponderado por recencia (comportamiento actual del VRP)
  - SES      : Simple Exponential Smoothing con alpha optimizado por SSE
  - Holt     : Double Exponential Smoothing (maneja tendencia lineal)

Selección automática por sucursal via leave-last-out cross-validation.

Uso típico:
    from logic.demand_forecast import predecir_demanda, evaluar_modelos
    from logic.historico_logic import obtener_historicos_como_dfs  # ya existente

    # Cargar historiales crudos desde MongoDB
    db = get_db()
    historics = list(db["rutas_historicas"].find({}))
    historics.sort(key=lambda d: d.get("fecha_inicio", d.get("cargado_en", ""))[:10])

    predicciones = predecir_demanda(historics)
    # -> {101: {'kg_predicho': 1240.0, 'modelo': 'Holt', ...}, ...}

    metricas = evaluar_modelos(historics)
    # -> {'metricas_por_modelo': {...}, 'total_sucursales_evaluadas': 28}
"""

import numpy as np
from collections import defaultdict
from typing import Dict, List, Tuple, Optional


# ── Extracción de series desde documentos MongoDB ─────────────────────────────

def extraer_series_demanda(historics: List[dict]) -> Dict[int, List[float]]:
    """
    Construye una serie temporal de kg totales por sucursal.

    Cada documento en historics = un ciclo logístico.
    La lista de retorno está ordenada del ciclo más antiguo al más reciente,
    coincidiendo con el orden en que se pasa historics.

    Returns:
        {id_sucursal: [kg_ciclo0, kg_ciclo1, ..., kg_cicloN]}
    """
    series: Dict[int, List[float]] = {}
    n_ciclos = len(historics)

    for ciclo_idx, doc in enumerate(historics):
        agg: Dict[int, float] = defaultdict(float)
        for fila in doc.get("filas", []):
            suc_id = fila.get("id_sucursal")
            kg = float(fila.get("kg_entrega", 0) or 0)
            if suc_id is not None and kg > 0:
                agg[int(suc_id)] += kg

        for suc_id, kg_total in agg.items():
            if suc_id not in series:
                series[suc_id] = [0.0] * ciclo_idx
            while len(series[suc_id]) < ciclo_idx:
                series[suc_id].append(0.0)
            series[suc_id].append(kg_total)

    # Rellenar ciclos faltantes al final con 0
    for suc_id in series:
        while len(series[suc_id]) < n_ciclos:
            series[suc_id].append(0.0)

    return series


# ── Modelos de predicción ─────────────────────────────────────────────────────

def _baseline(series: np.ndarray) -> float:
    """Promedio ponderado por recencia — comportamiento actual del VRP."""
    n = len(series)
    weights = np.arange(1.0, n + 1.0)
    return float(np.average(series, weights=weights))


def _ses(series: np.ndarray, alpha: float) -> float:
    """Simple Exponential Smoothing. Retorna predicción del próximo período."""
    level = float(series[0])
    for y in series[1:]:
        level = alpha * float(y) + (1.0 - alpha) * level
    return level


def _holt(series: np.ndarray, alpha: float, beta: float) -> float:
    """Holt's Double Exponential Smoothing. Maneja tendencia lineal."""
    if len(series) < 2:
        return float(series[-1])
    level = float(series[0])
    trend = float(series[1]) - float(series[0])
    for y in series[1:]:
        prev_level = level
        level = alpha * float(y) + (1.0 - alpha) * (level + trend)
        trend = beta * (level - prev_level) + (1.0 - beta) * trend
    return level + trend


def _optimizar_alpha(series: np.ndarray) -> float:
    """Grid search sobre alpha en [0.05, 0.95] minimizando SSE one-step-ahead."""
    best_alpha, best_sse = 0.3, float("inf")
    for a in np.arange(0.05, 1.0, 0.05):
        sse = 0.0
        level = float(series[0])
        for y in series[1:]:
            sse += (float(y) - level) ** 2
            level = a * float(y) + (1.0 - a) * level
        if sse < best_sse:
            best_sse, best_alpha = sse, float(a)
    return best_alpha


def _optimizar_holt(series: np.ndarray) -> Tuple[float, float]:
    """Grid search sobre (alpha, beta) minimizando SSE one-step-ahead."""
    best_params = (0.3, 0.1)
    best_sse = float("inf")
    for a in np.arange(0.1, 1.0, 0.1):
        for b in np.arange(0.05, 0.55, 0.1):
            sse = 0.0
            level = float(series[0])
            trend = float(series[1]) - float(series[0])
            for y in series[1:]:
                pred = level + trend
                sse += (float(y) - pred) ** 2
                prev_level = level
                level = a * float(y) + (1.0 - a) * (level + trend)
                trend = b * (level - prev_level) + (1.0 - b) * trend
            if sse < best_sse:
                best_sse, best_params = sse, (float(a), float(b))
    return best_params


# ── API pública ───────────────────────────────────────────────────────────────

def predecir_demanda(historics: List[dict]) -> Dict[int, dict]:
    """
    Predice kg del próximo ciclo logístico por sucursal.

    Selección de modelo: leave-last-out CV sobre el historial de cada sucursal.
    El modelo con menor error absoluto en el último ciclo conocido gana.

    Args:
        historics: lista de documentos rutas_historicas, ordenados del más
                   antiguo al más reciente (mismo orden que _sort_key_historico).

    Returns:
        {
            id_sucursal: {
                'kg_predicho' : float,
                'modelo'      : 'baseline' | 'SES' | 'Holt' | 'last_known',
                'alpha'       : float | None,
                'beta'        : float | None,
                'confianza'   : float (0–1),
                'historial_kg': [float],
            }
        }
    """
    series_map = extraer_series_demanda(historics)
    resultado: Dict[int, dict] = {}

    for suc_id, serie in series_map.items():
        arr = np.array([v for v in serie if v > 0], dtype=float)
        n = len(arr)

        if n == 0:
            continue

        if n == 1:
            resultado[suc_id] = {
                "kg_predicho": round(arr[0], 1),
                "modelo": "last_known",
                "alpha": None, "beta": None,
                "confianza": 0.25,
                "historial_kg": arr.tolist(),
            }
            continue

        # Partición leave-last-out
        train, actual = arr[:-1], arr[-1]

        # ── Predicciones en train con cada modelo ─────────────────────────────
        pred_base = _baseline(train)
        err_base = abs(actual - pred_base)

        alpha_cv = _optimizar_alpha(train) if len(train) >= 2 else 0.3
        pred_ses = _ses(train, alpha_cv)
        err_ses = abs(actual - pred_ses)

        if len(train) >= 3:
            a_cv, b_cv = _optimizar_holt(train)
            pred_holt = _holt(train, a_cv, b_cv)
            err_holt = abs(actual - pred_holt)
        else:
            err_holt = float("inf")

        # ── Selección del mejor modelo ────────────────────────────────────────
        errores = {"baseline": err_base, "SES": err_ses}
        if err_holt != float("inf"):
            errores["Holt"] = err_holt
        mejor = min(errores, key=errores.get)

        # ── Predicción final con serie completa ───────────────────────────────
        if mejor == "SES":
            alpha_f = _optimizar_alpha(arr)
            kg_pred = _ses(arr, alpha_f)
            al, be = round(alpha_f, 2), None
        elif mejor == "Holt":
            a_f, b_f = _optimizar_holt(arr)
            kg_pred = _holt(arr, a_f, b_f)
            al, be = round(a_f, 2), round(b_f, 2)
        else:
            kg_pred = _baseline(arr)
            al, be = None, None

        # Confianza: inversa del coeficiente de variación
        cv = float(np.std(arr) / np.mean(arr)) if np.mean(arr) > 0 else 1.0
        confianza = round(max(0.10, min(0.99, 1.0 - cv * 0.6)), 2)

        resultado[suc_id] = {
            "kg_predicho": round(max(0.0, kg_pred), 1),
            "modelo": mejor,
            "alpha": al,
            "beta": be,
            "confianza": confianza,
            "historial_kg": arr.tolist(),
        }

    return resultado


def evaluar_modelos(historics: List[dict]) -> dict:
    """
    Evaluación comparativa de los tres modelos mediante leave-last-out CV.

    Args:
        historics: lista ordenada de documentos rutas_historicas.

    Returns:
        {
            'metricas_por_modelo': {
                'baseline': {'MAE_kg': float, 'RMSE_kg': float, 'MAPE_pct': float,
                             'n_sucursales': int},
                'SES':      {...},
                'Holt':     {...},
            },
            'total_sucursales_evaluadas': int,
            'mejor_modelo_global': str,
            'mejora_mae_vs_baseline': {'SES': float_pct, 'Holt': float_pct},
        }
    """
    series_map = extraer_series_demanda(historics)

    bucket: Dict[str, Dict[str, list]] = {
        m: {"abs": [], "sq": [], "pct": []} for m in ("baseline", "SES", "Holt")
    }
    evaluadas = 0

    for suc_id, serie in series_map.items():
        arr = np.array([v for v in serie if v > 0], dtype=float)
        if len(arr) < 3:
            continue

        train, actual = arr[:-1], arr[-1]
        if actual <= 0:
            continue
        evaluadas += 1

        def _registrar(modelo: str, pred: float) -> None:
            ae = abs(actual - pred)
            bucket[modelo]["abs"].append(ae)
            bucket[modelo]["sq"].append(ae ** 2)
            bucket[modelo]["pct"].append(ae / actual * 100.0)

        _registrar("baseline", _baseline(train))

        alpha_cv = _optimizar_alpha(train) if len(train) >= 2 else 0.3
        _registrar("SES", _ses(train, alpha_cv))

        if len(train) >= 3:
            a_cv, b_cv = _optimizar_holt(train)
            _registrar("Holt", _holt(train, a_cv, b_cv))

    metricas: Dict[str, dict] = {}
    for modelo, data in bucket.items():
        if data["abs"]:
            abs_arr = np.array(data["abs"])
            sq_arr = np.array(data["sq"])
            pct_arr = np.array(data["pct"])
            metricas[modelo] = {
                "MAE_kg": round(float(np.mean(abs_arr)), 1),
                "RMSE_kg": round(float(np.sqrt(np.mean(sq_arr))), 1),
                "MAPE_pct": round(float(np.mean(pct_arr)), 2),
                "n_sucursales": len(abs_arr),
            }
        else:
            metricas[modelo] = {
                "MAE_kg": None, "RMSE_kg": None,
                "MAPE_pct": None, "n_sucursales": 0,
            }

    # Mejor modelo por MAE
    validos = {m: v["MAE_kg"] for m, v in metricas.items() if v["MAE_kg"] is not None}
    mejor = min(validos, key=validos.get) if validos else "baseline"

    # Mejora porcentual de MAE vs baseline
    base_mae = metricas["baseline"]["MAE_kg"]
    mejora: Dict[str, Optional[float]] = {}
    for m in ("SES", "Holt"):
        mae = metricas[m]["MAE_kg"]
        if base_mae and mae is not None and base_mae > 0:
            mejora[m] = round((base_mae - mae) / base_mae * 100.0, 1)
        else:
            mejora[m] = None

    return {
        "metricas_por_modelo": metricas,
        "total_sucursales_evaluadas": evaluadas,
        "mejor_modelo_global": mejor,
        "mejora_mae_vs_baseline": mejora,
    }
