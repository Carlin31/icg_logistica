"""
app_rutas.py – Sistema de Rutas Logísticas con VRP Híbrido
Ejecutar con:  streamlit run app_rutas.py
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import io
from math import radians, sin, cos, sqrt, atan2
from collections import defaultdict

# ─────────────────────────────────────── CONFIGURACIÓN ───────────
st.set_page_config(
    page_title="LogiRutas VRP",
    page_icon="🚚",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────── CSS ──────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Syne:wght@400;600;700;800&family=DM+Sans:ital,wght@0,300;0,400;0,500;1,300&display=swap');
:root {
  --bg:       #0d0f14;
  --surface:  #14181f;
  --card:     #1b2029;
  --border:   #252c38;
  --accent:   #f5a623;
  --accent2:  #3ecfcf;
  --accent3:  #e05c5c;
  --text:     #e8eaf0;
  --muted:    #7a8290;
  --green:    #4ecb82;
}
html, body, [data-testid="stAppViewContainer"], [data-testid="stApp"] {
    background-color: var(--bg) !important;
    color: var(--text) !important;
    font-family: 'DM Sans', sans-serif;
}
[data-testid="stSidebar"] {
    background: var(--surface) !important;
    border-right: 1px solid var(--border);
}
[data-testid="stSidebar"] * { color: var(--text) !important; }
#MainMenu, footer, header { visibility: hidden; }
h1, h2, h3 { font-family: 'Syne', sans-serif !important; }
[data-testid="stMetric"] {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 16px 20px;
}
[data-testid="stMetricLabel"] { color: var(--muted) !important; font-size: 0.8rem !important; }
[data-testid="stMetricValue"] { color: var(--accent) !important; font-family: 'Syne', sans-serif !important; }
.stButton > button {
    background: var(--accent) !important;
    color: #0d0f14 !important;
    border: none !important;
    font-family: 'Syne', sans-serif !important;
    font-weight: 700 !important;
    border-radius: 8px !important;
    padding: 10px 24px !important;
    letter-spacing: 0.5px;
    transition: all 0.2s;
}
.stButton > button:hover { background: #f8ba50 !important; box-shadow: 0 0 16px rgba(245,166,35,0.4) !important; }
[data-testid="stDownloadButton"] > button {
    background: transparent !important;
    color: var(--accent2) !important;
    border: 1px solid var(--accent2) !important;
    font-family: 'Syne', sans-serif !important;
    font-weight: 600 !important;
    border-radius: 8px !important;
}
[data-testid="stDownloadButton"] > button:hover { background: rgba(62,207,207,0.1) !important; }
[data-testid="stDataFrame"] { border: 1px solid var(--border); border-radius: 10px; overflow: hidden; }
[data-testid="stFileUploader"] {
    background: var(--card) !important;
    border: 1.5px dashed var(--border) !important;
    border-radius: 12px !important;
    padding: 8px !important;
}
[data-testid="stTabs"] [role="tab"] {
    font-family: 'Syne', sans-serif !important;
    font-weight: 600 !important;
    color: var(--muted) !important;
    border-bottom: 2px solid transparent !important;
}
[data-testid="stTabs"] [role="tab"][aria-selected="true"] {
    color: var(--accent) !important;
    border-bottom: 2px solid var(--accent) !important;
}
[data-testid="stExpander"] {
    background: var(--card) !important;
    border: 1px solid var(--border) !important;
    border-radius: 10px !important;
}
[data-testid="stSelectbox"] > div, [data-testid="stMultiSelect"] > div {
    background: var(--card) !important;
    border: 1px solid var(--border) !important;
    border-radius: 8px !important;
    color: var(--text) !important;
}
[data-testid="stAlert"] { border-radius: 10px !important; }
.stat-card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 20px 24px;
    margin-bottom: 12px;
}
.stat-card h4 { font-family: 'Syne', sans-serif; color: var(--muted); font-size: 0.78rem; margin: 0 0 4px; text-transform: uppercase; letter-spacing: 1px; }
.stat-card .val { font-family: 'Syne', sans-serif; font-size: 2rem; font-weight: 800; color: var(--accent); margin: 0; line-height: 1; }
.stat-card .sub { color: var(--muted); font-size: 0.8rem; margin-top: 4px; }
.sim-badge {
    display: inline-block;
    padding: 6px 18px;
    border-radius: 100px;
    font-family: 'Syne', sans-serif;
    font-weight: 700;
    font-size: 1.4rem;
}
.sim-high  { background: rgba(78,203,130,0.15); color: #4ecb82; border: 1px solid #4ecb82; }
.sim-mid   { background: rgba(245,166,35,0.15);  color: #f5a623; border: 1px solid #f5a623; }
.sim-low   { background: rgba(224,92,92,0.15);   color: #e05c5c; border: 1px solid #e05c5c; }
.section-title {
    font-family: 'Syne', sans-serif;
    font-size: 1.05rem;
    font-weight: 700;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 2px;
    margin: 24px 0 12px;
    border-left: 3px solid var(--accent);
    padding-left: 12px;
}
.logo-text { font-family: 'Syne', sans-serif; font-size: 1.7rem; font-weight: 800; color: var(--accent); letter-spacing: -0.5px; }
.logo-sub  { font-size: 0.78rem; color: var(--muted); letter-spacing: 1px; text-transform: uppercase; }
.vrp-pill  {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 100px;
    font-size: 0.75rem;
    font-weight: 700;
    font-family: 'Syne', sans-serif;
}
</style>
""", unsafe_allow_html=True)

# ═══════════════════════════════════════ DATOS DEL SISTEMA ════════

VEHICULOS_CAP = {
    "F 350_1": 3500, "F 350_2": 3500, "F 350_3": 3500,
    "K 16": 2500,    "K 20": 2500,
    "T 17_1": 2500,  "T 17_2": 2500,
    "T 20": 1500,    "T 23": 1500,    "T 25": 1300,
    "J 19": 2500,    "J 18": 2500,    "KANGOO": 700,
}

TEMPLATE = {
    1:('K 16','LUNES',3), 2:('F 350_2','MARTES',1), 3:('T 17_2','MIERCOLES',1),
    4:('F 350_1','MARTES',1), 5:('K 16','JUEVES',1), 6:('K 16','MIERCOLES',3),
    7:('F 350_2','MARTES',4), 8:('T 17_2','JUEVES',1), 9:('T 23','MARTES',2),
    10:('K 16','MIERCOLES',2), 11:('F 350_3','MARTES',1), 12:('K 16','JUEVES',3),
    13:('T 20','MIERCOLES',2), 14:('J 19','MIERCOLES',1), 15:('F 350_2','MARTES',6),
    16:('F 350_1','JUEVES',3), 17:('T 23','LUNES',1), 18:('F 350_2','JUEVES',1),
    19:('F 350_2','JUEVES',5), 20:('T 20','MARTES',1), 21:('F 350_1','JUEVES',1),
    22:('F 350_1','JUEVES',5), 23:('T 20','MARTES',1), 24:('K 16','LUNES',2),
    25:('K 16','LUNES',1), 26:('T 23','LUNES',2), 27:('F 350_1','MARTES',2),
    28:('T 17_1','LUNES',3), 29:('T 17_1','LUNES',1), 30:('T 17_1','LUNES',2),
    31:('F 350_2','MARTES',2), 32:('T 23','VIERNES',1), 33:('F 350_2','JUEVES',4),
    34:('T 23','LUNES',3), 35:('T 17_1','MIERCOLES',1), 36:('K 16','LUNES',2),
    37:('K 16','JUEVES',2), 38:('F 350_2','JUEVES',3), 39:('F 350_3','JUEVES',4),
    40:('T 17_1','VIERNES',3), 41:('T 20','VIERNES',2), 42:('T 25','MIERCOLES',1),
    43:('T 25','VIERNES',2), 44:('T 25','VIERNES',3), 45:('F 350_3','JUEVES',6),
    46:('F 350_2','JUEVES',3), 47:('J 19','MIERCOLES',2), 48:('T 25','VIERNES',1),
    49:('F 350_1','MARTES',5), 50:('T 25','MARTES',4), 51:('T 25','MARTES',3),
    52:('K 16','VIERNES',4), 53:('K 16','VIERNES',2), 54:('F 350_2','MARTES',5),
    55:('F 350_2','MARTES',3), 56:('T 25','MARTES',1), 57:('F 350_2','JUEVES',2),
    58:('T 20','MARTES',2), 59:('T 23','MARTES',1), 60:('T 20','VIERNES',1),
    61:('T 17_1','VIERNES',5), 62:('T 23','MIERCOLES',2), 63:('K 16','LUNES',3),
    64:('T 20','JUEVES',1), 65:('T 20','JUEVES',2), 66:('T 23','MIERCOLES',3),
    67:('T 17_2','JUEVES',2), 68:('F 350_1','JUEVES',2), 69:('K 16','MIERCOLES',1),
    70:('T 20','JUEVES',3), 71:('T 17_2','JUEVES',5), 72:('T 17_2','JUEVES',3),
    73:('T 17_2','MIERCOLES',3), 74:('F 350_2','JUEVES',2), 75:('F 350_1','MARTES',3),
    76:('T 23','LUNES',2), 77:('T 20','LUNES',3), 78:('T 23','VIERNES',2),
    79:('K 16','MIERCOLES',4), 80:('T 17_1','VIERNES',2), 81:('K 16','VIERNES',3),
    82:('T 17_1','VIERNES',4), 83:('K 16','VIERNES',1), 84:('T 25','LUNES',1),
    85:('T 17_2','MIERCOLES',2), 86:('F 350_1','MARTES',4), 87:('T 23','MIERCOLES',1),
    88:('F 350_1','JUEVES',4), 89:('T 25','JUEVES',2), 90:('F 350_3','JUEVES',1),
    91:('T 20','MIERCOLES',1), 92:('F 350_3','JUEVES',2), 93:('F 350_3','MARTES',2),
    94:('T 17_1','VIERNES',1), 95:('T 25','MARTES',2), 96:('F 350_3','JUEVES',5),
    97:('T 17_1','MIERCOLES',2), 98:('F 350_3','JUEVES',3), 99:('T 17_2','JUEVES',4),
    100:('F 350_1','MARTES',6), 101:('T 17_2','LUNES',3),
}

COORDS = {
    1:(18.44876,-96.35809), 2:(18.08435,-96.12531), 3:(18.05926,-95.52912),
    4:(18.36521,-95.79520), 5:(18.10732,-95.88094), 6:(18.24066,-96.13355),
    7:(18.08756,-96.13498), 8:(19.19496,-96.13692), 9:(18.60314,-96.68606),
    10:(18.23995,-96.13654), 11:(17.83074,-95.81403), 12:(18.07512,-95.70601),
    13:(17.99159,-95.40149), 14:(18.24268,-96.40032), 15:(18.08426,-96.14116),
    16:(18.63237,-95.52057), 17:(18.83430,-96.79893), 18:(18.16041,-96.09451),
    19:(18.16041,-96.09451), 20:(18.51078,-96.53622), 21:(18.76935,-95.76095),
    22:(18.59700,-95.44363), 23:(18.50610,-96.45838), 24:(18.45355,-96.34998),
    25:(18.45729,-96.36487), 26:(18.89094,-96.80131), 27:(18.36846,-95.78980),
    28:(18.72335,-95.98693), 29:(18.77039,-96.17050), 30:(18.80207,-96.06305),
    31:(18.08336,-96.11879), 32:(18.46486,-95.30093), 33:(17.94672,-96.17203),
    34:(18.97201,-96.72228), 35:(18.07118,-96.53689), 36:(18.44488,-96.35990),
    37:(18.11140,-95.87953), 38:(18.06649,-96.14572), 39:(18.41843,-95.11249),
    40:(19.37040,-96.37467), 41:(19.09770,-96.33426), 42:(17.44841,-95.42821),
    43:(19.96877,-96.61126), 44:(20.02850,-96.64538), 45:(18.15656,-95.18498),
    46:(18.08561,-96.13000), 47:(18.35374,-96.16675), 48:(19.77218,-96.43242),
    49:(18.37333,-95.74604), 50:(19.05155,-96.98240), 51:(19.07429,-97.04964),
    52:(19.50588,-96.62073), 53:(19.35347,-96.56421), 54:(18.08592,-96.15119),
    55:(18.11104,-96.14188), 56:(18.96477,-97.01657), 57:(18.08561,-96.13000),
    58:(18.53776,-96.60675), 59:(18.74772,-96.78747), 60:(19.04541,-96.42404),
    61:(19.44535,-96.40699), 62:(19.07863,-96.15902), 63:(18.44020,-96.34553),
    64:(18.30449,-95.84303), 65:(18.23122,-95.94717), 66:(19.06025,-95.98800),
    67:(19.13461,-96.17994), 68:(18.61189,-95.65804), 69:(18.23637,-96.14189),
    70:(18.17730,-96.03599), 71:(19.20263,-96.22050), 72:(19.15379,-96.19753),
    73:(18.03164,-95.52428), 74:(18.09578,-96.11200), 75:(18.36712,-95.80008),
    76:(18.44133,-96.36234), 77:(18.45104,-96.36738), 78:(18.45928,-95.30621),
    79:(18.24371,-96.12644), 80:(19.26775,-96.39042), 81:(19.36222,-96.65840),
    82:(19.39949,-96.36066), 83:(19.28269,-96.44099), 84:(18.85086,-96.91448),
    85:(18.02394,-95.52926), 86:(18.37107,-95.75873), 87:(19.04055,-96.24023),
    88:(18.62927,-95.51382), 89:(18.17831,-96.09120), 90:(18.48293,-95.21962),
    91:(18.43571,-95.21688), 92:(17.99848,-95.39938), 93:(17.83127,-95.82024),
    94:(19.19964,-96.34602), 95:(19.01294,-97.02890), 96:(18.42075,-95.10501),
    97:(18.07390,-96.53703), 98:(18.43513,-95.17638), 99:(19.16695,-96.22055),
    100:(18.43438,-95.73368), 101:(18.44587,-96.34668),
}

DIA_ORDEN = {"LUNES": 1, "MARTES": 2, "MIERCOLES": 3, "JUEVES": 4, "VIERNES": 5}

VEH_COLORS = {
    "F 350_1": "#f5a623", "F 350_2": "#e05c5c", "F 350_3": "#9b59b6",
    "K 16": "#3ecfcf",    "K 20": "#2ecc71",
    "T 17_1": "#e67e22",  "T 17_2": "#1abc9c",
    "T 20": "#3498db",    "T 23": "#e91e63",    "T 25": "#ff5722",
    "J 19": "#8bc34a",    "J 18": "#607d8b",    "KANGOO": "#ffc107",
}

# ═══════════════════════════════════════ VRP PARÁMETROS ═══════════
# Desviación estándar aceptable respecto al histórico
VRP_DEV_NORMAL   = 0.30
# Máxima desviación en casos excepcionales
VRP_DEV_ESPECIAL = 0.50
# Umbral para camión pequeño (≤ este valor → excepción VRP)
UMBRAL_PEQUEÑO   = 1300

VRP_STATUS_ICON = {
    "NORMAL":       "✓",
    "EDGE_CASE":    "⚠",
    "CRITICO":      "✕",
    "SOBRECARGA":   "✕",
    "SIN_HIST":     "—",
    "OK_PEQUEÑO":   "✓",
    "CONSOLIDADA":  "↗",
}

VRP_STATUS_COLOR = {
    "NORMAL":       "#4ecb82",
    "EDGE_CASE":    "#f5a623",
    "CRITICO":      "#e05c5c",
    "SOBRECARGA":   "#e05c5c",
    "SIN_HIST":     "#7a8290",
    "OK_PEQUEÑO":   "#3ecfcf",
    "CONSOLIDADA":  "#9b59b6",
}

VRP_STATUS_LABEL = {
    "NORMAL":       "Normal (≤30%)",
    "EDGE_CASE":    "Caso especial (30–50%)",
    "CRITICO":      "Crítico (>50%)",
    "SOBRECARGA":   "Sobrecarga",
    "SIN_HIST":     "Sin historial",
    "OK_PEQUEÑO":   "Camión pequeño (cap. ok)",
    "CONSOLIDADA":  "Consolidada en ruta cercana",
}

# ════════════════════════════════════ FUNCIONES CORE ══════════════

def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    dl = radians(lat2 - lat1); dlo = radians(lon2 - lon1)
    a = sin(dl/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlo/2)**2
    return 2 * R * atan2(sqrt(a), sqrt(1 - a))


def nearest_neighbor(sc_dict):
    ids = list(sc_dict.keys())
    if len(ids) <= 1:
        return ids
    start = max(ids, key=lambda x: sc_dict[x][0])
    visited = [start]
    rem = set(ids) - {start}
    while rem:
        last = visited[-1]
        la, lo = sc_dict[last]
        near = min(rem, key=lambda x: haversine(la, lo, sc_dict[x][0], sc_dict[x][1]))
        visited.append(near)
        rem.remove(near)
    return visited


def read_csv_robust(file):
    try:
        df = pd.read_csv(file, encoding='utf-8-sig')
        df.columns = [c.strip().replace('﻿', '') for c in df.columns]
        return df
    except Exception:
        file.seek(0)
        return pd.read_csv(file, encoding='latin-1')


def to_csv_bytes(df):
    buf = io.BytesIO()
    df.to_csv(buf, index=False, encoding='utf-8')
    return buf.getvalue()

# ════════════════════════════════════ VRP: HISTORIAL ══════════════

def build_template_from_history(dfs, recency_weights=None):
    """
    Construye template dinámico desde lista de DataFrames históricos.

    Parámetros
    ----------
    dfs              : list[DataFrame] – ejemplos históricos (más antiguo → más reciente)
    recency_weights  : list[float]     – pesos por ejemplo (None → 1, 2, 3 ... n)

    Retorna
    -------
    template    : {id_sucursal: (vehiculo, dia_semana, seq_mediana)}
    kg_hist     : {id_sucursal: float}   – kg promedio ponderado por sucursal
    route_stats : {(vehiculo, dia_semana): dict}  – estadísticas por ruta
    """
    if not dfs:
        return {}, {}, {}

    n = len(dfs)
    if recency_weights is None:
        recency_weights = [float(i + 1) for i in range(n)]

    # Acumuladores
    vd_votes    = defaultdict(lambda: defaultdict(float))   # sid → {(veh,dia): peso}
    kg_records  = defaultdict(list)                          # sid → [(kg, peso)]
    seq_records = defaultdict(lambda: defaultdict(list))     # sid → {(veh,dia): [seqs]}
    route_kg    = defaultdict(list)                          # (veh,dia) → [kg_total por ejemplo]

    for w, df in zip(recency_weights, dfs):
        if df is None:
            continue
        df = df.copy()
        df.columns = [c.strip() for c in df.columns]
        required = ['id_sucursal', 'vehiculo', 'dia_semana', 'secuencia_visita', 'kg_entrega']
        if not all(c in df.columns for c in required):
            continue
        df['id_sucursal']      = df['id_sucursal'].astype(int)
        df['secuencia_visita'] = df['secuencia_visita'].astype(int)
        df['kg_entrega']       = df['kg_entrega'].astype(float)
        df['dia_semana']       = df['dia_semana'].str.strip().str.upper()

        for _, row in df.iterrows():
            sid = int(row['id_sucursal'])
            veh = str(row['vehiculo']).strip()
            dia = str(row['dia_semana']).strip()
            vd_votes[sid][(veh, dia)] += w
            kg_records[sid].append((float(row['kg_entrega']), w))
            seq_records[sid][(veh, dia)].append(int(row['secuencia_visita']))

        for (veh, dia), grp in df.groupby(['vehiculo', 'dia_semana']):
            route_kg[(str(veh).strip(), str(dia).strip().upper())].append(
                grp['kg_entrega'].sum()
            )

    # Construir template: (veh, dia) con mayor peso por sucursal
    template = {}
    for sid, votes in vd_votes.items():
        (veh, dia) = max(votes, key=votes.get)
        seqs = seq_records[sid].get((veh, dia), [])
        seq = int(round(sum(seqs) / len(seqs))) if seqs else 999
        template[sid] = (veh, dia, seq)

    # kg promedio ponderado por sucursal
    kg_hist = {}
    for sid, records in kg_records.items():
        total_w = sum(w for _, w in records)
        kg_hist[sid] = sum(kg * w for kg, w in records) / total_w if total_w > 0 else 0.0

    # Estadísticas por ruta (vehiculo, dia)
    route_stats = {}
    for (veh, dia), totals in route_kg.items():
        # Miembros de esta ruta (solo los que el template asigna a este (veh, dia))
        members_map = {}
        for sid, (tv, td, tseq) in template.items():
            if tv == veh and td == dia:
                seqs = seq_records[sid].get((veh, dia), [])
                members_map[sid] = int(round(sum(seqs) / len(seqs))) if seqs else 999

        ordered = sorted(members_map.items(), key=lambda x: x[1])
        avg = sum(totals) / len(totals)
        std = (sum((t - avg) ** 2 for t in totals) / len(totals)) ** 0.5 if len(totals) > 1 else 0.0
        cap = VEHICULOS_CAP.get(veh, 3500)

        route_stats[(veh, dia)] = {
            'kg_avg':      round(avg, 1),
            'kg_std':      round(std, 1),
            'kg_min':      round(min(totals), 1),
            'kg_max':      round(max(totals), 1),
            'n_ejemplos':  len(totals),
            'members':     ordered,        # [(sid, seq)]
            'capacity':    cap,
            'is_small':    cap <= UMBRAL_PEQUEÑO,
        }

    return template, kg_hist, route_stats

# ════════════════════════════════════ VRP: GENERACIÓN ═════════════

def _vrp_status(total_kg, hist_avg, capacity, is_small):
    """Determina el estado VRP de una ruta."""
    if total_kg > capacity:
        return "SOBRECARGA"
    if is_small:
        return "OK_PEQUEÑO"   # T 25 / KANGOO: solo verificar capacidad
    if hist_avg is None or hist_avg == 0:
        return "SIN_HIST"
    dev = abs(total_kg - hist_avg) / hist_avg
    if dev > VRP_DEV_ESPECIAL:
        return "CRITICO"
    if dev > VRP_DEV_NORMAL:
        return "EDGE_CASE"
    return "NORMAL"


# Umbral para consolidación: ruta >50% por debajo del histórico → candidata
_CONSOL_DEV_LOW = 0.50
# Distancia máxima entre centroides para permitir la integración
_CONSOL_MAX_KM  = 100.0


def _centroid(mems, coords):
    if not mems:
        return (0.0, 0.0)
    lats = [coords.get(m['sid'], (0.0, 0.0))[0] for m in mems]
    lons = [coords.get(m['sid'], (0.0, 0.0))[1] for m in mems]
    return (sum(lats) / len(lats), sum(lons) / len(lons))


def _consolidate_underloaded(groups, kg_map, route_stats, coords):
    """
    Post-assignment: rutas críticamente por debajo del histórico (>50%) se
    integran en la ruta del mismo día más cercana geográficamente, siempre que:
      • La ruta destino tenga capacidad suficiente
      • La ruta destino no supere 50% de desviación tras la fusión
      • La distancia entre centroides sea ≤ 100 km
      • Camiones pequeños (≤ 1,300 kg) nunca se consolidan

    Retorna
    -------
    groups : dict – grupos actualizados (fuentes vacías eliminadas)
    log    : list[dict] – registro de cada consolidación realizada
    """
    log = []

    def _total(key):
        return sum(kg_map.get(m['sid'], 0) for m in groups.get(key, []))

    # Identificar rutas candidatas (más subocupadas primero)
    candidates = []
    for (veh, dia) in list(groups.keys()):
        mems = groups[(veh, dia)]
        if not mems:
            continue
        cap = VEHICULOS_CAP.get(veh, 3500)
        if cap <= UMBRAL_PEQUEÑO:
            continue  # Excepción T 25: no consolidar
        hist_avg = route_stats.get((veh, dia), {}).get('kg_avg')
        if not hist_avg or hist_avg == 0:
            continue
        total_kg = _total((veh, dia))
        dev_low = (hist_avg - total_kg) / hist_avg  # Positivo → por debajo del histórico
        if dev_low > _CONSOL_DEV_LOW:
            candidates.append((veh, dia, dev_low, total_kg))

    candidates.sort(key=lambda x: -x[2])  # Más subocupado primero

    for (src_veh, src_dia, dev_low, src_kg) in candidates:
        src_mems = groups.get((src_veh, src_dia), [])
        if not src_mems:
            continue

        src_ctr = _centroid(src_mems, coords)

        # Buscar la mejor ruta destino (mismo día, más cercana, con capacidad)
        best_dest = None
        best_dist = float('inf')

        for (dst_veh, dst_dia) in list(groups.keys()):
            if (dst_veh, dst_dia) == (src_veh, src_dia):
                continue
            if dst_dia != src_dia:
                continue  # Mismo día de entrega
            dst_mems = groups[(dst_veh, dst_dia)]
            if not dst_mems:
                continue
            dst_cap = VEHICULOS_CAP.get(dst_veh, 3500)
            if dst_cap <= UMBRAL_PEQUEÑO:
                continue  # No integrar en camión pequeño

            # Verificar capacidad tras la fusión
            dst_total = _total((dst_veh, dst_dia))
            if dst_total + src_kg > dst_cap:
                continue

            # Verificar que la ruta destino no supere 50% de desviación
            dst_hist_avg = route_stats.get((dst_veh, dst_dia), {}).get('kg_avg')
            if dst_hist_avg and dst_hist_avg > 0:
                new_dev = abs(dst_total + src_kg - dst_hist_avg) / dst_hist_avg
                if new_dev > VRP_DEV_ESPECIAL:
                    continue

            # Distancia entre centroides
            dst_ctr = _centroid(dst_mems, coords)
            dist = haversine(src_ctr[0], src_ctr[1], dst_ctr[0], dst_ctr[1])
            if dist > _CONSOL_MAX_KM:
                continue

            if dist < best_dist:
                best_dist = dist
                best_dest = (dst_veh, dst_dia)

        if best_dest:
            dst_veh, dst_dia = best_dest
            for m in src_mems:
                m['seq'] = 999  # Reset: la secuencia se recalcula con nearest-neighbor
                groups[(dst_veh, dst_dia)].append(m)

            log.append({
                'origen':          f"{src_veh} — {src_dia}",
                'destino':         f"{dst_veh} — {dst_dia}",
                'paradas_movidas': len(src_mems),
                'kg_movidos':      round(src_kg),
                'dist_km':         round(best_dist, 1),
                'motivo':          f"Ruta {dev_low*100:.0f}% por debajo del histórico",
            })
            groups[(src_veh, src_dia)] = []  # Vaciar ruta origen

    return {k: v for k, v in groups.items() if v}, log


def generate_routes_vrp(pedidos_df, active_template=None, kg_hist=None, route_stats=None):
    """
    Generación VRP híbrida: respeta secuencias históricas + control de desviación.

    Reglas:
    • Desviación ≤ 30 %  → NORMAL
    • 30 % < desv ≤ 50 % → EDGE_CASE  (caso especial, permitido pero señalado)
    • Desviación > 50 %  → CRITICO    (requiere revisión)
    • Rutas >50% por debajo del histórico → se consolidan en la ruta coherente más cercana
    • Camiones ≤ 1,300 kg (T 25, KANGOO) → excepción: solo verifica capacidad, no se consolidan
    • No se generan combinaciones nuevas; todo sigue el historial
    """
    if active_template is None:
        active_template = TEMPLATE
    if kg_hist is None:
        kg_hist = {}
    if route_stats is None:
        route_stats = {}

    coords = dict(COORDS)
    for _, r in pedidos_df.iterrows():
        sid = int(r['id_sucursal'])
        if sid not in coords:
            try:
                coords[sid] = (float(r['latitud']), float(r['longitud']))
            except Exception:
                pass

    kg_map = {int(r['id_sucursal']): float(r['kg_entrega']) for _, r in pedidos_df.iterrows()}

    # ── Asignar (vehiculo, dia) según template histórico ──────────
    groups = defaultdict(list)
    for _, row in pedidos_df.iterrows():
        sid = int(row['id_sucursal'])
        lat = float(row.get('latitud', 0))
        lon = float(row.get('longitud', 0))
        if sid in coords:
            lat, lon = coords[sid]

        if sid in active_template:
            veh, dia, seq = active_template[sid]
        else:
            # Fallback geográfico: sucursal no vista en historial
            near = min(
                (k for k in active_template if k in coords),
                key=lambda k: haversine(lat, lon, coords[k][0], coords[k][1]),
                default=None
            )
            if near:
                veh, dia, seq = active_template[near][0], active_template[near][1], 999
            else:
                veh, dia, seq = 'F 350_1', 'LUNES', 999

        groups[(veh, dia)].append({'sid': sid, 'seq': seq, 'lat': lat, 'lon': lon})

    # ── Consolidar rutas críticamente subocupadas ──────────────────
    consol_log = []
    consol_src_set = set()   # rutas que fueron absorbidas (para el reporte)
    consol_dst_map = {}      # (dst_veh, dst_dia) → lista de notas de origen

    if route_stats:  # Solo consolidar cuando hay datos históricos de referencia
        groups, consol_log = _consolidate_underloaded(groups, kg_map, route_stats, coords)
        for entry in consol_log:
            consol_src_set.add(entry['origen'])
            dst_key = entry['destino']
            if dst_key not in consol_dst_map:
                consol_dst_map[dst_key] = []
            consol_dst_map[dst_key].append(
                f"+{entry['paradas_movidas']} paradas de {entry['origen']} ({entry['kg_movidos']:,} kg)"
            )

    rows = []
    report_rows = []

    for (veh, dia), members in sorted(groups.items()):
        total_kg = sum(kg_map.get(m['sid'], 0) for m in members)
        capacity = VEHICULOS_CAP.get(veh, 3500)
        is_small = capacity <= UMBRAL_PEQUEÑO

        hist = route_stats.get((veh, dia), {})
        hist_avg = hist.get('kg_avg', None)

        deviation = None
        if hist_avg and hist_avg > 0:
            deviation = abs(total_kg - hist_avg) / hist_avg

        status = _vrp_status(total_kg, hist_avg, capacity, is_small)

        ruta_key = f"{veh} — {dia}"
        notas = "; ".join(consol_dst_map.get(ruta_key, []))

        report_rows.append({
            'vehiculo':     veh,
            'dia_semana':   dia,
            'sucursales':   len(members),
            'kg_total':     round(total_kg),
            'kg_hist_avg':  round(hist_avg) if hist_avg is not None else None,
            'desviacion_%': round(deviation * 100, 1) if deviation is not None else None,
            'capacidad_kg': capacity,
            'uso_%':        round(total_kg / capacity * 100, 1),
            'estado':       status,
            'notas':        notas,
        })

        # ── Secuencia: histórica si no hay empates; nearest-neighbor si los hay ──
        # Stops recibidos por consolidación (seq=999) se insertan al final
        # y luego toda la ruta se reordena con nearest-neighbor
        if any(m['seq'] == 999 for m in members):
            # Hay paradas nuevas (consolidadas): reordenar toda la ruta
            sc = {m['sid']: (m['lat'], m['lon']) for m in members}
            ordered = nearest_neighbor(sc)
        else:
            seqs = [m['seq'] for m in members]
            if len(seqs) == len(set(seqs)):
                ordered = [m['sid'] for m in sorted(members, key=lambda x: x['seq'])]
            else:
                sc = {m['sid']: (m['lat'], m['lon']) for m in members}
                ordered = nearest_neighbor(sc)

        for i, sid in enumerate(ordered, 1):
            rows.append({
                'id_sucursal':      sid,
                'vehiculo':         veh,
                'dia_semana':       dia,
                'secuencia_visita': i,
                'kg_entrega':       int(kg_map.get(sid, 0)),
            })

    # ── Registrar en el reporte las rutas que fueron consolidadas ──
    for entry in consol_log:
        src_parts = entry['origen'].split(' — ')
        src_veh, src_dia = src_parts[0], src_parts[1] if len(src_parts) > 1 else ''
        hist_avg_src = route_stats.get((src_veh, src_dia), {}).get('kg_avg')
        report_rows.append({
            'vehiculo':     src_veh,
            'dia_semana':   src_dia,
            'sucursales':   entry['paradas_movidas'],
            'kg_total':     entry['kg_movidos'],
            'kg_hist_avg':  round(hist_avg_src) if hist_avg_src else None,
            'desviacion_%': None,
            'capacidad_kg': VEHICULOS_CAP.get(src_veh, 3500),
            'uso_%':        None,
            'estado':       'CONSOLIDADA',
            'notas':        f"→ {entry['destino']} ({entry['dist_km']} km) · {entry['motivo']}",
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        df['_d'] = df['dia_semana'].map(DIA_ORDEN)
        df = df.sort_values(['vehiculo', '_d', 'secuencia_visita']).drop(columns='_d')

    return df, pd.DataFrame(report_rows), consol_log

# ════════════════════════════════════ GENERACIÓN ESTÁNDAR ═════════

def generate_routes(pedidos_df):
    """Generación basada en template estático (modo sin VRP)."""
    coords = dict(COORDS)
    for _, r in pedidos_df.iterrows():
        if r['id_sucursal'] not in coords:
            coords[r['id_sucursal']] = (r['latitud'], r['longitud'])
    kg_map = {r['id_sucursal']: r['kg_entrega'] for _, r in pedidos_df.iterrows()}

    groups = defaultdict(list)
    for _, row in pedidos_df.iterrows():
        sid = int(row['id_sucursal'])
        lat, lon = coords.get(sid, (row['latitud'], row['longitud']))
        if sid in TEMPLATE:
            veh, dia, seq = TEMPLATE[sid]
        else:
            near = min(
                (k for k in TEMPLATE if k in coords),
                key=lambda k: haversine(lat, lon, coords[k][0], coords[k][1]),
                default=None
            )
            veh, dia, seq = (TEMPLATE[near][0], TEMPLATE[near][1], 999) if near else ('F 350_1', 'LUNES', 999)
        groups[(veh, dia)].append({'sid': sid, 'seq': seq, 'lat': lat, 'lon': lon})

    rows = []
    for (veh, dia), members in groups.items():
        seqs = [m['seq'] for m in members]
        if len(seqs) == len(set(seqs)):
            ordered = [m['sid'] for m in sorted(members, key=lambda x: x['seq'])]
        else:
            sc = {m['sid']: (m['lat'], m['lon']) for m in members}
            ordered = nearest_neighbor(sc)
        for i, sid in enumerate(ordered, 1):
            rows.append({'id_sucursal': sid, 'vehiculo': veh, 'dia_semana': dia,
                         'secuencia_visita': i, 'kg_entrega': int(kg_map.get(sid, 0))})

    df = pd.DataFrame(rows)
    df['_d'] = df['dia_semana'].map(DIA_ORDEN)
    df = df.sort_values(['vehiculo', '_d', 'secuencia_visita']).drop(columns='_d')
    return df


def compare_routes(df_gen, df_act):
    g = df_gen.set_index('id_sucursal')
    a = df_act.set_index('id_sucursal')
    common = g.index.intersection(a.index)
    n = len(common)
    if n == 0:
        return {}
    gc = g.loc[common]; ac = a.loc[common]
    vm  = (gc['vehiculo'] == ac['vehiculo']).sum()
    dm  = (gc['dia_semana'].str.upper() == ac['dia_semana'].str.upper()).sum()
    vdm = ((gc['vehiculo'] == ac['vehiculo']) &
           (gc['dia_semana'].str.upper() == ac['dia_semana'].str.upper())).sum()
    sm  = ((gc['vehiculo'] == ac['vehiculo']) &
           (gc['dia_semana'].str.upper() == ac['dia_semana'].str.upper()) &
           (gc['secuencia_visita'] == ac['secuencia_visita'])).sum()
    km  = (gc['kg_entrega'] == ac['kg_entrega']).sum()
    fm  = ((gc['vehiculo'] == ac['vehiculo']) &
           (gc['dia_semana'].str.upper() == ac['dia_semana'].str.upper()) &
           (gc['secuencia_visita'] == ac['secuencia_visita']) &
           (gc['kg_entrega'] == ac['kg_entrega'])).sum()
    sim = (0.5 * vdm / n + 0.3 * sm / n + 0.2 * km / n) * 100
    detail = []
    for sid in common:
        detail.append({'id_sucursal': sid,
                       'gen_veh': gc.loc[sid, 'vehiculo'],   'act_veh': ac.loc[sid, 'vehiculo'],
                       'gen_dia': gc.loc[sid, 'dia_semana'], 'act_dia': ac.loc[sid, 'dia_semana'],
                       'gen_seq': gc.loc[sid, 'secuencia_visita'],
                       'act_seq': ac.loc[sid, 'secuencia_visita'],
                       'veh_ok': gc.loc[sid, 'vehiculo'] == ac.loc[sid, 'vehiculo'],
                       'dia_ok': gc.loc[sid, 'dia_semana'].upper() == ac.loc[sid, 'dia_semana'].upper(),
                       'seq_ok': gc.loc[sid, 'secuencia_visita'] == ac.loc[sid, 'secuencia_visita'],
                       'kg_ok':  gc.loc[sid, 'kg_entrega'] == ac.loc[sid, 'kg_entrega']})
    return dict(n=n, vm=int(vm), dm=int(dm), vdm=int(vdm), sm=int(sm), km=int(km),
                fm=int(fm), sim=sim, detail=detail)

# ═══════════════════════════════════ GRÁFICAS ═════════════════════

def fig_uso_capacidad(df_rutas):
    df = df_rutas.groupby(['vehiculo', 'dia_semana'])['kg_entrega'].sum().reset_index()
    df['capacidad'] = df['vehiculo'].map(VEHICULOS_CAP)
    df['uso_pct'] = df['kg_entrega'] / df['capacidad'] * 100
    df['ruta'] = df['vehiculo'] + " — " + df['dia_semana']
    df = df.sort_values('uso_pct', ascending=True)
    colors = [('#4ecb82' if u <= 100 else '#e05c5c') for u in df['uso_pct']]
    fig = go.Figure(go.Bar(x=df['uso_pct'], y=df['ruta'], orientation='h',
                           marker_color=colors,
                           text=df['uso_pct'].apply(lambda x: f"{x:.0f}%"),
                           textposition='outside'))
    fig.add_vline(x=100, line_dash='dash', line_color='#f5a623', line_width=1.5)
    fig.update_layout(template='plotly_dark', paper_bgcolor='#1b2029', plot_bgcolor='#1b2029',
                      height=max(350, len(df) * 22 + 80),
                      xaxis_title='% Capacidad utilizada', yaxis_title='',
                      margin=dict(l=10, r=60, t=20, b=30),
                      font=dict(family='DM Sans', color='#e8eaf0'), showlegend=False)
    return fig


def fig_kg_por_dia(df_rutas):
    df = df_rutas.groupby('dia_semana')['kg_entrega'].sum().reset_index()
    df['_o'] = df['dia_semana'].map(DIA_ORDEN)
    df = df.sort_values('_o')
    fig = go.Figure(go.Bar(x=df['dia_semana'], y=df['kg_entrega'],
                           marker_color='#f5a623',
                           text=df['kg_entrega'].apply(lambda x: f"{x:,}"),
                           textposition='outside'))
    fig.update_layout(template='plotly_dark', paper_bgcolor='#1b2029', plot_bgcolor='#1b2029',
                      height=300, yaxis_title='kg totales', xaxis_title='',
                      margin=dict(l=10, r=10, t=20, b=30),
                      font=dict(family='DM Sans', color='#e8eaf0'))
    return fig


def fig_mapa(df_rutas):
    coords_df = pd.DataFrame([{'id_sucursal': k, 'lat': v[0], 'lon': v[1]} for k, v in COORDS.items()])
    merged = df_rutas.merge(coords_df, on='id_sucursal', how='left')
    merged['color'] = merged['vehiculo'].map(VEH_COLORS).fillna('#888888')
    merged['hover'] = (merged['vehiculo'] + " — " + merged['dia_semana'] + "<br>Seq: " +
                       merged['secuencia_visita'].astype(str) + "  |  " +
                       merged['kg_entrega'].astype(str) + " kg")
    fig = go.Figure()
    for veh in merged['vehiculo'].unique():
        sub = merged[merged['vehiculo'] == veh].sort_values(['dia_semana', 'secuencia_visita'])
        color = VEH_COLORS.get(veh, '#888888')
        fig.add_trace(go.Scattermapbox(
            lat=sub['lat'], lon=sub['lon'], mode='markers+text',
            marker=dict(size=10, color=color, opacity=0.85),
            text=sub['secuencia_visita'].astype(str),
            textfont=dict(size=9, color='white'),
            textposition='top center', name=veh,
            hovertext=sub['hover'], hoverinfo='text'))
    fig.update_layout(
        mapbox=dict(style='carto-darkmatter',
                    center=dict(lat=merged['lat'].mean(), lon=merged['lon'].mean()), zoom=6.5),
        paper_bgcolor='#1b2029', height=540,
        margin=dict(l=0, r=0, t=0, b=0),
        legend=dict(bgcolor='#14181f', bordercolor='#252c38', borderwidth=1,
                    font=dict(color='#e8eaf0', size=11)))
    return fig


def fig_similitud_radar(metrics):
    cats = ['Vehículo', 'Día', 'Veh+Día', 'Secuencia', 'Kg']
    n = metrics['n']
    vals = [metrics['vm']/n*100, metrics['dm']/n*100, metrics['vdm']/n*100,
            metrics['sm']/n*100, metrics['km']/n*100]
    fig = go.Figure(go.Scatterpolar(r=vals + [vals[0]], theta=cats + [cats[0]], fill='toself',
                                    fillcolor='rgba(245,166,35,0.15)',
                                    line=dict(color='#f5a623', width=2),
                                    marker=dict(color='#f5a623', size=7)))
    fig.update_layout(
        polar=dict(bgcolor='#1b2029',
                   radialaxis=dict(visible=True, range=[0, 100],
                                   tickfont=dict(color='#7a8290'),
                                   gridcolor='#252c38', linecolor='#252c38'),
                   angularaxis=dict(tickfont=dict(color='#e8eaf0', size=12),
                                    gridcolor='#252c38', linecolor='#252c38')),
        paper_bgcolor='#1b2029', height=300,
        margin=dict(l=40, r=40, t=20, b=20), showlegend=False,
        font=dict(family='DM Sans', color='#e8eaf0'))
    return fig


def fig_vrp_desviacion(vrp_df):
    """Gráfica de barras horizontales: desviación por ruta con líneas de umbral."""
    if vrp_df.empty:
        return go.Figure()
    df = vrp_df.copy()
    df['ruta'] = df['vehiculo'] + ' — ' + df['dia_semana']
    df['color'] = df['estado'].map(VRP_STATUS_COLOR).fillna('#7a8290')
    df['label'] = df['desviacion_%'].apply(lambda x: f"{x:.1f}%" if pd.notna(x) else "—")
    df = df.sort_values('desviacion_%', ascending=True, na_position='first')

    fig = go.Figure(go.Bar(
        x=df['desviacion_%'].fillna(0),
        y=df['ruta'],
        orientation='h',
        marker_color=df['color'],
        text=df['label'],
        textposition='outside',
        hovertemplate=(
            '<b>%{y}</b><br>'
            'Desviación: %{x:.1f}%<br>'
            '<extra></extra>'
        ),
    ))
    fig.add_vline(x=30, line_dash='dash', line_color='#f5a623', line_width=1.5,
                  annotation_text=" 30%", annotation_font_color='#f5a623',
                  annotation_position='top right')
    fig.add_vline(x=50, line_dash='dash', line_color='#e05c5c', line_width=1.5,
                  annotation_text=" 50%", annotation_font_color='#e05c5c',
                  annotation_position='top right')
    fig.update_layout(
        template='plotly_dark', paper_bgcolor='#1b2029', plot_bgcolor='#1b2029',
        height=max(320, len(df) * 24 + 80),
        xaxis_title='Desviación respecto al histórico (%)',
        yaxis_title='',
        margin=dict(l=10, r=90, t=30, b=30),
        font=dict(family='DM Sans', color='#e8eaf0'),
        showlegend=False,
    )
    return fig

# ═══════════════════════════════════ SIDEBAR ══════════════════════

with st.sidebar:
    st.markdown('<div class="logo-text">🚚 LogiRutas</div>', unsafe_allow_html=True)
    st.markdown('<div class="logo-sub">Sistema de Rutas · VRP Híbrido</div>', unsafe_allow_html=True)
    st.markdown("---")

    # Estado VRP
    vrp_active = st.session_state.get('vrp_template') is not None
    if vrp_active:
        n_hist = len(st.session_state.get('hist_dfs', []))
        st.markdown(
            f'<div style="background:rgba(78,203,130,0.12);border:1px solid #4ecb82;'
            f'border-radius:8px;padding:8px 12px;margin-bottom:12px;">'
            f'<span style="color:#4ecb82;font-weight:700;font-size:0.85rem;">● MODO VRP ACTIVO</span><br>'
            f'<span style="color:#7a8290;font-size:0.75rem;">{n_hist} ejemplo(s) en historial base</span>'
            f'</div>', unsafe_allow_html=True)
    else:
        st.markdown(
            '<div style="background:rgba(122,130,144,0.08);border:1px solid #252c38;'
            'border-radius:8px;padding:8px 12px;margin-bottom:12px;">'
            '<span style="color:#7a8290;font-size:0.82rem;">○ Modo estándar (sin historial)</span><br>'
            '<span style="color:#7a8290;font-size:0.72rem;">Carga historial en 📚 para activar VRP</span>'
            '</div>', unsafe_allow_html=True)

    st.markdown("**Flota disponible**")
    for veh, cap in VEHICULOS_CAP.items():
        color = VEH_COLORS.get(veh, '#888')
        small_tag = ' <span style="color:#ff5722;font-size:0.7rem;">▸ pequeño</span>' if cap <= UMBRAL_PEQUEÑO else ''
        st.markdown(
            f'<div style="display:flex;align-items:center;gap:8px;margin:3px 0;">'
            f'<div style="width:9px;height:9px;border-radius:50%;background:{color};flex-shrink:0;"></div>'
            f'<span style="font-size:0.81rem;color:#e8eaf0;">{veh}{small_tag}</span>'
            f'<span style="margin-left:auto;font-size:0.77rem;color:#7a8290;">{cap:,} kg</span>'
            f'</div>', unsafe_allow_html=True)

    st.markdown("---")
    st.markdown(
        '<span style="color:#7a8290;font-size:0.72rem;">'
        'Desviación VRP: 30% normal · 50% máx<br>'
        'Camiones ≤ 1,300 kg → excepción T 25</span>',
        unsafe_allow_html=True)

# ═══════════════════════════════════ TABS ═════════════════════════

tab_hist, tab_gen, tab_cmp, tab_info = st.tabs([
    "📚  Historial Base",
    "📦  Generar Ruta",
    "⚖️  Comparar Rutas",
    "ℹ️  Acerca del sistema",
])

# ─────────────────────────────── TAB 1: HISTORIAL BASE ───────────
with tab_hist:
    st.markdown("## Historial Base (VRP)")
    st.markdown(
        "Carga aquí los ejemplos de rutas **confirmadas** que servirán como base para el generador VRP. "
        "El sistema construirá un template dinámico y calculará las estadísticas de desviación aceptable "
        "por ruta. Los archivos más recientes reciben mayor peso automáticamente."
    )

    # Formato esperado
    with st.expander("Ver formato esperado"):
        st.markdown("**Columnas requeridas**")
        st.code(
            "id_sucursal, vehiculo, dia_semana, secuencia_visita, kg_entrega\n"
            "# Columna opcional: sucursal_cliente\n"
            "# Ejemplo:\n"
            "21,F 350_1,JUEVES,1,834\n"
            "68,F 350_1,JUEVES,2,617",
            language='text'
        )
        st.caption(
            "Este es el mismo formato que genera el sistema en la pestaña 📦. "
            "Puedes usar directamente los archivos resultado_ejemplo_*.csv como historial."
        )

    # Carga de archivos
    up_hist = st.file_uploader(
        "Cargar ejemplos de rutas confirmadas (CSV)",
        type=['csv'],
        accept_multiple_files=True,
        key='up_hist',
        help="Sube uno o más archivos. Ordénalos del más antiguo al más reciente: el último recibe mayor peso.",
    )

    if up_hist:
        hist_dfs_raw = []
        hist_names_raw = []
        errs = []
        for f in up_hist:
            try:
                df_h = read_csv_robust(f)
                req_h = ['id_sucursal', 'vehiculo', 'dia_semana', 'secuencia_visita', 'kg_entrega']
                miss_h = [c for c in req_h if c not in df_h.columns]
                if miss_h:
                    errs.append(f"'{f.name}': columnas faltantes {miss_h}")
                else:
                    hist_dfs_raw.append(df_h)
                    hist_names_raw.append(f.name)
            except Exception as e:
                errs.append(f"'{f.name}': {e}")

        for err in errs:
            st.warning(f"Archivo omitido — {err}")

        if hist_dfs_raw:
            st.success(f"✓ {len(hist_dfs_raw)} archivo(s) válido(s) listo(s)")

            # Tabla de archivos cargados con sus pesos
            st.markdown("**Ejemplos cargados** — el peso aumenta con el orden (más reciente = mayor peso):")
            for i, (name, df_h) in enumerate(zip(hist_names_raw, hist_dfs_raw)):
                w = i + 1
                n_suc = df_h['id_sucursal'].nunique() if 'id_sucursal' in df_h.columns else '?'
                n_rut = df_h.groupby(['vehiculo', 'dia_semana']).ngroups if 'vehiculo' in df_h.columns else '?'
                st.markdown(
                    f'<div style="display:flex;align-items:center;gap:12px;padding:6px 14px;'
                    f'margin:3px 0;background:#1b2029;border:1px solid #252c38;border-radius:6px;">'
                    f'<span style="color:#7a8290;font-size:0.8rem;min-width:24px;">#{i+1}</span>'
                    f'<span style="color:#e8eaf0;font-size:0.85rem;flex:1;">{name}</span>'
                    f'<span style="color:#7a8290;font-size:0.77rem;">{n_suc} suc · {n_rut} rutas</span>'
                    f'<span style="color:#f5a623;font-size:0.78rem;font-weight:700;min-width:60px;text-align:right;">Peso: {w}</span>'
                    f'</div>',
                    unsafe_allow_html=True
                )

            st.markdown("")
            if st.button("✅ Confirmar y usar como historial base", type="primary"):
                with st.spinner("Construyendo template VRP desde historial..."):
                    n = len(hist_dfs_raw)
                    weights = [float(i + 1) for i in range(n)]
                    tmpl, kg_h, rstats = build_template_from_history(hist_dfs_raw, weights)

                st.session_state['vrp_template']    = tmpl
                st.session_state['vrp_kg_hist']     = kg_h
                st.session_state['vrp_route_stats'] = rstats
                st.session_state['hist_dfs']        = hist_dfs_raw
                st.session_state['hist_names']      = hist_names_raw

                st.success(
                    f"✓ Template VRP construido: **{len(tmpl)} sucursales** · "
                    f"**{len(rstats)} rutas** identificadas"
                )
                st.rerun()

    # ── Panel de estado actual del historial ─────────────────────
    if st.session_state.get('vrp_template'):
        tmpl   = st.session_state['vrp_template']
        rstats = st.session_state['vrp_route_stats']
        kg_h   = st.session_state['vrp_kg_hist']
        names  = st.session_state.get('hist_names', [])

        st.markdown("---")
        c1, c2, c3 = st.columns(3)
        c1.metric("Sucursales en template", len(tmpl))
        c2.metric("Rutas identificadas", len(rstats))
        c3.metric("Ejemplos cargados", len(names))

        # Tabla de estadísticas por ruta
        st.markdown("### Estadísticas por ruta (base histórica)")
        rs_rows = []
        for (veh, dia), rs in sorted(rstats.items(),
                                      key=lambda x: (x[0][0], DIA_ORDEN.get(x[0][1], 9))):
            uso_hist = rs['kg_avg'] / rs['capacity'] * 100 if rs['capacity'] > 0 else 0
            rs_rows.append({
                'Vehículo':    veh,
                'Día':         dia,
                'Paradas':     len(rs['members']),
                'kg prom.':    f"{rs['kg_avg']:,.0f}",
                'kg mín.':     f"{rs['kg_min']:,.0f}",
                'kg máx.':     f"{rs['kg_max']:,.0f}",
                'Capacidad':   f"{rs['capacity']:,}",
                'Uso hist. %': f"{uso_hist:.1f}%",
                '≤1300 kg':    "🔸" if rs.get('is_small') else "",
                'N ejem.':     rs['n_ejemplos'],
            })
        st.dataframe(pd.DataFrame(rs_rows), width='stretch', height=380)

        # Vista previa del template completo
        with st.expander(f"Ver template completo ({len(tmpl)} sucursales)"):
            tmpl_rows = []
            for sid, (veh, dia, seq) in sorted(tmpl.items()):
                tmpl_rows.append({
                    'id_sucursal':   sid,
                    'vehiculo':      veh,
                    'dia_semana':    dia,
                    'seq_hist':      seq,
                    'kg_prom_hist':  round(kg_h.get(sid, 0)),
                })
            st.dataframe(pd.DataFrame(tmpl_rows), width='stretch', height=420)

        st.markdown("")
        if st.button("🗑  Borrar historial y volver a modo estándar", type="secondary"):
            for k in ['vrp_template', 'vrp_kg_hist', 'vrp_route_stats', 'hist_dfs', 'hist_names', 'vrp_report']:
                st.session_state.pop(k, None)
            st.rerun()
    else:
        st.info("No hay historial cargado. Sube archivos CSV arriba para activar el modo VRP.")

# ─────────────────────────────── TAB 2: GENERAR RUTA ─────────────
with tab_gen:
    st.markdown("## Generación de Rutas")

    vrp_active = st.session_state.get('vrp_template') is not None

    if vrp_active:
        n_hist = len(st.session_state.get('hist_dfs', []))
        st.info(
            f"**Modo VRP Híbrido activo** · {n_hist} ejemplo(s) en historial base.  \n"
            f"Las rutas respetan secuencias históricas. Desviación aceptable: "
            f"**≤ 30 % normal** · **30–50 % caso especial** · **> 50 % crítico**.  \n"
            f"Camiones ≤ {UMBRAL_PEQUEÑO:,} kg (T 25, KANGOO): solo se verifica capacidad, sin restricción de desviación."
        )
    else:
        st.markdown(
            "Sube un archivo de pedidos y el sistema asignará automáticamente vehículo, "
            "día y secuencia basándose en el template histórico."
        )
        st.caption("Carga historial en **📚 Historial Base** para activar el modo VRP Híbrido.")

    up_ped = st.file_uploader(
        "Archivo de pedidos (CSV)", type=['csv'], key='ped_up',
        help="Columnas requeridas: id_sucursal, sucursal_cliente, latitud, longitud, kg_entrega"
    )

    if up_ped:
        try:
            df_ped = read_csv_robust(up_ped)
            required = ['id_sucursal', 'latitud', 'longitud', 'kg_entrega']
            missing = [c for c in required if c not in df_ped.columns]
            if missing:
                st.error(f"Columnas faltantes: {missing}")
            else:
                df_ped['id_sucursal'] = df_ped['id_sucursal'].astype(int)
                df_ped['kg_entrega']  = df_ped['kg_entrega'].astype(float)

                col1, col2, col3, col4 = st.columns(4)
                col1.metric("Sucursales", f"{len(df_ped)}")
                col2.metric("kg totales", f"{df_ped['kg_entrega'].sum():,.0f}")
                col3.metric("kg promedio", f"{df_ped['kg_entrega'].mean():,.0f}")
                col4.metric("kg máximo", f"{df_ped['kg_entrega'].max():,.0f}")

                with st.expander("Vista previa de pedidos"):
                    st.dataframe(df_ped, width='stretch', height=200)

                btn_lbl = "🚀  Generar Rutas (VRP Híbrido)" if vrp_active else "🚀  Generar Rutas"
                if st.button(btn_lbl, use_container_width=False):
                    with st.spinner("Calculando rutas..."):
                        if vrp_active:
                            df_result, vrp_rep, consol_log = generate_routes_vrp(
                                df_ped,
                                st.session_state['vrp_template'],
                                st.session_state['vrp_kg_hist'],
                                st.session_state['vrp_route_stats'],
                            )
                            st.session_state['vrp_report']   = vrp_rep
                            st.session_state['consol_log']   = consol_log
                        else:
                            df_result = generate_routes(df_ped)
                            st.session_state.pop('vrp_report', None)
                            st.session_state.pop('consol_log', None)

                    st.session_state['df_result'] = df_result
                    n_vehs = df_result['vehiculo'].nunique() if not df_result.empty else 0
                    st.success(f"✓ {len(df_result)} asignaciones generadas en {n_vehs} vehículos")
        except Exception as e:
            st.error(f"Error leyendo el archivo: {e}")

    if 'df_result' in st.session_state:
        df_r     = st.session_state['df_result']
        vrp_rep  = st.session_state.get('vrp_report')

        # ── Reporte VRP (solo en modo VRP activo) ─────────────────
        if vrp_rep is not None and not vrp_rep.empty:
            st.markdown('<div class="section-title">Reporte VRP</div>', unsafe_allow_html=True)

            consol_log  = st.session_state.get('consol_log', [])
            n_normal    = (vrp_rep['estado'] == 'NORMAL').sum()
            n_edge      = (vrp_rep['estado'] == 'EDGE_CASE').sum()
            n_critico   = (vrp_rep['estado'] == 'CRITICO').sum()
            n_sobre     = (vrp_rep['estado'] == 'SOBRECARGA').sum()
            n_pequeño   = (vrp_rep['estado'] == 'OK_PEQUEÑO').sum()
            n_sinhist   = (vrp_rep['estado'] == 'SIN_HIST').sum()
            n_consol    = (vrp_rep['estado'] == 'CONSOLIDADA').sum()
            total_rutas = len(vrp_rep)

            rc1, rc2, rc3, rc4, rc5, rc6 = st.columns(6)
            rc1.metric("✓ Normal (≤30%)",       n_normal,  f"de {total_rutas}")
            rc2.metric("⚠ Caso especial",        n_edge,    "30–50%")
            rc3.metric("✕ Crítico / Sobrecarga", n_critico + n_sobre, ">50% o cap.")
            rc4.metric("🔸 Camión pequeño",       n_pequeño, "excepción T25")
            rc5.metric("— Sin historial",          n_sinhist, "nuevo patrón")
            rc6.metric("↗ Consolidadas",           n_consol,  "integradas en ruta cercana")

            # Alertas de consolidación realizadas
            if consol_log:
                for entry in consol_log:
                    st.info(
                        f"↗ **Consolidación**: {entry['paradas_movidas']} parada(s) de "
                        f"**{entry['origen']}** ({entry['kg_movidos']:,} kg) integradas en "
                        f"**{entry['destino']}** — distancia {entry['dist_km']} km · {entry['motivo']}"
                    )

            # Alertas de rutas CRITICO o SOBRECARGA
            for _, rw in vrp_rep[vrp_rep['estado'].isin(['CRITICO', 'SOBRECARGA'])].iterrows():
                ico  = "🚨" if rw['estado'] == 'SOBRECARGA' else "⚠️"
                dev  = f"{rw['desviacion_%']:.1f}%" if pd.notna(rw['desviacion_%']) else "—"
                uso  = f"{rw['uso_%']:.1f}%" if pd.notna(rw['uso_%']) else "—"
                hist = f"{int(rw['kg_hist_avg']):,} kg" if pd.notna(rw['kg_hist_avg']) else "sin ref."
                st.warning(
                    f"{ico} **{rw['vehiculo']} — {rw['dia_semana']}**: "
                    f"{int(rw['kg_total']):,} kg entregados · histórico {hist} · "
                    f"desviación **{dev}** · uso capacidad **{uso}**"
                )

            # Gráfica + tabla (excluir filas CONSOLIDADA de la gráfica de desviación)
            vrp_rep_activas = vrp_rep[vrp_rep['estado'] != 'CONSOLIDADA']
            rv1, rv2 = st.columns([3, 2])
            with rv1:
                st.markdown("**Desviación por ruta vs. histórico**")
                st.plotly_chart(fig_vrp_desviacion(vrp_rep_activas), use_container_width=True)

            with rv2:
                st.markdown("**Detalle por ruta**")
                disp = vrp_rep.copy()
                disp['estado'] = disp['estado'].apply(
                    lambda s: f"{VRP_STATUS_ICON.get(s, '?')} {VRP_STATUS_LABEL.get(s, s)}"
                )
                disp = disp.sort_values('desviacion_%', ascending=False, na_position='last')
                cols_show = ['vehiculo', 'dia_semana', 'sucursales', 'kg_total',
                             'kg_hist_avg', 'desviacion_%', 'uso_%', 'estado']
                if 'notas' in disp.columns:
                    cols_show.append('notas')
                st.dataframe(
                    disp[cols_show],
                    use_container_width=True,
                    height=420,
                    column_config={
                        'desviacion_%': st.column_config.ProgressColumn(
                            "Desv. %", format="%.1f%%", min_value=0, max_value=100),
                        'uso_%': st.column_config.ProgressColumn(
                            "Uso cap. %", format="%.1f%%", min_value=0, max_value=150),
                    }
                )

            st.download_button(
                "⬇ Descargar reporte VRP (CSV)",
                to_csv_bytes(vrp_rep),
                "reporte_vrp.csv",
                "text/csv",
            )

        # ── Resultado visual (siempre visible) ────────────────────
        st.markdown('<div class="section-title">Resultado</div>', unsafe_allow_html=True)

        c1, c2 = st.columns([3, 2])
        with c1:
            st.markdown("**Mapa de rutas**")
            st.plotly_chart(fig_mapa(df_r), width='stretch')
        with c2:
            st.markdown("**Uso de capacidad por ruta**")
            st.plotly_chart(fig_uso_capacidad(df_r), width='stretch')

        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown("**kg por día**")
            st.plotly_chart(fig_kg_por_dia(df_r), width='stretch')
        with col_b:
            st.markdown("**Resumen por vehículo**")
            resumen = (df_r.groupby('vehiculo')
                       .agg(rutas=('dia_semana', 'nunique'),
                            sucursales=('id_sucursal', 'count'),
                            kg_total=('kg_entrega', 'sum'))
                       .reset_index())
            resumen['cap_kg'] = resumen['vehiculo'].map(VEHICULOS_CAP)
            resumen['uso_%']  = (resumen['kg_total'] / resumen['cap_kg'] / resumen['rutas'] * 100).round(1)
            st.dataframe(resumen, width='stretch', height=350,
                         column_config={'uso_%': st.column_config.ProgressColumn(
                             "Uso%", format="%.1f%%", min_value=0, max_value=150)})

        st.markdown('<div class="section-title">Detalle completo</div>', unsafe_allow_html=True)

        fc1, fc2 = st.columns(2)
        veh_opts = ['Todos'] + sorted(df_r['vehiculo'].unique())
        dia_opts = ['Todos'] + [d for d in ['LUNES', 'MARTES', 'MIERCOLES', 'JUEVES', 'VIERNES']
                                if d in df_r['dia_semana'].values]
        sel_veh = fc1.selectbox("Filtrar vehículo", veh_opts)
        sel_dia = fc2.selectbox("Filtrar día", dia_opts)

        df_show = df_r.copy()
        if sel_veh != 'Todos': df_show = df_show[df_show['vehiculo'] == sel_veh]
        if sel_dia != 'Todos': df_show = df_show[df_show['dia_semana'] == sel_dia]

        st.dataframe(df_show, width='stretch', height=380)

        st.download_button("⬇ Descargar CSV de rutas", to_csv_bytes(df_r),
                           "rutas_generadas.csv", "text/csv", use_container_width=False)

# ─────────────────────────────── TAB 3: COMPARAR ─────────────────
with tab_cmp:
    st.markdown("## Comparación de Rutas")
    st.markdown("Compara las rutas generadas contra los resultados reales para medir la similitud del sistema.")

    cc1, cc2 = st.columns(2)
    with cc1:
        st.markdown("**Ruta generada**")
        up_gen = st.file_uploader("CSV generado por el sistema", type=['csv'], key='up_gen')
    with cc2:
        st.markdown("**Ruta real**")
        up_real = st.file_uploader("CSV con resultado real", type=['csv'], key='up_real')

    if not (up_gen and up_real):
        st.markdown("")
        if st.button("▶ Usar ejemplo de demostración (Ej5 generado vs real)"):
            st.session_state['demo_compare'] = True

    run_compare = False
    df_g = None; df_a = None

    if up_gen and up_real:
        df_g = read_csv_robust(up_gen)
        df_a = read_csv_robust(up_real)
        run_compare = True
    elif st.session_state.get('demo_compare'):
        st.info("Sube ambos archivos para comparar. Ejemplo: usa 'rutas_generadas.csv' "
                "de la pestaña 📦 y el archivo resultado_ejemplo_*.csv original.")
        st.session_state['demo_compare'] = False

    if run_compare and df_g is not None and df_a is not None:
        for df in [df_g, df_a]:
            df.columns = [c.strip() for c in df.columns]
            df['id_sucursal']      = df['id_sucursal'].astype(int)
            df['secuencia_visita'] = df['secuencia_visita'].astype(int)
            df['kg_entrega']       = df['kg_entrega'].astype(float)

        metrics = compare_routes(df_g, df_a)
        if not metrics:
            st.error("No hay sucursales en común entre los dos archivos.")
        else:
            sim = metrics['sim']
            sim_cls = 'sim-high' if sim >= 75 else ('sim-mid' if sim >= 50 else 'sim-low')

            st.markdown(f"""
            <div class="stat-card" style="text-align:center;border-color:{'#4ecb82' if sim>=75 else ('#f5a623' if sim>=50 else '#e05c5c')}">
              <h4>Índice de Similitud Global</h4>
              <div style="margin:12px 0">
                <span class="sim-badge {sim_cls}">{sim:.2f}%</span>
              </div>
              <div class="sub">Veh+Día × 50% &nbsp;|&nbsp; Secuencia × 30% &nbsp;|&nbsp; Kg × 20%</div>
            </div>
            """, unsafe_allow_html=True)

            n = metrics['n']
            m1, m2, m3, m4, m5 = st.columns(5)
            m1.metric("Vehículo ✓",     f"{metrics['vm']}/{n}", f"{metrics['vm']/n*100:.1f}%")
            m2.metric("Día ✓",          f"{metrics['dm']}/{n}", f"{metrics['dm']/n*100:.1f}%")
            m3.metric("Veh+Día ✓",      f"{metrics['vdm']}/{n}", f"{metrics['vdm']/n*100:.1f}%")
            m4.metric("Veh+Día+Sec ✓",  f"{metrics['sm']}/{n}", f"{metrics['sm']/n*100:.1f}%")
            m5.metric("Todo exacto ✓",  f"{metrics['fm']}/{n}", f"{metrics['fm']/n*100:.1f}%")

            ra1, ra2 = st.columns([1, 2])
            with ra1:
                st.markdown("**Perfil de similitud**")
                st.plotly_chart(fig_similitud_radar(metrics), width='stretch')
            with ra2:
                st.markdown("**Discrepancias en Vehículo / Día**")
                det = pd.DataFrame(metrics['detail'])
                wrong = det[~(det['veh_ok'] & det['dia_ok'])]
                if len(wrong) == 0:
                    st.success("✓ Todos los vehículos y días son correctos.")
                else:
                    wrong_show = wrong[['id_sucursal', 'gen_veh', 'gen_dia',
                                        'act_veh', 'act_dia', 'seq_ok', 'kg_ok']].copy()
                    wrong_show.columns = ['Sucursal', 'Gen Vehículo', 'Gen Día',
                                          'Real Vehículo', 'Real Día', 'Sec OK', 'Kg OK']
                    st.dataframe(wrong_show, width='stretch', height=320)

            with st.expander(f"Ver tabla completa de comparación ({n} sucursales)"):
                det_full = pd.DataFrame(metrics['detail'])
                det_full['✓'] = det_full.apply(
                    lambda r: '✅' if r['veh_ok'] and r['dia_ok'] else '❌', axis=1)
                st.dataframe(
                    det_full[['id_sucursal', '✓', 'gen_veh', 'act_veh', 'gen_dia', 'act_dia',
                               'gen_seq', 'act_seq', 'veh_ok', 'dia_ok', 'seq_ok', 'kg_ok']],
                    width='stretch', height=400)

# ─────────────────────────────── TAB 4: ACERCA ───────────────────
with tab_info:
    st.markdown("## Acerca del sistema")

    col1, col2 = st.columns([3, 2])
    with col1:
        st.markdown("""
**LogiRutas VRP** combina patrones históricos con validación de capacidad para generar
rutas consistentes y eficientes.

### Modo VRP Híbrido

**1. Carga del historial base (📚 Historial Base)**
- El usuario sube uno o más CSVs de rutas confirmadas
- El sistema construye un template dinámico por consenso ponderado
- Los ejemplos más recientes reciben mayor peso automáticamente
- Se calculan estadísticas de kg por ruta (promedio, mín, máx)

**2. Generación con control de desviación (📦 Generar Ruta)**
- Cada sucursal se asigna a su ruta histórica (vehículo + día + secuencia)
- Se compara el kg total de cada ruta vs. el promedio histórico
- Se aplican los umbrales de desviación:
  - **≤ 30 %** → Normal ✓
  - **30–50 %** → Caso especial ⚠ (permitido, señalado)
  - **> 50 %** → Crítico ✕ (requiere revisión)
- Se verifica que ninguna ruta supere la capacidad del vehículo

**3. Excepción camión pequeño (T 25, KANGOO)**
- Vehículos con capacidad **≤ 1,300 kg** reciben tratamiento especial
- Solo se verifica que el total de kg no supere la capacidad
- No se aplica restricción de desviación porcentual
- Sus rutas históricas son las más pequeñas del esquema

**4. Sin nuevas combinaciones**
- Las secuencias y grupos de sucursales provienen exclusivamente del historial
- Sucursales no vistas se asignan por proximidad geográfica (nearest-neighbor)

### Modo estándar (sin historial)

Si no se carga historial, el sistema usa el template estático aprendido
de las 5 semanas Feb–Abr 2026 con el algoritmo nearest-neighbor original.

```bash
# Iniciar la app
streamlit run app_rutas.py
```
        """)

    with col2:
        st.markdown("### Precisión del modelo")
        data = {
            'Ejemplo':     ['Ej1 Feb 9-13', 'Ej2 Feb 23-27', 'Ej3 Mar 9-13', 'Ej4 Mar 23-27', 'Ej5 Abr 6-10'],
            'Veh+Día %':   [17.8, 18.8, 52.5, 50.5, 92.1],
            'Similitud %': [32.2, 33.3, 55.5, 56.6, 89.5],
        }
        df_acc = pd.DataFrame(data)
        fig_acc = go.Figure()
        fig_acc.add_trace(go.Bar(x=df_acc['Ejemplo'], y=df_acc['Similitud %'],
                                 name='Similitud global', marker_color='#f5a623', opacity=0.85))
        fig_acc.add_trace(go.Scatter(x=df_acc['Ejemplo'], y=df_acc['Veh+Día %'],
                                     name='Veh+Día exacto', mode='lines+markers',
                                     line=dict(color='#3ecfcf', width=2),
                                     marker=dict(size=8, color='#3ecfcf')))
        fig_acc.update_layout(template='plotly_dark', paper_bgcolor='#1b2029',
                              plot_bgcolor='#1b2029', height=280,
                              legend=dict(bgcolor='rgba(0,0,0,0)', font=dict(size=11, color='#e8eaf0')),
                              margin=dict(l=10, r=10, t=20, b=40),
                              yaxis=dict(range=[0, 105]),
                              font=dict(family='DM Sans', color='#e8eaf0'))
        st.plotly_chart(fig_acc, width='stretch')

        st.markdown("""
**Interpretación**
La baja precisión en Feb se debe a que el esquema de rutas cambió significativamente
entre esa temporada y abril. El template aprende del patrón **más reciente**,
logrando **92% de precisión** en el último mes.

> Carga los últimos ejemplos en **📚 Historial Base** y activa el modo VRP para
> mantener la precisión alta semana a semana.
        """)

    st.markdown("---")
    st.markdown("### Estructura de archivos")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Pedidos (entrada)**")
        st.code(
            "id_sucursal,sucursal_cliente,latitud,longitud,kg_entrega\n"
            "1,LORES TIERRA 1,18.4487,-96.3580,1139\n"
            "2,LORES CENTRO,18.0843,-96.1253,1435",
            language='text'
        )
    with c2:
        st.markdown("**Resultado / Historial (salida y entrada VRP)**")
        st.code(
            "id_sucursal,vehiculo,dia_semana,secuencia_visita,kg_entrega\n"
            "4,F 350_1,MARTES,1,1079\n"
            "27,F 350_1,MARTES,2,864",
            language='text'
        )
