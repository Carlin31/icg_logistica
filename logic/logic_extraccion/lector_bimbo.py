import pandas as pd


class LectorBimbo:
    @staticmethod
    def leer_y_normalizar(archivo, columna_inicio: str = '') -> pd.DataFrame:
        try:
            # La fila 2 del Excel (índice 1) contiene los encabezados reales
            df = pd.read_excel(archivo, header=1)

            if 'Codigos de Barra' not in df.columns:
                return pd.DataFrame()

            # Filtrar filas que no son productos (pie de tabla): '#' o barcode vacíos
            df = df[pd.to_numeric(df['#'], errors='coerce').notna()].copy()
            df = df[df['Codigos de Barra'].notna()].copy()

            if df.empty:
                return pd.DataFrame()

            # ── Localizar columna ancla (case-insensitive) ───────────────────
            # El primer nombre_bimbo registrado en sucursales marca el inicio
            # de los mayoristas; todo lo que viene después son también mayoristas.
            cols_norm_lower = [str(col).strip().lower() for col in df.columns]
            ancla_lower     = columna_inicio.strip().lower()

            try:
                idx_inicio = cols_norm_lower.index(ancla_lower)
            except ValueError:
                print(f"LectorBimbo: columna ancla '{columna_inicio}' no encontrada en el archivo")
                return pd.DataFrame()

            # ── Columnas de mayoristas ───────────────────────────────────────
            columnas_excluir_lower = {'costo', 'importe', 'total cajas', 'cap', 'pedido', 'total'}
            cols_sucursales = [
                col for col in df.columns[idx_inicio:]
                if str(col).strip().lower() not in columnas_excluir_lower
            ]

            if not cols_sucursales:
                return pd.DataFrame()

            # ── Melt: una fila por (producto, sucursal) ──────────────────────
            df_subset = df[['Codigos de Barra'] + list(cols_sucursales)]
            df_melted = df_subset.melt(
                id_vars=['Codigos de Barra'],
                value_vars=cols_sucursales,
                var_name='Sucursal',
                value_name='Piezas',
            )

            # Convertir barcode float → string entero limpio (7501030490920.0 → '7501030490920')
            def _normalizar_barcode(val) -> str:
                if pd.isna(val) or str(val).strip() in ('', 'nan'):
                    return ''
                try:
                    return str(int(float(val)))
                except (ValueError, TypeError):
                    return str(val).strip()

            df_melted = df_melted.rename(columns={'Codigos de Barra': 'codigo_barra'})
            df_melted['codigo_barra'] = df_melted['codigo_barra'].apply(_normalizar_barcode)
            df_melted['Piezas']       = pd.to_numeric(df_melted['Piezas'], errors='coerce').fillna(0)
            df_melted['Sucursal']     = df_melted['Sucursal'].astype(str).str.strip()

            # Solo filas con barcode válido y piezas > 0
            df_final = df_melted[
                (df_melted['codigo_barra'] != '') &
                (df_melted['Piezas'] > 0)
            ].copy()

            return df_final[['Sucursal', 'codigo_barra', 'Piezas']]

        except Exception as e:
            print(f"Error procesando Bimbo: {e}")
            return pd.DataFrame()
