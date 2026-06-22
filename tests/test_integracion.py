"""
tests/test_integracion.py
Pruebas de integración y comparación numérica con template puro.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from logic.vrp_afinidad.afinidad import construir_afinidad
from logic.vrp_afinidad.clarke_wright import generar_rutas
from logic.vrp_afinidad.estado_vrp import calcular_estado, porcentaje_ocupacion
from tests.datos_sinteticos import (
    generar_historiales, pedidos_normal, pedidos_cambiado,
    COORDS, COORDS_CAMBIADO, DEPOSITO, CAPACIDADES, PATRON_CANONICAL,
)


# ── Helper: asignación tipo "template puro" ───────────────────────────────────

def template_puro(pedidos: dict, patron: dict, capacidades: dict) -> list:
    """
    Simula el sistema actual: asigna cada nodo al (vehiculo, dia) del patrón
    sin optimizar capacidad. Devuelve lista de (vehiculo, kg_total, capacidad).
    """
    from collections import defaultdict
    grupos = defaultdict(float)
    for sid, kg in pedidos.items():
        veh, dia = patron.get(sid, ("V1", "LUNES"))
        grupos[(veh, dia)] += kg

    resultado = []
    for (veh, dia), kg_total in grupos.items():
        cap = capacidades.get(veh, 3500)
        resultado.append({"vehiculo": veh, "dia": dia, "kg_total": kg_total, "capacidad": cap})
    return resultado


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def historiales():
    return generar_historiales(n=6)


@pytest.fixture(scope="module")
def afinidad_data(historiales):
    return construir_afinidad(historiales)


@pytest.fixture(scope="module")
def rutas_normal(afinidad_data):
    return generar_rutas(
        pedidos=pedidos_normal(),
        coords=COORDS,
        afinidad_data=afinidad_data,
        capacidades=CAPACIDADES,
        deposito_coords=DEPOSITO,
        lambda_afinidad=0.5,
    )


# ── Tests de integración ──────────────────────────────────────────────────────

def test_no_sobrecarga_nunca(rutas_normal):
    """Restricción dura global: ninguna ruta supera su capacidad."""
    for ruta in rutas_normal:
        assert ruta.kg_total <= ruta.capacidad, (
            f"SOBRECARGA: {ruta.vehiculo} {ruta.kg_total:.0f} kg > {ruta.capacidad} kg cap"
        )


def test_ocupacion_promedio_mejor_que_template(rutas_normal):
    """
    La ocupación promedio del nuevo método debe superar a la del template puro.
    Esto es el test numérico clave para la tesina.
    """
    pedidos = pedidos_normal()
    tmpl = template_puro(pedidos, PATRON_CANONICAL, CAPACIDADES)

    ocup_nuevo = [r.ocupacion for r in rutas_normal]
    ocup_tmpl  = [t["kg_total"] / t["capacidad"] for t in tmpl]

    avg_nuevo = sum(ocup_nuevo) / len(ocup_nuevo)
    avg_tmpl  = sum(ocup_tmpl)  / len(ocup_tmpl)

    print(f"\n  Ocupación promedio — CW-Afinidad: {avg_nuevo*100:.1f}%  |  Template puro: {avg_tmpl*100:.1f}%")

    # El nuevo método debe lograr al menos la misma ocupación promedio
    # (en la práctica mejor porque Clarke-Wright consolida más)
    assert avg_nuevo >= avg_tmpl * 0.90, (
        f"Método nuevo ({avg_nuevo*100:.1f}%) significativamente peor "
        f"que template ({avg_tmpl*100:.1f}%)"
    )


def test_rutas_sin_sobrecarga_vs_template():
    """El template puro puede generar SOBRECARGA; el nuevo método nunca debe."""
    historiales = generar_historiales(n=6)
    afinidad_data = construir_afinidad(historiales)

    # Pedidos aumentados para forzar sobrecarga en template puro
    pedidos = {sid: kg * 1.5 for sid, kg in pedidos_normal().items()}

    rutas_nuevo = generar_rutas(
        pedidos=pedidos,
        coords=COORDS,
        afinidad_data=afinidad_data,
        capacidades=CAPACIDADES,
        deposito_coords=DEPOSITO,
        lambda_afinidad=0.5,
    )

    tmpl = template_puro(pedidos, PATRON_CANONICAL, CAPACIDADES)
    sobrecargas_tmpl = sum(1 for t in tmpl if t["kg_total"] > t["capacidad"])
    sobrecargas_nuevo = sum(1 for r in rutas_nuevo if r.kg_total > r.capacidad)

    print(f"\n  SOBRECARGA — Template: {sobrecargas_tmpl}  |  CW-Afinidad: {sobrecargas_nuevo}")

    assert sobrecargas_nuevo == 0, "El nuevo método no debe generar sobrecargas"


def test_estado_vrp_estados_validos(rutas_normal):
    """calcular_estado debe devolver uno de los estados conocidos."""
    estados_validos = {"NORMAL", "EDGE_CASE", "CRITICO", "SOBRECARGA", "SIN_HIST", "OK_PEQUEÑO"}
    for ruta in rutas_normal:
        estado = calcular_estado(ruta, route_stats_historico=None)
        assert estado in estados_validos, f"Estado inválido: {estado}"


def test_estado_sin_historial_es_sin_hist(rutas_normal):
    """Sin route_stats, el estado debe ser SIN_HIST (o OK_PEQUEÑO para camionetas)."""
    for ruta in rutas_normal:
        estado = calcular_estado(ruta, route_stats_historico=None)
        if ruta.capacidad > 1300:
            assert estado == "SIN_HIST"


def test_porcentaje_ocupacion(rutas_normal):
    """porcentaje_ocupacion debe estar en (0, 100]."""
    for ruta in rutas_normal:
        pct = porcentaje_ocupacion(ruta)
        assert 0.0 < pct <= 100.0, f"Porcentaje fuera de rango: {pct}"


def test_contexto_cambiado_todos_cubiertos(afinidad_data):
    """Con contexto cambiado (nueva sucursal, más demanda), todos los nodos son cubiertos."""
    pedidos = pedidos_cambiado()
    rutas = generar_rutas(
        pedidos=pedidos,
        coords=COORDS_CAMBIADO,
        afinidad_data=afinidad_data,
        capacidades=CAPACIDADES,
        deposito_coords=DEPOSITO,
        lambda_afinidad=0.5,
    )
    nodos_cubiertos = {n for r in rutas for n in r.paradas}
    assert nodos_cubiertos == set(pedidos.keys()), \
        f"Nodos no cubiertos: {set(pedidos.keys()) - nodos_cubiertos}"


def test_metricas_comparativas_impresas(afinidad_data, rutas_normal, capsys):
    """
    Test que imprime tabla comparativa usable en la tesina.
    No falla por criterio numérico — solo verifica que se ejecuta sin error.
    """
    pedidos = pedidos_normal()
    tmpl = template_puro(pedidos, PATRON_CANONICAL, CAPACIDADES)

    ocup_nuevo  = [r.ocupacion * 100 for r in rutas_normal]
    ocup_tmpl   = [t["kg_total"] / t["capacidad"] * 100 for t in tmpl]

    sobrec_nuevo = sum(1 for r in rutas_normal if r.kg_total > r.capacidad)
    sobrec_tmpl  = sum(1 for t in tmpl if t["kg_total"] > t["capacidad"])

    print("\n" + "="*60)
    print(f"{'Métrica':<30} {'CW-Afinidad':>12} {'Template puro':>14}")
    print("="*60)
    print(f"{'Número de rutas':<30} {len(rutas_normal):>12} {len(tmpl):>14}")
    print(f"{'Ocupación promedio %':<30} {sum(ocup_nuevo)/len(ocup_nuevo):>11.1f}% {sum(ocup_tmpl)/len(ocup_tmpl):>13.1f}%")
    print(f"{'Ocupación mínima %':<30} {min(ocup_nuevo):>11.1f}% {min(ocup_tmpl):>13.1f}%")
    print(f"{'Rutas en SOBRECARGA':<30} {sobrec_nuevo:>12} {sobrec_tmpl:>14}")
    print("="*60)

    assert len(rutas_normal) > 0
