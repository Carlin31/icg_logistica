"""
tests/datos_sinteticos.py
Generador de historiales y pedidos sintéticos para probar vrp_afinidad.

Geografía simulada: Veracruz-Xalapa, México.
Depósito: ~Veracruz centro.
Clusters:
  A — Norte (Nautla, Martínez de la Torre, …)  — vehículo V1, LUNES
  B — Sur  (Alvarado, Tlacotalpan, …)           — vehículo V2, MARTES
  C — Montaña (Xalapa, Coatepec, …)             — vehículo V3, MIERCOLES
Mayoristas: 5 clientes grandes, cada uno va solo o en pareja.
"""
from datetime import date, timedelta
import random

DEPOSITO = (19.1738, -96.1342)  # Veracruz, Ver.

# Flota
CAPACIDADES = {
    "V1": 5000,   # camión grande
    "V2": 4000,
    "V3": 3000,
    "V4": 1200,   # camioneta pequeña
}

# ── Coordenadas de sucursales (30 sucursales) ─────────────────────────────────
# Cluster A — Norte (ids 1-10)
CLUSTER_A = {
    1:  (19.42, -96.88),
    2:  (19.50, -96.79),
    3:  (19.61, -96.60),
    4:  (19.68, -96.42),
    5:  (19.75, -96.35),
    6:  (19.38, -96.95),
    7:  (19.45, -96.72),
    8:  (19.53, -96.65),
    9:  (19.59, -96.55),
    10: (19.30, -97.00),
}

# Cluster B — Sur (ids 11-20)
CLUSTER_B = {
    11: (18.78, -95.80),
    12: (18.65, -95.75),
    13: (18.60, -96.10),
    14: (18.50, -96.20),
    15: (18.70, -95.70),
    16: (18.90, -96.05),
    17: (18.55, -96.00),
    18: (18.45, -96.15),
    19: (18.80, -95.90),
    20: (18.62, -95.85),
}

# Cluster C — Montaña (ids 21-30)
CLUSTER_C = {
    21: (19.52, -97.01),
    22: (19.45, -97.08),
    23: (19.35, -97.10),
    24: (19.60, -96.95),
    25: (19.40, -97.15),
    26: (19.55, -97.05),
    27: (19.30, -97.20),
    28: (19.48, -97.02),
    29: (19.62, -96.90),
    30: (19.25, -97.18),
}

# Mayoristas (ids 101-105) — van solos o en pareja por su volumen
MAYORISTAS = {
    101: (19.20, -96.20),
    102: (18.85, -96.10),
    103: (19.55, -96.80),
    104: (19.10, -96.50),
    105: (18.70, -96.30),
}

COORDS = {**CLUSTER_A, **CLUSTER_B, **CLUSTER_C, **MAYORISTAS}

# Patrones canónicos: quién va en qué vehículo y día
PATRON_CANONICAL = {
    **{sid: ("V1", "LUNES")    for sid in CLUSTER_A},
    **{sid: ("V2", "MARTES")   for sid in CLUSTER_B},
    **{sid: ("V3", "MIERCOLES") for sid in CLUSTER_C},
    101: ("V1", "LUNES"),    # mayorista norte — va con V1
    102: ("V2", "MARTES"),   # mayorista sur
    103: ("V1", "LUNES"),
    104: ("V4", "JUEVES"),   # mayorista especial, camioneta
    105: ("V2", "MARTES"),
}

# kg típicos por nodo
KG_BASE = {
    **{sid: random.randint(200, 400) for sid in CLUSTER_A},
    **{sid: random.randint(150, 350) for sid in CLUSTER_B},
    **{sid: random.randint(180, 320) for sid in CLUSTER_C},
    101: 900,
    102: 800,
    103: 750,
    104: 400,
    105: 700,
}


def _generar_filas(patron: dict, kg_map: dict, ruido: float = 0.05) -> list:
    """Genera filas de un historial a partir de un patrón con ruido de kg."""
    rng = random.Random()
    filas = []
    # Construir secuencias por ruta
    rutas: dict = {}
    for sid, (veh, dia) in patron.items():
        rutas.setdefault((veh, dia), []).append(sid)

    for (veh, dia), miembros in rutas.items():
        for seq, sid in enumerate(miembros, 1):
            kg = kg_map.get(sid, 200) * (1 + rng.uniform(-ruido, ruido))
            filas.append({
                "id_sucursal":      sid,
                "vehiculo":         veh,
                "dia_semana":       dia,
                "secuencia_visita": seq,
                "kg_entrega":       round(kg),
                "tipo":             "mayorista" if sid >= 100 else "sucursal",
            })
    return filas


def generar_historiales(n: int = 6, seed: int = 42) -> list:
    """
    Genera n historiales ordenados del más antiguo al más reciente.
    Los 4 primeros son CSV importados (confirmada=False),
    los 2 últimos son confirmados desde el panel.
    Los patrones son casi idénticos — pequeñas variaciones de kg.
    """
    random.seed(seed)
    base_date = date(2024, 1, 1)
    historiales = []

    for i in range(n):
        confirmada = (i >= n - 2)
        fecha = base_date + timedelta(weeks=i * 4)
        filas = _generar_filas(PATRON_CANONICAL, KG_BASE, ruido=0.08)
        hist = {
            "nombre":      f"Logística semana {i+1}",
            "confirmada":  confirmada,
            "fecha_inicio": fecha.isoformat(),
            "cargado_en":  fecha.isoformat() + "T00:00:00",
            "filas":       filas,
        }
        historiales.append(hist)
    return historiales


def pedidos_normal(seed: int = 1) -> dict:
    """Pedidos similares al historial (todos los nodos del patrón canónico)."""
    random.seed(seed)
    return {
        sid: round(kg * (1 + random.uniform(-0.05, 0.05)))
        for sid, kg in KG_BASE.items()
    }


def pedidos_cambiado(seed: int = 2) -> dict:
    """
    Contexto cambiado:
    - Mayor demanda en algunos nodos (+30%)
    - Una sucursal nueva (id=31) cerca del cluster C
    - Sin vehículo V1 (se simula limitando capacidades en el test)
    """
    random.seed(seed)
    pedidos = {}
    for sid, kg in KG_BASE.items():
        if sid in {3, 4, 21, 22}:
            pedidos[sid] = round(kg * 1.30)  # demanda alta
        else:
            pedidos[sid] = round(kg * (1 + random.uniform(-0.05, 0.05)))

    # Sucursal nueva cerca del cluster C
    pedidos[31] = 250
    return pedidos


COORDS_CAMBIADO = {**COORDS, 31: (19.44, -97.06)}  # nueva sucursal, cerca de C
