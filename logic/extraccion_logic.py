"""
logic/extraccion_logic.py
Orquestador de la Sección 1 — Extracción de datos.

Responsabilidades:
  1. Leer archivos Excel por proveedor (ICG / Bimbo / Proalmex) → Tiendas Lores.
  2. Cruzar con catálogos de productos y sucursales desde MongoDB.
  3. Calcular peso (kg) y volumen (m³) por sucursal y proveedor.
  4. Devolver datos consolidados + desglose por perfil para ambas métricas.
  5. Leer archivo Excel de Clientes Mayoristas y consolidar peso por cliente.

Delegación de cálculos:
  logic/logic_extraccion/calculadora.py    → calcular_peso / calcular_volumen
  logic/logic_extraccion/lector_mayoristas → LectorMayoristas
"""
import pandas as pd
from logic.logic_extraccion.lector_icg        import LectorICG
from logic.logic_extraccion.lector_bimbo      import LectorBimbo
from logic.logic_extraccion.lector_proalmex   import LectorProalmex
from logic.logic_extraccion.lector_mayoristas import LectorMayoristas
from logic.logic_extraccion.calculadora       import calcular_peso, calcular_volumen
from logic.configuracion_logic                import listar_productos, listar_productos_proalmex, listar_productos_bimbo, listar_sucursales


def procesar_archivos_extraccion(archivos: dict) -> dict:
    """
    Procesa los archivos Excel recibidos y devuelve peso y volumen consolidados.

    Returns:
        {
          'status':           'ok' | 'error',
          'data':             { suc: {id_sucursal, icg_kg, …, total_kg} },   # peso consolidado
          'desglose':         { perfil: { suc: {id_sucursal, kg} } },
          'datos_volumen':    { suc: {id_sucursal, icg_m3, …, total_m3} },   # volumen consolidado
          'desglose_volumen': { perfil: { suc: {id_sucursal, m3} } },
          'advertencias': {
            'claves_no_encontradas_icg':      [clave_sae, …],   # no existen en catálogo de productos
            'total_claves_icg':               int,             # total de claves distintas procesadas (ICG)
            'claves_no_encontradas_proalmex': ['Descripción (Tamaño)', …],
            'total_claves_proalmex':          int,             # total de productos distintos procesados (Proalmex)
          },
        }
    """
    dfs_procesados = []
    claves_proalmex_total: set[str]    = set()
    claves_proalmex_no_encontradas: set[str] = set()
    codigos_bimbo_total: set[str]      = set()
    codigos_bimbo_no_encontrados: set[str]   = set()

    # ── 1. Catálogos de la BD ────────────────────────────────────────────────
    productos_db          = listar_productos()
    productos_proalmex_db = listar_productos_proalmex()
    productos_bimbo_db    = listar_productos_bimbo()
    sucursales_db         = listar_sucursales()

    if not productos_db:
        return {'status': 'error', 'mensaje': 'No hay productos cargados en el sistema.'}

    # Mapa de productos Bimbo: codigo_barra → {peso, volumen}
    map_productos_bimbo: dict[str, dict] = {
        str(p.get('codigo_barra', '')).strip(): {
            'peso':    float(p.get('peso',    0) or 0),
            'volumen': float(p.get('volumen', 0) or 0),
        }
        for p in productos_bimbo_db if p.get('codigo_barra')
    }

    # Mapa de peso Proalmex: (linea, tamano) → peso_kg
    # Normalización: strip + lower para tolerancia a diferencias de mayúsculas/espacios
    map_peso_proalmex: dict[tuple, float] = {
        (
            str(p.get('linea',  '')).strip().lower(),
            str(p.get('tamano', '')).strip().lower(),
        ): float(p.get('peso', 0) or 0)
        for p in productos_proalmex_db
    }

    # Mapa de volumen Proalmex: (linea, tamano) → volumen_m3 (misma clave que el de peso)
    map_volumen_proalmex: dict[tuple, float] = {
        (
            str(p.get('linea',  '')).strip().lower(),
            str(p.get('tamano', '')).strip().lower(),
        ): float(p.get('volumen', 0) or 0)
        for p in productos_proalmex_db
    }

    df_productos = pd.DataFrame(productos_db)

    # clave_sae es el identificador de producto que usan los archivos Excel
    df_productos['clave_sae'] = (
        pd.to_numeric(df_productos['clave_sae'] if 'clave_sae' in df_productos.columns else pd.Series(dtype=float),
                      errors='coerce').fillna(0).astype(int)
    )
    df_productos['peso'] = (
        pd.to_numeric(df_productos['peso'] if 'peso' in df_productos.columns else pd.Series(dtype=float),
                      errors='coerce').fillna(0)
    )
    # Recalcular volumen desde las dimensiones actuales (largo, ancho, alto en metros)
    for _dim in ('largo', 'ancho', 'alto'):
        df_productos[_dim] = pd.to_numeric(
            df_productos[_dim] if _dim in df_productos.columns else pd.Series(dtype=float),
            errors='coerce'
        ).fillna(0)
    df_productos['volumen'] = (
        df_productos['largo'] * df_productos['ancho'] * df_productos['alto']
    ).round(6)

    # ── 2. Mapas de sucursales ───────────────────────────────────────────────
    # nombre_icg-proalmex → nombre_base  (para archivos ICG y Proalmex)
    map_pedido = {
        str(s.get('nombre_icg-proalmex', '')).strip(): s.get('nombre_base', 'Desconocida')
        for s in sucursales_db if s.get('nombre_icg-proalmex')
    }
    # nombre_bimbo → nombre_base  (para archivos Bimbo, comparación case-insensitive)
    map_bimbo = {
        str(s.get('nombre_bimbo', '')).strip().lower(): s.get('nombre_base', 'Desconocida')
        for s in sucursales_db if s.get('nombre_bimbo')
    }
    map_id_sucursal = {}
    for s in sucursales_db:
        nombre_base = s.get('nombre_base', 'Desconocida')
        id_val = s.get('num_tienda', str(s['_id'])) if '_id' in s else 'N/A'
        map_id_sucursal[nombre_base] = id_val

    def clean_name(name):
        return str(name).strip() if pd.notnull(name) else ''

    # ── 3. Leer y etiquetar cada archivo ────────────────────────────────────
    if archivos.get('icg'):
        df_icg = LectorICG.leer_y_normalizar(archivos['icg'])
        if not df_icg.empty:
            df_icg['Proveedor'] = 'ICG'
            df_icg['Sucursal']  = df_icg['Sucursal'].apply(clean_name).map(map_pedido).fillna(df_icg['Sucursal'])
            dfs_procesados.append(df_icg)

    if archivos.get('bimbo'):
        # Determinar la columna ancla: primer nombre_bimbo registrado (orden por num_tienda)
        primer_nombre_bimbo = next(
            (str(s.get('nombre_bimbo', '')).strip()
             for s in sorted(sucursales_db, key=lambda s: s.get('num_tienda') or 0)
             if s.get('nombre_bimbo')),
            '',
        )
        df_bimbo = LectorBimbo.leer_y_normalizar(archivos['bimbo'], primer_nombre_bimbo)
        if not df_bimbo.empty:
            df_bimbo['Proveedor'] = 'Bimbo'
            # Match case-insensitive: las columnas del Excel vienen en minúsculas
            sucursal_orig = df_bimbo['Sucursal'].apply(clean_name)
            df_bimbo['Sucursal'] = sucursal_orig.str.lower().map(map_bimbo).fillna(sucursal_orig)

            # Pre-calcular peso y volumen usando codigo_barra → productos_bimbo
            def _peso_total_bimbo(row) -> float:
                codigo = str(row.get('codigo_barra', '')).strip()
                if not codigo or codigo.lower() == 'nan':
                    return 0.0
                codigos_bimbo_total.add(codigo)
                prod = map_productos_bimbo.get(codigo)
                if prod is None:
                    codigos_bimbo_no_encontrados.add(codigo)
                    return 0.0
                return prod['peso'] * float(row['Piezas'])

            def _volumen_total_bimbo(row) -> float:
                codigo = str(row.get('codigo_barra', '')).strip()
                if not codigo or codigo.lower() == 'nan':
                    return 0.0
                prod = map_productos_bimbo.get(codigo)
                if prod is None:
                    return 0.0
                return prod['volumen'] * float(row['Piezas'])

            df_bimbo['bimbo_peso_precalc']    = df_bimbo.apply(_peso_total_bimbo,    axis=1)
            df_bimbo['bimbo_volumen_precalc']  = df_bimbo.apply(_volumen_total_bimbo, axis=1)
            dfs_procesados.append(df_bimbo)

    if archivos.get('proalmex'):
        df_proalmex = LectorProalmex.leer_y_normalizar(archivos['proalmex'])
        if not df_proalmex.empty:
            df_proalmex['Proveedor'] = 'Proalmex'
            df_proalmex['Sucursal']  = df_proalmex['Sucursal'].apply(clean_name).map(map_pedido).fillna(df_proalmex['Sucursal'])

            # Obtener peso unitario desde productos_proalmex usando (linea, tamano)
            def _peso_unitario_proalmex(row) -> float:
                desc_raw   = row['descripcion_proalmex']
                tamano_raw = row['tamano_proalmex']

                # Vacío/NaN no es una clave real: no se cuenta ni se reporta como no encontrada
                if pd.isnull(desc_raw) or not str(desc_raw).strip() or str(desc_raw).strip().lower() == 'nan':
                    return 0.0

                desc   = str(desc_raw).strip()
                tamano = '' if (pd.isnull(tamano_raw) or str(tamano_raw).strip().lower() == 'nan') else str(tamano_raw).strip()
                etiqueta = f'{desc} ({tamano})' if tamano else desc
                claves_proalmex_total.add(etiqueta)

                clave = (desc.lower(), tamano.lower())
                if clave not in map_peso_proalmex:
                    claves_proalmex_no_encontradas.add(etiqueta)
                return map_peso_proalmex.get(clave, 0.0)

            df_proalmex['peso_precalc'] = (
                df_proalmex.apply(_peso_unitario_proalmex, axis=1) * df_proalmex['Piezas']
            )

            # Volumen unitario desde productos_proalmex usando (linea, tamano),
            # misma clave que el peso. Sin reportar claves (ya lo hace el peso).
            def _volumen_unitario_proalmex(row) -> float:
                desc_raw   = row['descripcion_proalmex']
                tamano_raw = row['tamano_proalmex']
                if pd.isnull(desc_raw) or not str(desc_raw).strip() or str(desc_raw).strip().lower() == 'nan':
                    return 0.0
                desc   = str(desc_raw).strip()
                tamano = '' if (pd.isnull(tamano_raw) or str(tamano_raw).strip().lower() == 'nan') else str(tamano_raw).strip()
                return map_volumen_proalmex.get((desc.lower(), tamano.lower()), 0.0)

            df_proalmex['volumen_precalc'] = (
                df_proalmex.apply(_volumen_unitario_proalmex, axis=1) * df_proalmex['Piezas']
            )
            dfs_procesados.append(df_proalmex)

    if not dfs_procesados:
        return {'status': 'error', 'mensaje': 'No se cargaron archivos válidos o están vacíos.'}

    # ── 4. Consolidar ────────────────────────────────────────────────────────
    df_consolidado = pd.concat(dfs_procesados, ignore_index=True)
    df_consolidado['clave_sae'] = (
        pd.to_numeric(df_consolidado['clave_sae'] if 'clave_sae' in df_consolidado.columns else pd.Series(dtype=float),
                      errors='coerce').fillna(0).astype(int)
    )
    df_consolidado['Piezas'] = (
        pd.to_numeric(df_consolidado['Piezas'] if 'Piezas' in df_consolidado.columns else pd.Series(dtype=float),
                      errors='coerce').fillna(0)
    )

    # ── 5. Cruzar con catálogo por clave_sae (peso y volumen) ────────────────
    df_enrich = df_consolidado.merge(
        df_productos[['clave_sae', 'peso', 'volumen']], on='clave_sae', how='left'
    )
    # Claves ICG (clave_sae) procesadas y las que no se encontraron en el catálogo
    # (clave_sae == 0 se descarta: indica fila sin clave válida en el Excel origen)
    mask_icg = (df_enrich['Proveedor'] == 'ICG') & (df_enrich['clave_sae'] != 0)
    total_claves_icg = int(df_enrich.loc[mask_icg, 'clave_sae'].nunique())
    claves_icg_no_encontradas = sorted(
        df_enrich.loc[mask_icg & df_enrich['peso'].isna(), 'clave_sae'].unique().tolist()
    )

    df_enrich['peso_total_fila']    = df_enrich['Piezas'] * df_enrich['peso'].fillna(0)
    df_enrich['volumen_total_fila'] = df_enrich['Piezas'] * df_enrich['volumen'].fillna(0)

    # Para filas Proalmex usamos el peso y volumen pre-calculados por (linea, tamano)
    # ya que productos_proalmex no comparte clave_sae con el catálogo ICG
    if 'peso_precalc' in df_enrich.columns:
        mask_proalmex = df_enrich['Proveedor'] == 'Proalmex'
        df_enrich.loc[mask_proalmex, 'peso_total_fila'] = (
            df_enrich.loc[mask_proalmex, 'peso_precalc'].fillna(0)
        )
        if 'volumen_precalc' in df_enrich.columns:
            df_enrich.loc[mask_proalmex, 'volumen_total_fila'] = (
                df_enrich.loc[mask_proalmex, 'volumen_precalc'].fillna(0)
            )

    # Para filas Bimbo usamos los valores pre-calculados por codigo_barra
    # ya que productos_bimbo no comparte clave_sae con el catálogo ICG
    if 'bimbo_peso_precalc' in df_enrich.columns:
        mask_bimbo = df_enrich['Proveedor'] == 'Bimbo'
        df_enrich.loc[mask_bimbo, 'peso_total_fila']    = df_enrich.loc[mask_bimbo, 'bimbo_peso_precalc'].fillna(0)
        df_enrich.loc[mask_bimbo, 'volumen_total_fila'] = df_enrich.loc[mask_bimbo, 'bimbo_volumen_precalc'].fillna(0)

    # ── 6. Agrupar por Sucursal + Proveedor ──────────────────────────────────
    df_agrupado_peso = df_enrich.groupby(['Sucursal', 'Proveedor']).agg(
        total_peso=('peso_total_fila', 'sum')
    ).reset_index()

    df_agrupado_vol = df_enrich.groupby(['Sucursal', 'Proveedor']).agg(
        total_volumen=('volumen_total_fila', 'sum')
    ).reset_index()

    # ── 7. Delegar cálculo a calculadora.py ──────────────────────────────────
    datos_peso, desglose_peso         = calcular_peso(df_agrupado_peso, map_id_sucursal)
    datos_volumen, desglose_volumen   = calcular_volumen(df_agrupado_vol, map_id_sucursal)

    return {
        'status':           'ok',
        'data':             datos_peso,
        'desglose':         desglose_peso,
        'datos_volumen':    datos_volumen,
        'desglose_volumen': desglose_volumen,
        'advertencias': {
            'claves_no_encontradas_icg':        claves_icg_no_encontradas,
            'total_claves_icg':                 total_claves_icg,
            'claves_no_encontradas_proalmex':   sorted(claves_proalmex_no_encontradas),
            'total_claves_proalmex':            len(claves_proalmex_total),
            'codigos_no_encontrados_bimbo':     sorted(codigos_bimbo_no_encontrados),
            'total_codigos_bimbo':              len(codigos_bimbo_total),
        },
    }


# ══════════════════════════════════════════════════════════════════════════════
# CLIENTES MAYORISTAS
# ══════════════════════════════════════════════════════════════════════════════

def procesar_mayoristas(archivo) -> dict:
    """
    Lee el archivo Excel de Clientes Mayoristas, consolida el peso total
    por cliente y resuelve el nombre contra la colección 'clientes_mayoristas'
    en MongoDB.

    Returns:
        {
          'status': 'ok' | 'error',
          'consolidado': [
            {'codigo': int, 'nombre': str, 'peso_total_kg': float},
            …
          ],
          'advertencias': {
            'codigos_no_encontrados':     [int, …],   # no existen en clientes_mayoristas
            'total_codigos_mayoristas':   int,        # total de códigos distintos procesados
          },
        }
    """
    df = LectorMayoristas.leer_y_normalizar(archivo)
    if df.empty:
        return {
            'status':  'error',
            'mensaje': 'No se pudo leer el archivo o no contiene datos válidos.',
        }

    # ── Resolver nombres desde SQL Server ────────────────────────────────────
    try:
        from db import get_db, get_table
        from sqlalchemy import select

        db    = get_db()
        tabla = get_table('clientes_mayoristas')
        clientes_db = db.execute(
            select(tabla.c.id_cliente, tabla.c.nombre, tabla.c.activo)
        ).all()
        # id_cliente ya es BIGINT en SQL Server (a diferencia de Mongo, donde
        # podía llegar como str/Decimal128/int según el cliente que lo insertó).
        map_nombre = {}
        excluidos  = set()
        for c in clientes_db:
            if c.id_cliente is None:
                continue
            cid = int(c.id_cliente)
            map_nombre[cid] = c.nombre
            if c.activo is False:
                excluidos.add(cid)
    except Exception as e:
        print(f"[procesar_mayoristas] Error al conectar con SQL Server: {e}")
        map_nombre = {}
        excluidos  = set()
        excluidos  = set()

    # ── Construir resultado consolidado ─────────────────────────────────────
    consolidado = []
    codigos_total: set[int] = set()
    codigos_no_encontrados: set[int] = set()
    for _, row in df.iterrows():
        codigo = int(row['codigo_cliente'])
        if codigo in excluidos:
            continue
        codigos_total.add(codigo)
        if codigo not in map_nombre:
            codigos_no_encontrados.add(codigo)
        nombre = map_nombre.get(codigo, f'Cliente {codigo}')
        consolidado.append({
            'documento':     str(row.get('documento', '') or codigo),
            'codigo':        codigo,
            'nombre':        nombre,
            'peso_total_kg': float(row['peso_total_kg']),
        })

    # Ordenar por número de documento (referencia principal del pedido)
    consolidado.sort(key=lambda x: str(x.get('documento', '')))

    return {
        'status':       'ok',
        'consolidado':  consolidado,
        'advertencias': {
            'codigos_no_encontrados':   sorted(codigos_no_encontrados),
            'total_codigos_mayoristas': len(codigos_total),
        },
    }
