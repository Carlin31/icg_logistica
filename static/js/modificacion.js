// ===== Modificación manual de rutas =====
// Lee desde asignaciones (MongoDB). Incluye sucursales + mayoristas.
// Permite cambiar vehículo por ruta, reordenar paradas y confirmar cada ruta.

'use strict';

// ── Estado global ──────────────────────────────────────────────
let _rutas           = [];
let _rutasFiltradas  = [];
let _pesos           = {};
let _volumenes       = {};
let _sucDisponibles  = [];
let _vehiculos       = [];          // flota activa [{placas, abrev, capacidad_ton, ...}]
let _choferesDisponibles = [];      // [{_id, nombre}] — catálogo de choferes (Configuración)
let _ultimoVehiculoCreado = "";     // placas del último vehículo usado al crear una ruta manual (continuidad día a día)
let _indiceActivo    = 0;
let _tiempos         = {};
let _confirmadas     = {};
let _diaActivo       = "__todos__";
let _logisticaId     = "";
let _mapa             = null;
let _rutaLayer        = null;
let _markersLayer     = null;
let _pendientesLayer  = null;
let _mayoristasLayer  = null;
let _cancelarBatch    = false;
let _altLayer          = null; // Leaflet layer para línea de referencia gris
let _modoPersonalizado = {};   // rutaId → bool (false=recomendada, true=personalizada)
let _viaPoints         = {};   // rutaId → [{lat, lon}]  — paso obligatorio
let _puntosEvitar      = {};   // rutaId → [{lat, lon}]  — zonas a evitar
let _modoEdicionMapa   = {};   // rutaId → 'obligatorio' | 'evitar'
let _rutaPersonalizada = {};   // rutaId → {geometry, distancia_km, traslado_min, ...}
let _viaLayer          = null; // Leaflet layer para via-points y zonas evitar
let _pendientes             = {};   // { num_tienda: {num_tienda, nombre, latitud, longitud, peso_kg} }
let _quitarResolve          = null;
let _crearRuta              = { dia: "", vehiculo: "", sucursales: [], mayoristas: [], query: "", queryMayoristas: "", verTodasSuc: false, verTodosMay: false };
let _configDias             = {};          // config_dias from MongoDB (per-day schedule)
let _mayoristasTodos        = [];          // todos los mayoristas disponibles [{id_cliente, documento, nombre, peso_kg, latitud, longitud}]

/** Clave de unicidad por documento: usa documento si existe, si no id_cliente. */
function _docKey(m) {
  return m.documento || String(m.id_cliente ?? "");
}

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
  _ultimaActualizacionFlota = parseInt(localStorage.getItem("icg_flota_actualizada") || "0", 10);
  bindEventos();

  const modo = new URLSearchParams(window.location.search).get("modo") || "";
  if (modo === "crear-ruta") {
    setTimeout(() => abrirModalCrearRuta(), 120);
  }
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
    const res  = await fetch('/menu/api/activa');
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
  document.getElementById("btn-agregar-may")?.addEventListener("click", abrirModalAgregarMayorista);
  document.getElementById("btn-crear-ruta")?.addEventListener("click", abrirModalCrearRuta);
  document.getElementById("btn-eliminar-ruta")?.addEventListener("click", eliminarRutaActiva);
  document.getElementById("modal-agregar-close")?.addEventListener("click", cerrarModalAgregar);
  document.getElementById("modal-may-close")?.addEventListener("click", cerrarModalAgregarMayorista);
  document.getElementById("buscar-sucursal")?.addEventListener("input", filtrarDisponibles);
  document.getElementById("buscar-mayorista")?.addEventListener("input", () => {
    renderMayoristasDisponibles(document.getElementById("buscar-mayorista").value);
  });
  document.getElementById("modal-agregar-may")?.addEventListener("click", (e) => {
    if (e.target.id === "modal-agregar-may") cerrarModalAgregarMayorista();
  });
  document.getElementById("modal-agregar")?.addEventListener("click", (e) => {
    if (e.target.id === "modal-agregar") cerrarModalAgregar();
  });
  document.getElementById("modal-crear-close")?.addEventListener("click", cerrarModalCrearRuta);
  document.getElementById("btn-crear-cancel")?.addEventListener("click", cerrarModalCrearRuta);
  document.getElementById("btn-crear-confirm")?.addEventListener("click", crearRutaManual);
  document.getElementById("buscar-sucursal-crear")?.addEventListener("input", (e) => {
    _crearRuta.query = e.target.value;
    _crearRuta.verTodasSuc = false;
    renderListaCrearSucursales();
  });
  document.getElementById("buscar-mayorista-crear")?.addEventListener("input", (e) => {
    _crearRuta.queryMayoristas = e.target.value;
    _crearRuta.verTodosMay = false;
    renderListaCrearMayoristas();
  });
  document.getElementById("crear-dia")?.addEventListener("change", (e) => {
    _crearRuta.dia = e.target.value;
    // Si el vehículo seleccionado ya no está disponible en el nuevo día,
    // intentar continuar con el vehículo de continuidad para ese día.
    const vehActual = _vehiculos.find(v => v.placas === _crearRuta.vehiculo);
    if (vehActual && (vehActual.ocupacion || {})[_crearRuta.dia]) {
      _crearRuta.vehiculo = _vehiculoDeContinuidad(_crearRuta.dia);
    }
    renderVehiculosCrearRuta();
    actualizarEstadoCrearRuta();
  });
  document.getElementById("crear-vehiculo")?.addEventListener("change", (e) => {
    _crearRuta.vehiculo = e.target.value;
    actualizarChoferInfoCrearRuta();
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
    const rutasPromise      = fetchWithRetries("/modificacion/rutas", {}, timeoutMs, retries).catch(e => ({ error: e }));
    const pesosPromise      = fetchWithRetries("/modificacion/pesos", {}, timeoutMs, retries).catch(e => ({ error: e }));
    const volumenesPromise  = fetchWithRetries("/modificacion/volumenes", {}, timeoutMs, retries).catch(e => ({ error: e }));
    const sucPromise        = fetchWithRetries("/modificacion/sucursales", {}, timeoutMs, retries).catch(e => ({ error: e }));
    const vehPromise        = fetchWithRetries("/modificacion/vehiculos", {}, timeoutMs, retries).catch(e => ({ error: e }));
    const horariosPromise   = fetch("/modificacion/horarios-config").catch(() => null);
    const choferesPromise   = fetch("/configuracion/choferes").catch(() => null);

    const [rutasRes, pesosRes, volumenesRes, sucRes, vehRes, horariosRes, choferesRes] = await Promise.all([rutasPromise, pesosPromise, volumenesPromise, sucPromise, vehPromise, horariosPromise, choferesPromise]);

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
    _volumenes      = (volumenesRes && !volumenesRes.error && volumenesRes.ok) ? await volumenesRes.json() : {};
    _sucDisponibles = await sucRes.json();
    _vehiculos      = vehRes.ok ? await vehRes.json() : [];
    try { _configDias = (horariosRes?.ok) ? await horariosRes.json() : {}; } catch (_) { _configDias = {}; }
    try { _choferesDisponibles = (choferesRes?.ok) ? await choferesRes.json() : []; } catch (_) { _choferesDisponibles = []; }

    _rutas          = rutasData.rutas || [];
    _logisticaId    = rutasData.logistica_id || _logisticaId || "";
    _mayoristasTodos = rutasData.mayoristas_disponibles || [];

    if (rutasData.status !== "ok") {
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

    // Restaurar sucursales pendientes y mayoristas libres desde MongoDB (fuente de verdad)
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

    if (_rutas.length > 0) {
      const primerDia = _rutas.find(r => r.dia === "lunes") ? "lunes"
                      : (DIAS_ORDEN.find(d => _rutas.some(r => r.dia === d.key))?.key || "__todos__");
      aplicarFiltroDia(primerDia);
      console.log('[cargarDatos] finalizado, primer dia aplicado:', primerDia);
    } else {
      document.getElementById("estado-vacio").style.display = "block";
      document.getElementById("nav-rutas").style.display = "none";
      document.getElementById("panel-principal").style.display = "none";
      console.log('[cargarDatos] finalizado sin rutas programadas');
    }

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
    const body = JSON.stringify({ paradas, hora_salida: _horaSalidaDeRuta(ruta) });
    const res = await fetch("/modificacion/recalcular-tiempos", {
      method: "POST", headers: { "Content-Type": "application/json" }, body,
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

// ── Selector de ruta (recomendada / personalizada) ─────────────

function renderSelectorRuta(ruta) {
  const zona = document.getElementById("zona-alternativas");
  if (!zona) return;
  const t = _tiempos[ruta.id] || {};
  if (!rutaTieneOSRM(ruta.id)) { zona.innerHTML = ""; return; }

  const esP      = !!_modoPersonalizado[ruta.id];
  const vps      = _viaPoints[ruta.id] || [];
  const rutaCust = _rutaPersonalizada[ruta.id];

  // Stats ruta recomendada
  const recKm  = (t.distancia_km || 0).toFixed(1);
  const recMin = Math.round(t.traslado_min || 0);
  const recReg = t.hora_regreso || "—";

  // Stats ruta personalizada
  let custLabel = "";
  if (rutaCust) {
    custLabel = `${rutaCust.distancia_km.toFixed(1)} km · ${Math.round(rutaCust.traslado_min)} min conducción · Regreso ${rutaCust.hora_regreso}`;
  } else if (esP && vps.length > 0) {
    custLabel = "Calculando…";
  } else {
    custLabel = "Haz clic en el mapa para trazar puntos de paso";
  }

  // Editor (solo visible en modo personalizado)
  let editorHTML = "";
  if (esP) {
    const modo = _modoEdicionMapa[ruta.id] || "obligatorio";
    const pes  = _puntosEvitar[ruta.id] || [];

    // Botones para cambiar modo de edición
    const modoToggle = `
      <div class="pers-modo-toggle">
        <button class="pers-modo-btn ${modo === "obligatorio" ? "pers-modo-activo-obl" : ""}"
                onclick="cambiarModoEdicion('${ruta.id}', 'obligatorio')">
          <i data-lucide="check-circle" style="width:12px;height:12px"></i> Paso obligatorio
        </button>
        <button class="pers-modo-btn ${modo === "evitar" ? "pers-modo-activo-evit" : ""}"
                onclick="cambiarModoEdicion('${ruta.id}', 'evitar')">
          <i data-lucide="ban" style="width:12px;height:12px"></i> Zonas a evitar
        </button>
      </div>`;

    // Panel de paso obligatorio
    const instruccionObl = vps.length === 0 && modo === "obligatorio"
      ? `<div class="pers-instruccion pers-instruccion-obl"><i data-lucide="mouse-pointer-click" style="width:13px;height:13px;vertical-align:middle"></i> Haz clic en el mapa para agregar puntos de paso obligatorio</div>`
      : "";
    const viaItems = vps.map((vp, i) => `
      <div class="via-item">
        <span class="via-numero">${i + 1}</span>
        <span class="via-coords">${vp.lat.toFixed(5)}, ${vp.lon.toFixed(5)}</span>
        <button class="via-eliminar" onclick="eliminarViaPoint('${ruta.id}', ${i})" title="Eliminar punto">
          <i data-lucide="x" style="width:12px;height:12px"></i>
        </button>
      </div>`).join("");
    const limpiarObl = vps.length > 0
      ? `<button class="btn-limpiar-via" onclick="limpiarViaPoints('${ruta.id}')">
           <i data-lucide="trash-2" style="width:12px;height:12px"></i> Limpiar paso obligatorio
         </button>`
      : "";
    const panelObl = `
      <div class="pers-seccion">
        ${instruccionObl}
        ${viaItems ? `<div class="via-lista">${viaItems}</div>` : ""}
        ${limpiarObl}
      </div>`;

    // Panel de zonas a evitar
    const instruccionEvit = pes.length === 0 && modo === "evitar"
      ? `<div class="pers-instruccion pers-instruccion-evit"><i data-lucide="mouse-pointer-click" style="width:13px;height:13px;vertical-align:middle"></i> Haz clic en el mapa para marcar zonas a evitar</div>`
      : "";
    const evitItems = pes.map((pe, i) => `
      <div class="via-item via-item-evitar">
        <span class="via-numero via-numero-evitar">${i + 1}</span>
        <span class="via-coords">${pe.lat.toFixed(5)}, ${pe.lon.toFixed(5)}</span>
        <button class="via-eliminar" onclick="eliminarPuntoEvitar('${ruta.id}', ${i})" title="Eliminar zona">
          <i data-lucide="x" style="width:12px;height:12px"></i>
        </button>
      </div>`).join("");
    const limpiarEvit = pes.length > 0
      ? `<button class="btn-limpiar-via btn-limpiar-evitar" onclick="limpiarPuntosEvitar('${ruta.id}')">
           <i data-lucide="trash-2" style="width:12px;height:12px"></i> Limpiar zonas
         </button>`
      : "";
    const panelEvit = `
      <div class="pers-seccion">
        ${instruccionEvit}
        ${evitItems ? `<div class="via-lista">${evitItems}</div>` : ""}
        ${limpiarEvit}
      </div>`;

    editorHTML = `
      <div class="pers-editor">
        ${modoToggle}
        ${modo === "obligatorio" ? panelObl : panelEvit}
      </div>`;
  }

  zona.innerHTML = `
    <div class="selector-ruta-panel">
      <div class="selector-ruta-titulo">Selección de ruta</div>
      <div class="selector-ruta-opciones">
        <div class="selector-ruta-opcion ${!esP ? "opcion-activa opcion-rec" : ""}"
             onclick="seleccionarModoRuta('${ruta.id}', false)" role="button" tabindex="0">
          <span class="opcion-radio">${!esP
            ? `<i data-lucide="circle-check-big" style="width:16px;height:16px;color:#2563eb"></i>`
            : `<i data-lucide="circle" style="width:16px;height:16px;color:#94a3b8"></i>`}</span>
          <div class="opcion-info">
            <div class="opcion-nombre">Ruta recomendada</div>
            <div class="opcion-stats">${recKm} km · ${recMin} min · Regreso ${recReg}</div>
          </div>
        </div>
        <div class="selector-ruta-opcion ${esP ? "opcion-activa opcion-cust" : ""}"
             onclick="seleccionarModoRuta('${ruta.id}', true)" role="button" tabindex="0">
          <span class="opcion-radio">${esP
            ? `<i data-lucide="circle-check-big" style="width:16px;height:16px;color:#f59e0b"></i>`
            : `<i data-lucide="circle" style="width:16px;height:16px;color:#94a3b8"></i>`}</span>
          <div class="opcion-info">
            <div class="opcion-nombre">Ruta personalizada</div>
            <div class="opcion-stats ${!rutaCust && esP && !vps.length ? "opcion-stats-hint" : ""}">${custLabel}</div>
          </div>
        </div>
      </div>
      ${editorHTML}
    </div>`;
  lucide.createIcons();
}

function seleccionarModoRuta(rutaId, esPersonalizada) {
  const ruta = _rutas.find(r => r.id === rutaId);
  if (!ruta) return;
  _modoPersonalizado[rutaId] = esPersonalizada;
  const mapaEl = document.getElementById("mapa");
  if (mapaEl) mapaEl.classList.toggle("mapa-personalizar", esPersonalizada);
  renderSelectorRuta(ruta);
  renderResumenTiempos(ruta);
  renderIndicadores(ruta);
  actualizarMapa(ruta);
  renderNavRutas();
}

// ── Ruta personalizada: paso obligatorio y zonas a evitar ──────

function cambiarModoEdicion(rutaId, modo) {
  _modoEdicionMapa[rutaId] = modo;
  const ruta = _rutas.find(r => r.id === rutaId);
  if (ruta) renderSelectorRuta(ruta);
}

function eliminarViaPoint(rutaId, idx) {
  const ruta = _rutas.find(r => r.id === rutaId);
  if (!ruta) return;
  const vps = _viaPoints[rutaId] || [];
  vps.splice(idx, 1);
  _viaPoints[rutaId] = vps;
  const tieneAlgo = vps.length > 0 || (_puntosEvitar[rutaId] || []).length > 0;
  if (tieneAlgo) {
    renderSelectorRuta(ruta);
    actualizarMapa(ruta);
    _recalcularPersonalizado(ruta).then(() => {
      renderSelectorRuta(ruta);
      renderResumenTiempos(ruta);
      renderIndicadores(ruta);
      actualizarMapa(ruta);
    });
  } else {
    _rutaPersonalizada[rutaId] = null;
    renderSelectorRuta(ruta);
    renderResumenTiempos(ruta);
    renderIndicadores(ruta);
    actualizarMapa(ruta);
  }
}

function eliminarPuntoEvitar(rutaId, idx) {
  const ruta = _rutas.find(r => r.id === rutaId);
  if (!ruta) return;
  const pes = _puntosEvitar[rutaId] || [];
  pes.splice(idx, 1);
  _puntosEvitar[rutaId] = pes;
  renderSelectorRuta(ruta);
  actualizarMapa(ruta);
  _recalcularPersonalizado(ruta).then(() => {
    renderSelectorRuta(ruta);
    renderResumenTiempos(ruta);
    renderIndicadores(ruta);
    actualizarMapa(ruta);
  });
}

function limpiarViaPoints(rutaId) {
  const ruta = _rutas.find(r => r.id === rutaId);
  if (!ruta) return;
  _viaPoints[rutaId] = [];
  if (!(_puntosEvitar[rutaId] || []).length) _rutaPersonalizada[rutaId] = null;
  renderSelectorRuta(ruta);
  renderResumenTiempos(ruta);
  renderIndicadores(ruta);
  actualizarMapa(ruta);
}

function limpiarPuntosEvitar(rutaId) {
  const ruta = _rutas.find(r => r.id === rutaId);
  if (!ruta) return;
  _puntosEvitar[rutaId] = [];
  if (!(_viaPoints[rutaId] || []).length) _rutaPersonalizada[rutaId] = null;
  renderSelectorRuta(ruta);
  renderResumenTiempos(ruta);
  renderIndicadores(ruta);
  actualizarMapa(ruta);
}

function limpiarPersonalizacion(rutaId) {
  const ruta = _rutas.find(r => r.id === rutaId);
  if (!ruta) return;
  _viaPoints[rutaId]         = [];
  _puntosEvitar[rutaId]      = [];
  _rutaPersonalizada[rutaId] = null;
  renderSelectorRuta(ruta);
  renderResumenTiempos(ruta);
  renderIndicadores(ruta);
  actualizarMapa(ruta);
}

async function _recalcularPersonalizado(ruta) {
  const paradas      = _paradasDeRuta(ruta);
  const via          = _viaPoints[ruta.id] || [];
  const puntosEvitar = _puntosEvitar[ruta.id] || [];
  if (!paradas.length || (!via.length && !puntosEvitar.length)) return;
  const statusEl = document.getElementById("osrm-ruta-status");
  if (statusEl) statusEl.style.display = "flex";
  try {
    const res = await fetch("/modificacion/ruta-personalizada", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({
        paradas,
        via_points:     via,
        puntos_evitar:  puntosEvitar,
        hora_salida:    _horaSalidaDeRuta(ruta),
      }),
    });
    if (res.ok) {
      const data = await res.json();
      if (data.status === "ok" && data.ruta) {
        _rutaPersonalizada[ruta.id] = data.ruta;
      }
    }
  } catch { /* silencioso */ }
  if (statusEl) statusEl.style.display = "none";
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
  _altLayer        = L.layerGroup().addTo(_mapa);
  _rutaLayer       = L.layerGroup().addTo(_mapa);
  _pendientesLayer = L.layerGroup().addTo(_mapa);
  _mayoristasLayer = L.layerGroup().addTo(_mapa);
  _viaLayer        = L.layerGroup().addTo(_mapa);
  _mapa.on("popupopen", () => lucide.createIcons());

  _mapa.on("click", async (e) => {
    const ruta = _rutasFiltradas[_indiceActivo];
    if (!ruta || !_modoPersonalizado[ruta.id]) return;
    const modo = _modoEdicionMapa[ruta.id] || "obligatorio";
    if (modo === "evitar") {
      if (!_puntosEvitar[ruta.id]) _puntosEvitar[ruta.id] = [];
      _puntosEvitar[ruta.id].push({ lat: e.latlng.lat, lon: e.latlng.lng });
    } else {
      if (!_viaPoints[ruta.id]) _viaPoints[ruta.id] = [];
      _viaPoints[ruta.id].push({ lat: e.latlng.lat, lon: e.latlng.lng });
    }
    renderSelectorRuta(ruta);
    actualizarMapa(ruta);
    await _recalcularPersonalizado(ruta);
    renderSelectorRuta(ruta);
    renderResumenTiempos(ruta);
    renderIndicadores(ruta);
    actualizarMapa(ruta);
  });

  // Recalcula el tamaño del mapa al redimensionar ventana (ej. rotar dispositivo)
  let _resizeTimer;
  window.addEventListener("resize", () => {
    clearTimeout(_resizeTimer);
    _resizeTimer = setTimeout(() => _mapa && _mapa.invalidateSize(), 150);
  });
}

function actualizarMapa(ruta) {
  if (!_mapa) return;
  _markersLayer.clearLayers();
  _altLayer.clearLayers();
  _rutaLayer.clearLayers();
  _viaLayer.clearLayers();
  renderPendientesLayer();
  const mayLibres = _mayoristasNoAsignados();
  renderMayoristasLibresLayer(mayLibres);
  const tiempos = _tiempos[ruta.id] || {};
  const paradas = _paradasDeRuta(ruta);
  const bounds  = [];

  const esP      = !!_modoPersonalizado[ruta.id];
  const rutaCust = _rutaPersonalizada[ruta.id];

  if (!esP) {
    // Modo recomendada: trazar geometría del sistema en azul
    if (tiempos.geometry && tiempos.geometry.length > 1) {
      const latlngs = tiempos.geometry.map(c => [c[1], c[0]]);
      L.polyline(latlngs, { color: "#2563eb", weight: 6, opacity: 0.95, lineJoin: "round" }).addTo(_rutaLayer);
    }
  } else {
    // Modo personalizado: referencia gris + ruta personalizada ámbar
    if (tiempos.geometry && tiempos.geometry.length > 1) {
      const refLatlngs = tiempos.geometry.map(c => [c[1], c[0]]);
      L.polyline(refLatlngs, { color: "#94a3b8", weight: 2, opacity: 0.45, dashArray: "6 5", lineJoin: "round" }).addTo(_altLayer);
    }
    if (rutaCust && rutaCust.geometry && rutaCust.geometry.length > 1) {
      const custLatlngs = rutaCust.geometry.map(c => [c[1], c[0]]);
      L.polyline(custLatlngs, { color: "#f59e0b", weight: 6, opacity: 0.95, lineJoin: "round" }).addTo(_rutaLayer);
    }
    // Marcadores de paso obligatorio
    (_viaPoints[ruta.id] || []).forEach((vp, i) => {
      L.marker([vp.lat, vp.lon], {
        icon: L.divIcon({ className: "", html: `<div class="marker-via">${i + 1}</div>`, iconSize: [22, 22], iconAnchor: [11, 11] }),
        zIndexOffset: 800,
      }).addTo(_viaLayer);
    });
    // Zonas a evitar: círculo rojo + marcador
    (_puntosEvitar[ruta.id] || []).forEach((pe, i) => {
      L.circle([pe.lat, pe.lon], {
        radius: 300, color: "#dc2626", fillColor: "#fee2e2",
        fillOpacity: 0.35, weight: 2,
      }).addTo(_viaLayer);
      L.marker([pe.lat, pe.lon], {
        icon: L.divIcon({ className: "", html: `<div class="marker-evitar">${i + 1}</div>`, iconSize: [22, 22], iconAnchor: [11, 11] }),
        zIndexOffset: 810,
      }).addTo(_viaLayer);
    });
  }

  // Color de marcadores según modo
  const rutaColor = esP ? "#f59e0b" : "#2563eb";

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
    const color = esMay ? "#f97316" : rutaColor;
    const html  = `<div class="marker-orden" style="background:${color}">${i + 1}</div>`;
    L.marker([p.latitud, p.longitud], {
      icon: L.divIcon({ className: "", html, iconSize: [28,28], iconAnchor: [14,14] }),
    }).bindPopup(`<b>${i+1}. ${h(esMay ? _labelMayorista(p) : p.nombre)}</b>${peso ? `<br>${peso.toLocaleString("es-MX")} kg` : ""}`).addTo(_markersLayer);
  });

  // Fallback línea punteada si aún no hay geometría
  if (!tiempos.geometry?.length && paradas.length > 0 && tiempos.matriz) {
    const pts = [tiempos.matriz];
    paradas.forEach(p => { if (p.latitud != null && p.longitud != null) pts.push([p.latitud, p.longitud]); });
    pts.push(tiempos.matriz);
    L.polyline(pts, { color: "#94a3b8", weight: 2, opacity: 0.6, dashArray: "8 6" }).addTo(_rutaLayer);
  }

  // Incluir pendientes en los límites del mapa para que siempre sean visibles
  Object.values(_pendientes).forEach(suc => {
    if (suc.latitud != null && suc.longitud != null) bounds.push([suc.latitud, suc.longitud]);
  });

  // Incluir mayoristas sin ruta en los límites del mapa
  mayLibres.forEach(m => {
    const may = _normalizarMayorista({ ...m, tipo: "mayorista" });
    if (may?.latitud != null && may?.longitud != null) bounds.push([may.latitud, may.longitud]);
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

function _mayoristasNoAsignados() {
  const asignados = new Set();
  _rutas.forEach(r => (r.mayoristas || []).forEach(m => asignados.add(_docKey(m))));
  return (_mayoristasTodos || []).filter(m => !asignados.has(_docKey(m)));
}

function renderMayoristasLibresLayer(libres = null) {
  if (!_mayoristasLayer) return;
  _mayoristasLayer.clearLayers();
  const lista = libres || _mayoristasNoAsignados();
  lista.forEach(m => {
    const may = _normalizarMayorista({ ...m, tipo: "mayorista" });
    if (!may || may.latitud == null || may.longitud == null) return;
    const peso = may.peso_kg || 0;
    L.marker([may.latitud, may.longitud], {
      icon: L.divIcon({
        className: "",
        html: '<div style="width:26px;height:26px;border-radius:50%;background:#fdba74;color:#7c2d12;display:flex;align-items:center;justify-content:center;font-weight:700;border:2px solid #f97316">M</div>',
        iconSize: [26, 26],
        iconAnchor: [13, 13],
      }),
      zIndexOffset: 500,
    }).bindPopup(
      `<b style="color:#ea580c">Sin ruta: ${h(_labelMayorista(may))}</b>` +
      (peso ? `<br>${peso.toLocaleString("es-MX")} kg` : "") +
      `<br><span style="font-size:0.8em;color:#9a3412">Usa "+ Mayorista" para asignarlo</span>`
    ).addTo(_mayoristasLayer);
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

  // Desactivar modo personalizado de la ruta anterior al cambiar
  const rutaAnterior = _rutasFiltradas[_indiceActivo];
  if (rutaAnterior && _modoPersonalizado[rutaAnterior.id]) {
    _modoPersonalizado[rutaAnterior.id] = false;
    const mapaEl = document.getElementById("mapa");
    if (mapaEl) mapaEl.classList.remove("mapa-personalizar");
  }

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
  const volTotal  = calcularVolumenRuta(ruta);
  document.getElementById("meta-ruta").innerHTML = `
    <span class="meta-item"><i data-lucide="calendar-days" style="width:12px;height:12px"></i> ${capitalizar(ruta.dia)}</span>
    <span class="meta-item"><i data-lucide="package" style="width:12px;height:12px"></i> ${pesoTotal.toLocaleString("es-MX")} kg</span>
    ${volTotal > 0 ? `<span class="meta-item"><i data-lucide="box" style="width:12px;height:12px"></i> ${volTotal.toFixed(3)} m³</span>` : ""}
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
  renderSelectorChofer(ruta);
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
  renderSelectorRuta(ruta);
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

function _capacidadEfectivaTon(cap) {
  const c = Number(cap);
  if (!Number.isFinite(c) || c <= 0) return 0;
  return (c >= 3.5 && c <= 4.0) ? 3.9 : c;
}

function renderSelectorVehiculo(ruta) {
  const zona = document.getElementById("zona-vehiculo");
  if (!zona) return;

  const placasActual = ruta.vehiculo_placas || "";
  const pesoKg       = calcularPesoRuta(ruta);
  const pesoTon      = pesoKg / 1000;

  // Utilización de la ruta con el vehículo actual
  const vehActual    = _vehiculos.find(v => v.placas === placasActual);
  const capActualNom = vehActual ? Number(vehActual.capacidad_ton || 0) : 0;
  const capActualEff = _capacidadEfectivaTon(capActualNom);
  const pctActual    = capActualEff > 0 ? (pesoTon / capActualEff) * 100 : null;

  // Días activos en esta logística (para denominador del indicador semanal)
  const diasConRutas = [...new Set(_rutas.map(r => r.dia).filter(Boolean))];
  const totalDiasSemana = diasConRutas.length || 5;

  // Ordenar: disponibles primero (más cercano al 100%), ocupados al fondo
  const ordenados = [..._vehiculos].sort((a, b) => {
    const aOcu = !!(a.ocupacion || {})[ruta.dia] && a.placas !== placasActual;
    const bOcu = !!(b.ocupacion || {})[ruta.dia] && b.placas !== placasActual;
    if (aOcu !== bOcu) return aOcu ? 1 : -1;
    const pA = _capacidadEfectivaTon(a.capacidad_ton) > 0
      ? Math.abs((pesoTon / _capacidadEfectivaTon(a.capacidad_ton)) * 100 - 100) : 999;
    const pB = _capacidadEfectivaTon(b.capacidad_ton) > 0
      ? Math.abs((pesoTon / _capacidadEfectivaTon(b.capacidad_ton)) * 100 - 100) : 999;
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
    const capNom         = Number(v.capacidad_ton || 0);
    const capTonEff      = _capacidadEfectivaTon(capNom);
    const capKgEff       = capTonEff * 1000;
    const pctRuta        = capKgEff > 0 ? (pesoKg / capKgEff) * 100 : 0;
    const esMejor        = v.placas === mejorPlacas;

    // Color de la barra de utilización para esta ruta
    const utilClass = pctRuta >= 90 && pctRuta <= 110 ? "util-ideal"
                    : pctRuta >= 75 && pctRuta <= 125  ? "util-ok"
                    : pctRuta > 125                     ? "util-sobre"
                    : "util-sub";

    const pctLabel = capKgEff > 0 ? `${pctRuta.toFixed(0)}%` : "—";

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

    const diasUsados     = (v.dias_ocupados || []).length;
    const usoChipClass   = diasUsados === 0 ? "uso-libre"
                         : diasUsados >= totalDiasSemana ? "uso-lleno"
                         : diasUsados >= Math.ceil(totalDiasSemana * 0.6) ? "uso-medio"
                         : "uso-bajo";
    const usoChip = `<span class="veh-uso-chip ${usoChipClass}" title="Usado ${diasUsados} de ${totalDiasSemana} días esta semana">${diasUsados}/${totalDiasSemana}</span>`;

    const capLabel = `${capNom} ton`;
    const tooltip = `${v.abrev || v.descripcion} · ${capLabel} · ${pctRuta.toFixed(1)}% utilización para esta ruta${v.chofer ? " · " + v.chofer : ""}`;

    return `
      <div class="veh-opcion${esActual ? " veh-opcion--actual" : ""}${ocupadoEsteDia ? " veh-opcion--ocupado" : ""}${esMejor && !esActual ? " veh-opcion--mejor" : ""}"
           data-placas="${h(v.placas)}" title="${h(tooltip)}">
        <div class="veh-opcion-top">
          <div class="veh-opcion-info">
            <span class="veh-nombre">${h(v.abrev || v.descripcion)}</span>
            <span class="veh-detalle">${h(v.placas)} · ${capLabel}${v.chofer ? " · " + h(v.chofer) : ""}</span>
          </div>
          <div class="veh-opcion-right">${usoChip}${badge}</div>
        </div>
        <div class="veh-util-wrap">
          <div class="veh-util-track">
            <span class="veh-util-bar ${utilClass}" style="width:${Math.min(pctRuta, 130)}%"></span>
            <span class="veh-util-mark100"></span>
          </div>
          <span class="veh-util-label ${utilClass}">${pctLabel}</span>
        </div>
        <div class="veh-util-sub">${pesoKg.toLocaleString("es-MX")} kg de ${(capKgEff).toLocaleString("es-MX")} kg cap.</div>
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
        <div class="veh-resumen-hint ${rClass}">${rLabel} · ${(capActualEff * 1000).toLocaleString("es-MX")} kg de capacidad</div>
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

  // Actualizar el nombre de la ruta para reflejar el nuevo vehículo
  ruta.nombre = `${vehiculo.abrev || vehiculo.placas} — ${capitalizar(ruta.dia)}`;
  const tituloEl = document.getElementById("titulo-ruta");
  if (tituloEl) tituloEl.textContent = ruta.nombre;

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

  // El chofer por defecto depende del vehículo; si no hay override para esta
  // ruta, el chofer mostrado sigue al nuevo vehículo.
  ruta.chofer_default    = vehiculo.chofer || "";
  ruta.chofer_default_id = vehiculo.chofer_id || null;
  if (!ruta.chofer_personalizado) {
    ruta.chofer    = ruta.chofer_default;
    ruta.chofer_id = ruta.chofer_default_id;
  }

  renderSelectorVehiculo(ruta);
  renderSelectorChofer(ruta);
  renderIndicadores(ruta);
  renderNavRutas();
  mostrarToastMod(`Vehículo cambiado a ${vehiculo.abrev || vehiculo.placas}`, "ok");
}

// ── Selector de chofer (override por ruta/día) ──────────────────
function renderSelectorChofer(ruta) {
  const zona = document.getElementById("zona-chofer");
  if (!zona) return;

  const choferActual = ruta.chofer || "";
  const esPersonalizado = !!ruta.chofer_personalizado;
  const defaultLabel = ruta.chofer_default
    ? `Predeterminado del vehículo (${ruta.chofer_default})`
    : "Predeterminado del vehículo (sin asignar)";

  const opciones = _choferesDisponibles.map(c => {
    const sel = esPersonalizado && c.nombre === choferActual ? "selected" : "";
    return `<option value="${h(c.nombre)}" data-chofer-id="${c._id}" ${sel}>${h(c.nombre)}</option>`;
  }).join("");

  zona.innerHTML = `
    <div class="chofer-selector-row">
      <i data-lucide="user"></i>
      <select id="select-chofer-ruta">
        <option value="" ${!esPersonalizado ? "selected" : ""}>${h(defaultLabel)}</option>
        ${opciones}
      </select>
    </div>
    ${esPersonalizado ? `<span class="chofer-badge-personalizado">Solo esta ruta</span>` : ""}`;

  document.getElementById("select-chofer-ruta").addEventListener("change", e => {
    const choferId = e.target.selectedOptions[0]?.dataset.choferId || null;
    cambiarChofer(ruta, e.target.value, choferId);
  });
  lucide.createIcons();
}

function cambiarChofer(ruta, nuevoChofer, nuevoChoferId = null) {
  ruta.chofer_personalizado = !!nuevoChofer;
  ruta.chofer    = nuevoChofer || ruta.chofer_default || "";
  ruta.chofer_id = nuevoChofer ? nuevoChoferId : (ruta.chofer_default_id || null);

  fetch("/modificacion/actualizar-chofer", {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({ ruta_id: ruta.id, dia: ruta.dia, chofer: nuevoChofer, chofer_id: nuevoChoferId }),
  }).catch(err => console.warn("[actualizar-chofer]", err));

  renderSelectorChofer(ruta);
  mostrarToastMod(
    nuevoChofer ? `Chofer de esta ruta: ${nuevoChofer}` : "Chofer restablecido al predeterminado del vehículo",
    "ok"
  );
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
  const esP  = !!(_modoPersonalizado[ruta.id] && _rutaPersonalizada[ruta.id]);
  const base = _tiempos[ruta.id] || {};
  const cust = _rutaPersonalizada[ruta.id] || {};
  const traslado   = esP ? cust.traslado_min  : base.traslado_min;
  const descarga   = esP ? cust.descarga_min  : base.descarga_min;
  const distancia  = esP ? cust.distancia_km  : base.distancia_km;
  const origen     = esP ? "osrm_personalizada" : base.origen_tiempo;
  const origenClass = (origen === "osrm" || origen === "osrm_personalizada") ? "osrm" : origen === "haversine_fallback" ? "haversine" : "pendiente";
  const origenLabel = origen === "osrm_personalizada"
    ? '<i data-lucide="pencil-ruler" style="width:11px;height:11px"></i> Personalizada'
    : origen === "osrm"
    ? '<i data-lucide="route" style="width:11px;height:11px"></i> OSRM real'
    : origen === "haversine_fallback"
    ? '<i data-lucide="ruler" style="width:11px;height:11px"></i> Haversine'
    : '<i data-lucide="clock" style="width:11px;height:11px"></i> Pendiente';
  document.getElementById("resumen-tiempos").innerHTML = `
    <div class="tiempo-celda"><div class="t-label">Conducción</div><div class="t-valor">${formatMin(traslado)}</div></div>
    <div class="tiempo-celda"><div class="t-label">Descarga</div><div class="t-valor">${formatMin(descarga)}</div></div>
    <div class="tiempo-celda"><div class="t-label">Distancia</div><div class="t-valor">${distancia ? distancia + " km" : "…"}</div></div>
    <div class="tiempo-celda"><div class="t-label">Fuente</div><div class="t-valor"><span class="origen-badge ${origenClass}">${origenLabel}</span></div></div>
  `;
  lucide.createIcons();
}

function renderIndicadores(ruta) {
  const zona   = document.getElementById("zona-indicadores");
  const base   = _tiempos[ruta.id] || {};
  const esP    = !!(_modoPersonalizado[ruta.id] && _rutaPersonalizada[ruta.id]);
  const cust   = _rutaPersonalizada[ruta.id] || {};
  const pesoKg = calcularPesoRuta(ruta);
  let capTon   = ruta.capacidad_ton;
  if (!capTon && ruta.pct_utilizacion > 0 && pesoKg > 0) {
    capTon = parseFloat(((pesoKg / 1000) / (ruta.pct_utilizacion / 100)).toFixed(2));
  }
  capTon = capTon || 2.5;
  const capTonEff = _capacidadEfectivaTon(capTon);
  const pct      = capTonEff > 0 ? (pesoKg / 1000 / capTonEff) * 100 : 0;
  const barClass = pct <= 100 ? "verde" : pct <= 120 ? "naranja" : "rojo";

  const volM3    = calcularVolumenRuta(ruta);
  const vehObj   = _vehiculos.find(v => v.abreviatura === ruta.vehiculo || v.placas === ruta.placas) || null;
  const capVol   = vehObj?.volumen_m3 ?? 0;
  const pctVol   = capVol > 0 ? (volM3 / capVol) * 100 : 0;
  const barVolCls = pctVol <= 100 ? "verde" : pctVol <= 120 ? "naranja" : "rojo";
  const volBarHTML = volM3 > 0 ? `
    <div class="cap-bar-wrap">
      <div class="cap-bar-label">
        <span>Volumen: ${capVol > 0 ? pctVol.toFixed(1) + "%" : "—"}</span>
        <span>${volM3.toFixed(3)}${capVol > 0 ? " / " + capVol.toFixed(2) + " m³" : " m³"}</span>
      </div>
      <div class="cap-bar"><div class="cap-bar-fill ${barVolCls}" style="width:${capVol > 0 ? Math.min(pctVol, 100) : 0}%"></div></div>
    </div>` : "";

  const horaReg    = esP ? (cust.hora_regreso || "—") : (base.hora_regreso || ruta.hora_regreso || "—");
  const horaSalida = _horaSalidaDeRuta(ruta);
  const horaLimite = _horaLimiteDeRuta(ruta);
  const cumple     = horaReg === "—"
    ? (ruta.cumple_horario !== false)
    : horaReg <= horaLimite;

  zona.innerHTML = `
    <div class="cap-bar-wrap">
      <div class="cap-bar-label">
        <span>Peso: ${pct.toFixed(1)}%</span>
        <span>${(pesoKg / 1000).toFixed(2)} / ${capTonEff} ton</span>
      </div>
      <div class="cap-bar"><div class="cap-bar-fill ${barClass}" style="width:${Math.min(pct,100)}%"></div></div>
    </div>
    ${volBarHTML}
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
  const volTotal  = calcularVolumenRuta(ruta);
  const metaEl = document.getElementById("meta-ruta");
  if (metaEl) {
    metaEl.innerHTML = `
      <span class="meta-item"><i data-lucide="calendar-days" style="width:12px;height:12px"></i> ${capitalizar(ruta.dia)}</span>
      <span class="meta-item"><i data-lucide="package" style="width:12px;height:12px"></i> ${pesoTotal.toLocaleString("es-MX")} kg</span>
      ${volTotal > 0 ? `<span class="meta-item"><i data-lucide="box" style="width:12px;height:12px"></i> ${volTotal.toFixed(3)} m³</span>` : ""}
      ${ruta.parte ? `<span class="meta-item">Parte ${ruta.parte} de ${ruta.total_partes}</span>` : ""}
    `;
    lucide.createIcons();
  }
  renderSelectorVehiculo(ruta);
  renderSelectorChofer(ruta);
  renderIndicadores(ruta);
  renderResumenTiempos(ruta);
}

// ── Lista de paradas (sucursales + mayoristas interleaved) ─────

/** Etiqueta canónica de un mayorista: "DOC | Nombre" o sólo "Nombre". */
function _labelMayorista(m) {
  const doc    = m.documento || "";
  const nombre = m.nombre    || `Cliente ${m.id_cliente}`;
  return doc ? `${doc} | ${nombre}` : nombre;
}

/**
 * Devuelve la secuencia combinada y ordenada de sucursales + mayoristas.
 * Cada elemento tiene `tipo`, `orden` y los campos propios.
 */
function _paradasDeRuta(ruta) {
  const sucs = (ruta.sucursales || []).map(s => ({ ...s, tipo: "sucursal" }));
  const mays = (ruta.mayoristas  || []).map(m => _normalizarMayorista({ ...m, tipo: "mayorista" }));
  return [...sucs, ...mays].sort((a, b) => (a.orden ?? 9999) - (b.orden ?? 9999));
}

function _normalizarMayorista(may) {
  if (!may) return may;
  const numOrNull = (v) => {
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  };
  let lat = numOrNull(may.latitud);
  let lon = numOrNull(may.longitud);
  const key = _docKey(may);
  let ref = null;
  if (key) {
    // Buscar primero por clave de documento (específica), luego por id_cliente (genérica)
    ref = _mayoristasTodos.find(m => _docKey(m) === key)
       || _mayoristasTodos.find(m => Number(m.id_cliente) === Number(may.id_cliente))
       || null;
  }
  if (lat == null) lat = numOrNull(ref?.latitud);
  if (lon == null) lon = numOrNull(ref?.longitud);
  return {
    ...may,
    nombre: may.nombre || ref?.nombre,
    latitud: lat,
    longitud: lon,
  };
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
            <div class="suc-nombre">${h(_labelMayorista(p))}</div>
            <div class="suc-detalle may-detalle">
              Mayoristas${p.peso_kg > 0 ? ` · ${p.peso_kg.toLocaleString("es-MX")} kg` : ""}
              ${p.latitud == null ? ' · <span style="color:#ef4444;font-size:0.65rem">sin coords</span>' : ""}
            </div>
          </div>
          <button class="suc-quitar may-quitar" data-idx="${i}" title="Quitar de la ruta"><i data-lucide="x" style="width:13px;height:13px"></i></button>
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

  lista.querySelectorAll(".suc-quitar:not(.may-quitar)").forEach(btn => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      quitarParada(ruta, Number(btn.dataset.idx));
    });
  });

  lista.querySelectorAll(".may-quitar").forEach(btn => {
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
      persistirOrdenParadas(ruta);

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
 * Persiste la secuencia exacta (sucursales + mayoristas entrelazados) de una
 * ruta para que sobreviva a una recarga de página. Se llama tras cualquier
 * cambio de orden: drag & drop, agregar o quitar una parada.
 */
function persistirOrdenParadas(ruta) {
  const ordenParadas = _paradasDeRuta(ruta).map(p => ({
    tipo:  p.tipo,
    key:   p.tipo === "mayorista" ? _docKey(p) : p.num_tienda,
    orden: p.orden,
  }));
  fetch("/modificacion/actualizar-orden", {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({ ruta_id: ruta.id, dia: ruta.dia, orden_paradas: ordenParadas }),
  }).catch(err => console.warn("[actualizar-orden]", err));
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

    if (p.tipo === "mayorista" && p.id_cliente != null) {
      // Persistir retiro de mayorista en MongoDB (fire-and-forget)
      fetch("/modificacion/quitar-mayorista", {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({
          ruta_id:    ruta.id,
          id_cliente: p.id_cliente,
          documento:  p.documento || "",
        }),
      }).catch(err => console.warn("[quitar-mayorista]", err));
    } else if (p.tipo !== "mayorista" && p.num_tienda != null) {
      // Registrar sucursal como pendiente y persistir en MongoDB
      const pesoKg = _pesos[String(p.num_tienda)] || p.peso_kg || 0;
      _pendientes[String(p.num_tienda)] = {
        num_tienda: p.num_tienda,
        nombre:     p.nombre,
        latitud:    p.latitud,
        longitud:   p.longitud,
        peso_kg:    pesoKg,
      };
      fetch("/modificacion/quitar-sucursal", {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({
          ruta_id:    ruta.id,
          dia:        ruta.dia,
          num_tienda: p.num_tienda,
          nombre:     p.nombre,
          latitud:    p.latitud,
          longitud:   p.longitud,
          peso_kg:    pesoKg,
        }),
      }).catch(err => console.warn("[quitar-sucursal]", err));
    }

    paradas.splice(idx, 1);
    paradas.forEach((p, i) => { p.orden = i + 1; });
    _sincronizarParadas(ruta, paradas);
    persistirOrdenParadas(ruta);
    ruta.num_sucursales = ruta.sucursales.length;

    delete _tiempos[ruta.id];
    renderParadas(ruta);
    renderMayoristasLibresLayer();
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

/**
 * Vehículo con el que debería continuar la nueva ruta, para que la
 * asignación "siga donde se quedó" la ruta anterior:
 *   1) El último vehículo usado al crear una ruta en esta sesión (sin
 *      importar el día — así continúa también al pasar al día siguiente).
 *   2) Si aún no se ha creado ninguna ruta en esta sesión, el vehículo de
 *      la última ruta del día hábil inmediatamente anterior con rutas.
 * Solo se sugiere si el vehículo sigue disponible (no ocupado) ese día.
 */
function _vehiculoDeContinuidad(dia) {
  const estaDisponible = (placas) => {
    if (!placas) return false;
    const veh = _vehiculoPorPlacas(placas);
    return !!veh && !(veh.ocupacion || {})[dia];
  };

  if (estaDisponible(_ultimoVehiculoCreado)) return _ultimoVehiculoCreado;

  const idx = DIAS_ORDEN.findIndex(d => d.key === dia);
  for (let i = idx - 1; i >= 0; i--) {
    const diaAnterior = DIAS_ORDEN[i].key;
    const rutasDia = _rutas.filter(r => r.dia === diaAnterior);
    if (rutasDia.length === 0) continue;
    const placas = rutasDia[rutasDia.length - 1].vehiculo_placas;
    if (estaDisponible(placas)) return placas;
    break; // ya encontramos el día anterior con rutas; no seguir retrocediendo
  }
  return "";
}

function abrirModalCrearRuta() {
  if (!_vehiculos || _vehiculos.length === 0) {
    mostrarToastMod("No hay vehículos disponibles para asignar.", "warn");
    return;
  }
  const diaDefault = _diaActivo !== "__todos__"
    ? _diaActivo
    : (DIAS_ORDEN.find(d => _rutas.some(r => r.dia === d.key))?.key || DIAS_ORDEN[0].key);
  const vehiculoDefault = _vehiculoDeContinuidad(diaDefault);
  _crearRuta = { dia: diaDefault, vehiculo: vehiculoDefault, sucursales: [], mayoristas: [], query: "", queryMayoristas: "", verTodasSuc: false, verTodosMay: false };
  renderModalCrearRuta();
  document.getElementById("modal-crear-ruta")?.classList.remove("hidden");
  setTimeout(() => document.getElementById("buscar-sucursal-crear")?.focus(), 100);
}

function cerrarModalCrearRuta() {
  document.getElementById("modal-crear-ruta")?.classList.add("hidden");
}

function renderVehiculosCrearRuta() {
  const vehSel = document.getElementById("crear-vehiculo");
  if (!vehSel) return;

  const dia = _crearRuta.dia;

  // Separar: disponibles primero, ocupados al final
  const disponibles = _vehiculos.filter(v => !(v.ocupacion || {})[dia]);
  const ocupados    = _vehiculos.filter(v =>  (v.ocupacion || {})[dia]);

  const renderOpcion = (v, ocupado) => {
    const nombre = v.abrev || v.descripcion || v.placas || "";
    if (ocupado) {
      const rutaNombre = (v.ocupacion[dia] || {}).ruta_nombre || "otra ruta";
      return `<option value="${h(v.placas)}" disabled>
        ${h(nombre)} · ${h(v.placas)} — No disponible (${h(rutaNombre)})
      </option>`;
    }
    return `<option value="${h(v.placas)}">${h(nombre)} · ${h(v.placas)}</option>`;
  };

  const opciones = [
    '<option value="">Selecciona vehículo…</option>',
    ...disponibles.map(v => renderOpcion(v, false)),
    ...(ocupados.length
      ? [`<option disabled>── No disponibles el ${dia} ──</option>`,
         ...ocupados.map(v => renderOpcion(v, true))]
      : []),
  ];

  vehSel.innerHTML = opciones.join("");
  vehSel.value = _crearRuta.vehiculo;
  actualizarChoferInfoCrearRuta();
}

/** Muestra el chofer predeterminado del vehículo elegido al crear una ruta manual. */
function actualizarChoferInfoCrearRuta() {
  const info = document.getElementById("crear-chofer-info");
  if (!info) return;
  const vehiculo = _vehiculos.find(v => v.placas === _crearRuta.vehiculo);
  const chofer = vehiculo?.chofer || "";
  if (!vehiculo) {
    info.classList.add("hidden");
    info.textContent = "";
    return;
  }
  info.classList.remove("hidden");
  info.innerHTML = chofer
    ? `<i data-lucide="user"></i> Chofer predeterminado: <strong>${h(chofer)}</strong>`
    : `<i data-lucide="user-x"></i> Este vehículo no tiene chofer asignado`;
  lucide.createIcons();
}

function renderModalCrearRuta() {
  const diaSel = document.getElementById("crear-dia");
  if (diaSel) {
    diaSel.innerHTML = DIAS_ORDEN.map(d =>
      `<option value="${d.key}">${d.label}</option>`
    ).join("");
    diaSel.value = _crearRuta.dia;
  }

  renderVehiculosCrearRuta();

  const buscar = document.getElementById("buscar-sucursal-crear");
  if (buscar) buscar.value = _crearRuta.query || "";

  const buscarMayorista = document.getElementById("buscar-mayorista-crear");
  if (buscarMayorista) buscarMayorista.value = _crearRuta.queryMayoristas || "";

  renderListaCrearSucursales();
  renderListaCrearMayoristas();
  actualizarEstadoCrearRuta();
}

function renderListaCrearSucursales() {
  const q = (_crearRuta.query || "").toLowerCase().trim();
  const seleccion = new Set(_crearRuta.sucursales.map(n => String(n)));
  const pendientes = Object.values(_pendientes || {});
  const pendSet = new Set(pendientes.map(p => String(p.num_tienda)));

  // Sucursales ya asignadas en cualquier ruta activa
  const yaAsignadas = new Set();
  _rutas.forEach(r => (r.sucursales || []).forEach(s => yaAsignadas.add(String(s.num_tienda))));

  const filtra = (s) => {
    if (!s) return false;
    const nombre = nombreSucursalLabel(s).toLowerCase();
    const nt = String(s.num_tienda ?? "");
    return !q || nombre.includes(q) || nt.includes(q);
  };

  const LIMITE_SUC = 5;

  const pendientesFil = pendientes.filter(filtra);
  const disponiblesFil = (_sucDisponibles || [])
    .filter(s => !pendSet.has(String(s.num_tienda)))
    .filter(s => !yaAsignadas.has(String(s.num_tienda)))
    .filter(filtra);

  // Mostrar primeras 5 + las ya seleccionadas aunque caigan fuera del límite
  const verTodas = _crearRuta.verTodasSuc;
  const dispVisibles = verTodas
    ? disponiblesFil
    : disponiblesFil.filter((s, i) => i < LIMITE_SUC || seleccion.has(String(s.num_tienda)));
  const ocultas = verTodas ? 0
    : disponiblesFil.filter((s, i) => i >= LIMITE_SUC && !seleccion.has(String(s.num_tienda))).length;

  const renderItem = s => {
    const selected = seleccion.has(String(s.num_tienda));
    return `
      <div class="disp-item${selected ? " selected" : ""}" data-nt="${s.num_tienda}">
        <div>
          <span class="nombre">${h(nombreSucursalLabel(s))}</span>
          <span class="num">#${h(s.num_tienda)}</span>
        </div>
        <span class="sel-check">${selected ? '<i data-lucide="check"></i>' : ""}</span>
      </div>`;
  };

  let html = "";
  if (pendientesFil.length > 0) {
    html += `<div class="disp-seccion-pend"><span class="disp-seccion-label"><i data-lucide="triangle-alert" style="width:13px;height:13px;vertical-align:middle"></i> Pendientes de asignar</span></div>`;
    html += pendientesFil.map(renderItem).join("");
  }

  if (dispVisibles.length > 0) {
    if (pendientesFil.length > 0) {
      html += `<div class="disp-seccion-label disp-seccion-sep">Todas las sucursales</div>`;
    }
    html += dispVisibles.map(renderItem).join("");
  }

  if (ocultas > 0) {
    html += `<button class="btn-ver-mas" id="btn-ver-mas-suc">Ver ${ocultas} más…</button>`;
  }

  if (!html) {
    html = '<div class="lista-sucursales-vacia">No se encontraron sucursales con ese criterio.</div>';
  }

  const lista = document.getElementById("lista-suc-crear");
  if (!lista) return;
  lista.innerHTML = html;
  lucide.createIcons();

  document.getElementById("btn-ver-mas-suc")?.addEventListener("click", () => {
    _crearRuta.verTodasSuc = true;
    renderListaCrearSucursales();
  });

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

function renderListaCrearMayoristas() {
  const LIMITE_MAY = 5;

  const q = (_crearRuta.queryMayoristas || "").toLowerCase().trim();
  // _crearRuta.mayoristas guarda _docKey strings, no id_cliente numbers
  const seleccion = new Set(_crearRuta.mayoristas);
  const disponibles = _mayoristasNoAsignados().filter(m => {
    if (!m) return false;
    const nombre = String(m.nombre || "").toLowerCase();
    const doc    = String(m.documento || "").toLowerCase();
    const idCl   = String(m.id_cliente ?? "");
    return !q || nombre.includes(q) || doc.includes(q) || idCl.includes(q);
  });

  // Mostrar primeros 5 + los ya seleccionados aunque caigan fuera del límite
  const verTodos = _crearRuta.verTodosMay;
  const dispVisibles = verTodos
    ? disponibles
    : disponibles.filter((m, i) => i < LIMITE_MAY || seleccion.has(_docKey(m)));
  const ocultos = verTodos ? 0
    : disponibles.filter((m, i) => i >= LIMITE_MAY && !seleccion.has(_docKey(m))).length;

  let html = "";
  if (dispVisibles.length > 0) {
    html = dispVisibles.map(m => {
      const dk       = _docKey(m);
      const idCl     = String(m.id_cliente ?? "");
      const selected = seleccion.has(dk);
      const peso     = Number(m.peso_kg || 0).toLocaleString("es-MX");
      return `
        <div class="disp-item${selected ? " selected" : ""}" data-doc-key="${h(dk)}">
          <div>
            <span class="nombre">${h(_labelMayorista(m))}</span>
            <span class="num">#${h(idCl)}</span>
          </div>
          <span class="disp-pend-tag">${peso} kg${selected ? " · seleccionado" : ""}</span>
        </div>`;
    }).join("");
  }

  if (ocultos > 0) {
    html += `<button class="btn-ver-mas" id="btn-ver-mas-may">Ver ${ocultos} más…</button>`;
  }

  const lista = document.getElementById("lista-may-crear");
  if (!lista) return;
  lista.innerHTML = html || `<div class="lista-sucursales-vacia">No se encontraron mayoristas con ese criterio.</div>`;
  lucide.createIcons();

  document.getElementById("btn-ver-mas-may")?.addEventListener("click", () => {
    _crearRuta.verTodosMay = true;
    renderListaCrearMayoristas();
  });

  const contador = document.getElementById("crear-contador-may");
  if (contador) {
    contador.textContent = `${_crearRuta.mayoristas.length} seleccionado${_crearRuta.mayoristas.length !== 1 ? "s" : ""}`;
  }

  lista.querySelectorAll(".disp-item").forEach(item => {
    item.addEventListener("click", () => {
      const dk  = item.dataset.docKey;
      const idx = _crearRuta.mayoristas.indexOf(dk);
      if (idx >= 0) _crearRuta.mayoristas.splice(idx, 1);
      else _crearRuta.mayoristas.push(dk);
      renderListaCrearMayoristas();
      actualizarEstadoCrearRuta();
    });
  });
}

function actualizarEstadoCrearRuta() {
  const btn = document.getElementById("btn-crear-confirm");
  if (!btn) return;
  btn.disabled = !_crearRuta.vehiculo || (_crearRuta.sucursales.length === 0 && _crearRuta.mayoristas.length === 0);
}

async function crearRutaManual() {
  if (!_crearRuta.vehiculo) {
    mostrarToastMod("Selecciona un vehículo para la ruta.", "warn");
    return;
  }
  if (_crearRuta.sucursales.length === 0 && _crearRuta.mayoristas.length === 0) {
    mostrarToastMod("Selecciona al menos una sucursal o un mayorista.", "warn");
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

    const mayoristas = _crearRuta.mayoristas.map(dk => {
      const m = (_mayoristasTodos || []).find(item => _docKey(item) === dk) || {};
      return {
        id_cliente: m.id_cliente ?? null,
        documento:  m.documento  || "",
        nombre:     m.nombre     || `Mayorista ${dk}`,
        peso_kg:    Number(m.peso_kg || 0),
        latitud:    m.latitud  ?? null,
        longitud:   m.longitud ?? null,
      };
    });

    const res = await fetch("/modificacion/crear-ruta", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        dia: _crearRuta.dia,
        vehiculo_placas: _crearRuta.vehiculo,
        sucursales,
        mayoristas,
      }),
    });
    const data = await res.json();
    if (!res.ok || data.status !== "ok") {
      throw new Error(data.mensaje || `Error ${res.status}`);
    }

    if (!data.ruta) throw new Error("Respuesta incompleta del servidor");

    _rutas.push(data.ruta);
    _ordenarRutas();
    _ultimoVehiculoCreado = data.ruta.vehiculo_placas || _ultimoVehiculoCreado;
    persistirOrdenParadas(data.ruta);
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
  paradas.push({
    tipo: "sucursal",
    num_tienda:   suc.num_tienda,
    nombre:       nombreResuelto,
    latitud:      suc.latitud,
    longitud:     suc.longitud,
    peso_kg:      peso,
    descarga_min: Math.min(peso * MIN_DESCARGA_POR_KG, MAX_DESCARGA_MIN),
    orden:        maxOrden + 1,
  });
  paradas.sort((a, b) => (a.orden ?? 9999) - (b.orden ?? 9999));
  paradas.forEach((p, i) => { p.orden = i + 1; });
  _sincronizarParadas(ruta, paradas);
  persistirOrdenParadas(ruta);
  ruta.num_sucursales = ruta.sucursales.length;
  delete _tiempos[ruta.id];
  renderParadas(ruta);
  _actualizarIndicadoresPeso(ruta);
  actualizarStatusOSRM();
  renderNavRutas();
  calcularOSRMParaRuta(ruta).then(() => {
    if (_rutasFiltradas[_indiceActivo]?.id === ruta.id) renderContenidoRuta(ruta);
  });
}

// ── Modal agregar mayorista ────────────────────────────────────
function abrirModalAgregarMayorista() {
  document.getElementById("modal-agregar-may").classList.remove("hidden");
  document.getElementById("buscar-mayorista").value = "";
  renderMayoristasDisponibles("");
  setTimeout(() => document.getElementById("buscar-mayorista").focus(), 100);
}
function cerrarModalAgregarMayorista() {
  document.getElementById("modal-agregar-may").classList.add("hidden");
}

function renderMayoristasDisponibles(query) {
  const ruta = _rutasFiltradas[_indiceActivo];
  if (!ruta) return;
  const asignados = new Set();
  _rutas.forEach(r => (r.mayoristas || []).forEach(m => asignados.add(_docKey(m))));
  const enRuta = new Set((ruta.mayoristas || []).map(m => _docKey(m)));
  const q = (query || "").toLowerCase().trim();

  const disponibles = _mayoristasTodos.filter(m => {
    if (asignados.has(_docKey(m))) return false;
    if (enRuta.has(_docKey(m))) return false;
    if (!q) return true;
    return (m.nombre || "").toLowerCase().includes(q) || (m.documento || "").toLowerCase().includes(q) || String(m.id_cliente).includes(q);
  }).slice(0, 50);

  const listaEl = document.getElementById("lista-disponibles-may");
  if (!listaEl) return;

  if (disponibles.length === 0) {
    listaEl.innerHTML = `<div style="padding:16px;color:#94a3b8;font-size:0.82rem;text-align:center">
      ${q ? "Sin resultados para esta búsqueda." : "No hay mayoristas disponibles para agregar."}
    </div>`;
    return;
  }

  listaEl.innerHTML = disponibles.map(m => {
    const peso = (m.peso_kg || 0).toLocaleString("es-MX");
    return `
      <div class="disp-item" data-doc-key="${h(_docKey(m))}">
        <div>
          <span class="nombre">${h(_labelMayorista(m))}</span>
          <span class="num" style="color:#f97316">★ Mayorista</span>
        </div>
        <span style="color:#2563eb;font-size:0.75rem;font-weight:600">${peso} kg · + Agregar</span>
      </div>`;
  }).join("");
  lucide.createIcons();

  listaEl.querySelectorAll(".disp-item").forEach(item => {
    item.addEventListener("click", () => {
      const docKey = item.dataset.docKey;
      const may    = _mayoristasTodos.find(m => _docKey(m) === docKey);
      if (!may) return;
      agregarMayorista(ruta, may);
      cerrarModalAgregarMayorista();
    });
  });
}

async function agregarMayorista(ruta, may) {
  const pesoActual = calcularPesoRuta(ruta);

  try {
    const res = await fetch("/modificacion/agregar-mayorista", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({
        ruta_id:          ruta.id,
        dia:              ruta.dia,
        id_cliente:       may.id_cliente,
        documento:        may.documento || "",
        nombre:           may.nombre,
        latitud:          may.latitud,
        longitud:         may.longitud,
        peso_kg:          may.peso_kg || 0,
        peso_ruta_actual: pesoActual,
      }),
    });
    const data = await res.json();
    if (data.status !== "ok") {
      mostrarToastMod(`Error al agregar mayorista: ${data.mensaje || "desconocido"}`, "err");
      return;
    }

    // Incorporar en el estado en memoria
    if (!ruta.mayoristas) ruta.mayoristas = [];
    const paradas  = _paradasDeRuta(ruta);
    const maxOrden = paradas.reduce((m, p) => Math.max(m, p.orden ?? 0), 0);
    ruta.mayoristas.push({
      tipo:       "mayorista",
      id_cliente: may.id_cliente,
      documento:  may.documento || "",
      nombre:     may.nombre,
      latitud:    may.latitud,
      longitud:   may.longitud,
      peso_kg:    may.peso_kg || 0,
      orden:      maxOrden + 1,
    });

    // Si el vehículo fue cambiado automáticamente, actualizar estado en memoria
    if (data.vehiculo_cambiado && data.nuevo_vehiculo) {
      _cambiarVehiculoEnMemoria(ruta, data.nuevo_vehiculo);
      mostrarToastMod(
        `Vehículo cambiado a ${data.nuevo_vehiculo.abrev || data.nuevo_vehiculo.placas} por capacidad insuficiente`,
        "warn"
      );
    }

    persistirOrdenParadas(ruta);
    delete _tiempos[ruta.id];
    renderParadas(ruta);
    renderMayoristasLibresLayer();
    _actualizarIndicadoresPeso(ruta);
    actualizarStatusOSRM();
    renderNavRutas();
    calcularOSRMParaRuta(ruta).then(() => {
      if (_rutasFiltradas[_indiceActivo]?.id === ruta.id) renderContenidoRuta(ruta);
    });
    mostrarToastMod(`${_labelMayorista(may)} agregado a la ruta`, "ok");
  } catch (err) {
    console.error("[agregarMayorista]", err);
    mostrarToastMod("Error al agregar mayorista", "err");
  }
}

function _cambiarVehiculoEnMemoria(ruta, nuevoVeh) {
  const vAntes = _vehiculoPorPlacas(ruta.vehiculo_placas || "");
  if (vAntes && vAntes.ocupacion) {
    delete vAntes.ocupacion[ruta.dia];
    _recalcularMetricasVehiculo(vAntes);
  }
  const vNuevo = _vehiculoPorPlacas(nuevoVeh.placas || "");
  if (vNuevo) {
    if (!vNuevo.ocupacion) vNuevo.ocupacion = {};
    vNuevo.ocupacion[ruta.dia] = { ruta_id: ruta.id, ruta_nombre: ruta.nombre };
    _recalcularMetricasVehiculo(vNuevo);
  }
  ruta.vehiculo_placas    = nuevoVeh.placas;
  ruta.vehiculo_abrev     = nuevoVeh.abrev;
  ruta.capacidad_ton      = nuevoVeh.capacidad_ton;
  ruta.chofer_default     = nuevoVeh.chofer || "";
  ruta.chofer_default_id  = nuevoVeh.chofer_id || null;
  if (!ruta.chofer_personalizado) {
    ruta.chofer    = ruta.chofer_default;
    ruta.chofer_id = ruta.chofer_default_id;
  }
  renderSelectorVehiculo(ruta);
  renderSelectorChofer(ruta);
  renderIndicadores(ruta);
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
  if (btnSolo)   btnSolo.disabled   = total === 0;
  if (btnSeguir) btnSeguir.disabled = total === 0;
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
      const capTonEff = _capacidadEfectivaTon(capTon);
      const esP     = !!(_modoPersonalizado[ruta.id] && _rutaPersonalizada[ruta.id]);
      const cust    = _rutaPersonalizada[ruta.id] || {};
      const geom    = esP ? (cust.geometry || []) : (t.geometry || []);
      const conducMin = esP ? (cust.traslado_min || 0) : (t.traslado_min || 0);
      const totalMin  = esP ? (cust.total_min    || 0) : (t.total_min    || 0);
      const distKm    = esP ? (cust.distancia_km || 0) : (t.distancia_km || 0);
      const horaReg   = esP ? (cust.hora_regreso || "—") : (t.hora_regreso || ruta.hora_regreso || "—");
      const descMin   = esP ? (cust.descarga_min || t.descarga_min || 0) : (t.descarga_min || 0);
      return {
        id:                 ruta.id,
        nombre:             ruta.nombre,
        tipo:               ruta.tipo,
        dia:                ruta.dia,
        vehiculo_abrev:     ruta.vehiculo_abrev,
        vehiculo_placas:    ruta.vehiculo_placas,
        chofer:             ruta.chofer || "",
        chofer_id:          ruta.chofer_id || null,
        chofer_personalizado: !!ruta.chofer_personalizado,
        capacidad_ton:      capTon,
        peso_kg:            pesoKg,
        peso_ton:           parseFloat((pesoKg / 1000).toFixed(3)),
        pct_utilizacion:    parseFloat(((pesoKg / 1000 / capTonEff) * 100).toFixed(1)),
        conduccion_min:     conducMin,
        descarga_min:       descMin,
        extra_min:          t.extra_min || HORAS_EXTRA_RUTA_MIN,
        total_min:          totalMin,
        distancia_km:       distKm,
        hora_salida:        _horaSalidaDeRuta(ruta),
        hora_regreso:       horaReg,
        origen_tiempo:      esP ? "osrm_personalizada" : (t.origen_tiempo || "desconocido"),
        geometria_osrm:     geom,
        es_personalizada:   esP,
        via_points:         esP ? (_viaPoints[ruta.id] || []) : [],
        puntos_evitar:      esP ? (_puntosEvitar[ruta.id] || []) : [],
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
          documento:  m.documento || "",
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
        const veh = ruta.vehiculo_abrev || "";
        const dia = (ruta.dia || "").toUpperCase();
        for (const suc of (ruta.sucursales || [])) {
          if (!suc.num_tienda) continue;
          filasHistorial.push({
            num_tienda:       suc.num_tienda,
            vehiculo:         veh,
            dia_semana:       dia,
            secuencia_visita: suc.orden ?? 1,
            kg_entrega:       Math.round(_pesos[String(suc.num_tienda)] || suc.peso_kg || 0),
          });
        }
        for (const may of (ruta.mayoristas || [])) {
          if (!may.id_cliente) continue;
          filasHistorial.push({
            tipo:             "mayorista",
            id_cliente:       may.id_cliente,
            vehiculo:         veh,
            dia_semana:       dia,
            secuencia_visita: may.orden ?? 999,
            kg_entrega:       Math.round(may.peso_kg || 0),
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
      const hayRutas = _rutas.length > 0;
      if (btnSolo) {
        btnSolo.disabled = !hayRutas;
        btnSolo.innerHTML = '<i data-lucide="save"></i> Guardar';
        btnSolo.style.background = ""; btnSolo.style.color = "";
        lucide.createIcons();
      }
      if (btnSeguir) {
        btnSeguir.disabled = !hayRutas;
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

function calcularVolumenRuta(ruta) {
  return (ruta.sucursales || []).reduce((sum, s) =>
    sum + (_volumenes[String(s.num_tienda)] || 0), 0);
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

  document.getElementById("btn-aplicar-dia").addEventListener("click", async () => {
    const nuevoDia   = document.getElementById("select-dia-ruta").value;
    const diaOriginal = ruta.dia;
    if (nuevoDia === diaOriginal) return;

    const btnAplicar = document.getElementById("btn-aplicar-dia");
    if (btnAplicar) { btnAplicar.disabled = true; btnAplicar.textContent = "…"; }

    try {
      const res = await fetch("/modificacion/cambiar-dia", {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ ruta_id: ruta.id, dia_actual: diaOriginal, dia_nuevo: nuevoDia }),
      });
      const data = await res.json();
      if (!res.ok || data.status !== "ok") throw new Error(data.mensaje || `Error ${res.status}`);

      // Liberar vehículo en el día original, re-asignar al nuevo día
      _liberarOcupacionVehiculo(ruta);     // libera usando ruta.dia = diaOriginal
      ruta.dia = nuevoDia;
      ruta._modificado = true;
      _marcarOcupacionVehiculo(ruta);      // marca usando ruta.dia = nuevoDia

      // Actualizar filtro de días (los conteos cambian)
      renderFiltroDias();

      // Navegar al nuevo día y seleccionar la ruta movida
      aplicarFiltroDia(nuevoDia);
      const idxNuevo = _rutasFiltradas.findIndex(r => r.id === ruta.id);
      if (idxNuevo >= 0) seleccionarRuta(idxNuevo);

      actualizarProgreso();
      mostrarToastMod(`Ruta movida a ${capitalizar(nuevoDia)}`, "ok");
    } catch (err) {
      mostrarToastMod(`Error al cambiar día: ${err.message}`, "error");
      const sel = document.getElementById("select-dia-ruta");
      if (sel) sel.value = diaOriginal;
      if (btnAplicar) { btnAplicar.disabled = false; btnAplicar.textContent = "Aplicar"; }
    }
  });
}

// ── Sincronización de flota con Configuración ──────────────────
let _ultimaActualizacionFlota = 0;

async function refrescarVehiculos() {
  try {
    const res = await fetch("/modificacion/vehiculos");
    if (!res.ok) return;
    const nuevos = await res.json();
    const antes = _vehiculos.length;
    _vehiculos = nuevos;
    try {
      const resChof = await fetch("/configuracion/choferes");
      if (resChof.ok) _choferesDisponibles = await resChof.json();
    } catch (_) { /* mantiene el catálogo previo si falla */ }
    const ruta = _rutasFiltradas[_indiceActivo];
    if (ruta) {
      const vehActual = _vehiculoPorPlacas(ruta.vehiculo_placas || "");
      ruta.chofer_default    = vehActual?.chofer || "";
      ruta.chofer_default_id = vehActual?.chofer_id || null;
      if (!ruta.chofer_personalizado) {
        ruta.chofer    = ruta.chofer_default;
        ruta.chofer_id = ruta.chofer_default_id;
      }
      renderSelectorVehiculo(ruta);
      renderSelectorChofer(ruta);
    }
    if (nuevos.length !== antes) {
      mostrarToastMod("Flota actualizada automáticamente", "ok");
    }
  } catch (err) {
    console.warn("[refrescarVehiculos]", err);
  }
}

// Cuando Configuración actualiza un vehículo en otra pestaña
window.addEventListener("storage", (e) => {
  if (e.key === "icg_flota_actualizada") refrescarVehiculos();
});

// Cuando el usuario vuelve a esta pestaña después de haber estado en Configuración
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState !== "visible") return;
  const ts = parseInt(localStorage.getItem("icg_flota_actualizada") || "0", 10);
  if (ts > _ultimaActualizacionFlota) {
    _ultimaActualizacionFlota = ts;
    refrescarVehiculos();
  }
});

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
