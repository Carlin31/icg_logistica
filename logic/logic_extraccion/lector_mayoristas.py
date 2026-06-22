"""
logic/logic_extraccion/lector_mayoristas.py

Lee y normaliza el archivo Excel de Clientes Mayoristas.

Formato esperado del Excel:
  - Encabezados en la fila 2 (header=1 en pandas)
  - Columna 'Documento'  → identificador único del pedido (referencia principal)
  - Columna 'Cliente'    → código numérico del cliente (identificador del mayorista)
  - Columna 'Peso total' → peso en kg de ese pedido/documento

El lector agrupa los registros por (Documento, Cliente) y suma el peso total,
devolviendo un DataFrame limpio con [documento, codigo_cliente, peso_total_kg].

Si el archivo no tiene la columna 'Documento', se usa el código del cliente como
documento (compatibilidad hacia atrás con archivos del formato anterior).

La búsqueda del nombre del cliente se delega a extraccion_logic.py,
que lo resuelve contra la colección 'clientes_mayoristas' en MongoDB.

Reglas de interpretación numérica para 'Peso total':
  - El punto (.) es SIEMPRE separador decimal, nunca de miles.
  - Si el valor llega como texto con coma decimal (ej. "1.093,50"),
    se elimina el punto de miles y la coma se convierte a punto.
  - El resultado se redondea a 2 decimales.
"""
import re
import pandas as pd


def _parsear_peso(valor) -> float:
    """
    Convierte un valor de peso a float garantizando que el punto (.)
    se interprete como separador decimal y no como separador de miles.
    """
    if pd.isnull(valor):
        return 0.0
    if isinstance(valor, (int, float)):
        return round(float(valor), 2)

    s = str(valor).strip()

    # Formato europeo: punto de miles + coma decimal  →  "1.093,50"
    if ',' in s:
        s = s.replace('.', '').replace(',', '.')

    # Eliminar cualquier carácter que no sea dígito, punto o signo negativo
    s = re.sub(r'[^\d.\-]', '', s)

    try:
        return round(float(s), 2)
    except ValueError:
        return 0.0


class LectorMayoristas:

    COLUMNA_DOCUMENTO = 'Documento'
    COLUMNA_CLIENTE   = 'Cliente'
    COLUMNA_PESO      = 'Peso total'

    @classmethod
    def _detectar_fila_encabezado(cls, archivo, max_filas: int = 50) -> int:
        """
        Busca en las primeras `max_filas` del archivo la fila que contiene los
        nombres de columna esperados ('Documento', 'Cliente', 'Peso total'),
        en lugar de asumir que el encabezado siempre está en la fila 2.

        Retorna el índice de fila (0-based, formato `header=` de pandas) con
        más coincidencias. Si ninguna fila tiene al menos 2 coincidencias,
        devuelve 1 (comportamiento anterior, fila 2 del Excel).
        """
        try:
            if hasattr(archivo, 'seek'):
                archivo.seek(0)
            crudo = pd.read_excel(archivo, header=None, nrows=max_filas, dtype=str)
        except Exception as e:
            print(f"[LectorMayoristas] No se pudo escanear encabezados: {e}")
            return 1
        finally:
            if hasattr(archivo, 'seek'):
                archivo.seek(0)

        objetivo = {cls.COLUMNA_DOCUMENTO, cls.COLUMNA_CLIENTE, cls.COLUMNA_PESO}
        mejor_fila, mejor_coincidencias = 1, 0

        for idx, fila in crudo.iterrows():
            valores = {str(v).strip() for v in fila.tolist() if pd.notna(v)}
            coincidencias = len(objetivo & valores)
            if coincidencias > mejor_coincidencias:
                mejor_coincidencias, mejor_fila = coincidencias, idx

        return mejor_fila if mejor_coincidencias >= 2 else 1

    @classmethod
    def leer_y_normalizar(cls, archivo) -> pd.DataFrame:
        """
        Lee el archivo Excel de mayoristas y retorna un DataFrame con:
            documento (str) | codigo_cliente (int) | peso_total_kg (float)

        Cada documento es un pedido único. Filas con 'Cliente' nulo o cero
        son descartadas. Si no existe la columna 'Documento', se usa
        str(codigo_cliente) como documento (compatibilidad hacia atrás).

        El nombre de las columnas se busca en todo el documento (no solo en
        una fila fija), por lo que el encabezado puede estar en cualquier
        posición dentro de las primeras filas del archivo.
        """
        try:
            fila_encabezado = cls._detectar_fila_encabezado(archivo)
            if hasattr(archivo, 'seek'):
                archivo.seek(0)
            df = pd.read_excel(archivo, header=fila_encabezado, dtype=str)

            # Verificar columnas mínimas requeridas
            cols_requeridas = {cls.COLUMNA_CLIENTE, cls.COLUMNA_PESO}
            faltantes = cols_requeridas - set(df.columns)
            if faltantes:
                print(f"[LectorMayoristas] Columnas faltantes en el archivo: {faltantes}")
                return pd.DataFrame()

            tiene_documento = cls.COLUMNA_DOCUMENTO in df.columns

            # Conservar columnas relevantes
            cols_usar = [cls.COLUMNA_CLIENTE, cls.COLUMNA_PESO]
            if tiene_documento:
                cols_usar = [cls.COLUMNA_DOCUMENTO] + cols_usar
            df = df[cols_usar].copy()

            # Eliminar filas donde el código de cliente esté vacío
            df = df.dropna(subset=[cls.COLUMNA_CLIENTE])
            df = df[df[cls.COLUMNA_CLIENTE].str.strip() != '']

            # Normalizar código de cliente → entero
            df[cls.COLUMNA_CLIENTE] = (
                pd.to_numeric(df[cls.COLUMNA_CLIENTE], errors='coerce')
                .fillna(0).astype(int)
            )

            # Normalizar peso
            df[cls.COLUMNA_PESO] = df[cls.COLUMNA_PESO].apply(_parsear_peso)

            # Descartar códigos de cliente inválidos
            df = df[df[cls.COLUMNA_CLIENTE] > 0]

            if df.empty:
                return pd.DataFrame()

            if tiene_documento:
                # Normalizar documento: string limpio, sin nulos
                df[cls.COLUMNA_DOCUMENTO] = (
                    df[cls.COLUMNA_DOCUMENTO]
                    .fillna('')
                    .astype(str)
                    .str.strip()
                )
                # Filas sin documento heredan el código del cliente como documento
                mask_sin_doc = df[cls.COLUMNA_DOCUMENTO] == ''
                df.loc[mask_sin_doc, cls.COLUMNA_DOCUMENTO] = (
                    df.loc[mask_sin_doc, cls.COLUMNA_CLIENTE].astype(str)
                )

                # Agrupar por (Documento, Cliente) sumando peso
                df_agrupado = (
                    df.groupby([cls.COLUMNA_DOCUMENTO, cls.COLUMNA_CLIENTE], as_index=False)
                      .agg(peso_total_kg=(cls.COLUMNA_PESO, 'sum'))
                      .rename(columns={
                          cls.COLUMNA_DOCUMENTO: 'documento',
                          cls.COLUMNA_CLIENTE:   'codigo_cliente',
                      })
                )
            else:
                # Sin columna Documento: agrupa por cliente (comportamiento anterior)
                # y usa str(codigo_cliente) como documento
                df_agrupado = (
                    df.groupby(cls.COLUMNA_CLIENTE, as_index=False)
                      .agg(peso_total_kg=(cls.COLUMNA_PESO, 'sum'))
                      .rename(columns={cls.COLUMNA_CLIENTE: 'codigo_cliente'})
                )
                df_agrupado['documento'] = df_agrupado['codigo_cliente'].astype(str)
                df_agrupado = df_agrupado[['documento', 'codigo_cliente', 'peso_total_kg']]

            df_agrupado['peso_total_kg'] = df_agrupado['peso_total_kg'].round(2)

            return df_agrupado

        except Exception as e:
            print(f"[LectorMayoristas] Error procesando archivo: {e}")
            return pd.DataFrame()
