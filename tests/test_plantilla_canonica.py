"""
tests/test_plantilla_canonica.py

Pruebas de la plantilla canónica (Fase 1, ConVRP).

- Pruebas PURAS (sin BD): helpers, integridad del bridge congelado, y parseo del
  Excel canónico (42 grupos / 23 rígidos / 101 miembros) — se saltan si el Excel
  no está a mano.
- Pruebas de BD (roundtrip carga→lectura): se saltan si no hay conexión a SQL
  Server, para no romper el suite en entornos sin base.
"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
import pytest

from logic.plantilla_canonica import (
    _norm, _parse_nos, _col, _leer_bridge, parsear_plantilla, _resolver_unidad,
    BRIDGE_CSV_DEFAULT,
)


# ── resolución de la unidad de referencia (Excel 'F350_2' vs BD 'F 350_2') ──
def test_resolver_unidad_tolera_espacios_y_acentos():
    unidades = ["F 350_1", "F 350_2", "K 16", "T 17_1", "KANGOO"]
    assert _resolver_unidad("F350_2", unidades) == "F 350_2"
    assert _resolver_unidad("T17_1", unidades) == "T 17_1"
    assert _resolver_unidad("K16", unidades) == "K 16"
    assert _resolver_unidad("KANGOO", unidades) == "KANGOO"


def test_resolver_unidad_no_adivina():
    unidades = ["F 350_1", "K 16"]
    assert _resolver_unidad("XYZ_9", unidades) is None
    assert _resolver_unidad(None, unidades) is None

_RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_XLSX_CANDIDATOS = [
    os.path.join(_RAIZ, "datos", "rutas_canonicas_lores_1.xlsx"),
    r"C:/Users/carli/Downloads/rutas_canonicas_lores_1.xlsx",
]
XLSX = next((p for p in _XLSX_CANDIDATOS if os.path.exists(p)), None)
need_xlsx = pytest.mark.skipif(XLSX is None, reason="Excel canónico no disponible")


# ── helpers puros ──────────────────────────────────────────────────────────
def test_norm_quita_acentos_y_mayus():
    assert _norm(" San Andrés  Tuxtla ") == "SAN ANDRES TUXTLA"
    assert _norm("Cuitláhuac") == "CUITLAHUAC"


def test_parse_nos():
    assert _parse_nos("3, 5 7") == [3, 5, 7]
    assert _parse_nos("31") == [31]
    assert _parse_nos(None) == []
    assert _parse_nos("12/8;9") == [12, 8, 9]


def test_col_encuentra_por_palabras():
    df = pd.DataFrame(columns=["GRUPO", "NO. SUCURSAL", "DÍA (ÚLT. 4 SEM)"])
    assert _col(df, "GRUPO") == "GRUPO"
    assert _col(df, "NO", "SUCURSAL") == "NO. SUCURSAL"
    assert _col(df, "DIA") == "DÍA (ÚLT. 4 SEM)"
    assert _col(df, "NO EXISTE") is None


# ── integridad del bridge congelado (datos/mapeo_no_a_numtienda.csv) ───────
def test_bridge_csv_101_y_num_tienda_unico():
    df = pd.read_csv(BRIDGE_CSV_DEFAULT, encoding="utf-8-sig")
    assert len(df) == 101
    assert df["num_tienda"].nunique() == 101       # biyección
    assert df["no_sucursal"].nunique() == 101


def test_bridge_flags_revisados_no_pendientes():
    bridge, pendientes = _leer_bridge(BRIDGE_CSV_DEFAULT)
    assert len(bridge) == 101
    assert pendientes == []                          # nada sin revisar
    flagged = [(no, r) for no, r in bridge.items() if r["flag"]]
    assert len(flagged) == 2                          # Cd. Isla 1 y San Andrés 1
    assert all(r["estado_revision"] == "revisado_ok" for _, r in flagged)


# ── parseo del Excel canónico (puro, sin BD) ───────────────────────────────
@need_xlsx
def test_parseo_grupos_y_rigidos():
    p = parsear_plantilla(XLSX)
    grupos = p["grupos"]
    assert len(grupos) == 42
    assert sum(1 for g in grupos if g["rigidez"] == "RIGIDO") == 23
    assert sum(1 for g in grupos if g["rigidez"] == "FLEXIBLE") == 19
    # todo grupo tiene al menos un miembro
    con_miembros = {g for g, _ in p["miembros"]}
    assert all(g["grupo"] in con_miembros for g in grupos)
    # 101 miembros, todos con num_tienda resuelto (1..101)
    assert len(p["miembros"]) == 101
    assert all(1 <= nt <= 200 for _, nt in p["miembros"])


@need_xlsx
def test_parseo_zonas_cotaxtla_a_grupo_31():
    p = parsear_plantilla(XLSX)
    zonas = {_norm(z["zona"]): z for z in p["zonas"]}
    assert "COTAXTLA" in zonas
    assert "31" in str(zonas["COTAXTLA"]["grupos_lores"])


@need_xlsx
def test_parseo_dias_admisibles():
    p = parsear_plantilla(XLSX)
    dias = p["dias_admisibles"]
    por_grupo = {}
    for d in dias:
        por_grupo.setdefault(d["grupo"], []).append(d)
    assert len(por_grupo) == 42                       # todos los grupos cubiertos
    n_multi = sum(1 for g, ds in por_grupo.items() if len(ds) > 1)
    n_uno = sum(1 for g, ds in por_grupo.items() if len(ds) == 1)
    assert n_multi == 26 and n_uno == 16              # coincide con el análisis
    # cada grupo tiene exactamente un día marcado canónico y está en su set
    for g, ds in por_grupo.items():
        canon = [x for x in ds if x["es_canonico"]]
        assert len(canon) == 1


# ── roundtrip BD (se salta si no hay SQL Server) ───────────────────────────
@pytest.fixture(scope="module")
def app_ctx():
    try:
        from app import create_app
        app = create_app()
        ctx = app.app_context(); ctx.push()
        from db import get_db, get_table
        get_db().execute  # fuerza apertura de conexión
        get_table("plantilla_meta")
    except Exception as e:  # sin BD o sin tablas
        pytest.skip(f"BD no disponible: {e}")
        return
    yield app
    ctx.pop()


def test_roundtrip_lectura_bd(app_ctx):
    from logic.plantilla_canonica import (
        version_vigente, obtener_grupos, grupo_de_sucursal, zona_de_poblacion,
    )
    assert version_vigente() is not None
    grupos = obtener_grupos()
    assert len(grupos) == 42
    assert sum(1 for g in grupos if g["rigidez"] == "RIGIDO") == 23
    # grupo_de_sucursal round-trip sobre un miembro real
    algun = next(g for g in grupos if g["sucursales"])
    nt = algun["sucursales"][0]
    assert grupo_de_sucursal(nt)["grupo"] == algun["grupo"]
    # población desconocida → None (cae a fallback global, no se adivina)
    assert zona_de_poblacion("POBLACION_QUE_NO_EXISTE_XYZ") is None
    # días admisibles: cada grupo tiene ≥1 y el preferido está en el set
    for g in grupos:
        assert g["dias_admisibles"], f"grupo {g['grupo']} sin días admisibles"
        assert g["dia_preferido"] in g["dias_admisibles"]


# ── derivación de rigidez/día/unidad para zonas que fusionan grupos viejos ──
def _grupo_info(rigidez, dia, unidad_ref, dias_admisibles, forzada=False):
    return dict(rigidez=rigidez, dia=dia, dia_preferido=dia,
                unidad_ref=unidad_ref, unidades_afines=None,
                unidad_forzada=forzada, dias_admisibles=dias_admisibles)


def test_derivar_grupo_zona_gana_mayoria_clara():
    from logic.plantilla_canonica import derivar_grupo_zona
    grupo_de_sucursal = {1: 10, 2: 10, 3: 10, 4: 10, 5: 20, 6: 20}
    grupos_por_id = {
        10: _grupo_info("RIGIDO", "MARTES", "F 350_1", ["MARTES"], forzada=True),
        20: _grupo_info("FLEXIBLE", "JUEVES", "F 350_2", ["JUEVES"]),
    }
    d = derivar_grupo_zona([1, 2, 3, 4, 5, 6], grupo_de_sucursal, grupos_por_id)
    assert d["grupo_origen"] == 10
    assert d["pct"] == pytest.approx(4 / 6)
    assert d["revisar"] is False
    assert d["rigidez"] == "RIGIDO"
    assert d["unidad_ref"] == "F 350_1"
    assert d["unidad_forzada"] is True


def test_derivar_grupo_zona_empate_gana_menor_numero():
    from logic.plantilla_canonica import derivar_grupo_zona
    grupo_de_sucursal = {1: 20, 2: 20, 3: 20, 4: 10, 5: 10, 6: 10}
    grupos_por_id = {
        10: _grupo_info("RIGIDO", "LUNES", "K 16", ["LUNES"]),
        20: _grupo_info("FLEXIBLE", "MARTES", "T 20", ["MARTES"]),
    }
    d = derivar_grupo_zona([1, 2, 3, 4, 5, 6], grupo_de_sucursal, grupos_por_id)
    assert d["grupo_origen"] == 10          # empate 3-3, gana el numero mas bajo
    assert d["pct"] == pytest.approx(0.5)


def test_derivar_grupo_zona_bajo_umbral_marca_revisar():
    from logic.plantilla_canonica import derivar_grupo_zona
    grupo_de_sucursal = {1: 10, 2: 20, 3: 30, 4: 40}
    grupos_por_id = {
        10: _grupo_info("RIGIDO", "LUNES", "K 16", ["LUNES"]),
        20: _grupo_info("RIGIDO", "LUNES", "T 20", ["LUNES"]),
        30: _grupo_info("FLEXIBLE", "LUNES", "T 23", ["LUNES"]),
        40: _grupo_info("FLEXIBLE", "LUNES", "T 25", ["LUNES"]),
    }
    d = derivar_grupo_zona([1, 2, 3, 4], grupo_de_sucursal, grupos_por_id)
    assert d["grupo_origen"] == 10
    assert d["pct"] == 0.25
    assert d["revisar"] is True


# ── datos estaticos de la reorganizacion de zonas 2026-08 (sin BD) ─────────
def test_zonas_cubren_101_sucursales_sin_duplicados():
    from scripts.reorganizar_zonas_2026 import ZONAS_SIMPLES, SUB_RUTAS_ESPECIALES
    todos = [s for sucs in ZONAS_SIMPLES.values() for s in sucs]
    todos += [s for r in SUB_RUTAS_ESPECIALES for s in r["sucursales"]]
    assert len(todos) == 101
    assert len(set(todos)) == 101


def test_solo_zona_22_supera_6_sucursales():
    from scripts.reorganizar_zonas_2026 import ZONAS_SIMPLES, SUB_RUTAS_ESPECIALES
    for zona, sucs in ZONAS_SIMPLES.items():
        limite = 8 if zona == 22 else 6
        assert len(sucs) <= limite, f"zona {zona} tiene {len(sucs)} sucursales"
    for r in SUB_RUTAS_ESPECIALES:
        assert len(r["sucursales"]) <= 6, r


def test_sub_rutas_especiales_grupo_y_zona():
    from scripts.reorganizar_zonas_2026 import SUB_RUTAS_ESPECIALES
    por_grupo = {r["grupo"]: r["zona"] for r in SUB_RUTAS_ESPECIALES}
    assert por_grupo == {5: 5, 25: 5, 26: 5, 11: 11, 27: 11}


def test_construir_sub_rutas_agrega_24_zonas():
    from scripts.reorganizar_zonas_2026 import ZONAS_SIMPLES, SUB_RUTAS_ESPECIALES
    zonas = set(ZONAS_SIMPLES) | {r["zona"] for r in SUB_RUTAS_ESPECIALES}
    assert zonas == set(range(1, 25))
    grupos_simples = set(ZONAS_SIMPLES)          # grupo == zona para las simples
    grupos_especiales = {r["grupo"] for r in SUB_RUTAS_ESPECIALES}
    assert len(grupos_simples) + len(grupos_especiales) == 27
    assert not (grupos_simples & grupos_especiales)   # sin colision de numero
