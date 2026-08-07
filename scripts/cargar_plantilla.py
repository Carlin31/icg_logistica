"""
cargar_plantilla.py

Carga (o RECARGA) la plantilla canónica en SQL Server desde el Excel canónico,
el bridge revisado (datos/mapeo_no_a_numtienda.csv) y, si se pasa, el
diccionario población→zona. Cada corrida crea una VERSIÓN NUEVA (no borra).

Uso:
    python scripts/cargar_plantilla.py "<ruta xlsx>" [--poblacion <csv>] [--nota "texto"]

Ejemplo:
    python scripts/cargar_plantilla.py "C:/Users/carli/Downloads/rutas_canonicas_lores_1.xlsx"
"""
import sys, os, argparse
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import create_app
from logic.plantilla_canonica import cargar_plantilla_desde_excel, PlantillaError


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("xlsx")
    ap.add_argument("--poblacion", default=None)
    ap.add_argument("--bridge", default=None)
    ap.add_argument("--nota", default=None)
    a = ap.parse_args()

    app = create_app()
    with app.app_context():
        try:
            rep = cargar_plantilla_desde_excel(
                a.xlsx, bridge_csv=a.bridge, poblacion_csv=a.poblacion, nota=a.nota)
        except PlantillaError as e:
            print("ABORTADO:", e)
            sys.exit(1)

    print(f"OK  version={rep['version']}")
    print(f"  grupos={rep['grupos']}  (RIGIDO={rep['rigidos']}, FLEX={rep['flexibles']})")
    print(f"  miembros={rep['miembros']}  zonas={rep['zonas']}  poblaciones={rep['poblaciones']}")
    print(f"  flags coord={rep['flags']}")
    if rep["warnings"]:
        print("  advertencias:")
        for w in rep["warnings"]:
            print("   -", w)


if __name__ == "__main__":
    main()
