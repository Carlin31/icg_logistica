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
    capacidad_efectiva_kg,
    DIA_ORDEN,
    ordenar_paradas_por_historico,
)

# Algoritmo de afinidad histórica — motor VRP activo en producción.
from logic.vrp_afinidad.afinidad import construir_afinidad
from logic.vrp_afinidad.clarke_wright import haversine as _haversine_cw, Ruta as _Ruta_CW
from logic.vrp_afinidad.estado_vrp import calcular_estado as _calcular_estado_cw

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


_DIA_ORDEN = ["LUNES", "MARTES", "MIERCOLES", "MIÉRCOLES", "JUEVES", "VIERNES", "SABADO", "SÁBADO", "DOMINGO"]


def _dias_desde_filas(filas: list) -> list:
    """Devuelve lista ordenada de días únicos presentes en las filas."""
    raw = set(str(f.get("dia_semana") or "").strip().upper() for f in filas if f.get("dia_semana"))
    raw.discard("")
    return sorted(raw, key=lambda d: _DIA_ORDEN.index(d) if d in _DIA_ORDEN else 99)


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

def listar_rutas_historicas(tipo_registro: str | None = None) -> list:
    """
    Lista todos los historiales cargados en orden de peso ascendente
    (más antiguo = peso 1, confirmadas recientes = peso n).
    Mismo criterio que el VRP para que el número mostrado en la UI sea fiel al peso real.
    """
    try:
        db   = get_db()
        filtro = {"tipo_registro": tipo_registro} if tipo_registro else {"$or": [{"tipo_registro": {"$exists": False}}, {"tipo_registro": {"$ne": "mayoristas"}}]}
        docs = list(db["rutas_historicas"].find(filtro, {"filas": 0}))
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
        dias    = _dias_desde_filas(filas)

        db  = get_db()
        doc = {
            "nombre":       nombre,
            "filas":        filas,
            "n_sucursales": n_suc,
            "n_rutas":      n_rut,
            "dias":         dias,
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


def obtener_historicos_como_dfs(tipo_registro: str | None = None) -> list:
    """
    Devuelve lista de DataFrames (pandas) de todos los historiales cargados,
    ordenados del más antiguo al más reciente por fecha efectiva de la logística
    (fecha_inicio para rutas confirmadas, cargado_en para CSV importados).
    """
    if not _PANDAS:
        return []
    try:
        db   = get_db()
        filtro = {"tipo_registro": tipo_registro} if tipo_registro else {"$or": [{"tipo_registro": {"$exists": False}}, {"tipo_registro": {"$ne": "mayoristas"}}]}
        docs = list(db["rutas_historicas"].find(filtro))
        docs.sort(key=_sort_key_historico)
        dfs  = []
        for doc in docs:
            filas = [f for f in doc.get("filas", []) if f.get("tipo") != "mayorista"]
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
    template, kg_hist, route_stats, historial_rutas = build_template_from_history(dfs, weights)

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
        if id_suc is None or peso <= 0:
            continue
        try:
            pedidos_dict[int(id_suc)] = peso
        except (TypeError, ValueError):
            # id_sucursal no numérico (ej. 'N/A' cuando el nombre de la
            # sucursal no se pudo mapear) → se descarta esta fila
            continue

    if not pedidos_dict:
        return {"status": "error", "mensaje": "No hay sucursales con peso en la extracción."}

    volumenes_dict: dict = {}
    for _, valores in ext_doc.get("datos_volumen", {}).items():
        id_suc = valores.get("id_sucursal")
        vol    = float(valores.get("total_m3") or 0)
        if id_suc is not None:
            try:
                volumenes_dict[int(id_suc)] = vol
            except (TypeError, ValueError):
                pass

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
        pedidos_dict, coords_dict, template, kg_hist, route_stats, vehiculos_cap, historial_rutas
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


# ── VRP Clarke-Wright + afinidad histórica — motor activo ─────────────────────
#
# Reemplaza a generar_rutas_vrp(): combina distancia real entre sucursales con
# afinidad histórica ponderada por recencia (savings = distancia + λ × afinidad),
# en vez del voto rígido por sucursal de build_template_from_history().
# Escribe en `asignaciones` y `vrp_reportes` (colecciones de producción) y
# también en las colecciones _preview para poder comparar con el algoritmo anterior.

def _historiales_crudos_sucursales() -> list:
    """Documentos crudos de rutas_historicas (solo filas de sucursal) para construir_afinidad()."""
    db = get_db()
    filtro = {"$or": [{"tipo_registro": {"$exists": False}}, {"tipo_registro": {"$ne": "mayoristas"}}]}
    docs = list(db["rutas_historicas"].find(filtro))
    for doc in docs:
        doc["filas"] = [f for f in doc.get("filas", []) if f.get("tipo") != "mayorista"]
    return [d for d in docs if d["filas"]]


def _dia_preferido_por_nodo(pedidos_dict: dict, coords_dict: dict, pref_vehiculo_dia: dict) -> dict:
    """
    Determina el día de entrega de cada sucursal del pedido actual:
      1. Si tiene preferencia histórica (afinidad) → se usa esa.
      2. Si es una sucursal nueva → hereda el día del nodo histórico
         geográficamente más cercano (mismo criterio de fallback que
         generate_routes_vrp en vrp_logic.py).
    """
    nodos_con_pref = [n for n in pref_vehiculo_dia if n in coords_dict]
    dias = {}
    for sid in pedidos_dict:
        if sid in pref_vehiculo_dia:
            dias[sid] = pref_vehiculo_dia[sid][1]
            continue
        if sid in coords_dict and nodos_con_pref:
            lat, lon = coords_dict[sid]
            near = min(
                nodos_con_pref,
                key=lambda k: _haversine_cw(lat, lon, coords_dict[k][0], coords_dict[k][1]),
            )
            dias[sid] = pref_vehiculo_dia[near][1]
        else:
            dias[sid] = "LUNES"
    return dias


def _extraer_secuencias_historicas(historiales: list) -> dict:
    """
    Extrae la secuencia mediana de visita por sucursal y combinación (vehiculo, dia).
    Retorna: {sid: {(veh, dia): seq_mediana}}
    """
    seq_records: dict = defaultdict(lambda: defaultdict(list))
    for hist in historiales:
        for fila in hist.get("filas", []):
            try:
                sid = int(fila["id_sucursal"])
                veh = str(fila.get("vehiculo", "")).strip()
                dia = str(fila.get("dia_semana", "")).strip().upper()
                seq = int(fila.get("secuencia_visita", 999))
                if veh and dia:
                    seq_records[sid][(veh, dia)].append(seq)
            except (KeyError, ValueError, TypeError):
                continue
    result = {}
    for sid, vd_seqs in seq_records.items():
        result[sid] = {}
        for (veh, dia), seqs in vd_seqs.items():
            result[sid][(veh, dia)] = int(round(sum(seqs) / len(seqs)))
    return result


# Umbrales para decidir si una ruta histórica "se parece lo suficiente" al
# pedido actual como para copiarse tal cual, en vez de pasar por el algoritmo
# de afinidad. Ambos deben cumplirse (cobertura Y similitud de peso).
UMBRAL_COPIA_COBERTURA = 0.85  # ≥85 % de las sucursales de esa ruta deben estar en el pedido actual
UMBRAL_COPIA_PESO      = 0.85  # similitud de peso total ≥85 % (desviación ≤15 %)


def _detectar_copias_exactas(historiales: list, pedidos_dict: dict) -> dict:
    """
    Verificación exacta por ruta: busca, entre todas las instancias históricas
    (cada combinación vehículo+día de cada semana/CSV cargado), aquellas cuyo
    conjunto de sucursales y peso total se parezcan lo suficiente al pedido
    actual como para copiarlas tal como fueron realizadas — mismas
    sucursales, mismo vehículo/día, mismo orden de visita — en vez de pasar
    por el algoritmo de afinidad.

    "Se parecen lo suficiente" si, comparando solo las sucursales de esa
    ruta histórica que también están en el pedido actual:
      - cobertura      = (sucursales de la ruta histórica presentes en el
                          pedido actual) / (total de sucursales de esa ruta) ≥ 85 %.
      - similitud_peso = promedio, por CADA sucursal de esa intersección, de
                          1 − |peso_pedido_sid − peso_histórico_sid| / max(ambos) ≥ 85 %.
                          Se compara sucursal por sucursal (no el total de la
                          ruta) para que errores en sentido contrario entre
                          distintas sucursales no se compensen entre sí y
                          aparenten una coincidencia que en realidad no existe.

    Si el pedido actual se desvía más que eso de cualquier ruta histórica,
    esas sucursales NO se copian y se resuelven con el algoritmo de afinidad
    normal (votación + co-ocurrencia + resolución de sobrecarga).

    Cuando varias instancias históricas compiten por las mismas sucursales,
    gana la de mejor coincidencia (cobertura × similitud de peso); una
    sucursal ya copiada no se reutiliza en otra coincidencia más débil.

    Retorna: {(veh, dia): [{"sid": int, "seq": int}, …]} en el orden
    histórico original, listo para sembrarse directamente como ruta.
    """
    instancias: list = []
    for hist in historiales:
        grupos: dict = defaultdict(list)
        for fila in hist.get("filas", []):
            try:
                sid = int(fila["id_sucursal"])
                veh = str(fila.get("vehiculo", "")).strip()
                dia = str(fila.get("dia_semana", "")).strip().upper()
                kg  = float(fila.get("kg_entrega", 0) or 0)
                seq = int(fila.get("secuencia_visita", 999) or 999)
            except (KeyError, ValueError, TypeError):
                continue
            if not veh or not dia:
                continue
            grupos[(veh, dia)].append((sid, kg, seq))
        for (veh, dia), filas_ruta in grupos.items():
            instancias.append({"veh": veh, "dia": dia, "filas": filas_ruta})

    candidatos = []
    for inst in instancias:
        sids_hist = {f[0] for f in inst["filas"]}
        if not sids_hist:
            continue
        sids_en_pedido = {sid for sid in sids_hist if sid in pedidos_dict}
        if not sids_en_pedido:
            continue

        cobertura = len(sids_en_pedido) / len(sids_hist)
        if cobertura < UMBRAL_COPIA_COBERTURA:
            continue

        kg_hist_por_sid = {sid: kg for sid, kg, _ in inst["filas"] if sid in sids_en_pedido}
        similitudes = []
        for sid in sids_en_pedido:
            kg_hist = kg_hist_por_sid.get(sid, 0.0)
            kg_actual = pedidos_dict[sid]
            tope = max(kg_hist, kg_actual)
            if tope <= 0:
                continue
            similitudes.append(1.0 - abs(kg_actual - kg_hist) / tope)
        if not similitudes:
            continue

        similitud_peso = sum(similitudes) / len(similitudes)
        if similitud_peso < UMBRAL_COPIA_PESO:
            continue

        score = cobertura * similitud_peso
        candidatos.append((score, inst, sids_en_pedido))

    candidatos.sort(key=lambda c: -c[0])  # mejor coincidencia primero

    copiadas: dict = {}
    sids_usados: set = set()
    for _score, inst, sids_en_pedido in candidatos:
        disponibles = sids_en_pedido - sids_usados
        if not disponibles:
            continue
        miembros = sorted(
            ({"sid": sid, "seq": seq} for sid, _kg, seq in inst["filas"] if sid in disponibles),
            key=lambda m: m["seq"],
        )
        if not miembros:
            continue
        clave = (inst["veh"], inst["dia"])
        copiadas.setdefault(clave, []).extend(miembros)
        sids_usados.update(disponibles)

    return copiadas


def _afinidad_con_ruta(sid: int, miembros: list, afinidad_norm: dict) -> float:
    """Suma de afinidades del nodo sid con todos los miembros de una ruta."""
    total = 0.0
    for m in miembros:
        if m["sid"] == sid:
            continue
        par = (min(sid, m["sid"]), max(sid, m["sid"]))
        total += afinidad_norm.get(par, 0.0)
    return total


def _coocurrencia_valida(sid: int, miembros: list, afinidad_raw: dict, nodos_hist: set) -> bool:
    """
    Restricción dura: sid solo puede ir en una ruta si ha aparecido junto a
    CADA uno de sus miembros históricos al menos una vez en el historial.

    Si sid o el miembro no están en el historial (sucursal nueva), la
    restricción no aplica — no hay datos para evaluarla.
    """
    if sid not in nodos_hist:
        return True  # sucursal nueva: sin restricción
    for m in miembros:
        if m["sid"] == sid or m["sid"] not in nodos_hist:
            continue
        par = (min(sid, m["sid"]), max(sid, m["sid"]))
        if afinidad_raw.get(par, 0.0) == 0.0:
            return False  # nunca aparecieron juntas
    return True


_UMBRAL_GEO_INDICIO_KM = 100.0


def _centroide_grupo(miembros: list, coords_dict: dict) -> tuple | None:
    pts = [coords_dict[m["sid"]] for m in miembros if m["sid"] in coords_dict]
    if not pts:
        return None
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def _elegir_destino_por_peso(
    sid: int,
    kg: float,
    grupos: dict,
    pedidos_dict: dict,
    vehiculos_cap: dict,
    afinidad_raw: dict,
    nodos_hist: set,
    coords_dict: dict,
    excluir: tuple,
) -> tuple | None:
    """
    Elige el mejor destino (vehiculo, dia) para `sid`, priorizando el peso:
    entre los destinos válidos, se prefiere el que deje la utilización más
    cercana al 100 % sin exceder la capacidad del vehículo.

    La sobrecarga NUNCA debe quedar sin resolver mientras exista capacidad
    disponible en alguna otra ruta. Se evalúa en tres niveles, en orden:
      Nivel 1 (estricto):   co-ocurrencia histórica con TODOS los miembros del destino.
      Nivel 2 (relajado):   basta un indicio mínimo — afinidad > 0 con algún
                            miembro del destino, o que su centro geográfico
                            quede a menos de 100 km de `sid`.
      Nivel 3 (proximidad pura, solo si 1 y 2 no encuentran nada): cualquier
                            destino con capacidad disponible, elegido por
                            cercanía geográfica real (o, si el destino está
                            vacío y no tiene un punto de referencia, por la
                            distancia a la matriz/depósito).

    Prioriza el mismo día; si no hay nada válido ese día, prueba otros días.
    Solo devuelve None si NINGUNA ruta del sistema tiene capacidad disponible
    para recibir a `sid` (caso límite: flota completa sin espacio).
    """
    dia_actual = excluir[1]
    sid_coords = coords_dict.get(sid)

    def _indicio_minimo(alt_mems: list) -> bool:
        for m in alt_mems:
            par = (min(sid, m["sid"]), max(sid, m["sid"]))
            if afinidad_raw.get(par, 0.0) > 0.0:
                return True
        if sid_coords:
            centro = _centroide_grupo(alt_mems, coords_dict)
            if centro and _haversine_cw(sid_coords[0], sid_coords[1], centro[0], centro[1]) <= _UMBRAL_GEO_INDICIO_KM:
                return True
        return False

    for nivel_estricto in (True, False):
        mejor_dest, mejor_pct = None, -1.0
        for mismo_dia_primero in (True, False):
            for (alt_veh, alt_dia) in grupos:
                if (alt_veh, alt_dia) == excluir:
                    continue
                if mismo_dia_primero and alt_dia != dia_actual:
                    continue
                alt_mems = grupos[(alt_veh, alt_dia)]
                if not alt_mems:
                    continue
                cap_alt = vehiculos_cap.get(alt_veh, 3500)
                if cap_alt <= 0:
                    continue
                nuevo_total = sum(pedidos_dict.get(m["sid"], 0) for m in alt_mems) + kg
                if nuevo_total > cap_alt:
                    continue

                if nivel_estricto:
                    if not _coocurrencia_valida(sid, alt_mems, afinidad_raw, nodos_hist):
                        continue
                elif not _indicio_minimo(alt_mems):
                    continue

                pct = nuevo_total / cap_alt  # qué tan cerca queda del 100 % de uso
                if pct > mejor_pct:
                    mejor_pct = pct
                    mejor_dest = (alt_veh, alt_dia)
            if mejor_dest:
                break
        if mejor_dest:
            return mejor_dest

    # Nivel 3 — proximidad pura: ninguna ruta tuvo indicio histórico/geográfico
    # razonable, pero la sobrecarga debe resolverse igual si hay capacidad en
    # cualquier otra ruta. Se elige la más cercana geográficamente.
    mejor_dest, mejor_dist = None, float("inf")
    for (alt_veh, alt_dia) in grupos:
        if (alt_veh, alt_dia) == excluir:
            continue
        alt_mems = grupos[(alt_veh, alt_dia)]
        cap_alt = vehiculos_cap.get(alt_veh, 3500)
        if cap_alt <= 0:
            continue
        nuevo_total = sum(pedidos_dict.get(m["sid"], 0) for m in alt_mems) + kg
        if nuevo_total > cap_alt:
            continue

        centro = _centroide_grupo(alt_mems, coords_dict) if alt_mems else None
        if sid_coords and centro:
            dist = _haversine_cw(sid_coords[0], sid_coords[1], centro[0], centro[1])
        elif sid_coords:
            dist = _haversine_cw(sid_coords[0], sid_coords[1], _MATRIZ_LAT, _MATRIZ_LON)
        else:
            dist = 0.0
        if dist < mejor_dist:
            mejor_dist = dist
            mejor_dest = (alt_veh, alt_dia)

    return mejor_dest


def _resolver_sobrecarga_con_afinidad(
    groups: dict,
    pedidos_dict: dict,
    vehiculos_cap: dict,
    afinidad_norm: dict,
    afinidad_raw: dict,
    nodos_hist: set,
    coords_dict: dict | None = None,
    max_iter: int = 200,
) -> dict:
    """
    Resuelve rutas con sobrecarga moviendo sucursales hacia una ruta
    alternativa, priorizando SIEMPRE el peso: entre los destinos válidos se
    elige el que deje la utilización más cercana al 100 % sin exceder la
    capacidad (ver _elegir_destino_por_peso para los dos niveles de validación
    histórica — estricto y, si no hay nada, el indicio mínimo relajado).

    Se prueba candidata por candidata, empezando por la de MENOR afinidad con
    el resto de su ruta, hasta encontrar una con destino válido disponible.
    Si ninguna sucursal de la ruta tiene un destino válido, la ruta permanece
    sobrecargada (no se fuerza un movimiento sin ningún indicio histórico).
    """
    coords_dict = coords_dict or {}
    for _ in range(max_iter):
        cambio = False
        for (veh, dia) in list(groups.keys()):
            miembros = groups[(veh, dia)]
            if not miembros:
                continue
            total_kg = sum(pedidos_dict.get(m["sid"], 0) for m in miembros)
            cap = vehiculos_cap.get(veh, 3500)
            if total_kg <= cap or len(miembros) <= 1:
                continue

            candidatas = sorted(
                miembros,
                key=lambda m: _afinidad_con_ruta(m["sid"], miembros, afinidad_norm),
            )

            for cand in candidatas:
                cand_kg = pedidos_dict.get(cand["sid"], 0)
                mejor_dest = _elegir_destino_por_peso(
                    cand["sid"], cand_kg, groups, pedidos_dict, vehiculos_cap,
                    afinidad_raw, nodos_hist, coords_dict, excluir=(veh, dia),
                )

                if mejor_dest:
                    miembros.remove(cand)
                    cand["seq"] = 999
                    groups[mejor_dest].append(cand)
                    cambio = True
                    break

        if not cambio:
            break

    return {k: v for k, v in groups.items() if v}


def _detectar_historicamente_solos(
    historiales: list,
    umbral_solo: float = 0.5,
    min_apariciones_solo: int | None = None,
) -> set:
    """
    Identifica sucursales que históricamente realizan la ruta en solitario.

    Una sucursal se considera "históricamente sola" si:
    - Apareció como única parada de su (vehiculo, dia) al menos
      `min_apariciones_solo` veces.
    - Esa fracción representa >= `umbral_solo` de todas sus apariciones.

    El doble criterio (frecuencia absoluta + porcentaje) evita marcar como
    "sola" a una sucursal que solo apareció una o dos veces en el historial
    y casualmente no tenía compañía.

    Si `min_apariciones_solo` no se especifica, se usa `min(2, len(historiales))`:
    con un único historial cargado, exigir 2 confirmaciones es imposible de
    cumplir y rompería la reproducción fiel de ese único ejemplo (toda ruta
    solitaria del historial se fusionaría a otra al generar). Con 1 solo
    historial, 1 aparición basta para considerarla "histórica".

    Retorna: set de id_sucursal que deben mantenerse en ruta individual.
    """
    if min_apariciones_solo is None:
        min_apariciones_solo = min(2, len(historiales)) or 1

    apariciones: dict      = defaultdict(int)
    apariciones_solo: dict = defaultdict(int)

    for hist in historiales:
        filas = hist.get("filas", [])
        por_vd: dict = defaultdict(list)
        for fila in filas:
            try:
                sid = int(fila["id_sucursal"])
                veh = str(fila.get("vehiculo", "")).strip()
                dia = str(fila.get("dia_semana", "")).strip().upper()
                if veh and dia:
                    por_vd[(veh, dia)].append(sid)
                    apariciones[sid] += 1
            except (KeyError, ValueError, TypeError):
                continue

        for _key, miembros in por_vd.items():
            ids_uniq = list(dict.fromkeys(miembros))
            if len(ids_uniq) == 1:
                apariciones_solo[ids_uniq[0]] += 1

    solos: set = set()
    for sid, total in apariciones.items():
        n_solo = apariciones_solo.get(sid, 0)
        if n_solo >= min_apariciones_solo and (n_solo / total) >= umbral_solo:
            solos.add(sid)
    return solos


def _consolidar_aisladas(
    groups: dict,
    pedidos_dict: dict,
    vehiculos_cap: dict,
    afinidad_norm: dict,
    afinidad_raw: dict,
    nodos_hist: set,
    solos_historicos: set,
) -> dict:
    """
    Anti-aislamiento: integra rutas de 1 parada en la ruta históricamente
    compatible que, cumpliendo los requisitos, deje la utilización más
    cercana al 100 % (peso ante todo), siempre que:
      1. La sucursal NO tenga historial de operar sola (está en solos_historicos).
      2. Haya capacidad disponible en el destino (respeta CAP-4).
      3. Co-ocurrencia válida con TODOS los miembros del destino.
      4. Afinidad acumulada > 0 — evidencia histórica de que pertenece ahí.

    Para sucursales nuevas (no en el historial) se relaja la condición 4:
    basta con capacidad y co-ocurrencia para integrarlas.
    """
    for (veh, dia) in list(groups.keys()):
        miembros = groups[(veh, dia)]
        if len(miembros) != 1:
            continue

        solo     = miembros[0]
        solo_sid = solo["sid"]

        # Respetar el patrón histórico de ruta solitaria
        if solo_sid in solos_historicos:
            continue

        solo_kg  = pedidos_dict.get(solo_sid, 0)
        es_nueva = solo_sid not in nodos_hist

        mejor_dest, mejor_pct, mejor_aff = None, -1.0, -1.0
        for (alt_veh, alt_dia) in groups:
            if (alt_veh, alt_dia) == (veh, dia):
                continue
            alt_mems = groups[(alt_veh, alt_dia)]
            if not alt_mems:
                continue
            cap_alt = vehiculos_cap.get(alt_veh, 3500)
            nuevo_total = sum(pedidos_dict.get(m["sid"], 0) for m in alt_mems) + solo_kg
            if cap_alt <= 0 or nuevo_total > cap_alt:
                continue
            if not _coocurrencia_valida(solo_sid, alt_mems, afinidad_raw, nodos_hist):
                continue
            pct = nuevo_total / cap_alt
            if pct > mejor_pct:
                mejor_pct  = pct
                mejor_dest = (alt_veh, alt_dia)
                mejor_aff  = _afinidad_con_ruta(solo_sid, alt_mems, afinidad_norm)

        if mejor_dest and (mejor_aff > 0 or es_nueva):
            miembros.remove(solo)
            solo["seq"] = 999
            groups[mejor_dest].append(solo)

    return {k: v for k, v in groups.items() if v}


def generar_rutas_vrp_afinidad(logistica_id: str, lambda_afinidad: float = 0.5) -> dict:
    """
    Reproduce los patrones históricos validados adaptados al pedido actual.

    Reglas que el algoritmo garantiza:
    ─ CAP-4: camiones 3.5t tienen tope fijo de 3.9t (aplicado en vehiculos_cap).
    ─ Anti-fusión: dos grupos históricamente separados no se unen aunque quede
      capacidad. La co-ocurrencia es una restricción dura en cada movimiento.
    ─ Anti-aislamiento: sucursales que quedan solas en una ruta se integran en
      la ruta históricamente compatible más afín con capacidad disponible.

    Flujo:
    0. Verificación exacta por ruta: las rutas históricas cuyo conjunto de
       sucursales y peso total coincidan lo suficiente con el pedido actual
       (≥85 % de cobertura y ≥85 % de similitud de peso) se copian tal como
       fueron realizadas — mismas sucursales, vehículo/día y orden — sin
       pasar por la votación de afinidad. Solo lo que se desvía demasiado
       del historial pasa por los pasos 1-6.
    1. Construcción de la matriz de afinidad ponderada por recencia y por
       similitud de peso con el pedido actual.
    2. Asignación de cada sucursal a su (vehiculo, dia) histórico preferido,
       verificando co-ocurrencia con los ya asignados (anti-fusión inicial).
    3. Sucursales nuevas: heredan la ruta del nodo histórico más cercano.
    4. Resolución de sobrecargas: el peso manda — se reasigna por historial
       y, si no hay alternativa histórica, por proximidad y disponibilidad,
       de forma que ningún vehículo quede sobre su capacidad mientras haya
       cupo en otra ruta.
    5. Consolidación de rutas de 1 parada: si la sucursal NO tiene historial
       de operar sola, se integra en la ruta más afín con capacidad y
       co-ocurrencia válida. Si sí tiene ese historial, se mantiene sola.
    6. Secuencia histórica conservada; inserción por proximidad para paradas
       sin historial de orden.
    """
    oid = _parse_oid(logistica_id)
    if not oid:
        return {"status": "error", "mensaje": "logistica_id inválido"}
    if not _PANDAS:
        return {"status": "error", "mensaje": "pandas no está instalado"}

    db = get_db()

    # ── 1. Historial crudo ─────────────────────────────────────────────────────
    historiales = _historiales_crudos_sucursales()
    if not historiales:
        return {
            "status":  "error",
            "mensaje": "No hay historial cargado. Ve a Configuración → Rutas Históricas y carga al menos un CSV.",
        }

    # ── 2. Pedidos actuales (idéntico a generar_rutas_vrp) ─────────────────────
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
        if id_suc is None or peso <= 0:
            continue
        try:
            pedidos_dict[int(id_suc)] = peso
        except (TypeError, ValueError):
            continue

    if not pedidos_dict:
        return {"status": "error", "mensaje": "No hay sucursales con peso en la extracción."}

    # Matriz de afinidad: la semana histórica cuyo peso total se parezca más
    # al pedido actual se usa como guía principal de agrupación y orden, por
    # encima de las demás (ver construir_afinidad → factor de similitud de peso).
    afinidad_data = construir_afinidad(historiales, pedidos_dict)

    # ── 3. Coordenadas y nombres de sucursales ─────────────────────────────────
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

    # ── 4. Vehículos (CAP-4: camiones 3.5t tienen tope fijo de 3.9t) ────────────
    # obtener_capacidades_vehiculos() aplica capacidad_efectiva_kg() que implementa
    # esta regla: cualquier vehículo 3500-4000 kg nominal → límite efectivo = 3900 kg.
    vehiculos_cap  = obtener_capacidades_vehiculos()
    info_vehiculos = obtener_info_vehiculos()
    if not vehiculos_cap:
        return {"status": "error", "mensaje": "No hay vehículos activos con capacidad configurada."}

    # ── 5. Asignar cada sucursal a su (vehiculo, dia) y secuencia histórica ───────
    pref_vd        = afinidad_data["pref_vehiculo_dia"]   # {sid: (veh, dia)}
    afinidad_norm  = afinidad_data["afinidad"]            # {(i,j): float normalizado}
    afinidad_raw   = afinidad_data["afinidad_raw"]        # {(i,j): float sin normalizar}
    nodos_hist     = set(afinidad_data["nodos"])          # sucursales que aparecen en historial
    seq_hist       = _extraer_secuencias_historicas(historiales)
    nodos_con_pref = [n for n in pref_vd if n in coords_dict]

    groups: dict = defaultdict(list)
    pendientes: list = []  # sucursales que no pudieron unirse a su grupo preferido

    # ── Verificación exacta por ruta: si una ruta histórica coincide lo
    # suficiente (cobertura y peso) con el pedido actual, se copia tal como
    # fue realizada — mismas sucursales, vehículo/día y orden — en vez de
    # pasar por la votación de afinidad. Lo que no coincide lo suficiente sí
    # pasa por el algoritmo normal más abajo. La capacidad y la sobrecarga
    # se siguen verificando después, igual que para el resto de las rutas.
    copias_exactas = _detectar_copias_exactas(historiales, pedidos_dict)
    sids_copiados: set = set()
    for (veh, dia), miembros in copias_exactas.items():
        groups[(veh, dia)].extend(miembros)
        sids_copiados.update(m["sid"] for m in miembros)

    for sid, kg in pedidos_dict.items():
        if sid in sids_copiados:
            continue  # ya asignada por copia exacta de una ruta histórica
        if sid in pref_vd:
            veh, dia = pref_vd[sid]
            seq = seq_hist.get(sid, {}).get((veh, dia), 999)
            # Anti-fusión inicial: solo agregar si co-ocurre con los ya asignados
            if not groups[(veh, dia)] or _coocurrencia_valida(sid, groups[(veh, dia)], afinidad_raw, nodos_hist):
                groups[(veh, dia)].append({"sid": sid, "seq": seq})
            else:
                pendientes.append((sid, seq))
        elif nodos_con_pref and sid in coords_dict:
            # Sucursal nueva: hereda la ruta del nodo histórico más cercano
            lat, lon = coords_dict[sid]
            near = min(
                nodos_con_pref,
                key=lambda n: _haversine_cw(lat, lon, coords_dict[n][0], coords_dict[n][1]),
            )
            veh, dia = pref_vd[near]
            groups[(veh, dia)].append({"sid": sid, "seq": 999})
        elif vehiculos_cap:
            veh, dia = next(iter(vehiculos_cap)), "LUNES"
            groups[(veh, dia)].append({"sid": sid, "seq": 999})
        else:
            groups[("VEHICULO", "LUNES")].append({"sid": sid, "seq": 999})

    # Reasignar pendientes al grupo compatible con capacidad disponible,
    # priorizando el que quede más cerca del 100 % de uso (peso ante todo).
    for sid, seq in pendientes:
        kg = pedidos_dict.get(sid, 0)
        mejor_alt = _elegir_destino_por_peso(
            sid, kg, groups, pedidos_dict, vehiculos_cap,
            afinidad_raw, nodos_hist, coords_dict, excluir=pref_vd[sid],
        )
        if mejor_alt:
            alt_seq = seq_hist.get(sid, {}).get(mejor_alt, 999)
            groups[mejor_alt].append({"sid": sid, "seq": alt_seq})
        else:
            # Sin grupo compatible: ruta propia (el anti-aislamiento intentará integrarlo)
            veh, dia = pref_vd[sid]
            groups[(veh, dia)].append({"sid": sid, "seq": seq})

    # ── 6. Resolver sobrecargas respetando capacidad e historial (peso ante todo) ──
    groups = _resolver_sobrecarga_con_afinidad(
        groups, pedidos_dict, vehiculos_cap, afinidad_norm, afinidad_raw, nodos_hist,
        coords_dict,
    )

    # ── 6.5. Anti-aislamiento: integrar rutas de 1 parada en ruta compatible ───
    # Detectar sucursales que históricamente operan solas (umbral ≥ 50 % de sus
    # apariciones y al menos 2 veces confirmado) para no forzarlas en grupo.
    solos_historicos = _detectar_historicamente_solos(historiales)
    groups = _consolidar_aisladas(
        groups, pedidos_dict, vehiculos_cap, afinidad_norm, afinidad_raw,
        nodos_hist, solos_historicos,
    )

    # ── 7. Estadísticas históricas para clasificar el estado de cada ruta ───────
    dfs_hist = obtener_historicos_como_dfs()
    n_hist   = len(dfs_hist)
    weights  = [float(i + 1) for i in range(n_hist)]
    _, _, route_stats_hist, _ = build_template_from_history(dfs_hist, weights) if dfs_hist else ({}, {}, {}, {})

    rows: list = []
    report_rows: list = []

    for (veh, dia), miembros in sorted(groups.items()):
        total_kg = sum(pedidos_dict.get(m["sid"], 0) for m in miembros)
        cap      = vehiculos_cap.get(veh, 3500)

        ruta_tmp = _Ruta_CW(
            paradas=[m["sid"] for m in miembros],
            kg_total=total_kg,
            vehiculo=veh,
            capacidad=cap,
            dia=dia,
        )
        estado = _calcular_estado_cw(ruta_tmp, route_stats_hist)

        notas_sb = ""
        if total_kg > cap:
            notas_sb = f"Sobrecarga: {round(total_kg - cap)} kg sin ruta destino disponible"

        total_m3   = sum(volumenes_dict.get(m["sid"], 0.0) for m in miembros)
        report_rows.append({
            "vehiculo":     veh,
            "dia_semana":   dia,
            "sucursales":   len(miembros),
            "kg_total":     round(total_kg),
            "capacidad_kg": cap,
            "uso_%":        round(total_kg / cap * 100, 1) if cap > 0 else 0,
            "m3_total":     round(total_m3, 4),
            "estado":       estado,
            "notas":        notas_sb,
        })

        # Orden: prioriza la secuencia histórica de cada parada. Las paradas
        # sin historial válido (nuevas o reasignadas) se insertan por
        # proximidad geográfica, sin descartar el orden histórico del resto.
        ordered = ordenar_paradas_por_historico(miembros, coords_dict)

        for i, sid in enumerate(ordered, 1):
            rows.append({
                "num_tienda":       sid,
                "vehiculo":         veh,
                "dia_semana":       dia,
                "secuencia_visita": i,
                "kg_entrega":       int(pedidos_dict.get(sid, 0)),
            })

    if not rows:
        return {"status": "error", "mensaje": "No se generaron rutas. Verifica el historial y los pedidos."}

    # ── 8. Construir detalle_por_dia (mismo formato que generar_rutas_vrp) ──────
    grupos = defaultdict(list)
    for r in rows:
        grupos[(r["vehiculo"], r["dia_semana"])].append(r)

    detalle_por_dia = {}
    for (veh, dia), ruts in grupos.items():
        dia_key = _normalizar_dia(dia)
        if dia_key not in detalle_por_dia:
            detalle_por_dia[dia_key] = {}

        ruta_id  = f"vrpaf_{veh.replace(' ', '_').lower()}_{dia.lower()}"
        placas   = info_vehiculos.get(veh, {}).get("placas", "")
        total_kg = sum(r["kg_entrega"] for r in ruts)
        cap_kg   = vehiculos_cap.get(veh, 3500)
        pct      = round(total_kg / cap_kg * 100, 1) if cap_kg > 0 else 0

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
            "nombre_ruta":            f"{veh} — {dia.capitalize()}",
            "vehiculo_placas":        placas,
            "vehiculo_abreviatura":   veh,
            "capacidad_ton":          cap_kg / 1000,
            "peso_total_kg":          total_kg,
            "porcentaje_utilizacion": pct,
            "hora_salida":            "08:00",
            "hora_regreso_estimada":  "",
            "cumple_horario":         True,
            "sucursales":             sucursales,
            "vrp_estado":             vrp_estado,
        }

    # ── 8. Guardar en producción y en colecciones _preview para comparar ─────
    now_iso = datetime.now().isoformat()
    asig_doc = {
        "logistica_id":    oid,
        "detalle_por_dia": detalle_por_dia,
        "generado_por":    "vrp_afinidad",
        "lambda_afinidad": lambda_afinidad,
        "n_historicos":    len(historiales),
        "guardado_en":     now_iso,
    }
    db["asignaciones"].update_one(
        {"logistica_id": oid},
        {"$set": asig_doc},
        upsert=True,
    )
    db["vrp_reportes"].update_one(
        {"logistica_id": oid},
        {"$set": {
            "logistica_id":    oid,
            "reporte":         report_rows,
            "consolidaciones": [],
            "lambda_afinidad": lambda_afinidad,
            "generado_en":     now_iso,
        }},
        upsert=True,
    )
    # Copia en colecciones _preview para comparar con el algoritmo anterior
    db["asignaciones_vrp_afinidad_preview"].update_one(
        {"logistica_id": oid},
        {"$set": {**asig_doc, "generado_por": "vrp_afinidad_preview"}},
        upsert=True,
    )
    db["vrp_reportes_afinidad"].update_one(
        {"logistica_id": oid},
        {"$set": {
            "logistica_id":    oid,
            "reporte":         report_rows,
            "lambda_afinidad": lambda_afinidad,
            "generado_en":     now_iso,
        }},
        upsert=True,
    )

    return {
        "status":            "ok",
        "total_rutas":       len(grupos),
        "total_sucursales":  len(pedidos_dict),
        "n_historicos":      len(historiales),
        "lambda_afinidad":   lambda_afinidad,
        "reporte":           report_rows,
        "consolidaciones":   [],
        "rutas_copiadas_exactas":      len(copias_exactas),
        "sucursales_copiadas_exactas": len(sids_copiados),
    }


def obtener_preview_vrp_afinidad(logistica_id: str) -> dict:
    """Devuelve el último preview de Clarke-Wright + afinidad (no afecta asignaciones reales)."""
    oid = _parse_oid(logistica_id)
    if not oid:
        return {}
    try:
        db  = get_db()
        doc = db["vrp_reportes_afinidad"].find_one({"logistica_id": oid})
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

def guardar_en_historico(logistica_id: str, nombre: str, rutas_list: list, tipo_registro: str = "sucursales") -> dict:
    """
    Guarda rutas confirmadas como un nuevo documento en rutas_historicas.

    rutas_list: list de dicts con campos num_tienda/id_sucursal, vehiculo,
                dia_semana, secuencia_visita, kg_entrega
    """
    try:
        filas = []
        for r in rutas_list:
            id_sucursal = r.get("id_sucursal") or r.get("num_tienda", 0)
            id_cliente = r.get("id_cliente")
            if id_cliente is None and tipo_registro == "mayoristas":
                id_cliente = id_sucursal
            filas.append({
                "id_sucursal":      id_sucursal,
                "id_cliente":       id_cliente,
                "ruta_id":          r.get("ruta_id"),
                "tipo":             r.get("tipo") or ("mayorista" if tipo_registro == "mayoristas" else "sucursal"),
                "vehiculo":         r.get("vehiculo", ""),
                "dia_semana":       str(r.get("dia_semana", "")).upper(),
                "secuencia_visita": int(r.get("secuencia_visita") or r.get("orden") or 1),
                "kg_entrega":       float(r.get("kg_entrega") or r.get("peso_kg") or 0),
            })

        if not filas:
            return {"status": "error", "mensaje": "No hay rutas para guardar"}

        filas_suc = [f for f in filas if f.get("tipo") != "mayorista"]
        filas_may = [f for f in filas if f.get("tipo") == "mayorista"]

        if _PANDAS:
            df_suc = pd.DataFrame(filas_suc) if filas_suc else pd.DataFrame(columns=["id_sucursal", "vehiculo", "dia_semana"])
            n_suc  = int(df_suc["id_sucursal"].nunique()) if filas_suc else 0
            df_all = pd.DataFrame(filas) if filas else pd.DataFrame(columns=["vehiculo", "dia_semana"])
            n_rut  = int(df_all.groupby(["vehiculo", "dia_semana"]).ngroups) if filas else 0
        else:
            n_suc = len(set(f["id_sucursal"] for f in filas_suc if f.get("id_sucursal")))
            grupos = defaultdict(set)
            for f in filas:
                grupos[f["vehiculo"]].add(f["dia_semana"])
            n_rut = sum(len(v) for v in grupos.values())

        n_may = len(set(str(f.get("id_cliente") or "") for f in filas_may if f.get("id_cliente")))
        dias  = _dias_desde_filas(filas)

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
            "n_mayoristas":  n_may,
            "n_rutas":       n_rut,
            "dias":          dias,
            "logistica_id":  str(logistica_id),
            "fecha_inicio":  fecha_inicio,
            "fecha_fin":     fecha_fin,
            "cargado_en":    cargado_en,
            "confirmada":    True,
            "tipo_registro": tipo_registro,
        }
        # Upsert: si ya existe un historial confirmado para esta logística, actualizarlo
        res = db["rutas_historicas"].update_one(
            {"logistica_id": str(logistica_id), "confirmada": True, "tipo_registro": {"$ne": "mayoristas"}},
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
        filas  = [f for f in doc.get("filas", []) if f.get("tipo") != "mayorista"]
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
    # CAP-4: capacidad_kg ya incluye el tope fijo de 3900 kg para vehículos de
    # 3.5-4 t; para cualquier otro, es exactamente su capacidad nominal.
    veh_info = {}        # abrev → {placas, capacidad_kg}
    all_vehiculos = []
    for v in db["vehiculos"].find({}):
        abrev   = v.get("abreviatura") or v.get("nombre_corto") or ""
        placas  = v.get("placas") or ""
        cap_ton = float(v.get("capacidad_toneladas") or 0)
        cap_kg  = capacidad_efectiva_kg(cap_ton * 1000)
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
            if cap_kg <= 0 or peso_kg > cap_kg:
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
        # CAP-4: nunca se sugiere un vehículo que quede sobre su capacidad
        # efectiva (capacidad_kg ya incluye el tope de 3.9 t para 3.5-4 t).
        disponibles = [
            v for v in all_vehiculos
            if v["placas"] and v["capacidad_kg"] > 0
            and v["placas"] not in assigned_per_day[dia]
            and peso_kg <= v["capacidad_kg"]
        ]

        def _score(v):
            pct_v = peso_kg / v["capacidad_kg"] * 100
            return abs(pct_v - 100)

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
        tmpl, _, route_stats, _ = build_template_from_history(dfs, weights)
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

        # Cargar coordenadas y nombres de mayoristas
        may_coords: dict = {}
        may_nombres: dict = {}
        for may in db["clientes_mayoristas"].find({}):
            idc = may.get("id_cliente")
            lat = may.get("latitud")
            lon = may.get("longitud")
            if idc is None or lat is None or lon is None:
                continue
            try:
                may_coords[int(idc)]  = (float(lat), float(lon))
                may_nombres[int(idc)] = may.get("nombre") or str(idc)
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
                es_may = f.get("tipo") == "mayorista"
                if es_may:
                    try:
                        idc = int(f.get("id_cliente") or 0)
                    except (ValueError, TypeError):
                        continue
                    coord = may_coords.get(idc)
                    if not coord:
                        continue
                    paradas.append({
                        "tipo":       "mayorista",
                        "id_cliente": idc,
                        "nombre":     may_nombres.get(idc, str(idc)),
                        "lat":        coord[0],
                        "lon":        coord[1],
                        "orden":      int(f.get("secuencia_visita", 0)),
                        "kg_entrega": float(f.get("kg_entrega", 0)),
                    })
                else:
                    try:
                        nt = int(f.get("id_sucursal", 0))
                    except (ValueError, TypeError):
                        continue
                    coord = coords_dict.get(nt)
                    if not coord:
                        continue
                    paradas.append({
                        "tipo":       "sucursal",
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

        # Cargar coordenadas de mayoristas
        may_coords: dict = {}
        may_nombres: dict = {}
        for may in db["clientes_mayoristas"].find({}):
            idc = may.get("id_cliente")
            lat = may.get("latitud")
            lon = may.get("longitud")
            if idc is None or lat is None or lon is None:
                continue
            try:
                may_coords[int(idc)]  = (float(lat), float(lon))
                may_nombres[int(idc)] = may.get("nombre") or str(idc)
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
                es_may = f.get("tipo") == "mayorista"
                if es_may:
                    try:
                        idc = int(f.get("id_cliente") or 0)
                    except (ValueError, TypeError):
                        continue
                    coord = may_coords.get(idc)
                    if not coord:
                        continue
                    paradas.append({
                        "tipo":       "mayorista",
                        "id_cliente": idc,
                        "nombre":     may_nombres.get(idc, str(idc)),
                        "lat":        coord[0],
                        "lon":        coord[1],
                        "orden":      int(f.get("secuencia_visita", 0)),
                        "kg_entrega": float(f.get("kg_entrega", 0)),
                    })
                else:
                    try:
                        nt = int(f.get("id_sucursal", 0))
                    except (ValueError, TypeError):
                        continue
                    coord = coords_dict.get(nt)
                    if not coord:
                        continue
                    paradas.append({
                        "tipo":       "sucursal",
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
