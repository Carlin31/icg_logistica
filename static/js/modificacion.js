// ===== Modificación manual de rutas =====
// Lee desde asignaciones (MongoDB). Incluye sucursales + mayoristas.
// Permite cambiar vehículo por ruta, reordenar paradas y confirmar cada ruta.

'use strict';

// ── Estado global ──────────────────────────────────────────────
let _rutas           = [];
let _rutasFiltradas  = [];
let _pesos           = {};
let _sucDisponibles  = [];
let _vehiculos       = [];          // flota activa [{placas, abrev, capacidad_ton, ...}]
let _indiceActivo    = 0;
let _tiempos         = {};
let _confirmadas     = {};
let _diaActivo       = "__todos__";
let _logisticaId     = "";
let _mapa             = null;
let _rutaLayer        = null;
let _markersLayer     = null;
let _pendientesLayer  = null;
let _cancelarBatch    = false;
let _pendientes             = {};   // { num_tienda: {num_tienda, nombre, latitud, longitud, peso_kg} }
let _quitarResolve          = null;
let _crearRuta              = { dia: "", vehiculo: "", sucursales: [], query: "" };
let _historialAutoGuardado  = false;
let _configDias             = {};          // config_dias from MongoDB (per-day schedule)

const MSG_MOD = {
  recalcular: [
    "Consultando servicio OSRM…",
    "Calculando trayecto real por carretera…",
    "Procesando segmentos de la ruta…",
    "Actualizando tiempos estimados…",
  ],
  guardar: [
    "Guardando rutas modificadas…",
    "Registrando paradas por ruta…",
    "Actualizando tiempos y pesos…",
    "Finalizando guardado…",
  ],
};

const MIN_DESCARGA_POR_KG  = 0.1;
const MAX_DESCARGA_MIN     = 120;
const HORAS_EXTRA_RUTA_MIN = 0;

const DIAS_ORDEN = [
  { key: "lunes",     label: "Lun" },
  { key: "martes",    label: "Mar" },
  { key: "miercoles", label: "Mié" },
  { key: "jueves",    label: "Jue" },
  { key: "viernes",   label: "Vie" },
  { key: "sabado",    label: "Sáb" },
  { key: "domingo",   label: "Dom" },
];

function _horaSalidaDeRuta(ruta) {
  return _configDias[ruta?.dia]?.hora_salida || ruta?.hora_salida || "08:00";
}
function _horaLimiteDeRuta(ruta) {
  return _configDias[ruta?.dia]?.hora_limite || "18:00";
}

// ── Init ───────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", async () => {
  lucide.createIcons();
  const activa = await verificarSesionLogistica();
  if (!activa) return;
  await cargarDatos();
  bindEventos();
});

// Helper: fetch con timeout para evitar bloqueos indefinidos
function fetchWithTimeout(url, opts = {}, timeout = 8000) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('timeout')) , timeout);
    fetch(url, opts).then(res => {
      clearTimeout(timer);
      resolve(res);
    }).catch(err => {
      clearTimeout(timer);
      reject(err);
    });
  });
}

// Helper: fetch con reintentos y backoff exponencial
function fetchWithRetries(url, opts = {}, timeout = 8000, retries = 3, backoffBase = 500) {
  return new Promise(async (resolve, reject) => {
    let attempt = 0;
    while (attempt < retries) {
      try {
        const res = await fetchWithTimeout(url, opts, timeout);
        return resolve(res);
      } catch (err) {
        attempt++;
        if (attempt >= retries) return reject(err);
        const wait = backoffBase * Math.pow(2, attempt - 1);
        console.warn(`[fetchWithRetries] intento ${attempt} falló para ${url}: ${err.message}. reintentando en ${wait}ms`);
        await new Promise(r => setTimeout(r, wait));
      }
    }
  });
}

// ── Verificar sesión ───────────────────────────────────────────
async function verificarSesionLogistica() {
  try {
    const res  = await fetch('/api/activa');
    const data = await res.json();
    if (data.status !== 'ok') { redirigirAlMenu('No hay ninguna logística activa.'); return false; }
    _logisticaId = data.id || "";
    return true;
  } catch { redirigirAlMenu('Error de conexión.'); return false; }
}
function redirigirAlMenu(msg) {
  alert(`${msg}\n\nSerás redirigido al menú principal.`);
  window.location.href = '/';
}

// ── Persistencia de confirmadas (localStorage) ─────────────────
function _claveStorage() {
  return `mod_confirmadas_${_logisticaId}`;
}
function _guardarConfirmadas() {
  if (!_logisticaId) return;
  try { localStorage.setItem(_claveStorage(), JSON.stringify(_confirmadas)); } catch (_) {}
}

function _persistirConfirmadas() {
  // Persiste el estado actual en MongoDB (fire-and-forget)
  fetch("/modificacion/rutas-confirmadas", {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({ ruta_ids: Object.keys(_confirmadas) }),
  }).catch(err => console.warn("[rutas-confirmadas]", err));
}
function _restaurarConfirmadas() {
  if (!_logisticaId) return;
  try {
    const raw = localStorage.getItem(_claveStorage());
    if (raw) _confirmadas = JSON.parse(raw);
  } catch (_) { _confirmadas = {}; }
}

// ── Avance automático al siguiente día ─────────────────────────
function _siguienteDiaConPendientes(diaActual) {
  const idx = DIAS_ORDEN.findIndex(d => d.key === diaActual);
  for (let i = idx + 1; i < DIAS_ORDEN.length; i++) {
    const dia = DIAS_ORDEN[i].key;
    if (_rutas.some(r => r.dia === dia && !_confirmadas[r.id])) return dia;
  }
  return null;
}

function _ordenarRutas() {
  const orden = new Map(DIAS_ORDEN.map((d, i) => [d.key, i]));
  _rutas.sort((a, b) => {
    const da = orden.has(a.dia) ? orden.get(a.dia) : 99;
    const db = orden.has(b.dia) ? orden.get(b.dia) : 99;
    if (da !== db) return da - db;
    return String(a.nombre || "").localeCompare(String(b.nombre || ""), "es");
  });
}

function _aplicarPendientes(lista) {
  _pendientes = {};
  (lista || []).forEach(s => {
    if (s && s.num_tienda != null) _pendientes[String(s.num_tienda)] = s;
  });
}

function _actualizarConteoRutas() {
  const el = document.getElementById("cnt-rutas");
  if (el) el.textContent = _rutas.length;
}

// ── Bind de eventos ────────────────────────────────────────────
function bindEventos() {
  document.getElementById("btn-recalcular")?.addEventListener("click", recalcularActiva);
  document.getElementById("btn-confirmar")?.addEventListener("click", () => {
    const btn = document.getElementById("btn-confirmar");
    if (btn && btn.dataset.modo === "desconfirmar") desconfirmarActiva();
    else if (btn) confirmarActiva();
  });
  document.getElementById("btn-autorizar-todas")?.addEventListener("click", autorizarTodas);
  document.getElementById("btn-guardar-solo")?.addEventListener("click",   () => guardarTodo(false));
  document.getElementById("btn-guardar-seguir")?.addEventListener("click", () => guardarTodo(true));
  document.getElementById("btn-agregar-suc")?.addEventListener("click", abrirModalAgregar);
  document.getElementById("btn-crear-ruta")?.addEventListener("click", abrirModalCrearRuta);
  document.getElementById("btn-eliminar-ruta")?.addEventListener("click", eliminarRutaActiva);
  document.getElementById("modal-agregar-close")?.addEventListener("click", cerrarModalAgregar);
  document.getElementById("buscar-sucursal")?.addEventListener("input", filtrarDisponibles);
  document.getElementById("modal-agregar")?.addEventListener("click", (e) => {
    if (e.target.id === "modal-agregar") cerrarModalAgregar();
  });
  document.getElementById("modal-crear-close")?.addEventListener("click", cerrarModalCrearRuta);
  document.getElementById("btn-crear-cancel")?.addEventListener("click", cerrarModalCrearRuta);
  document.getElementById("btn-crear-confirm")?.addEventListener("click", crearRutaManual);
  document.getElementById("buscar-sucursal-crear")?.addEventListener("input", (e) => {
    _crearRuta.query = e.target.value;
    renderListaCrearSucursales();
  });
  document.getElementById("crear-dia")?.addEventListener("change", (e) => {
    _crearRuta.dia = e.target.value;
  });
  document.getElementById("crear-vehiculo")?.addEventListener("change", (e) => {
    _crearRuta.vehiculo = e.target.value;
    actualizarEstadoCrearRuta();
  });
  document.getElementById("modal-crear-ruta")?.addEventListener("click", (e) => {
    if (e.target.id === "modal-crear-ruta") cerrarModalCrearRuta();
  });
  document.getElementById("btn-calcular-todas")?.addEventListener("click", calcularTodasOSRM);
  document.getElementById("btn-cancelar-osrm")?.addEventListener("click", () => { _cancelarBatch = true; });

  // Modal de confirmación para quitar parada
  document.getElementById("quitar-cancel")?.addEventListener("click", () => {
    document.getElementById("modal-quitar-parada")?.classList.add("hidden");
    if (_quitarResolve) { _quitarResolve(false); _quitarResolve = null; }
  });
  document.getElementById("quitar-ok")?.addEventListener("click", () => {
    document.getElementById("modal-quitar-parada")?.classList.add("hidden");
    if (_quitarResolve) { _quitarResolve(true); _quitarResolve = null; }
  });
  document.getElementById("modal-quitar-parada")?.addEventListener("click", (e) => {
    if (e.target.id === "modal-quitar-parada") {
      document.getElementById("modal-quitar-parada")?.classList.add("hidden");
      if (_quitarResolve) { _quitarResolve(false); _quitarResolve = null; }
    }
  });

}

// ── Carga inicial ──────────────────────────────────────────────
async function cargarDatos() {
  const banner = document.getElementById("banner-cargando");
  banner.style.display = "flex";

  try {
    console.log('[cargarDatos] iniciando fetch de recursos con reintentos');
    // Usar fetch con reintentos y timeout para evitar bloqueo indefinido
    const retries = 3;
    const timeoutMs = 10000;
    const rutasPromise    = fetchWithRetries("/modificacion/rutas", {}, timeoutMs, retries).catch(e => ({ error: e }));
    const pesosPromise    = fetchWithRetries("/modificacion/pesos", {}, timeoutMs, retries).catch(e => ({ error: e }));
    const sucPromise      = fetchWithRetries("/modificacion/sucursales", {}, timeoutMs, retries).catch(e => ({ error: e }));
    const vehPromise      = fetchWithRetries("/modificacion/vehiculos", {}, timeoutMs, retries).catch(e => ({ error: e }));
    const horariosPromise = fetch("/modificacion/horarios-config").catch(() => null);

    const [rutasRes, pesosRes, sucRes, vehRes, horariosRes] = await Promise.all([rutasPromise, pesosPromise, sucPromise, vehPromise, horariosPromise]);

    if (rutasRes && rutasRes.error) throw rutasRes.error;
    if (pesosRes && pesosRes.error) throw pesosRes.error;
    if (sucRes && sucRes.error) throw sucRes.error;
    if (vehRes && vehRes.error) throw vehRes.error;

    if (rutasRes.status === 400 || (pesosRes && pesosRes.status === 400)) {
      redirigirAlMenu('Sin logística activa.');
      return;
    }

    const rutasData = await rutasRes.json();
    _pesos          = await pesosRes.json();
    _sucDisponibles = await sucRes.json();
    _vehiculos      = vehRes.ok ? await vehRes.json() : [];
    try { _configDias = (horariosRes?.ok) ? await horariosRes.json() : {}; } catch (_) { _configDias = {}; }

    _rutas       = rutasData.rutas || [];
    _logisticaId = rutasData.logistica_id || _logisticaId || "";

    if (rutasData.status !== "ok" || _rutas.length === 0) {
      banner.style.display = "none";
      document.getElementById("estado-vacio").style.display = "block";
      document.getElementById("resumen-fuentes").style.display = "none";
      document.getElementById("filtro-dias").style.display = "none";
      document.getElementById("nav-rutas").style.display = "none";
      document.getElementById("panel-principal").style.display = "none";
      const btnDel = document.getElementById("btn-eliminar-ruta");
      if (btnDel) btnDel.disabled = true;
      lucide.createIcons();
      return;
    }

    // Confirmadas: MongoDB tiene prioridad; localStorage como respaldo
    const serverConf = rutasData.rutas_confirmadas || [];
    if (serverConf.length > 0) {
      _confirmadas = {};
      serverConf.forEach(id => { _confirmadas[id] = true; });
      _guardarConfirmadas();   // sincronizar localStorage con el servidor
    } else {
      _restaurarConfirmadas(); // sin datos en servidor, usar localStorage
    }

    // Restaurar sucursales pendientes desde MongoDB (fuente de verdad)
    _aplicarPendientes(rutasData.sucursales_pendientes || []);
    _actualizarConteoRutas();
    document.getElementById("resumen-fuentes").style.display = "flex";

    inicializarMapa();
    renderPendientesLayer();
    renderPanelPendientes();
    actualizarStatusOSRM();
    renderFiltroDias();

    banner.style.display = "none";
    document.getElementById("filtro-dias").style.display = "flex";
    lucide.createIcons();

    const primerDia = _rutas.find(r => r.dia === "lunes") ? "lunes"
                    : (DIAS_ORDEN.find(d => _rutas.some(r => r.dia === d.key))?.key || "__todos__");
    aplicarFiltroDia(primerDia);
    console.log('[cargarDatos] finalizado, primer dia aplicado:', primerDia);

  } catch (err) {
    console.error("[cargarDatos]", err);
    banner.style.display = "none";
    document.getElementById("estado-vacio").style.display = "block";
    lucide.createIcons();
  }
}

// ── Helpers de vehículo ────────────────────────────────────────

/**
 * Devuelve el objeto de vehículo de `_vehiculos` para las placas dadas.
 * Los datos de ocupación vienen directamente del servidor (MongoDB).
 */
function _vehiculoPorPlacas(placas) {
  return _vehiculos.find(v => v.placas === placas) || null;
}

// ── OSRM: cálculo individual ───────────────────────────────────
async function calcularOSRMParaRuta(ruta) {
  const paradas = _paradasDeRuta(ruta);
  if (!paradas.length) {
    _tiempos[ruta.id] = tiempoVacio(); return;
  }
  const statusEl = document.getElementById("osrm-ruta-status");
  statusEl.style.display = "flex";
  try {
    const res = await fetch("/modificacion/recalcular-tiempos", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paradas, hora_salida: _horaSalidaDeRuta(ruta) }),
    });
    if (res.status === 400) { redirigirAlMenu('Sin logística activa.'); return; }
    _tiempos[ruta.id] = res.ok ? await res.json() : { ...tiempoVacio(), origen_tiempo: "error" };
  } catch (err) {
    console.error(`[OSRM:${ruta.id}]`, err);
    _tiempos[ruta.id] = { ...tiempoVacio(), origen_tiempo: "error" };
  }
  statusEl.style.display = "none";
  actualizarStatusOSRM();
}

function tiempoVacio() {
  return {
    traslado_min: 0, descarga_min: 0, extra_min: HORAS_EXTRA_RUTA_MIN,
    total_min: 0, distancia_km: 0,
    origen_tiempo: "pendiente", geometry: [], hora_regreso: "—", matriz: null,
  };
}

function rutaTieneOSRM(rutaId) {
  const t = _tiempos[rutaId];
  return t && t.origen_tiempo && t.origen_tiempo !== "pendiente" && t.origen_tiempo !== "error";
}

// ── OSRM: cálculo batch ────────────────────────────────────────
async function calcularTodasOSRM() {
  const btn    = document.getElementById("btn-calcular-todas");
  btn.disabled = true;
  _cancelarBatch = false;
  const banner = document.getElementById("banner-osrm");
  const texto  = document.getElementById("banner-osrm-texto");
  const fill   = document.getElementById("osrm-progress-fill");
  banner.style.display = "flex";

  const pendientes = _rutas.filter(r => !rutaTieneOSRM(r.id) && _paradasDeRuta(r).length > 0);
  const total = pendientes.length;
  let completadas = 0;

  for (const ruta of pendientes) {
    if (_cancelarBatch) break;
    texto.textContent = `Calculando ${ruta.nombre} (${completadas + 1}/${total})…`;
    fill.style.width  = `${(completadas / total) * 100}%`;
    try {
      const res = await fetch("/modificacion/recalcular-tiempos", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ paradas: _paradasDeRuta(ruta), hora_salida: _horaSalidaDeRuta(ruta) }),
      });
      if (res.status === 400) { redirigirAlMenu('Sin logística activa.'); return; }
      _tiempos[ruta.id] = res.ok ? await res.json() : { ...tiempoVacio(), origen_tiempo: "error" };
    } catch {
      _tiempos[ruta.id] = { ...tiempoVacio(), origen_tiempo: "error" };
    }
    completadas++;
    actualizarStatusOSRM();
    renderNavRutas();
    if (_rutasFiltradas[_indiceActivo]?.id === ruta.id) renderContenidoRuta(_rutasFiltradas[_indiceActivo]);
    if (!_cancelarBatch && completadas < total) await sleep(1200);
  }

  fill.style.width = "100%";
  texto.textContent = _cancelarBatch
    ? `Detenido. ${completadas} de ${total} calculadas.`
    : `${completadas} rutas calculadas con OSRM.`;
  setTimeout(() => { banner.style.display = "none"; btn.disabled = false; lucide.createIcons(); }, 2000);
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

function actualizarStatusOSRM() {
  const total      = _rutas.filter(r => _paradasDeRuta(r).length > 0).length;
  const calculadas = _rutas.filter(r => rutaTieneOSRM(r.id)).length;
  const dot        = document.querySelector(".osrm-dot");
  const text       = document.getElementById("osrm-status-text");
  const btn        = document.getElementById("btn-calcular-todas");
  text.textContent = `OSRM: ${calculadas} / ${total} calculadas`;
  if (dot) dot.className = "osrm-dot " + (calculadas === total ? "completo" : calculadas > 0 ? "calculando" : "pendiente");
  if (btn) {
    btn.disabled = calculadas === total;
    btn.innerHTML = calculadas === total
      ? '<i data-lucide="check"></i> Todas calculadas'
      : `<i data-lucide="map"></i> Calcular ${total - calculadas} rutas`;
    lucide.createIcons();
  }
}

// ── Mapa Leaflet ───────────────────────────────────────────────
function inicializarMapa() {
  _mapa = L.map("mapa", { zoomControl: true }).setView([18.87, -96.95], 9);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a>', maxZoom: 18,
  }).addTo(_mapa);
  _markersLayer    = L.layerGroup().addTo(_mapa);
  _rutaLayer       = L.layerGroup().addTo(_mapa);
  _pendientesLayer = L.layerGroup().addTo(_mapa);
  _mapa.on("popupopen", () => lucide.createIcons());
}

function actualizarMapa(ruta) {
  if (!_mapa) return;
  _markersLayer.clearLayers();
  _rutaLayer.clearLayers();
  renderPendientesLayer();   // siempre actualizar marcadores pendientes
  const tiempos = _tiempos[ruta.id] || {};
  const paradas = _paradasDeRuta(ruta);
  const bounds  = [];

  if (tiempos.matriz) {
    const [lat, lon] = tiempos.matriz;
    bounds.push([lat, lon]);
    L.marker([lat, lon], {
      icon: L.divIcon({ className: "", html: '<div class="marker-matriz">M</div>', iconSize: [32,32], iconAnchor: [16,16] }),
    }).bindPopup("<b>Matriz / Bodega</b>").addTo(_markersLayer);
  }

  paradas.forEach((p, i) => {
    if (p.latitud == null || p.longitud == null) return;
    bounds.push([p.latitud, p.longitud]);
    const esMay = p.tipo === "mayorista";
    const peso  = esMay ? (p.peso_kg || 0) : (_pesos[String(p.num_tienda)] || p.peso_kg || 0);
    const color = esMay ? "#f97316" : "#2563eb";
    const html  = `<div class="marker-orden" style="background:${color}">${i + 1}</div>`;
    L.marker([p.latitud, p.longitud], {
      icon: L.divIcon({ className: "", html, iconSize: [28,28], iconAnchor: [14,14] }),
    }).bindPopup(`<b>${i+1}. ${h(p.nombre)}</b>${peso ? `<br>${peso.toLocaleString("es-MX")} kg` : ""}`).addTo(_markersLayer);
  });

  if (tiempos.geometry && tiempos.geometry.length > 1) {
    const latlngs = tiempos.geometry.map(c => [c[1], c[0]]);
    L.polyline(latlngs, { color: "#2563eb", weight: 4, opacity: 0.85, lineJoin: "round" }).addTo(_rutaLayer);
  } else if (paradas.length > 0 && tiempos.matriz) {
    const pts = [tiempos.matriz];
    paradas.forEach(p => { if (p.latitud != null && p.longitud != null) pts.push([p.latitud, p.longitud]); });
    pts.push(tiempos.matriz);
    L.polyline(pts, { color: "#94a3b8", weight: 2, opacity: 0.6, dashArray: "8 6" }).addTo(_rutaLayer);
  }

  // Incluir pendientes en los límites del mapa para que siempre sean visibles
  Object.values(_pendientes).forEach(suc => {
    if (suc.latitud != null && suc.longitud != null) bounds.push([suc.latitud, suc.longitud]);
  });

  if (bounds.length > 0) _mapa.fitBounds(bounds, { padding: [40,40], maxZoom: 13 });
}

// ── Sucursales pendientes (quitadas de rutas) ──────────────────

function renderPendientesLayer() {
  if (!_pendientesLayer) return;
  _pendientesLayer.clearLayers();
  Object.values(_pendientes).forEach(suc => {
    if (suc.latitud == null || suc.longitud == null) return;
    const peso = _pesos[String(suc.num_tienda)] || suc.peso_kg || 0;
    L.marker([suc.latitud, suc.longitud], {
      icon: L.divIcon({
        className: "",
        html: `<div class="marker-pendiente">!</div>`,
        iconSize: [30, 30],
        iconAnchor: [15, 15],
      }),
      zIndexOffset: 600,
    }).bindPopup(
      `<b style="color:#ea580c"><i data-lucide="triangle-alert" style="width:13px;height:13px;vertical-align:middle"></i> Sin ruta: ${h(suc.nombre)}</b>` +
      (peso ? `<br>${peso.toLocaleString("es-MX")} kg` : "") +
      `<br><span style="font-size:0.8em;color:#9a3412">Usa "+ Parada" para reasignarla</span>`
    ).addTo(_pendientesLayer);
  });
}

function renderPanelPendientes() {
  const panel = document.getElementById("panel-pendientes");
  if (!panel) return;
  const lista = Object.values(_pendientes);
  if (lista.length === 0) { panel.style.display = "none"; return; }

  panel.style.display = "flex";
  panel.innerHTML = `
    <div class="pend-icono"><i data-lucide="alert-circle"></i></div>
    <div class="pend-cuerpo">
      <span class="pend-titulo">${lista.length} sucursal${lista.length !== 1 ? "es" : ""} sin ruta asignada</span>
      <div class="pend-chips">
        ${lista.map(s => `
          <span class="pend-chip" title="${h(nombreSucursalLabel(s))} — ${(_pesos[String(s.num_tienda)] || s.peso_kg || 0).toLocaleString("es-MX")} kg">
            ${h(nombreSucursalLabel(s))}
          </span>`).join("")}
      </div>
    </div>`;
  lucide.createIcons();
}

// ── Filtro por día ─────────────────────────────────────────────
function renderFiltroDias() {
  const conteo = {};
  _rutas.forEach(r => { conteo[r.dia] = (conteo[r.dia] || 0) + 1; });
  const container = document.getElementById("filtro-dias");
  let html = ``;
  DIAS_ORDEN.forEach(({ key, label }) => {
    if (!conteo[key]) return;
    html += `<button class="filtro-dia-btn" data-dia="${key}">${label} <span class="cnt">(${conteo[key]})</span></button>`;
  });
  container.innerHTML = html;
  container.querySelectorAll(".filtro-dia-btn").forEach(btn => {
    btn.addEventListener("click", () => aplicarFiltroDia(btn.dataset.dia));
  });
}

function aplicarFiltroDia(diaKey) {
  _diaActivo = diaKey;
  document.querySelectorAll(".filtro-dia-btn").forEach(btn =>
    btn.classList.toggle("activo", btn.dataset.dia === diaKey));
  _rutasFiltradas = diaKey === "__todos__" ? _rutas : _rutas.filter(r => r.dia === diaKey);
  _indiceActivo = 0;

  const nav    = document.getElementById("nav-rutas");
  const panel  = document.getElementById("panel-principal");

  if (diaKey === "__todos__") {
    nav.style.display   = "none";
    panel.style.display = "none";
    return;
  }

  nav.style.display = "flex";
  renderNavRutas();
  if (_rutasFiltradas.length === 0) {
    panel.style.display = "none";
    const btnDel = document.getElementById("btn-eliminar-ruta");
    if (btnDel) btnDel.disabled = true;
    return;
  }
  seleccionarRuta(0);
}

// ── Navegador de rutas ─────────────────────────────────────────
function renderNavRutas() {
  const nav = document.getElementById("nav-rutas");
  if (_rutasFiltradas.length === 0) {
    nav.innerHTML = '<div style="color:#94a3b8;font-size:0.82rem;padding:8px">No hay rutas en este día.</div>';
    return;
  }
  nav.innerHTML = _rutasFiltradas.map((ruta, i) => {
    const n        = _paradasDeRuta(ruta).length;
    const tiene    = rutaTieneOSRM(ruta.id);
    const esError  = _tiempos[ruta.id]?.origen_tiempo === "error";
    const dotClass = tiene ? "osrm-ok" : esError ? "osrm-fail" : "osrm-pending";
    const dotText  = tiene ? "OSRM" : esError ? "Error" : "Pendiente";
    return `
      <button class="nav-ruta-btn${i === _indiceActivo ? " activo" : ""}${_confirmadas[ruta.id] ? " confirmada" : ""}" data-idx="${i}">
        <div class="nav-ruta-nombre">${h(ruta.nombre)}</div>
        <div class="nav-ruta-dia">${capitalizar(ruta.dia)} · ${n} parada${n !== 1 ? "s" : ""}</div>
        <div class="nav-ruta-info">${ruta.vehiculo_abrev || "—"} · ${ruta.vehiculo_placas || "—"}</div>
        <div class="nav-ruta-osrm"><span class="mini-dot ${dotClass}"></span> ${dotText}</div>
      </button>`;
  }).join("");
  nav.querySelectorAll(".nav-ruta-btn").forEach(btn => {
    btn.addEventListener("click", () => seleccionarRuta(Number(btn.dataset.idx)));
  });
  actualizarProgreso();
}

// ── Seleccionar ruta ───────────────────────────────────────────
async function seleccionarRuta(idx) {
  if (idx < 0 || idx >= _rutasFiltradas.length) return;
  _indiceActivo = idx;
  const ruta = _rutasFiltradas[idx];

  if (!_mapa) inicializarMapa();

  document.getElementById("panel-principal").style.display = "grid";
  const btnDel = document.getElementById("btn-eliminar-ruta");
  if (btnDel) btnDel.disabled = false;

  document.querySelectorAll(".nav-ruta-btn").forEach((btn, i) =>
    btn.classList.toggle("activo", i === idx));

  document.getElementById("titulo-ruta").textContent = ruta.nombre;
  const pesoTotal = calcularPesoRuta(ruta);
  document.getElementById("meta-ruta").innerHTML = `
    <span class="meta-item"><i data-lucide="calendar-days" style="width:12px;height:12px"></i> ${capitalizar(ruta.dia)}</span>
    <span class="meta-item"><i data-lucide="package" style="width:12px;height:12px"></i> ${pesoTotal.toLocaleString("es-MX")} kg</span>
    ${ruta.parte ? `<span class="meta-item">Parte ${ruta.parte} de ${ruta.total_partes}</span>` : ""}
  `;

  const btnConf = document.getElementById("btn-confirmar");
  if (_confirmadas[ruta.id]) {
    btnConf.innerHTML = '<i data-lucide="x-circle"></i> Desconfirmar';
    btnConf.classList.add("desconfirmar");
    btnConf.classList.remove("confirmada");
    btnConf.disabled = false;
    btnConf.dataset.modo = "desconfirmar";
  } else {
    btnConf.innerHTML = '<i data-lucide="check"></i> Confirmar ruta';
    btnConf.classList.remove("desconfirmar");
    btnConf.classList.remove("confirmada");
    btnConf.disabled = false;
    btnConf.dataset.modo = "confirmar";
  }

  renderSelectorVehiculo(ruta);
  renderSelectorDia(ruta);
  renderParadas(ruta);

  if (!rutaTieneOSRM(ruta.id)) {
    renderResumenTiempos(ruta);
    renderIndicadores(ruta);
    actualizarMapa(ruta);
    await calcularOSRMParaRuta(ruta);
    renderNavRutas();
    if (_rutasFiltradas[_indiceActivo]?.id === ruta.id) renderContenidoRuta(ruta);
  } else {
    renderContenidoRuta(ruta);
  }

  lucide.createIcons();
  setTimeout(() => _mapa && _mapa.invalidateSize(), 100);
}

function renderContenidoRuta(ruta) {
  renderResumenTiempos(ruta);
  renderIndicadores(ruta);
  actualizarMapa(ruta);
}

// ── Selector de vehículo ───────────────────────────────────────

/**
 * Abrevia un nombre de día a 2-3 letras con mayúscula inicial.
 * "miercoles" → "Mié", "lunes" → "Lun", etc.
 */
const _ABREV_DIA = {
  lunes: "Lun", martes: "Mar", miercoles: "Mié",
  jueves: "Jue", viernes: "Vie", sabado: "Sáb", domingo: "Dom",
};
function _abrevDia(d) { return _ABREV_DIA[d] || capitalizar(d.slice(0, 3)); }

function renderSelectorVehiculo(ruta) {
  const zona = document.getElementById("zona-vehiculo");
  if (!zona) return;

  const placasActual = ruta.vehiculo_placas || "";
  const pesoKg       = calcularPesoRuta(ruta);
  const pesoTon      = pesoKg / 1000;

  // Utilización de la ruta con el vehículo actual
  const vehActual    = _vehiculos.find(v => v.placas === placasActual);
  const pctActual    = vehActual && vehActual.capacidad_ton > 0
    ? (pesoTon / vehActual.capacidad_ton) * 100 : null;

  // Ordenar: disponibles primero (más cercano al 100%), ocupados al fondo
  const ordenados = [..._vehiculos].sort((a, b) => {
    const aOcu = !!(a.ocupacion || {})[ruta.dia] && a.placas !== placasActual;
    const bOcu = !!(b.ocupacion || {})[ruta.dia] && b.placas !== placasActual;
    if (aOcu !== bOcu) return aOcu ? 1 : -1;
    const pA = a.capacidad_ton > 0 ? Math.abs((pesoTon / a.capacidad_ton) * 100 - 100) : 999;
    const pB = b.capacidad_ton > 0 ? Math.abs((pesoTon / b.capacidad_ton) * 100 - 100) : 999;
    return pA - pB;
  });

  // Mejor vehículo disponible (para el chip "Mejor ajuste")
  const mejorDisp = ordenados.find(v =>
    v.placas !== placasActual &&
    !(v.ocupacion || {})[ruta.dia] &&
    v.capacidad_ton > 0
  );
  const mejorPlacas = mejorDisp?.placas || "";

  const opcionesHTML = ordenados.map(v => {
    const esActual       = v.placas === placasActual;
    const ocu            = v.ocupacion || {};
    const ocupadoEsteDia = !esActual && !!ocu[ruta.dia];
    const capKg          = (v.capacidad_ton || 0) * 1000;
    const pctRuta        = capKg > 0 ? (pesoKg / capKg) * 100 : 0;
    const esMejor        = v.placas === mejorPlacas;

    // Color de la barra de utilización para esta ruta
    const utilClass = pctRuta >= 90 && pctRuta <= 110 ? "util-ideal"
                    : pctRuta >= 75 && pctRuta <= 125  ? "util-ok"
                    : pctRuta > 125                     ? "util-sobre"
                    : "util-sub";

    const pctLabel = capKg > 0 ? `${pctRuta.toFixed(0)}%` : "—";

    // Badge de disponibilidad
    let badge = "";
    if (esActual) {
      badge = `<span class="veh-badge veh-badge--actual"><i data-lucide="check" style="width:10px;height:10px"></i> Actual</span>`;
    } else if (ocupadoEsteDia) {
      const info = ocu[ruta.dia];
      badge = `<span class="veh-badge veh-badge--ocupado" title="${h(info?.ruta_nombre || '')}"><i data-lucide="x-circle" style="width:10px;height:10px"></i> Ocupado</span>`;
    } else if (esMejor) {
      badge = `<span class="veh-badge veh-badge--mejor">✦ Mejor ajuste</span>`;
    }

    const tooltip = `${v.abrev || v.descripcion} · ${v.capacidad_ton} ton · ${pctRuta.toFixed(1)}% utilización para esta ruta${v.chofer ? " · " + v.chofer : ""}`;

    return `
      <div class="veh-opcion${esActual ? " veh-opcion--actual" : ""}${ocupadoEsteDia ? " veh-opcion--ocupado" : ""}${esMejor && !esActual ? " veh-opcion--mejor" : ""}"
           data-placas="${h(v.placas)}" title="${h(tooltip)}">
        <div class="veh-opcion-top">
          <div class="veh-opcion-info">
            <span class="veh-nombre">${h(v.abrev || v.descripcion)}</span>
            <span class="veh-detalle">${h(v.placas)} · ${v.capacidad_ton} ton${v.chofer ? " · " + h(v.chofer) : ""}</span>
          </div>
          ${badge}
        </div>
        <div class="veh-util-wrap">
          <div class="veh-util-track">
            <span class="veh-util-bar ${utilClass}" style="width:${Math.min(pctRuta, 130)}%"></span>
            <span class="veh-util-mark100"></span>
          </div>
          <span class="veh-util-label ${utilClass}">${pctLabel}</span>
        </div>
        <div class="veh-util-sub">${pesoKg.toLocaleString("es-MX")} kg de ${(capKg).toLocaleString("es-MX")} kg cap.</div>
      </div>`;
  }).join("");

  // Resumen de utilización actual
  let resumenHTML = "";
  if (pctActual !== null) {
    const rClass = pctActual >= 90 && pctActual <= 110 ? "util-ideal"
                 : pctActual >= 75 && pctActual <= 125  ? "util-ok"
                 : pctActual > 125                       ? "util-sobre"
                 : "util-sub";
    const rLabel = pctActual >= 90 && pctActual <= 110 ? "Utilización óptima"
                 : pctActual >= 75 && pctActual <= 125  ? "Utilización aceptable"
                 : pctActual > 125                       ? "Sobrecargado"
                 : "Subutilizado";
    resumenHTML = `
      <div class="veh-resumen-util ${rClass}">
        <div class="veh-resumen-row">
          <span class="veh-resumen-carga"><i data-lucide="package" style="width:11px;height:11px"></i> ${pesoKg.toLocaleString("es-MX")} kg de carga</span>
          <span class="veh-resumen-pct ${rClass}">${pctActual.toFixed(0)}% utilizado</span>
        </div>
        <div class="veh-resumen-bar-track">
          <div class="veh-resumen-bar-fill ${rClass}" style="width:${Math.min(pctActual, 130)}%"></div>
          <div class="veh-resumen-bar-mark"></div>
        </div>
        <div class="veh-resumen-hint ${rClass}">${rLabel} · ${(vehActual.capacidad_ton * 1000).toLocaleString("es-MX")} kg de capacidad</div>
      </div>`;
  }

  zona.innerHTML = `
    <div class="vehiculo-selector">
      <div class="vehiculo-selector-label">
        <i data-lucide="truck"></i> Vehículo
        <span class="veh-actual-nombre">${h(ruta.vehiculo_abrev || "Sin asignar")}</span>
        <span class="veh-actual-placas">${h(placasActual)}</span>
      </div>
      ${resumenHTML}
      <div class="veh-lista" id="veh-lista-${h(ruta.id)}">
        ${opcionesHTML || '<div class="veh-vacio">No hay vehículos disponibles.</div>'}
      </div>
    </div>`;

  zona.querySelectorAll(".veh-opcion").forEach(el => {
    el.addEventListener("click", () => cambiarVehiculo(ruta, el.dataset.placas));
  });
  lucide.createIcons();
}

function cambiarVehiculo(ruta, nuevasPlacas) {
  const vehiculo = _vehiculos.find(v => v.placas === nuevasPlacas);
  if (!vehiculo) return;

  const placasAntes = ruta.vehiculo_placas || "";

  // Liberar el vehículo anterior en el mapa de ocupación en memoria
  if (placasAntes && placasAntes !== nuevasPlacas) {
    const vAnterior = _vehiculos.find(v => v.placas === placasAntes);
    if (vAnterior && vAnterior.ocupacion) {
      delete vAnterior.ocupacion[ruta.dia];
      // Recalcular métricas del anterior
      _recalcularMetricasVehiculo(vAnterior);
    }
  }

  // Ocupar el nuevo vehículo en el mapa de ocupación en memoria
  if (!vehiculo.ocupacion) vehiculo.ocupacion = {};
  vehiculo.ocupacion[ruta.dia] = { ruta_id: ruta.id, ruta_nombre: ruta.nombre };
  _recalcularMetricasVehiculo(vehiculo);

  ruta.vehiculo_placas = vehiculo.placas;
  ruta.vehiculo_abrev  = vehiculo.abrev;
  ruta.capacidad_ton   = vehiculo.capacidad_ton;

  // Persistir cambio de vehículo en MongoDB
  fetch("/modificacion/actualizar-vehiculo", {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({
      ruta_id:              ruta.id,
      dia:                  ruta.dia,
      vehiculo_placas:      vehiculo.placas,
      vehiculo_abreviatura: vehiculo.abrev,
      capacidad_ton:        vehiculo.capacidad_ton,
    }),
  }).catch(err => console.warn("[actualizar-vehiculo]", err));

  const eraConfirmada = !!_confirmadas[ruta.id];
  if (eraConfirmada) _guardarHistorialRuta(ruta);
  renderSelectorVehiculo(ruta);
  renderIndicadores(ruta);
  renderNavRutas();
  mostrarToastMod(`Vehículo cambiado a ${vehiculo.abrev || vehiculo.placas}`, "ok");
}

/**
 * Recalcula pct_semana / dias_libres / dias_ocupados en memoria tras un
 * cambio de vehículo, para mantener consistencia sin recargar del servidor.
 */
function _recalcularMetricasVehiculo(v) {
  const diasHabiles = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado"];
  const ocu = v.ocupacion || {};
  v.dias_ocupados = Object.keys(ocu);
  v.dias_libres   = diasHabiles.filter(d => !ocu[d]);
  v.pct_semana    = Math.round(diasHabiles.filter(d => !!ocu[d]).length / diasHabiles.length * 1000) / 10;
}

// ── Resumen de tiempos ─────────────────────────────────────────
function renderResumenTiempos(ruta) {
  const t = _tiempos[ruta.id] || {};
  const origenClass = t.origen_tiempo === "osrm" ? "osrm" : t.origen_tiempo === "haversine_fallback" ? "haversine" : "pendiente";
  const origenLabel = t.origen_tiempo === "osrm"
    ? '<i data-lucide="route" style="width:11px;height:11px"></i> OSRM real'
    : t.origen_tiempo === "haversine_fallback"
    ? '<i data-lucide="ruler" style="width:11px;height:11px"></i> Haversine'
    : '<i data-lucide="clock" style="width:11px;height:11px"></i> Pendiente';
  document.getElementById("resumen-tiempos").innerHTML = `
    <div class="tiempo-celda"><div class="t-label">Conducción</div><div class="t-valor">${formatMin(t.traslado_min)}</div></div>
    <div class="tiempo-celda"><div class="t-label">Descarga</div><div class="t-valor">${formatMin(t.descarga_min)}</div></div>
    <div class="tiempo-celda"><div class="t-label">Distancia</div><div class="t-valor">${t.distancia_km ? t.distancia_km + " km" : "…"}</div></div>
    <div class="tiempo-celda"><div class="t-label">Fuente</div><div class="t-valor"><span class="origen-badge ${origenClass}">${origenLabel}</span></div></div>
  `;
  lucide.createIcons();
}

function renderIndicadores(ruta) {
  const zona   = document.getElementById("zona-indicadores");
  const t      = _tiempos[ruta.id] || {};
  const pesoKg = calcularPesoRuta(ruta);
  let capTon   = ruta.capacidad_ton;
  if (!capTon && ruta.pct_utilizacion > 0 && pesoKg > 0) {
    capTon = parseFloat(((pesoKg / 1000) / (ruta.pct_utilizacion / 100)).toFixed(2));
  }
  capTon = capTon || 2.5;
  const pct      = (pesoKg / 1000 / capTon) * 100;
  const barClass = pct <= 100 ? "verde" : pct <= 120 ? "naranja" : "rojo";
  const horaReg    = t.hora_regreso || ruta.hora_regreso || "—";
  const horaSalida = _horaSalidaDeRuta(ruta);
  const horaLimite = _horaLimiteDeRuta(ruta);
  const cumple     = horaReg === "—"
    ? (ruta.cumple_horario !== false)
    : horaReg <= horaLimite;

  zona.innerHTML = `
    <div class="cap-bar-wrap">
      <div class="cap-bar-label">
        <span>Utilización: ${pct.toFixed(1)}%</span>
        <span>${(pesoKg / 1000).toFixed(2)} / ${capTon} ton</span>
      </div>
      <div class="cap-bar"><div class="cap-bar-fill ${barClass}" style="width:${Math.min(pct,100)}%"></div></div>
    </div>
    <div class="hora-regreso">
      <span>Salida: ${horaSalida} · Regreso estimado:</span>
      <span class="badge-hora ${cumple ? "ok" : "tarde"}">${horaReg}</span>
      ${!cumple ? '<span style="font-size:0.72rem;color:#991b1b;display:inline-flex;align-items:center;gap:3px"><i data-lucide="triangle-alert" style="width:11px;height:11px"></i> Fuera de horario</span>' : ""}
    </div>
  `;
  lucide.createIcons();
}

function _actualizarIndicadoresPeso(ruta) {
  const pesoTotal = calcularPesoRuta(ruta);
  const metaEl = document.getElementById("meta-ruta");
  if (metaEl) {
    metaEl.innerHTML = `
      <span class="meta-item"><i data-lucide="calendar-days" style="width:12px;height:12px"></i> ${capitalizar(ruta.dia)}</span>
      <span class="meta-item"><i data-lucide="package" style="width:12px;height:12px"></i> ${pesoTotal.toLocaleString("es-MX")} kg</span>
      ${ruta.parte ? `<span class="meta-item">Parte ${ruta.parte} de ${ruta.total_partes}</span>` : ""}
    `;
    lucide.createIcons();
  }
  renderSelectorVehiculo(ruta);
  renderIndicadores(ruta);
  renderResumenTiempos(ruta);
}

// ── Lista de paradas (sucursales + mayoristas interleaved) ─────

/**
 * Devuelve la secuencia combinada y ordenada de sucursales + mayoristas.
 * Cada elemento tiene `tipo`, `orden` y los campos propios.
 */
function _paradasDeRuta(ruta) {
  const sucs = (ruta.sucursales || []).map(s => ({ ...s, tipo: "sucursal" }));
  const mays = (ruta.mayoristas  || []).map(m => ({ ...m, tipo: "mayorista" }));
  return [...sucs, ...mays].sort((a, b) => (a.orden ?? 9999) - (b.orden ?? 9999));
}

function renderParadas(ruta) {
  const lista   = document.getElementById("lista-sucursales");
  const paradas = _paradasDeRuta(ruta);

  if (paradas.length === 0) {
    lista.innerHTML = '<div class="lista-sucursales-vacia">Esta ruta no tiene paradas asignadas.</div>';
    return;
  }

  lista.innerHTML = paradas.map((p, i) => {
    if (p.tipo === "mayorista") {
      return `
        <div class="suc-item may-item" data-tipo="mayorista" data-idx="${i}" draggable="true">
          <span class="suc-grip"><i data-lucide="grip-vertical" style="width:14px;height:14px"></i></span>
          <span class="suc-orden may-orden">${i + 1}</span>
          <div class="suc-info">
            <div class="suc-nombre">${h(p.nombre || `Cliente ${p.id_cliente}`)}</div>
            <div class="suc-detalle may-detalle">
              Mayorista${p.peso_kg > 0 ? ` · ${p.peso_kg.toLocaleString("es-MX")} kg` : ""}
              ${p.latitud == null ? ' · <span style="color:#ef4444;font-size:0.65rem">sin coords</span>' : ""}
            </div>
          </div>
          <span class="may-peso">${p.peso_kg > 0 ? p.peso_kg.toLocaleString("es-MX") + " kg" : "—"}</span>
        </div>`;
    }
    // Sucursal
    const peso    = _pesos[String(p.num_tienda)] || p.peso_kg || 0;
    const descMin = Math.min(peso * MIN_DESCARGA_POR_KG, MAX_DESCARGA_MIN).toFixed(0);
    const sinC    = p.latitud == null || p.longitud == null;
    return `
      <div class="suc-item" data-tipo="sucursal" data-idx="${i}" draggable="true">
        <span class="suc-grip"><i data-lucide="grip-vertical" style="width:14px;height:14px"></i></span>
        <span class="suc-orden">${i + 1}</span>
        <div class="suc-info">
          <div class="suc-nombre">${h(nombreSucursalLabel(p))}${sinC ? ' <span style="color:#ef4444;font-size:0.65rem">sin coords</span>' : ''}</div>
          <div class="suc-detalle">#${p.num_tienda} · ${peso.toLocaleString("es-MX")} kg · ~${descMin} min descarga</div>
        </div>
        <button class="suc-quitar" data-idx="${i}" title="Quitar de la ruta"><i data-lucide="x" style="width:13px;height:13px"></i></button>
      </div>`;
  }).join("");

  setupDragAndDropParadas(lista, ruta);

  lista.querySelectorAll(".suc-quitar").forEach(btn => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      quitarParada(ruta, Number(btn.dataset.idx));
    });
  });

  lucide.createIcons();
}

// ── Drag & drop para la lista combinada ───────────────────────
function setupDragAndDropParadas(container, ruta) {
  let dragIdx = null;

  container.querySelectorAll(".suc-item").forEach(item => {
    item.addEventListener("dragstart", (e) => {
      dragIdx = Number(item.dataset.idx);
      item.classList.add("dragging");
      e.dataTransfer.effectAllowed = "move";
    });
    item.addEventListener("dragend", () => {
      item.classList.remove("dragging");
      container.querySelectorAll(".suc-item").forEach(el => el.classList.remove("drag-over"));
    });
    item.addEventListener("dragover", (e) => {
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      container.querySelectorAll(".suc-item").forEach(el => el.classList.remove("drag-over"));
      item.classList.add("drag-over");
    });
    item.addEventListener("dragleave", () => item.classList.remove("drag-over"));
    item.addEventListener("drop", (e) => {
      e.preventDefault();
      const dropIdx = Number(item.dataset.idx);
      if (dragIdx === null || dragIdx === dropIdx) return;

      // Reordenar la lista combinada
      const paradas = _paradasDeRuta(ruta);
      const [moved] = paradas.splice(dragIdx, 1);
      paradas.splice(dropIdx, 0, moved);
      paradas.forEach((p, i) => { p.orden = i + 1; });

      // Sincronizar de vuelta a sucursales y mayoristas
      _sincronizarParadas(ruta, paradas);

      delete _tiempos[ruta.id];
      renderParadas(ruta);
      actualizarStatusOSRM();
      renderNavRutas();
      calcularOSRMParaRuta(ruta).then(() => {
        if (_rutasFiltradas[_indiceActivo]?.id === ruta.id) renderContenidoRuta(ruta);
      });
      dragIdx = null;
    });
  });
}

/**
 * Después de reordenar la lista combinada, actualiza las arrays originales
 * `ruta.sucursales` y `ruta.mayoristas` con los nuevos órdenes.
 */
function _sincronizarParadas(ruta, paradasOrdenadas) {
  ruta.sucursales = paradasOrdenadas
    .filter(p => p.tipo === "sucursal")
    .map((p, i) => ({ ...p, orden: p.orden ?? i + 1 }));
  ruta.mayoristas = paradasOrdenadas
    .filter(p => p.tipo === "mayorista")
    .map((p, i) => ({ ...p, orden: p.orden ?? i + 1 }));
}

/**
 * Muestra un modal de confirmación personalizado para quitar una parada de la ruta.
 * Reemplaza al confirm() nativo del navegador con una interfaz integrada.
 */
function mostrarConfirmQuitarParada(nombreParada) {
  const modalTitle = document.getElementById("quitar-title");
  const modalMsg = document.getElementById("quitar-msg");
  const modalMeta = document.getElementById("quitar-meta");
  const modalOverlay = document.getElementById("modal-quitar-parada");
  
  if (!modalTitle || !modalMsg || !modalMeta || !modalOverlay) {
    // Fallback al confirm nativo si los elementos no existen
    return Promise.resolve(confirm(`¿Quitar "${nombreParada}" de esta ruta?\n\nQuedará marcada como pendiente (naranja) en el mapa hasta ser reasignada.`));
  }
  
  modalTitle.textContent = "Quitar parada de la ruta";
  modalMsg.textContent = `¿Estás seguro de que deseas quitar "${nombreParada}" de esta ruta?`;
  modalMeta.innerHTML = `
    <span style="display:inline-block; margin-top:8px; padding:8px 12px; background:#fff3cd; border-left:3px solid #ffc107; border-radius:3px; font-size:0.85rem; color:#333;">
      <i data-lucide="info" style="width:14px;height:14px;display:inline-block;margin-right:6px;vertical-align:-2px;"></i>
      Quedará marcada como pendiente (naranja) en el mapa hasta ser reasignada.
    </span>
  `;
  
  modalOverlay.classList.remove("hidden");
  
  return new Promise(resolve => { _quitarResolve = resolve; });
}

function quitarParada(ruta, idx) {
  const paradas = _paradasDeRuta(ruta);
  const p = paradas[idx];
  if (!p) return;

  // Mostrar modal y esperar confirmación
  mostrarConfirmQuitarParada(p.nombre).then(async (confirmado) => {
    if (!confirmado) return;

    // Registrar como pendiente si es sucursal regular (no mayorista)
    if (p.tipo !== "mayorista" && p.num_tienda != null) {
      const pesoKg = _pesos[String(p.num_tienda)] || p.peso_kg || 0;
      _pendientes[String(p.num_tienda)] = {
        num_tienda: p.num_tienda,
        nombre:     p.nombre,
        latitud:    p.latitud,
        longitud:   p.longitud,
        peso_kg:    pesoKg,
      };
      // Persistir en MongoDB (fire-and-forget)
      fetch("/modificacion/quitar-sucursal", {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({
          ruta_id:   ruta.id,
          dia:       ruta.dia,
          num_tienda: p.num_tienda,
          nombre:    p.nombre,
          latitud:   p.latitud,
          longitud:  p.longitud,
          peso_kg:   pesoKg,
        }),
      }).catch(err => console.warn("[quitar-sucursal]", err));
    }

    const eraConfirmada = !!_confirmadas[ruta.id];
    paradas.splice(idx, 1);
    paradas.forEach((p, i) => { p.orden = i + 1; });
    _sincronizarParadas(ruta, paradas);
    ruta.num_sucursales = ruta.sucursales.length;

    delete _tiempos[ruta.id];
    if (eraConfirmada) _guardarHistorialRuta(ruta);
    renderParadas(ruta);
    _actualizarIndicadoresPeso(ruta);
    renderPendientesLayer();
    renderPanelPendientes();
    actualizarStatusOSRM();
    renderNavRutas();
    calcularOSRMParaRuta(ruta).then(() => {
      if (_rutasFiltradas[_indiceActivo]?.id === ruta.id) renderContenidoRuta(ruta);
    });
  });
}

function desconfirmarActiva() {
  const ruta = _rutasFiltradas[_indiceActivo];
  if (!ruta || !_confirmadas[ruta.id]) return;
  if (!confirm(`¿Desconfirmar "${ruta.nombre}"?\n\nPodrás modificarla nuevamente.`)) return;
  delete _confirmadas[ruta.id];
  _guardarConfirmadas();
  _persistirConfirmadas();
  actualizarProgreso();
  renderNavRutas();
  seleccionarRuta(_indiceActivo);
  mostrarToastMod("Ruta desconfirmada", "info");
}

// ── Crear / eliminar rutas manuales ───────────────────────────

function _buscarSucursalPorNum(num) {
  const nt = Number(num);
  return _sucDisponibles.find(s => Number(s.num_tienda) === nt)
    || _pendientes[String(nt)]
    || null;
}

function _marcarOcupacionVehiculo(ruta) {
  const veh = _vehiculoPorPlacas(ruta.vehiculo_placas || "");
  if (!veh) return;
  if (!veh.ocupacion) veh.ocupacion = {};
  veh.ocupacion[ruta.dia] = { ruta_id: ruta.id, ruta_nombre: ruta.nombre };
  _recalcularMetricasVehiculo(veh);
}

function _liberarOcupacionVehiculo(ruta) {
  const veh = _vehiculoPorPlacas(ruta.vehiculo_placas || "");
  if (!veh || !veh.ocupacion) return;
  if (veh.ocupacion[ruta.dia]?.ruta_id === ruta.id) {
    delete veh.ocupacion[ruta.dia];
    _recalcularMetricasVehiculo(veh);
  }
}

function abrirModalCrearRuta() {
  if (!_vehiculos || _vehiculos.length === 0) {
    mostrarToastMod("No hay vehículos disponibles para asignar.", "warn");
    return;
  }
  const diaDefault = _diaActivo !== "__todos__"
    ? _diaActivo
    : (DIAS_ORDEN.find(d => _rutas.some(r => r.dia === d.key))?.key || DIAS_ORDEN[0].key);
  _crearRuta = { dia: diaDefault, vehiculo: "", sucursales: [], query: "" };
  renderModalCrearRuta();
  document.getElementById("modal-crear-ruta")?.classList.remove("hidden");
  setTimeout(() => document.getElementById("buscar-sucursal-crear")?.focus(), 100);
}

function cerrarModalCrearRuta() {
  document.getElementById("modal-crear-ruta")?.classList.add("hidden");
}

function renderModalCrearRuta() {
  const diaSel = document.getElementById("crear-dia");
  if (diaSel) {
    diaSel.innerHTML = DIAS_ORDEN.map(d =>
      `<option value="${d.key}">${d.label}</option>`
    ).join("");
    diaSel.value = _crearRuta.dia;
  }

  const vehSel = document.getElementById("crear-vehiculo");
  if (vehSel) {
    const opciones = [
      '<option value="">Selecciona vehículo…</option>',
      ..._vehiculos.map(v => {
        const nombre = v.abrev || v.descripcion || v.placas || "";
        return `<option value="${h(v.placas)}">${h(nombre)} · ${h(v.placas)}</option>`;
      }),
    ];
    vehSel.innerHTML = opciones.join("");
    vehSel.value = _crearRuta.vehiculo;
  }

  const buscar = document.getElementById("buscar-sucursal-crear");
  if (buscar) buscar.value = _crearRuta.query || "";

  renderListaCrearSucursales();
  actualizarEstadoCrearRuta();
}

function renderListaCrearSucursales() {
  const q = (_crearRuta.query || "").toLowerCase().trim();
  const seleccion = new Set(_crearRuta.sucursales.map(n => String(n)));
  const pendientes = Object.values(_pendientes || {});
  const pendSet = new Set(pendientes.map(p => String(p.num_tienda)));

  const filtra = (s) => {
    if (!s) return false;
    const nombre = nombreSucursalLabel(s).toLowerCase();
    const nt = String(s.num_tienda ?? "");
    return !q || nombre.includes(q) || nt.includes(q);
  };

  const pendientesFil = pendientes.filter(filtra);
  const disponiblesFil = (_sucDisponibles || [])
    .filter(s => !pendSet.has(String(s.num_tienda)))
    .filter(filtra);

  let html = "";
  if (pendientesFil.length > 0) {
    html += `<div class="disp-seccion-pend"><span class="disp-seccion-label"><i data-lucide="triangle-alert" style="width:13px;height:13px;vertical-align:middle"></i> Pendientes de asignar</span></div>`;
    html += pendientesFil.map(s => {
      const selected = seleccion.has(String(s.num_tienda));
      return `
        <div class="disp-item${selected ? " selected" : ""}" data-nt="${s.num_tienda}">
          <div>
            <span class="nombre">${h(nombreSucursalLabel(s))}</span>
            <span class="num">#${h(s.num_tienda)}</span>
          </div>
          <span class="sel-check">${selected ? '<i data-lucide="check"></i>' : ""}</span>
        </div>`;
    }).join("");
  }

  if (disponiblesFil.length > 0) {
    if (pendientesFil.length > 0) {
      html += `<div class="disp-seccion-label disp-seccion-sep">Todas las sucursales</div>`;
    }
    html += disponiblesFil.map(s => {
      const selected = seleccion.has(String(s.num_tienda));
      return `
        <div class="disp-item${selected ? " selected" : ""}" data-nt="${s.num_tienda}">
          <div>
            <span class="nombre">${h(nombreSucursalLabel(s))}</span>
            <span class="num">#${h(s.num_tienda)}</span>
          </div>
          <span class="sel-check">${selected ? '<i data-lucide="check"></i>' : ""}</span>
        </div>`;
    }).join("");
  }

  if (!html) {
    html = '<div class="lista-sucursales-vacia">No se encontraron sucursales con ese criterio.</div>';
  }

  const lista = document.getElementById("lista-suc-crear");
  if (!lista) return;
  lista.innerHTML = html;
  lucide.createIcons();
  const contador = document.getElementById("crear-contador");
  if (contador) {
    contador.textContent = `${_crearRuta.sucursales.length} seleccionada${_crearRuta.sucursales.length !== 1 ? "s" : ""}`;
  }

  lista.querySelectorAll(".disp-item").forEach(item => {
    item.addEventListener("click", () => {
      const nt = Number(item.dataset.nt);
      const idx = _crearRuta.sucursales.indexOf(nt);
      if (idx >= 0) _crearRuta.sucursales.splice(idx, 1);
      else _crearRuta.sucursales.push(nt);
      renderListaCrearSucursales();
      actualizarEstadoCrearRuta();
    });
  });
}

function actualizarEstadoCrearRuta() {
  const btn = document.getElementById("btn-crear-confirm");
  if (!btn) return;
  btn.disabled = !_crearRuta.vehiculo || _crearRuta.sucursales.length === 0;
}

async function crearRutaManual() {
  if (!_crearRuta.vehiculo) {
    mostrarToastMod("Selecciona un vehículo para la ruta.", "warn");
    return;
  }
  if (_crearRuta.sucursales.length === 0) {
    mostrarToastMod("Selecciona al menos una sucursal.", "warn");
    return;
  }

  const btn = document.getElementById("btn-crear-confirm");
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<i data-lucide="loader-circle"></i> Creando…';
    lucide.createIcons();
  }

  try {
    const sucursales = _crearRuta.sucursales.map(nt => {
      const s = _buscarSucursalPorNum(nt) || {};
      return { num_tienda: nt, nombre: s.nombre || "" };
    });

    const res = await fetch("/modificacion/crear-ruta", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        dia: _crearRuta.dia,
        vehiculo_placas: _crearRuta.vehiculo,
        sucursales,
      }),
    });
    const data = await res.json();
    if (!res.ok || data.status !== "ok") {
      throw new Error(data.mensaje || `Error ${res.status}`);
    }

    if (!data.ruta) throw new Error("Respuesta incompleta del servidor");

    _rutas.push(data.ruta);
    _ordenarRutas();
    if (data.sucursales_pendientes) _aplicarPendientes(data.sucursales_pendientes);
    _marcarOcupacionVehiculo(data.ruta);
    _actualizarConteoRutas();

    document.getElementById("estado-vacio").style.display = "none";
    document.getElementById("resumen-fuentes").style.display = "flex";
    document.getElementById("filtro-dias").style.display = "flex";
    renderPendientesLayer();
    renderPanelPendientes();
    renderFiltroDias();
    actualizarStatusOSRM();
    actualizarProgreso();

    cerrarModalCrearRuta();

    const diaDestino = data.ruta.dia;
    if (_diaActivo !== diaDestino) {
      aplicarFiltroDia(diaDestino);
    } else {
      _rutasFiltradas = _rutas.filter(r => r.dia === _diaActivo);
      renderNavRutas();
    }

    const idx = _rutasFiltradas.findIndex(r => r.id === data.ruta.id);
    if (idx >= 0) seleccionarRuta(idx);

    mostrarToastMod("Ruta creada correctamente", "ok");
  } catch (err) {
    mostrarToastMod(`No se pudo crear la ruta: ${err.message}`, "error");
  } finally {
    if (btn) {
      btn.innerHTML = '<i data-lucide="plus-circle"></i> Crear ruta';
      lucide.createIcons();
      actualizarEstadoCrearRuta();
    }
  }
}

async function eliminarRutaActiva() {
  const ruta = _rutasFiltradas[_indiceActivo];
  if (!ruta) return;

  const ok = confirm(`¿Eliminar la ruta "${ruta.nombre}" del ${capitalizar(ruta.dia)}?\n\nLas sucursales quedarán como pendientes.`);
  if (!ok) return;

  const btn = document.getElementById("btn-eliminar-ruta");
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<i data-lucide="loader-circle"></i> Eliminando…';
    lucide.createIcons();
  }

  try {
    const res = await fetch("/modificacion/eliminar-ruta", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ruta_id: ruta.id, dia: ruta.dia }),
    });
    const data = await res.json();
    if (!res.ok || data.status !== "ok") {
      throw new Error(data.mensaje || `Error ${res.status}`);
    }

    _rutas = _rutas.filter(r => r.id !== ruta.id);
    delete _confirmadas[ruta.id];
    _guardarConfirmadas();
    _persistirConfirmadas();
    _liberarOcupacionVehiculo(ruta);
    _ordenarRutas();

    if (data.sucursales_pendientes) {
      _aplicarPendientes(data.sucursales_pendientes);
    } else {
      (ruta.sucursales || []).forEach(s => {
        _pendientes[String(s.num_tienda)] = {
          num_tienda: s.num_tienda,
          nombre:     s.nombre,
          latitud:    s.latitud,
          longitud:   s.longitud,
          peso_kg:    _pesos[String(s.num_tienda)] || s.peso_kg || 0,
        };
      });
    }

    _actualizarConteoRutas();
    renderPendientesLayer();
    renderPanelPendientes();
    renderFiltroDias();
    actualizarStatusOSRM();
    actualizarProgreso();

    if (_rutas.length === 0) {
      document.getElementById("estado-vacio").style.display = "block";
      document.getElementById("nav-rutas").style.display = "none";
      document.getElementById("panel-principal").style.display = "none";
    } else {
      document.getElementById("estado-vacio").style.display = "none";
      const diaDestino = _diaActivo !== "__todos__" ? _diaActivo : _rutas[0].dia;
      aplicarFiltroDia(diaDestino);
    }

    mostrarToastMod("Ruta eliminada", "ok");
  } catch (err) {
    mostrarToastMod(`No se pudo eliminar la ruta: ${err.message}`, "error");
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = '<i data-lucide="trash-2"></i> Eliminar';
      lucide.createIcons();
    }
  }
}

// ── Modal agregar sucursal ─────────────────────────────────────
function abrirModalAgregar() {
  document.getElementById("modal-agregar").classList.remove("hidden");
  document.getElementById("buscar-sucursal").value = "";
  renderDisponibles("");
  setTimeout(() => document.getElementById("buscar-sucursal").focus(), 100);
}
function cerrarModalAgregar() { document.getElementById("modal-agregar").classList.add("hidden"); }
function filtrarDisponibles() { renderDisponibles(document.getElementById("buscar-sucursal").value); }

function renderDisponibles(query) {
  const ruta   = _rutasFiltradas[_indiceActivo];
  const enRuta = new Set((ruta.sucursales || []).map(s => String(s.num_tienda)));

  // Sucursales asignadas en CUALQUIER ruta (incluyendo la actual)
  const asignadas = new Set();
  _rutas.forEach(r => (r.sucursales || []).forEach(s => asignadas.add(String(s.num_tienda))));

  const q = (query || "").toLowerCase().trim();

  // Pendientes: fueron quitadas de rutas, no están en asignadas
  const pendientesCoinciden = Object.values(_pendientes).filter(s =>
    !enRuta.has(String(s.num_tienda)) &&
    (!q || nombreSucursalLabel(s).toLowerCase().includes(q) || String(s.num_tienda).includes(q))
  );

  // Solo sucursales completamente libres (no en ninguna ruta)
  const disponibles = _sucDisponibles.filter(s =>
    !asignadas.has(String(s.num_tienda)) &&
    (!q || nombreSucursalLabel(s).toLowerCase().includes(q) || String(s.num_tienda).includes(q))
  ).slice(0, 50);

  const pendientesHTML = pendientesCoinciden.length > 0
    ? `<div class="disp-seccion-pend">
         <span class="disp-seccion-label"><i data-lucide="triangle-alert" style="width:13px;height:13px;vertical-align:middle"></i> Pendientes de asignar</span>
       </div>` +
      pendientesCoinciden.map(s => {
        const peso = (_pesos[String(s.num_tienda)] || s.peso_kg || 0).toLocaleString("es-MX");
        return `
          <div class="disp-item disp-item--pend" data-nt="${s.num_tienda}">
            <div>
              <span class="nombre">${h(nombreSucursalLabel(s))}</span>
              <span class="num">#${s.num_tienda}</span>
            </div>
            <span class="disp-pend-tag">● Pendiente · ${peso} kg</span>
          </div>`;
      }).join("") +
      (disponibles.length > 0 ? `<div class="disp-seccion-label disp-seccion-sep">Todas las sucursales</div>` : "")
    : "";

  const todosHTML = disponibles.map(s => `
      <div class="disp-item" data-nt="${s.num_tienda}">
        <div><span class="nombre">${h(nombreSucursalLabel(s))}</span><span class="num">#${s.num_tienda}</span></div>
        <span style="color:#2563eb;font-size:0.75rem;font-weight:600">+ Agregar</span>
      </div>`).join("");

  document.getElementById("lista-disponibles").innerHTML =
    pendientesHTML + todosHTML ||
    `<div style="padding:16px;color:#94a3b8;font-size:0.82rem;text-align:center">
       ${q ? "Sin resultados para esta búsqueda." : "No hay sucursales libres disponibles."}
     </div>`;
  lucide.createIcons();

  document.getElementById("lista-disponibles").querySelectorAll(".disp-item").forEach(item => {
    item.addEventListener("click", () => {
      const nt  = Number(item.dataset.nt);
      const suc = _sucDisponibles.find(s => s.num_tienda === nt)
               || Object.values(_pendientes).find(s => s.num_tienda === nt);
      if (!suc || enRuta.has(String(nt))) return;
      agregarSucursal(ruta, suc);
      cerrarModalAgregar();
    });
  });
}

function agregarSucursal(ruta, suc) {
  // Si estaba pendiente, dejar de marcarla como tal y persistir en MongoDB
  const eraPendiente = !!_pendientes[String(suc.num_tienda)];
  delete _pendientes[String(suc.num_tienda)];
  renderPendientesLayer();
  renderPanelPendientes();

  // Nombre resuelto: usar la función de lookup para garantizar que no quede vacío
  const nombreResuelto = nombreSucursalLabel(suc);

  // Persistir incorporación en MongoDB (fire-and-forget)
  fetch("/modificacion/agregar-sucursal", {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({
      ruta_id:    ruta.id,
      dia:        ruta.dia,
      num_tienda: suc.num_tienda,
      nombre:     nombreResuelto,
      latitud:    suc.latitud,
      longitud:   suc.longitud,
      peso_kg:    _pesos[String(suc.num_tienda)] || suc.peso_kg || 0,
    }),
  }).catch(err => console.warn("[agregar-sucursal]", err));

  const peso     = _pesos[String(suc.num_tienda)] || 0;
  const paradas  = _paradasDeRuta(ruta);
  const maxOrden = paradas.reduce((m, p) => Math.max(m, p.orden ?? 0), 0);
  ruta.sucursales.push({
    tipo: "sucursal",
    num_tienda:   suc.num_tienda,
    nombre:       nombreResuelto,
    latitud:      suc.latitud,
    longitud:     suc.longitud,
    peso_kg:      peso,
    descarga_min: Math.min(peso * MIN_DESCARGA_POR_KG, MAX_DESCARGA_MIN),
    orden:        maxOrden + 1,
  });
  const eraConfirmada = !!_confirmadas[ruta.id];
  ruta.num_sucursales = ruta.sucursales.length;
  delete _tiempos[ruta.id];
  if (eraConfirmada) _guardarHistorialRuta(ruta);
  renderParadas(ruta);
  _actualizarIndicadoresPeso(ruta);
  actualizarStatusOSRM();
  renderNavRutas();
  calcularOSRMParaRuta(ruta).then(() => {
    if (_rutasFiltradas[_indiceActivo]?.id === ruta.id) renderContenidoRuta(ruta);
  });
}

// ── Recalcular y confirmar ─────────────────────────────────────
async function recalcularActiva() {
  const ruta = _rutasFiltradas[_indiceActivo];
  const btn  = document.getElementById("btn-recalcular");
  btn.disabled = true;
  btn.innerHTML = '<i data-lucide="loader-circle" style="width:14px;height:14px"></i> Calculando…';
  lucide.createIcons();
  Loader.show('Recalculando Tiempos', MSG_MOD.recalcular);
  try {
    delete _tiempos[ruta.id];
    await calcularOSRMParaRuta(ruta);
    renderContenidoRuta(ruta);
    renderNavRutas();
  } finally {
    Loader.hide();
    btn.disabled = false;
    btn.innerHTML = '<i data-lucide="rotate-cw"></i> Recalcular tiempos';
    lucide.createIcons();
  }
}

function confirmarActiva() {
  const ruta = _rutasFiltradas[_indiceActivo];
  _confirmadas[ruta.id] = true;
  _guardarConfirmadas();
  _persistirConfirmadas();
  actualizarProgreso();
  renderNavRutas();
  seleccionarRuta(_indiceActivo);

  // Comprobar si todas las rutas del día activo quedaron confirmadas
  if (_diaActivo !== "__todos__") {
    const rutasDia     = _rutas.filter(r => r.dia === _diaActivo);
    const diaCompleto  = rutasDia.length > 0 && rutasDia.every(r => _confirmadas[r.id]);

    if (diaCompleto) {
      const nextDay = _siguienteDiaConPendientes(_diaActivo);
      if (nextDay) {
        const labelDia = DIAS_ORDEN.find(d => d.key === nextDay)?.label || capitalizar(nextDay);
        mostrarToastMod(
          `${capitalizar(_diaActivo)} completado — Pasando a ${labelDia}…`,
          "ok"
        );
        setTimeout(() => {
          aplicarFiltroDia(nextDay);
          // Actualizar botón activo en filtro-dias
          document.querySelectorAll(".filtro-dia-btn").forEach(btn =>
            btn.classList.toggle("activo", btn.dataset.dia === nextDay));
        }, 900);
      } else {
        mostrarToastMod("¡Todas las rutas confirmadas!", "ok");
      }
      return;
    }
  }

  // Avanzar a la siguiente ruta pendiente del mismo día
  const sig = _rutasFiltradas.findIndex((r, i) => i > _indiceActivo && !_confirmadas[r.id]);
  if (sig !== -1) setTimeout(() => seleccionarRuta(sig), 300);
}

function actualizarProgreso() {
  const total          = _rutas.length;
  const confirmadas    = Object.keys(_confirmadas).length;
  const todoConfirmado = confirmadas >= total && total > 0;

  const badge = document.getElementById("progreso-badge");
  badge.textContent = `${confirmadas} / ${total} confirmadas`;
  badge.classList.toggle("completo", todoConfirmado);

  const btnAutorizar = document.getElementById("btn-autorizar-todas");
  if (btnAutorizar) {
    btnAutorizar.disabled = total === 0 || todoConfirmado;
    btnAutorizar.innerHTML = todoConfirmado
      ? '<i data-lucide="check-check"></i> Todas autorizadas'
      : '<i data-lucide="check-check"></i> Autorizar todas';
    lucide.createIcons();
  }

  const btnSolo   = document.getElementById("btn-guardar-solo");
  const btnSeguir = document.getElementById("btn-guardar-seguir");
  if (btnSolo)   btnSolo.disabled   = !todoConfirmado;
  if (btnSeguir) btnSeguir.disabled = !todoConfirmado;

  // Auto-guardar en historial al completar todas las rutas (una sola vez por sesión)
  if (todoConfirmado && !_historialAutoGuardado) {
    _historialAutoGuardado = true;
    _autoGuardarHistorial();
  }
  // Resetear si se desconfirma alguna
  if (!todoConfirmado) _historialAutoGuardado = false;
}

// ── Autorizar todas ────────────────────────────────────────────
function autorizarTodas() {
  if (_rutas.length === 0) return;
  const sinConfirmar = _rutas.filter(r => !_confirmadas[r.id]).length;
  if (sinConfirmar === 0) return;

  const plural = sinConfirmar !== 1;
  if (!confirm(
    `¿Confirmar y autorizar ${sinConfirmar} ruta${plural ? "s" : ""} pendiente${plural ? "s" : ""}?\n\n` +
    `Esta acción las marcará todas como revisadas.`
  )) return;

  _rutas.forEach(r => { _confirmadas[r.id] = true; });
  _guardarConfirmadas();
  _persistirConfirmadas();
  actualizarProgreso();
  renderNavRutas();

  const btnConf = document.getElementById("btn-confirmar");
  if (btnConf) {
    btnConf.innerHTML = '<i data-lucide="x-circle"></i> Desconfirmar';
    btnConf.classList.add("desconfirmar");
    btnConf.classList.remove("confirmada");
    btnConf.disabled = false;
    btnConf.dataset.modo = "desconfirmar";
    lucide.createIcons();
  }

  mostrarToastMod(`${_rutas.length} ruta${_rutas.length !== 1 ? "s" : ""} autorizadas`, "ok");
}

// ── Guardar todo ───────────────────────────────────────────────
async function guardarTodo(redirigir = false) {
  const btnSolo   = document.getElementById("btn-guardar-solo");
  const btnSeguir = document.getElementById("btn-guardar-seguir");
  const btnActivo = redirigir ? btnSeguir : btnSolo;

  if (btnSolo)   btnSolo.disabled   = true;
  if (btnSeguir) btnSeguir.disabled = true;
  if (btnActivo) btnActivo.innerHTML = '<i data-lucide="loader-circle"></i> Guardando…';
  lucide.createIcons();

  Loader.show(redirigir ? 'Guardando y Continuando' : 'Guardando Modificaciones', MSG_MOD.guardar);

  const payload = {
    fecha_modificacion: new Date().toISOString(),
    rutas_confirmadas: _rutas.map(ruta => {
      const t      = _tiempos[ruta.id] || {};
      const pesoKg = calcularPesoRuta(ruta);
      let capTon   = ruta.capacidad_ton;
      if (!capTon && ruta.pct_utilizacion > 0 && pesoKg > 0) {
        capTon = parseFloat(((pesoKg / 1000) / (ruta.pct_utilizacion / 100)).toFixed(2));
      }
      capTon = capTon || 2.5;
      return {
        id:                 ruta.id,
        nombre:             ruta.nombre,
        tipo:               ruta.tipo,
        dia:                ruta.dia,
        vehiculo_abrev:     ruta.vehiculo_abrev,
        vehiculo_placas:    ruta.vehiculo_placas,
        capacidad_ton:      capTon,
        peso_kg:            pesoKg,
        peso_ton:           parseFloat((pesoKg / 1000).toFixed(3)),
        pct_utilizacion:    parseFloat(((pesoKg / 1000 / capTon) * 100).toFixed(1)),
        conduccion_min:     t.traslado_min || 0,
        descarga_min:       t.descarga_min || 0,
        extra_min:          t.extra_min || HORAS_EXTRA_RUTA_MIN,
        total_min:          t.total_min || 0,
        distancia_km:       t.distancia_km || 0,
        hora_salida:        _horaSalidaDeRuta(ruta),
        hora_regreso:       t.hora_regreso || ruta.hora_regreso || "—",
        origen_tiempo:      t.origen_tiempo || "desconocido",
        num_sucursales:     (ruta.sucursales || []).length,
        sucursales: (ruta.sucursales || []).map((s, i) => ({
          num_tienda:   s.num_tienda,
          nombre:       s.nombre,
          orden:        s.orden ?? i + 1,
          peso_kg:      _pesos[String(s.num_tienda)] || s.peso_kg || 0,
          descarga_min: parseFloat(Math.min(
            (_pesos[String(s.num_tienda)] || s.peso_kg || 0) * MIN_DESCARGA_POR_KG,
            MAX_DESCARGA_MIN
          ).toFixed(1)),
          latitud:  s.latitud,
          longitud: s.longitud,
        })),
        mayoristas: (ruta.mayoristas || []).map((m, i) => ({
          id_cliente: m.id_cliente,
          nombre:     m.nombre,
          orden:      m.orden ?? i + 1,
          peso_kg:    m.peso_kg || 0,
          latitud:    m.latitud,
          longitud:   m.longitud,
        })),
      };
    }),
  };

  try {
    const res = await fetch("/modificacion/guardar", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (res.status === 400) { Loader.hide(); redirigirAlMenu('Sin logística activa.'); return; }
    if (res.ok) {
      // Guardar absolutamente todas las rutas (todos los días) en el historial
      const filasHistorial = [];
      for (const ruta of _rutas) {
        for (const suc of (ruta.sucursales || [])) {
          if (!suc.num_tienda) continue;
          filasHistorial.push({
            num_tienda:       suc.num_tienda,
            vehiculo:         ruta.vehiculo_abrev || "",
            dia_semana:       (ruta.dia || "").toUpperCase(),
            secuencia_visita: suc.orden ?? 1,
            kg_entrega:       Math.round(_pesos[String(suc.num_tienda)] || suc.peso_kg || 0),
          });
        }
      }
      if (filasHistorial.length) {
        fetch("/modificacion/guardar-historico", {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify({ nombre: "", rutas: filasHistorial }),
        }).catch(err => console.warn("[guardar-historico]", err));
      }

      // Limpiar localStorage al guardar exitosamente
      try { localStorage.removeItem(_claveStorage()); } catch (_) {}
      if (redirigir) {
        const _slug = window.__PERFIL_SLUG__;
        window.location.href = _slug ? `/pdf/${_slug}` : "/pdf/";
        return;
      }
      Loader.hide();
      mostrarToastMod("Rutas guardadas correctamente", "ok");
      if (btnSolo) {
        btnSolo.innerHTML = '<i data-lucide="check"></i> Guardado';
        btnSolo.style.background = "#16a34a"; btnSolo.style.color = "#fff";
        lucide.createIcons();
      }
    } else throw new Error("Error");
  } catch (err) {
    console.error("[guardarTodo]", err);
    Loader.hide();
    mostrarToastMod("Error al guardar", "error");
    if (btnActivo) {
      btnActivo.innerHTML = '<i data-lucide="x"></i> Error';
      btnActivo.style.background = "#dc2626"; btnActivo.style.color = "#fff";
      lucide.createIcons();
    }
  } finally {
    setTimeout(() => {
      const todosOk = Object.keys(_confirmadas).length >= _rutas.length && _rutas.length > 0;
      if (btnSolo) {
        btnSolo.disabled = !todosOk;
        btnSolo.innerHTML = '<i data-lucide="save"></i> Guardar';
        btnSolo.style.background = ""; btnSolo.style.color = "";
        lucide.createIcons();
      }
      if (btnSeguir) {
        btnSeguir.disabled = !todosOk;
        btnSeguir.innerHTML = '<i data-lucide="save"></i> Guardar y seguir';
        btnSeguir.style.background = ""; btnSeguir.style.color = "";
        lucide.createIcons();
      }
    }, 2500);
  }
}

// ── Helpers ────────────────────────────────────────────────────
function calcularPesoRuta(ruta) {
  const pesoSucs = (ruta.sucursales || []).reduce((sum, s) =>
    sum + (_pesos[String(s.num_tienda)] || s.peso_kg || 0), 0);
  const pesoMays = (ruta.mayoristas  || []).reduce((sum, m) => sum + (m.peso_kg || 0), 0);
  return pesoSucs + pesoMays;
}

function formatMin(min) {
  if (min == null) return "…";
  if (min <= 0)    return "—";
  if (min < 60)    return `${Math.round(min)} min`;
  const hh = Math.floor(min / 60), mm = Math.round(min % 60);
  return mm > 0 ? `${hh}h ${mm}min` : `${hh}h`;
}

function capitalizar(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : ""; }

function h(s) {
  return String(s ?? "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}

function nombreSucursalLabel(s) {
  if (!s) return "";
  const nombre = String(s.nombre || "").trim();
  // "Sucursal" es el placeholder genérico: ignorarlo y buscar el nombre real
  if (nombre && nombre !== "Sucursal") return nombre;
  const nt = s.num_tienda != null ? String(s.num_tienda) : "";
  if (nt) {
    const match = _sucDisponibles.find(x => String(x.num_tienda) === nt);
    const alt = String(match?.nombre || "").trim();
    if (alt && alt !== "Sucursal") return alt;
  }
  return nombre || "";   // devuelve lo que haya o cadena vacía, nunca el placeholder
}

// ═══════════════════════════════════════════════════════════════
// CAMBIAR DÍA DE UNA RUTA
// ═══════════════════════════════════════════════════════════════

function renderSelectorDia(ruta) {
  const zona = document.getElementById("zona-cambio-dia");
  if (!zona) return;

  const diaActual = ruta.dia || "";
  zona.innerHTML = `
    <div class="mod-dia-row">
      <label class="mod-dia-label">Día de entrega</label>
      <select id="select-dia-ruta" class="mod-dia-select">
        ${DIAS_ORDEN.map(d => `
          <option value="${d.key}" ${d.key === diaActual.toLowerCase() ? "selected" : ""}>${d.label}</option>
        `).join("")}
      </select>
      <button class="btn btn-sm btn-primary" id="btn-aplicar-dia">Aplicar</button>
    </div>
  `;

  document.getElementById("btn-aplicar-dia").addEventListener("click", () => {
    const nuevoDia = document.getElementById("select-dia-ruta").value;
    if (nuevoDia === ruta.dia) return;
    ruta.dia = nuevoDia;
    ruta._modificado = true;
    mostrarToastMod(`Día cambiado a ${capitalizar(nuevoDia)}`, "ok");
    renderRutaActiva();
  });
}

// ═══════════════════════════════════════════════════════════════
// AUTO-GUARDAR EN HISTORIAL (se dispara al confirmar todas las rutas)
// ═══════════════════════════════════════════════════════════════

function _guardarHistorialRuta(ruta) {
  const filas = (ruta.sucursales || [])
    .filter(s => s.num_tienda != null)
    .map(s => ({
      num_tienda:       s.num_tienda,
      vehiculo:         ruta.vehiculo_abrev || "",
      dia_semana:       (ruta.dia || "").toUpperCase(),
      secuencia_visita: s.orden,
      kg_entrega:       Math.round(_pesos[String(s.num_tienda)] || s.peso_kg || 0),
    }));
  if (!filas.length) return;
  const nombre = `Logística ${new Date().toLocaleDateString("es-MX")}`;
  fetch("/modificacion/guardar-historico", {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({ nombre, rutas: filas }),
  })
  .then(r => r.json())
  .then(data => { if (data.status === "ok") mostrarToastMod("Historial actualizado", "ok"); })
  .catch(err => console.warn("[historial-ruta]", err));
}

function _autoGuardarHistorial() {
  const filas = [];
  for (const ruta of _rutas) {
    if (!_confirmadas[ruta.id]) continue;
    for (const suc of (ruta.sucursales || [])) {
      if (!suc.num_tienda) continue;
      filas.push({
        num_tienda:       suc.num_tienda,
        vehiculo:         ruta.vehiculo_abrev || "",
        dia_semana:       (ruta.dia || "").toUpperCase(),
        secuencia_visita: suc.orden,
        kg_entrega:       Math.round(suc.peso_kg || 0),
      });
    }
  }
  if (!filas.length) return;

  const nombre = `Logística ${new Date().toLocaleDateString("es-MX")}`;
  fetch("/modificacion/guardar-historico", {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({ nombre, rutas: filas }),
  })
  .then(r => r.json())
  .then(data => {
    if (data.status === "ok")
      mostrarToastMod("Historial actualizado automáticamente", "ok");
  })
  .catch(err => console.warn("[auto-historial]", err));
}

// ── Toast de notificación ──────────────────────────────────────
function mostrarToastMod(msg, tipo = "info") {
  let toast = document.getElementById("mod-toast");
  if (!toast) {
    toast = document.createElement("div");
    toast.id = "mod-toast";
    document.body.appendChild(toast);
  }
  toast.textContent = msg;
  toast.className = `mod-toast mod-toast--${tipo}`;
  requestAnimationFrame(() => {
    toast.classList.add("visible");
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => toast.classList.remove("visible"), 3000);
  });
}
