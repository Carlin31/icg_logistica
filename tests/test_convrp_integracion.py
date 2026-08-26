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


# --- construir_groups_convrp: guard de unidad_ref sin resolver -------------
#
# El guard (línea ~104) originalmente trataba unidad_ref=None igual que un
# valor de Excel sin resolver contra el catálogo -- pero None es el valor
# deliberado para "sin preferencia histórica de unidad" (grupos 11 y 27,
# Zona 11 / Tierra Blanca, ver docs/superpowers/specs/
# 2026-08-26-reorganizacion-zonas-canonicas-design.md). Con la plantilla
# vigente real conteniendo esos grupos, construir_groups_convrp() explotaba
# siempre, sin importar si esa zona tenía pedidos esa semana.
#
# Se mockean las demás colaboraciones con BD (obtener_grupos, version_vigente,
# _coocurrencia_de_bd, construir_groups_desde_plantilla) siguiendo el mismo
# patrón de monkeypatch.setattr(modulo, "funcion", ...) que usan
# tests/test_candado_historico.py y tests/test_resolver_sobrecarga_mayoristas.py
# -- aquí no hay nada que probar del motor en sí, sólo si el guard deja pasar
# o no antes de llegar a él.

def _stub_colaboradores_bd(monkeypatch, plantilla):
    monkeypatch.setattr(ci, "obtener_grupos", lambda: plantilla)
    monkeypatch.setattr(ci, "version_vigente", lambda: "v-test")
    monkeypatch.setattr(ci, "horarios_por_dia", lambda: {})
    monkeypatch.setattr(ci, "_coocurrencia_de_bd", lambda plantilla: {})
    monkeypatch.setattr(
        ci, "construir_groups_desde_plantilla",
        lambda *a, **k: ({}, []))


def test_unidad_ref_none_no_dispara_el_guard_de_sin_resolver(monkeypatch):
    # Grupo 11 (Zona 11 / Tierra Blanca): unidad_ref=None es "sin preferencia"
    # a propósito, no un dato roto -- no debe levantar ValueError.
    plantilla = [
        {"grupo": 11, "unidad_ref": None, "sucursales": [], "unidades_afines": None},
        {"grupo": 3, "unidad_ref": "F350_1", "sucursales": [], "unidades_afines": None},
    ]
    vehiculos_cap = {"F350_1": 1000}
    _stub_colaboradores_bd(monkeypatch, plantilla)

    groups, excepciones, meta = construir_groups_convrp(
        pedidos_dict={}, volumenes_dict={}, coords_dict={},
        vehiculos_cap=vehiculos_cap, vehiculos_vol={}, depot=(0, 0))

    assert groups == {}
    assert excepciones == []


def test_unidad_ref_no_resuelto_contra_catalogo_si_dispara_el_guard(monkeypatch):
    # Caso real que el guard debe seguir atrapando: un unidad_ref no-None que
    # no existe en el catálogo de vehículos (p.ej. import de Excel roto).
    plantilla = [
        {"grupo": 5, "unidad_ref": "UNIDAD_QUE_NO_EXISTE", "sucursales": [],
         "unidades_afines": None},
    ]
    vehiculos_cap = {"F350_1": 1000}
    _stub_colaboradores_bd(monkeypatch, plantilla)

    with pytest.raises(ValueError, match="unidad_ref sin resolver"):
        construir_groups_convrp(
            pedidos_dict={}, volumenes_dict={}, coords_dict={},
            vehiculos_cap=vehiculos_cap, vehiculos_vol={}, depot=(0, 0))
