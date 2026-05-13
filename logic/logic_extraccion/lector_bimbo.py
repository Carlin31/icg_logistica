import pandas as pd

class LectorBimbo:
    @staticmethod
    def leer_y_normalizar(archivo) -> pd.DataFrame:
        try:
            # header=1 toma el segundo registro como cabecera
            df = pd.read_excel(archivo, header=1) 

            if '#' not in df.columns:
                return pd.DataFrame()

            max_col = min(112, len(df.columns))
            
            # Definimos las columnas que no deben ser interpretadas como sucursales
            columnas_excluir = ['Costo', 'Importe', 'Total Cajas', 'cap','PEDIDO']
            
            # Filtramos para obtener solo las sucursales reales
            cols_sucursales = [
                col for col in df.columns[11:max_col] 
                if str(col).strip() not in columnas_excluir
            ]

            df_subset = df[['#'] + list(cols_sucursales)]
            df_melted = df_subset.melt(id_vars=['#'], value_vars=cols_sucursales, var_name='Sucursal', value_name='Piezas')

            df_melted = df_melted.rename(columns={'#': 'clave_sae'})
            df_melted['clave_sae'] = pd.to_numeric(df_melted['clave_sae'], errors='coerce').fillna(0).astype(int)
            df_melted['Piezas'] = pd.to_numeric(df_melted['Piezas'], errors='coerce').fillna(0)
            df_melted['Sucursal'] = df_melted['Sucursal'].astype(str).str.strip()

            df_final = df_melted[df_melted['Piezas'] > 0].copy()
            return df_final[['Sucursal', 'clave_sae', 'Piezas']]
            
        except Exception as e:
            print(f"Error procesando Bimbo: {e}")
            return pd.DataFrame()