"""
logic/logic_extraccion/calculadora.py
Cálculo consolidado de peso (kg) y volumen (m³) por sucursal y proveedor.

Fuente de volumen:
  El campo `volumen` viene directamente de MongoDB (colección `productos`),
  donde está pre-calculado como Double en m³:
    volumen = (largo_cm × ancho_cm × alto_cm) / 1_000_000
  No se recalcula aquí; se acumula: Σ(piezas × volumen_unitario) por sucursal.

Precisión:
  - La acumulación se realiza en float nativo (máxima precisión).
  - El redondeo a 6 decimales se aplica únicamente en el resultado final,
    alineado con el tipo Double del esquema de MongoDB.
"""
import math

_PROVEEDOR_KEY = {'ICG': 'icg', 'Proalmex': 'proalmex', 'Bimbo': 'bimbo'}
_DECIMALES_VOL = 6   # coincide con el Double del esquema productos.volumen


def calcular_peso(df_agrupado_peso: object, map_id_sucursal: dict) -> tuple[dict, dict]:
    """
    Produce el consolidado de peso y su desglose por perfil.

    Args:
        df_agrupado_peso: DataFrame agrupado con columnas
                          ['Sucursal', 'Proveedor', 'total_peso'].
        map_id_sucursal:  { nombre_tienda: id_sucursal }

    Returns:
        (datos_peso, desglose_peso)
          datos_peso   → { suc: {id_sucursal, icg_kg, proalmex_kg, bimbo_kg, total_kg} }
          desglose_peso → { perfil: { suc: {id_sucursal, kg} } }
    """
    datos    = {}
    desglose = {k: {} for k in _PROVEEDOR_KEY.values()}

    for _, row in df_agrupado_peso.iterrows():
        suc  = str(row['Sucursal'])
        prov = row['Proveedor']
        kg   = int(math.ceil(row['total_peso']))
        id_s = map_id_sucursal.get(suc, 'N/A')

        if suc not in datos:
            datos[suc] = {
                'id_sucursal': id_s,
                'icg_kg':      0,
                'proalmex_kg': 0,
                'bimbo_kg':    0,
                'total_kg':    0,
            }

        key = _PROVEEDOR_KEY.get(prov)
        if key:
            datos[suc][f'{key}_kg']  = kg
            datos[suc]['total_kg']  += kg
            desglose[key][suc] = {'id_sucursal': id_s, 'kg': kg}

    desglose = {k: v for k, v in desglose.items() if v}
    return datos, desglose


def calcular_volumen(df_agrupado_vol: object, map_id_sucursal: dict) -> tuple[dict, dict]:
    """
    Produce el consolidado de volumen y su desglose por perfil.

    Args:
        df_agrupado_vol: DataFrame agrupado con columnas
                         ['Sucursal', 'Proveedor', 'total_volumen'].
        map_id_sucursal: { nombre_tienda: id_sucursal }

    Returns:
        (datos_volumen, desglose_volumen)
          datos_volumen   → { suc: {id_sucursal, icg_m3, proalmex_m3, bimbo_m3, total_m3} }
          desglose_volumen → { perfil: { suc: {id_sucursal, m3} } }
    """
    datos    = {}
    desglose = {k: {} for k in _PROVEEDOR_KEY.values()}

    for _, row in df_agrupado_vol.iterrows():
        suc  = str(row['Sucursal'])
        prov = row['Proveedor']
        # Acumular en float completo; redondear al final para no perder precisión
        m3   = float(row['total_volumen'])
        id_s = map_id_sucursal.get(suc, 'N/A')

        if suc not in datos:
            datos[suc] = {
                'id_sucursal': id_s,
                'icg_m3':      0.0,
                'proalmex_m3': 0.0,
                'bimbo_m3':    0.0,
                'total_m3':    0.0,
            }

        key = _PROVEEDOR_KEY.get(prov)
        if key:
            m3_r = round(m3, _DECIMALES_VOL)
            datos[suc][f'{key}_m3']  = m3_r
            datos[suc]['total_m3']   = round(datos[suc]['total_m3'] + m3, _DECIMALES_VOL)
            desglose[key][suc] = {'id_sucursal': id_s, 'm3': m3_r}

    desglose = {k: v for k, v in desglose.items() if v}
    return datos, desglose
