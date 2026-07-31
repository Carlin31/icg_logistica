"""
logic/pdf_logic.py
Reporte de pesos — paleta azul, diseño compacto a dos columnas.

Fuente de datos: `modificaciones_rutas` (+ tablas normalizadas) en SQL Server,
filtrada por logistica_id proveniente de la sesión Flask.
"""
import json
import os
import re
from datetime import datetime

from bson import ObjectId
from bson.errors import InvalidId
from sqlalchemy import select

from reportlab.lib.pagesizes import LETTER, portrait
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.platypus import (
    BaseDocTemplate, PageTemplate, Frame,
    Table, TableStyle, Paragraph, Spacer, KeepTogether,
)

from db import get_db, get_table
from logic.mayoristas_logic import calcular_distribucion_mayoristas, _insertar_pos_proxima
from logic.modificacion_logic import obtener_modificacion_previa
from logic.groq_logic import generar_nombre_poblacion
from logic.logistica_tiempo import (
    TIEMPO_ENTREGA_ESTRICTO, evaluar_ruta_por_tiempo, evaluar_llegadas, hhmm_a_min,
)
from logic.asignacion_logic import consultar_osrm

# ── Directorio temporal para el PDF generado ─────────────────
# Se usa /tmp para compatibilidad con entornos de producción (Render, etc.)
# donde el sistema de archivos del proyecto puede ser de solo lectura.
# /tmp siempre es escribible y no necesita crearse.
TEMP_DIR = "/tmp/icg_pdf"

# ── Medidas de página ─────────────────────────────────────────
PW, PH             = portrait(LETTER)
MARGEN             = 15
ESPACIO_ENTRE_COLS = 10
ANCHO_COL          = (PW - MARGEN * 2 - ESPACIO_ENTRE_COLS) / 2

# ── Anchos de columna (suman ANCHO_COL) ──────────────────────
CW = [38, 28, 20, 125, 26, 22, 27]
iDIA, iAPOYO, iSEC, iSUC, iPESO, iVOL, iPCT_R = range(7)
NCOLS = 7

# ── Fuentes ───────────────────────────────────────────────────
SZ_ENC = 8.0
SZ_HDR = 6.5
SZ_DAT = 5.5
SZ_TOT = 5.5

# ── Paleta ────────────────────────────────────────────────────
C_ENC_VEH  = colors.HexColor("#1565C0")
C_HDR_COL  = colors.HexColor("#E3F2FD")
C_DIA_BG   = colors.HexColor("#F5F9FF")
C_SUBRUTA  = colors.HexColor("#DCEEFB")
C_SUBTOT   = colors.HexColor("#BBDEFB")
C_NAVY     = colors.HexColor("#1565C0")
C_BORDE    = colors.HexColor("#B0BEC5")
C_ALERTA   = colors.HexColor("#C0392B")
C_BLANCO   = colors.white
# ── Naranja para mayoristas ───────────────────────────────────
C_MAY_BG   = colors.HexColor("#FFF7ED")   # fondo fila mayorista
C_MAY_TEXT = colors.HexColor("#EA580C")   # texto naranja
C_MAY_SUBTOT = colors.HexColor("#FFEDD5") # subtotal cuando hay mayoristas
# ── Rojo para paradas no entregables por tiempo (Fase A) ──────
C_TARDE_BG   = colors.HexColor("#FEE2E2")  # fondo fila fuera de horario
C_TARDE_TEXT = colors.HexColor("#B91C1C")  # texto rojo

ORDEN_DIA = {"lunes": 1, "martes": 2, "miercoles": 3, "jueves": 4, "viernes": 5}
ABREV_DIA = {
    "lunes":     "LUNES",
    "martes":    "MARTES",
    "miercoles": "MIÉRCOLES",
    "jueves":    "JUEVES",
    "viernes":   "VIERNES",
}

# ── Reglas de auxiliares por ruta ───────────────────────────
# 1 auxiliar: peso <= 1500 kg o 2-3 sucursales
# 2 auxiliares: peso > 1500 kg o 5+ sucursales
AUX_PESO_1_MAX = 1500
AUX_PESO_2_MIN = 1500
AUX_SUC_2_MIN = 5


def _id_valido(doc_id: str) -> "str | None":
    try:
        return str(ObjectId(doc_id))
    except (InvalidId, TypeError):
        return None


# ── Helpers de párrafo ────────────────────────────────────────
def _p(txt, sz=SZ_DAT, bold=False, color=colors.black, align=TA_LEFT, italic=False):
    fn = ("Helvetica-Bold" if bold else
          "Helvetica-Oblique" if italic else "Helvetica")
    return Paragraph(
        str(txt),
        ParagraphStyle("_",
                       fontName=fn, fontSize=sz, leading=sz + 2,
                       textColor=color, alignment=align,
                       spaceBefore=0, spaceAfter=0),
    )

def _pc(t, **kw): return _p(t, align=TA_CENTER, **kw)
def _pr(t, **kw): return _p(t, align=TA_RIGHT, **kw)
def _hdr(label):  return _pc(label, sz=SZ_HDR, bold=True, color=C_NAVY)


def _auxiliares_para_ruta(peso_kg: float, n_suc: int) -> int:
    if peso_kg > AUX_PESO_2_MIN or n_suc >= AUX_SUC_2_MIN:
        return 2
    if peso_kg <= AUX_PESO_1_MAX or 2 <= n_suc <= 3:
        return 1
    return 0


# ── Encabezado fijo en canvas ─────────────────────────────────
def _draw_header(nombre_log, expedido):
    def draw(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica-Bold", 12)
        canvas.drawCentredString(PW / 2, PH - 25, "INTEGRADORA COMERCIAL DEL GOLFO")
        canvas.setFont("Helvetica", 9)
        canvas.drawCentredString(PW / 2, PH - 40, f"Logística del {nombre_log}")
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.grey)
        canvas.drawCentredString(PW / 2, PH - 52, f"Expedido: {expedido}")
        canvas.setFillColor(colors.black)
        canvas.setLineWidth(1)
        canvas.line(MARGEN, PH - 58, PW - MARGEN, PH - 58)
        canvas.restoreState()
    return draw


# ── Helpers para agrupación de mayoristas en PDF ─────────────────────────────

def _leer_poblaciones(db, id_clientes: list) -> dict:
    """Returns {id_cliente_int: poblacion_str_upper} from clientes_mayoristas."""
    if not id_clientes:
        return {}
    resultado: dict = {}
    try:
        tabla = get_table("clientes_mayoristas")
        for row in db.execute(
            select(tabla.c.id_cliente, tabla.c.poblacion).where(tabla.c.id_cliente.in_(id_clientes))
        ):
            id_cl = int(row.id_cliente or 0)
            pob   = str(row.poblacion or "").strip().upper()
            resultado[id_cl] = pob
    except Exception as e:
        print(f"[_leer_poblaciones] {e}")
    return resultado


def _formatear_docs_agrupados(docs: list) -> str:
    """
    Compresses a list of document codes into short form.
    ['BB2872', 'BB2873', 'BB2874'] -> 'BB2872/73/74'
    (first doc full, subsequent docs = last 2 digits of their numeric part)
    """
    if not docs:
        return ""
    if len(docs) == 1:
        return docs[0]

    first  = docs[0]
    prefix = re.match(r'^([A-Za-z]+)', first)
    pfx    = prefix.group(1).upper() if prefix else ""

    parts = [first]
    for doc in docs[1:]:
        if pfx and doc.upper().startswith(pfx):
            num_part = doc[len(pfx):]
            digits   = re.sub(r'\D', '', num_part)
            parts.append(digits[-2:] if len(digits) >= 2 else (digits or doc))
        else:
            parts.append(doc)
    return "/".join(parts)


def _abreviar_nombre(nombre: str) -> str:
    """Returns up to 3 words of a mayorista name, max 25 chars."""
    palabras = str(nombre or "").upper().split()
    abrev = " ".join(palabras[:3])
    return abrev[:25]


def _agrupar_may_por_poblacion(paradas: list, pob_map: dict) -> list:
    """
    Merges consecutive mayorista paradas that share the same poblacion into a
    single synthetic entry. Paradas with unknown poblacion get a Groq-inferred
    name appended. Returns a new paradas list ready for PDF row generation.

    Format:
      1 doc, known pob       : 'BB2872_AYOZINTEPEC'
      N docs, mismo cliente  : 'BB2872/73/74_CTES. NOMBRE_DESTINATARIO'
      N docs, distinto cte.  : 'BB2872/73/74_CTES. AYOZINTEPEC'
      unknown pob            : Groq fallback, or raw doc string

    Si varios documentos agrupados pertenecen al mismo numero de cliente,
    se muestra el nombre del destinatario en lugar de la poblacion (el
    cliente es mas especifico que la ciudad para identificar el pedido).
    """
    result: list = []
    i = 0
    while i < len(paradas):
        p = paradas[i]
        if p["_tipo"] != "mayorista":
            result.append(p)
            i += 1
            continue

        id_cl = int(p.get("id_cliente") or 0)
        pob   = pob_map.get(id_cl, "")

        # Accumulate consecutive mayoristas with the same non-empty poblacion
        group = [p]
        j = i + 1
        while j < len(paradas) and paradas[j]["_tipo"] == "mayorista":
            next_id  = int(paradas[j].get("id_cliente") or 0)
            next_pob = pob_map.get(next_id, "")
            if pob and next_pob == pob:
                group.append(paradas[j])
                j += 1
            else:
                break

        docs    = [str(m.get("documento") or m.get("nombre", "-")) for m in group]
        doc_str = _formatear_docs_agrupados(docs)

        if len(group) == 1:
            # Single document: show abbreviated recipient name
            abrev = _abreviar_nombre(p.get("nombre", ""))
            nombre_display = f"{doc_str}_{abrev}" if abrev else doc_str
            entry = dict(p)
            entry["_display_nombre"] = nombre_display
            result.append(entry)
        else:
            # Multiple consecutive docs from the same poblacion
            ids_grupo     = {int(m.get("id_cliente") or 0) for m in group}
            mismo_cliente = len(ids_grupo) == 1

            if mismo_cliente:
                # Mismos documentos de un solo cliente: usar el nombre del
                # destinatario en vez de la poblacion (mas especifico)
                nombre_dest    = _abreviar_nombre(group[0].get("nombre", ""))
                nombre_display = f"{doc_str}_CTES. {nombre_dest}" if nombre_dest else doc_str
            elif pob:
                nombre_display = f"{doc_str}_CTES. {pob}"
            else:
                nombres_may  = [str(m.get("nombre", "")) for m in group if m.get("nombre")]
                pob_inferida = generar_nombre_poblacion(nombres_may) if nombres_may else ""
                nombre_display = f"{doc_str}_CTES. {pob_inferida}" if pob_inferida else doc_str
            result.append({
                "_tipo":           "mayorista",
                "orden":           group[0].get("orden"),
                "peso_kg":         sum(float(m.get("peso_kg") or 0) for m in group),
                "_display_nombre": nombre_display,
                "_entregable":     all(m.get("_entregable", True) for m in group),
            })
        i = j

    return result


def _volumenes_suc(db, oid: str) -> dict:
    """Returns {num_tienda_int: total_m3} from extraccion.datos_volumen (columna JSON)."""
    resultado: dict = {}
    try:
        tabla = get_table("extraccion")
        fila = db.execute(select(tabla.c.datos_volumen).where(tabla.c.logistica_id == oid)).mappings().first()
        if fila and fila["datos_volumen"]:
            for _, valores in json.loads(fila["datos_volumen"]).items():
                id_suc = valores.get("id_sucursal")
                vol_m3 = valores.get("total_m3", 0)
                if id_suc is not None:
                    try:
                        resultado[int(id_suc)] = float(vol_m3)
                    except (TypeError, ValueError):
                        pass
    except Exception as e:
        print(f"[_volumenes_suc] {e}")
    return resultado


# ── Tabla de un vehículo ──────────────────────────────────────
def _tabla_vehiculo(veh_abrev: str, veh_placas: str, rutas: list,
                    veh_chofer: str = "", veh_ton: float = 0.0, db=None,
                    vol_map: "dict | None" = None,
                    cfg_tiempo: "dict | None" = None) -> list:
    rutas_ord = sorted(rutas, key=lambda r: ORDEN_DIA.get(r.get("dia", "").lower(), 99))

    conductor = veh_chofer or veh_placas
    ton_txt   = f" ({veh_ton:g} ton)" if veh_ton else ""
    enc_txt   = f"{veh_abrev}   ·   {conductor}{ton_txt}"
    fila_enc = [_pc(enc_txt, sz=SZ_ENC, bold=True, color=C_BLANCO)] + [""] * (NCOLS - 1)
    vol_map = vol_map or {}
    fila_hdr = [
        _hdr("DIA"), _hdr("APOYO"), _hdr("SEC."), _hdr("PARADA"),
        _pc("PESO\n(KG)", sz=SZ_HDR, bold=True, color=C_NAVY),
        _pc("VOL\n(m³)",  sz=SZ_HDR, bold=True, color=C_NAVY),
        _pc("%\nRUTA",    sz=SZ_HDR, bold=True, color=C_NAVY),
    ]

    data_rows   = []
    span_cmds   = []
    style_extra = []

    for ruta in rutas_ord:
        dia      = ruta.get("dia", "").lower()
        dia_lbl  = ABREV_DIA.get(dia, dia.upper())
        peso_ruta = float(ruta.get("peso_kg", 0))
        pct_util  = float(ruta.get("pct_utilizacion", 0))
        es_sub    = ruta.get("tipo", "") == "subruta"

        # Combinar sucursales + mayoristas en secuencia unificada
        sucs = [dict(p, _tipo="sucursal")  for p in ruta.get("sucursales", [])]
        mays = [dict(p, _tipo="mayorista") for p in ruta.get("mayoristas",  [])]

        sucs_ord = sorted(sucs, key=lambda p: p.get("orden") if p.get("orden") is not None else 9999)
        mays_ord = sorted(mays, key=lambda p: p.get("orden") if p.get("orden") is not None else 9999)

        # Detectar si el orden guardado ya entrelaza mayoristas con sucursales.
        # Si todos los mayoristas tienen orden > max(suc.orden), los datos son
        # antiguos (guardados antes de la corrección geográfica): se reordena
        # por proximidad. De lo contrario se respeta el orden guardado.
        if sucs_ord and mays_ord:
            max_suc = max(s.get("orden") or 0 for s in sucs_ord)
            min_may = min(m.get("orden") or 9999 for m in mays_ord)
            if min_may > max_suc:
                # Datos desactualizados: insertar mayoristas por proximidad geográfica
                paradas = list(sucs_ord)
                for m in mays_ord:
                    pos = _insertar_pos_proxima(paradas, m)
                    paradas.insert(pos, m)
                for i, p in enumerate(paradas, 1):
                    p["orden"] = i
            else:
                # Orden ya entrelazado: respetar el orden guardado
                paradas = sorted(sucs + mays, key=lambda p: p.get("orden") if p.get("orden") is not None else 9999)
        else:
            paradas = sorted(sucs + mays, key=lambda p: p.get("orden") if p.get("orden") is not None else 9999)

        # Fase A — tiempo de entrega: sobre las paradas ordenadas (antes de
        # agrupar), marcar las que no se alcanzan a entregar antes del cierre.
        if cfg_tiempo and cfg_tiempo.get("activo"):
            dcfg  = cfg_tiempo.get("dias", {}).get(dia, {})
            h_sal = hhmm_a_min(dcfg.get("hora_salida"), 420)
            h_lim = hhmm_a_min(dcfg.get("hora_limite"), 1080)
            depot = cfg_tiempo.get("depot")
            paradas_t = [{
                "latitud": p.get("latitud"), "longitud": p.get("longitud"),
                "peso_kg": p.get("peso_kg", 0),
                "es_mayorista": p["_tipo"] == "mayorista",
            } for p in paradas]
            # Traslado real por OSRM (cacheado): matriz→p1→…→pn→matriz. Una parada
            # sin coords repite el punto previo (tramo 0). Si OSRM falla, haversine.
            tramos = None
            try:
                pts, prev = [depot], depot
                for p in paradas:
                    la, lo = p.get("latitud"), p.get("longitud")
                    if la is not None and lo is not None:
                        prev = (float(la), float(lo))
                    pts.append(prev)
                pts.append(depot)
                r = consultar_osrm(pts)
                if "error" not in r and r.get("tramos_min"):
                    tramos = r["tramos_min"]
            except Exception:
                tramos = None
            evals = (evaluar_llegadas(paradas_t, tramos, h_sal, h_lim) if tramos
                     else evaluar_ruta_por_tiempo(paradas_t, depot, h_sal, h_lim,
                                                  cfg_tiempo.get("velocidad", 35.0)))
            for p, e in zip(paradas, evals):
                p["_entregable"] = e["entregable_por_tiempo"]

        # Agrupar mayoristas consecutivos de la misma poblacion (BB2872/73/74_CTES. AYOZINTEPEC)
        if db is not None and any(p["_tipo"] == "mayorista" for p in paradas):
            may_ids = list({int(p.get("id_cliente") or 0)
                            for p in paradas
                            if p["_tipo"] == "mayorista" and p.get("id_cliente")})
            pob_map = _leer_poblaciones(db, may_ids)
            paradas = _agrupar_may_por_poblacion(paradas, pob_map)

        n_suc = sum(1 for p in paradas if p["_tipo"] == "sucursal")
        aux_count = _auxiliares_para_ruta(peso_ruta, n_suc)
        aux_txt = str(aux_count) if aux_count else "—"

        row_start = 2 + len(data_rows)

        for i, p in enumerate(paradas):
            es_may = p["_tipo"] == "mayorista"
            entregable = p.get("_entregable", True)
            p_kg   = float(p.get("peso_kg", 0))
            pct_r  = (p_kg / peso_ruta * 100) if peso_ruta else 0

            if es_may:
                nombre = str(p.get("_display_nombre") or p.get("documento") or p.get("nombre", "-"))
                p_vol  = 0.0
            else:
                nombre = str(p.get("nombre", "-"))
                nt     = p.get("num_tienda")
                p_vol  = vol_map.get(int(nt), 0.0) if nt is not None else 0.0

            if not entregable:
                nombre = f"{nombre}  ·  FUERA DE HORARIO"

            vol_txt = f"{p_vol:.3f}" if p_vol > 0 else "—"
            # Rojo si no es entregable por tiempo; si no, naranja mayorista / negro sucursal.
            col = C_TARDE_TEXT if not entregable else (C_MAY_TEXT if es_may else colors.black)

            data_rows.append([
                _pc(dia_lbl, sz=SZ_DAT, bold=True) if i == 0 else "",
                _pc(aux_txt, sz=SZ_DAT, bold=True) if i == 0 else "",
                _pc(str(p.get("orden", i + 1)), sz=SZ_DAT, color=col),
                _p(nombre, sz=SZ_DAT, color=col, bold=es_may or (not entregable)),
                _pr(f"{int(p_kg):,}", sz=SZ_DAT, color=col),
                _pr(vol_txt, sz=SZ_DAT, color=col),
                _pc(f"{pct_r:.0f}%", sz=SZ_DAT, color=col),
            ])

            ridx = 2 + len(data_rows) - 1
            if not entregable:
                # Fondo rojo: parada que no se alcanza a entregar antes del cierre
                style_extra.append(("BACKGROUND", (0, ridx), (-1, ridx), C_TARDE_BG))
            elif es_may:
                # Fondo naranja para toda la fila de mayorista
                style_extra.append(("BACKGROUND", (0, ridx), (-1, ridx), C_MAY_BG))
            elif es_sub:
                style_extra.append(("BACKGROUND", (iSUC, ridx), (iPCT_R, ridx), C_SUBRUTA))

        row_end = 2 + len(data_rows) - 1
        if row_end > row_start:
            span_cmds += [
                ("SPAN", (iDIA,   row_start), (iDIA,   row_end)),
                ("SPAN", (iAPOYO, row_start), (iAPOYO, row_end)),
            ]

        # Etiqueta del totalizador por día
        n_may = sum(1 for p in paradas if p["_tipo"] == "mayorista")

        lbl_partes = [f"{n_suc} suc."]
        if n_may:
            lbl_partes.append(f"{n_may} may.")
        total_lbl = f"TOTAL {dia_lbl}  ·  " + "  +  ".join(lbl_partes)
        n_tarde = sum(1 for p in paradas if not p.get("_entregable", True))
        if n_tarde:
            total_lbl += f"  ·  {n_tarde} FUERA DE HORARIO"

        vol_ruta = sum(
            vol_map.get(int(p.get("num_tienda")), 0.0)
            for p in paradas
            if p["_tipo"] == "sucursal" and p.get("num_tienda") is not None
        )
        vol_ruta_txt = f"{vol_ruta:.3f}" if vol_ruta > 0 else "—"

        sob = pct_util > 100
        data_rows.append([
            "", "", "",
            _p(total_lbl, sz=SZ_TOT, bold=True),
            _pr(f"{int(peso_ruta):,}", sz=SZ_TOT, bold=True),
            _pr(vol_ruta_txt, sz=SZ_TOT, bold=True),
            _pc(f"{pct_util:.1f}%", sz=SZ_TOT, bold=True,
                color=C_ALERTA if sob else C_NAVY),
        ])
        sub_idx = 2 + len(data_rows) - 1
        # Subtotal naranja si la ruta tiene mayoristas, azul si no
        c_subtot = C_MAY_SUBTOT if n_may else C_SUBTOT
        style_extra += [
            ("BACKGROUND", (0, sub_idx), (-1, sub_idx), c_subtot),
            ("FONTNAME",   (0, sub_idx), (-1, sub_idx), "Helvetica-Bold"),
        ]

    all_rows = [fila_enc, fila_hdr] + data_rows
    n        = len(all_rows)

    base_style = [
        ("SPAN",          (0, 0),   (-1, 0)),
        ("BACKGROUND",    (0, 0),   (-1, 0),  C_ENC_VEH),
        ("TEXTCOLOR",     (0, 0),   (-1, 0),  C_BLANCO),
        ("FONTNAME",      (0, 0),   (-1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0),   (-1, 0),  SZ_ENC),
        ("ALIGN",         (0, 0),   (-1, 0),  "CENTER"),
        ("VALIGN",        (0, 0),   (-1, 0),  "MIDDLE"),
        ("TOPPADDING",    (0, 0),   (-1, 0),  5),
        ("BOTTOMPADDING", (0, 0),   (-1, 0),  5),
        ("BACKGROUND",    (0, 1),   (-1, 1),  C_HDR_COL),
        ("FONTNAME",      (0, 1),   (-1, 1),  "Helvetica-Bold"),
        ("TEXTCOLOR",     (0, 1),   (-1, 1),  C_NAVY),
        ("BACKGROUND",    (iDIA,   2), (iDIA,   -1), C_DIA_BG),
        ("BACKGROUND",    (iAPOYO, 2), (iAPOYO, -1), C_DIA_BG),
        ("GRID",          (0, 0),   (-1, -1), 0.4, C_BORDE),
        ("FONTSIZE",      (0, 1),   (-1, -1), SZ_HDR),
        ("ALIGN",         (0, 1),   (-1, -1), "CENTER"),
        ("ALIGN",         (iSUC, 2),  (iSUC, -1),  "LEFT"),
        ("ALIGN",         (iPESO, 2), (iPESO, -1), "RIGHT"),
        ("ALIGN",         (iVOL, 2),  (iVOL, -1),  "RIGHT"),
        ("VALIGN",        (0, 0),   (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 1),   (-1, -1), 3),
        ("BOTTOMPADDING", (0, 1),   (-1, -1), 3),
        ("LEFTPADDING",   (0, 0),   (-1, -1), 2),
        ("RIGHTPADDING",  (0, 0),   (-1, -1), 2),
    ]

    t = Table(all_rows, colWidths=CW,
              rowHeights=[None, 28] + [None] * (n - 2),
              repeatRows=2)
    t.setStyle(TableStyle(base_style + span_cmds + style_extra))
    return [KeepTogether([t, Spacer(1, 8)])]


# ── Función principal ─────────────────────────────────────────
# NOTA: `_mayoristas_por_ruta_db()` (leía `distribucion_mayoristas`) se
# eliminó en la migración a SQL Server -- estaba definida pero sin ninguna
# llamada en todo el repo (código muerto, confirmado por búsqueda exhaustiva
# antes de migrar). El flujo real de mayoristas para el fallback de
# asignaciones ya pasa por `calcular_distribucion_mayoristas()`.

def _rutas_desde_asignaciones(db, oid) -> list:
    """
    Construye la lista de rutas para el PDF a partir de `asignaciones_rutas`
    + `asignaciones_sucursales` (guardadas en el Paso 3 — Asignación).

    Se usa como fallback cuando `modificaciones_rutas` no existe, permitiendo
    generar el PDF sin haber pasado por la etapa de Modificación.
    Incluye mayoristas vía `calcular_distribucion_mayoristas()`.
    """
    tabla_ar = get_table("asignaciones_rutas")
    filas_rutas = db.execute(select(tabla_ar).where(tabla_ar.c.logistica_id == oid)).mappings().all()
    if not filas_rutas:
        return []

    coords_map: dict = {}
    try:
        tabla_rcs = get_table("rutas_config_sucursales")
        for suc in db.execute(select(tabla_rcs.c.num_tienda, tabla_rcs.c.latitud, tabla_rcs.c.longitud)):
            nt = str(suc.num_tienda or "")
            if nt and suc.latitud is not None and suc.longitud is not None:
                coords_map[nt] = (float(suc.latitud), float(suc.longitud))
    except Exception:
        coords_map = {}

    chofer_overrides: dict = {}
    t_chov = get_table("asignaciones_chofer_overrides")
    for r in db.execute(select(t_chov).where(t_chov.c.logistica_id == oid)):
        chofer_overrides[r.ruta_key] = {"nombre": r.nombre, "chofer_id": r.chofer_id}

    tabla_as = get_table("asignaciones_sucursales")
    sucs_por_ruta: dict = {}
    for s in db.execute(select(tabla_as).where(tabla_as.c.logistica_id == oid)):
        sucs_por_ruta.setdefault(s.ruta_key, []).append(s)

    rutas: list = []
    rutas_base: list = []
    for info in filas_rutas:
        ruta_id = info["ruta_key"]
        dia     = info["dia_semana"]

        suc_list = []
        for i, s in enumerate(sucs_por_ruta.get(ruta_id, [])):
            nt = s.num_tienda
            lat, lon = None, None
            if nt is not None:
                coord = coords_map.get(str(nt))
                if coord:
                    lat, lon = coord
            suc_list.append({
                "tipo":     "sucursal",
                "num_tienda": nt,
                "orden":    s.orden if s.orden is not None else i + 1,
                "nombre":   s.nombre or "—",
                "peso_kg":  float(s.peso_kg or 0),
                "latitud":  lat,
                "longitud": lon,
            })

        rutas.append({
            "id":               ruta_id,
            "nombre":           info["nombre_ruta"] or "",
            "tipo":             "autorizada",
            "dia":              dia,
            "vehiculo_abrev":   info["vehiculo_abreviatura"] or "S/N",
            "vehiculo_placas":  info["vehiculo_placas"] or "—",
            "capacidad_ton":    float(info["capacidad_ton"] or 0),
            "peso_kg":          float(info["peso_total_kg"] or 0),
            "pct_utilizacion":  float(info["porcentaje_utilizacion"] or 0),
            "chofer_override":  chofer_overrides.get(ruta_id),
            "sucursales":       suc_list,
            "mayoristas":       [],
        })

        rutas_base.append({
            "_id":        ruta_id,
            "sucursales": suc_list,
            "cap_ton":    info["capacidad_ton"],
        })

    if rutas_base:
        dist = calcular_distribucion_mayoristas(str(oid), rutas_base)
        may_por_ruta = dist.get("mayoristas_por_ruta", {})
        orden_suc_dist = dist.get("orden_sucursales", {})
        for r in rutas:
            rid = r.get("id", "")
            may_list = may_por_ruta.get(rid, [])
            r["mayoristas"] = may_list
            if may_list:
                peso_may = sum(float(m.get("peso_kg") or 0) for m in may_list)
                r["peso_kg"] = float(r.get("peso_kg") or 0) + peso_may
                cap_ton = float(r.get("capacidad_ton") or 0)
                if cap_ton > 0:
                    r["pct_utilizacion"] = round((r["peso_kg"] / 1000 / cap_ton) * 100, 1)
            # Actualizar orden de sucursales al entrelazado calculado geográficamente
            suc_ord_map = orden_suc_dist.get(rid, {})
            for s in r.get("sucursales", []):
                nt = str(s.get("num_tienda", ""))
                if nt in suc_ord_map:
                    s["orden"] = suc_ord_map[nt]
    return rutas


def _filtrar_mayoristas_con_pedidos(rutas: list) -> None:
    """
    Excluye mayoristas del reporte cuyos pedidos no tienen peso registrado.
    Solo se mantienen los mayoristas con peso_kg mayor que 0.
    """
    for ruta in rutas:
        mayoristas = ruta.get("mayoristas")
        if not isinstance(mayoristas, list):
            continue
        ruta["mayoristas"] = [
            m for m in mayoristas
            if float(m.get("peso_kg", 0) or 0) > 0
        ]


def _pesos_mayoristas(db, oid: str) -> dict:
    """
    Lee `extraccion.mayoristas` (columna JSON) y devuelve {documento_str: peso_kg}.
    Se usa para enriquecer los mayoristas cuyo peso_kg llegó como 0 desde
    la distribución calculada (que no almacena pesos individuales para el
    camino de `asignaciones`).
    """
    resultado: dict = {}
    try:
        tabla = get_table("extraccion")
        fila = db.execute(select(tabla.c.mayoristas).where(tabla.c.logistica_id == oid)).mappings().first()
        if fila and fila["mayoristas"]:
            for m in json.loads(fila["mayoristas"]):
                doc = str(m.get("documento") or m.get("codigo") or m.get("id_cliente") or "")
                if doc:
                    try:
                        resultado[doc] = float(m.get("peso_total_kg", 0) or 0)
                    except (TypeError, ValueError):
                        pass
    except Exception as e:
        print(f"[_pesos_mayoristas] {e}")
    return resultado


def generar_pdf(datos_sesion: dict) -> str:
    """
    Genera el reporte PDF de pesos.

    Fuente de datos (en orden de preferencia):
      1. `modificaciones_rutas` (+ tablas normalizadas) — datos confirmados
         tras la etapa de Modificación.
      2. `asignaciones` (+ tablas normalizadas) — fallback si Modificación
         no fue guardada. Permite generar el PDF directamente después de la
         etapa de Asignación.

    Devuelve la ruta absoluta al PDF generado en static/temp/.
    """
    os.makedirs(TEMP_DIR, exist_ok=True)

    logistica_id = datos_sesion.get("id")
    oid = _id_valido(logistica_id) if logistica_id else None
    if not oid:
        raise ValueError("No hay logística activa o su ID es inválido.")

    db  = get_db()

    # ── 1. Intentar leer desde modificaciones_rutas ───────────────
    mod_doc = obtener_modificacion_previa(oid)
    rutas: list = mod_doc.get("rutas_confirmadas", []) if mod_doc else []

    # ── 2. Fallback a asignaciones si no hay modificaciones ───────
    if not rutas:
        rutas = _rutas_desde_asignaciones(db, oid)

    if not rutas:
        raise FileNotFoundError(
            "No se encontraron datos para generar el reporte. "
            "Completa al menos la etapa de Asignación (Paso 3) y guarda antes de generar el PDF."
        )

    # ── 3. Enriquecer peso_kg de mayoristas desde extraccion ─────
    # La distribución calculada no guarda pesos individuales, por lo que
    # los mayoristas suelen llegar con peso_kg=0. Se corrige leyendo
    # extraccion.mayoristas (columna JSON, campo peso_total_kg).
    pesos_may = _pesos_mayoristas(db, oid)
    if pesos_may:
        for ruta in rutas:
            for m in ruta.get("mayoristas", []):
                if not m.get("peso_kg"):
                    doc = str(m.get("documento") or m.get("id_cliente") or "")
                    m["peso_kg"] = pesos_may.get(doc, 0.0)

    _filtrar_mayoristas_con_pedidos(rutas)

    vol_map = _volumenes_suc(db, oid)

    nombre_log = datos_sesion.get("nombre", "Logística")
    f_ini      = datos_sesion.get("fecha_inicio", "")
    f_fin      = datos_sesion.get("fecha_fin", "")
    expedido   = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    rango_log  = (f"{nombre_log}  {f_ini} — {f_fin}"
                  if f_ini and f_fin else nombre_log)

    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = os.path.join(TEMP_DIR, f"{ts}.pdf")

    doc_pdf = BaseDocTemplate(
        filepath,
        pagesize=portrait(LETTER),
        rightMargin=MARGEN, leftMargin=MARGEN,
        topMargin=65, bottomMargin=MARGEN,
        title=f"Reporte — {nombre_log}",
        author="Sistema ICG",
    )

    frame_izq = Frame(
        MARGEN, MARGEN, ANCHO_COL, PH - 75,
        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
        id="col_izq", showBoundary=0,
    )
    frame_der = Frame(
        MARGEN + ANCHO_COL + ESPACIO_ENTRE_COLS, MARGEN, ANCHO_COL, PH - 75,
        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
        id="col_der", showBoundary=0,
    )

    doc_pdf.addPageTemplates([
        PageTemplate(
            id="DosColumnas",
            frames=[frame_izq, frame_der],
            onPage=_draw_header(rango_log, expedido),
        )
    ])

    # Mapa placas → chofer desde la tabla `vehiculos` (solo activo=True
    # estricto, a propósito distinto del "$ne:False" usado en otros módulos)
    chofer_por_placas: dict = {}
    try:
        tabla_veh = get_table("vehiculos")
        for v in db.execute(
            select(tabla_veh.c.placas, tabla_veh.c.chofer, tabla_veh.c.capacidad_toneladas)
            .where(tabla_veh.c.activo == True)  # noqa: E712
        ):
            plac = v.placas or ""
            if plac:
                chofer_por_placas[plac] = {
                    "chofer": v.chofer or "",
                    "ton":    float(v.capacidad_toneladas or 0),
                }
    except Exception as e:
        print(f"[generar_pdf] Error al leer choferes: {e}")

    # Fase A — config de tiempo de entrega (para marcar paradas fuera de horario).
    # Degradación segura: ante cualquier error, no se marca nada.
    cfg_tiempo = None
    if TIEMPO_ENTREGA_ESTRICTO:
        try:
            cfg_row = db.execute(select(get_table("configuracion"))).mappings().first() or {}
            depot = (float(cfg_row.get("matriz_lat") or 18.87329315661368),
                     float(cfg_row.get("matriz_lon") or -96.9491574270346))
            cd = cfg_row.get("config_dias")
            cd = json.loads(cd) if isinstance(cd, str) else (cd or {})
            cfg_tiempo = {
                "activo":    True,
                "depot":     depot,
                "velocidad": float(cfg_row.get("velocidad_kmh") or 35.0),
                "dias":      cd,
            }
        except Exception as e:  # noqa: BLE001
            print(f"[generar_pdf] tiempo de entrega desactivado por error: {e}")
            cfg_tiempo = None

    # Agrupar por (vehículo, chofer efectivo): una ruta con chofer
    # personalizado (cambiado solo para ese día/ruta en Modificación) recibe
    # su propio bloque dentro del mismo vehículo, sin afectar las demás rutas.
    grupos: dict = {}
    for r in rutas:
        veh  = r.get("vehiculo_abrev") or "S/N"
        plac = r.get("vehiculo_placas") or "—"
        veh_info = chofer_por_placas.get(plac, {})
        chofer_default = veh_info.get("chofer", "") if isinstance(veh_info, dict) else ""
        ton            = veh_info.get("ton", 0.0)   if isinstance(veh_info, dict) else 0.0
        chofer_efectivo = r.get("chofer_override") or r.get("chofer") or chofer_default

        clave = (veh, chofer_efectivo)
        if clave not in grupos:
            grupos[clave] = {
                "veh": veh, "placas": plac, "chofer": chofer_efectivo, "ton": ton, "rutas": [],
            }
        grupos[clave]["rutas"].append(r)

    elements = []
    for clave in sorted(grupos, key=lambda k: (k[0] or "", k[1] or "")):
        info = grupos[clave]
        elements.extend(_tabla_vehiculo(info["veh"], info["placas"], info["rutas"], info["chofer"], info["ton"], db, vol_map, cfg_tiempo))

    doc_pdf.build(elements)
    return filepath
