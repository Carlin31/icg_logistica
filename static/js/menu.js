/**
 * static/js/menu.js
 * Lógica del Menú Principal — Gestión de Logísticas
 *
 * Cambio respecto a versión anterior:
 *   - Los indicadores de progreso usan `secciones_completadas` (booleanos)
 *     en lugar del campo embebido `datos` que ya no existe en la colección.
 *   - Se eliminó la llamada a /api/guardar-seccion (el endpoint fue removido;
 *     la persistencia por sección ahora es directa en cada módulo).
 */

// ── Estado global ────────────────────────────────────────────────────────────
let _todasLogisticas = [];
let _logisticaActivaId   = null;
let _logisticaActivaSlug = null;
let _pendienteEliminarId = null;

const SECCIONES_INFO = {
  extraccion:  { label: "EXT" },
  asignacion:  { label: "ASI" },
  modificacion: { label: "MOD" },
};

// ── Fullscreen Loader ─────────────────────────────────────────────────────────
const Loader = (() => {
  let _rotTimer = null;

  function show(titulo, mensajes = []) {
    const overlay = document.getElementById("fl-overlay");
    const title   = document.getElementById("fl-title");
    const msg     = document.getElementById("fl-msg");
    if (!overlay) return;

    clearInterval(_rotTimer);
    title.textContent = titulo;
    msg.textContent   = mensajes[0] || "";
    msg.classList.remove("fl-fade");
    overlay.classList.add("fl-visible");

    if (mensajes.length > 1) {
      let idx = 0;
      _rotTimer = setInterval(() => {
        idx = (idx + 1) % mensajes.length;
        msg.classList.add("fl-fade");
        setTimeout(() => {
          msg.textContent = mensajes[idx];
          msg.classList.remove("fl-fade");
        }, 350);
      }, 2600);
    }
  }

  function hide() {
    clearInterval(_rotTimer);
    const overlay = document.getElementById("fl-overlay");
    if (overlay) overlay.classList.remove("fl-visible");
  }

  return { show, hide };
})();

// ── Mensajes contextuales por acción ─────────────────────────────────────────
const MSG = {
  crear: [
    "Validando el rango de fechas…",
    "Registrando en la base de datos…",
    "Configurando parámetros iniciales…",
    "Preparando la nueva logística…",
  ],
  activar: [
    "Verificando datos de la logística…",
    "Iniciando sesión de trabajo…",
    "Cargando módulos del sistema…",
    "Redirigiendo a Extracción…",
  ],
  eliminar: [
    "Eliminando la logística del sistema…",
    "Borrando datos de extracción…",
    "Borrando asignaciones y modificaciones…",
    "Limpiando todos los registros asociados…",
  ],
};

// ── Init ─────────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", async () => {
  lucide.createIcons();
  preseleccionarSemana();
  await cargarActiva();
  await cargarLogisticas();
  document.getElementById("overlay-eliminar")
    .addEventListener("click", (e) => { if (e.target === e.currentTarget) cerrarModal(); });
});

// ── Preseleccionar semana actual (lunes–viernes) ──────────────────────────────
function preseleccionarSemana() {
  const hoy  = new Date();
  const dow  = hoy.getDay();
  const diff = dow === 0 ? -6 : 1 - dow;

  const lunes   = new Date(hoy);
  const viernes = new Date(hoy);
  lunes.setDate(hoy.getDate() + diff);
  viernes.setDate(hoy.getDate() + diff + 4);

  document.getElementById("fi").value = lunes.toISOString().slice(0, 10);
  document.getElementById("ff").value = viernes.toISOString().slice(0, 10);
  actualizarPreview();
}

// ── Preview del nombre auto-generado ─────────────────────────────────────────
const MESES = [
  "", "enero", "febrero", "marzo", "abril", "mayo", "junio",
  "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
];

function nombreDesde(fi, ff) {
  const d1 = new Date(fi + "T12:00:00");
  const d2 = new Date(ff + "T12:00:00");
  if (d1.getMonth() === d2.getMonth() && d1.getFullYear() === d2.getFullYear()) {
    return `Logística del ${d1.getDate()} al ${d2.getDate()} de ${MESES[d1.getMonth() + 1]} del ${d2.getFullYear()}`;
  }
  return `Logística del ${d1.getDate()} de ${MESES[d1.getMonth() + 1]} al ${d2.getDate()} de ${MESES[d2.getMonth() + 1]} del ${d2.getFullYear()}`;
}

function actualizarPreview() {
  const fi = document.getElementById("fi").value;
  const ff = document.getElementById("ff").value;
  const el = document.getElementById("nombre-preview");

  if (!fi || !ff) { el.textContent = "Selecciona un rango semanal..."; return; }
  if (fi > ff) {
    el.innerHTML = '<i data-lucide="triangle-alert"></i> La fecha de inicio debe ser anterior al fin.';
    lucide.createIcons();
    return;
  }
  el.innerHTML = '<i data-lucide="file-text"></i> ' + nombreDesde(fi, ff);
  lucide.createIcons();
}

// ── Crear logística ───────────────────────────────────────────────────────────
async function crearLogistica() {
  const fi = document.getElementById("fi").value;
  const ff = document.getElementById("ff").value;

  if (!fi || !ff) { mostrarToast("Selecciona las fechas de inicio y fin.", "error"); return; }
  if (fi > ff)    { mostrarToast("La fecha de inicio debe ser anterior al fin.", "error"); return; }

  Loader.show("Creando Logística", MSG.crear);
  const btn = document.getElementById("btn-crear");
  btn.disabled = true;
  btn.textContent = "Creando…";

  try {
    const res  = await fetch("/api/crear", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fecha_inicio: fi, fecha_fin: ff }),
    });
    const data = await res.json();

    if (data.status === "ok") {
      mostrarToast(`Logística creada: ${data.nombre}`, "ok");
      await cargarLogisticas();
    } else {
      mostrarToast(data.mensaje, "error");
    }
  } catch {
    mostrarToast("Error de conexión.", "error");
  } finally {
    Loader.hide();
    btn.disabled = false;
    btn.textContent = "Crear Logística";
  }
}

// ── Cargar banner de logística activa ─────────────────────────────────────────
async function cargarActiva() {
  try {
    const res  = await fetch("/api/activa");
    const data = await res.json();
    if (data.status === "ok") {
      _logisticaActivaId   = data.id;
      _logisticaActivaSlug = data.slug || null;
      mostrarBanner(data);
    }
  } catch { /* silencioso */ }
}

function mostrarBanner(data) {
  const banner = document.getElementById("banner-activa");
  banner.style.display = "flex";
  document.getElementById("banner-nombre").textContent = data.nombre;
  document.getElementById("banner-sub").textContent =
    `${formatearFecha(data.inicio)} — ${formatearFecha(data.fin)}`;
}

function irAExtraccion() {
  const slug = _logisticaActivaSlug;
  window.location.href = slug ? `/extraccion/${slug}` : "/extraccion/";
}

// ── Cargar y renderizar historial ─────────────────────────────────────────────
async function cargarLogisticas() {
  const grid = document.getElementById("grid-logisticas");
  grid.innerHTML = '<div class="loader-wrapper"><div class="spinner"></div></div>';

  try {
    const res  = await fetch("/api/listar");
    const data = await res.json();
    _todasLogisticas = data.logisticas || [];
    document.getElementById("badge-total").textContent = _todasLogisticas.length;
    filtrar();
  } catch {
    grid.innerHTML = '<div class="empty-state"><div class="empty-icon"><i data-lucide="triangle-alert"></i></div><h3>Error al cargar las logísticas</h3><p>Verifica la conexión con el servidor.</p></div>';
    lucide.createIcons();
  }
}

// ── Filtrar ───────────────────────────────────────────────────────────────────
function filtrar() {
  const texto = document.getElementById("buscador").value.toLowerCase().trim();
  const filtradas = _todasLogisticas.filter(l =>
    !texto || l.nombre.toLowerCase().includes(texto)
  );
  renderizarGrid(filtradas);
}

// ── Renderizar grid ───────────────────────────────────────────────────────────
function renderizarGrid(lista) {
  const grid = document.getElementById("grid-logisticas");

  if (lista.length === 0) {
    grid.innerHTML = `
      <div class="empty-state">
        <div class="empty-icon"><i data-lucide="inbox"></i></div>
        <h3>Sin logísticas</h3>
        <p>No se encontraron logísticas con los filtros actuales.<br>Crea una nueva para comenzar.</p>
      </div>`;
    lucide.createIcons();
    return;
  }

  grid.innerHTML = lista.map(l => renderCard(l)).join("");
  lucide.createIcons();
}

function renderCard(l) {
  const esActiva = l._id === _logisticaActivaId;

  // ── Dots de progreso por sección ──────────────────────────────────────────
  const secciones = l.secciones_completadas || {};
  const dotsHTML  = Object.entries(SECCIONES_INFO).map(([sec, info]) => {
    const hecho = secciones[sec] === true;
    const cls   = hecho ? "hecho" : "vacio";
    return `<div class="sec-dot ${cls}" title="${sec.charAt(0).toUpperCase() + sec.slice(1)}">${info.label}</div>`;
  }).join("");

  const borderColor = esActiva
    ? "border-color: var(--azul-claro); box-shadow: 0 0 0 2px rgba(45,108,202,.25);"
    : "";

  return `
    <div class="card" id="card-${l._id}" style="${borderColor}">
      <div class="card-top"></div>
      <div class="card-body">
        ${esActiva ? `<div class="card-activa-badge"><i data-lucide="play"></i> ACTIVA</div>` : ""}
        <div class="card-nombre">${esc(l.nombre)}</div>
        <div class="card-meta">
          <span class="meta-pill"><i data-lucide="calendar"></i> ${formatearFecha(l.fecha_inicio)} — ${formatearFecha(l.fecha_fin)}</span>
          <span class="meta-pill"><i data-lucide="clock"></i> ${l.ultima_modificacion || l.creado_en || "—"}</span>
        </div>
        <div class="secciones-progreso">${dotsHTML}</div>
      </div>
      <div class="card-footer">
        <button class="btn btn-activar" onclick="activarLogistica('${l._id}')">
          <i data-lucide="play"></i> ${esActiva ? "En uso" : "Seleccionar"}
        </button>

        <a class="btn btn-permalink" href="/extraccion/${esc(l.slug || _slugClient(l.nombre))}" title="Enlace directo a este perfil"><i data-lucide="link"></i></a>
        <button class="btn btn-eliminar" onclick="pedirEliminar('${l._id}', '${esc(l.nombre)}')" title="Eliminar"><i data-lucide="trash-2"></i></button>
      </div>
    </div>`;
}

// ── Activar logística ─────────────────────────────────────────────────────────
async function activarLogistica(id) {
  Loader.show("Activando Logística", MSG.activar);
  const btn = document.querySelector(`#card-${id} .btn-activar`);
  if (btn) { btn.disabled = true; btn.textContent = "Cargando…"; }

  try {
    const res  = await fetch(`/api/activar/${id}`, { method: "POST" });
    const data = await res.json();

    if (data.status === "ok") {
      _logisticaActivaId   = id;
      _logisticaActivaSlug = data.slug || null;
      mostrarBanner({ ...data, inicio: data.fecha_inicio, fin: data.fecha_fin });
      mostrarToast(`${data.nombre} activada. Redirigiendo...`, "ok");
      await cargarLogisticas();
      const destino = data.slug ? `/extraccion/${data.slug}` : "/extraccion/";
      setTimeout(() => window.location.href = destino, 1400);
      // El loader permanece visible hasta que la redirección completa
    } else {
      Loader.hide();
      mostrarToast(data.mensaje, "error");
      if (btn) { btn.disabled = false; btn.innerHTML = '<i data-lucide="play"></i> Seleccionar'; lucide.createIcons(); }
    }
  } catch {
    Loader.hide();
    mostrarToast("Error de conexión.", "error");
    if (btn) { btn.disabled = false; btn.innerHTML = '<i data-lucide="play"></i> Seleccionar'; lucide.createIcons(); }
  }
}

// ── Eliminar logística ────────────────────────────────────────────────────────
function pedirEliminar(id, nombre) {
  _pendienteEliminarId = id;
  document.getElementById("modal-msg").innerHTML =
    `¿Estás seguro de que deseas eliminar <strong>${esc(nombre)}</strong>?<br><br>
     <strong>Esta acción no se puede deshacer</strong> y borrará todos los datos asociados
     (extracción, rutas, asignación, modificación).`;
  document.getElementById("overlay-eliminar").classList.add("visible");
  document.getElementById("btn-confirmar-eliminar").onclick = confirmarEliminar;
}

function cerrarModal() {
  _pendienteEliminarId = null;
  document.getElementById("overlay-eliminar").classList.remove("visible");
}

async function confirmarEliminar() {
  if (!_pendienteEliminarId) return;

  // Guardar el ID ANTES de cerrarModal(), que lo pone a null.
  const idAEliminar = _pendienteEliminarId;
  cerrarModal();
  Loader.show("Eliminando Logística", MSG.eliminar);

  try {
    const res  = await fetch(`/api/eliminar/${idAEliminar}`, { method: "DELETE" });
    const data = await res.json();
    if (data.status === "ok") {
      if (_logisticaActivaId === idAEliminar) {
        _logisticaActivaId = null;
        document.getElementById("banner-activa").style.display = "none";
      }
      mostrarToast("Logística eliminada.", "ok");
      await cargarLogisticas();
    } else {
      mostrarToast(data.mensaje, "error");
    }
  } catch {
    mostrarToast("Error al eliminar.", "error");
  } finally {
    Loader.hide();
  }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

/**
 * Genera el slug en cliente para perfiles sin campo `slug` en la respuesta.
 * Replica la lógica de _slugify() de Python.
 */
function _slugClient(nombre) {
  return nombre
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

function esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function formatearFecha(iso) {
  if (!iso) return "—";
  const [a, m, d] = iso.split("-");
  return `${d}/${m}/${a}`;
}

// ── Toast ─────────────────────────────────────────────────────────────────────
function mostrarToast(msg, tipo = "info") {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.className = `toast ${tipo}`;
  requestAnimationFrame(() => {
    el.classList.add("visible");
    clearTimeout(el._timer);
    el._timer = setTimeout(() => el.classList.remove("visible"), 3200);
  });
}