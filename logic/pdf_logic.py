"""
logic/pdf_logic.py
Reporte de pesos — paleta azul, diseño compacto a dos columnas.

Fuente de datos: colección `modificaciones_rutas` (MongoDB),
filtrada por logistica_id proveniente de la sesión Flask.

No se leen archivos JSON.
"""
import os
from datetime import datetime
from bson import ObjectId
from bson.errors import InvalidId

from reportlab.lib.pagesizes import LETTER, portrait
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.platypus import (
    BaseDocTemplate, PageTemplate, Frame,
    Table, TableStyle, Paragraph, Spacer, KeepTogether,
)

from db import get_db
from logic.mayoristas_logic import calcular_distribucion_mayoristas

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
CW = [38, 28, 20, 147, 26, 27]
iDIA, iAPOYO, iSEC, iSUC, iPESO, iPCT_R = range(6)
NCOLS = 6

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


def _parse_oid(doc_id: str) -> ObjectId | None:
    try:
        return ObjectId(doc_id)
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


# ── Tabla de un vehículo ──────────────────────────────────────
def _tabla_vehiculo(veh_abrev: str, veh_placas: str, rutas: list,
                    veh_chofer: str = "", veh_ton: float = 0.0) -> list:
    rutas_ord = sorted(rutas, key=lambda r: ORDEN_DIA.get(r.get("dia", "").lower(), 99))

    conductor = veh_chofer or veh_placas
    ton_txt   = f" ({veh_ton:g} ton)" if veh_ton else ""
    enc_txt   = f"{veh_abrev}   ·   {conductor}{ton_txt}"
    fila_enc = [_pc(enc_txt, sz=SZ_ENC, bold=True, color=C_BLANCO)] + [""] * (NCOLS - 1)
    fila_hdr = [
        _hdr("DIA"), _hdr("APOYO"), _hdr("SEC."), _hdr("PARADA"),
        _pc("PESO\n(KG)", sz=SZ_HDR, bold=True, color=C_NAVY),
        _pc("%\nRUTA",   sz=SZ_HDR, bold=True, color=C_NAVY),
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

        # Combinar sucursales + mayoristas en secuencia unificada por orden
        sucs = [dict(p, _tipo="sucursal")  for p in ruta.get("sucursales", [])]
        mays = [dict(p, _tipo="mayorista") for p in ruta.get("mayoristas",  [])]
        paradas = sorted(
            sucs + mays,
            key=lambda p: p.get("orden") if p.get("orden") is not None else 9999,
        )

        n_suc = sum(1 for p in paradas if p["_tipo"] == "sucursal")
        aux_count = _auxiliares_para_ruta(peso_ruta, n_suc)
        aux_txt = str(aux_count) if aux_count else "—"

        row_start = 2 + len(data_rows)

        for i, p in enumerate(paradas):
            es_may = p["_tipo"] == "mayorista"
            p_kg   = float(p.get("peso_kg", 0))
            pct_r  = (p_kg / peso_ruta * 100) if peso_ruta else 0

            nombre = str(p.get("nombre", "—"))
            if es_may:
                pass  # sin prefijo adicional

            data_rows.append([
                _pc(dia_lbl, sz=SZ_DAT, bold=True) if i == 0 else "",
                _pc(aux_txt, sz=SZ_DAT, bold=True) if i == 0 else "",
                _pc(str(p.get("orden", i + 1)), sz=SZ_DAT,
                    color=C_MAY_TEXT if es_may else colors.black),
                _p(nombre, sz=SZ_DAT,
                   color=C_MAY_TEXT if es_may else colors.black,
                   bold=es_may),
                _pr(f"{int(p_kg):,}", sz=SZ_DAT,
                    color=C_MAY_TEXT if es_may else colors.black),
                _pc(f"{pct_r:.0f}%", sz=SZ_DAT,
                    color=C_MAY_TEXT if es_may else colors.black),
            ])

            ridx = 2 + len(data_rows) - 1
            if es_may:
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

        sob = pct_util > 100
        data_rows.append([
            "", "", "",
            _p(total_lbl, sz=SZ_TOT, bold=True),
            _pr(f"{int(peso_ruta):,}", sz=SZ_TOT, bold=True),
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
        ("ALIGN",         (iSUC, 2), (iSUC, -1), "LEFT"),
        ("ALIGN",         (iPESO, 2),(iPESO,-1), "RIGHT"),
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
def _mayoristas_por_ruta_db(db) -> dict:
    """
    Lee `distribucion_mayoristas` y devuelve un mapa
    { ruta_id_str: [ { nombre, peso_kg, orden, tipo:'mayorista' } ] }.

    Se usa para enriquecer el fallback que lee desde `asignaciones`
    (en ese camino las rutas no tienen mayoristas embebidos).
    """
    resultado: dict = {}
    try:
        dist = db["distribucion_mayoristas"].find_one({"_key": "ultimo"})
        if not dist:
            return resultado
        for ruta in dist.get("rutas", []):
            rid  = str(ruta.get("_id", ""))
            mays = []
            for p in ruta.get("paradas_integradas", []):
                if p.get("tipo") != "mayorista":
                    continue
                id_cl = p.get("id_cliente")
                mays.append({
                    "tipo":       "mayorista",
                    "id_cliente": id_cl,
                    "nombre":     p.get("nombre_base") or p.get("nombre", ""),
                    "peso_kg":    float(p.get("peso_kg", 0)),
                    "orden":      p.get("orden"),
                })
            if mays:
                resultado[rid] = mays
    except Exception as e:
        print(f"[_mayoristas_por_ruta_db] Error: {e}")
    return resultado


def _rutas_desde_asignaciones(db, oid) -> list:
    """
    Construye la lista de rutas para el PDF a partir de la colección
    `asignaciones` (guardada en el Paso 3 — Asignación).

    Se usa como fallback cuando `modificaciones_rutas` no existe, permitiendo
    generar el PDF sin haber pasado por la etapa de Modificación.
    Incluye mayoristas desde `distribucion_mayoristas`.
    """
    doc = db["asignaciones"].find_one({"logistica_id": oid})
    if not doc:
        return []

    coords_map: dict = {}
    try:
        for rdoc in db["rutas_config"].find({}, {"sucursales": 1}):
            for suc in rdoc.get("sucursales", []):
                nt = str(suc.get("num_tienda", ""))
                lat = suc.get("latitud")
                lon = suc.get("longitud")
                if nt and lat is not None and lon is not None:
                    coords_map[nt] = (float(lat), float(lon))
    except Exception:
        coords_map = {}

    rutas: list = []
    rutas_base: list = []
    detalle = doc.get("detalle_por_dia", {})
    for dia, rutas_dia in detalle.items():
        if not isinstance(rutas_dia, dict):
            continue
        for ruta_id, info in rutas_dia.items():
            if not isinstance(info, dict):
                continue
            suc_list = []
            for i, s in enumerate(info.get("sucursales", [])):
                nt = s.get("num_tienda")
                lat, lon = None, None
                if nt is not None:
                    coord = coords_map.get(str(nt))
                    if coord:
                        lat, lon = coord
                suc_list.append({
                    "tipo":    "sucursal",
                    "num_tienda": nt,
                    "orden":   s.get("orden", i + 1),
                    "nombre":  s.get("nombre") or s.get("nombre_tienda") or s.get("nombre_pedido") or "—",
                    "peso_kg": float(s.get("peso_kg", 0) or 0),
                    "latitud": lat,
                    "longitud": lon,
                })

            rutas.append({
                "id":               ruta_id,
                "nombre":           info.get("nombre_ruta", ""),
                "tipo":             "autorizada",
                "dia":              dia,
                "vehiculo_abrev":   info.get("vehiculo_abreviatura") or "S/N",
                "vehiculo_placas":  info.get("vehiculo_placas") or "—",
                "capacidad_ton":    float(info.get("capacidad_ton") or 0),
                "peso_kg":          float(info.get("peso_total_kg") or 0),
                "pct_utilizacion":  float(info.get("porcentaje_utilizacion") or 0),
                "sucursales":       suc_list,
                "mayoristas":       [],
            })

            rutas_base.append({"_id": ruta_id, "sucursales": suc_list})

    if rutas_base:
        dist = calcular_distribucion_mayoristas(str(oid), rutas_base)
        may_por_ruta = dist.get("mayoristas_por_ruta", {})
        for r in rutas:
            may_list = may_por_ruta.get(r.get("id", ""), [])
            r["mayoristas"] = may_list
            if may_list:
                peso_may = sum(float(m.get("peso_kg") or 0) for m in may_list)
                r["peso_kg"] = float(r.get("peso_kg") or 0) + peso_may
                cap_ton = float(r.get("capacidad_ton") or 0)
                if cap_ton > 0:
                    r["pct_utilizacion"] = round((r["peso_kg"] / 1000 / cap_ton) * 100, 1)
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


def _pesos_mayoristas(db, oid: ObjectId) -> dict:
    """
    Lee `extraccion.mayoristas` y devuelve {id_cliente_int: peso_kg}.
    Se usa para enriquecer los mayoristas cuyo peso_kg llegó como 0 desde
    `distribucion_mayoristas` (que no almacena pesos individuales).
    """
    resultado: dict = {}
    try:
        ext = db["extraccion"].find_one({"logistica_id": oid})
        if ext:
            for m in ext.get("mayoristas", []):
                id_cl = m.get("codigo") or m.get("id_cliente")
                try:
                    resultado[int(id_cl)] = float(m.get("peso_total_kg", 0) or 0)
                except (TypeError, ValueError):
                    pass
    except Exception as e:
        print(f"[_pesos_mayoristas] {e}")
    return resultado


def generar_pdf(datos_sesion: dict) -> str:
    """
    Genera el reporte PDF de pesos.

    Fuente de datos (en orden de preferencia):
      1. `modificaciones_rutas` — datos confirmados tras la etapa de Modificación.
      2. `asignaciones`         — fallback si Modificación no fue guardada.
         Permite generar el PDF directamente después de la etapa de Asignación.

    Devuelve la ruta absoluta al PDF generado en static/temp/.
    """
    os.makedirs(TEMP_DIR, exist_ok=True)

    logistica_id = datos_sesion.get("id")
    oid = _parse_oid(logistica_id) if logistica_id else None
    if not oid:
        raise ValueError("No hay logística activa o su ID es inválido.")

    db  = get_db()

    # ── 1. Intentar leer desde modificaciones_rutas ───────────────
    doc  = db["modificaciones_rutas"].find_one({"logistica_id": oid})
    rutas: list = doc.get("rutas_confirmadas", []) if doc else []

    # ── 2. Fallback a asignaciones si no hay modificaciones ───────
    if not rutas:
        rutas = _rutas_desde_asignaciones(db, oid)

    if not rutas:
        raise FileNotFoundError(
            "No se encontraron datos para generar el reporte. "
            "Completa al menos la etapa de Asignación (Paso 3) y guarda antes de generar el PDF."
        )

    # ── 3. Enriquecer peso_kg de mayoristas desde extraccion ─────
    # distribucion_mayoristas no guarda pesos individuales, por lo que
    # los mayoristas suelen llegar con peso_kg=0.  Se corrige leyendo
    # la colección extraccion (campo mayoristas[].peso_total_kg).
    pesos_may = _pesos_mayoristas(db, oid)
    if pesos_may:
        for ruta in rutas:
            for m in ruta.get("mayoristas", []):
                if not m.get("peso_kg"):
                    id_cl = m.get("id_cliente")
                    try:
                        m["peso_kg"] = pesos_may.get(int(id_cl), 0.0)
                    except (TypeError, ValueError):
                        pass

    _filtrar_mayoristas_con_pedidos(rutas)

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

    # Mapa placas → chofer desde la colección `vehiculos`
    chofer_por_placas: dict = {}
    try:
        for v in db["vehiculos"].find({"activo": True}, {"placas": 1, "chofer": 1, "capacidad_toneladas": 1}):
            plac = v.get("placas", "")
            if plac:
                chofer_por_placas[plac] = {
                    "chofer": v.get("chofer", "") or "",
                    "ton":    float(v.get("capacidad_toneladas") or 0),
                }
    except Exception as e:
        print(f"[generar_pdf] Error al leer choferes: {e}")

    grupos: dict = {}
    for r in rutas:
        veh  = r.get("vehiculo_abrev") or "S/N"
        plac = r.get("vehiculo_placas") or "—"
        if veh not in grupos:
            veh_info  = chofer_por_placas.get(plac, {})
            grupos[veh] = {
                "placas": plac,
                "chofer": veh_info.get("chofer", "") if isinstance(veh_info, dict) else "",
                "ton":    veh_info.get("ton", 0.0)   if isinstance(veh_info, dict) else 0.0,
                "rutas":  [],
            }
        grupos[veh]["rutas"].append(r)

    elements = []
    for veh in sorted(grupos, key=lambda v: v or ""):
        info = grupos[veh]
        elements.extend(_tabla_vehiculo(veh, info["placas"], info["rutas"], info["chofer"], info["ton"]))

    doc_pdf.build(elements)
    return filepath