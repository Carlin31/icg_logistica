"""
reorganizar_zonas_2026.py

Reorganiza los `grupos` LORES canónicos (42, versión 17) en las 24 zonas de
negocio dadas por los jefes de prácticas en agosto 2026. Reemplaza
plantilla_grupo/plantilla_grupo_sucursal/plantilla_grupo_dia con una versión
nueva; NO toca plantilla_zona_mayorista/plantilla_poblacion_zona/
plantilla_bridge_sucursal.

Ver docs/superpowers/specs/2026-08-26-reorganizacion-zonas-canonicas-design.md
para la resolución de nombres, la evidencia histórica y la excepción de la
Zona 22 (8 sucursales en un solo grupo, pese al límite general de 6).

PENDIENTE (no lo resuelve este script): los CSV
datos/dias_admisibles_por_grupo.csv, datos/unidad_ref_por_grupo.csv y
datos/grupos_unidad_forzada.csv siguen referenciando la numeración VIEJA de
42 grupos. Son el input por defecto de `cargar_plantilla_desde_excel`
(scripts/cargar_plantilla.py). Si alguien vuelve a correr ese script con un
Excel canónico nuevo sin migrar antes esos 3 CSV a la numeración de zonas,
pisaría esta reorganización.

Uso:
    python scripts/reorganizar_zonas_2026.py --dry-run   # solo muestra
    python scripts/reorganizar_zonas_2026.py              # escribe la version nueva
"""
import sys, os, argparse
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import create_app
from logic.plantilla_canonica import obtener_grupos, cargar_zonas_manual, derivar_grupo_zona

# 22 zonas que quedan como una sola ruta (num_tienda). La Zona 22 tiene 8
# sucursales -- excepcion de negocio confirmada, no se parte (ver spec).
ZONAS_SIMPLES = {
    1:  [4, 27, 75, 86, 49, 100],
    2:  [92, 13, 3, 73, 85],
    3:  [5, 37, 12],
    4:  [64, 65, 70, 18],
    6:  [11, 93, 42],
    7:  [35, 97],
    8:  [33, 19],
    9:  [6, 10, 69, 79, 89],
    10: [14, 47],
    12: [23, 20, 58],
    13: [59, 9],
    14: [17, 26, 34],
    15: [56, 95, 51, 50],
    16: [87, 62, 66, 8],
    17: [67, 72, 99, 71],
    18: [94, 80, 83, 60, 41],
    19: [81, 52, 53, 40],
    20: [82, 61, 48, 43, 44],
    21: [21, 68, 16, 88, 22],
    22: [39, 45, 90, 91, 96, 98, 32, 78],
    23: [29, 28, 30],
    24: [84],
}

# Zona 5 (Tuxtepec) y Zona 11 (Tierra Blanca) superan el limite de 6
# sucursales/dia: se parten en sub-rutas fijas, tomadas del historico real
# (ver spec) en vez de la regla generica de derivar_grupo_zona.
SUB_RUTAS_ESPECIALES = [
    dict(grupo=5, zona=5, rigidez="FLEXIBLE", dia="MARTES",
         dias_admisibles=["MARTES", "MIERCOLES"], unidad_ref="F 350_2",
         unidad_forzada=True, sucursales=[2, 7, 15, 31, 54, 55]),
    dict(grupo=25, zona=5, rigidez="RIGIDO", dia="JUEVES",
         dias_admisibles=["JUEVES"], unidad_ref="F 350_2",
         unidad_forzada=False, sucursales=[38, 46, 57]),
    dict(grupo=26, zona=5, rigidez="FLEXIBLE", dia="MARTES",
         dias_admisibles=["MARTES", "JUEVES", "MIERCOLES"], unidad_ref="K 16",
         unidad_forzada=False, sucursales=[74]),
    dict(grupo=11, zona=11, rigidez="FLEXIBLE", dia="LUNES",
         dias_admisibles=["LUNES"], unidad_ref=None,
         unidad_forzada=False, sucursales=[1, 24, 25, 36]),
    dict(grupo=27, zona=11, rigidez="FLEXIBLE", dia="LUNES",
         dias_admisibles=["LUNES"], unidad_ref=None,
         unidad_forzada=False, sucursales=[63, 76, 77, 101]),
]


def construir_sub_rutas():
    """Arma las 27 sub-rutas: 22 derivadas del grupo viejo con mas peso +
    5 especiales (Zona 5 y 11). Devuelve (sub_rutas, alertas_revisar)."""
    grupos_viejos = obtener_grupos()
    grupos_por_id = {g["grupo"]: g for g in grupos_viejos}
    grupo_de_sucursal = {s: g["grupo"] for g in grupos_viejos for s in g["sucursales"]}

    sub_rutas = list(SUB_RUTAS_ESPECIALES)
    revisar = []
    for zona, sucursales in ZONAS_SIMPLES.items():
        d = derivar_grupo_zona(sucursales, grupo_de_sucursal, grupos_por_id)
        sub_rutas.append(dict(
            grupo=zona, zona=zona, rigidez=d["rigidez"], dia=d["dia"],
            dia_preferido=d["dia_preferido"], dias_admisibles=d["dias_admisibles"],
            unidad_ref=d["unidad_ref"], unidades_afines=d["unidades_afines"],
            unidad_forzada=d["unidad_forzada"], sucursales=sucursales))
        if d["revisar"]:
            revisar.append((zona, d["grupo_origen"], d["pct"]))
    return sub_rutas, revisar


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--nota", default="Reorganizacion de zonas canonicas 2026-08 "
                                      "(24 zonas de negocio, ver spec)")
    a = ap.parse_args()

    app = create_app()
    with app.app_context():
        sub_rutas, revisar = construir_sub_rutas()

        todas = [s for r in sub_rutas for s in r["sucursales"]]
        dups = sorted({s for s in todas if todas.count(s) > 1})

        print(f"{len(sub_rutas)} grupos, {len({r['zona'] for r in sub_rutas})} zonas, "
              f"{len(todas)} sucursales ({len(set(todas))} unicas)")
        if dups:
            print("ABORTADO: sucursales duplicadas entre grupos:", dups)
            return 1
        for zona, grupo_origen, pct in revisar:
            print(f"  REVISAR zona {zona}: hereda de grupo {grupo_origen} solo al {pct:.0%}")

        for r in sorted(sub_rutas, key=lambda r: (r["zona"], r["grupo"])):
            print(f"  grupo {r['grupo']:>3} zona {r['zona']:>2}  {r['rigidez']:<8} "
                  f"{str(r['dia']):<10} {str(r['unidad_ref']):<10} "
                  f"forzada={r['unidad_forzada']}  sucursales={r['sucursales']}")

        if a.dry_run:
            print("\n--dry-run: no se escribio nada en la BD.")
            return 0

        rep = cargar_zonas_manual(sub_rutas, nota=a.nota)
        print(f"\nOK version={rep['version']}  grupos={rep['grupos']}  "
              f"zonas={rep['zonas']}  sucursales={rep['sucursales']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
