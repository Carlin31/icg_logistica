"""
logic/logic_extraccion/lector_mayoristas.py

Lee y normaliza el archivo Excel de Clientes Mayoristas.

Formato esperado del Excel:
  - Encabezados en la fila 2 (header=1 en pandas)
  - Columna 'Cliente'     → código numérico del cliente (identificador)
  - Columna 'Peso total'  → peso en kg de ese pedido/documento

El lector agrupa los registros por cliente y suma el peso total,
devolviendo un DataFrame limpio con [codigo_cliente, peso_total_kg].

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

    Casos que maneja:
      - Numérico (int/float): devuelve float directamente.
      - String con coma decimal europeo "1.093,50"  → 1093.50
      - String con solo punto decimal  "50.782"     → 50.782
      - String sin decimales           "150"        → 150.0
      - Cualquier otro caso no parseable            → 0.0
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

    COLUMNA_CLIENTE = 'Cliente'
    COLUMNA_PESO    = 'Peso total'

    @classmethod
    def leer_y_normalizar(cls, archivo) -> pd.DataFrame:
        """
        Lee el archivo Excel de mayoristas y retorna un DataFrame con:
            codigo_cliente (int) | peso_total_kg (float, 2 decimales)

        Filas con 'Cliente' nulo o cero son descartadas.
        Filas vacías intercaladas se eliminan al hacer dropna sobre la
        columna clave.
        """
        try:
            # Leer todo como objeto para aplicar _parsear_peso manualmente
            # y evitar que pandas interprete valores como separadores de miles.
            df = pd.read_excel(archivo, header=1, dtype=str)

            # Verificar columnas mínimas requeridas
            cols_requeridas = {cls.COLUMNA_CLIENTE, cls.COLUMNA_PESO}
            faltantes = cols_requeridas - set(df.columns)
            if faltantes:
                print(f"[LectorMayoristas] Columnas faltantes en el archivo: {faltantes}")
                return pd.DataFrame()

            # Conservar solo las columnas relevantes
            df = df[[cls.COLUMNA_CLIENTE, cls.COLUMNA_PESO]].copy()

            # Eliminar filas donde el código de cliente esté vacío
            df = df.dropna(subset=[cls.COLUMNA_CLIENTE])
            df = df[df[cls.COLUMNA_CLIENTE].str.strip() != '']

            # Normalizar código de cliente → entero
            df[cls.COLUMNA_CLIENTE] = (
                pd.to_numeric(df[cls.COLUMNA_CLIENTE], errors='coerce')
                .fillna(0).astype(int)
            )

            # Normalizar peso con la función de parseo seguro (punto = decimal)
            df[cls.COLUMNA_PESO] = df[cls.COLUMNA_PESO].apply(_parsear_peso)

            # Descartar códigos de cliente inválidos
            df = df[df[cls.COLUMNA_CLIENTE] > 0]

            if df.empty:
                return pd.DataFrame()

            # Consolidar: un registro por cliente con peso acumulado
            df_agrupado = (
                df.groupby(cls.COLUMNA_CLIENTE, as_index=False)
                  .agg(peso_total_kg=(cls.COLUMNA_PESO, 'sum'))
                  .rename(columns={cls.COLUMNA_CLIENTE: 'codigo_cliente'})
            )
            # Redondear el acumulado a 2 decimales
            df_agrupado['peso_total_kg'] = df_agrupado['peso_total_kg'].round(2)

            return df_agrupado

        except Exception as e:
            print(f"[LectorMayoristas] Error procesando archivo: {e}")
            return pd.DataFrame()
