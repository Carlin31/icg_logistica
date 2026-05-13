import pandas as pd

class LectorProalmex:
    MAPEO_COLUMNAS = {'Clave': '#', 'Producto': 'Descripción'}

    @staticmethod
    def leer_y_normalizar(archivo) -> pd.DataFrame:
        try:
            df = pd.read_excel(archivo, header=1)
            df = df.rename(columns=LectorProalmex.MAPEO_COLUMNAS)
            
            if '#' not in df.columns:
                return pd.DataFrame()
                
            # Ampliamos la lista de exclusión con los nuevos requerimientos
            columnas_excluir = [
                '#', 'Descripción', 'Empaque', 'Capacidad', 
                'Costo', 'Importe', 'Total Cajas', 'cap','PEDIDO'
            ]
            
            # Filtramos las sucursales
            cols_sucursales = [
                col for col in df.columns 
                if str(col).strip() not in columnas_excluir
            ]
            
            df_melted = df.melt(id_vars=['#'], value_vars=cols_sucursales, var_name='Sucursal', value_name='Piezas')
            df_melted = df_melted.rename(columns={'#': 'clave_sae'})
            
            df_melted['clave_sae'] = pd.to_numeric(df_melted['clave_sae'], errors='coerce').fillna(0).astype(int)
            df_melted['Piezas'] = pd.to_numeric(df_melted['Piezas'], errors='coerce').fillna(0)
            
            df_final = df_melted[df_melted['Piezas'] > 0].copy()
            return df_final[['Sucursal', 'clave_sae', 'Piezas']]

        except Exception as e:
            print(f"Error procesando Proalmex: {e}")
            return pd.DataFrame()