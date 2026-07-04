# Auditoría UX/Accesibilidad — ICG Logistics
**Fecha:** Junio 2026 · **Versión analizada:** app_9.1.0  
**Alcance:** `static/css/` (12 archivos), `static/js/` (11 archivos), `templates/` (base + 10 módulos)  
**Estándar de accesibilidad:** WCAG 2.1 AA

---

## 1. Resumen Ejecutivo

Se identificaron **5 categorías de problemas de alto impacto** que afectan mantenibilidad, consistencia visual y accesibilidad:

| # | Problema | Severidad | Impacto |
|---|----------|-----------|---------|
| A | `body { zoom: 0.8 }` global — propiedad no estándar, rompe rem | **Alta** | Todos los usuarios, accesibilidad |
| B | Sistema de tokens desconectado en `menu.css` (`--header-h: 64px` vs 52px) | **Alta** | Inconsistencia visual header |
| C | Componentes duplicados: `.btn`, `.modal-overlay`, `.data-table` en 4-6 archivos | **Alta** | Mantenibilidad, deriva visual |
| D | Ausencia total de `:focus-visible` y `prefers-reduced-motion` | **Alta** | WCAG 2.1 AA, usuarios con teclado/motor |
| E | Modales sin `role="dialog"`, `aria-modal`, ni focus trap | **Alta** | Lectores de pantalla, navegación por teclado |

Adicionalmente se documentan problemas de severidad media y baja por módulo en §3.

---

## 2. Contexto de arquitectura

```
Flask SSR (Jinja2)
├─ base.html          → plantilla maestra (todos los módulos salvo menu y conductor)
├─ static/css/base.css → tokens + layout compartido
└─ static/css/{módulo}.css → estilos por página (cargados vía {% block extra_css %})

Módulos standalone (no extienden base.html):
├─ menu/index.html    → layout propio + menu.css (redefine :root)
├─ conductor/index.html    → conductor.css (redefine html,body; tokens --cd-*)
└─ conductor/desktop.html  → conductor_desktop.css (redefine html,body; tokens --cdd-*)
```

El sistema de tokens de `base.css` es **la fuente de verdad intencional**, pero está parcialmente saboteado por redefiniciones locales. La arquitectura CSS sigue un patrón "un archivo por módulo" que es correcto para este tipo de app; el problema es la duplicación de componentes compartidos dentro de esos archivos modulares.

---

## 3. Hallazgos por módulo

### 3.1 `base.css` + `base.html`

**Propósito:** Tokens de diseño, header, nav panel, fullscreen loader, footer.

**Problemas:**

**[B-1] `body { zoom: 0.8 }` — Anti-patrón de accesibilidad**  
Archivo: `static/css/base.css:7`

```css
/* Estado actual: */
body { zoom: 0.8; }
html { font-size: clamp(10.5px, 0.32vw + 8px, 11.6px); }
```

`zoom` es una propiedad no estándar (no en la spec CSS). Sus efectos:
- Interfiere con el zoom del navegador (Ctrl++ / Cmd++) que usuarios con baja visión utilizan
- Rompe la semántica de `rem`: aunque el `font-size` en `html` escale vía `clamp()`, el `zoom: 0.8` achica todo uniformemente por encima, haciendo que `1rem` se comporte como `~0.7rem` en pantalla
- Cada página standalone lo sobreescribe de forma diferente: `menu.css` → `zoom: 0.95`, `auth.css` → `zoom: 1`, `conductor.css` → reset completo de `html, body`
- El control de zoom del usuario (base.js + servidor) ya provee el mecanismo correcto; el `zoom: 0.8` inicial es redundante y problemático

**Solución:** Eliminar `body { zoom: 0.8 }`. El escalado vía `clamp()` en `html` ya existe y es el mecanismo correcto.

---

**[B-2] Skip link ausente**  
Archivo: `templates/base.html` (falta antes de `<header>`)

Usuarios de teclado deben tabular por todos los ítems del header antes de llegar al contenido. Un skip link resuelve esto.

```html
<!-- Agregar como primer elemento del <body> -->
<a href="#contenido" class="skip-link">Saltar al contenido principal</a>
```

```css
.skip-link {
  position: absolute;
  top: -100%;
  left: 0;
  padding: 8px 16px;
  background: var(--azul);
  color: white;
  z-index: 9999;
  font-weight: 600;
}
.skip-link:focus { top: 0; }
```

---

**[B-3] `:focus-visible` ausente en todo el sistema**  
Muchos inputs y controles tienen `outline: none` sin un reemplazo equivalente para navegación por teclado:

```css
/* Patrón repetido en usuarios.css, configuracion.css, asignacion.css: */
.form-control { outline: none; }
.input-search  { outline: none; }
/* El :focus agrega box-shadow — válido para ratón, pero no distingue 
   clic vs teclado. El estándar moderno es :focus-visible */
```

**Solución global en `base.css`:**

```css
/* Añadir al final de base.css */
:focus-visible {
  outline: 2px solid var(--azul);
  outline-offset: 2px;
}
/* Solo para inputs (el box-shadow ya es suficiente señal adicional): */
.form-control:focus-visible,
.input-search:focus-visible {
  outline: 2px solid var(--azul);
  outline-offset: 0;
}
```

---

**[B-4] `prefers-reduced-motion` ausente**  
Hay animaciones en: fullscreen loader (3 capas), `.fl-dot` pulsante, spinners en múltiples módulos, transiciones de tarjetas. Ninguna respeta la preferencia del usuario.

```css
/* Añadir al final de base.css */
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
  }
}
```

---

### 3.2 `menu.css` + `menu/index.html`

**Propósito:** Pantalla de bienvenida/selección de perfil. Standalone (no extiende `base.html`).

**Problemas:**

**[M-1] Redefinición de tokens con valores distintos**  
Archivo: `static/css/menu.css` (primeras ~50 líneas)

```css
/* menu.css redefine :root con valores que contradicen base.css: */
:root {
  --header-h: 64px;  /* base.css tiene 52px — ¡12px de diferencia! */
  --sombra-sm: 0 1px 3px rgba(0,0,0,.10);  /* base.css: rgba(0,0,0,.08) */
  /* ... más redefiniciones ... */
}
body { zoom: 0.95; }  /* distinto del 0.8 de base.css */
```

Si algún módulo importa `base.css` y luego `menu.css`, el token `--header-h` será 64px, rompiendo el layout del header calculado con `calc(100vh - var(--header-h))`.

**Severidad:** Alta si menu.css se carga junto a base.css en algún flujo futuro. Actualmente es standalone, pero el patrón es un pasivo de mantenibilidad.

**Solución:** `menu.css` debe importar los tokens de `base.css` y sobreescribir SOLO lo que necesite con clases específicas (`.menu-header`), no redefiniendo `:root`.

---

**[M-2] Fullscreen loader duplicado**  
El HTML y CSS del `.fl-overlay` están definidos en `base.html` + `base.css` Y replicados en `menu.css` + `menu/index.html`. Si cambia el loader principal, menu queda desactualizado.

---

### 3.3 `asignacion.css` (2114 líneas — módulo más grande)

**Propósito:** Vista de tabla y kanban para asignación de rutas. Incluye modal de mapa, panel VRP, tarjetas de ruta con semáforos de estado.

**Problemas:**

**[A-1] Sistema de color para días duplicado internamente**

```css
/* Dentro de asignacion.css hay DOS sistemas de badges para los mismos días: */
.sel-group-badge--lunes   { background: #dbeafe; color: #1d4ed8; }  /* versión 1 */
.sel-day-badge--lunes     { background: #eff6ff; color: #1e40af; }  /* versión 2 */
/* Mismo día, colores ligeramente distintos — inconsistente visualmente */
```

Existen 7 pares (uno por día) con valores hex divergentes. Un solo sistema unificado en `base.css` como tokens nombrados eliminaría la divergencia.

**[A-2] `.btn` definido con valores hardcoded**

```css
/* asignacion.css: */
.btn { padding: 7px 14px; border-radius: 6px; font-size: 0.85rem; }

/* configuracion.css (mismo selector, valores distintos): */
.btn { padding: 8px 14px; border-radius: var(--radio-btn); font-size: var(--text-base); }
```

El `border-radius: 6px` hardcoded en asignacion ignora el token `--radio-btn`. Whichever file loads last wins.

**[A-3] Tooltip CSS-only inaccesible**

```css
[data-tooltip]::after {
  content: attr(data-tooltip);
  /* Solo se muestra en :hover */
}
/* Sin :focus-visible — los usuarios de teclado no ven el tooltip */
/* Screen readers no leen el ::after content */
```

**Solución:** Reemplazar por `title` attribute (nativo, accesible) o por tooltips con `role="tooltip"` + `aria-describedby`.

**[A-4] Tarjetas de ruta como `<div>` no interactivos**  
Las `.sel-route-card` tienen `cursor: pointer` y listener de click en JS, pero probablemente son `<div>`, no `<button>`. Usuarios de teclado no pueden tabular ni activar con Enter.

**Solución:** Usar `<button class="sel-route-card">` o agregar `tabindex="0"` + listener `keydown` con Enter/Space.

---

### 3.4 `configuracion.css` (1053 líneas)

**Propósito:** Tabs de configuración, gestión de perfiles de logística, vehículos, mayoristas.

**Problemas:**

**[C-1] `.btn`, `.modal-overlay`, `.modal-box`, `.form-group`, `.form-control`, `.switch`, `.slider` todos redefinidos**  
Este módulo tiene el conjunto más completo de componentes compartidos pero en su propia copia. Cualquier ajuste global al sistema de botones requiere cambiar N archivos.

**[C-2] Toggle switch duplicado con `asignacion.css`**

```css
/* Aparece idénticamente en configuracion.css Y asignacion.css: */
.switch { position: relative; display: inline-block; width: 40px; height: 22px; }
.slider { position: absolute; cursor: pointer; inset: 0; background: #ccc; border-radius: 22px; }
```

---

### 3.5 `modificacion.css` (1573 líneas)

**Propósito:** Editor de rutas con drag-and-drop de paradas y mapa Leaflet en split-panel.

**Problemas:**

**[Mo-1] `.btn` con `font-size: 0.78rem`**  
El tercer valor distinto para el mismo componente:

```css
/* modificacion.css: */
.btn { border-radius: 6px; font-size: 0.78rem; }
/* vs asignacion: 0.85rem, vs configuracion: var(--text-base) ≈ 0.875rem */
```

**[Mo-2] Valores con fallbacks defensivos innecesarios**

```css
color: var(--texto-sub, #6b7280);  /* La coma-fallback sugiere desconfianza en que base.css carga primero */
```

Si base.css no carga, la UI está rota de todas formas. Los fallbacks en custom properties solo añaden ruido.

**[Mo-3] Modal de confirmación como `.confirm-box` (distinto nombre que en otros módulos)**  
`asignacion.css` y `usuarios.css` usan `.modal-box`. `modificacion.css` usa `.confirm-box`. Ambos son modales de confirmación, pero clases distintas impiden compartir CSS.

---

### 3.6 `extraccion.css` (916 líneas)

**Propósito:** Carga de archivos Excel, validación, previsualización de datos.

**Puntos positivos:**
- El único módulo que **no redefine `.btn`** — usa clases propias (`ext-btn-upload`, `ext-btn-action`, `ext-btn-confirmar`)
- Patrón más limpio: prefijo `ext-` evita conflictos con CSS de otros módulos
- Buen uso de tokens del sistema

**Problemas menores:**
- Mezcla de `var(--azul)` y `#3b82f6` hardcoded para el mismo azul
- Algunos `#0f172a`, `#334155` sin usar el token `--texto-oscuro` equivalente

---

### 3.7 `conductor.css` + `conductor_desktop.css`

**Propósito:** Portal del conductor. Dos variantes: mobile-first (field use) y desktop (3 columnas con mapa central).

**Arquitectura:**
- La decisión de tener dos plantillas/CSS separadas es **justificada**: la experiencia mobile en campo y la supervisión en escritorio son flujos distintos
- El prefijado `--cd-*` / `--cdd-*` evita colisiones de tokens — patrón correcto para componentes standalone

**Problemas:**

**[Cd-1] Tokens duplicados respecto a `base.css`**

```css
/* conductor.css: */
--cd-azul: #2563eb;    /* = var(--azul) en base.css */
--cd-verde: #16a34a;   /* = var(--verde) en base.css */
--cd-rojo: #ef4444;    /* base.css usa #dc2626 — ¡valores distintos! */
```

El rojo del conductor (`#ef4444`) difiere del rojo del sistema (`#dc2626`). Si los estados de cancelación o error necesitan ser consistentes entre la vista del conductor y la del administrador, estos colores divergentes son problemáticos.

**[Cd-2] `conductor.css` y `conductor_desktop.css` son ~70% código paralelo**  
Los componentes `.cd-parada`, `.cd-confirm-box`, `.cd-toast`, `.cd-progreso-bar-wrap`, y todos los estados de parada (entregada, cancelada) están duplicados con prefijo `cd-` en uno y `cdd-` en otro. Cualquier cambio de comportamiento en paradas requiere editar los dos archivos.

**Posible mejora:** Extraer un `conductor-shared.css` con los componentes comunes y diferencias mínimas en los dos archivos específicos.

---

### 3.8 `seguimiento.css` (386 líneas)

**Propósito:** Vista de auditoría y seguimiento de rutas autorizadas.

**Problemas:**

**[S-1] `max-width: 1420px` vs contenedor base de `1200px`**  

```css
/* seguimiento.css: */
.seg-container { max-width: 1420px; margin: 0 auto; }

/* base.css (presumiblemente): */
#contenido { max-width: 1200px; }
```

Si el `#contenido` tiene `max-width: 1200px` en base.css, el `.seg-container` nunca alcanza sus 1420px — la declaración es un no-op confuso. Si se eliminó el max-width del contenedor base, entonces seguimiento sí desborda el resto de los módulos.

**[S-2] `.data-table`, `.input-search`, `.table-responsive` redefinidos**  
Tercera copia de estos componentes (también en `configuracion.css` y `usuarios.css`).

---

### 3.9 `usuarios.css` (87 líneas)

**Propósito:** Gestión de usuarios administrativos.

**Puntos positivos:**
- El archivo más pequeño y ordenado de los módulos con admin
- Usa tokens CSS correctamente en todo momento

**Problemas:**
- Contiene la definición más completa de `.btn` (con todas las variantes de color), pero al ser el último en cargarse puede sobreescribir las definiciones de asignacion/configuracion en páginas donde ambos CSS se cargan (aunque este caso no parece ocurrir actualmente)

---

### 3.10 `auth.css` (177 líneas)

**Propósito:** Login y selector de tipo de usuario.

**Puntos positivos:**
- `zoom: 1` cancela correctamente el `body { zoom: 0.8 }` de base.css para la pantalla de login
- Buen uso de tokens
- Animación de entrada (`auth-fade-up`) es la única con efecto moderado y propósito claro

---

### 3.11 `pdf.css` (281 líneas)

**Propósito:** Generación de PDF de rutas y autorización de logísticas.

**Puntos positivos:**
- Uso consistente de tokens CSS
- Módulo bien dimensionado para su alcance

**Problemas:**
- `.modal-overlay`, `.modal-box`, `.modal-footer` redefinidos (quinta instancia)
- `.btn-primary`, `.btn-secondary`, `.btn-autorizar`, `.btn-cancelar-autorizacion` — cuatro variantes de botón en lugar de usar el sistema global `.btn`

---

## 4. Propuesta de sistema de diseño base

### 4.1 Tokens a consolidar en `base.css`

Los siguientes tokens faltan o deben normalizarse:

```css
:root {
  /* ── Colores semánticos adicionales (faltan hoy) ── */
  --color-error:       #dc2626;   /* alinear con conductor que usa #ef4444 */
  --color-error-bg:    #fef2f2;
  --color-error-borde: #fecaca;
  --color-exito:       #16a34a;
  --color-exito-bg:    #f0fdf4;
  --color-aviso:       #f59e0b;
  --color-aviso-bg:    #fffbeb;

  /* ── Colores de días (semana laboral) — centralizar aquí ── */
  --dia-lunes-bg:      #dbeafe;  --dia-lunes-txt: #1d4ed8;
  --dia-martes-bg:     #fce7f3;  --dia-martes-txt: #be185d;
  --dia-miercoles-bg:  #d1fae5;  --dia-miercoles-txt: #065f46;
  --dia-jueves-bg:     #fef3c7;  --dia-jueves-txt: #92400e;
  --dia-viernes-bg:    #ede9fe;  --dia-viernes-txt: #5b21b6;
  --dia-sabado-bg:     #ffedd5;  --dia-sabado-txt: #9a3412;

  /* ── Espaciado ── */
  --sp-1: 4px;  --sp-2: 8px;  --sp-3: 12px;  --sp-4: 16px;
  --sp-5: 20px; --sp-6: 24px; --sp-8: 32px;

  /* ── Transiciones ── */
  --t-fast: 0.15s ease;
  --t-base: 0.2s ease;
}
```

### 4.2 Archivo `shared.css` — componentes que deben dejar de duplicarse

Se propone crear `static/css/shared.css` e importarlo después de `base.css` en `base.html`:

```html
<link rel="stylesheet" href="{{ url_for('static', filename='css/base.css') }}">
<link rel="stylesheet" href="{{ url_for('static', filename='css/shared.css') }}">
```

Contenido de `shared.css`:

```css
/* ── Botones ── */
.btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 16px;
  border: none;
  border-radius: var(--radio-btn);
  font-size: var(--text-base);
  font-weight: 600;
  cursor: pointer;
  transition: background var(--t-fast), opacity var(--t-fast);
  line-height: 1;
}
.btn:disabled { opacity: .6; cursor: not-allowed; }
.btn svg, .btn i { width: 15px; height: 15px; flex-shrink: 0; }
.btn-sm { padding: 5px 11px; font-size: var(--text-sm); }

.btn-primary   { background: var(--azul);     color: #fff; }
.btn-secondary { background: var(--gris-icono); color: #fff; }
.btn-success   { background: var(--verde);    color: #fff; }
.btn-danger    { background: var(--rojo);     color: #fff; }
.btn-outline   { background: transparent; border: 1.5px solid var(--azul); color: var(--azul); }

.btn-primary:hover:not(:disabled)   { background: var(--azul-dark); }
.btn-success:hover:not(:disabled)   { background: #15803d; }
.btn-danger:hover:not(:disabled)    { background: #b91c1c; }
.btn-outline:hover:not(:disabled)   { background: var(--azul); color: #fff; }
.btn-secondary:hover:not(:disabled) { background: #4b5563; }

/* ── Modal ── */
.modal-overlay {
  position: fixed; inset: 0;
  background: rgba(0,0,0,.45);
  display: flex; align-items: center; justify-content: center;
  z-index: 1000;
}
.modal-overlay.hidden { display: none; }
.modal-box {
  background: var(--blanco);
  border-radius: var(--radio-sm);
  padding: 28px;
  width: 500px;
  max-width: 92vw;
  max-height: 88vh;
  overflow-y: auto;
  box-shadow: var(--sombra-xl);
}
.modal-box h3 { margin: 0 0 .8rem; font-size: 1rem; }
.modal-footer { display: flex; justify-content: flex-end; gap: 10px; margin-top: 18px; }

/* ── Formulario ── */
.form-group { margin-bottom: 14px; }
.form-group label { display: block; font-size: var(--text-sm); color: var(--gris-texto); margin-bottom: 4px; font-weight: 500; }
.form-control {
  width: 100%; padding: 8px 10px;
  border: 1px solid var(--gris-borde); border-radius: var(--radio-btn);
  font-size: var(--text-base); font-family: inherit;
  outline: none;
  transition: border-color var(--t-fast), box-shadow var(--t-fast);
}
.form-control:focus { border-color: var(--azul); box-shadow: 0 0 0 3px rgba(37,99,235,.12); }

/* ── Tabla de datos ── */
.table-responsive { width: 100%; overflow-x: auto; }
.data-table { width: 100%; border-collapse: collapse; font-size: var(--text-base); }
.data-table th,
.data-table td { padding: 9px 12px; border: 1px solid var(--gris-borde); text-align: left; }
.data-table thead th {
  background: var(--gris-bg-alt); font-weight: 700;
  color: var(--gris-texto); font-size: var(--text-xs);
  text-transform: uppercase; letter-spacing: .04em;
}
.data-table tbody tr:nth-child(even) { background: var(--gris-bg-alt); }
.data-table tbody tr:hover { background: var(--azul-bg); }

/* ── Búsqueda ── */
.input-search {
  max-width: 340px; width: 100%; padding: 7px 12px;
  border: 1px solid var(--gris-borde); border-radius: var(--radio-btn);
  font-size: var(--text-base); outline: none;
  transition: border-color var(--t-fast);
}
.input-search:focus { border-color: var(--azul); box-shadow: 0 0 0 3px rgba(37,99,235,.1); }

/* ── Toggle switch ── */
.switch { position: relative; display: inline-block; width: 40px; height: 22px; }
.switch input { opacity: 0; width: 0; height: 0; }
.slider {
  position: absolute; cursor: pointer; inset: 0;
  background: #ccc; border-radius: 22px;
  transition: background var(--t-fast);
}
.slider::before {
  content: ''; position: absolute;
  width: 16px; height: 16px; left: 3px; bottom: 3px;
  background: white; border-radius: 50%;
  transition: transform var(--t-fast);
}
.switch input:checked + .slider { background: var(--azul); }
.switch input:checked + .slider::before { transform: translateX(18px); }

/* ── Toast global ── */
.toast-container {
  position: fixed; bottom: 20px; right: 20px; z-index: 2000;
  display: flex; flex-direction: column; gap: 8px;
}
.toast {
  padding: 11px 18px; border-radius: var(--radio-sm);
  color: #fff; font-size: var(--text-base); font-weight: 600;
  box-shadow: var(--sombra-lg); cursor: pointer;
  animation: toast-in .2s ease;
}
.toast.ok    { background: var(--verde); }
.toast.error { background: var(--rojo); }
.toast.aviso { background: var(--acento); color: #1c1917; }
@keyframes toast-in { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }

/* ── Accesibilidad ── */
.skip-link {
  position: absolute; top: -100%; left: 0;
  padding: 8px 16px; background: var(--azul); color: #fff;
  font-weight: 600; font-size: var(--text-base); z-index: 9999;
  text-decoration: none;
}
.skip-link:focus { top: 0; }

:focus-visible {
  outline: 2px solid var(--azul);
  outline-offset: 2px;
  border-radius: 2px;
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
  }
}
```

### 4.3 Escala tipográfica (conservar y documetar)

Los tokens de tipografía ya están bien definidos en `base.css`. El problema es que `zoom: 0.8` sobre el `clamp()` los hace impredecibles. Eliminando el zoom, los tamaños quedan:

| Token | Valor rem | Píxeles aproximados |
|-------|-----------|---------------------|
| `--text-xs`   | 0.72rem | ~8.5px (muy pequeño — revisar uso en labels) |
| `--text-sm`   | 0.82rem | ~9.7px |
| `--text-base` | 0.875rem | ~10.3px |
| `--text-md`   | 0.95rem | ~11.2px |
| `--text-lg`   | 1rem | ~11.8px |

Con el `clamp()` en `html` ajustando entre 10.5px y 11.6px de base, estos valores resultan en una tipografía muy compacta. Al eliminar el `zoom: 0.8`, se recomienda ajustar el `clamp()` ligeramente para compensar:

```css
/* Propuesta: de clamp(10.5px, 0.32vw + 8px, 11.6px) a: */
html { font-size: clamp(13px, 0.9vw + 10px, 15px); }
/* Esto daría --text-base = ~0.875rem × 14px ≈ 12.3px — más legible */
```

---

## 5. Checklist de accesibilidad WCAG 2.1 AA

| Criterio | Descripción | Estado | Severidad | Acción |
|----------|-------------|--------|-----------|--------|
| 1.1.1 | Alt text en imágenes | ✅ Parcial | — | Los `<img>` de íconos tienen `alt=""` correcto |
| 1.3.1 | Información y relaciones semánticas | ⚠️ Parcial | Media | Modales sin `role="dialog"` |
| 1.3.3 | Instrucciones sin referencia solo sensorial | ✅ OK | — | — |
| 1.4.1 | Uso del color | ⚠️ Parcial | Media | Semáforos de ruta usan solo color (inset box-shadow) sin ícono/texto alternativo |
| 1.4.3 | Contraste mínimo (4.5:1 texto) | ✅ Corregido | — | `.header-brand-sub` → `rgba(255,255,255,.72)` (~6.8:1) |
| 1.4.4 | Redimensionar texto (200%) | ❌ Falla | Alta | `body { zoom: 0.8 }` interfiere con zoom del navegador |
| 1.4.10 | Reflow (320px) | ⚠️ Parcial | Media | Modificación (split-panel) no reapila en 320px |
| 1.4.11 | Contraste de componentes UI (3:1) | ⚠️ No verificado | Media | Revisar bordes de inputs en estado normal |
| 1.4.13 | Contenido en hover/focus | ✅ Parcial corregido | — | Tooltips: `:focus-visible` agregado; falta `role="tooltip"` + `aria-describedby` |
| 2.1.1 | Teclado | ❌ Falla | Alta | Tarjetas de ruta (divs clickables), drag-drop |
| 2.1.2 | Sin trampa de teclado | ❌ Falla | Alta | Modales sin focus trap |
| 2.4.3 | Orden del foco | ⚠️ Parcial | Media | Nav panel al abrirse no captura foco |
| 2.4.7 | Foco visible | ✅ Corregido | — | `:focus-visible` global en `shared.css` |
| 2.4.11 | Apariencia del foco (AA 2.2) | ✅ Corregido | — | `:focus-visible` con `outline: 2px solid var(--azul)` |
| 3.2.1 | Al recibir foco | ✅ OK | — | — |
| 4.1.2 | Nombre, función, valor (ARIA) | ⚠️ Parcial | Alta | Modales sin `aria-labelledby`, `aria-modal` |
| 4.1.3 | Mensajes de estado | ✅ Parcial | — | `role="status"` en loader; faltan en toasts |
| Skip link | Saltar al contenido principal | ✅ Implementado | — | `<a class="skip-link">` en `base.html` |
| Movimiento reducido | `prefers-reduced-motion` | ✅ Implementado | — | Media query global en `shared.css` |

**Fallas críticas pendientes (bloquean cumplimiento AA):**
1. Modales sin focus trap ni ARIA de dialog
2. `body { zoom: 0.8 }` aún interfiere con zoom del navegador (requiere coordinar JS + clamp)
3. Tarjetas de ruta como `<div>` no accesibles por teclado

---

## 6. Plan de implementación priorizado

### Fase A — Quick wins (≤ 2h, sin riesgo de regresión)

| # | Acción | Archivo(s) | Tiempo est. |
|---|--------|-----------|-------------|
| A1 | Crear `static/css/shared.css` con `:focus-visible` y `prefers-reduced-motion` | Nuevo archivo | 30 min |
| A2 | Agregar `<link>` a `shared.css` en `base.html` | `templates/base.html` | 5 min |
| A3 | Agregar skip link en `base.html` | `templates/base.html` | 10 min |
| A4 | Eliminar `body { zoom: 0.8 }` de `base.css` | `static/css/base.css` | 5 min |
| A5 | Ajustar `clamp()` de `html` para compensar (test visual requerido) | `static/css/base.css` | 20 min |
| A6 | Corregir contraste de `.header-brand-sub` | `static/css/base.css` | 5 min |
| A7 | Agregar `[data-tooltip]:focus-visible::after` para accesibilidad de tooltips | `static/css/asignacion.css` | 10 min |
| A8 | Añadir `role="tooltip"` a contenidos de tooltips en el HTML | Templates de asignacion | 15 min |

### Fase B — Consolidación CSS (4-8h, impacto en todos los módulos)

| # | Acción | Descripción |
|---|--------|-------------|
| B1 | Mover `.btn` y variantes a `shared.css` | Eliminar de asignacion, configuracion, modificacion, usuarios, pdf. Requiere revisar que clases de hover/disabled sean consistentes. |
| B2 | Mover `.modal-overlay/.modal-box/.modal-footer` a `shared.css` | Unificar ancho (500px), radius, padding. Revisar casos donde el modal es más pequeño (confirmar acción vs formulario grande). |
| B3 | Mover `.data-table`, `.input-search`, `.table-responsive` a `shared.css` | Eliminar de configuracion, seguimiento, usuarios. |
| B4 | Mover `.switch/.slider` a `shared.css` | Eliminar de asignacion y configuracion. |
| B5 | Unificar sistema de toasts | Proponer un `.toast-container`/`.toast` global. Adaptar JS de cada módulo para usar clases comunes. |
| B6 | Unificar sistema de colores de días | Un solo set `--dia-{dia}-bg/txt` en `base.css`. Eliminar duplicación dentro de `asignacion.css`. |

### Fase C — Accesibilidad estructural (4-6h, cambios en HTML + JS)

| # | Acción | Descripción |
|---|--------|-------------|
| C1 ✅ | Focus trap en modales | `trapFocus()` + MutationObserver automático en `base.js`. Activa en cualquier `[role="dialog"]` que deja de ser `display:none`. |
| C2 ✅ | ARIA en modales | `role="dialog"`, `aria-modal="true"`, `aria-labelledby` agregados a los 15 modales de todos los módulos. |
| C3 ✅ | Foco al abrir nav panel | En `base.html` JS: `openNav()` foca primer elemento del panel; `closeNav()` devuelve foco al toggle. |
| C4 ✅ | Tarjetas de ruta como botones | `<div class="sel-route-card">` → `<button type="button" class="sel-route-card">` en `asignacion.js`. CSS: `display:block; width:100%; text-align:left; font:inherit`. |
| C5 ✅ | Semáforo de rutas | Ya cumplido: cada `badge-indicador` incluye ícono (check/x/triangle-alert) + texto ("Dentro del rango"/"Subutilizado"/"Sobrecargado"). El color es suplementario. |

### Fase D — Refactorización de módulos standalone (8-12h)

| # | Acción | Descripción |
|---|--------|-------------|
| D1 | Alinear `menu.css` con tokens de `base.css` | Eliminar la redefinición de `:root` en `menu.css`. Importar base.css. Usar `--header-h` correcto. |
| D2 | Conductor shared styles | Extraer `conductor-shared.css` con componentes comunes entre `conductor.css` y `conductor_desktop.css`. |
| D3 | Alinear color de error conductor | `--cd-rojo/#ef4444` → `--rojo/#dc2626` o decidir el valor canónico del sistema y propagarlo. |
| D4 | Ajustar `seguimiento.css` max-width | Verificar comportamiento real de `.seg-container { max-width: 1420px }` vs el contenedor padre. |

---

## 7. Patrones de referencia

### Patrón de modal accesible

```html
<!-- Template -->
<div id="modal-editar" class="modal-overlay hidden"
     role="dialog" aria-modal="true" aria-labelledby="modal-editar-titulo">
  <div class="modal-box">
    <h3 id="modal-editar-titulo">Editar perfil</h3>
    <!-- contenido -->
    <div class="modal-footer">
      <button class="btn btn-secondary" data-close-modal>Cancelar</button>
      <button class="btn btn-primary" id="btn-guardar">Guardar</button>
    </div>
  </div>
</div>
```

```js
// base.js o shared.js
function abrirModal(modalEl) {
  modalEl.classList.remove('hidden');
  const focusables = modalEl.querySelectorAll(
    'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
  );
  const primero = focusables[0];
  const ultimo = focusables[focusables.length - 1];
  primero?.focus();

  function trapFocus(e) {
    if (e.key !== 'Tab') return;
    if (e.shiftKey && document.activeElement === primero) {
      e.preventDefault(); ultimo?.focus();
    } else if (!e.shiftKey && document.activeElement === ultimo) {
      e.preventDefault(); primero?.focus();
    }
  }
  function cerrarConEsc(e) { if (e.key === 'Escape') cerrarModal(modalEl); }
  modalEl.addEventListener('keydown', trapFocus);
  modalEl.addEventListener('keydown', cerrarConEsc);
  modalEl._cleanup = () => {
    modalEl.removeEventListener('keydown', trapFocus);
    modalEl.removeEventListener('keydown', cerrarConEsc);
  };
}

function cerrarModal(modalEl) {
  modalEl.classList.add('hidden');
  modalEl._cleanup?.();
}
```

### Patrón de botón interactivo desde div

```html
<!-- Antes (inaccesible): -->
<div class="sel-route-card" onclick="verRuta(id)">...</div>

<!-- Después (accesible): -->
<button class="sel-route-card" onclick="verRuta(id)" type="button">...</button>
<!-- O si no puede ser button (ej. contiene otros botones): -->
<div class="sel-route-card" tabindex="0" role="button"
     onclick="verRuta(id)"
     onkeydown="if(event.key==='Enter'||event.key===' ')verRuta(id)">...</div>
```

---

## Apéndice — Inventario de duplicaciones CSS

| Componente | Definido en |
|-----------|------------|
| `.btn` (base) | asignacion.css, configuracion.css, modificacion.css, usuarios.css, menu.css |
| `.btn-primary` (separado) | pdf.css |
| `.modal-overlay` + `.modal-box` | asignacion.css, configuracion.css, usuarios.css, pdf.css |
| `.confirm-box` (alias modal) | modificacion.css |
| `.ext-modal-box` (alias modal) | extraccion.css |
| `.form-group` + `.form-control` | asignacion.css, configuracion.css, modificacion.css, usuarios.css |
| `.data-table` | configuracion.css, seguimiento.css, usuarios.css |
| `.input-search` | configuracion.css, seguimiento.css, usuarios.css |
| `.table-responsive` | configuracion.css, seguimiento.css, usuarios.css |
| `.switch` + `.slider` | asignacion.css, configuracion.css |
| Spinner CSS | pdf.css, conductor.css, conductor_desktop.css |
| Sistema de toasts | 5+ implementaciones (cfg, ext, mod, usr, cd, cdd) |
| Colores de días (lunes–sábado) | 2 sistemas dentro de asignacion.css |
| Fullscreen loader HTML/CSS | base.html/base.css, menu.css/menu template |

**Total estimado de líneas CSS duplicadas:** ~600-800 líneas eliminables mediante `shared.css`.
