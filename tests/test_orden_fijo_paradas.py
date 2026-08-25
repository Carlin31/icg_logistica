"""
tests/test_orden_fijo_paradas.py
"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from logic.orden_fijo_paradas import aplicar_orden_fijo


def test_aplica_orden_fijo_cuando_toda_la_ruta_pertenece_a_la_regla():
    orden_fijo = {4: ("regla_a", 1), 27: ("regla_a", 2), 75: ("regla_a", 3)}
    miembros = [{"sid": 75}, {"sid": 4}, {"sid": 27}]
    assert aplicar_orden_fijo(miembros, orden_fijo) == [4, 27, 75]


def test_aplica_orden_fijo_con_sucursales_faltantes_esa_semana():
    orden_fijo = {4: ("regla_a", 1), 27: ("regla_a", 2), 75: ("regla_a", 3)}
    miembros = [{"sid": 75}, {"sid": 4}]          # sin la 27 esta semana
    assert aplicar_orden_fijo(miembros, orden_fijo) == [4, 75]


def test_no_aplica_si_hay_una_sucursal_ajena_a_la_regla():
    orden_fijo = {4: ("regla_a", 1), 27: ("regla_a", 2)}
    miembros = [{"sid": 4}, {"sid": 27}, {"sid": 999}]   # 999 no está en ninguna regla
    assert aplicar_orden_fijo(miembros, orden_fijo) is None


def test_no_aplica_si_mezcla_dos_reglas_distintas():
    orden_fijo = {4: ("regla_a", 1), 100: ("regla_b", 1)}
    miembros = [{"sid": 4}, {"sid": 100}]
    assert aplicar_orden_fijo(miembros, orden_fijo) is None


def test_no_aplica_con_orden_fijo_vacio():
    miembros = [{"sid": 4}, {"sid": 27}]
    assert aplicar_orden_fijo(miembros, {}) is None


def test_no_aplica_con_miembros_vacios():
    assert aplicar_orden_fijo([], {4: ("regla_a", 1)}) is None
