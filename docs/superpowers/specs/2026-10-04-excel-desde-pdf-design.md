# Exportar la logística a Excel (a partir del PDF generado)

Fecha: 2026-10-04

## Objetivo

Agregar un botón **Descargar Excel** en la sección PDF, junto a *Descargar PDF*. El Excel es una
reinterpretación del PDF ya generado, en formato vertical (bloques de vehículo apilados en una sola
columna), como el ejemplo `LOG DEL 1 AL 5 DE JUNIO DEL 26_HT.xls` (hoja RESUMEN).

## Restricciones acordadas

- **No se toca `logic/pdf_logic.py` ni `POST /pdf/generar`.** El PDF ya funciona; el Excel lo lee.
- El botón **aparece solo después de generar el PDF** (hoy *Descargar PDF* está deshabilitado hasta entonces).
- Columnas iguales al PDF: DIA, APOYO, SEC., PARADA, PESO (KG), VOL (m³), % RUTA. Con filas TOTAL por día.
- Todo en commits locales; sin push ni deploy.

## Flujo

1. Usuario presiona **Generar PDF**; `pdf.js` guarda el blob (`_blobUrl`) como hoy.
2. `pdf.js` mantiene también el blob original (`_blob`) y muestra el botón `btn-excel`.
3. Clic en **Descargar Excel**: `POST /pdf/generar-excel` (multipart, campo `pdf`) con el mismo PDF
   de la vista previa.
4. El servidor responde el `.xlsx` como adjunto; `pdf.js` lo descarga como `<Nombre_logística>.xlsx`.

Ventaja: el Excel sale del PDF exacto que ve el usuario y no hay estado en el servidor (funciona con
varios workers de gunicorn).

## Componentes

### `logic/excel_logic.py` (nuevo)

`pdf_a_excel(bytes_pdf: bytes) -> bytes`

1. **Lectura** con `pdfplumber`: `page.find_tables()` en cada página.
2. **Orden de lectura:** el PDF es de dos columnas por página. Ordenar tablas por
   `(página, columna, y)` con columna = 0 si `x0 < ancho/2` y 1 en otro caso. Esto replica el orden
   en que `generar_pdf` llenó los frames.
3. **Continuaciones:** si una tabla tiene el mismo título de vehículo que la anterior (reportlab repite
   las 2 filas de encabezado con `repeatRows=2` si una tabla se parte), se fusiona con la anterior.
4. **Por tabla:** fila 0 = título del vehículo (`F 350_1 · CÉSAR (4 ton)`), fila 1 = encabezado,
   resto = paradas y totales.
   - Fila TOTAL: la celda PARADA empieza con `TOTAL`.
   - Día: las filas entre dos TOTAL forman un día. DIA y APOYO son el único texto no vacío de esas
     columnas en el grupo (la celda combinada del PDF centra el texto en una fila intermedia).
   - Mayorista: el texto de la fila es naranja (`non_stroking_color` de los caracteres ≈ `#EA580C`).
   - Subruta: fondo `C_SUBRUTA` en la fila (no cambia la lectura de datos; se puede omitir si no
     se detecta de forma fiable, ver Riesgos).
5. **Cabecera de la hoja:** líneas de texto sobre el primer bloque de la página 1
   (`INTEGRADORA COMERCIAL DEL GOLFO`, nombre y rango de la logística, `Expedido: ...`).
6. **Escritura** con `openpyxl` en una sola hoja `RESUMEN`:
   - Cabecera arriba, luego cada vehículo: barra azul (merge sobre las 7 columnas), fila de
     encabezado, filas de datos, fila en blanco.
   - DIA y APOYO combinados (merge) por día.
   - PESO, VOL y % RUTA como **números** (`1,286` → `1286`, `0.843`, `49%` → `0.49` con formato `0%`;
     los totales con un decimal `80.6%` → `0.806` con formato `0.0%`). `—` en VOL → celda vacía.
   - Mayoristas en naranja; fila TOTAL con el mismo fondo azul claro / naranja claro del PDF.
   - Anchos de columna fijos y bordes finos.
7. Devuelve los bytes del `.xlsx` (`BytesIO`).

Errores: PDF sin tablas de vehículo → `ValueError("El PDF no contiene tablas de rutas.")`.

### `router/pdf_router.py`

`POST /generar-excel`:
- Requiere logística activa (misma regla que `/generar`, 400 si no).
- Toma `request.files["pdf"]`; valida que exista, que empiece con `%PDF` y que pese ≤ 10 MB (400 si no).
- Llama `pdf_a_excel`; `ValueError` → 422, otra excepción → 500, con `{"status":"error","mensaje":...}`.
- Éxito: `send_file(BytesIO, as_attachment=True, download_name=f"{nombre}.xlsx",
  mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")`.
- Actualizar el docstring del módulo (lista de endpoints).

### `templates/pdf/index.html` y `static/js/pdf.js`

- Botón `btn-excel` (icono `file-spreadsheet`, clase `btn btn-descargar`, texto "Descargar Excel"),
  oculto por defecto, dentro de `.pdf-acciones` justo después de `btn-descargar`.
- `generarPDF()`: al iniciar, ocultar `btn-excel` y soltar `_blob`; al terminar bien, mostrarlo.
- `descargarExcel()`: `FormData` con el blob, `fetch('/pdf/generar-excel')`, loader durante la espera,
  mismo manejo de error que el PDF (`#mensaje-error`), descarga con nombre tomado de
  `Content-Disposition`.

### Dependencias y documentación

- `requirements.txt`: agregar `pdfplumber` (pin de la versión probada, 0.11.10).
- `README.md`: nuevo endpoint, nueva dependencia y el flujo del botón.

## Pruebas (`tests/test_excel_logic.py`)

Se generan PDFs de muestra con el código real de `_tabla_vehiculo` / `BaseDocTemplate` (sin tocarlo)
a partir de rutas sintéticas, y se convierten:

- Mismos vehículos, mismo orden de paradas y mismos pesos/volúmenes/porcentajes que las rutas de entrada.
- Filas TOTAL presentes por día y con los valores correctos.
- Mayorista detectado (naranja) y parada normal no.
- Ruta de varios días: DIA/APOYO correctos y combinados.
- Dos columnas y más de una página: orden de vehículos = orden del PDF.
- Valores numéricos reales en las celdas (`isinstance(..., (int, float))`), `—` → vacío.
- Router: sin logística → 400; sin archivo → 400; archivo que no es PDF → 400; éxito → xlsx válido.

Verificación manual: convertir el PDF real `Logistica_del_5_al_9_de_octubre_del_2026.pdf` y abrir el Excel.

## Riesgos y decisiones

- **Acoplamiento al formato del PDF.** Si cambian las columnas o los colores del PDF, el lector debe
  ajustarse. Mitigación: los tests generan el PDF con el código real, así que fallan si el PDF cambia
  y el Excel deja de calzar.
- **Detección de mayorista por color.** Es heurística. Tolerancia en la comparación de color; si no
  se detecta, la fila sale sin resaltar pero con los datos correctos.
- **Subrutas:** el sombreado de subruta es opcional; los datos nunca dependen de él.
- **Dependencias nuevas** (`pdfplumber` + pdfminer.six, Pillow, pypdfium2, cryptography): hay que
  reinstalar `requirements.txt` donde se despliegue. Fuera de alcance: despliegue.
- Fuera de alcance: columnas JUG/COMENTARIOS del ejemplo, hojas JUGUETES/MAYOREO/LOGISTICA LORES.
