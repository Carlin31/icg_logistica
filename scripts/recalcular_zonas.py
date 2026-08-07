"""
recalcular_zonas.py — recalcula núcleo y confianza de las zonas de mayoreo.

**Hay que correrlo después de cada `cargar_plantilla.py`**: el Excel canónico no
trae la presencia del grupo núcleo ni la frecuencia, así que una versión recién
cargada deja las zonas sin `grupo_nucleo`, sin `pct_nucleo` y sin `confianza`.
Sin esos campos el enganche de mayoristas cae entero a la geografía y el
resolver pierde la capa de excepciones histórica.

Los dos ejes de la confianza (ver `enganche_zona.confianza_zona`):
  FRECUENCIA   — en cuántas semanas se vio la zona.
  CONSISTENCIA — en qué proporción de sus paradas viajó su grupo núcleo.

Uso:
    python scripts/recalcular_zonas.py
"""
import sys, os
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from collections import Counter
from sqlalchemy import select
from app import create_app


def main():
    app = create_app()
    with app.app_context():
        from db import get_db, get_table
        from logic.plantilla_canonica import (recalcular_confianza_zonas,
                                              obtener_grupos, version_vigente)
        db = get_db()

        # zona de cada cliente, vía el diccionario población→zona de la plantilla.
        # Se normaliza con `_norm` (la misma del resolver), no con .upper():
        # SAN FELIPE DE LA PEÑA no empata sin quitar diacríticos, y el cliente
        # se caería en silencio dejando su zona sin grupo núcleo.
        from logic.enganche_zona import _norm
        tp = get_table("plantilla_poblacion_zona")
        pobl = {_norm(r.poblacion): str(r.zona) for r in
                db.execute(select(tp.c.poblacion, tp.c.zona).where(tp.c.vigente == 1))}
        zona_de_cliente = {}
        sin_zona = []
        for c in db.execute(select(get_table("clientes_mayoristas"))).mappings():
            if c.get("id_cliente") is None:
                continue
            z = pobl.get(_norm(c.get("poblacion")))
            if z:
                zona_de_cliente[str(int(c["id_cliente"]))] = z
            else:
                sin_zona.append(str(c.get("poblacion")))

        grupo_de_sucursal = {}
        for g in obtener_grupos():
            for s in g.get("sucursales", []):
                grupo_de_sucursal[s] = int(g["grupo"])

        if not zona_de_cliente:
            print("ABORTADO: ningún cliente resolvió zona. ¿Se cargó "
                  "mapeo_poblacion_a_zona.csv en esta versión de la plantilla?")
            return 1
        if not grupo_de_sucursal:
            print("ABORTADO: la plantilla vigente no tiene sucursales por grupo.")
            return 1

        ev = recalcular_confianza_zonas(zona_de_cliente, grupo_de_sucursal)
        t = get_table("plantilla_zona_mayorista")
        filas = [dict(r) for r in
                 db.execute(select(t).where(t.c.vigente == 1)).mappings()]
        conf = Counter(str(f.get("confianza")) for f in filas)
        con_nucleo = len([f for f in filas if f.get("grupo_nucleo") is not None])
        print(f"plantilla v{version_vigente()}: {len(filas)} zonas vigentes")
        print(f"  clientes con zona resuelta : {len(zona_de_cliente)}")
        if sin_zona:
            from collections import Counter as _C
            print(f"  clientes SIN zona ({len(sin_zona)}): poblaciones fuera del "
                  f"diccionario -> {sorted(_C(sin_zona))}")
        print(f"  zonas con evidencia        : {len(ev)}")
        print(f"  zonas con grupo núcleo     : {con_nucleo}")
        print(f"  confianza                  : {dict(conf)}")
        sin = sorted(f["zona"] for f in filas if f.get("grupo_nucleo") is None)
        if sin:
            print(f"  sin núcleo ({len(sin)}): {sin}")
            print("  (son zonas sin paradas en el histórico usable; caen al "
                  "fallback, no se adivinan)")
        return 0


if __name__ == "__main__":
    sys.exit(main())
