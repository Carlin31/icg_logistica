# Diseño: auto-avance de pestaña al guardar datos en Extracción

## Contexto

Sigue del cambio previo (`2026-07-08-confirmar-seleccion-ultima-pestana-design.md`),
que ya deja el botón "Confirmar selección" visible únicamente en la pestaña
Mayoristas. Ahora se pide que, al presionar "Guardar datos", el sistema avance
automáticamente a la siguiente pestaña del flujo, para que el usuario no tenga
que cambiarla manualmente en el camino normal.

`guardarDatos()` (`static/js/extraccion.js:620-735`) es un guardado **global**:
en un solo clic guarda `consolidadoPeso`/`consolidadoVolumen` (que combinan los
tres perfiles ICG/Proalmex/Bimbo), Mayoristas y las fuentes eliminadas —no hay
un guardado independiente por proveedor. El "avance" descrito por el usuario,
por tanto, no depende de qué se guardó técnicamente, sino de en qué pestaña
estaba el usuario al presionar el botón: se interpreta como "ya revisé esta
sección, sigo con la siguiente".

## Objetivo

Al guardar datos exitosamente, avanzar automáticamente a la siguiente pestaña
según el orden: Consolidado → ICG → Proalmex → Bimbo → Mayoristas. Al guardar
estando en Mayoristas (última), no avanza (se mantiene ahí). Se añade un aviso
en el toast de éxito indicando a qué pestaña se avanzó.

## Alcance

- Cambio de frontend únicamente en `static/js/extraccion.js`. Sin cambios de
  backend, HTML ni CSS.
- El auto-avance solo ocurre si el guardado fue exitoso y hubo algo que
  guardar (mismo criterio que ya usa el código para decidir el texto del
  toast de éxito: `hayPeso || hayVol || hayMayoristas`). Si el único cambio
  fue eliminar fuentes (`hayEliminadas` sin datos nuevos), **no** avanza.
- Si el guardado falla por completo (excepción de red, capturada en el
  `catch` de `guardarDatos`) o no había nada que guardar (return anticipado
  por "No hay datos para guardar"), no avanza.
- La navegación manual por clic en pestañas se mantiene sin cambios (el
  usuario puede seguir haciendo clic en cualquier pestaña en cualquier
  momento); el auto-avance es una conveniencia adicional, no un bloqueo.
- Nota de comportamiento preexistente fuera de alcance: si el guardado de
  Tiendas Lores responde con error genérico (no 400), el código actual ya
  muestra un toast de error y luego, más abajo, sobreescribe ese toast con
  uno de éxito (`static/js/extraccion.js:694-696` seguido de
  `:717-724`, un único elemento `.ext-toast` reutilizado). Este comportamiento
  no se modifica ni se corrige como parte de este cambio; el auto-avance
  simplemente sigue el mismo criterio (`hayPeso || hayVol || hayMayoristas`)
  que ya determina ese toast final.

## Diseño

En `static/js/extraccion.js`:

1. Añadir cerca de `PERFILES` (línea ~51):

   ```js
   const FLOW_ORDER  = ['consolidado', 'icg', 'proalmex', 'bimbo', 'mayoristas'];
   const FLOW_LABELS = {
     consolidado: 'Consolidado',
     icg:         'ICG',
     proalmex:    'Proalmex',
     bimbo:       'Bimbo',
     mayoristas:  'Mayoristas',
   };
   ```

2. Añadir una función `avanzarSiguientePestana()` cerca de `cambiarTab()`
   (después de la línea ~257):

   ```js
   async function avanzarSiguientePestana() {
     const idx = FLOW_ORDER.indexOf(state.tabActiva);
     if (idx === -1 || idx === FLOW_ORDER.length - 1) return null;
     const siguiente = FLOW_ORDER[idx + 1];
     await cambiarTab(siguiente);
     return siguiente;
   }
   ```

   Reutiliza `cambiarTab()` tal cual (misma función que ya actualiza clases
   `active` de tabs/paneles y llama a `actualizarBotonConfirmar()`), así que
   no duplica lógica de cambio de pestaña.

3. Modificar el bloque final de éxito dentro de `guardarDatos()`
   (`static/js/extraccion.js:717-724`), que hoy es:

   ```js
   state.hayUnsaved = false;
   actualizarUnsavedIndicator();
   mostrarToast(
     (hayPeso || hayVol || hayMayoristas)
       ? 'Datos guardados correctamente'
       : 'Fuentes eliminadas. Regenera las rutas VRP para actualizar las asignaciones.',
     'ok'
   );
   ```

   para que quede:

   ```js
   state.hayUnsaved = false;
   actualizarUnsavedIndicator();

   const huboGuardado = hayPeso || hayVol || hayMayoristas;
   const siguientePestana = huboGuardado ? await avanzarSiguientePestana() : null;

   let mensaje = huboGuardado
     ? 'Datos guardados correctamente'
     : 'Fuentes eliminadas. Regenera las rutas VRP para actualizar las asignaciones.';
   if (siguientePestana) {
     mensaje += `. Avanzando a ${FLOW_LABELS[siguientePestana]}…`;
   }
   mostrarToast(mensaje, 'ok');
   ```

   Solo hay un elemento `.ext-toast` reutilizado en el DOM (`mostrarToast`,
   línea ~1152), así que el aviso de avance se concatena en el mismo toast de
   éxito en vez de disparar un segundo toast (que reemplazaría al primero
   antes de que fuera legible).

No se requiere estado adicional ni cambios en `cambiarTab()` mismo (ya quedó
listo para ser invocada desde cualquier punto tras el cambio anterior).

## Pruebas / verificación

Manual, en navegador (no hay framework de tests JS en este repo):

1. Con datos cargados en ICG, Proalmex, Bimbo y Mayoristas, empezar en la
   pestaña Consolidado y presionar "Guardar datos": debe guardar y pasar a
   la pestaña ICG, con el toast mostrando "...Avanzando a ICG…".
2. Repetir en ICG → debe avanzar a Proalmex; en Proalmex → a Bimbo; en Bimbo
   → a Mayoristas.
3. En Mayoristas, presionar "Guardar datos": debe guardar y permanecer en
   Mayoristas (sin mensaje de avance), con "Confirmar selección" visible
   (por el cambio anterior).
4. Verificar que la navegación manual por clic en cualquier pestaña sigue
   funcionando en cualquier momento, sin restricciones nuevas.
5. Provocar el caso "No hay datos para guardar" (todo vacío) y confirmar que
   no cambia de pestaña.
6. Simular un error de red (p. ej. desconectando el backend) y confirmar que
   no cambia de pestaña ni muestra el mensaje de avance.
