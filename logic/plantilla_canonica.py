"""
logic/plantilla_canonica.py

Plantilla canónica (ConVRP): carga y lectura de la plantilla histórica sobre la
que el VRP dejará de GENERAR rutas de cero para pasar a AJUSTAR.

Fuentes:
  - datos/mapeo_no_a_numtienda.csv  → bridge revisado No. SUCURSAL ↔ num_tienda
    (construido y validado en Fase 0: biyección por coordenada + cruce por
    nombre; 2 pares con dist>2 km ya revisados = error de captura, no de mapeo).
  - rutas_canonicas_lores_1.xlsx    → hojas LORES (grupos) y MAYORISTAS_ZONAS.
  - mapeo_poblacion_a_zona.csv      → diccionario población/municipio → zona.

Versionado NO destructivo: cada carga inserta una versión nueva y marca la
anterior vigente=0; nunca borra. Los lectores devuelven la versión vigente.

NOTA IMPORTANTE — `rutas_historicas_visitas`: NO es una tabla huérfana. Es la
fuente normalizada (id_sucursal=num_tienda, vehiculo, dia_semana,
secuencia_visita, kg_entrega) desde la que se reconstruyen los grupos de
co-viaje y es la BASE de la validación por origen móvil de Fase 2/4. No
limpiar.
"""
import os
import re
import io
import hashlib
import unicodedata
from datetime import datetime

import pandas as pd
from sqlalchemy import select, insert, update, func

from db import get_db, get_table, transaccion

_RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRIDGE_CSV_DEFAULT = os.path.join(_RAIZ, "datos", "mapeo_no_a_numtienda.csv")
DIAS_CSV_DEFAULT = os.path.join(_RAIZ, "datos", "dias_admisibles_por_grupo.csv")
UNIDAD_CSV_DEFAULT = os.path.join(_RAIZ, "datos", "unidad_ref_por_grupo.csv")
FORZAR_CSV_DEFAULT = os.path.join(_RAIZ, "datos", "grupos_unidad_forzada.csv")


# ── normalización ──────────────────────────────────────────────────────────
def _norm(s) -> str:
    s = str(s).strip().upper()
    s = "".join(c for c in unicodedata.normalize("NFD", s)
                if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s)


def _parse_nos(x) -> list:
    """'3, 5 7' → [3,5,7] (tolera comas, espacios, ; / )."""
    return [int(t) for t in re.split(r"[,\s;/]+", str(x)) if t.strip().isdigit()]


def _resolver_unidad(ref, unidades):
    """
    Resuelve la unidad de referencia del Excel contra los nombres reales de la
    BD: el Excel escribe 'F350_2' y el catálogo 'F 350_2'. Se compara ignorando
    espacios, acentos y mayúsculas.

    Sin adivinanzas: si no hay coincidencia exacta tras normalizar, devuelve
    None y el llamador lo reporta (una preferencia mal resuelta se ignora en
    silencio y el VRP deja de respetar la unidad histórica).
    """
    if ref is None:
        return None
    objetivo = _norm(ref).replace(" ", "")
    if not objetivo:
        return None
    for u in unidades:
        if _norm(u).replace(" ", "") == objetivo:
            return u
    return None


def _col(df: pd.DataFrame, *claves):
    """Devuelve el nombre real de la 1ª columna cuyo header normalizado
    contiene TODAS las `claves` (también normalizadas). None si no hay."""
    claves = [_norm(k) for k in claves]
    for c in df.columns:
        cn = _norm(c)
        if all(k in cn for k in claves):
            return c
    return None


# ── errores ────────────────────────────────────────────────────────────────
class PlantillaError(Exception):
    """Aborta la carga: dato que no se debe adivinar (nombre sin resolver, etc.)."""


# ── lectura del bridge (CSV congelado y revisado) ──────────────────────────
def _leer_bridge(bridge_csv: str) -> dict:
    df = pd.read_csv(bridge_csv, encoding="utf-8-sig")
    df.columns = [c.strip() for c in df.columns]
    bridge = {}
    pendientes = []
    for _, r in df.iterrows():
        no = int(r["no_sucursal"])
        estado = str(r.get("estado_revision", "ok")).strip().lower()
        if estado == "pendiente":
            pendientes.append((no, r.get("nombre_excel"), r.get("dist_m")))
        bridge[no] = dict(
            num_tienda=int(r["num_tienda"]),
            nombre_excel=r.get("nombre_excel"), nombre_bd=r.get("nombre_bd"),
            lat_excel=r.get("lat_excel"), lon_excel=r.get("lon_excel"),
            lat_bd=r.get("lat_bd"), lon_bd=r.get("lon_bd"),
            dist_m=(int(r["dist_m"]) if pd.notna(r.get("dist_m")) else None),
            flag=(None if pd.isna(r.get("flag")) or str(r.get("flag")).strip() == "" else str(r.get("flag")).strip()),
            estado_revision=estado,
        )
    return bridge, pendientes


# ── parseo PURO (sin BD; testeable) ────────────────────────────────────────
def parsear_plantilla(xlsx_path: str, bridge_csv: str = None,
                      poblacion_csv: str = None, dias_csv: str = None,
                      unidades: list = None, unidad_csv: str = None,
                      forzar_csv: str = None) -> dict:
    """
    Lee el Excel canónico + el bridge (+ opcional población→zona) y devuelve el
    payload parseado SIN tocar la BD. No versiona ni escribe: es la parte pura,
    testeable. `cargar_plantilla_desde_excel` la usa y luego persiste.

    Aborta (PlantillaError) si:
      - el bridge tiene filas estado_revision='pendiente' (sin revisar), o
      - un No. SUCURSAL referenciado en LORES no existe en el bridge.
    """
    bridge_csv = bridge_csv or BRIDGE_CSV_DEFAULT
    warnings = []

    bridge, pendientes = _leer_bridge(bridge_csv)
    if pendientes:
        raise PlantillaError(
            f"Bridge con {len(pendientes)} fila(s) estado_revision='pendiente' "
            f"(dist>2 km sin revisar): {pendientes}. Revísalas antes de cargar.")
    for no, r in bridge.items():
        if r["estado_revision"] == "revisado_ok":
            warnings.append(
                f"No {no} ({r['nombre_excel']}→{r['nombre_bd']}): dist {r['dist_m']} m "
                f"REVISADA (error de captura, mapeo correcto); se conserva.")

    # ── hoja LORES → grupos + miembros ──
    lores = pd.read_excel(xlsx_path, sheet_name="LORES", header=2)
    c_grupo = _col(lores, "GRUPO")
    c_rig   = _col(lores, "RIGIDEZ")
    c_nos   = _col(lores, "NO", "SUCURSAL")
    c_dia   = _col(lores, "DIA")
    c_coh   = _col(lores, "COHESION")
    c_tam   = _col(lores, "TAM")
    c_uni   = _col(lores, "UNIDAD")
    c_que   = _col(lores, "QUE HACE")
    if not (c_grupo and c_rig and c_nos):
        raise PlantillaError(f"LORES: no encuentro columnas GRUPO/RIGIDEZ/NO.SUCURSAL "
                             f"(vistas: {list(lores.columns)})")

    grupos, miembros, no_resueltos = [], [], []
    unidades_sin_resolver: list = []
    for _, r in lores.iterrows():
        if pd.isna(r[c_grupo]):
            continue
        g = int(r[c_grupo])
        nts = []
        for no in _parse_nos(r[c_nos]):
            if no not in bridge:
                no_resueltos.append((g, no))
            else:
                nts.append(bridge[no]["num_tienda"])
        # La unidad de referencia se resuelve contra el catálogo real: el Excel
        # escribe 'F350_2' y la BD 'F 350_2'. Sin resolver, la preferencia de
        # unidad se ignoraría en silencio y el VRP no respetaría la unidad
        # histórica (la consistencia que pidió la empresa).
        uni_raw = (str(r[c_uni]).strip() if c_uni and pd.notna(r[c_uni]) else None)
        uni = uni_raw
        if unidades:
            resuelta = _resolver_unidad(uni_raw, unidades)
            if resuelta:
                uni = resuelta
            elif uni_raw:
                unidades_sin_resolver.append((g, uni_raw))
        grupos.append(dict(
            grupo=g,
            rigidez=_norm(r[c_rig]),
            dia=(_norm(r[c_dia]) if c_dia and pd.notna(r[c_dia]) else None),
            tam=(int(r[c_tam]) if c_tam and pd.notna(r[c_tam]) else len(nts)),
            cohesion=(float(r[c_coh]) if c_coh and pd.notna(r[c_coh]) else None),
            unidad_ref=uni,
            que_hace_vrp=(str(r[c_que]).strip() if c_que and pd.notna(r[c_que]) else None),
        ))
        for nt in nts:
            miembros.append((g, nt))
    if no_resueltos:
        raise PlantillaError(
            f"LORES referencia No. SUCURSAL fuera del bridge (sin resolver, no se "
            f"adivinan): {no_resueltos}")
    if unidades_sin_resolver:
        warnings.append(
            f"unidad_ref sin equivalente en el catálogo de vehículos "
            f"(la preferencia de unidad NO se aplicará a esos grupos): "
            f"{unidades_sin_resolver}")

    # ── unidad_ref recalibrada (unidad_ref_por_grupo.csv) ──
    # El Excel trae la unidad MÁS FRECUENTE de cada grupo por separado, y eso
    # ignora que la unidad es un recurso compartido: 8 grupos apuntaban a
    # T 17_2 y 8 a T 20 mientras J 18, J 19 y K 20 no eran referencia de
    # ninguno. El CSV trae la asignación resuelta POR DÍA contra la capacidad
    # de la flota (scripts/calibrar_unidad_ref.py). Medido fuera de muestra:
    # composición de ruta 22.0 % -> 27.4 %, jaccard 48.6 -> 53.5, cero unidades
    # ociosas. Si el archivo no está, queda la unidad del Excel.
    unidad_csv = unidad_csv or UNIDAD_CSV_DEFAULT
    if os.path.exists(unidad_csv):
        udf = pd.read_csv(unidad_csv, encoding="utf-8-sig")
        udf.columns = [c.strip() for c in udf.columns]
        nueva = {int(r["grupo"]): str(r["unidad_ref"]).strip()
                 for _, r in udf.iterrows() if str(r.get("unidad_ref", "")).strip()}
        # afinidad histórica grupo→unidad ('T 17_1:4 | F 350_3:2'). El motor la
        # usa para desempatar cuando cede la unidad de referencia: sin ella el
        # desempate era el orden alfabético del nombre del camión.
        afines = {}
        if "afinidad" in udf.columns:
            for _, r in udf.iterrows():
                v = str(r.get("afinidad") or "").strip()
                if v:
                    afines[int(r["grupo"])] = v[:400]
        sin_catalogo = sorted({u for u in nueva.values()
                               if unidades and u not in set(unidades)})
        if sin_catalogo:
            raise PlantillaError(
                f"unidad_ref_por_grupo.csv nombra unidades que no están en el "
                f"catálogo de vehículos: {sin_catalogo}. Se aborta en vez de "
                f"cargar una preferencia que el motor ignoraría en silencio.")
        cambios = []
        faltantes = []
        for gd in grupos:
            u = nueva.get(gd["grupo"])
            if u is None:
                faltantes.append(gd["grupo"])
                continue
            if u != gd["unidad_ref"]:
                cambios.append((gd["grupo"], gd["unidad_ref"], u))
            gd["unidad_ref"] = u
            gd["unidades_afines"] = afines.get(gd["grupo"])
        if faltantes:
            warnings.append(
                f"unidad_ref_por_grupo.csv no cubre los grupos {sorted(faltantes)}: "
                f"conservan la unidad del Excel.")
        if cambios:
            warnings.append(
                f"unidad_ref recalibrada por asignación global en {len(cambios)} "
                f"grupos (Excel -> CSV): "
                + ", ".join(f"g{g}:{a}->{b}" for g, a, b in cambios[:12])
                + (" …" if len(cambios) > 12 else ""))

    # ── unidad_forzada (grupos_unidad_forzada.csv) ──
    # Regla de negocio puntual, no una recalibración: el grupo listado NUNCA
    # cede su unidad_ref, sin importar sobrecupo (ver `asignar_unidades` en
    # convrp_logic.py). Hallado en producción 2026-08-12: el enganche de
    # mayoristas por zona oscila sin converger entre pasadas, y según en cuál
    # se corte el tope, Tuxtepec (grupo 1) y Cosamaloapan (grupo 4) quedaban
    # intercambiados entre F 350_2 y F 350_1. Arreglar la oscilación de raíz
    # movería otras rutas de forma impredecible; esto ancla sólo estos dos
    # grupos sin tocar el resto del reparto de la semana.
    forzar_csv = forzar_csv or FORZAR_CSV_DEFAULT
    forzados: set = set()
    if os.path.exists(forzar_csv):
        zdf = pd.read_csv(forzar_csv, encoding="utf-8-sig")
        zdf.columns = [c.strip() for c in zdf.columns]
        forzados = {int(r["grupo"]) for _, r in zdf.iterrows()}
    for gd in grupos:
        gd["unidad_forzada"] = gd["grupo"] in forzados

    # ── días admisibles por grupo (dias_admisibles_por_grupo.csv) ──
    # Un grupo sólo puede moverse dentro de estos días (rígido y flexible por
    # igual: rigidez de composición y flexibilidad de día son dimensiones
    # independientes). El día canónico es la preferencia (penalización análoga a
    # la de unidad). Se valida contra el día de LORES (últimas 4 sem).
    dias_csv = dias_csv or DIAS_CSV_DEFAULT
    dia_lores = {g["grupo"]: g["dia"] for g in grupos}
    dias_admisibles = []
    if os.path.exists(dias_csv):
        ddf = pd.read_csv(dias_csv, encoding="utf-8-sig")
        ddf.columns = [c.strip() for c in ddf.columns]
        for _, r in ddf.iterrows():
            g = int(r["grupo"])
            canon = _norm(r["dia_canonico"])
            adm = [_norm(d) for d in str(r["dias_admisibles"]).split("|") if d.strip()]
            if canon not in adm:            # el canónico siempre está en el set
                adm = [canon] + adm
            if g in dia_lores and dia_lores[g] and _norm(dia_lores[g]) not in adm:
                warnings.append(
                    f"grupo {g}: día LORES '{dia_lores[g]}' NO está en el set admisible "
                    f"{adm} (revisar); se agrega para no perder el canónico.")
                adm = [_norm(dia_lores[g])] + [d for d in adm if d != _norm(dia_lores[g])]
            elif g in dia_lores and dia_lores[g] and _norm(dia_lores[g]) != canon:
                warnings.append(
                    f"grupo {g}: día canónico CSV '{canon}' ≠ día LORES "
                    f"'{dia_lores[g]}' (últimas 4 sem manda como preferido).")
            # el preferido efectivo es el día de LORES si existe, si no el canónico CSV
            preferido = _norm(dia_lores[g]) if (g in dia_lores and dia_lores[g]) else canon
            for i, d in enumerate(adm):
                dias_admisibles.append(dict(grupo=g, dia=d,
                                            es_canonico=1 if d == preferido else 0,
                                            orden=i))
        grupos_sin_dias = sorted(set(dia_lores) - {d["grupo"] for d in dias_admisibles})
        if grupos_sin_dias:
            warnings.append(f"grupos sin fila en dias_admisibles (sólo su día LORES "
                            f"será admisible): {grupos_sin_dias}")
            for g in grupos_sin_dias:
                if dia_lores.get(g):
                    dias_admisibles.append(dict(grupo=g, dia=_norm(dia_lores[g]),
                                                es_canonico=1, orden=0))
    else:
        warnings.append(f"dias_admisibles: archivo no encontrado ({dias_csv}); "
                        f"cada grupo quedará con sólo su día LORES como admisible.")
        for g, d in dia_lores.items():
            if d:
                dias_admisibles.append(dict(grupo=g, dia=_norm(d), es_canonico=1, orden=0))

    # ── hoja MAYORISTAS_ZONAS ──
    zonas = []
    try:
        zdf = pd.read_excel(xlsx_path, sheet_name="MAYORISTAS_ZONAS", header=2)
        z_zona = _col(zdf, "ZONA")
        z_tipo = _col(zdf, "TIPO")
        z_grp  = _col(zdf, "GRUPO")
        z_dia  = _col(zdf, "DIA")
        z_reg  = _col(zdf, "REGLA")
        z_uni  = _col(zdf, "UNIDAD")
        z_kg   = _col(zdf, "KG")
        z_sem  = _col(zdf, "SEMANAS")
        z_par  = _col(zdf, "PARADAS")
        z_pct  = _col(zdf, "% DIA") or _col(zdf, "%")
        from logic.enganche_zona import confianza_zona
        for _, r in zdf.iterrows():
            if not z_zona or pd.isna(r[z_zona]):
                continue
            grupos_txt = (str(r[z_grp]).strip() if z_grp and pd.notna(r[z_grp]) else None)
            n_grupos = len([x for x in re.split(r"[^0-9]+", grupos_txt or "") if x.isdigit()])
            semanas = (float(r[z_sem]) if z_sem and pd.notna(r[z_sem]) else None)
            paradas = (float(r[z_par]) if z_par and pd.notna(r[z_par]) else None)
            pct = (float(r[z_pct]) if z_pct and pd.notna(r[z_pct]) else None)
            zonas.append(dict(
                zona=str(r[z_zona]).strip(),
                tipo=(_norm(r[z_tipo]) if z_tipo and pd.notna(r[z_tipo]) else None),
                grupos_lores=grupos_txt,
                dia_habitual=(_norm(r[z_dia]) if z_dia and pd.notna(r[z_dia]) else None),
                regla_enganche=(str(r[z_reg]).strip() if z_reg and pd.notna(r[z_reg]) else None),
                unidad_ref=(str(r[z_uni]).strip() if z_uni and pd.notna(r[z_uni]) else None),
                kg_prom=(float(r[z_kg]) if z_kg and pd.notna(r[z_kg]) else None),
                semanas=(int(semanas) if semanas is not None else None),
                paradas=(int(paradas) if paradas is not None else None),
                pct_dia=pct,
                # Confianza derivada de la evidencia: no todas las zonas del
                # catálogo son regla firme (TUXTEPEC: 3 de 9 semanas, día 67 %,
                # repartida entre 3 grupos).
                # La consistencia real (presencia del grupo núcleo) no está en el
                # Excel: `recalcular_confianza_zonas()` la calcula desde el
                # histórico y sobreescribe esto. Aquí queda un valor provisional.
                confianza=confianza_zona(semanas, None),
                grupo_nucleo=None, pct_nucleo=None,
            ))
    except Exception as e:  # noqa: BLE001
        # NO se degrada a catálogo vacío: un catálogo de zonas vacío es
        # indistinguible de uno cargado y dejaría el enganche por zona sin
        # destino, funcionando en apariencia y sin enganchar nada. Se aborta.
        raise PlantillaError(
            f"MAYORISTAS_ZONAS no se pudo parsear ({type(e).__name__}: {e}). "
            f"Sin catálogo de zonas el enganche de mayoristas no tiene destino; "
            f"se aborta en vez de cargar una plantilla a medias.") from e

    # ── diccionario población → zona (opcional; sólo confianza resuelta) ──
    poblaciones = []
    if poblacion_csv and os.path.exists(poblacion_csv):
        pdf = pd.read_csv(poblacion_csv, encoding="utf-8-sig")
        pdf.columns = [c.strip() for c in pdf.columns]
        p_pob = _col(pdf, "POBLACION") or _col(pdf, "MUNICIPIO") or pdf.columns[0]
        p_zona = _col(pdf, "ZONA")
        p_conf = _col(pdf, "CONFIANZA")
        for _, r in pdf.iterrows():
            zona = r.get(p_zona) if p_zona else None
            conf = str(r.get(p_conf)).strip().lower() if p_conf else ""
            if pd.isna(zona) or str(zona).strip() == "" or conf == "sin resolver":
                continue  # sin resolver → sin zona → fallback global (no se adivina)
            poblaciones.append(dict(
                poblacion=str(r[p_pob]).strip(), zona=str(zona).strip(),
                confianza=(conf or None)))
    elif poblacion_csv:
        warnings.append(f"población→zona: archivo no encontrado ({poblacion_csv}); "
                        f"esa tabla queda vacía (Fase 3 usará fallback global).")
    else:
        warnings.append("población→zona: no se pasó CSV; tabla vacía por ahora.")

    return dict(bridge=bridge, grupos=grupos, miembros=miembros, zonas=zonas,
                poblaciones=poblaciones, dias_admisibles=dias_admisibles,
                warnings=warnings, excel_hash=_hash_archivo(xlsx_path))


# ── carga (recargable, versionada; escribe BD) ─────────────────────────────
def cargar_plantilla_desde_excel(xlsx_path: str, bridge_csv: str = None,
                                 poblacion_csv: str = None, dias_csv: str = None,
                                 nota: str = None, semanas_analisis: int = 9,
                                 unidad_csv: str = None, forzar_csv: str = None) -> dict:
    """
    Parsea la plantilla y escribe una VERSIÓN NUEVA. Nunca borra: marca la
    anterior vigente=0. Advierte (warnings) por filas 'revisado_ok' (dist>2 km
    ya validadas en Fase 0).
    """
    try:
        from logic.vrp_logic import obtener_capacidades_vehiculos
        unidades = sorted(obtener_capacidades_vehiculos() or {})
    except Exception as e:  # noqa: BLE001
        # Sin catálogo, `_resolver_unidad` no corre y `unidad_ref` se guarda en
        # crudo ('F350_2' en vez de 'F 350_2'): la preferencia de unidad se
        # ignoraría en silencio, que es el bug que ya nos costó una vuelta.
        raise PlantillaError(
            f"no se pudo leer el catálogo de vehículos ({type(e).__name__}: {e}); "
            f"sin él `unidad_ref` quedaría sin resolver y la preferencia de "
            f"unidad se ignoraría en silencio.") from e
    if not unidades:
        raise PlantillaError(
            "el catálogo de vehículos vino vacío; `unidad_ref` quedaría sin "
            "resolver y la preferencia de unidad se ignoraría en silencio.")
    p = parsear_plantilla(xlsx_path, bridge_csv=bridge_csv,
                          poblacion_csv=poblacion_csv, dias_csv=dias_csv,
                          unidad_csv=unidad_csv, forzar_csv=forzar_csv,
                          unidades=unidades)
    bridge, grupos, miembros = p["bridge"], p["grupos"], p["miembros"]
    zonas, poblaciones, warnings = p["zonas"], p["poblaciones"], p["warnings"]
    dias_admisibles = p["dias_admisibles"]

    # ── escritura atómica, versión nueva ──
    ahora = datetime.now().isoformat()
    excel_hash = p["excel_hash"]
    n_flags = sum(1 for r in bridge.values() if r["flag"])

    with transaccion() as conn:
        t_meta = get_table("plantilla_meta")
        ver = (conn.execute(select(func.max(t_meta.c.version))).scalar() or 0) + 1

        tablas = ["plantilla_bridge_sucursal", "plantilla_grupo",
                  "plantilla_grupo_sucursal", "plantilla_grupo_dia",
                  "plantilla_zona_mayorista", "plantilla_poblacion_zona"]
        for tn in tablas:
            t = get_table(tn)
            conn.execute(update(t).where(t.c.vigente == 1).values(vigente=0))
        conn.execute(update(t_meta).where(t_meta.c.vigente == 1).values(vigente=0))

        tb = get_table("plantilla_bridge_sucursal")
        conn.execute(insert(tb), [dict(
            version=ver, no_sucursal=no, num_tienda=r["num_tienda"],
            nombre_excel=r["nombre_excel"], nombre_bd=r["nombre_bd"],
            lat_excel=r["lat_excel"], lon_excel=r["lon_excel"],
            lat_bd=r["lat_bd"], lon_bd=r["lon_bd"], dist_m=r["dist_m"],
            flag=r["flag"], estado_revision=r["estado_revision"],
            vigente_desde=ahora, vigente=1) for no, r in bridge.items()])

        tg = get_table("plantilla_grupo")
        conn.execute(insert(tg), [dict(version=ver, vigente_desde=ahora, vigente=1, **g)
                                  for g in grupos])
        tgs = get_table("plantilla_grupo_sucursal")
        conn.execute(insert(tgs), [dict(version=ver, grupo=g, num_tienda=nt,
                                        vigente_desde=ahora, vigente=1)
                                  for g, nt in miembros])
        if dias_admisibles:
            tgd = get_table("plantilla_grupo_dia")
            conn.execute(insert(tgd), [dict(version=ver, vigente_desde=ahora, vigente=1, **d)
                                      for d in dias_admisibles])
        if zonas:
            tz = get_table("plantilla_zona_mayorista")
            conn.execute(insert(tz), [dict(version=ver, vigente_desde=ahora, vigente=1, **z)
                                      for z in zonas])
        if poblaciones:
            tp = get_table("plantilla_poblacion_zona")
            conn.execute(insert(tp), [dict(version=ver, vigente_desde=ahora, vigente=1, **p)
                                      for p in poblaciones])

        conn.execute(insert(t_meta).values(
            version=ver, cargado_en=ahora, excel_archivo=os.path.basename(xlsx_path),
            excel_hash=excel_hash, semanas_analisis=semanas_analisis,
            n_grupos=len(grupos), n_zonas=len(zonas), n_poblaciones=len(poblaciones),
            n_flags=n_flags, vigente=1, nota=nota))

    return {
        "status": "ok", "version": ver,
        "grupos": len(grupos), "miembros": len(miembros),
        "rigidos": sum(1 for g in grupos if g["rigidez"] == "RIGIDO"),
        "flexibles": sum(1 for g in grupos if g["rigidez"] != "RIGIDO"),
        "zonas": len(zonas), "poblaciones": len(poblaciones),
        "flags": n_flags, "warnings": warnings,
    }


def derivar_grupo_zona(sucursales: list, grupo_de_sucursal: dict,
                       grupos_por_id: dict, umbral: float = 0.60) -> dict:
    """
    Deriva rigidez/día/unidad_ref de una zona nueva que fusiona sucursales de
    varios `grupo` LORES viejos: hereda TODOS los valores calibrados del
    grupo que más sucursales le aportó a la zona. Empate -> gana el grupo de
    número más bajo (determinista).

    Si el grupo ganador cubre menos del `umbral` (60% por defecto, mismo
    criterio que `confianza_zona()` usa para "confianza BAJA" en
    enganche_zona.py) de las sucursales de la zona, `revisar=True`: se
    devuelve igual, no se aborta, pero queda marcada para que el negocio la
    revise.

    sucursales        : [num_tienda,...] de la zona nueva.
    grupo_de_sucursal : {num_tienda: grupo_id} del catálogo VIEJO vigente.
    grupos_por_id     : {grupo_id: {rigidez, dia, dia_preferido, unidad_ref,
                         unidades_afines, unidad_forzada, dias_admisibles}}
                        -- p. ej. {g["grupo"]: g for g in obtener_grupos()}.
    """
    from collections import Counter
    cnt = Counter(grupo_de_sucursal.get(s) for s in sucursales)
    total = len(sucursales) or 1
    ganador, veces = sorted(
        cnt.items(), key=lambda kv: (-kv[1], kv[0] if kv[0] is not None else 10**9)
    )[0]
    pct = veces / total
    info = grupos_por_id.get(ganador, {})
    return dict(
        grupo_origen=ganador, pct=pct, revisar=pct < umbral,
        rigidez=info.get("rigidez"), dia=info.get("dia"),
        dia_preferido=info.get("dia_preferido"), unidad_ref=info.get("unidad_ref"),
        unidades_afines=info.get("unidades_afines"),
        unidad_forzada=bool(info.get("unidad_forzada")),
        dias_admisibles=info.get("dias_admisibles") or [],
    )


def cargar_zonas_manual(sub_rutas: list, nota: str = None) -> dict:
    """
    Escribe una VERSIÓN NUEVA de `plantilla_grupo`/`plantilla_grupo_sucursal`/
    `plantilla_grupo_dia` a partir de una lista de sub-rutas ya resuelta (no
    hay Excel que parsear: es la reorganización manual de zonas 2026-08, ver
    docs/superpowers/specs/2026-08-26-reorganizacion-zonas-canonicas-design.md).

    NO toca `plantilla_bridge_sucursal`, `plantilla_zona_mayorista` ni
    `plantilla_poblacion_zona` -- cada tabla filtra su propio `vigente=1`
    independiente del número de versión, así que pueden quedar en versiones
    distintas sin romper los lectores (`obtener_bridge`, `obtener_zona`,
    `zona_de_poblacion`). Nunca borra: mismo patrón no-destructivo que
    `cargar_plantilla_desde_excel`.

    sub_rutas: [{grupo, zona, rigidez, dia, dia_preferido (opcional),
                 unidad_ref (opcional, None = sin preferencia),
                 unidades_afines (opcional), unidad_forzada (opcional, bool),
                 dias_admisibles: [dia,...], sucursales: [num_tienda,...]}]
    """
    ahora = datetime.now().isoformat()
    with transaccion() as conn:
        t_meta = get_table("plantilla_meta")
        ver = (conn.execute(select(func.max(t_meta.c.version))).scalar() or 0) + 1

        for tn in ["plantilla_grupo", "plantilla_grupo_sucursal", "plantilla_grupo_dia"]:
            t = get_table(tn)
            conn.execute(update(t).where(t.c.vigente == 1).values(vigente=0))
        conn.execute(update(t_meta).where(t_meta.c.vigente == 1).values(vigente=0))

        tg = get_table("plantilla_grupo")
        conn.execute(insert(tg), [dict(
            version=ver, grupo=int(r["grupo"]), zona=int(r["zona"]),
            rigidez=r["rigidez"], dia=r.get("dia"),
            tam=len(r.get("sucursales", [])), cohesion=None,
            unidad_ref=r.get("unidad_ref"), que_hace_vrp=r.get("que_hace_vrp"),
            unidades_afines=r.get("unidades_afines"),
            unidad_forzada=bool(r.get("unidad_forzada")),
            vigente_desde=ahora, vigente=1) for r in sub_rutas])

        tgs = get_table("plantilla_grupo_sucursal")
        conn.execute(insert(tgs), [dict(
            version=ver, grupo=int(r["grupo"]), num_tienda=nt,
            vigente_desde=ahora, vigente=1)
            for r in sub_rutas for nt in r.get("sucursales", [])])

        filas_dia = []
        for r in sub_rutas:
            adm = r.get("dias_admisibles") or ([r["dia"]] if r.get("dia") else [])
            preferido = r.get("dia_preferido") or r.get("dia")
            for i, d in enumerate(adm):
                filas_dia.append(dict(
                    version=ver, grupo=int(r["grupo"]), dia=d,
                    es_canonico=1 if d == preferido else 0, orden=i,
                    vigente_desde=ahora, vigente=1))
        if filas_dia:
            conn.execute(insert(get_table("plantilla_grupo_dia")), filas_dia)

        conn.execute(insert(t_meta).values(
            version=ver, cargado_en=ahora, excel_archivo=None, excel_hash=None,
            semanas_analisis=None, n_grupos=len(sub_rutas), n_zonas=None,
            n_poblaciones=None, n_flags=None, vigente=1, nota=nota))

    return dict(
        status="ok", version=ver, grupos=len(sub_rutas),
        zonas=len({r["zona"] for r in sub_rutas}),
        sucursales=sum(len(r.get("sucursales", [])) for r in sub_rutas))


def _hash_archivo(path: str) -> str:
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()[:16]
    except OSError:
        return ""


# ── LECTORES (versión vigente por defecto) ─────────────────────────────────
def version_vigente() -> int | None:
    db = get_db(); t = get_table("plantilla_meta")
    return db.execute(select(t.c.version).where(t.c.vigente == 1)).scalar()


def obtener_bridge(version: int = None) -> dict:
    """No. SUCURSAL → num_tienda (dict) de la versión vigente (o la indicada)."""
    db = get_db(); t = get_table("plantilla_bridge_sucursal")
    stmt = select(t.c.no_sucursal, t.c.num_tienda)
    stmt = stmt.where(t.c.version == version) if version else stmt.where(t.c.vigente == 1)
    return {int(r.no_sucursal): int(r.num_tienda) for r in db.execute(stmt)}


def obtener_grupos(version: int = None) -> list:
    """Lista de grupos con miembros y días admisibles:
    [{grupo, rigidez, dia, unidad_ref, sucursales:[num_tienda],
      dias_admisibles:[dia], dia_preferido}]."""
    db = get_db()
    tg = get_table("plantilla_grupo"); tgs = get_table("plantilla_grupo_sucursal")
    tgd = get_table("plantilla_grupo_dia")
    sg = select(tg).where(tg.c.version == version) if version else select(tg).where(tg.c.vigente == 1)
    ss = select(tgs).where(tgs.c.version == version) if version else select(tgs).where(tgs.c.vigente == 1)
    sd = select(tgd).where(tgd.c.version == version) if version else select(tgd).where(tgd.c.vigente == 1)
    miembros = {}
    for r in db.execute(ss):
        miembros.setdefault(int(r.grupo), []).append(int(r.num_tienda))
    dias = {}; preferido = {}
    for r in db.execute(sd).mappings():
        dias.setdefault(int(r["grupo"]), []).append((r["orden"], r["dia"], r["es_canonico"]))
    for g, lst in dias.items():
        lst.sort(key=lambda x: (x[0] if x[0] is not None else 99))
        preferido[g] = next((d for _, d, esc in lst if esc), lst[0][1] if lst else None)
    out = []
    for r in db.execute(sg).mappings():
        g = int(r["grupo"])
        out.append(dict(grupo=g, zona=r.get("zona"), rigidez=r["rigidez"], dia=r["dia"],
                        tam=r["tam"], cohesion=r["cohesion"], unidad_ref=r["unidad_ref"],
                        unidades_afines=r.get("unidades_afines"),
                        unidad_forzada=bool(r.get("unidad_forzada")),
                        que_hace_vrp=r["que_hace_vrp"],
                        sucursales=sorted(miembros.get(g, [])),
                        dias_admisibles=[d for _, d, _ in sorted(dias.get(g, []))],
                        dia_preferido=preferido.get(g, r["dia"])))
    return sorted(out, key=lambda g: g["grupo"])


def dias_admisibles_de_grupo(grupo: int, version: int = None) -> list:
    """Días en que el grupo puede operar (el preferido primero por `orden`)."""
    db = get_db(); t = get_table("plantilla_grupo_dia")
    st = select(t.c.dia, t.c.orden).where(t.c.grupo == int(grupo))
    st = st.where(t.c.version == version) if version else st.where(t.c.vigente == 1)
    filas = sorted(db.execute(st).all(), key=lambda x: (x.orden if x.orden is not None else 99))
    return [f.dia for f in filas]


def grupo_de_sucursal(num_tienda: int, version: int = None):
    """Devuelve (grupo, rigidez, dia) de la sucursal, o None si no está en la plantilla."""
    db = get_db()
    tgs = get_table("plantilla_grupo_sucursal"); tg = get_table("plantilla_grupo")
    ss = select(tgs.c.grupo).where(tgs.c.num_tienda == int(num_tienda))
    ss = ss.where(tgs.c.version == version) if version else ss.where(tgs.c.vigente == 1)
    g = db.execute(ss).scalar()
    if g is None:
        return None
    sg = select(tg.c.grupo, tg.c.rigidez, tg.c.dia).where(tg.c.grupo == g)
    sg = sg.where(tg.c.version == version) if version else sg.where(tg.c.vigente == 1)
    r = db.execute(sg).mappings().first()
    return dict(r) if r else None


def zona_de_poblacion(poblacion: str, version: int = None):
    """Devuelve la zona de una población/municipio (match normalizado), o None
    (sin zona → Fase 3 cae al fallback global; nunca se adivina)."""
    db = get_db(); t = get_table("plantilla_poblacion_zona")
    st = select(t.c.poblacion, t.c.zona, t.c.confianza)
    st = st.where(t.c.version == version) if version else st.where(t.c.vigente == 1)
    objetivo = _norm(poblacion)
    for r in db.execute(st):
        if _norm(r.poblacion) == objetivo:
            return dict(zona=r.zona, confianza=r.confianza)
    return None


# Semanas excluidas del cálculo de evidencia de zona: su carga de mayoristas
# está incompleta en la BD y contaría como "la zona no viajó".
# 15-19 jun: el Excel MAYOREO tiene 27 clientes / 3,073 kg y la BD sólo 11 /
# 1,327 kg — faltan 17 clientes. Es un caso aislado (las otras 8 semanas cuadran
# o difieren sólo por pedidos SUSPENDIDO que sí se entregaron); ese archivo se
# procesó incompleto. No se infiere el ruteo faltante: se excluye la semana.
SEMANAS_MAYORISTAS_INCOMPLETAS = ("15 al 19 de junio",)


def evidencia_zonas_desde_historico(zona_de_cliente: dict, grupo_de_sucursal: dict) -> dict:
    """
    {zona: {semanas, paradas, grupo_nucleo, pct_nucleo}} calculado sobre las
    entregas reales a mayoristas de `rutas_historicas`.

    El GRUPO NÚCLEO es el grupo Lores presente en la mayor proporción de las
    paradas de esa zona, y esa proporción (`pct_nucleo`) es la consistencia. Se
    mide por PRESENCIA, no por coincidencia del conjunto: que una zona viaje a
    veces con "4" y a veces con "4|19" no es duda de destino si el 4 está
    siempre.

    Es el destino de enganche que debe usar el VRP: el núcleo, no el conjunto.

    zona_de_cliente   : {str(id_cliente): zona}
    grupo_de_sucursal : {num_tienda: grupo}
    """
    import json as _j
    from collections import Counter, defaultdict
    db = get_db()
    t = get_table("rutas_historicas")
    presencia = defaultdict(Counter)
    paradas, semanas = Counter(), defaultdict(set)
    for r in db.execute(select(t.c.nombre, t.c.filas, t.c.tipo_registro)).mappings():
        nombre = str(r["nombre"]).lower()
        if r["tipo_registro"] != "sucursales" or "julio" in nombre:
            continue
        if any(x in nombre for x in SEMANAS_MAYORISTAS_INCOMPLETAS):
            continue          # carga de mayoristas incompleta: no cuenta
        viajes = defaultdict(list)
        for f in (_j.loads(r["filas"]) if r["filas"] else []):
            viajes[(str(f.get("vehiculo")), str(f.get("dia_semana")).upper())].append(f)
        for _, fs in viajes.items():
            grupos = sorted({grupo_de_sucursal.get(int(x["id_sucursal"]))
                             for x in fs
                             if x.get("tipo") != "mayorista"
                             and x.get("id_sucursal") is not None
                             and grupo_de_sucursal.get(int(x["id_sucursal"]))})
            for f in fs:
                if f.get("tipo") != "mayorista" or f.get("id_cliente") is None:
                    continue
                zona = zona_de_cliente.get(str(f["id_cliente"]))
                if not zona:
                    continue
                paradas[zona] += 1
                semanas[zona].add(str(r["nombre"]))
                for g in (grupos or [None]):
                    presencia[zona][g] += 1
    salida = {}
    for z, cnt in presencia.items():
        n = paradas[z] or 1
        # desempate determinista: mayor presencia, luego menor id de grupo
        nucleo, veces = sorted(cnt.items(),
                               key=lambda kv: (-kv[1], kv[0] if kv[0] else 10**9))[0]
        salida[z] = {
            "semanas": len(semanas[z]),
            "paradas": n,
            "grupo_nucleo": nucleo,          # None = viajó sin sucursales Lores
            "pct_nucleo": round(veces / n, 3),
        }
    return salida


def recalcular_confianza_zonas(zona_de_cliente: dict, grupo_de_sucursal: dict) -> dict:
    """
    Recalcula `grupo_nucleo`, `pct_nucleo`, `semanas`, `paradas` y `confianza` de
    las zonas VIGENTES a partir del histórico, y los guarda. Se corre después de
    cargar la plantilla (el Excel no trae la presencia del núcleo).
    """
    from sqlalchemy import update as _update
    from logic.enganche_zona import confianza_zona
    ev = evidencia_zonas_desde_historico(zona_de_cliente, grupo_de_sucursal)
    db = get_db()
    t = get_table("plantilla_zona_mayorista")
    for zona, e in ev.items():
        db.execute(_update(t).where(t.c.zona == zona, t.c.vigente == 1).values(
            semanas=e["semanas"], paradas=e["paradas"],
            grupo_nucleo=e["grupo_nucleo"], pct_nucleo=e["pct_nucleo"],
            confianza=confianza_zona(e["semanas"], e["pct_nucleo"])))
    return ev


def horarios_por_dia() -> dict:
    """
    {'LUNES': (salida_min, cierre_min), ...} leído de `configuracion.config_dias`.

    Traduce la llave: config_dias usa minúsculas ('miercoles') y el resto del
    sistema mayúsculas sin acento ('MIERCOLES'). El horario NO es uniforme —
    el lunes sale 11:00 y los demás 07:00 — así que asumir 07:00 para todos
    regala 4 h los lunes en la restricción de tiempo.
    """
    import json as _j
    from logic.logistica_tiempo import hhmm_a_min
    try:
        db = get_db()
        crudo = db.execute(select(get_table("configuracion").c.config_dias)).scalar()
        datos = _j.loads(crudo) if isinstance(crudo, str) else (crudo or {})
    except Exception as e:  # noqa: BLE001
        # Devolver {} significaría "07:00 para todos los días", que es
        # exactamente el error del lunes (abre 11:00) — 4 h de regalo en la
        # restricción que más decide. Mejor fallar que asumir.
        raise PlantillaError(
            f"no se pudo leer `configuracion.config_dias` ({type(e).__name__}: "
            f"{e}); asumir 07:00 para todos los días falsea la restricción de "
            f"tiempo (el lunes abre 11:00).") from e
    out = {}
    for dia, v in (datos or {}).items():
        if not isinstance(v, dict) or not v.get("habilitado", True):
            continue
        out[_norm(dia)] = (hhmm_a_min(v.get("hora_salida"), 420),
                           hhmm_a_min(v.get("hora_limite"), 1200))
    return out


def obtener_zona(zona: str, version: int = None):
    """Info de enganche de una zona de mayoristas, o None."""
    db = get_db(); t = get_table("plantilla_zona_mayorista")
    st = select(t)
    st = st.where(t.c.version == version) if version else st.where(t.c.vigente == 1)
    objetivo = _norm(zona)
    for r in db.execute(st).mappings():
        if _norm(r["zona"]) == objetivo:
            return dict(r)
    return None
