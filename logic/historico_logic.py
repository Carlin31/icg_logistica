"""
logic/historico_logic.py
Gestión del historial de rutas y generación VRP híbrida para Flask.

Colección MongoDB: `rutas_historicas`
Cada documento:
  {
    nombre:        str,
    filas:         [{id_sucursal, vehiculo, dia_semana, secuencia_visita, kg_entrega}],
    n_sucursales:  int,
    n_rutas:       int,
    cargado_en:    ISO str,
    confirmada:    bool,
    logistica_id:  str (solo rutas confirmadas desde el panel)
  }
"""

import io
import csv
import math
import time
import json as _json
import unicodedata
import urllib.request
from datetime import datetime, date as _date
from collections import defaultdict
from bson import ObjectId
from bson.errors import InvalidId

# Coordenadas fijas de la matriz (no editables desde la UI)
_MATRIZ_LAT = 18.87329315661368
_MATRIZ_LON = -96.9491574270346
_OSRM_BASE        = "https://router.project-osrm.org/route/v1/driving"
_OSRM_TIMEOUT     = 20
_OSRM_MAX_RETRIES = 3
_OSRM_RETRY_DELAY = 1.5

from db import get_db
from logic.vrp_logic import (
    build_template_from_history,
    generate_routes_vrp,
    obtener_capacidades_vehiculos,
    obtener_placas_por_abrev,
    obtener_info_vehiculos,
    DIA_ORDEN,
)

try:
    import pandas as pd
    _PANDAS = True
except ImportError:
    _PANDAS = False


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_oid(doc_id: str):
    try:
        return ObjectId(doc_id)
    except (InvalidId, TypeError):
        return None


def _normalizar_dia(dia: str) -> str:
    """'MIÉRCOLES' → 'miercoles'"""
    s = dia.lower()
    s = unicodedata.normalize("NFD", s)
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


_MESES_ES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio",
             "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def _nombre_desde_fechas(fecha_inicio: str, fecha_fin: str) -> str:
    """Genera el nombre del historial desde el rango de fechas (mismo formato que la logística)."""
    try:
        fi = _date.fromisoformat(fecha_inicio)
        ff = _date.fromisoformat(fecha_fin)
        mes_i = _MESES_ES[fi.month]
        mes_f = _MESES_ES[ff.month]
        anio  = ff.year
        if fi.month == ff.month and fi.year == ff.year:
            return f"Logística del {fi.day} al {ff.day} de {mes_i} del {anio}"
        return f"Logística del {fi.day} de {mes_i} al {ff.day} de {mes_f} del {anio}"
    except Exception:
        return ""


def _sort_key_historico(doc: dict) -> tuple:
    """
    Clave de ordenamiento para historiales: más antiguo → más reciente (= mayor peso).
    Las rutas confirmadas desde el panel siempre van al final (grupo 1),
    los CSV importados van primero (grupo 0), cada grupo ordenado por fecha.
    """
    confirmada = 1 if doc.get("confirmada") else 0
    fi = doc.get("fecha_inicio", "")
    fecha = fi[:10] if fi else doc.get("cargado_en", "")[:10]
    return (confirmada, fecha)


# ── CRUD de Rutas Históricas ──────────────────────────────────────────────────

def listar_rutas_historicas() -> list:
    """
    Lista todos los historiales cargados en orden de peso ascendente
    (más antiguo = peso 1, confirmadas recientes = peso n).
    Mismo criterio que el VRP para que el número mostrado en la UI sea fiel al peso real.
    """
    try:
        db   = get_db()
        docs = list(db["rutas_historicas"].find({}, {"filas": 0}))
        docs.sort(key=_sort_key_historico)
        for doc in docs:
            doc["_id"] = str(doc["_id"])
        return docs
    except Exception as e:
        print(f"[listar_rutas_historicas] {e}")
        return []


def cargar_csv_historico(csv_bytes: bytes, nombre: str) -> dict:
    """
    Parsea un CSV de rutas históricas y lo guarda en MongoDB.
    Formato esperado: id_sucursal, vehiculo, dia_semana, secuencia_visita, kg_entrega
    """
    if not _PANDAS:
        return {"status": "error", "mensaje": "pandas no está instalado"}

    try:
        csv_str = csv_bytes.decode("utf-8-sig", errors="replace")
        df      = pd.read_csv(io.StringIO(csv_str))
        df.columns = [c.strip().replace("﻿", "") for c in df.columns]

        required = ["id_sucursal", "vehiculo", "dia_semana", "secuencia_visita", "kg_entrega"]
        missing  = [c for c in required if c not in df.columns]
        if missing:
            return {"status": "error", "mensaje": f"Columnas faltantes: {missing}"}

        df["id_sucursal"]      = df["id_sucursal"].astype(int)
        df["secuencia_visita"] = df["secuencia_visita"].astype(int)
        df["kg_entrega"]       = df["kg_entrega"].astype(float)
        df["dia_semana"]       = df["dia_semana"].str.strip().str.upper()

        filas   = df.to_dict(orient="records")
        n_suc   = int(df["id_sucursal"].nunique())
        n_rut   = int(df.groupby(["vehiculo", "dia_semana"]).ngroups)

        db  = get_db()
        doc = {
            "nombre":       nombre,
            "filas":        filas,
            "n_sucursales": n_suc,
            "n_rutas":      n_rut,
            "cargado_en":   datetime.now().isoformat(),
            "confirmada":   False,
        }
        res = db["rutas_historicas"].insert_one(doc)
        return {
            "status":       "ok",
            "id":           str(res.inserted_id),
            "n_sucursales": n_suc,
            "n_rutas":      n_rut,
        }
    except Exception as e:
        return {"status": "error", "mensaje": str(e)}


def eliminar_historico(hist_id: str) -> dict:
    oid = _parse_oid(hist_id)
    if not oid:
        return {"status": "error", "mensaje": "ID inválido"}
    try:
        db = get_db()
        db["rutas_historicas"].delete_one({"_id": oid})
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "mensaje": str(e)}


def obtener_historicos_como_dfs() -> list:
    """
    Devuelve lista de DataFrames (pandas) de todos los historiales cargados,
    ordenados del más antiguo al más reciente por fecha efectiva de la logística
    (fecha_inicio para rutas confirmadas, cargado_en para CSV importados).
    """
    if not _PANDAS:
        return []
    try:
        db   = get_db()
        docs = list(db["rutas_historicas"].find({}))
        docs.sort(key=_sort_key_historico)
        dfs  = []
        for doc in docs:
            filas = doc.get("filas", [])
            if filas:
                dfs.append(pd.DataFrame(filas))
        return dfs
    except Exception as e:
        print(f"[obtener_historicos_como_dfs] {e}")
        return []


# ── Generación VRP desde historial ────────────────────────────────────────────

def generar_rutas_vrp(logistica_id: str) -> dict:
    """
    Genera rutas VRP híbridas usando historial + datos de extracción actuales.

    Flujo:
    1. Lee historiales desde MongoDB → DataFrames
    2. Construye template (vehiculo, dia, seq) por sucursal
    3. Lee pedidos actuales desde extraccion[logistica_id]
    4. Obtiene coordenadas desde colección sucursales
    5. Ejecuta VRP hybrid
    6. Guarda resultado en asignaciones[logistica_id] (detalle_por_dia)

    Retorna dict con status, total_rutas, reporte VRP, consolidaciones.
    """
    oid = _parse_oid(logistica_id)
    if not oid:
        return {"status": "error", "mensaje": "logistica_id inválido"}

    if not _PANDAS:
        return {"status": "error", "mensaje": "pandas no está instalado"}

    db = get_db()

    # ── 1. Cargar historiales ─────────────────────────────────────────────────
    dfs = obtener_historicos_como_dfs()
    if not dfs:
        return {
            "status":  "error",
            "mensaje": "No hay historial cargado. Ve a Configuración → Rutas Históricas y carga al menos un CSV.",
        }

    # ── 2. Construir template VRP ─────────────────────────────────────────────
    n       = len(dfs)
    weights = [float(i + 1) for i in range(n)]
    template, kg_hist, route_stats = build_template_from_history(dfs, weights)

    # ── 3. Leer pedidos de la extracción ─────────────────────────────────────
    ext_doc = db["extraccion"].find_one({"logistica_id": oid})
    if not ext_doc:
        return {
            "status":  "error",
            "mensaje": "No hay datos de extracción para esta logística. Completa la sección Extracción primero.",
        }

    datos        = ext_doc.get("datos", {})
    pedidos_dict = {}
    for _, valores in datos.items():
        id_suc = valores.get("id_sucursal")
        peso   = float(valores.get("total_kg") or 0)
        if id_suc is not None and peso > 0:
            pedidos_dict[int(id_suc)] = peso

    if not pedidos_dict:
        return {"status": "error", "mensaje": "No hay sucursales con peso en la extracción."}

    # ── 4. Coordenadas y nombres de sucursales ────────────────────────────────
    coords_dict = {}
    suc_nombres = {}
    for suc in db["sucursales"].find({}):
        nt  = suc.get("num_tienda")
        lat = suc.get("latitud")
        lon = suc.get("longitud")
        if nt is not None and lat is not None and lon is not None:
            coords_dict[int(nt)] = (float(lat), float(lon))
            suc_nombres[int(nt)] = (
                suc.get("nombre_base")
                or suc.get("nombre_icg-proalmex")
                or suc.get("nombre_bimbo")
                or str(nt)
            )

    # ── 5. Capacidades de vehículos ───────────────────────────────────────────
    vehiculos_cap = obtener_capacidades_vehiculos()
    info_vehiculos = obtener_info_vehiculos()

    # ── 6. Ejecutar VRP ───────────────────────────────────────────────────────
    rows, report_rows, consol_log = generate_routes_vrp(
        pedidos_dict, coords_dict, template, kg_hist, route_stats, vehiculos_cap
    )

    if not rows:
        return {"status": "error", "mensaje": "No se generaron rutas. Verifica el historial y los pedidos."}

    # ── 7. Construir detalle_por_dia para asignaciones ────────────────────────
    grupos = defaultdict(list)
    for r in rows:
        grupos[(r["vehiculo"], r["dia_semana"])].append(r)

    detalle_por_dia = {}
    for (veh, dia), ruts in grupos.items():
        dia_key = _normalizar_dia(dia)
        if dia_key not in detalle_por_dia:
            detalle_por_dia[dia_key] = {}

        # ID sintético para la ruta VRP
        ruta_id   = f"vrp_{veh.replace(' ', '_').lower()}_{dia.lower()}"
        placas    = info_vehiculos.get(veh, {}).get("placas", "")
        total_kg  = sum(r["kg_entrega"] for r in ruts)
        cap_kg    = vehiculos_cap.get(veh, 3500)
        pct       = round(total_kg / cap_kg * 100, 1) if cap_kg > 0 else 0

        # Estado VRP para esta ruta
        vrp_estado = next(
            (rr["estado"] for rr in report_rows
             if rr["vehiculo"] == veh and rr["dia_semana"] == dia),
            "SIN_HIST"
        )

        sucursales = []
        for r in sorted(ruts, key=lambda x: x["secuencia_visita"]):
            nt = r["num_tienda"]
            sucursales.append({
                "num_tienda": nt,
                "nombre":     suc_nombres.get(nt, str(nt)),
                "orden":      r["secuencia_visita"],
                "peso_kg":    r["kg_entrega"],
            })

        detalle_por_dia[dia_key][ruta_id] = {
            "nombre_ruta":           f"{veh} — {dia.capitalize()}",
            "vehiculo_placas":       placas,
            "vehiculo_abreviatura":  veh,
            "capacidad_ton":         cap_kg / 1000,
            "peso_total_kg":         total_kg,
            "porcentaje_utilizacion": pct,
            "hora_salida":           "08:00",
            "hora_regreso_estimada": "",
            "cumple_horario":        True,
            "sucursales":            sucursales,
            "vrp_estado":            vrp_estado,
        }

    # ── 8. Guardar en colección asignaciones ──────────────────────────────────
    asig_doc = {
        "logistica_id":   oid,
        "detalle_por_dia": detalle_por_dia,
        "generado_por":   "vrp_historico",
        "n_historicos":   n,
        "guardado_en":    datetime.now().isoformat(),
    }
    db["asignaciones"].update_one(
        {"logistica_id": oid},
        {"$set": asig_doc},
        upsert=True,
    )

    # ── 9. Guardar reporte VRP en colección separada ──────────────────────────
    db["vrp_reportes"].update_one(
        {"logistica_id": oid},
        {"$set": {
            "logistica_id": oid,
            "reporte":      report_rows,
            "consolidaciones": consol_log,
            "generado_en":  datetime.now().isoformat(),
        }},
        upsert=True,
    )

    return {
        "status":         "ok",
        "total_rutas":    len(grupos),
        "total_sucursales": len(pedidos_dict),
        "n_historicos":   n,
        "reporte":        report_rows,
        "consolidaciones": consol_log,
    }


def obtener_reporte_vrp(logistica_id: str) -> dict:
    """Devuelve el último reporte VRP generado para la logística activa."""
    oid = _parse_oid(logistica_id)
    if not oid:
        return {}
    try:
        db  = get_db()
        doc = db["vrp_reportes"].find_one({"logistica_id": oid})
        if not doc:
            return {}
        doc.pop("_id", None)
        doc["logistica_id"] = str(doc["logistica_id"])
        return doc
    except Exception:
        return {}


# ── CSV Export ────────────────────────────────────────────────────────────────

def exportar_csv_rutas(rutas_list: list) -> str:
    """
    Exporta rutas al formato histórico CSV.

    rutas_list: list de dicts con campos:
        num_tienda (o id_sucursal), vehiculo, dia_semana, secuencia_visita, kg_entrega
    """
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["id_sucursal", "vehiculo", "dia_semana", "secuencia_visita", "kg_entrega"])

    for r in rutas_list:
        id_suc = r.get("id_sucursal") or r.get("num_tienda", "")
        writer.writerow([
            id_suc,
            r.get("vehiculo", ""),
            r.get("dia_semana", ""),
            r.get("secuencia_visita", ""),
            int(r.get("kg_entrega", 0)),
        ])

    return output.getvalue()


# ── Guardar rutas confirmadas en historial ────────────────────────────────────

def guardar_en_historico(logistica_id: str, nombre: str, rutas_list: list) -> dict:
    """
    Guarda rutas confirmadas como un nuevo documento en rutas_historicas.

    rutas_list: list de dicts con campos num_tienda/id_sucursal, vehiculo,
                dia_semana, secuencia_visita, kg_entrega
    """
    try:
        filas = []
        for r in rutas_list:
            filas.append({
                "id_sucursal":      r.get("id_sucursal") or r.get("num_tienda", 0),
                "vehiculo":         r.get("vehiculo", ""),
                "dia_semana":       str(r.get("dia_semana", "")).upper(),
                "secuencia_visita": int(r.get("secuencia_visita") or r.get("orden") or 1),
                "kg_entrega":       float(r.get("kg_entrega") or r.get("peso_kg") or 0),
            })

        if not filas:
            return {"status": "error", "mensaje": "No hay rutas para guardar"}

        if _PANDAS:
            df    = pd.DataFrame(filas)
            n_suc = int(df["id_sucursal"].nunique())
            n_rut = int(df.groupby(["vehiculo", "dia_semana"]).ngroups)
        else:
            n_suc = len(set(f["id_sucursal"] for f in filas))
            grupos = defaultdict(set)
            for f in filas:
                grupos[f["vehiculo"]].add(f["dia_semana"])
            n_rut = sum(len(v) for v in grupos.values())

        db = get_db()

        fecha_inicio = ""
        fecha_fin    = ""
        try:
            oid_log = _parse_oid(str(logistica_id))
            if oid_log:
                log_doc = db["logisticas"].find_one({"_id": oid_log})
                if log_doc:
                    fecha_inicio = log_doc.get("fecha_inicio", "")
                    fecha_fin    = log_doc.get("fecha_fin", "")
        except Exception:
            pass

        cargado_en = f"{fecha_inicio}T00:00:00" if fecha_inicio else datetime.now().isoformat()

        nombre_auto = _nombre_desde_fechas(fecha_inicio, fecha_fin)
        if nombre_auto:
            nombre = nombre_auto

        doc = {
            "nombre":        nombre,
            "filas":         filas,
            "n_sucursales":  n_suc,
            "n_rutas":       n_rut,
            "logistica_id":  str(logistica_id),
            "fecha_inicio":  fecha_inicio,
            "fecha_fin":     fecha_fin,
            "cargado_en":    cargado_en,
            "confirmada":    True,
        }
        # Upsert: si ya existe un historial confirmado para esta logística, actualizarlo
        res = db["rutas_historicas"].update_one(
            {"logistica_id": str(logistica_id), "confirmada": True},
            {"$set": doc},
            upsert=True,
        )
        doc_id = str(res.upserted_id) if res.upserted_id else ""
        return {"status": "ok", "id": doc_id}
    except Exception as e:
        return {"status": "error", "mensaje": str(e)}


# ── Resumen del historial ─────────────────────────────────────────────────────

def sugerir_vehiculos_optimos(routes_info: list) -> dict:
    """
    Sugiere el vehículo más rentable para cada ruta.

    Prioridad 1 — Histórico: se busca el vehículo que históricamente visitó
    un conjunto de sucursales con mayor solapamiento (Jaccard) ponderado
    por recencia (último CSV cargado = mayor peso).

    Prioridad 2 — Capacidad: si no hay histórico suficiente, se elige el
    vehículo cuya capacidad quede lo más cercana posible al 100 % de utilización
    sin superar el 120 %.

    routes_info: [{"id": str, "dia": str, "peso_kg": float, "sucursales": [int]}]
    Returns: { route_id: { "placas": str, "abrev": str, "fuente": "historico"|"capacidad"|"ninguno", "pct": float } }
    """
    from collections import defaultdict as _dd

    db = get_db()

    # ── 1. Construir grupos históricos por (vehiculo, dia) con su set de sucursales ──
    docs = list(db["rutas_historicas"].find({}))
    docs.sort(key=_sort_key_historico)  # más antiguo → más reciente por fecha real
    n    = len(docs)
    hist_groups = []  # [{sucursales: set, vehiculo: str, dia: str, weight: float}]
    for i, doc in enumerate(docs):
        weight = float(i + 1)  # recencia: más reciente → mayor peso
        filas  = doc.get("filas", [])
        grupos = _dd(set)
        for f in filas:
            key = (str(f.get("vehiculo", "")), _normalizar_dia(str(f.get("dia_semana", ""))))
            suc = f.get("id_sucursal")
            if suc is not None:
                grupos[key].add(int(suc))
        for (veh, dia), suc_set in grupos.items():
            if suc_set:
                hist_groups.append({"sucursales": suc_set, "vehiculo": veh, "dia": dia, "weight": weight})

    # ── 2. Información de vehículos ──────────────────────────────────────────────
    veh_info = {}        # abrev → {placas, capacidad_kg}
    all_vehiculos = []
    for v in db["vehiculos"].find({}):
        abrev   = v.get("abreviatura") or v.get("nombre_corto") or ""
        placas  = v.get("placas") or ""
        cap_ton = float(v.get("capacidad_toneladas") or 0)
        cap_kg  = cap_ton * 1000
        all_vehiculos.append({"placas": placas, "abrev": abrev, "capacidad_kg": cap_kg})
        if abrev and placas and cap_kg > 0:
            veh_info[abrev] = {"placas": placas, "capacidad_kg": cap_kg}

    # ── 3. Asignar por orden de peso descendente (más pesado = más restringido) ──
    result            = {}
    assigned_per_day  = _dd(set)  # dia → set de placas ya asignadas

    routes_sorted = sorted(routes_info, key=lambda r: float(r.get("peso_kg") or 0), reverse=True)

    for route in routes_sorted:
        route_id = str(route.get("id", ""))
        dia      = _normalizar_dia(str(route.get("dia") or ""))
        peso_kg  = float(route.get("peso_kg") or 0)
        suc_set  = set(int(s) for s in (route.get("sucursales") or []))

        # ── Paso A: búsqueda histórica (Jaccard + recencia) ──
        best_veh   = None
        best_score = -1.0

        for hg in hist_groups:
            if not suc_set or not hg["sucursales"]:
                continue
            inter    = len(suc_set & hg["sucursales"])
            union    = len(suc_set | hg["sucursales"])
            jaccard  = inter / union if union else 0.0
            if jaccard < 0.25:
                continue
            score = jaccard * hg["weight"]
            if score <= best_score:
                continue
            vi = veh_info.get(hg["vehiculo"])
            if not vi:
                continue
            cap_kg = vi["capacidad_kg"]
            if cap_kg <= 0 or peso_kg > cap_kg * 1.20:
                continue
            if vi["placas"] in assigned_per_day[dia]:
                continue
            best_score = score
            best_veh   = {"placas": vi["placas"], "abrev": hg["vehiculo"], "capacidad_kg": cap_kg}

        if best_veh:
            placas = best_veh["placas"]
            assigned_per_day[dia].add(placas)
            pct = round(peso_kg / best_veh["capacidad_kg"] * 100, 1) if best_veh["capacidad_kg"] > 0 else 0
            result[route_id] = {"placas": placas, "abrev": best_veh["abrev"], "fuente": "historico", "pct": pct}
            continue

        # ── Paso B: más cercano al 100 % de capacidad ──────────────────────────
        disponibles = [
            v for v in all_vehiculos
            if v["placas"] and v["capacidad_kg"] > 0
            and v["placas"] not in assigned_per_day[dia]
        ]

        def _score(v):
            pct_v = peso_kg / v["capacidad_kg"] * 100
            if pct_v <= 120:
                return abs(pct_v - 100)
            return 200 + (pct_v - 120)  # penalizar sobrecarga

        if disponibles:
            best_v = min(disponibles, key=_score)
            placas = best_v["placas"]
            pct    = round(peso_kg / best_v["capacidad_kg"] * 100, 1)
            assigned_per_day[dia].add(placas)
            result[route_id] = {"placas": placas, "abrev": best_v["abrev"], "fuente": "capacidad", "pct": pct}
        else:
            result[route_id] = {"placas": "", "abrev": "", "fuente": "ninguno", "pct": 0}

    return result


def resumen_historial() -> dict:
    """Estadísticas rápidas del historial cargado."""
    dfs = obtener_historicos_como_dfs()
    if not dfs:
        return {"n_ejemplos": 0, "n_sucursales": 0, "n_rutas": 0}

    if not _PANDAS:
        return {"n_ejemplos": len(dfs)}

    try:
        n        = len(dfs)
        weights  = [float(i + 1) for i in range(n)]
        tmpl, _, route_stats = build_template_from_history(dfs, weights)
        return {
            "n_ejemplos":   n,
            "n_sucursales": len(tmpl),
            "n_rutas":      len(route_stats),
        }
    except Exception:
        return {"n_ejemplos": len(dfs), "n_sucursales": 0, "n_rutas": 0}


# ── Geometrías OSRM para visualización en mapa ────────────────────────────────

def _geo_cache_key(coords: list) -> str:
    return ";".join(f"{lat:.5f},{lon:.5f}" for lat, lon in coords)


def _haversine_total_km(pts: list) -> float:
    total = 0.0
    for i in range(len(pts) - 1):
        lat1, lon1 = pts[i]
        lat2, lon2 = pts[i + 1]
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = (math.sin(dlat / 2) ** 2
             + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
             * math.sin(dlon / 2) ** 2)
        total += 6371.0 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return round(total, 2)


def _consultar_osrm_geometria(wp: list, db) -> tuple:
    """
    Devuelve (polyline, distancia_km, duracion_min, origen, from_cache).
    Flujo: caché → OSRM (hasta _OSRM_MAX_RETRIES intentos) → Haversine solo si todo falla.
    Solo persiste en caché resultados reales de OSRM (nunca Haversine).
    """
    clave = _geo_cache_key(wp)

    # 1. Revisar caché primero
    try:
        doc = db["cache_osrm"].find_one({"clave": clave, "tipo": "geometria"})
        if doc:
            c = doc["resultado"]
            return (c.get("polyline", []), c.get("distancia_km", 0.0),
                    c.get("duracion_min", 0.0), c.get("origen", "osrm"), True)
    except Exception:
        pass

    # 2. Consultar OSRM con reintentos
    waypoints_str = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in wp)
    url           = f"{_OSRM_BASE}/{waypoints_str}?overview=full&geometries=geojson"
    polyline      = []
    distancia_km  = 0.0
    duracion_min  = 0.0
    origen        = "sin_datos"
    ultimo_error  = None

    for intento in range(_OSRM_MAX_RETRIES):
        try:
            if intento > 0:
                time.sleep(_OSRM_RETRY_DELAY)
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "ICG-HistMapViewer/1.0", "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=_OSRM_TIMEOUT) as resp:
                data = _json.loads(resp.read().decode("utf-8"))

            if data.get("code") == "Ok" and data.get("routes"):
                route        = data["routes"][0]
                geom         = route.get("geometry", {})
                polyline     = [[c[1], c[0]] for c in geom.get("coordinates", [])]
                distancia_km = round(route.get("distance", 0.0) / 1000, 2)
                duracion_min = round(route.get("duration", 0.0) / 60, 1)
                origen       = "osrm"
                break
            else:
                ultimo_error = f"OSRM code={data.get('code')}"
        except urllib.error.HTTPError as e:
            ultimo_error = f"HTTP {e.code}"
            if e.code == 429:
                time.sleep(_OSRM_RETRY_DELAY * 2)
        except Exception as e:
            ultimo_error = str(e)
    else:
        # 3. Haversine solo como último recurso — no se guarda en caché
        print(f"[_consultar_osrm_geometria] OSRM agotó reintentos ({ultimo_error}); usando fallback lineal")
        polyline     = [[lat, lon] for lat, lon in wp]
        distancia_km = _haversine_total_km(wp)
        duracion_min = round(distancia_km / 35.0 * 60, 1)
        origen       = "fallback_lineal"

    # Solo guardar en caché resultados reales de OSRM
    if origen == "osrm" and polyline:
        try:
            db["cache_osrm"].update_one(
                {"clave": clave, "tipo": "geometria"},
                {"$set": {
                    "resultado": {
                        "polyline":     polyline,
                        "distancia_km": distancia_km,
                        "duracion_min": duracion_min,
                        "origen":       origen,
                    },
                    "actualizado_en": datetime.now().isoformat(),
                }},
                upsert=True,
            )
        except Exception:
            pass

    return polyline, distancia_km, duracion_min, origen, False


def obtener_geometrias_historico(hist_id: str) -> dict:
    """
    Devuelve geometrías OSRM para todas las rutas de un historial.
    Agrupa las filas por (vehiculo, dia_semana), construye waypoints
    depósito→paradas→depósito y consulta OSRM con caché en cache_osrm.
    """
    oid = _parse_oid(hist_id)
    if not oid:
        return {"status": "error", "mensaje": "ID inválido"}

    try:
        db  = get_db()
        doc = db["rutas_historicas"].find_one({"_id": oid})
        if not doc:
            return {"status": "error", "mensaje": "Historial no encontrado"}

        filas = doc.get("filas", [])
        if not filas:
            return {"status": "ok", "rutas": [], "nombre": doc.get("nombre", "")}

        # Cargar coordenadas y nombres de sucursales
        coords_dict: dict = {}
        suc_nombres: dict = {}
        for suc in db["sucursales"].find({}):
            nt  = suc.get("num_tienda")
            lat = suc.get("latitud")
            lon = suc.get("longitud")
            if nt is None or lat is None or lon is None:
                continue
            try:
                coords_dict[int(nt)] = (float(lat), float(lon))
                suc_nombres[int(nt)] = (
                    suc.get("nombre_base")
                    or suc.get("nombre_icg-proalmex")
                    or suc.get("nombre_bimbo")
                    or str(nt)
                )
            except (ValueError, TypeError):
                pass

        # Agrupar filas por (vehiculo, dia_semana)
        grupos: dict = defaultdict(list)
        for f in filas:
            key = (str(f.get("vehiculo", "")), str(f.get("dia_semana", "")).upper())
            grupos[key].append(f)

        rutas = []
        for (vehiculo, dia), filas_grupo in sorted(grupos.items()):
            filas_sorted = sorted(filas_grupo, key=lambda x: int(x.get("secuencia_visita", 0)))

            paradas = []
            for f in filas_sorted:
                try:
                    nt = int(f.get("id_sucursal", 0))
                except (ValueError, TypeError):
                    continue
                coord = coords_dict.get(nt)
                if not coord:
                    continue
                paradas.append({
                    "num_tienda": nt,
                    "nombre":     suc_nombres.get(nt, str(nt)),
                    "lat":        coord[0],
                    "lon":        coord[1],
                    "orden":      int(f.get("secuencia_visita", 0)),
                    "kg_entrega": float(f.get("kg_entrega", 0)),
                })

            if not paradas:
                continue

            wp = ([(_MATRIZ_LAT, _MATRIZ_LON)]
                  + [(p["lat"], p["lon"]) for p in paradas]
                  + [(_MATRIZ_LAT, _MATRIZ_LON)])

            polyline, distancia_km, duracion_min, origen, _ = _consultar_osrm_geometria(wp, db)

            ruta_id = f"{vehiculo.replace(' ', '_').lower()}_{dia.lower()}"
            rutas.append({
                "id":           ruta_id,
                "nombre":       f"{vehiculo} — {dia.capitalize()}",
                "vehiculo":     vehiculo,
                "dia":          dia,
                "paradas":      [{
                    "tipo":   "depot",
                    "nombre": "Depósito (salida/regreso)",
                    "lat":    _MATRIZ_LAT,
                    "lon":    _MATRIZ_LON,
                    "orden":  0,
                }] + paradas,
                "polyline":     polyline,
                "distancia_km": distancia_km,
                "duracion_min": duracion_min,
                "origen":       origen,
            })

        return {"status": "ok", "rutas": rutas, "nombre": doc.get("nombre", "")}

    except Exception as e:
        return {"status": "error", "mensaje": str(e)}


def stream_geometrias_historico(hist_id: str):
    """
    Generador SSE: emite eventos de progreso mientras calcula geometrías OSRM.

    Eventos emitidos (cada uno como "data: JSON\\n\\n"):
      {"type":"start",  "total":N, "nombre":str}
      {"type":"ruta",   "idx":K, "total":N, "ruta":{...}, "from_cache":bool,
                        "elapsed_ms":int, "eta_ms":int}
      {"type":"done",   "total":N, "n_rutas":K, "elapsed_ms":int}
      {"type":"error",  "mensaje":str}
    """
    import time as _time

    def _ev(obj):
        return f"data: {_json.dumps(obj, ensure_ascii=False)}\n\n"

    oid = _parse_oid(hist_id)
    if not oid:
        yield _ev({"type": "error", "mensaje": "ID inválido"})
        return

    try:
        db  = get_db()
        doc = db["rutas_historicas"].find_one({"_id": oid})
        if not doc:
            yield _ev({"type": "error", "mensaje": "Historial no encontrado"})
            return

        filas = doc.get("filas", [])
        if not filas:
            yield _ev({"type": "done", "total": 0, "n_rutas": 0, "elapsed_ms": 0})
            return

        # Cargar coordenadas de sucursales
        coords_dict: dict = {}
        suc_nombres: dict = {}
        for suc in db["sucursales"].find({}):
            nt  = suc.get("num_tienda")
            lat = suc.get("latitud")
            lon = suc.get("longitud")
            if nt is None or lat is None or lon is None:
                continue
            try:
                coords_dict[int(nt)] = (float(lat), float(lon))
                suc_nombres[int(nt)] = (
                    suc.get("nombre_base")
                    or suc.get("nombre_icg-proalmex")
                    or suc.get("nombre_bimbo")
                    or str(nt)
                )
            except (ValueError, TypeError):
                pass

        # Agrupar por (vehiculo, dia_semana)
        grupos: dict = defaultdict(list)
        for f in filas:
            key = (str(f.get("vehiculo", "")), str(f.get("dia_semana", "")).upper())
            grupos[key].append(f)

        grupos_list = sorted(grupos.items())
        total = len(grupos_list)

        yield _ev({"type": "start", "total": total, "nombre": doc.get("nombre", "")})

        t_global = _time.time()
        osrm_times_ms = []   # tiempos de rutas NO cacheadas (para ETA real)
        n_rutas = 0

        for idx, ((vehiculo, dia), filas_grupo) in enumerate(grupos_list):
            t_ruta = _time.time()

            filas_sorted = sorted(filas_grupo, key=lambda x: int(x.get("secuencia_visita", 0)))
            paradas = []
            for f in filas_sorted:
                try:
                    nt = int(f.get("id_sucursal", 0))
                except (ValueError, TypeError):
                    continue
                coord = coords_dict.get(nt)
                if not coord:
                    continue
                paradas.append({
                    "num_tienda": nt,
                    "nombre":     suc_nombres.get(nt, str(nt)),
                    "lat":        coord[0],
                    "lon":        coord[1],
                    "orden":      int(f.get("secuencia_visita", 0)),
                    "kg_entrega": float(f.get("kg_entrega", 0)),
                })

            if not paradas:
                continue

            wp = ([(_MATRIZ_LAT, _MATRIZ_LON)]
                  + [(p["lat"], p["lon"]) for p in paradas]
                  + [(_MATRIZ_LAT, _MATRIZ_LON)])

            polyline, distancia_km, duracion_min, origen, from_cache = \
                _consultar_osrm_geometria(wp, db)

            t_ruta_ms = int((_time.time() - t_ruta) * 1000)
            if not from_cache:
                osrm_times_ms.append(t_ruta_ms)

            elapsed_ms = int((_time.time() - t_global) * 1000)
            remaining  = total - (idx + 1)

            # ETA: basado en el promedio de rutas que fueron a OSRM (las lentas)
            if osrm_times_ms:
                avg_osrm = sum(osrm_times_ms) / len(osrm_times_ms)
                eta_ms   = int(avg_osrm * remaining)
            else:
                eta_ms = 0  # todo desde caché, va a ser muy rápido

            ruta_id = f"{vehiculo.replace(' ', '_').lower()}_{dia.lower()}"
            ruta = {
                "id":           ruta_id,
                "nombre":       f"{vehiculo} — {dia.capitalize()}",
                "vehiculo":     vehiculo,
                "dia":          dia,
                "paradas":      [{
                    "tipo":   "depot",
                    "nombre": "Depósito (salida/regreso)",
                    "lat":    _MATRIZ_LAT,
                    "lon":    _MATRIZ_LON,
                    "orden":  0,
                }] + paradas,
                "polyline":     polyline,
                "distancia_km": distancia_km,
                "duracion_min": duracion_min,
                "origen":       origen,
            }
            n_rutas += 1

            yield _ev({
                "type":       "ruta",
                "idx":        idx + 1,
                "total":      total,
                "ruta":       ruta,
                "from_cache": from_cache,
                "elapsed_ms": elapsed_ms,
                "eta_ms":     eta_ms,
            })

        total_elapsed_ms = int((_time.time() - t_global) * 1000)
        yield _ev({"type": "done", "total": total, "n_rutas": n_rutas,
                   "elapsed_ms": total_elapsed_ms})

    except Exception as e:
        yield _ev({"type": "error", "mensaje": str(e)})
