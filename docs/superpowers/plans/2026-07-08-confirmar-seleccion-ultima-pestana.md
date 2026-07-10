# Confirmar selección solo en la última pestaña — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** En la página de Extracción de datos, el botón "Confirmar selección" solo debe mostrarse cuando la pestaña activa sea "Mayoristas" (la última del flujo Consolidado → ICG → Proalmex → Bimbo → Mayoristas), no en las demás pestañas.

**Architecture:** Cambio puramente de frontend en `static/js/extraccion.js`. Se añade una función pequeña `actualizarBotonConfirmar()` que fija `display` del botón `#btn-confirmar` según `state.tabActiva`, y se invoca tanto desde `actualizarUI()` (ya se llama tras cargar/guardar datos) como desde `cambiarTab()` (al cambiar de pestaña). No hay backend ni build step involucrados.

**Tech Stack:** Flask (backend, sin cambios), Jinja2 templates, JS vanilla (sin framework, sin bundler), CSS plano. No existe framework de tests para JS en este repo — la verificación de este cambio es manual, en navegador.

---

### Task 1: Ocultar/mostrar "Confirmar selección" según la pestaña activa

**Files:**
- Modify: `static/js/extraccion.js:596-603` (función `actualizarUI`)
- Modify: `static/js/extraccion.js:240-257` (función `cambiarTab`)

**Contexto necesario:**

Estado actual de `static/js/extraccion.js:596-607`:

```js
function actualizarUI() {
  const hayLores = PERFILES.some(
    p => state.perfiles[p].datos !== null || state.perfiles[p].volumen !== null
  );
  const hayMayoristas = state.mayoristas.consolidado !== null;
  document.getElementById('action-bar').style.display = (hayLores || hayMayoristas) ? 'flex' : 'none';
  actualizarUnsavedIndicator();
}

function actualizarUnsavedIndicator() {
  document.getElementById('unsaved-indicator').style.display = state.hayUnsaved ? 'inline' : 'none';
}
```

Estado actual de `static/js/extraccion.js:240-257`:

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
}
```

El botón vive en `templates/extraccion/index.html:394-411`:

```html
<div class="ext-action-bar" id="action-bar" style="display:none;">
  ...
  <button id="btn-guardar" class="ext-btn-secondary" onclick="guardarDatos()">
    <i data-lucide="save"></i> Guardar datos
  </button>
  <button id="btn-confirmar" class="ext-btn-confirmar" onclick="confirmarSeleccion()">
    Confirmar selección <i data-lucide="arrow-right"></i>
  </button>
</div>
```

`#btn-confirmar` no tiene `display` propio en CSS (`static/css/extraccion.css:538-551`), así que su valor por defecto de navegador es `inline-block`.

- [ ] **Step 1: Añadir la función `actualizarBotonConfirmar()` justo antes de `actualizarUI()`**

En `static/js/extraccion.js`, inmediatamente antes de la línea `function actualizarUI() {` (línea 596), insertar:

```js
function actualizarBotonConfirmar() {
  document.getElementById('btn-confirmar').style.display =
    state.tabActiva === 'mayoristas' ? '' : 'none';
}
```

- [ ] **Step 2: Llamar a `actualizarBotonConfirmar()` desde `actualizarUI()`**

Modificar `actualizarUI()` para que quede así (se agrega la última línea antes del cierre):

```js
function actualizarUI() {
  const hayLores = PERFILES.some(
    p => state.perfiles[p].datos !== null || state.perfiles[p].volumen !== null
  );
  const hayMayoristas = state.mayoristas.consolidado !== null;
  document.getElementById('action-bar').style.display = (hayLores || hayMayoristas) ? 'flex' : 'none';
  actualizarUnsavedIndicator();
  actualizarBotonConfirmar();
}
```

- [ ] **Step 3: Llamar a `actualizarBotonConfirmar()` desde `cambiarTab()`**

Modificar `cambiarTab()` para que, tras fijar `state.tabActiva` y actualizar las clases de tabs/paneles, también actualice el botón:

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

- [ ] **Step 4: Verificación manual en navegador**

No hay framework de tests JS en este repo, así que la verificación es manual:

1. Levantar el servidor:
   ```bash
   python app.py
   ```
2. Abrir `http://127.0.0.1:5000/extraccion/<slug-de-una-logistica-existente>` en el navegador (usar el mismo slug que se ve en la captura del usuario, p.ej. `logistica-del-15-al-19-de-junio-del-2026`).
3. Cargar o confirmar que ya hay datos guardados en ICG, Proalmex, Bimbo y Mayoristas (para que `#action-bar` esté visible).
4. Click en cada pestaña en orden (Consolidado, ICG, Proalmex, Bimbo, Mayoristas) y confirmar:
   - "Guardar datos" se ve en todas las pestañas donde ya se veía antes (sin cambio de comportamiento).
   - "Confirmar selección" está **oculto** en Consolidado, ICG, Proalmex y Bimbo.
   - "Confirmar selección" está **visible** únicamente en Mayoristas.
5. Con "Confirmar selección" visible en Mayoristas, click en el botón y confirmar que `confirmarSeleccion()` sigue funcionando igual que antes (valida datos, guarda si hace falta, redirige a `/asignacion/<slug>`).
6. Abrir las DevTools (F12) y confirmar que no hay errores nuevos en consola al cambiar de pestaña.

- [ ] **Step 5: Commit**

```bash
git add static/js/extraccion.js
git commit -m "$(cat <<'EOF'
Muestra Confirmar selección solo en la pestaña Mayoristas

El botón se ocultaba junto con Guardar datos según si había datos
cargados, sin importar la pestaña activa, permitiendo confirmar
antes de revisar todos los proveedores. Ahora depende también de
state.tabActiva.
EOF
)"
```

---

## Post-implementación

- Actualizar `README.md` solo si el proyecto lo requiere para cambios de flujo funcional visible; este es un ajuste de UI menor sin nueva ruta/endpoint/variable de entorno, así que según `CLAUDE.md` no es obligatorio actualizarlo. Confirmar con el usuario si prefiere documentarlo de todos modos.
