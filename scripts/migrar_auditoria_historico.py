"""
migrar_auditoria_historico.py — auditoría de escritura en `rutas_historicas`.

ADITIVO Y NO DESTRUCTIVO: agrega tres columnas con default NULL. No toca ni una
fila existente, no borra, no actualiza.

Por qué hace falta: `rutas_historicas.cargado_en` **no es una marca de
escritura**. Se fabrica en `guardar_en_historico` como

    cargado_en = f"{fecha_inicio}T00:00:00"

es decir, la fecha de INICIO de la semana. Por eso, cuando el histórico de
18-22 mayo se reescribió el 2026-08-03, el campo seguía diciendo 2026-05-18 y la
pregunta "¿cuándo se tocó esto?" quedó sin respuesta posible desde los datos.

Con estas columnas, cualquier escritura futura queda fechada, atribuida y con su
origen:
    escrito_en    fecha real (ISO) del momento de la escritura
    escrito_por   usuario de la sesión, o el nombre del script
    origen        'ui_modificacion' | 'script:<nombre>' | 'carga_csv' | …

Las filas ya existentes quedan con NULL: significa "escrita antes de que
existiera la auditoría", que es la verdad y no se debe inventar.

Uso:
    python scripts/migrar_auditoria_historico.py            # aplica
    python scripts/migrar_auditoria_historico.py --dry-run  # sólo muestra
"""
import sys, os
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy import text
from app import create_app

COLUMNAS = [
    ("escrito_en",  "NVARCHAR(40) NULL"),
    ("escrito_por", "NVARCHAR(120) NULL"),
    ("origen",      "NVARCHAR(60) NULL"),
]
TABLA = "rutas_historicas"


def main():
    seco = "--dry-run" in sys.argv
    app = create_app()
    with app.app_context():
        from db import get_engine
        eng = get_engine()
        with eng.begin() as conn:
            if conn.execute(text("SELECT OBJECT_ID(:t, 'U')"),
                            {"t": f"dbo.{TABLA}"}).scalar() is None:
                print(f"La tabla {TABLA} no existe.")
                return 1
            n = conn.execute(text(f"SELECT COUNT(*) FROM {TABLA}")).scalar()
            print(f"{TABLA}: {n} filas (ninguna se modifica)")
            for col, tipo in COLUMNAS:
                existe = conn.execute(text("SELECT COL_LENGTH(:t, :c)"),
                                      {"t": f"dbo.{TABLA}", "c": col}).scalar()
                if existe is not None:
                    print(f"  {col:<14} ya existe")
                    continue
                if seco:
                    print(f"  {col:<14} SE AGREGARÍA  ({tipo})")
                    continue
                conn.execute(text(f"ALTER TABLE {TABLA} ADD {col} {tipo}"))
                print(f"  {col:<14} agregada       ({tipo})")
        if seco:
            print("\n--dry-run: no se aplicó nada.")
        else:
            print("\nListo. Las filas previas quedan con NULL = 'escrita antes "
                  "de que existiera la auditoría'.")
            print("Recuerda: `cargado_en` NO es marca de escritura, es la fecha "
                  "de inicio de la semana.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
