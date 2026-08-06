import pandas as pd


class LectorProalmex:
    MAPEO_COLUMNAS = {'Clave': '#', 'Producto': 'Descripción'}

    # Columnas del Excel que son atributos del producto, no sucursales
    COLUMNAS_EXCLUIR = {
        '#', 'Descripción', 'Tamaño', 'Empaque', 'Capacidad',
        'Costo', 'Importe', 'Total Cajas', 'cap', 'PEDIDO',
    }

    # Sufijos de columnas auxiliares que aparecen pegadas al nombre de una
    # sucursal (ej. "Tiendas Lores IMPORTE", "Tiendas Lores INV. DISPONIBLE",
    # "Tiendas Lores INV. PROALMEX", "Tiendas Lores TOTAL CAJAS") y no
    # representan piezas de pedido reales. 'INV. BODEGA' y 'OBSERVACIONES' son
    # columnas de inventario/notas del Excel, no sucursales: sin ellas se
    # colaban como destinos falsos con id_sucursal 'N/A' (ver calculadora.py).
    SUFIJOS_EXCLUIR = (
        'IMPORTE', 'INV. DISPONIBLE', 'INV. PROALMEX', 'INV. BODEGA',
        'TOTAL CAJAS', 'OBSERVACIONES',
    )

    # Palabras clave de estas mismas columnas auxiliares, buscadas como
    # substring (no solo como sufijo exacto): variantes reales del Excel como
    # 'INV. EN BODEGA' (con 'EN' insertado) no terminan en 'INV. BODEGA' y se
    # colaban con id_sucursal 'N/A' pese a SUFIJOS_EXCLUIR (caso real,
    # logística 27-31 jul 2026). 'BODEGA' e 'IMPORTE' ya cubren sus
    # variantes exactas de SUFIJOS_EXCLUIR; no se repiten para no perder la
    # documentación de cada caso real arriba.
    PALABRAS_EXCLUIR = ('BODEGA', 'DISPONIBLE', 'OBSERVACIONES')

    @staticmethod
    def leer_y_normalizar(archivo) -> pd.DataFrame:
        """
        Lee el Excel de Proalmex y retorna un DataFrame con:
            Sucursal | descripcion_proalmex | tamano_proalmex | Piezas

        Los productos se identifican por la combinación (Descripción, Tamaño)
        para poder cruzarlos contra la colección productos_proalmex en MongoDB.
        """
        try:
            df = pd.read_excel(archivo, header=1)
            df = df.rename(columns=LectorProalmex.MAPEO_COLUMNAS)

            if 'Descripción' not in df.columns:
                return pd.DataFrame()

            # Columnas de sucursales = todo lo que no sea un atributo de producto
            # ni una columna auxiliar de IMPORTE/INVENTARIO pegada a una sucursal
            cols_sucursales = [
                col for col in df.columns
                if str(col).strip() not in LectorProalmex.COLUMNAS_EXCLUIR
                and not str(col).strip().upper().endswith(LectorProalmex.SUFIJOS_EXCLUIR)
                and not any(p in str(col).strip().upper() for p in LectorProalmex.PALABRAS_EXCLUIR)
            ]

            # Preservar Descripción y Tamaño (si existe) como identificadores
            id_vars = ['Descripción']
            if 'Tamaño' in df.columns:
                id_vars.append('Tamaño')

            df_melted = df.melt(
                id_vars=id_vars,
                value_vars=cols_sucursales,
                var_name='Sucursal',
                value_name='Piezas',
            )

            df_melted['Piezas'] = pd.to_numeric(df_melted['Piezas'], errors='coerce').fillna(0)
            df_final = df_melted[df_melted['Piezas'] > 0].copy()

            df_final = df_final.rename(columns={
                'Descripción': 'descripcion_proalmex',
                'Tamaño':      'tamano_proalmex',
            })

            # Garantizar que tamano_proalmex exista aunque no haya columna en el Excel
            if 'tamano_proalmex' not in df_final.columns:
                df_final['tamano_proalmex'] = ''

            return df_final[['Sucursal', 'descripcion_proalmex', 'tamano_proalmex', 'Piezas']]

        except Exception as e:
            print(f"[LectorProalmex] Error procesando archivo: {e}")
            return pd.DataFrame()
