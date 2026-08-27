"""
tests/test_convrp_integracion.py

`_elegir_mejor_pasada` -- cuando el enganche de mayoristas por zona
(`construir_rutas_con_mayoristas`) no llega a punto fijo en `max_pasadas`,
hay que decidir cuál de las pasadas usar. Hallado en producción 2026-08-12:
el enganche oscila en un ciclo de 2 (nunca converge), y con `max_pasadas`
par el corte siempre caía en la mitad "mala" del ciclo -- grupos completos
desviados de su unidad_ref sin ningún motivo geográfico (Tuxtepec+Veracruz
en el mismo camión, luego Veracruz+Amatitlán en otro). Elegir la pasada con
MENOS excepciones (desviaciones de unidad) en vez de la última por paridad
resuelve ambos casos sin pinnear grupo por grupo. Puro: sin BD.
"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import logic.convrp_integracion as ci
from logic.convrp_integracion import _elegir_mejor_pasada, construir_groups_convrp


def _pasada(n_excepciones):
    return {"excepciones": [{"tipo": "MOVIDO_UNIDAD"}] * n_excepciones}


def test_elige_la_pasada_con_menos_excepciones():
    historial = [_pasada(2), _pasada(0), _pasada(1)]
    assert _elegir_mejor_pasada(historial) == 1


def test_empate_elige_la_pasada_mas_temprana():
    historial = [_pasada(0), _pasada(1), _pasada(0)]
    assert _elegir_mejor_pasada(historial) == 0


def test_una_sola_pasada_se_elige_a_si_misma():
    assert _elegir_mejor_pasada([_pasada(3)]) == 0


def test_ciclo_de_dos_prefiere_la_mitad_sin_desviaciones():
    # Replica el patrón real: pasada 1 limpia, pasada 2 con desviaciones,
    # pasada 3 limpia otra vez, pasada 4 con desviaciones -- el corte por
    # paridad (max_pasadas=4) siempre caía en una pasada con desviaciones.
    historial = [_pasada(0), _pasada(2), _pasada(0), _pasada(2)]
    elegido = _elegir_mejor_pasada(historial)
    assert elegido in (0, 2)
    assert historial[elegido]["excepciones"] == []
