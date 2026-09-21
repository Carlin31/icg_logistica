"""
scripts/admin_overrides.py

CLI unico para aplicar overrides puntuales sobre la plantilla canonica y
los pines de mayoristas -- reemplaza el patron "un archivo nuevo por cada
parche" (mover_dia_preferido_grupo_*.py, afinidad_grupo27_tb1_tb4.py, etc.)
por subcomandos sobre logic/overrides_admin.py. Cada escritura queda en
overrides_auditoria con motivo obligatorio (ver
scripts/crear_overrides_auditoria.py).

Los scripts viejos NO se tocan (siguen siendo historial valido); este CLI
es para el PROXIMO override.

Uso:
    python scripts/admin_overrides.py dia-preferido --grupo 20 --dia VIERNES --motivo "..."
    python scripts/admin_overrides.py afinidad --grupo 27 --unidades "T 25:5 | T 23:4" --motivo "..."
    python scripts/admin_overrides.py unidades-excluidas --grupo 27 --unidades "F 350_1,F 350_2,F 350_3" --motivo "..."
    python scripts/admin_overrides.py ancla-mayorista --cliente 501 --sucursal 30 --motivo "..."
    python scripts/admin_overrides.py grupo-mayorista --cliente 555 --grupo 5 --motivo "..."
    python scripts/admin_overrides.py orden-fijo --regla zona_5 --parada 2:1 --parada 31:2 --motivo "..."

Todos aceptan --dry-run (solo muestra ANTES/DESPUES, no escribe) y
--aplicado-por (por defecto, el usuario de Windows/entorno actual).
"""
import sys
import os
import argparse
import getpass

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import create_app


def _quien(args):
    return args.aplicado_por or os.environ.get("USERNAME") or os.environ.get("USER") or getpass.getuser()


def _mostrar(resultado, dry_run):
    print("ANTES:   ", resultado.get("antes"))
    print("DESPUES: ", resultado.get("despues"))
    if resultado.get("sin_cambios"):
        print("(sin cambios -- ya estaba asi)")
    elif dry_run:
        print("\n--dry-run: no se escribio nada.")
    else:
        print("\nOK -- aplicado y registrado en overrides_auditoria.")


def cmd_dia_preferido(args):
    from logic.overrides_admin import fijar_dia_preferido
    r = fijar_dia_preferido(args.grupo, args.dia, motivo=args.motivo,
                             aplicado_por=_quien(args), dry_run=args.dry_run)
    _mostrar(r, args.dry_run)


def cmd_afinidad(args):
    from logic.overrides_admin import fijar_afinidad
    r = fijar_afinidad(args.grupo, args.unidades, motivo=args.motivo,
                        aplicado_por=_quien(args), dry_run=args.dry_run)
    _mostrar(r, args.dry_run)


def cmd_unidades_excluidas(args):
    from logic.overrides_admin import fijar_unidades_excluidas
    unidades = [u.strip() for u in args.unidades.split(",") if u.strip()]
    r = fijar_unidades_excluidas(args.grupo, unidades, motivo=args.motivo,
                                  aplicado_por=_quien(args), dry_run=args.dry_run)
    _mostrar(r, args.dry_run)


def cmd_ancla_mayorista(args):
    from logic.overrides_admin import crear_ancla_mayorista
    r = crear_ancla_mayorista(args.cliente, args.sucursal, motivo=args.motivo,
                               nota=args.nota, aplicado_por=_quien(args), dry_run=args.dry_run)
    _mostrar(r, args.dry_run)


def cmd_grupo_mayorista(args):
    from logic.overrides_admin import fijar_grupo_mayorista
    r = fijar_grupo_mayorista(args.cliente, args.grupo, motivo=args.motivo,
                               nota=args.nota, aplicado_por=_quien(args), dry_run=args.dry_run)
    _mostrar(r, args.dry_run)


def cmd_orden_fijo(args):
    from logic.overrides_admin import cargar_orden_fijo_regla
    entradas = []
    for p in args.parada:
        nt, _, pos = p.partition(":")
        if not pos:
            raise SystemExit(f"--parada invalida (usa num_tienda:posicion): {p!r}")
        entradas.append((int(nt), int(pos)))
    r = cargar_orden_fijo_regla(args.regla, entradas, motivo=args.motivo,
                                 aplicado_por=_quien(args), dry_run=args.dry_run)
    _mostrar(r, args.dry_run)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="comando", required=True)

    def _comunes(p):
        p.add_argument("--motivo", required=True, help="por que se aplica este override")
        p.add_argument("--aplicado-por", dest="aplicado_por", default=None)
        p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("dia-preferido", help="cambia el dia preferido de un grupo (entre sus admisibles)")
    p.add_argument("--grupo", type=int, required=True)
    p.add_argument("--dia", required=True)
    _comunes(p); p.set_defaults(func=cmd_dia_preferido)

    p = sub.add_parser("afinidad", help="fija unidades_afines de un grupo")
    p.add_argument("--grupo", type=int, required=True)
    p.add_argument("--unidades", required=True, help='ej. "T 25:5 | T 23:4 | T 20:3"')
    _comunes(p); p.set_defaults(func=cmd_afinidad)

    p = sub.add_parser("unidades-excluidas", help="fija unidades_excluidas de un grupo")
    p.add_argument("--grupo", type=int, required=True)
    p.add_argument("--unidades", required=True, help='lista separada por comas, ej. "F 350_1,F 350_2"')
    _comunes(p); p.set_defaults(func=cmd_unidades_excluidas)

    p = sub.add_parser("ancla-mayorista", help="fija el orden de visita de un mayorista tras una sucursal")
    p.add_argument("--cliente", type=int, required=True, help="id_cliente")
    p.add_argument("--sucursal", type=int, required=True, help="num_tienda")
    p.add_argument("--nota", default=None)
    _comunes(p); p.set_defaults(func=cmd_ancla_mayorista)

    p = sub.add_parser("grupo-mayorista", help="fija a que grupo/ruta viaja un mayorista")
    p.add_argument("--cliente", type=int, required=True, help="id_cliente")
    p.add_argument("--grupo", type=int, required=True)
    p.add_argument("--nota", default=None)
    _comunes(p); p.set_defaults(func=cmd_grupo_mayorista)

    p = sub.add_parser("orden-fijo", help="reemplazo completo de una regla de orden fijo de paradas")
    p.add_argument("--regla", required=True, help="nombre_regla")
    p.add_argument("--parada", action="append", required=True,
                    help="num_tienda:posicion, repetible (ej. --parada 2:1 --parada 31:2)")
    _comunes(p); p.set_defaults(func=cmd_orden_fijo)

    args = ap.parse_args()

    app = create_app()
    with app.app_context():
        try:
            args.func(args)
        except ValueError as e:
            raise SystemExit(f"Error: {e}")


if __name__ == "__main__":
    main()
