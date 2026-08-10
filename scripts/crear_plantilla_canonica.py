"""
crear_plantilla_canonica.py

Script de un solo uso (idempotente): crea en SQL Server las tablas de la
PLANTILLA CANÓNICA (ConVRP). No borra ni modifica datos; si una tabla ya
existe, la deja intacta.

Modelo de versionado (nunca destructivo):
  - Cada tabla de DATOS lleva (version, vigente_desde, vigente). Recargar la
    plantilla INSERTA una versión nueva y marca la anterior vigente=0; jamás
    borra el historial.
  - `plantilla_meta` lleva UNA FILA POR VERSIÓN (no una sola fila global).
  - Los lectores filtran vigente=1 por defecto.

Tablas:
  plantilla_meta              1 fila por versión (auditoría + qué versión rige)
  plantilla_bridge_sucursal   puente No. SUCURSAL (Excel) ↔ num_tienda (BD), con
                              ambas coordenadas, distancia y estado_revision
  plantilla_grupo             grupos canónicos (hoja LORES): rigidez, día, unidad_ref
  plantilla_grupo_sucursal    miembros de cada grupo (num_tienda)
  plantilla_zona_mayorista    zonas de mayoristas → grupo(s) Lores + regla de enganche
  plantilla_poblacion_zona    diccionario población/municipio → zona (con confianza)

Uso:
    python scripts/crear_plantilla_canonica.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy import text
from app import create_app
from db import get_engine

# Columnas agregadas después de la creación inicial: se aplican con ALTER
# idempotente para no obligar a recrear tablas que ya tienen datos.
ALTERS = [
    ("plantilla_grupo", "unidades_afines", "NVARCHAR(400) NULL"),
]

DDL = [
    # ── meta: una fila por versión ────────────────────────────────────────
    ("plantilla_meta", """
        CREATE TABLE plantilla_meta (
            version            INT           NOT NULL PRIMARY KEY,
            cargado_en         NVARCHAR(40)  NOT NULL,
            excel_archivo      NVARCHAR(260) NULL,
            excel_hash         NVARCHAR(64)  NULL,
            semanas_analisis   INT           NULL,
            n_grupos           INT           NULL,
            n_zonas            INT           NULL,
            n_poblaciones      INT           NULL,
            n_flags            INT           NULL,
            vigente            BIT           NOT NULL DEFAULT 0,
            nota               NVARCHAR(400) NULL
        )
    """),
    # ── bridge No.SUCURSAL ↔ num_tienda (Fase 0, revisado) ────────────────
    ("plantilla_bridge_sucursal", """
        CREATE TABLE plantilla_bridge_sucursal (
            version         INT           NOT NULL,
            no_sucursal     INT           NOT NULL,
            num_tienda      INT           NOT NULL,
            nombre_excel    NVARCHAR(160) NULL,
            nombre_bd       NVARCHAR(160) NULL,
            lat_excel       FLOAT         NULL,
            lon_excel       FLOAT         NULL,
            lat_bd          FLOAT         NULL,
            lon_bd          FLOAT         NULL,
            dist_m          INT           NULL,
            flag            NVARCHAR(40)  NULL,
            estado_revision NVARCHAR(40)  NOT NULL DEFAULT 'ok',
            vigente_desde   NVARCHAR(40)  NOT NULL,
            vigente         BIT           NOT NULL DEFAULT 1,
            CONSTRAINT PK_plantilla_bridge PRIMARY KEY (version, no_sucursal)
        )
    """),
    # ── grupos canónicos (LORES) ──────────────────────────────────────────
    ("plantilla_grupo", """
        CREATE TABLE plantilla_grupo (
            version       INT           NOT NULL,
            grupo         INT           NOT NULL,
            rigidez       NVARCHAR(20)  NOT NULL,
            dia           NVARCHAR(20)  NULL,
            tam           INT           NULL,
            cohesion      FLOAT         NULL,
            unidad_ref    NVARCHAR(60)  NULL,
            que_hace_vrp  NVARCHAR(400) NULL,
            unidades_afines NVARCHAR(400) NULL,
            vigente_desde NVARCHAR(40)  NOT NULL,
            vigente       BIT           NOT NULL DEFAULT 1,
            CONSTRAINT PK_plantilla_grupo PRIMARY KEY (version, grupo)
        )
    """),
    # ── miembros de grupo ─────────────────────────────────────────────────
    ("plantilla_grupo_sucursal", """
        CREATE TABLE plantilla_grupo_sucursal (
            version       INT          NOT NULL,
            grupo         INT          NOT NULL,
            num_tienda    INT          NOT NULL,
            vigente_desde NVARCHAR(40) NOT NULL,
            vigente       BIT          NOT NULL DEFAULT 1,
            CONSTRAINT PK_plantilla_grupo_suc PRIMARY KEY (version, grupo, num_tienda)
        )
    """),
    # ── zonas de mayoristas → enganche ────────────────────────────────────
    ("plantilla_zona_mayorista", """
        CREATE TABLE plantilla_zona_mayorista (
            version        INT           NOT NULL,
            zona           NVARCHAR(120) NOT NULL,
            tipo           NVARCHAR(40)  NULL,
            grupos_lores   NVARCHAR(80)  NULL,
            dia_habitual   NVARCHAR(20)  NULL,
            regla_enganche NVARCHAR(200) NULL,
            unidad_ref     NVARCHAR(60)  NULL,
            kg_prom        FLOAT         NULL,
            vigente_desde  NVARCHAR(40)  NOT NULL,
            vigente        BIT           NOT NULL DEFAULT 1,
            CONSTRAINT PK_plantilla_zona PRIMARY KEY (version, zona)
        )
    """),
    # ── días admisibles por grupo (el grupo se mueve sólo dentro de este set)─
    ("plantilla_grupo_dia", """
        CREATE TABLE plantilla_grupo_dia (
            version       INT          NOT NULL,
            grupo         INT          NOT NULL,
            dia           NVARCHAR(20) NOT NULL,
            es_canonico   BIT          NOT NULL DEFAULT 0,
            orden         INT          NULL,
            vigente_desde NVARCHAR(40) NOT NULL,
            vigente       BIT          NOT NULL DEFAULT 1,
            CONSTRAINT PK_plantilla_grupo_dia PRIMARY KEY (version, grupo, dia)
        )
    """),
    # ── excepciones del ConVRP por logística (las ve el planeador) ────────
    ("convrp_excepciones", """
        CREATE TABLE convrp_excepciones (
            logistica_id   NVARCHAR(40)  NOT NULL,
            generado_en    NVARCHAR(40)  NOT NULL,
            orden          INT           NOT NULL,
            tipo           NVARCHAR(40)  NOT NULL,
            grupo          INT           NULL,
            rigidez        NVARCHAR(20)  NULL,
            restriccion    NVARCHAR(20)  NULL,
            desde_unidad   NVARCHAR(60)  NULL,
            a_unidad       NVARCHAR(60)  NULL,
            desde_dia      NVARCHAR(20)  NULL,
            a_dia          NVARCHAR(20)  NULL,
            paradas        INT           NULL,
            detalle        NVARCHAR(MAX) NULL,
            motivo         NVARCHAR(600) NULL,
            CONSTRAINT PK_convrp_excepciones PRIMARY KEY (logistica_id, orden)
        )
    """),
    # ── diccionario población/municipio → zona ────────────────────────────
    ("plantilla_poblacion_zona", """
        CREATE TABLE plantilla_poblacion_zona (
            version       INT           NOT NULL,
            poblacion     NVARCHAR(160) NOT NULL,
            zona          NVARCHAR(120) NULL,
            confianza     NVARCHAR(20)  NULL,
            vigente_desde NVARCHAR(40)  NOT NULL,
            vigente       BIT           NOT NULL DEFAULT 1,
            CONSTRAINT PK_plantilla_pobl_zona PRIMARY KEY (version, poblacion)
        )
    """),
    # ── mayoristas del ConVRP completo, por logística (las lee Asignación/PDF/Modificación) ──
    ("convrp_mayoristas", """
        CREATE TABLE convrp_mayoristas (
            logistica_id  NVARCHAR(40)  NOT NULL,
            generado_en   NVARCHAR(40)  NOT NULL,
            unidad        NVARCHAR(60)  NOT NULL,
            dia           NVARCHAR(20)  NOT NULL,
            orden         INT           NOT NULL,
            id_cliente    INT           NOT NULL,
            nombre        NVARCHAR(200) NULL,
            peso_kg       FLOAT         NOT NULL,
            via_zona      NVARCHAR(20)  NULL,
            via_destino   NVARCHAR(20)  NULL,
            CONSTRAINT PK_convrp_mayoristas PRIMARY KEY (logistica_id, unidad, dia, id_cliente)
        )
    """),
]


def main():
    app = create_app()
    with app.app_context():
        eng = get_engine()
        creadas, existentes = [], []
        with eng.begin() as conn:
            for nombre, ddl in DDL:
                existe = conn.execute(
                    text("SELECT OBJECT_ID(:t, 'U')"), {"t": f"dbo.{nombre}"}
                ).scalar()
                if existe is None:
                    conn.execute(text(ddl))
                    creadas.append(nombre)
                else:
                    existentes.append(nombre)
        # columnas agregadas después: ALTER idempotente sobre tablas con datos
        agregadas = []
        with eng.begin() as conn:
            for tabla, col, tipo in ALTERS:
                if conn.execute(text("SELECT OBJECT_ID(:t, 'U')"),
                                {"t": f"dbo.{tabla}"}).scalar() is None:
                    continue
                hay = conn.execute(text("SELECT COL_LENGTH(:t, :c)"),
                                   {"t": f"dbo.{tabla}", "c": col}).scalar()
                if hay is None:
                    conn.execute(text(f"ALTER TABLE {tabla} ADD {col} {tipo}"))
                    agregadas.append(f"{tabla}.{col}")
        print("Tablas creadas:  ", creadas or "(ninguna)")
        print("Ya existían:     ", existentes or "(ninguna)")
        print("Columnas nuevas: ", agregadas or "(ninguna)")
        print("\nNota: reinicia el proceso Flask para que la reflexión de "
              "MetaData recoja las tablas nuevas (db.py refleja una sola vez "
              "por proceso).")


if __name__ == "__main__":
    main()
