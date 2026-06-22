import pandas as pd

class LectorICG:
    COLUMNAS_IGNORAR = ['TOTAL PIEZAS', 'TOTAL CAJAS', 'TOTAL IMPORTE', 'INV. BODEGA', 'INV. DISPONIBLE', 'COSTO', 'cap', 'CAP ICG', 'Costo','Importe','Total Cajas','PEDIDO','PIEZAS','']
    COLUMNAS_METADATA = ['#', 'Proveedor', 'Línea', 'Marca', 'PROVEEDOR', 'CLAVE SAE', 'Descripción', 'Tamaño', 'Clave', 'Producto', 'Origen']

    @classmethod
    def leer_y_normalizar(cls, archivo) -> pd.DataFrame:
        try:
            df = pd.read_excel(archivo, header=1)

            # Limpieza exhaustiva: buscar palabras clave en el nombre de la columna
            cols_a_borrar = [
                col for col in df.columns
                if str(col).upper().strip() in cls.COLUMNAS_IGNORAR
                or 'IMPORTE' in str(col).upper()
                or 'COSTO' in str(col).upper()
                or 'CAP' in str(col).upper()
                or 'CAJAS' in str(col).upper()
                or 'COMPRAS' in str(col).upper()
                or 'VTA' in str(col).upper()
            ]

            df = df.drop(columns=cols_a_borrar, errors='ignore')

            # Determinar qué columna contiene el identificador de producto SAE.
            # En el formato "Pedido Directo" ICG existe la columna 'CLAVE SAE' que es
            # el código real del sistema SAE y coincide con clave_sae en MongoDB.
            # La columna '#' es un número de fila secuencial y NO es el identificador.
            if 'CLAVE SAE' in df.columns:
                col_id = 'CLAVE SAE'
            elif '#' in df.columns:
                col_id = '#'
            else:
                return pd.DataFrame()

            df = df.dropna(subset=[col_id])

            # Unpivot (Melt)
            cols_existentes = [c for c in cls.COLUMNAS_METADATA if c in df.columns]
            df_melted = df.melt(id_vars=cols_existentes, var_name="Sucursal", value_name="Piezas")

            # Normalización: usar col_id como clave_sae
            df_melted = df_melted.rename(columns={col_id: 'clave_sae'})
            df_melted['clave_sae'] = pd.to_numeric(df_melted['clave_sae'], errors='coerce').fillna(0).astype(int)
            df_melted['Piezas'] = pd.to_numeric(df_melted['Piezas'], errors='coerce').fillna(0)

            # Filtrar solo sucursales con pedidos reales
            df_final = df_melted[df_melted['Piezas'] > 0].copy()
            return df_final[['Sucursal', 'clave_sae', 'Piezas']]

        except Exception as e:
            print(f"Error procesando ICG: {e}")
            return pd.DataFrame()