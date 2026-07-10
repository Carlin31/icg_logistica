# Auto-avance de pestaña al guardar datos — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** En la página de Extracción de datos, al presionar "Guardar datos" con éxito, avanzar automáticamente a la siguiente pestaña del flujo Consolidado → ICG → Proalmex → Bimbo → Mayoristas (quedándose en Mayoristas al llegar ahí), y avisar del avance en el mismo toast de éxito.

**Architecture:** Cambio puramente de frontend en `static/js/extraccion.js`. Se añaden dos constantes (`FLOW_ORDER`, `FLOW_LABELS`) y una función `avanzarSiguientePestana()` que reutiliza `cambiarTab()` ya existente. Se modifica el bloque final de `guardarDatos()` para invocar el avance y ajustar el mensaje del toast de éxito. Sin backend, sin build step.

**Tech Stack:** Flask (backend, sin cambios), JS vanilla (sin framework, sin bundler). No existe framework de tests para JS en este repo — la verificación es manual, en navegador.

---

### Task 1: Auto-avanzar de pestaña tras guardar datos exitosamente

**Files:**
- Modify: `static/js/extraccion.js:51` (agregar constantes junto a `PERFILES`)
- Modify: `static/js/extraccion.js:257-258` (agregar función `avanzarSiguientePestana` después de `cambiarTab`)
- Modify: `static/js/extraccion.js:717-724` (bloque final de éxito en `guardarDatos`)

**Contexto necesario:**

Estado actual de `static/js/extraccion.js:51` (línea única):

```js
const PERFILES = ['icg', 'proalmex', 'bimbo'];
```

Estado actual de `static/js/extraccion.js:240-258` (función `cambiarTab`, ya termina en `actualizarBotonConfirmar()` por un cambio previo):

```js
async function cambiarTab(tab) {
  if (_manualModalAbierto) {
    const ok = await mostrarConfirmGen(
      'Salir de la captura manual',
      '¿Está seguro de que desea salir de la pestaña? Los cambios no guardados se perderán.',
      'Salir'
    );
    if (!ok) return;
    _cerrarModalForzado();
  }
  state.tabActiva = tab;
  document.querySelectorAll('.ext-tab').forEach(b =>
    b.classList.toggle('active', b.dataset.tab === tab)
  );
  document.querySelectorAll('.ext-panel').forEach(p =>
    p.classList.toggle('active', p.id === `panel-${tab}`)
  );
  actualizarBotonConfirmar();
}
```

Estado actual de `static/js/extraccion.js:717-724` (bloque final de éxito dentro de `guardarDatos()`, que está envuelto en un `try { ... } catch (err) { ... } finally { ... }` — este bloque es la última sección dentro del `try`, justo antes del `catch` en la línea 726):

```js
    state.hayUnsaved = false;
    actualizarUnsavedIndicator();
    mostrarToast(
      (hayPeso || hayVol || hayMayoristas)
        ? 'Datos guardados correctamente'
        : 'Fuentes eliminadas. Regenera las rutas VRP para actualizar las asignaciones.',
      'ok'
    );

  } catch (err) {
```

`hayPeso`, `hayVol` y `hayMayoristas` son `const` declaradas al inicio de `guardarDatos()` (líneas 621-624) y siguen en alcance en este punto.

`mostrarToast(msg, tipo)` (definida en `static/js/extraccion.js:1152-1170`) reutiliza un único elemento `.ext-toast` del DOM — llamarla dos veces seguidas sobreescribe el mensaje anterior antes de que sea legible, así que el aviso de avance debe ir concatenado en el mismo mensaje, no en una segunda llamada.

- [ ] **Step 1: Añadir `FLOW_ORDER` y `FLOW_LABELS` junto a `PERFILES`**

En `static/js/extraccion.js`, reemplazar la línea 51:

```js
const PERFILES = ['icg', 'proalmex', 'bimbo'];
```

por:

```js
const PERFILES = ['icg', 'proalmex', 'bimbo'];

const FLOW_ORDER  = ['consolidado', 'icg', 'proalmex', 'bimbo', 'mayoristas'];
const FLOW_LABELS = {
  consolidado: 'Consolidado',
  icg:         'ICG',
  proalmex:    'Proalmex',
  bimbo:       'Bimbo',
  mayoristas:  'Mayoristas',
};
```

- [ ] **Step 2: Añadir `avanzarSiguientePestana()` después de `cambiarTab()`**

Inmediatamente después del cierre de `cambiarTab()` (después de la línea `}` que cierra la función, actualmente línea 258), insertar:

```js

async function avanzarSiguientePestana() {
  const idx = FLOW_ORDER.indexOf(state.tabActiva);
  if (idx === -1 || idx === FLOW_ORDER.length - 1) return null;
  const siguiente = FLOW_ORDER[idx + 1];
  await cambiarTab(siguiente);
  return siguiente;
}
```

- [ ] **Step 3: Invocar el avance y ajustar el toast final en `guardarDatos()`**

Reemplazar el bloque (líneas 717-724):

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

por:

```js
    state.hayUnsaved = false;
    actualizarUnsavedIndicator();

    const huboGuardado     = hayPeso || hayVol || hayMayoristas;
    const siguientePestana = huboGuardado ? await avanzarSiguientePestana() : null;

    let mensaje = huboGuardado
      ? 'Datos guardados correctamente'
      : 'Fuentes eliminadas. Regenera las rutas VRP para actualizar las asignaciones.';
    if (siguientePestana) {
      mensaje += `. Avanzando a ${FLOW_LABELS[siguientePestana]}…`;
    }
    mostrarToast(mensaje, 'ok');
```

No tocar nada del `catch`/`finally` que sigue después, ni las líneas anteriores dentro del mismo `try` (guardado de Tiendas Lores/Mayoristas, eliminación de fuentes).

- [ ] **Step 4: Verificación de sintaxis**

Run: `node --check static/js/extraccion.js`
Expected: sin salida (sin errores de sintaxis).

- [ ] **Step 5: Verificación manual en navegador**

No hay framework de tests JS en este repo, así que la verificación es manual:

1. Levantar el servidor:
   ```bash
   python app.py
   ```
2. Abrir `http://127.0.0.1:5000/extraccion/<slug-de-una-logistica-existente>` en el navegador, con datos ya cargados/guardados en ICG, Proalmex, Bimbo y Mayoristas (para que `hayPeso`/`hayVol`/`hayMayoristas` sean verdaderos al guardar).
3. Estando en la pestaña **Consolidado**, click en "Guardar datos": debe guardar y la pestaña activa debe pasar a **ICG**; el toast debe leer "Datos guardados correctamente. Avanzando a ICG…".
4. Estando en **ICG**, click en "Guardar datos": debe avanzar a **Proalmex** con el toast correspondiente.
5. Repetir en **Proalmex** → debe avanzar a **Bimbo**.
6. Repetir en **Bimbo** → debe avanzar a **Mayoristas**.
7. Estando en **Mayoristas**, click en "Guardar datos": debe guardar, permanecer en **Mayoristas** (sin cambiar de pestaña) y el toast debe leer solo "Datos guardados correctamente" (sin "Avanzando a…"). El botón "Confirmar selección" debe seguir visible (por el cambio anterior).
8. Confirmar que el clic manual en cualquier pestaña (Consolidado, ICG, Proalmex, Bimbo, Mayoristas) sigue funcionando sin restricciones en cualquier momento.
9. Vaciar todos los datos (o probar en una logística sin datos) y click en "Guardar datos": debe mostrar el toast de error "No hay datos para guardar." y NO cambiar de pestaña.
10. Abrir las DevTools (F12) y confirmar que no hay errores nuevos en consola durante todo el flujo.

- [ ] **Step 6: Commit**

```bash
git add static/js/extraccion.js
git commit -m "$(cat <<'EOF'
Auto-avanza a la siguiente pestaña al guardar datos en Extracción

Guardar datos ahora avanza automáticamente Consolidado→ICG→Proalmex
→Bimbo→Mayoristas cuando el guardado incluyó datos nuevos, quedándose
en Mayoristas al llegar ahí. La navegación manual por clic se mantiene
disponible en todo momento.
EOF
)"
```

---

## Post-implementación

- Este es un ajuste de UI/flujo sin nueva ruta/endpoint/variable de entorno,
  así que según `CLAUDE.md` no es obligatorio actualizar `README.md`.
