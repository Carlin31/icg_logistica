// ===== Portal del Conductor — interfaz de escritorio =====
'use strict';

let _logisticasData  = [];
let _rutaActiva       = null;
let _filtroActivo     = "todas";
let _mapa              = null;
let _rutaLayer          = null;
let _marcadoresLayer    = null;
let _paradaPendiente    = null; // {tipo, key, accion}

const ABREV_DIA = {
  lunes: "Lunes", martes: "Martes", miercoles: "Miércoles",
  jueves: "Jueves", viernes: "Viernes", sabado: "Sábado", domingo: "Domingo",
};

document.addEventListener("DOMContentLoaded", async () => {
  lucide.createIcons();

  document.querySelectorAll(".cdd-filtro").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".cdd-filtro").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      _filtroActivo = btn.dataset.filtro;
      renderLogisticas();
    });
  });

  // Modal confirmar entrega
  document.getElementById("cdd-confirm-cancelar").addEventListener("click", cerrarConfirm);
  document.getElementById("cdd-confirm-aceptar").addEventListener("click", confirmarEntregaPendiente);

  // Modal cancelar entrega
  document.getElementById("cdd-cancelar-no").addEventListener("click", cerrarConfirmCancelar);
  document.getElementById("cdd-cancelar-aceptar").addEventListener("click", confirmarCancelarPendiente);

  window.addEventListener("popstate", manejarNavegacionHistorial);

  await cargarRutas();
  aplicarEstadoDesdeURL();
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

function aplicarEstadoDesdeURL() {
  const inicial = window.__CD_ESTADO_INICIAL__;
  const { rutaId } = inicial && inicial.ruta_id ? { rutaId: inicial.ruta_id } : _parsearRutaURL();
  if (rutaId) abrirRuta(rutaId, { actualizarUrl: false });
}

function manejarNavegacionHistorial() {
  const { rutaId } = _parsearRutaURL();
  if (rutaId) {
    if (!_rutaActiva || _rutaActiva.id !== rutaId) abrirRuta(rutaId, { actualizarUrl: false });
  } else {
    cerrarDetalleRuta({ actualizarUrl: false });
  }
}

// ── Carga de rutas (sidebar) ──────────────────────────────────────
async function cargarRutas() {
  const spinner = document.getElementById("cdd-spinner");
  const vacio    = document.getElementById("cdd-vacio");

  spinner.classList.remove("hidden");
  vacio.classList.add("hidden");

  try {
    const res  = await fetch("/conductor/api/rutas");
    const data = await res.json();
    spinner.classList.add("hidden");

    if (data.status !== "ok" || !data.logisticas || data.logisticas.length === 0) {
      vacio.classList.remove("hidden");
      document.getElementById("cdd-logisticas").innerHTML = "";
      return;
    }
    _logisticasData = data.logisticas;
    renderLogisticas();
  } catch (err) {
    console.error("[cargarRutas]", err);
    spinner.classList.add("hidden");
    mostrarToastDesktop("Error de conexión al cargar tus rutas.", "error");
  }
}

function _pasaFiltro(r) {
  if (_filtroActivo === "pendientes")  return r.entregadas === 0 && r.canceladas === 0;
  if (_filtroActivo === "curso")       return (r.entregadas > 0 || r.canceladas > 0) && !r.completada;
  if (_filtroActivo === "completadas") return r.completada;
  return true;
}

function renderLogisticas() {
  const cont = document.getElementById("cdd-logisticas");

  // Separar activas y completadas (historial)
  const activos     = _logisticasData.filter(b => !b.logistica_completada);
  const completados = _logisticasData.filter(b => b.logistica_completada);

  const renderBuckets = (buckets, esHistorial = false) => buckets.map(bucket => {
    const log = bucket.logistica;
    const diasHTML = Object.entries(bucket.dias).map(([dia, rutas]) => {
      const filtradas = rutas.filter(_pasaFiltro);
      if (filtradas.length === 0) return "";
      return `
        <div class="cdd-dia-bloque">
          <div class="cdd-dia-titulo">${ABREV_DIA[dia] || dia}</div>
          ${filtradas.map(r => renderRutaItem(r)).join("")}
        </div>`;
    }).join("");
    if (!diasHTML.trim()) return "";
    const claseExtra = esHistorial ? " cdd-logistica--completada" : "";
    return `
      <section class="cdd-logistica-card${claseExtra}" id="cdd-log-${h(log.slug || log.id)}">
        <div class="cdd-logistica-header">
          <i data-lucide="${esHistorial ? 'check-circle-2' : 'building-2'}"></i>
          <span>${h(log.nombre)}</span>
        </div>
        ${diasHTML}
      </section>`;
  }).join("");

  let html = renderBuckets(activos);

  if (completados.length > 0) {
    html += `
      <div class="cdd-historial-sep">
        <i data-lucide="history"></i> Historial
      </div>
      ${renderBuckets(completados, true)}`;
  }

  cont.innerHTML = html || `<div class="cdd-vacio"><p>Sin rutas para este filtro.</p></div>`;

  lucide.createIcons();
  cont.querySelectorAll(".cdd-ruta-item").forEach(item => {
    item.addEventListener("click", () => abrirRuta(item.dataset.rutaId));
  });
}

function renderRutaItem(r) {
  const pct = r.pct_avance || 0;
  const activa = _rutaActiva && _rutaActiva.id === r.id;
  const estadoClass = r.completada ? "cdd-completada" : ((r.entregadas > 0 || r.canceladas > 0) ? "cdd-en-curso" : "");
  const canceladasLabel = r.canceladas > 0
    ? ` <span class="cdd-ruta-canceladas">${r.canceladas}✗</span>` : "";
  return `
    <button class="cdd-ruta-item ${estadoClass} ${activa ? "cdd-ruta-item--activa" : ""}" data-ruta-id="${h(r.id)}">
      <div class="cdd-ruta-item-top">
        <span class="cdd-ruta-item-veh"><i data-lucide="truck"></i> ${h(r.vehiculo_abrev || "Vehículo")}</span>
        ${r.completada ? `<i data-lucide="check-circle-2" class="cdd-tag-icon cdd-tag-ok"></i>` : ""}
      </div>
      <div class="cdd-ruta-item-nombre">${h(r.nombre || "Ruta")}</div>
      <div class="cdd-progreso-bar-wrap cdd-progreso-bar-wrap--sm">
        <div class="cdd-progreso-track"><div class="cdd-progreso-fill" style="width:${pct}%"></div></div>
        <span class="cdd-progreso-label">${r.entregadas}/${r.total_paradas} — ${pct}%${canceladasLabel}</span>
      </div>
    </button>`;
}

// ── Detalle de ruta (mapa + panel derecho) ────────────────────────
async function abrirRuta(rutaId, { actualizarUrl = true } = {}) {
  try {
    const res  = await fetch(`/conductor/api/ruta/${rutaId}`);
    const data = await res.json();
    if (data.status !== "ok") {
      mostrarToastDesktop(data.mensaje || "Esta ruta ya no está disponible.", "error");
      cerrarDetalleRuta({ actualizarUrl: true });
      return;
    }
    _rutaActiva = data;

    document.getElementById("cdd-mapa-vacio").classList.add("hidden");
    document.getElementById("cdd-mapa").classList.remove("hidden");
    document.getElementById("cdd-mapa-toolbar").classList.remove("hidden");
    document.getElementById("cdd-detalle-vacio").classList.add("hidden");
    document.getElementById("cdd-detalle-lista").classList.remove("hidden");

    document.getElementById("cdd-ruta-nombre").textContent = data.nombre || "Ruta";
    document.getElementById("cdd-ruta-meta").textContent =
      `${ABREV_DIA[data.dia] || data.dia} · Salida ${data.hora_salida || "—"}`;

    if (actualizarUrl) {
      const ubic = _ubicarRutaEnDatos(rutaId);
      const url  = _construirURL(ubic?.slug, ubic?.dia || data.dia, rutaId);
      history.pushState({ rutaId }, "", url);
    }

    inicializarMapaSiHaceFalta();
    dibujarRutaEnMapa(data);
    renderDetalleParadas(data.paradas);
    actualizarProgreso(data.paradas);
    renderLogisticas(); // refresca el resaltado de "ruta activa" en el sidebar
    lucide.createIcons();
  } catch (err) {
    console.error("[abrirRuta]", err);
    mostrarToastDesktop("Error de conexión al cargar la ruta.", "error");
  }
}

function cerrarDetalleRuta({ actualizarUrl = true } = {}) {
  _rutaActiva = null;
  document.getElementById("cdd-mapa-vacio").classList.remove("hidden");
  document.getElementById("cdd-mapa").classList.add("hidden");
  document.getElementById("cdd-mapa-toolbar").classList.add("hidden");
  document.getElementById("cdd-detalle-vacio").classList.remove("hidden");
  document.getElementById("cdd-detalle-lista").classList.add("hidden");
  if (actualizarUrl) history.pushState(null, "", "/conductor/");
  renderLogisticas();
}

function inicializarMapaSiHaceFalta() {
  if (_mapa) return;
  _mapa = L.map("cdd-mapa", { zoomControl: true }).setView([18.87, -96.95], 9);
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
    icon: L.divIcon({ className: "cdd-marker-matriz", html: '<i data-lucide="warehouse"></i>', iconSize: [30, 30] }),
  }).addTo(_marcadoresLayer);

  const bounds = [[matrizLat, matrizLon]];
  (data.paradas || []).forEach((p, i) => {
    if (p.latitud == null || p.longitud == null) return;
    const claseEstado = p.estado === "entregado" ? "cdd-marker--entregado"
                      : p.estado === "cancelada"  ? "cdd-marker--cancelado"
                      : "";
    L.marker([p.latitud, p.longitud], {
      icon: L.divIcon({
        className: `cdd-marker ${claseEstado}`,
        html: `<span>${i + 1}</span>`,
        iconSize: [28, 28],
      }),
    }).bindPopup(`<strong>${h(p.nombre)}</strong><br>${p.peso_kg ? p.peso_kg + " kg" : ""}`).addTo(_marcadoresLayer);
    bounds.push([p.latitud, p.longitud]);
  });

  requestAnimationFrame(() => {
    _mapa.invalidateSize();
    if (bounds.length > 1) _mapa.fitBounds(bounds, { padding: [40, 40] });
  });
  setTimeout(() => {
    _mapa.invalidateSize();
    if (bounds.length > 1) _mapa.fitBounds(bounds, { padding: [40, 40] });
  }, 300);
}

function renderDetalleParadas(paradas) {
  const cont = document.getElementById("cdd-detalle-lista");
  if (!paradas || paradas.length === 0) {
    cont.innerHTML = `<div class="cdd-vacio"><p>Esta ruta no tiene paradas.</p></div>`;
    return;
  }
  cont.innerHTML = paradas.map((p, i) => {
    const esEntregado = p.estado === "entregado";
    const esCancelado = p.estado === "cancelada";
    const claseEstado = esEntregado ? "cdd-parada--entregada"
                      : esCancelado ? "cdd-parada--cancelada"
                      : "";

    let accionDerecha = "";
    if (esEntregado) {
      accionDerecha = `
        <div class="cdd-parada-acciones">
          <span class="cdd-parada-check" title="Entregado"><i data-lucide="check-circle-2"></i></span>
          <button class="cdd-btn-cancelar-entrega" data-tipo="${p.tipo}" data-key="${h(p.key)}" title="Cancelar entrega">
            <i data-lucide="x-circle"></i> Cancelar
          </button>
        </div>`;
    } else if (esCancelado) {
      accionDerecha = `
        <div class="cdd-parada-acciones">
          <span class="cdd-parada-cancelada-icon" title="Cancelada"><i data-lucide="ban"></i></span>
          <button class="cdd-btn-entregar cdd-btn-entregar--reentrega" data-tipo="${p.tipo}" data-key="${h(p.key)}">
            <i data-lucide="package-check"></i> Re-entregar
          </button>
        </div>`;
    } else {
      accionDerecha = `<button class="cdd-btn-entregar" data-tipo="${p.tipo}" data-key="${h(p.key)}"><i data-lucide="package-check"></i> Entregar</button>`;
    }

    return `
      <div class="cdd-parada ${claseEstado}" data-tipo="${p.tipo}" data-key="${h(p.key)}">
        <div class="cdd-parada-num">${i + 1}</div>
        <div class="cdd-parada-info">
          <div class="cdd-parada-nombre">${h(p.nombre)}</div>
          <div class="cdd-parada-detalle">
            ${p.tipo === "mayorista" ? '<span class="cdd-parada-tag">Mayorista</span>' : '<span class="cdd-parada-tag cdd-parada-tag--suc">Sucursal</span>'}
            ${p.peso_kg ? `${Number(p.peso_kg).toLocaleString("es-MX")} kg` : ""}
            ${esCancelado ? ' · <span class="cdd-parada-cancelada-label">Cancelada</span>' : ""}
          </div>
          ${p.latitud != null
            ? `<div class="cdd-parada-coords">${p.latitud.toFixed(5)}, ${p.longitud.toFixed(5)}</div>`
            : `<div class="cdd-sin-coords">Sin ubicación registrada</div>`}
          ${esEntregado && p.entregado_en
            ? `<div class="cdd-parada-fecha">Entregado: ${new Date(p.entregado_en).toLocaleString("es-MX")}</div>`
            : ""}
        </div>
        ${accionDerecha}
      </div>`;
  }).join("");

  cont.querySelectorAll(".cdd-btn-entregar").forEach(btn => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      abrirConfirmEntrega(btn.dataset.tipo, btn.dataset.key);
    });
  });
  cont.querySelectorAll(".cdd-btn-cancelar-entrega").forEach(btn => {
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
  document.getElementById("cdd-progreso-fill").style.width = pct + "%";
  let label = `${entregadas} de ${total} entregadas — ${pct}%`;
  if (canceladas > 0) label += ` · ${canceladas} cancelada${canceladas > 1 ? "s" : ""}`;
  document.getElementById("cdd-progreso-label").textContent = label;
}

// ── Marcar entrega ───────────────────────────────────────────────
function abrirConfirmEntrega(tipo, key) {
  _paradaPendiente = { tipo, key, accion: "entregar" };
  const parada = (_rutaActiva.paradas || []).find(p => p.tipo === tipo && String(p.key) === String(key));
  const esCancelado = parada?.estado === "cancelada";
  document.getElementById("cdd-confirm-titulo").textContent = esCancelado ? "Re-entregar" : "Marcar como entregado";
  document.getElementById("cdd-confirm-msg").textContent =
    parada ? `¿Confirmas la entrega en "${parada.nombre}"?` : "¿Confirmas esta entrega?";
  document.getElementById("cdd-confirm-overlay").classList.remove("hidden");
}

function cerrarConfirm() {
  _paradaPendiente = null;
  document.getElementById("cdd-confirm-overlay").classList.add("hidden");
}

async function confirmarEntregaPendiente() {
  if (!_paradaPendiente || !_rutaActiva) { cerrarConfirm(); return; }
  const { tipo, key } = _paradaPendiente;
  const btnAceptar = document.getElementById("cdd-confirm-aceptar");
  btnAceptar.disabled = true;

  try {
    const res = await fetch("/conductor/api/marcar-entrega", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ ruta_id: _rutaActiva.id, parada_tipo: tipo, parada_key: key, estado: "entregado" }),
    });
    const data = await res.json();
    if (data.status !== "ok") {
      mostrarToastDesktop(data.mensaje || "No se pudo registrar la entrega.", "error");
      return;
    }
    const parada = _rutaActiva.paradas.find(p => p.tipo === tipo && String(p.key) === String(key));
    if (parada) {
      parada.estado    = "entregado";
      parada.entregado = true;
      parada.cancelado = false;
      parada.entregado_en = new Date().toISOString();
    }
    renderDetalleParadas(_rutaActiva.paradas);
    dibujarRutaEnMapa(_rutaActiva);
    actualizarProgreso(_rutaActiva.paradas);
    await cargarRutas();
    mostrarToastDesktop("Entrega registrada correctamente.", "ok");
  } catch (err) {
    console.error("[confirmarEntregaPendiente]", err);
    mostrarToastDesktop("Error de conexión al registrar la entrega.", "error");
  } finally {
    btnAceptar.disabled = false;
    cerrarConfirm();
  }
}

// ── Cancelar entrega ─────────────────────────────────────────────
function abrirConfirmCancelar(tipo, key) {
  _paradaPendiente = { tipo, key, accion: "cancelar" };
  const parada = (_rutaActiva.paradas || []).find(p => p.tipo === tipo && String(p.key) === String(key));
  document.getElementById("cdd-cancelar-msg").textContent =
    parada ? `¿Cancelar la entrega en "${parada.nombre}"?` : "¿Cancelar esta entrega?";
  document.getElementById("cdd-cancelar-overlay").classList.remove("hidden");
}

function cerrarConfirmCancelar() {
  _paradaPendiente = null;
  document.getElementById("cdd-cancelar-overlay").classList.add("hidden");
}

async function confirmarCancelarPendiente() {
  if (!_paradaPendiente || !_rutaActiva) { cerrarConfirmCancelar(); return; }
  const { tipo, key } = _paradaPendiente;
  const btnAceptar = document.getElementById("cdd-cancelar-aceptar");
  btnAceptar.disabled = true;

  try {
    const res = await fetch("/conductor/api/marcar-entrega", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ ruta_id: _rutaActiva.id, parada_tipo: tipo, parada_key: key, estado: "cancelada" }),
    });
    const data = await res.json();
    if (data.status !== "ok") {
      mostrarToastDesktop(data.mensaje || "No se pudo cancelar la entrega.", "error");
      return;
    }
    const parada = _rutaActiva.paradas.find(p => p.tipo === tipo && String(p.key) === String(key));
    if (parada) {
      parada.estado    = "cancelada";
      parada.entregado = false;
      parada.cancelado = true;
    }
    renderDetalleParadas(_rutaActiva.paradas);
    dibujarRutaEnMapa(_rutaActiva);
    actualizarProgreso(_rutaActiva.paradas);
    await cargarRutas();
    mostrarToastDesktop("Entrega cancelada.", "aviso");
  } catch (err) {
    console.error("[confirmarCancelarPendiente]", err);
    mostrarToastDesktop("Error de conexión al cancelar la entrega.", "error");
  } finally {
    btnAceptar.disabled = false;
    cerrarConfirmCancelar();
  }
}

// ── Utilidades ───────────────────────────────────────────────────
function h(s) { return String(s ?? "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;"); }

let _toastTimer = null;
function mostrarToastDesktop(msg, tipo = "ok") {
  const toast = document.getElementById("cdd-toast");
  toast.textContent = msg;
  toast.className = `cdd-toast cdd-toast--${tipo}`;
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => toast.classList.add("hidden"), 3200);
}
