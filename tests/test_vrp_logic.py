from logic.vrp_logic import capacidad_efectiva_kg


def test_cap4_sigue_igual_3500_a_4000_kg():
    assert capacidad_efectiva_kg(3500) == 3900
    assert capacidad_efectiva_kg(3900) == 3900
    assert capacidad_efectiva_kg(4000) == 3900


def test_cap1500_da_tolerancia_hasta_1549_kg():
    assert capacidad_efectiva_kg(1500) == 1549


def test_cap1500_no_afecta_capacidades_fuera_de_1500_kg():
    # 1300 kg (T 25 antes de corregir el dato) sigue siendo 100% nominal
    assert capacidad_efectiva_kg(1300) == 1300
    # Camiones medianos y KANGOO tampoco cambian
    assert capacidad_efectiva_kg(2500) == 2500
    assert capacidad_efectiva_kg(600) == 600
    # F350 (CAP-4) no se confunde con CAP-1.5
    assert capacidad_efectiva_kg(3900) == 3900
