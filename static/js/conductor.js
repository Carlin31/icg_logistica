// ===== Portal del Conductor — interfaz móvil/tablet =====
'use strict';

let _logisticasData   = [];
let _rutaActiva        = null;   // detalle de la ruta abierta actualmente
let _mapa               = null;
let _rutaLayer           = null;
let _marcadoresLayer     = null;
let _paradaPendiente     = null; // {tipo, key, accion} en espera de confirmación

const ABREV_DIA = {
  lunes: "Lunes", martes: "Martes", miercoles: "Miércoles",
  jueves: "Jueves", viernes: "Viernes", sabado: "Sábado", domingo: "Domingo",
};

document.addEventListener("DOMContentLoaded", async () => {
  lucide.createIcons();

  document.getElementById("cd-btn-volver").addEventListener("click", () => volverALista());

  // Modal confirmar entrega
  document.getElementById("cd-confirm-cancelar").addEventListener("click", cerrarConfirm);
  document.getElementById("cd-confirm-aceptar").addEventListener("click", confirmarEntregaPendiente);

  // Modal cancelar entrega
  document.getElementById("cd-cancelar-no").addEventListener("click", cerrarConfirmCancelar);
  document.getElementById("cd-cancelar-aceptar").addEventListener("click", confirmarCancelarPendiente);

  // Historial toggle
  document.getElementById("cd-historial-toggle").addEventListener("click", toggleHistorial);

  window.addEventListener("popstate", manejarNavegacionHistorial);

  await cargarRutas();
  aplicarEstadoDesdeURL(true);
});

// ── Persistencia de estado en la URL (slug/día/ruta) ──────────────
function _parsearRutaURL(pathname) {
  const partes = (pathname || location.pathname).replace(/^\/conductor\/?/, "").split("/").filter(Boolean);
  return { slug: partes[0] || null, dia: partes[1] || null, rutaId: partes[2] || null };
}

function _construirURL(slug, dia, rutaId) {
  if (!slug) return "/conductor/";
  let url = "/conductor/" + encodeURIComponent(slug);
  if (dia) {
    url += "/" + encodeURIComponent(dia);
    if (rutaId) url += "/" + encodeURIComponent(rutaId);
  }
  return url;
}

function _ubicarRutaEnDatos(rutaId) {
  for (const bucket of _logisticasData) {
    for (const [dia, rutas] of Object.entries(bucket.dias)) {
      const ruta = rutas.find(r => r.id === rutaId);
      if (ruta) return { slug: bucket.logistica.slug, dia };
    }
  }
  return null;
}

/** Al cargar la página (estado inicial embebido) o al navegar con atrás/adelante. */
function aplicarEstadoDesdeURL(esCargaInicial) {
  const { slug, dia, rutaId } = esCargaInicial && window.__CD_ESTADO_INICIAL__
    ? { slug: window.__CD_ESTADO_INICIAL__.logistica_slug, dia: window.__CD_ESTADO_INICIAL__.dia, rutaId: window.__CD_ESTADO_INICIAL__.ruta_id }
    : _parsearRutaURL();

  if (rutaId) {
    abrirRuta(rutaId, { actualizarUrl: false });
    return;
  }
  if (slug) {
    const card = document.getElementById("cd-log-" + CSS.escape(slug));
    if (card) setTimeout(() => card.scrollIntoView({ behavior: "smooth", block: "start" }), 200);
  }
}

function manejarNavegacionHistorial() {
  const { rutaId } = _parsearRutaURL();
  if (rutaId) {
    if (!_rutaActiva || _rutaActiva.id !== rutaId) abrirRuta(rutaId, { actualizarUrl: false });
  } else if (_rutaActiva) {
    volverALista({ actualizarUrl: false });
  }
}

// ── Carga de rutas ───────────────────────────────────────────────
async function cargarRutas() {
  const spinner = document.getElementById("cd-spinner");
  const vacio    = document.getElementById("cd-vacio");
  const cont     = document.getElementById("cd-logisticas");

  spinner.classList.remove("hidden");
  vacio.classList.add("hidden");
  cont.innerHTML = "";

  try {
    const res  = await fetch("/conductor/api/rutas");
    const data = await res.json();
    spinner.classList.add("hidden");

    if (data.status !== "ok" || !data.logisticas || data.logisticas.length === 0) {
      vacio.classList.remove("hidden");
      return;
    }
    _logisticasData = data.logisticas;
    renderLogisticas();
  } catch (err) {
    console.error("[cargarRutas]", err);
    spinner.classList.add("hidden");
    mostrarToastConductor("Error de conexión al cargar tus rutas.", "error");
  }
}

function renderLogisticas() {
  const cont            = document.getElementById("cd-logisticas");
  const historialLista  = document.getElementById("cd-historial-lista");
  const historialSection = document.getElementById("cd-historial-section");

  const activos     = _logisticasData.filter(b => !b.logistica_completada);
  const completados = _logisticasData.filter(b => b.logistica_completada);

  // Rutas activas
  if (activos.length === 0 && completados.length > 0) {
    cont.innerHTML = `<div class="cd-vacio"><i data-lucide="check-circle"></i><p>¡Todas las rutas han sido procesadas!</p><span>Revisa el historial para ver los detalles.</span></div>`;
  } else {
    cont.innerHTML = activos.map(bucket => renderBucket(bucket)).join("");
  }

  // Historial
  if (completados.length > 0) {
    historialLista.innerHTML = completados.map(bucket => renderBucket(bucket, true)).join("");
    historialSection.classList.remove("hidden");
  } else {
    historialSection.classList.add("hidden");
  }

  lucide.createIcons();
  document.querySelectorAll(".cd-ruta-card").forEach(card => {
    card.addEventListener("click", () => abrirRuta(card.dataset.rutaId));
  });
}

function renderBucket(bucket, esHistorial = false) {
  const log = bucket.logistica;
  const diasHTML = Object.entries(bucket.dias).map(([dia, rutas]) => `
    <div class="cd-dia-bloque">
      <div class="cd-dia-titulo">${ABREV_DIA[dia] || dia}</div>
      ${rutas.map(r => renderRutaCard(r)).join("")}
    </div>`).join("");

  const claseExtra = esHistorial ? " cd-logistica--completada" : "";
  return `
    <section class="cd-logistica-card${claseExtra}" id="cd-log-${h(log.slug || log.id)}">
      <div class="cd-logistica-header">
        <i data-lucide="${esHistorial ? 'check-circle-2' : 'building-2'}"></i>
        <div>
          <div class="cd-logistica-nombre">${h(log.nombre)}</div>
          <div class="cd-logistica-fechas">${h(log.fecha_inicio)} — ${h(log.fecha_fin)}</div>
        </div>
      </div>
      ${diasHTML}
    </section>`;
}

function renderRutaCard(r) {
  const pct = r.pct_avance || 0;
  const estadoClass = r.completada ? "cd-completada" : (r.entregadas > 0 || r.canceladas > 0 ? "cd-en-curso" : "");
  const canceladasLabel = r.canceladas > 0 ? ` · <span class="cd-ruta-canceladas">${r.canceladas} cancelada${r.canceladas > 1 ? "s" : ""}</span>` : "";
  return `
    <button class="cd-ruta-card ${estadoClass}" data-ruta-id="${h(r.id)}">
      <div class="cd-ruta-card-top">
        <span class="cd-ruta-card-veh"><i data-lucide="truck"></i> ${h(r.vehiculo_abrev || "Vehículo")}</span>
        ${r.completada
          ? `<span class="cd-tag cd-tag-ok"><i data-lucide="check"></i> Completada</span>`
          : (r.entregadas > 0 || r.canceladas > 0 ? `<span class="cd-tag cd-tag-curso">En curso</span>` : "")}
      </div>
      <div class="cd-ruta-card-nombre">${h(r.nombre || "Ruta")}</div>
      <div class="cd-progreso-bar-wrap cd-progreso-bar-wrap--sm">
        <div class="cd-progreso-track"><div class="cd-progreso-fill" style="width:${pct}%"></div></div>
        <span class="cd-progreso-label">${r.entregadas} de ${r.total_paradas} entregadas — ${pct}%${canceladasLabel}</span>
      </div>
    </button>`;
}

// ── Historial toggle ─────────────────────────────────────────────
function toggleHistorial() {
  const lista   = document.getElementById("cd-historial-lista");
  const toggle  = document.getElementById("cd-historial-toggle");
  const abierto = toggle.getAttribute("aria-expanded") === "true";
  toggle.setAttribute("aria-expanded", String(!abierto));
  lista.classList.toggle("hidden", abierto);
  toggle.classList.toggle("cd-historial-toggle--abierto", !abierto);
  if (!abierto) lucide.createIcons();
}

// ── Detalle de ruta ──────────────────────────────────────────────
async function abrirRuta(rutaId, { actualizarUrl = true } = {}) {
  document.getElementById("cd-vista-lista").classList.add("hidden");
  document.getElementById("cd-vista-ruta").classList.remove("hidden");
  document.getElementById("cd-lista-paradas").innerHTML = `<div class="cd-spinner"><div class="cd-spin"></div> Cargando ruta…</div>`;

  try {
    const res  = await fetch(`/conductor/api/ruta/${rutaId}`);
    const data = await res.json();
    if (data.status !== "ok") {
      mostrarToastConductor(data.mensaje || "Esta ruta ya no está disponible.", "error");
      volverALista({ actualizarUrl: true });
      return;
    }
    _rutaActiva = data;
    document.getElementById("cd-ruta-nombre").textContent = data.nombre || "Ruta";
    document.getElementById("cd-ruta-meta").textContent =
      `${ABREV_DIA[data.dia] || data.dia} · Salida ${data.hora_salida || "—"}`;

    if (actualizarUrl) {
      const ubic = _ubicarRutaEnDatos(rutaId);
      const url  = _construirURL(ubic?.slug, ubic?.dia || data.dia, rutaId);
      history.pushState({ rutaId }, "", url);
    }

    inicializarMapaSiHaceFalta();
    dibujarRutaEnMapa(data);
    renderListaParadas(data.paradas);
    actualizarProgreso(data.paradas);
    lucide.createIcons();
  } catch (err) {
    console.error("[abrirRuta]", err);
    mostrarToastConductor("Error de conexión al cargar la ruta.", "error");
    volverALista({ actualizarUrl: true });
  }
}

function volverALista({ actualizarUrl = true } = {}) {
  document.getElementById("cd-vista-ruta").classList.add("hidden");
  document.getElementById("cd-vista-lista").classList.remove("hidden");
  _rutaActiva = null;
  if (actualizarUrl) history.pushState(null, "", "/conductor/");
  cargarRutas(); // refresca progreso por si se marcaron entregas
}

function inicializarMapaSiHaceFalta() {
  if (_mapa) return;
  _mapa = L.map("cd-mapa", { zoomControl: true }).setView([18.87, -96.95], 9);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: '&copy; OpenStreetMap',
    maxZoom: 18,
  }).addTo(_mapa);
  _rutaLayer       = L.layerGroup().addTo(_mapa);
  _marcadoresLayer = L.layerGroup().addTo(_mapa);
}

function dibujarRutaEnMapa(data) {
  _mapa.invalidateSize();
  _rutaLayer.clearLayers();
  _marcadoresLayer.clearLayers();

  if (data.geometry && data.geometry.length > 1) {
    const latlngs = data.geometry.map(c => [c[1], c[0]]);
    L.polyline(latlngs, { color: "#2563eb", weight: 5, opacity: 0.9 }).addTo(_rutaLayer);
  }

  const [matrizLat, matrizLon] = data.matriz || [18.87, -96.95];
  L.marker([matrizLat, matrizLon], {
    icon: L.divIcon({ className: "cd-marker-matriz", html: '<i data-lucide="warehouse"></i>', iconSize: [28, 28] }),
  }).addTo(_marcadoresLayer);

  const bounds = [[matrizLat, matrizLon]];
  (data.paradas || []).forEach((p, i) => {
    if (p.latitud == null || p.longitud == null) return;
    const claseEstado = p.estado === "entregado" ? "cd-marker--entregado"
                      : p.estado === "cancelada"  ? "cd-marker--cancelado"
                      : "";
    L.marker([p.latitud, p.longitud], {
      icon: L.divIcon({
        className: `cd-marker ${claseEstado}`,
        html: `<span>${i + 1}</span>`,
        iconSize: [26, 26],
      }),
    }).bindPopup(`<strong>${h(p.nombre)}</strong><br>${p.peso_kg ? p.peso_kg + " kg" : ""}`).addTo(_marcadoresLayer);
    bounds.push([p.latitud, p.longitud]);
  });

  requestAnimationFrame(() => {
    _mapa.invalidateSize();
    if (bounds.length > 1) _mapa.fitBounds(bounds, { padding: [30, 30] });
  });
  setTimeout(() => {
    _mapa.invalidateSize();
    if (bounds.length > 1) _mapa.fitBounds(bounds, { padding: [30, 30] });
  }, 300);
}

function renderListaParadas(paradas) {
  const cont = document.getElementById("cd-lista-paradas");
  if (!paradas || paradas.length === 0) {
    cont.innerHTML = `<div class="cd-vacio"><p>Esta ruta no tiene paradas.</p></div>`;
    return;
  }
  cont.innerHTML = paradas.map((p, i) => {
    const esEntregado = p.estado === "entregado";
    const esCancelado = p.estado === "cancelada";

    const claseEstado = esEntregado ? "cd-parada--entregada"
                      : esCancelado ? "cd-parada--cancelada"
                      : "";

    let acciones = "";
    if (esEntregado) {
      acciones = `
        <div class="cd-parada-acciones">
          <span class="cd-parada-check" title="Entregado"><i data-lucide="check-circle-2"></i></span>
          <button class="cd-btn-cancelar-entrega" data-tipo="${p.tipo}" data-key="${h(p.key)}" title="Cancelar entrega">
            <i data-lucide="x-circle"></i>
          </button>
        </div>`;
    } else if (esCancelado) {
      acciones = `
        <div class="cd-parada-acciones">
          <span class="cd-parada-cancelada-icon" title="Cancelada"><i data-lucide="ban"></i></span>
          <button class="cd-btn-entregar cd-btn-entregar--reentrega" data-tipo="${p.tipo}" data-key="${h(p.key)}" title="Marcar como entregado">
            <i data-lucide="package-check"></i>
          </button>
        </div>`;
    } else {
      acciones = `<button class="cd-btn-entregar" data-tipo="${p.tipo}" data-key="${h(p.key)}"><i data-lucide="package-check"></i></button>`;
    }

    const horaLabel = esEntregado && p.entregado_en
      ? ` · <span class="cd-parada-hora">${fmtHoraCorta(p.entregado_en)}</span>`
      : esCancelado
      ? ` · <span class="cd-parada-cancelada-label">Cancelada</span>`
      : "";

    return `
      <div class="cd-parada ${claseEstado}" data-tipo="${p.tipo}" data-key="${h(p.key)}">
        <div class="cd-parada-num">${i + 1}</div>
        <div class="cd-parada-info">
          <div class="cd-parada-nombre">${h(p.nombre)}</div>
          <div class="cd-parada-detalle">
            ${p.tipo === "mayorista" ? '<span class="cd-parada-tag">Mayorista</span>' : ""}
            ${p.peso_kg ? `${Number(p.peso_kg).toLocaleString("es-MX")} kg` : ""}
            ${p.latitud == null ? ' · <span class="cd-sin-coords">sin ubicación</span>' : ""}${horaLabel}
          </div>
        </div>
        ${acciones}
      </div>`;
  }).join("");

  cont.querySelectorAll(".cd-btn-entregar").forEach(btn => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      abrirConfirmEntrega(btn.dataset.tipo, btn.dataset.key);
    });
  });
  cont.querySelectorAll(".cd-btn-cancelar-entrega").forEach(btn => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      abrirConfirmCancelar(btn.dataset.tipo, btn.dataset.key);
    });
  });
  lucide.createIcons();
}

function actualizarProgreso(paradas) {
  const total      = (paradas || []).length;
  const entregadas = (paradas || []).filter(p => p.estado === "entregado").length;
  const canceladas = (paradas || []).filter(p => p.estado === "cancelada").length;
  const pct = total ? Math.round((entregadas / total) * 100) : 0;
  document.getElementById("cd-progreso-fill").style.width = pct + "%";
  let label = `${entregadas} de ${total} entregadas — ${pct}%`;
  if (canceladas > 0) label += ` · ${canceladas} cancelada${canceladas > 1 ? "s" : ""}`;
  document.getElementById("cd-progreso-label").textContent = label;
}

// ── Marcar entrega ───────────────────────────────────────────────
function abrirConfirmEntrega(tipo, key) {
  _paradaPendiente = { tipo, key, accion: "entregar" };
  const parada = (_rutaActiva.paradas || []).find(p => p.tipo === tipo && String(p.key) === String(key));
  const esCancelado = parada?.estado === "cancelada";
  document.getElementById("cd-confirm-titulo").textContent = esCancelado ? "Re-entregar" : "Marcar como entregado";
  document.getElementById("cd-confirm-msg").textContent =
    parada ? `¿Confirmas la entrega en "${parada.nombre}"?` : "¿Confirmas esta entrega?";
  document.getElementById("cd-confirm-overlay").classList.remove("hidden");
}

function cerrarConfirm() {
  _paradaPendiente = null;
  document.getElementById("cd-confirm-overlay").classList.add("hidden");
}

async function confirmarEntregaPendiente() {
  if (!_paradaPendiente || !_rutaActiva) { cerrarConfirm(); return; }
  const { tipo, key } = _paradaPendiente;

  cerrarConfirm();

  try {
    Loader.show("Registrando entrega…");
    const res = await fetch("/conductor/api/marcar-entrega", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ ruta_id: _rutaActiva.id, parada_tipo: tipo, parada_key: key, estado: "entregado" }),
    });
    const data = await res.json();
    if (data.status !== "ok") {
      mostrarToastConductor(data.mensaje || "No se pudo registrar la entrega.", "error");
      return;
    }
    const parada = _rutaActiva.paradas.find(p => p.tipo === tipo && String(p.key) === String(key));
    if (parada) {
      parada.estado    = "entregado";
      parada.entregado = true;
      parada.cancelado = false;
      parada.entregado_en = new Date().toISOString();
    }
    renderListaParadas(_rutaActiva.paradas);
    dibujarRutaEnMapa(_rutaActiva);
    actualizarProgreso(_rutaActiva.paradas);
    mostrarToastConductor("Entrega registrada correctamente.", "ok");
  } catch (err) {
    console.error("[confirmarEntregaPendiente]", err);
    mostrarToastConductor("Error de conexión al registrar la entrega.", "error");
  } finally {
    Loader.hide();
  }
}

// ── Cancelar entrega ─────────────────────────────────────────────
function abrirConfirmCancelar(tipo, key) {
  _paradaPendiente = { tipo, key, accion: "cancelar" };
  const parada = (_rutaActiva.paradas || []).find(p => p.tipo === tipo && String(p.key) === String(key));
  document.getElementById("cd-cancelar-msg").textContent =
    parada ? `¿Cancelar la entrega en "${parada.nombre}"?` : "¿Cancelar esta entrega?";
  document.getElementById("cd-cancelar-overlay").classList.remove("hidden");
}

function cerrarConfirmCancelar() {
  _paradaPendiente = null;
  document.getElementById("cd-cancelar-overlay").classList.add("hidden");
}

async function confirmarCancelarPendiente() {
  if (!_paradaPendiente || !_rutaActiva) { cerrarConfirmCancelar(); return; }
  const { tipo, key } = _paradaPendiente;

  cerrarConfirmCancelar();

  try {
    Loader.show("Cancelando entrega…");
    const res = await fetch("/conductor/api/marcar-entrega", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ ruta_id: _rutaActiva.id, parada_tipo: tipo, parada_key: key, estado: "cancelada" }),
    });
    const data = await res.json();
    if (data.status !== "ok") {
      mostrarToastConductor(data.mensaje || "No se pudo cancelar la entrega.", "error");
      return;
    }
    const parada = _rutaActiva.paradas.find(p => p.tipo === tipo && String(p.key) === String(key));
    if (parada) {
      parada.estado    = "cancelada";
      parada.entregado = false;
      parada.cancelado = true;
    }
    renderListaParadas(_rutaActiva.paradas);
    dibujarRutaEnMapa(_rutaActiva);
    actualizarProgreso(_rutaActiva.paradas);
    mostrarToastConductor("Entrega cancelada.", "aviso");
  } catch (err) {
    console.error("[confirmarCancelarPendiente]", err);
    mostrarToastConductor("Error de conexión al cancelar la entrega.", "error");
  } finally {
    Loader.hide();
  }
}

// ── Loader del sistema ───────────────────────────────────────────
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

// ── Utilidades ───────────────────────────────────────────────────
function h(s) { return String(s ?? "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;"); }

function fmtHoraCorta(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return "";
  return d.toLocaleTimeString("es-MX", { hour: "2-digit", minute: "2-digit" });
}

let _toastTimer = null;
function mostrarToastConductor(msg, tipo = "ok") {
  const toast = document.getElementById("cd-toast");
  toast.textContent = msg;
  toast.className = `cd-toast cd-toast--${tipo}`;
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => toast.classList.add("hidden"), 3200);
}
