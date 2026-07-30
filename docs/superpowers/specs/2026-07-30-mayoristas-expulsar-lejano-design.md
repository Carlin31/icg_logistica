# Diseño: Sobrecarga de mayoristas — expulsar al más lejano (Parte 1)

**Fecha:** 2026-07-30
**Estado:** Aprobado (diseño) — Parte 1 de 2. La Parte 2 (preferir mismo día al
reubicar) queda pospuesta.

## Problema

Cuando una ruta se sobrecarga al integrar mayoristas,
`_resolver_sobrecarga_mayoristas` (logic/mayoristas_logic.py) expulsa al
mayorista **más pesado**. Eso saca al mayorista cercano-pesado y conserva a los
lejanos-livianos. Caso real observado (logística 27-31 jul, ruta F 350_1
martes = Cosamaloapan): el pedido **BB3905** (Cosamaloapan, a 2.8 km del
centroide, 206 kg) fue expulsado a otra ruta/día, mientras **AMAVER** (7.0 km,
108 kg) y **LA CONA** (7.6 km, 24 kg) se quedaron. Resultado geográficamente
absurdo.

## Objetivo

Al resolver una sobrecarga de mayoristas, **expulsar al mayorista más LEJANO del
centroide de su ruta** (no al más pesado), para conservar en la ruta a los
mayoristas geográficamente afines a ella.

## Alcance

- Solo cambia el **criterio de selección del mayorista a expulsar** dentro de
  `_resolver_sobrecarga_mayoristas`. El resto (a dónde se reubica el excedente,
  historial, capacidad como límite duro) se mantiene igual.
- **Fuera de alcance (Parte 2):** preferir una ruta del mismo día al reubicar el
  excedente (requiere enhebrar el día desde los llamadores).

## Diseño

- El orden de expulsión pasa de `peso_kg` descendente a **distancia al centroide
  de la ruta** descendente (más lejano primero), usando `centros_ruta` (ya
  disponible en la función) y `_haversine_km` (ya disponible).
- **Mayoristas sin coordenadas**: no se pueden medir → prioridad de expulsión
  mínima (se conservan; se expulsan solo si no queda otro remedio).
- **Interruptor** `MAYORISTAS_GEOGRAFICO` (constante de módulo). En `True`
  (por defecto) expulsa por lejanía; en `False` restaura el comportamiento
  anterior idéntico (expulsa por peso).
- No cambia la firma pública ni el contrato de `calcular_distribucion_mayoristas`.

## Pruebas

Sobre `_resolver_sobrecarga_mayoristas` directamente (función pura sobre dicts):

1. **Expulsa al lejano, conserva al cercano:** ruta sobrecargada con un mayorista
   cercano-pesado y uno lejano-liviano → con el interruptor en `True` se expulsa
   el lejano; el cercano se queda.
2. **Interruptor apagado = comportamiento previo:** con `False`, se expulsa el
   más pesado (como antes).
3. **Sin coordenadas:** un mayorista sin lat/lon no se expulsa por lejanía
   (se conserva si hay otro que sacar).
4. **Capacidad dura:** el destino nunca excede su capacidad (invariante previa
   preservada).
