"""Pruebas del criterio de expulsión por lejanía en _resolver_sobrecarga_mayoristas."""
import logic.mayoristas_logic as ml
from logic.mayoristas_logic import _resolver_sobrecarga_mayoristas


def _may(id_cliente, peso, lat, lon):
    return {"id_cliente": id_cliente, "peso_kg": peso, "latitud": lat, "longitud": lon}


# Ruta A (centroide 0,0) sobrecargada: un mayorista cercano-pesado (id 1) y uno
# lejano-liviano (id 2). Sobrecarga = 50 kg (100 suc + 250 may > 300 cap).
def _escenario():
    may = {"A": [_may(1, 200, 0.0, 0.01), _may(2, 50, 0.0, 1.0)], "B": []}
    peso_suc = {"A": 100, "B": 0}
    caps = {"A": 300, "B": 1000}
    centros = {"A": (0.0, 0.0), "B": (0.0, 1.0)}
    return may, peso_suc, caps, centros


def test_expulsa_al_mas_lejano_conserva_al_cercano(monkeypatch):
    monkeypatch.setattr(ml, "MAYORISTAS_GEOGRAFICO", True)
    may, peso_suc, caps, centros = _escenario()
    out = _resolver_sobrecarga_mayoristas(may, peso_suc, caps, {}, centros)
    assert sorted(m["id_cliente"] for m in out["A"]) == [1]   # cercano-pesado se queda
    assert sorted(m["id_cliente"] for m in out["B"]) == [2]   # lejano-liviano se fue


def test_interruptor_apagado_expulsa_al_mas_pesado(monkeypatch):
    monkeypatch.setattr(ml, "MAYORISTAS_GEOGRAFICO", False)
    may, peso_suc, caps, centros = _escenario()
    out = _resolver_sobrecarga_mayoristas(may, peso_suc, caps, {}, centros)
    # comportamiento previo: expulsa al más pesado (id 1), deja al liviano (id 2)
    assert sorted(m["id_cliente"] for m in out["A"]) == [2]


def test_sin_coordenadas_no_se_expulsa_por_lejania(monkeypatch):
    monkeypatch.setattr(ml, "MAYORISTAS_GEOGRAFICO", True)
    # id 1 sin coords (pesado); id 2 lejano-liviano con coords. Debe irse el 2.
    may = {"A": [{"id_cliente": 1, "peso_kg": 200, "latitud": None, "longitud": None},
                 _may(2, 50, 0.0, 1.0)], "B": []}
    peso_suc = {"A": 100, "B": 0}
    caps = {"A": 300, "B": 1000}
    centros = {"A": (0.0, 0.0), "B": (0.0, 1.0)}
    out = _resolver_sobrecarga_mayoristas(may, peso_suc, caps, {}, centros)
    ids_A = [m["id_cliente"] for m in out["A"]]
    assert 1 in ids_A       # sin-coords se conserva
    assert 2 not in ids_A   # el lejano con coords se fue


def test_destino_nunca_excede_capacidad(monkeypatch):
    monkeypatch.setattr(ml, "MAYORISTAS_GEOGRAFICO", True)
    may, peso_suc, caps, centros = _escenario()
    out = _resolver_sobrecarga_mayoristas(may, peso_suc, caps, {}, centros)
    for rid, mays in out.items():
        total = peso_suc.get(rid, 0) + sum(m["peso_kg"] for m in mays)
        assert total <= caps[rid]
