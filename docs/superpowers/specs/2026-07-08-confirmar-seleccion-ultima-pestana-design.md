# Diseño: mostrar "Confirmar selección" solo en la última pestaña

## Contexto

En la página de Extracción de datos (`/extraccion/<slug>`), la barra de acciones
inferior (`#action-bar`) contiene dos botones: "Guardar datos" y "Confirmar
selección". Actualmente ambos se muestran u ocultan juntos según si hay datos
cargados (`actualizarUI()` en `static/js/extraccion.js`), sin importar qué
pestaña esté activa. Esto permite confirmar la selección antes de haber
revisado todos los proveedores.

El orden de pestañas en el DOM ya es: Consolidado → ICG → Proalmex → Bimbo →
Mayoristas (`templates/extraccion/index.html`), que coincide con el flujo de
revisión deseado (ICG → Proalmex → Bimbo → Mayoristas), con Consolidado como
resumen inicial.

## Objetivo

El botón "Confirmar selección" solo debe ser visible cuando el usuario esté en
la última pestaña del flujo (Mayoristas), para asegurar que revise cada
proveedor antes de confirmar.

## Alcance

- Cambio de frontend únicamente (JS, y CSS si hace falta un ajuste de estilo
  para el estado oculto). No se tocan rutas de Flask ni endpoints.
- No se reordenan pestañas: el orden actual ya es correcto.
- Navegación libre entre pestañas: el usuario puede seguir haciendo clic en
  cualquier pestaña en cualquier momento (sin bloqueo secuencial forzado).
- "Guardar datos" no cambia de comportamiento: sigue visible en cualquier
  pestaña mientras haya datos cargados.
- La visibilidad general de `#action-bar` (mostrar/ocultar la barra completa
  según si hay datos) no cambia.

## Diseño

En `static/js/extraccion.js`:

1. En `actualizarUI()` (líneas ~596-603), agregar una línea que fije la
   visibilidad de `#btn-confirmar` según `state.tabActiva`:
   - Visible (`display: ''`, deja que aplique el estilo CSS por defecto del
     botón) cuando `state.tabActiva === 'mayoristas'`.
   - Oculto (`display: 'none'`) en cualquier otro valor de `tabActiva`.

2. En `cambiarTab()` (líneas ~241-257), después de actualizar
   `state.tabActiva` y las clases `active` de tabs/paneles, invocar
   `actualizarUI()` (o extraer la línea de visibilidad de `btn-confirmar` a
   una función pequeña reutilizable, p.ej. `actualizarBotonConfirmar()`, y
   llamarla desde ambos sitios) para que el botón se actualice de inmediato
   al cambiar de pestaña, no solo tras cargar/guardar datos.

No se requiere estado adicional (no hay "pestañas visitadas" ni bloqueo);
basta con leer `state.tabActiva` en el momento de renderizar.

## Pruebas / verificación

- Abrir la página de extracción con datos cargados en ICG, Proalmex, Bimbo y
  Mayoristas.
- Verificar que "Confirmar selección" está oculto en Consolidado, ICG,
  Proalmex y Bimbo, y visible únicamente al estar en la pestaña Mayoristas.
- Verificar que "Guardar datos" sigue visible en todas esas pestañas (sin
  cambios).
- Verificar que cambiar de pestaña actualiza el botón sin necesidad de
  recargar la página o de guardar datos.
