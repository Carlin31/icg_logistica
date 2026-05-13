import pandas as pd
from datetime import datetime
import re
import logging

# Importamos la función para obtener la instancia de MongoDB
from db import get_db 

# Configuramos el log para que los errores salgan en la consola de Flask
logging.basicConfig(level=logging.ERROR, format='%(asctime)s - %(levelname)s - %(message)s')

class LectorDatos:
    """Encargado de la lectura de metadatos y conexión a Base de Datos MongoDB."""

    @staticmethod
    def obtener_fecha_excel(archivo) -> str:
        try:
            df_header = pd.read_excel(archivo, header=None, nrows=5)
            patron_fecha = r'(\d{2}/\d{2}/\d{4})|(\d{4}-\d{2}-\d{2})'
            for col in df_header.columns:
                for celda in df_header[col]:
                    val_str = str(celda)
                    match = re.search(patron_fecha, val_str)
                    if match:
                        return match.group(0)
            return datetime.now().strftime("%d/%m/%Y")
        except Exception:
            return datetime.now().strftime("%d/%m/%Y")

    @classmethod
    def cargar_pesos(cls) -> pd.DataFrame:
        """Carga el dataset de pesos y volumetría de productos desde MongoDB."""
        try:
            # Obtenemos la BD desde el contexto actual de la request de Flask
            db = get_db()
            
            # Asumimos que tu colección en Mongo se llama 'productos'
            coleccion_productos = db["productos"]
            
            # Extraemos todos los documentos sin filtro ({})
            documentos = list(coleccion_productos.find({}))
            
            if not documentos:
                return pd.DataFrame()
            
            # Convertimos los documentos de Mongo a un DataFrame de Pandas
            df = pd.DataFrame(documentos)
            
            # Eliminamos el '_id' nativo de Mongo para no arrastrar basura en el cruce de Pandas
            if '_id' in df.columns:
                df = df.drop(columns=['_id'])
                
            return df
        except Exception as e:
            logging.error(f"Error conectando a MongoDB (Productos): {e}")
            return pd.DataFrame()

    @classmethod
    def cargar_vehiculos(cls) -> pd.DataFrame:
        """Carga el dataset de vehículos desde MongoDB."""
        try:
            db = get_db()
            coleccion_vehiculos = db["vehiculos"]
            
            documentos = list(coleccion_vehiculos.find({}))
            
            if not documentos:
                return pd.DataFrame()
                
            df = pd.DataFrame(documentos)
            
            if '_id' in df.columns:
                df = df.drop(columns=['_id'])
                
            return df
        except Exception as e:
            logging.error(f"Error conectando a MongoDB (Vehículos): {e}")
            return pd.DataFrame()

    @staticmethod
    def leer_excel_pedidos(archivo) -> pd.DataFrame:
        try:
            return pd.read_excel(archivo, header=1)
        except Exception as e:
            logging.error(f"Error leyendo el Excel genérico: {e}")
            return pd.DataFrame()