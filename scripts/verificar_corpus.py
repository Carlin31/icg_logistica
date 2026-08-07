"""
verificar_corpus.py — ¿el histórico de la BD es el plan del planeador?

SOLO LECTURA. No escribe en la BD ni toca los `.xls`.

Compara, semana por semana, la capa de SUCURSALES LORES de `rutas_historicas`
contra los archivos `_HT.xls` que entregó la empresa. Criterio de igualdad:

    (num_tienda, día, unidad)

Fuera del test por diseño, y hay que decirlo porque si no el número engaña:
  - **Mayoreo**: la capa de mayoreo de la BD viene de la distribución del
    sistema, no de la hoja, y no es consistente ni entre semanas.
  - **Paradas operativas** (recolecciones, aeropuerto, "mayoristas por
    definir"): nunca llegan a la BD — el front las descarta con
    `if (!suc.num_tienda) continue` — así que faltan en las 9 semanas.

El puente de nombres es el del pipeline canónico (`pipeline_rutas_canonicas.py`):
la hoja RESUMEN escribe 'LORES COSVER 1' y el catálogo 'COSAMALOAPAN 1'. Se
resuelve con tabla de ALIAS explícita, y sólo si falla se cae a `difflib`; el
desglose por método se imprime para que se vea cuánto del veredicto descansa en
coincidencia aproximada.

Uso:
    python scripts/verificar_corpus.py
"""
import sys, os, re, json, difflib, unicodedata
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import xlrd
import pandas as pd
from collections import Counter, defaultdict
from sqlalchemy import select
from app import create_app

DIR_XLS = r"C:/Users/carli/Downloads/Historicos"
DIAS = {"LUNES", "MARTES", "MIERCOLES", "JUEVES", "VIERNES", "SABADO", "DOMINGO"}

# Semana de cada archivo, por fecha de inicio (la llave del corpus).
ARCHIVO_A_FECHA = {
    "LOG DEL 9 AL 13 DE FEB 26 _HT.xls":            "2026-02-09",
    "LOG DEL 23 AL 27 DE FEB 26 _HT.xls":           "2026-02-23",
    "LOG DEL 9 AL 13 DE MARZO 26 _HT.xls":          "2026-03-09",
    "LOG DEL 23 AL 27 DE MARZO 26 _HT.xls":         "2026-03-23",
    "LOG DEL 6 AL 10 DE ABRIL 26 _HT.xls":          "2026-04-06",
    "LOG DEL 4 AL 8 DE MAYO DEL  26 _HT.xls":       "2026-05-04",
    "LOG DEL 18 AL 22 DE MAYO DEL  26 _HT.xls":     "2026-05-18",
    "LOG DEL 1 AL 5 DE JUNIO  DEL  26 _HT.xls":     "2026-06-01",
    "LOG DEL 15 AL 19 DE JUNIO  DEL  26 _HT.xls":   "2026-06-15",
}

# Tabla del pipeline canónico. Es EXPLÍCITA a propósito: son los nombres que la
# hoja de reparto abrevia de otra forma que el catálogo.
ALIAS = {
    'CENTRO': 'TUXT CENTRO', 'FFCC': 'FERROCARRIL', 'FMAGON': 'FLORES MAGON',
    'FORES MAGON': 'FLORES MAGON', 'VALLE': 'VALLLE NACIONAL',
    'VALLE NACIONAL': 'VALLLE NACIONAL',
    'PLAYA 1': 'PLAYA VICENTE 1', 'PLAYA_1': 'PLAYA VICENTE 1',
    'PLAYA 2': 'PLAYA VICENTE 2', 'PLAYA_2': 'PLAYA VICENTE 2',
    'SANTIAGO 1': 'SANTIAGO TUXTLA 1', 'SANTIAGO 2': 'SANTIAGO TUXTLA 2',
    'SAN ANDRES 1': 'SAN ANDRES TUXTLA 1', 'SAN ANDRES 2': 'SAN ANDRES TUXTLA 2',
    'SAN ANDRES 3': 'SAN ANDRES TUXTLA 3', 'CATEMACO 1': 'CATEMACO',
    'H.REAL': 'HACIENDA REAL', 'HACIENDA': 'HACIENDA REAL',
    'IMSS': 'SEGURO SOCIAL', 'ABEJAS': 'LAS ABEJAS', 'COSCO': 'COSCOMATEPEC',
    'P. NEGRAS': 'PIEDRAS NEGRAS', 'P.NEGRAS': 'PIEDRAS NEGRAS',
    'LORES.PIEDRAS NEGRAS': 'PIEDRAS NEGRAS', 'IGNACIO': 'IGNACIO DE LA LLAVE',
    'GABINO': 'GABINO BARREDA', 'PADELMA': 'PASO DE MACHO',
    'JAL DE DIAZ 1': 'JALAPA DE DIAZ 1', 'JAL DE DIAZ 2': 'JALAPA DE DIAZ 2',
    'JAL. DE DIAZ 2': 'JALAPA DE DIAZ 2', 'ANTON': 'ANTON LIZARDO',
    'CABABA': 'CABADA', 'LERDO_1': 'LERDO 1',
    'COSVER 1': 'COSAMALOAPAN 1', 'COSVER 2': 'COSAMALOAPAN 2',
    'COSVER 3': 'COSAMALOAPAN 3',
    '20 DE NOV': '20 DE NOVIEMBRE', '20 DE NOV 2': '20 DE NOVIEMBRE',
    '23 DE NOV': '23 DE NOV', '23 DE NOVIEMBRE': '23 DE NOV',
    'TOLOME / VENTAS': 'TOLOME', 'PASO DE OVEJAS / VENTAS': 'PASO DE OVEJAS',
}


def norm(s):
    s = str(s).strip().upper()
    s = "".join(c for c in unicodedata.normalize("NFD", s)
                if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s)


def dia_de_celda(valor):
    """Día de la semana en una celda de la columna DIA, o None.

    NO basta con `norm(c) in DIAS`: la hoja escribe el día con el chofer pegado
    y con puntuación —'VIERNES.', 'VIERNES_. SERGIO', 'LUNES_.  CESAR'— e
    incluso partido por el ajuste de línea ('MIERCO LES'). Reconocer sólo el
    literal exacto se come 4 de cada 30 celdas-día, y las filas de abajo heredan
    el día ANTERIOR: así aparecía Chiltepec en JUEVES cuando la hoja dice
    VIERNES.
    """
    solo = re.sub(r"[^A-Z]", "", norm(valor))
    for d in DIAS:
        if solo.startswith(d):
            return d
    return None


def unidad_de(bloque):
    b = norm(bloque)
    m = re.search(r"\(([^)]*)\)", b)
    return (m.group(1) if m else b).replace(" ", "").replace(".", "")


def parse_logistica(path):
    """{nombre_canónico: no_sucursal} desde la hoja 'LOGISTICA LORES'."""
    sh = xlrd.open_workbook(path).sheet_by_name("LOGISTICA LORES")
    out, hdr = {}, None
    for r in range(sh.nrows):
        v = [sh.cell_value(r, c) for c in range(sh.ncols)]
        if "NO. SUCURSAL" in [norm(x) for x in v]:
            hdr = r
            continue
        if hdr is None or len(v) < 4:
            continue
        no, nom = v[1], v[2]
        if nom == "" or v[3] == "":
            continue
        if isinstance(no, float):
            no = int(no)
        try:
            out[norm(nom)] = int(no)
        except (TypeError, ValueError):
            continue
    return out


def parse_resumen(path):
    """[{unidad, dia, sucursal_raw, kg}] desde la hoja 'RESUMEN'."""
    sh = xlrd.open_workbook(path).sheet_by_name("RESUMEN")
    filas, bloque, dia, dentro = [], None, None, False
    for r in range(sh.nrows):
        v = [sh.cell_value(r, c) if c < sh.ncols else "" for c in range(12)]
        c2, c3, c4, c5 = v[2], v[3], v[4], v[5]
        if isinstance(c2, str) and c2.strip() and all(str(x).strip() == "" for x in (c3, c4, c5)):
            nxt = sh.cell_value(r + 1, 2) if r + 1 < sh.nrows else ""
            if norm(nxt) == "DIA":
                bloque, dia, dentro = c2.strip(), None, True
                continue
        if norm(c2) == "DIA" and norm(c5) == "SUCURSAL":
            continue
        if not dentro:
            continue
        d = dia_de_celda(c2)
        if d:
            dia = d
        suc = str(c5).strip()
        if not suc or dia is None:
            continue
        filas.append(dict(unidad=unidad_de(bloque), dia=dia, sucursal_raw=norm(suc),
                          kg=v[6] if isinstance(v[6], float) else 0.0))
    return filas


def resolver(raw, name2no):
    """(nombre_canónico, método). Alias explícito primero; fuzzy sólo al final."""
    s = raw.replace("LORES.", "LORES ")
    s = re.sub(r"^LORES\s+", "", s).strip()
    if s in ALIAS:
        return ALIAS[s], "alias"
    if s in name2no:
        return s, "exacto"
    if "/" in s:
        head = s.split("/")[0].strip()
        if head in ALIAS:
            return ALIAS[head], "combinado"
        if head in name2no:
            return head, "combinado"
    m = re.match(r"T\.?\s*BCA\.?\s*(\d)", s)
    if m:
        return f"TIERRA {m.group(1)}", "alias"
    c = difflib.get_close_matches(s, list(name2no), n=1, cutoff=0.86)
    if c:
        return c[0], "fuzzy"
    return None, "SIN MATCH"


def main():
    app = create_app()
    with app.app_context():
        from db import get_db, get_table
        from logic.plantilla_canonica import _resolver_unidad, obtener_bridge
        db = get_db()

        # no_sucursal -> num_tienda (el bridge de Fase 0, ya validado)
        bridge = obtener_bridge()          # {no_sucursal: num_tienda}
        flota = [str(v.abreviatura) for v in
                 db.execute(select(get_table("vehiculos").c.abreviatura))]

        # BD: sólo tipo_registro='sucursales' (la fila huérfana de mayoreo no
        # forma parte del corpus y ya nos costó una vuelta de conteo)
        h = get_table("rutas_historicas")
        bd = {}
        for r in db.execute(select(h.c.filas, h.c.tipo_registro, h.c.fecha_inicio)).mappings():
            if r["tipo_registro"] != "sucursales":
                continue
            fi = str(r["fecha_inicio"] or "")[:10]
            if not fi:
                continue
            # Una tienda puede recibir más de una entrega en la semana (mismo
            # día con dos unidades, o dos días distintos): usar un set y no
            # sobrescribir, si no la segunda visita borra a la primera y el
            # comparador reporta una diferencia falsa.
            d = defaultdict(set)
            for f in json.loads(r["filas"] or "[]"):
                if f.get("tipo") == "mayorista" or f.get("id_sucursal") is None:
                    continue
                d[int(f["id_sucursal"])].add((str(f.get("vehiculo")),
                                              str(f.get("dia_semana")).upper()))
            bd[fi] = dict(d)

        metodos, sin_match = Counter(), Counter()
        sin_pref_total = {}
        print(f"{'semana':<13}{'filas xls':>9}{'puenteadas':>12}{'en BD':>8}"
              f"{'en ambas':>13}{'IGUALES':>9}{'difieren':>10}"
              f"{'sólo xls':>10}{'sólo BD':>9}   veredicto")
        veredicto = {}
        detalle_dif = defaultdict(list)
        faltan = {}
        for arch, fecha in sorted(ARCHIVO_A_FECHA.items(), key=lambda x: x[1]):
            p = os.path.join(DIR_XLS, arch)
            if not os.path.exists(p):
                print(f"{fecha:<13}ARCHIVO NO ENCONTRADO: {arch}")
                veredicto[fecha] = "SIN ARCHIVO"
                continue
            name2no = parse_logistica(p)
            filas = parse_resumen(p)
            # Sin el prefijo 'LORES' la fila se descartaba en silencio. Se
            # aceptan también las que resuelven contra el catálogo de la propia
            # hoja: son sucursales escritas sin la convención, no mayoreo.
            lores, sin_prefijo = [], []
            for f in filas:
                if f["sucursal_raw"].startswith("LORES"):
                    lores.append(f)
                elif re.sub(r"^(BB|AA)\d", "", f["sucursal_raw"]) == f["sucursal_raw"]:
                    nom_try, met_try = resolver(f["sucursal_raw"], name2no)
                    if nom_try is not None and met_try != "fuzzy":
                        lores.append(f)
                        sin_prefijo.append(f["sucursal_raw"])
            sin_pref_total[fecha] = sin_prefijo
            # Mismo motivo que del lado BD: acumular todas las visitas de la
            # semana por tienda, no quedarse sólo con la última fila leída.
            xls = defaultdict(set)
            for f in lores:
                nom, met = resolver(f["sucursal_raw"], name2no)
                metodos[met] += 1
                if nom is None:
                    sin_match[f["sucursal_raw"]] += 1
                    continue
                no = name2no.get(nom)
                nt = bridge.get(no) if no is not None else None
                if nt is None:
                    sin_match[f"(sin bridge) {nom}"] += 1
                    continue
                u = _resolver_unidad(f["unidad"], flota) or f["unidad"]
                xls[nt].add((u, f["dia"]))
            xls = dict(xls)
            enbd = bd.get(fecha, {})
            comunes = sorted(set(xls) & set(enbd))
            solo_xls = sorted(set(xls) - set(enbd))
            solo_bd = sorted(set(enbd) - set(xls))
            # rutas_historicas guarda como máximo UNA fila por (tienda, semana):
            # nunca representa una segunda visita (confirmado: ninguna tienda,
            # en ninguna semana, tiene más de un id_sucursal en `filas`). Por
            # eso la comparación correcta es "¿la entrada de BD corresponde a
            # ALGUNA de las visitas reales del Excel?" (subconjunto), no
            # igualdad estricta de conjuntos — si no, una tienda con dos
            # entregas reales en la semana siempre "difiere" aunque BD acierte
            # la única que sí guardó.
            ig = [s for s in comunes if enbd[s] <= xls[s]]
            dif = [s for s in comunes if not (enbd[s] <= xls[s])]
            for s in dif:
                detalle_dif[fecha].append((s, xls[s], enbd[s]))
            # Una sucursal presente en una fuente y no en la otra ES una
            # discrepancia, no un dato ausente: cuenta para el veredicto.
            faltantes = len(solo_xls) + len(solo_bd)
            ver = ("VERIFICADA" if not dif and not faltantes and comunes else
                   "SIN DATOS" if not comunes else
                   f"NO VERIFICADA ({len(dif)} dif + {faltantes} ausentes)")
            veredicto[fecha] = ver
            faltan[fecha] = (solo_xls, solo_bd)
            print(f"{fecha:<13}{len(lores):>9}{len(xls):>12}{len(enbd):>8}"
                  f"{len(comunes):>13}{len(ig):>9}{len(dif):>10}"
                  f"{len(solo_xls):>10}{len(solo_bd):>9}   {ver}")

        print(f"\nmétodos de puente: {dict(metodos)}")
        if sin_match:
            print(f"sin resolver ({sum(sin_match.values())} filas): "
                  f"{dict(sin_match.most_common(10))}")
        print(f"\nVERIFICADAS: {sum(1 for v in veredicto.values() if v == 'VERIFICADA')} de "
              f"{len(veredicto)}")
        for fecha, dif in sorted(detalle_dif.items()):
            print(f"\n  {fecha} — {len(dif)} sucursales difieren (xls -> BD):")
            for s, a, b in dif[:12]:
                fa = ", ".join(f"{u}/{d}" for u, d in sorted(a))
                fb = ", ".join(f"{u}/{d}" for u, d in sorted(b))
                print(f"     num_tienda {s:>4}   EXCEL=[{fa}]  BD=[{fb}]")
        print("")
        print("=== auditoría de ausencias (una ausencia ES discrepancia) ===")
        for fecha, (sx, sb) in sorted(faltan.items()):
            if sx or sb:
                print(f"  {fecha}  sólo en EXCEL: {sx}   sólo en BD: {sb}")
        n_sp = sum(len(v) for v in sin_pref_total.values())
        print("")
        print(f"=== nombres SIN el prefijo 'LORES' recuperados: {n_sp} ===")
        for fecha, v in sorted(sin_pref_total.items()):
            if v:
                print(f"  {fecha}: {sorted(set(v))}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
