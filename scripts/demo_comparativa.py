"""
demo_comparativa.py
Genera rutas con CW-Afinidad y con Template Puro, e imprime tabla comparativa.
Uso: python scripts/demo_comparativa.py
"""
import sys, os
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from collections import defaultdict
from logic.vrp_afinidad.afinidad import construir_afinidad
from logic.vrp_afinidad.clarke_wright import generar_rutas
from tests.datos_sinteticos import (
    generar_historiales, pedidos_normal, pedidos_cambiado,
    COORDS, COORDS_CAMBIADO, DEPOSITO, CAPACIDADES, PATRON_CANONICAL,
)


# ── Template puro (simula el sistema actual) ──────────────────────────────────

def _template_puro(pedidos: dict, patron: dict, capacidades: dict) -> list:
    grupos = defaultdict(float)
    for sid, kg in pedidos.items():
        veh, dia = patron.get(sid, ("V1", "LUNES"))
        grupos[(veh, dia)] += kg
    resultado = []
    for (veh, dia), kg_total in grupos.items():
        cap = capacidades.get(veh, 3500)
        resultado.append({
            "vehiculo": veh, "dia": dia,
            "kg_total": kg_total, "capacidad": cap,
        })
    return resultado


# ── Coincidencia con el historico ─────────────────────────────────────────────

def _rutas_identicas(rutas_cw, patron):
    """Cuenta rutas donde >=80% de paradas coinciden con el mismo (veh, dia) historico."""
    identicas = 0
    for ruta in rutas_cw:
        conteo = defaultdict(int)
        for n in ruta.paradas:
            vd = patron.get(n, ("?", "?"))
            conteo[vd] += 1
        if not conteo:
            continue
        _, cnt = max(conteo.items(), key=lambda x: x[1])
        if cnt / len(ruta.paradas) >= 0.80:
            identicas += 1
    return identicas


# ── Tabla comparativa ─────────────────────────────────────────────────────────

def _tabla(titulo, rutas_cw, tmpl):
    ocup_cw   = [r.kg_total / r.capacidad * 100 for r in rutas_cw]
    ocup_tmpl = [t["kg_total"] / t["capacidad"] * 100 for t in tmpl]

    sob_cw   = sum(1 for r in rutas_cw if r.kg_total > r.capacidad)
    sob_tmpl = sum(1 for t in tmpl if t["kg_total"] > t["capacidad"])
    id_cw    = _rutas_identicas(rutas_cw, PATRON_CANONICAL)

    sep = "=" * 66
    lin = "-" * 60

    def fila(etiqueta, val_cw, val_tmpl):
        print(f"  {etiqueta:<32} {val_cw:>12} {val_tmpl:>14}")

    print(f"\n{sep}")
    print(f"  {titulo}")
    print(sep)
    print(f"  {'Metrica':<32} {'CW-Afinidad':>12} {'Template puro':>14}")
    print(f"  {lin}")
    fila("Numero de rutas",        len(rutas_cw),           len(tmpl))
    fila("Ocupacion promedio %",   f"{sum(ocup_cw)/len(ocup_cw):.1f}%",
                                   f"{sum(ocup_tmpl)/len(ocup_tmpl):.1f}%")
    fila("Ocupacion minima %",     f"{min(ocup_cw):.1f}%",   f"{min(ocup_tmpl):.1f}%")
    fila("Ocupacion maxima %",     f"{max(ocup_cw):.1f}%",   f"{max(ocup_tmpl):.1f}%")
    fila("Rutas en SOBRECARGA",    sob_cw,                   sob_tmpl)
    fila("Rutas >=90% ocupacion",  sum(1 for x in ocup_cw  if x >= 90),
                                   sum(1 for x in ocup_tmpl if x >= 90))
    fila("Rutas identicas al hist", id_cw,                   "N/A")
    print(sep)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("\n  Generando historiales sinteticos...")
    historiales = generar_historiales(n=6)
    afinidad_data = construir_afinidad(historiales)

    print(f"  Historiales  : {len(historiales)}")
    print(f"  Nodos vistos : {len(afinidad_data['nodos'])}")
    print(f"  Pares afin.  : {len(afinidad_data['afinidad'])}")

    # ── Escenario 1: contexto normal ──────────────────────────────────────────
    pedidos_n  = pedidos_normal()
    rutas_cw_n = generar_rutas(
        pedidos=pedidos_n,
        coords=COORDS,
        afinidad_data=afinidad_data,
        capacidades=CAPACIDADES,
        deposito_coords=DEPOSITO,
        lambda_afinidad=0.5,
    )
    tmpl_n = _template_puro(pedidos_n, PATRON_CANONICAL, CAPACIDADES)
    _tabla("ESCENARIO 1 -- Contexto normal (pedidos similares al historial)",
           rutas_cw_n, tmpl_n)

    # ── Escenario 2: contexto cambiado ────────────────────────────────────────
    pedidos_c  = pedidos_cambiado()
    rutas_cw_c = generar_rutas(
        pedidos=pedidos_c,
        coords=COORDS_CAMBIADO,
        afinidad_data=afinidad_data,
        capacidades=CAPACIDADES,
        deposito_coords=DEPOSITO,
        lambda_afinidad=0.5,
    )
    patron_c = {**PATRON_CANONICAL, 31: ("V3", "MIERCOLES")}
    tmpl_c   = _template_puro(pedidos_c, patron_c, CAPACIDADES)
    _tabla("ESCENARIO 2 -- Contexto cambiado (+demanda, sucursal nueva)",
           rutas_cw_c, tmpl_c)

    # ── Escenario 3: lambda maximo (maximo sesgo historico) ───────────────────
    rutas_cw_l1 = generar_rutas(
        pedidos=pedidos_n,
        coords=COORDS,
        afinidad_data=afinidad_data,
        capacidades=CAPACIDADES,
        deposito_coords=DEPOSITO,
        lambda_afinidad=1.0,
    )
    _tabla("ESCENARIO 3 -- Contexto normal, lambda_afinidad=1.0",
           rutas_cw_l1, tmpl_n)

    # ── Desglose por ruta (escenario 1) ──────────────────────────────────────
    print("\n  DESGLOSE POR RUTA -- CW-Afinidad, Escenario 1")
    print(f"  {'#':<4} {'Vehiculo':<8} {'Paradas':>8} {'kg':>8} {'Cap':>7} {'Ocup%':>7}")
    print(f"  {'-'*45}")
    for idx, r in enumerate(sorted(rutas_cw_n, key=lambda x: -x.ocupacion), 1):
        print(f"  {idx:<4} {r.vehiculo:<8} {len(r.paradas):>8} "
              f"{r.kg_total:>8.0f} {r.capacidad:>7} {r.ocupacion*100:>6.1f}%")

    print()


if __name__ == "__main__":
    main()
