/**
 * static/js/asignacion.js — v3
 *
 * Novedades:
 *   - El rango óptimo de utilización (_utilMin / _utilMax) se lee desde la
 *     configuración general del sistema (/configuracion/config-general).
 *   - _scoreVehiculo(), barClass, badgeCap, íconos de opciones y el campo
 *     cumple_rango_capacidad usan el rango dinámico (ya no 80/120 hardcodeado).
 *   - El resumen de la asignación generada muestra cuántas rutas quedaron fuera
 *     del rango óptimo.
 *   - Toda la lógica previa (OSRM, config días, guardar, panel de selección)
 *     se conserva sin cambios.
 */

'use strict';

// ── Constantes ──────────────────────────────────────────────────────────────
const DIAS_SEMANA = [
  { key: "lunes",     label: "Lun" },
  { key: "martes",    label: "Mar" },
  { key: "miercoles", label: "Mié" },
  { key: "jueves",    label: "Jue" },
  { key: "viernes",   label: "Vie" },
  { key: "sabado",    label: "Sáb" },
  { key: "domingo",   label: "Dom" },
];

// ── Mensajes contextuales del loader ────────────────────────────────────────
const MSG_ASIG = {
  generar: [
    "Analizando rutas y vehículos…",
    "Calculando pesos por ruta…",
    "Buscando el vehículo más adecuado para cada ruta…",
    "Optimizando la distribución por días…",
    "Aplicando rangos de utilización…",
  ],
  guardar: [
    "Guardando asignación en la base de datos…",
    "Registrando vehículos por ruta…",
    "Guardando detalle por día…",
    "Finalizando guardado…",
  ],
};

const MIN_DESCARGA_POR_KG  = 0.1;
const MAX_DESCARGA_MIN     = 120;
const HORAS_EXTRA_RUTA_MIN = 0;
const VISTA_TODAS          = "__todas__";

// ── Estado global ───────────────────────────────────────────────────────────
let _rutas           = [];
let _vehiculos       = [];
let _pesos           = {};
let _volumenes       = {};
let _tiempos         = {};
let _configDias      = {};
let _asignacionesDia = {};
let _asignaciones    = {};   // { ruta_id: { dia, placas, peso_kg, peso_kg_mayoristas, pct, ... } }
let _diaActivo       = null;
let _cargandoTiempos = false;
let _mayoristas      = {};   // { rutaId: [{id_cliente, nombre, peso_kg, lat, lon, orden}] }
let _mapaLeaflet     = null; // instancia Leaflet activa

// Rango óptimo de utilización — se carga desde /configuracion/config-general
let _utilMin = 80;    // %
let _utilMax = 120;   // %

// Estado del reacomodamiento (Fase 3)
let _reacomoConfig  = null;   // configuración cargada del backend
let _reacomoDetalle = {};     // { ruta_id: { utilization_pct, within_tolerance, ... } }

// Estado de selección
const _seleccionadas = new Set();
const _entregadas    = new Set();
let   _filtroActivo  = "todas";
let   _vistaAgrupada = false;   // false = lista plana, true = agrupado por día
let   _sortDia       = null;    // null | "asc" (Lun→Vie) | "desc" (Vie→Lun)

// ── Init ────────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", async () => {
  lucide.createIcons();
  const activa = await verificarSesionLogistica();
  if (!activa) return;

  await cargarConfigDias();
  await cargarConfigUtilizacion();    // ← carga _utilMin / _utilMax
  await cargarConfigReacomodamiento(); // ← carga _reacomoConfig
  await cargarDatosIniciales();
  renderPanelSeleccion();
  bindEventosPanelSeleccion();
  bindEventosAsignacion();
});

// ══════════════════════════════════════════════════════════════════════════════
// AUTENTICACIÓN / SESIÓN
// ══════════════════════════════════════════════════════════════════════════════

async function verificarSesionLogistica() {
  try {
    const res  = await fetch('/api/activa');
    const data = await res.json();
    if (data.status !== 'ok') { redirigirAlMenu('No hay ninguna logística activa.'); return false; }
    return true;
  } catch { redirigirAlMenu('Error de conexión.'); return false; }
}

function redirigirAlMenu(msg) {
  alert(`${msg}\n\nSerás redirigido al menú principal.`);
  window.location.href = '/';
}

// ══════════════════════════════════════════════════════════════════════════════
// CONFIGURACIÓN GLOBAL (utilización + días)
// ══════════════════════════════════════════════════════════════════════════════

/**
 * Carga desde /configuracion/config-general:
 *   • utilizacion_min / utilizacion_max → _utilMin / _utilMax
 *   • config_dias               → base de _configDias
 *
 * Esta función se llama ANTES de cargarConfigDias(), de modo que la config
 * global actúa como capa base sobre la que se aplican los overrides
 * por logística.
 */
async function cargarConfigUtilizacion() {
  try {
    const res = await fetch("/configuracion/config-general");
    if (!res.ok) return;
    const cfg = await res.json();

    // Rango óptimo de utilización
    const min = parseFloat(cfg.utilizacion_min);
    const max = parseFloat(cfg.utilizacion_max);
    if (!isNaN(min) && min > 0) _utilMin = min;
    if (!isNaN(max) && max > 0) _utilMax = max;

    // Días de operación — se usa como base (puede ser sobreescrita por config
    // de logística en cargarConfigDias)
    if (cfg.config_dias && typeof cfg.config_dias === "object") {
      _configDias = { ...cfg.config_dias };
    }
  } catch (_) {
    // Silencioso — se usarán los defaults de cargarConfigDias
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// CARGA DE DATOS
// ══════════════════════════════════════════════════════════════════════════════

async function cargarDatosIniciales() {
  try {
    const [rutasRes, vehRes, pesosRes, volRes] = await Promise.all([
      fetch("/asignacion/rutas"),
      fetch("/configuracion/vehiculos"),
      fetch("/asignacion/pesos"),
      fetch("/asignacion/volumenes"),
    ]);

    if (pesosRes.status === 400) { redirigirAlMenu('Sin logística activa.'); return; }

    _rutas     = await rutasRes.json();
    _vehiculos = await vehRes.json();
    _pesos     = await pesosRes.json();
    _volumenes = (volRes.ok && volRes.status !== 400) ? await volRes.json() : {};

    // Seleccionar todas por defecto
    _rutas.forEach(r => _seleccionadas.add(String(r._id)));

    // Asignaciones previas
    try {
      const asigRes = await fetch("/asignacion/asignaciones");
      if (asigRes.ok) {
        const prev = await asigRes.json();
        if (prev.asignaciones_por_dia) {
          _asignacionesDia = prev.asignaciones_por_dia;
          // Restaurar dia_programado en rutas: sin esto, rutasDeDia() devuelve
          // vacío al guardar sin re-ejecutar VRP y detalle_por_dia queda en {}.
          for (const [dia, asigs] of Object.entries(_asignacionesDia)) {
            for (const rutaId of Object.keys(asigs)) {
              const ruta = _rutas.find(r => String(r._id) === rutaId);
              if (ruta && !ruta.dia_programado) ruta.dia_programado = dia;
            }
          }
        } else if (prev.asignaciones) {
          const diaOrig = prev.dia || obtenerDiaHoy();
          _asignacionesDia[diaOrig] = prev.asignaciones;
        }
        // Restaurar también el orden de sucursales si hay detalle previo
        if (prev.detalle_por_dia) {
          for (const asigsDia of Object.values(prev.detalle_por_dia)) {
            for (const [rutaId, det] of Object.entries(asigsDia)) {
              const ruta = _rutas.find(r => String(r._id) === rutaId);
              if (!ruta || !det.sucursales?.length) continue;
              const ordenMap = {};
              det.sucursales.forEach(s => { ordenMap[String(s.num_tienda)] = s.orden; });
              ruta.sucursales?.forEach(s => {
                const ord = ordenMap[String(s.num_tienda)];
                if (ord != null) s.orden = ord;
              });
            }
          }
        }
      }
    } catch (_) {}

    // Mayoristas asignados por ruta
    await cargarMayoristas();

  } catch (err) {
    console.error("[cargarDatosIniciales]", err);
    _rutas = []; _vehiculos = []; _pesos = {};
  }
}

async function cargarMayoristas() {
  try {
    const res = await fetch("/asignacion/mayoristas-por-ruta");
    if (res.ok) {
      const data = await res.json();
      // data es { mayoristas: {ruta_id: [...]}, orden_sucursales: {ruta_id: [num_tienda...]}}
      const may = (data && data.mayoristas) ? data.mayoristas : {};
      const ordenSuc = (data && data.orden_sucursales) ? data.orden_sucursales : {};
      _mayoristas = may || {};

      _rutas.forEach(r => {
        const rutaId = String(r._id);
        const ordenList = ordenSuc[rutaId];
        if (!Array.isArray(ordenList) || ordenList.length === 0) return;
        const ordenMap = {};
        ordenList.forEach((num, idx) => { ordenMap[String(num)] = idx + 1; });
        (r.sucursales || []).forEach(s => {
          const ord = ordenMap[String(s.num_tienda)];
          if (ord != null) s.orden = ord;
        });
      });
    }
  } catch (_) {
    _mayoristas = {};
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// PANEL DE SELECCIÓN
// ══════════════════════════════════════════════════════════════════════════════

function renderPanelSeleccion() {
  const rutasFilt = rutasFiltradas_();
  const kanban    = document.getElementById("sel-kanban");
  const spinner   = document.getElementById("sel-spinner");
  const empty     = document.getElementById("sel-empty");

  spinner.style.display = "none";

  if (rutasFilt.length === 0) {
    kanban.style.display = "none";
    empty.style.display  = "flex";
  } else {
    empty.style.display  = "none";
    kanban.style.display = "flex";
    kanban.innerHTML     = _renderKanban(rutasFilt);
    _bindEventosKanban(kanban);
  }

  actualizarResumenSeleccion();
  actualizarBtnGenerar();
  lucide.createIcons();
}

/** Tarjeta visual de una ruta dentro del kanban. */
function renderRutaCard(ruta) {
  const id          = String(ruta._id);
  const esEntregada = _entregadas.has(id);
  const esSel       = _seleccionadas.has(id) && !esEntregada;
  const pesoKg      = calcularPesoRuta(ruta);
  const sucursales  = ruta.sucursales || [];

  const estadoClass = esEntregada ? "is-entregada" : esSel ? "is-incluida" : "is-excluida";
  const estadoBadge = esEntregada
    ? `<span class="sel-card-chip sel-card-chip--entregada" data-tooltip="Ruta marcada como entregada. Ya fue ejecutada en un ciclo anterior y no se considera en la asignación actual."><i data-lucide="check"></i> Entregada</span>`
    : esSel
      ? `<span class="sel-card-chip sel-card-chip--incluida" data-tooltip="Ruta incluida en la asignación. Participará en el cálculo de rutas y se guardará al continuar.">Incluida</span>`
      : `<span class="sel-card-chip sel-card-chip--excluida" data-tooltip="Ruta excluida de la asignación actual. No se procesará ni se enviará al siguiente paso en este ciclo.">Excluida</span>`;

  const sucTags = sucursales.map(s => {
    const nombre = h(s.nombre_base || s.nombre_tienda || s.nombre_pedido || "?");
    const etiqueta = s.num_tienda ? `[${s.num_tienda}] ${nombre}` : nombre;
    return `<span class="sel-card-suc">${etiqueta}</span>`;
  }).join("");

  const footerPeso = pesoKg > 0
    ? `<span><i data-lucide="scale"></i> ${(pesoKg / 1000).toFixed(2)} t</span>`
    : "";

  return `
    <div class="sel-route-card ${estadoClass}" data-rutaid="${h(id)}"
         title="${esEntregada ? "Clic para restaurar la ruta" : "Clic para marcar como entregada"}">
      <div class="sel-route-card__header">
        <span class="sel-route-card__name">${h(ruta.nombre)}</span>
        ${estadoBadge}
      </div>
      ${sucTags ? `<div class="sel-route-card__sucs">${sucTags}</div>` : ""}
      <div class="sel-route-card__footer">
        <span><i data-lucide="map-pin"></i> ${sucursales.length} parada${sucursales.length !== 1 ? "s" : ""}</span>
        ${footerPeso}
      </div>
    </div>`;
}

// ── Orden canónico de días (Lun → Dom) ──────────────────────────────────────
const DIAS_ORDEN_KEYS = ["lunes","martes","miercoles","jueves","viernes","sabado","domingo"];
const DIAS_LABEL_FULL = {
  lunes: "Lunes", martes: "Martes", miercoles: "Miércoles",
  jueves: "Jueves", viernes: "Viernes", sabado: "Sábado", domingo: "Domingo",
};

/**
 * Ordena un array de rutas por día sugerido en orden canónico.
 * Rutas sin día van al final. Respeta la dirección de _sortDia.
 */
function _aplicarSortDia(rutas) {
  if (!_sortDia) return rutas;
  return [...rutas].sort((a, b) => {
    const da = _normalizarDia(a.dia_sugerido || "");
    const db = _normalizarDia(b.dia_sugerido || "");
    const ia = da ? DIAS_ORDEN_KEYS.indexOf(da) : 99;
    const ib = db ? DIAS_ORDEN_KEYS.indexOf(db) : 99;
    if (ia === ib) return 0;
    return _sortDia === "asc" ? ia - ib : ib - ia;
  });
}

/** Genera el HTML completo del tablero kanban agrupado por día. */
function _renderKanban(rutas) {
  const grupos = {};
  rutas.forEach(r => {
    const diaKey = DIAS_ORDEN_KEYS.find(d => d === _normalizarDia(r.dia_sugerido || ""))
                   || "__sin_dia__";
    if (!grupos[diaKey]) grupos[diaKey] = [];
    grupos[diaKey].push(r);
  });

  const diasPresentes = [
    ...DIAS_ORDEN_KEYS.filter(d => grupos[d]),
    ...(grupos["__sin_dia__"] ? ["__sin_dia__"] : []),
  ];

  return diasPresentes.map(diaKey => _renderDayColumn(diaKey, grupos[diaKey])).join("");
}

/** Genera una columna de día con su cabecera y tarjetas. */
function _renderDayColumn(diaKey, rutas) {
  const label     = diaKey === "__sin_dia__" ? "Sin día asignado" : (DIAS_LABEL_FULL[diaKey] || diaKey);
  const nEnt      = rutas.filter(r => _entregadas.has(String(r._id))).length;
  const nTotal    = rutas.length;
  const pesoTotal = rutas.reduce((acc, r) => acc + calcularPesoRuta(r), 0);

  // Rutas operativas (no entregadas): determinar si el día está activo o desactivado
  const rutasOperativas = rutas.filter(r => !_entregadas.has(String(r._id)));
  const todasExcluidas  = rutasOperativas.length > 0
    && rutasOperativas.every(r => !_seleccionadas.has(String(r._id)));

  let btnToggle = "";
  if (diaKey !== "__sin_dia__" && rutasOperativas.length > 0) {
    if (todasExcluidas) {
      btnToggle = `
        <button class="sel-btn-desactivar sel-btn-activar" data-dia="${h(diaKey)}" data-accion="activar"
                title="Volver a incluir todas las rutas de ${h(label)} en la asignación">
          <i data-lucide="circle-check"></i> Activar toda la semana
        </button>`;
    } else {
      btnToggle = `
        <button class="sel-btn-desactivar" data-dia="${h(diaKey)}" data-accion="desactivar"
                title="Excluir todas las rutas de ${h(label)} de la asignación semanal">
          <i data-lucide="ban"></i> Desactivar toda la semana
        </button>`;
    }
  }

  return `
    <div class="sel-day-col ${todasExcluidas ? "sel-day-col--desactivada" : ""}">
      <div class="sel-day-col__header">
        <div class="sel-day-col__top">
          <span class="sel-day-badge sel-day-badge--${h(diaKey)}">${h(label)}</span>
          <span class="sel-day-col__stats">
            ${nTotal} ruta${nTotal !== 1 ? "s" : ""}
            ${nEnt > 0 ? `<span class="sel-day-ent-count">· ${nEnt} ent.</span>` : ""}
            ${pesoTotal > 0 ? `· ${(pesoTotal / 1000).toFixed(1)} t` : ""}
          </span>
        </div>
        ${btnToggle}
      </div>
      <div class="sel-day-col__cards">
        ${rutas.map(r => renderRutaCard(r)).join("")}
      </div>
    </div>`;
}

/** Vincula eventos del tablero kanban. */
function _bindEventosKanban(kanban) {
  // Clic en tarjeta = toggle entregada / restaurar
  kanban.querySelectorAll(".sel-route-card").forEach(card => {
    card.addEventListener("click", () => {
      const id = card.dataset.rutaid;
      if (_entregadas.has(id)) {
        _entregadas.delete(id);
        _seleccionadas.add(id);
      } else {
        _entregadas.add(id);
        _seleccionadas.delete(id);
      }
      renderPanelSeleccion();
      actualizarResumenSeleccion();
      actualizarBtnGenerar();
    });
  });

  // Toggle activar / desactivar todas las rutas del día
  kanban.querySelectorAll(".sel-btn-desactivar").forEach(btn => {
    btn.addEventListener("click", e => {
      e.stopPropagation();
      const diaKey = btn.dataset.dia;
      const accion = btn.dataset.accion; // "activar" | "desactivar"

      _rutas
        .filter(r => _normalizarDia(r.dia_sugerido || "") === diaKey
                  && !_entregadas.has(String(r._id)))
        .forEach(r => {
          const id = String(r._id);
          if (accion === "activar") _seleccionadas.add(id);
          else                      _seleccionadas.delete(id);
        });

      renderPanelSeleccion();
      actualizarResumenSeleccion();
      actualizarBtnGenerar();
    });
  });
}


function rutasFiltradas_() {
  return _rutas.filter(r => {
    const id = String(r._id);
    switch (_filtroActivo) {
      case "seleccionadas":    return _seleccionadas.has(id) && !_entregadas.has(id);
      case "deseleccionadas":  return !_seleccionadas.has(id) && !_entregadas.has(id);
      case "entregadas":       return _entregadas.has(id);
      default:                 return true;
    }
  });
}

function actualizarFilaEstado(rutaId) {
  renderPanelSeleccion();
}

function actualizarMasterCheck() {
  // No aplica en la vista kanban (sin checkbox maestro)
}

function actualizarResumenSeleccion() {
  const el = document.getElementById("sel-summary");
  if (!el) return;
  const nSel  = [..._seleccionadas].filter(id => !_entregadas.has(id)).length;
  const nEnt  = _entregadas.size;
  const nTot  = _rutas.length;
  el.textContent = `${nSel} seleccionadas · ${nEnt} entregadas · ${nTot} total`;
}

function actualizarBtnGenerar() {
  const btn  = document.getElementById("btn-generar-asignacion");
  const info = document.getElementById("sel-action-info");
  if (!btn) return;
  const nSel = [..._seleccionadas].filter(id => !_entregadas.has(id)).length;
  btn.disabled = nSel === 0;
  if (info) {
    info.textContent = nSel > 0
      ? `Se asignarán ${nSel} ruta${nSel !== 1 ? "s" : ""} conforme a los días habilitados en Configuración, buscando el vehículo más próximo al 100 % dentro del rango ${_utilMin}–${_utilMax} %.`
      : "Selecciona al menos una ruta para continuar.";
  }
}

// ── Bind eventos del panel ──────────────────────────────────────────────────
function bindEventosPanelSeleccion() {

  document.getElementById("btn-toggle-sel").addEventListener("click", () => {
    const body = document.getElementById("sel-panel-body");
    const btn  = document.getElementById("btn-toggle-sel");
    const col  = body.classList.toggle("collapsed");
    btn.innerHTML = col
      ? 'Mostrar <i data-lucide="chevron-down"></i>'
      : 'Ocultar <i data-lucide="chevron-up"></i>';
    lucide.createIcons();
    btn.setAttribute("aria-expanded", String(!col));
  });

  document.getElementById("btn-sel-all").addEventListener("click", () => {
    _rutas.filter(r => !_entregadas.has(String(r._id))).forEach(r => _seleccionadas.add(String(r._id)));
    renderPanelSeleccion();
  });

  document.getElementById("btn-desel-all").addEventListener("click", () => {
    _rutas.filter(r => !_entregadas.has(String(r._id))).forEach(r => _seleccionadas.delete(String(r._id)));
    renderPanelSeleccion();
  });

  document.getElementById("btn-invert-sel").addEventListener("click", () => {
    _rutas.filter(r => !_entregadas.has(String(r._id))).forEach(r => {
      const id = String(r._id);
      if (_seleccionadas.has(id)) _seleccionadas.delete(id);
      else                        _seleccionadas.add(id);
    });
    renderPanelSeleccion();
  });

  document.querySelectorAll(".sel-filter").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".sel-filter").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      _filtroActivo = btn.dataset.filter;
      renderPanelSeleccion();
    });
  });

  document.getElementById("btn-generar-asignacion").addEventListener("click", generarAsignacion);
}

/** Actualiza el ícono visual del sort en la cabecera "Día sugerido". */
function _actualizarIconoSort() {
  const icon = document.getElementById("sort-dia-icon");
  const th   = document.getElementById("th-dia-sugerido");
  if (!icon || !th) return;
  if (_sortDia === "asc") {
    icon.innerHTML = '<i data-lucide="arrow-up"></i>';
    th.classList.add("sel-th-sorted");
    th.title = "Ordenado: Lunes → Domingo (clic para invertir)";
  } else if (_sortDia === "desc") {
    icon.innerHTML = '<i data-lucide="arrow-down"></i>';
    th.classList.add("sel-th-sorted");
    th.title = "Ordenado: Domingo → Lunes (clic para invertir)";
  } else {
    icon.innerHTML = '<i data-lucide="chevrons-up-down"></i>';
    th.classList.remove("sel-th-sorted");
    th.title = "Ordenar por día de operación";
  }
  lucide.createIcons();
}

// ══════════════════════════════════════════════════════════════════════════════
// GENERAR ASIGNACIÓN OPTIMIZADA
// ══════════════════════════════════════════════════════════════════════════════

async function generarAsignacion() {
  const btn = document.getElementById("btn-generar-asignacion");
  btn.disabled    = true;
  btn.textContent = "Generando…";
  btn.classList.add("generando");

  const idsExcluidos = [
    ..._rutas
       .map(r => String(r._id))
       .filter(id => !_seleccionadas.has(id) || _entregadas.has(id)),
  ];

  const payload = {
    rutas:         _rutas,
    vehiculos:     _vehiculos,
    pesos:         _pesos,
    volumenes:     _volumenes,
    config_dias:   _configDias,
    ids_excluidos: idsExcluidos,
  };

  Loader.show('Generando Asignación', MSG_ASIG.generar);

  try {
    const res    = await fetch("/asignacion/generar-asignacion", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify(payload),
    });

    if (res.status === 400) { Loader.hide(); redirigirAlMenu("Sin logística activa."); return; }
    if (!res.ok) throw new Error(`HTTP ${res.status}`);

    const resultado = await res.json();

    if (resultado.status !== "ok") {
      Loader.hide();
      alert("Error al generar asignación: " + (resultado.mensaje || "Error desconocido."));
      return;
    }

    // Si el servidor devolvió el rango, sincronizarlo con el estado local
    if (resultado.util_min != null) _utilMin = resultado.util_min;
    if (resultado.util_max != null) _utilMax = resultado.util_max;

    aplicarResultadoAsignacion(resultado);
    calcularDiasProgramados();
    mostrarZonaAsignacion(resultado);
    Loader.hide();  // la zona de asignación ya es visible; OSRM usa su propio banner
    await calcularTiemposOSRM();

  } catch (err) {
    console.error("[generarAsignacion]", err);
    Loader.hide();
    alert("Error de conexión al generar la asignación.");
  } finally {
    btn.disabled  = false;
    btn.innerHTML = '<i data-lucide="zap"></i> Generar asignación';
    btn.classList.remove("generando");
    lucide.createIcons();
  }
}

function aplicarResultadoAsignacion(resultado) {
  /**
   * Aplica el resultado del backend al estado local.
   *
   * Regla clave: el dia_programado de cada ruta siempre se toma de su
   * configuración original (dia_sugerido definido en Creación de Rutas).
   * El día que devuelve el backend (info.dia) solo se usa cuando la ruta
   * no tiene día configurado.
   *
   * Esto garantiza que las rutas predefinidas operen siempre en su día
   * asignado, incluso si el algoritmo del backend sugiriera otro día.
   */
  const asigs   = resultado.asignaciones || {};
  const diasHab = DIAS_SEMANA.filter(d => _configDias[d.key]?.habilitado).map(d => d.key);
  _asignacionesDia = {};
  _asignaciones    = asigs;   // guardar dict completo para el payload VRP

  for (const [rutaId, info] of Object.entries(asigs)) {
    const ruta = _rutas.find(r => String(r._id) === rutaId);
    if (!ruta) continue;

    // Día definitivo: siempre el configurado en la ruta (dia_sugerido).
    // Solo se acepta info.dia del backend si la ruta no tiene día configurado.
    const diaConfig     = _resolverDiaRuta(ruta, diasHab);
    ruta.dia_programado = diaConfig || info.dia;

    if (info.placas) {
      const dia = ruta.dia_programado;
      if (!_asignacionesDia[dia]) _asignacionesDia[dia] = {};
      _asignacionesDia[dia][rutaId] = info.placas;
    }
  }
}

function mostrarZonaAsignacion(resultado) {
  const panelBody = document.getElementById("sel-panel-body");
  const btnToggle = document.getElementById("btn-toggle-sel");
  panelBody.classList.add("collapsed");
  btnToggle.innerHTML = 'Mostrar <i data-lucide="chevron-down"></i>';
  lucide.createIcons();
  btnToggle.setAttribute("aria-expanded", "false");

  document.getElementById("zona-asignacion").style.display = "block";

  const resumenEl = document.createElement("div");
  resumenEl.className = "resumen-asignacion";
  const nSinVeh    = resultado.sin_vehiculo || 0;
  const nFueraRng  = resultado.fuera_rango  || 0;
  const nTotal     = resultado.total_rutas  || 0;
  const utilMin    = resultado.util_min ?? _utilMin;
  const utilMax    = resultado.util_max ?? _utilMax;

  let alertasHTML = "";
  if (nSinVeh > 0)
    alertasHTML += ` <span style="color:var(--amarillo)"><i data-lucide="triangle-alert"></i> ${nSinVeh} sin vehículo disponible.</span>`;
  if (nFueraRng > 0)
    alertasHTML += ` <span style="color:var(--naranja, #f97316)"><i data-lucide="triangle-alert"></i> ${nFueraRng} fuera del rango ${utilMin}–${utilMax} % (fallback).</span>`;
  if (nSinVeh === 0 && nFueraRng === 0)
    alertasHTML = ` <span style="color:var(--verde)">Todas dentro del rango óptimo ${utilMin}–${utilMax} %.</span>`;

  resumenEl.innerHTML = `
    <strong><i data-lucide="check-circle-2"></i> Asignación generada:</strong>
    ${nTotal} ruta${nTotal !== 1 ? "s" : ""} procesada${nTotal !== 1 ? "s" : ""}.
    ${alertasHTML}
    <span style="color:var(--texto-sub)">Ordenadas de menor a mayor peso · Vehículo más próximo al 100 %.</span>`;

  const grid = document.getElementById("rutas-grid");
  grid.parentElement.insertBefore(resumenEl, grid);
  lucide.createIcons();

  renderDiaSelector();
  activarDia(obtenerPrimerDiaConRutas() || obtenerDiaHoy());
}

function obtenerPrimerDiaConRutas() {
  for (const { key } of DIAS_SEMANA) {
    if (_configDias[key]?.habilitado && _rutasActivas().some(r => r.dia_programado === key)) {
      return key;
    }
  }
  return null;
}

// ══════════════════════════════════════════════════════════════════════════════
// EVENTOS DE LA ZONA DE ASIGNACIÓN
// ══════════════════════════════════════════════════════════════════════════════

function bindEventosAsignacion() {
  document.getElementById("btn-config-dias")?.addEventListener("click", abrirModalConfigDias);
  document.getElementById("modal-dias-cancel")?.addEventListener("click", () => cerrarModal("modal-config-dias"));
  document.getElementById("modal-dias-save")?.addEventListener("click", guardarConfigDias);

  // Tooltips: ajusta dirección según posición en pantalla
  function _ajustarDireccionTooltip(badge) {
    const rect = badge.getBoundingClientRect();
    badge.classList.toggle("tooltip-abajo", rect.top < 200);
  }
  document.addEventListener("mouseover", e => {
    const badge = e.target.closest("[data-tooltip]");
    if (badge) _ajustarDireccionTooltip(badge);
  });

  // Tooltips: clic ancla/desancla; clic fuera cierra
  document.addEventListener("click", e => {
    const badge = e.target.closest("[data-tooltip]");
    if (badge) {
      _ajustarDireccionTooltip(badge);
      const yaActivo = badge.classList.contains("tooltip-activo");
      document.querySelectorAll(".tooltip-activo").forEach(el => el.classList.remove("tooltip-activo"));
      if (!yaActivo) badge.classList.add("tooltip-activo");
      e.stopPropagation();
    } else {
      document.querySelectorAll(".tooltip-activo").forEach(el => el.classList.remove("tooltip-activo"));
    }
  });

  // Auto-asignar vehículos
  document.getElementById("btn-auto-asignar")?.addEventListener("click", autoAsignarVehiculos);

  // Reacomodamiento
  document.getElementById("btn-config-reacomo")?.addEventListener("click", abrirModalReacomo);
  document.getElementById("modal-reacomo-cancel")?.addEventListener("click", () => cerrarModal("modal-reacomo"));
  document.getElementById("modal-reacomo-apply")?.addEventListener("click", aplicarReacomodamiento);

  // Guardar y continuar → guarda y redirige a validación
  document.getElementById("btn-guardar-continuar")?.addEventListener("click", () => guardarAsignacion(true));
  // Solo guardar → guarda y permanece en la misma sección
  document.getElementById("btn-solo-guardar")?.addEventListener("click", () => guardarAsignacion(false));

  document.getElementById("btn-volver-sel")?.addEventListener("click", () => {
    document.getElementById("zona-asignacion").style.display = "none";
    const panelBody = document.getElementById("sel-panel-body");
    const btnToggle = document.getElementById("btn-toggle-sel");
    panelBody.classList.remove("collapsed");
    btnToggle.innerHTML = 'Ocultar <i data-lucide="chevron-up"></i>';
    lucide.createIcons();
    btnToggle.setAttribute("aria-expanded", "true");
    document.querySelectorAll(".resumen-asignacion").forEach(el => el.remove());
    document.querySelectorAll(".resumen-reacomo").forEach(el => el.remove());
  });

  document.getElementById("btn-cerrar-mapa")?.addEventListener("click", cerrarPanelMapa);
  document.getElementById("panel-mapa-backdrop")?.addEventListener("click", cerrarPanelMapa);
}

// ══════════════════════════════════════════════════════════════════════════════
// CÁLCULO DE TIEMPOS OSRM
// ══════════════════════════════════════════════════════════════════════════════

async function calcularTiemposOSRM() {
  const rutasAsig = _rutas.filter(r => r.dia_programado);
  if (rutasAsig.length === 0) return;
  _cargandoTiempos = true;
  mostrarBannerCargandoTiempos(true);

  try {
    const res = await fetch("/asignacion/calcular-tiempos", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rutas: rutasAsig, pesos: _pesos }),
    });
    if (res.status === 400) { redirigirAlMenu("Sin logística activa."); return; }
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    _tiempos = await res.json();
  } catch (err) {
    console.error("[calcularTiemposOSRM]", err);
    _tiempos = {};
  } finally {
    _cargandoTiempos = false;
    mostrarBannerCargandoTiempos(false);
    renderRutas();
    renderIndicadoresDia();
  }
}

async function recalcularTiempoRuta(rutaId) {
  try {
    const res = await fetch(`/asignacion/calcular-tiempos/${rutaId}`);
    if (res.ok) {
      _tiempos[rutaId] = await res.json();
      renderRutas();
      renderIndicadoresDia();
    }
  } catch (err) {
    console.error(`[recalcularTiempoRuta:${rutaId}]`, err);
  }
}

function mostrarBannerCargandoTiempos(mostrar) {
  const banner = document.getElementById("banner-tiempos");
  if (banner) banner.style.display = mostrar ? "flex" : "none";
}

// ══════════════════════════════════════════════════════════════════════════════
// PROGRAMACIÓN AUTOMÁTICA DE DÍAS
// ══════════════════════════════════════════════════════════════════════════════

// DIA_MAP_ES ya no se usa en calcularDiasProgramados; se conserva por compatibilidad.
const DIA_MAP_ES = {
  lunes: "lunes", martes: "martes", miercoles: "miércoles",
  jueves: "jueves", viernes: "viernes", sabado: "sábado", domingo: "domingo",
};

function calcularDiasProgramados() {
  /**
   * Asigna dia_programado a cada ruta respetando su configuración original.
   *
   * Regla 1 — Rutas con dia_sugerido configurado:
   *   Se asigna siempre a ese día si está habilitado.
   *   El sistema NO reasigna estas rutas a otro día para balancear carga.
   *
   * Regla 2 — Rutas sin dia_sugerido:
   *   Se distribuyen en el día habilitado con menos rutas asignadas.
   */
  const diasHab = DIAS_SEMANA.filter(d => _configDias[d.key]?.habilitado).map(d => d.key);
  if (diasHab.length === 0) return;

  const rutasPorDia = {};
  diasHab.forEach(d => { rutasPorDia[d] = 0; });

  // Primero limpiar dia_programado de rutas excluidas/entregadas
  _rutas.forEach(r => {
    if (!_seleccionadas.has(String(r._id)) || _entregadas.has(String(r._id))) {
      r.dia_programado = null;
    }
  });

  const activas = _rutasActivas();

  // PRIMERA PASADA: rutas activas con día configurado → fijar su día
  activas.forEach(r => {
    const diaConfig = _resolverDiaRuta(r, diasHab);
    if (diaConfig) {
      r.dia_programado = diaConfig;
      rutasPorDia[diaConfig] = (rutasPorDia[diaConfig] || 0) + 1;
    }
  });

  // SEGUNDA PASADA: rutas activas sin día configurado → día menos cargado
  activas.filter(r => !_resolverDiaRuta(r, diasHab)).forEach(r => {
    const diaMenos = diasHab.reduce((min, d) =>
      (rutasPorDia[d] || 0) < (rutasPorDia[min] || 0) ? d : min);
    r.dia_programado = diaMenos;
    rutasPorDia[diaMenos]++;
  });
}

// ══════════════════════════════════════════════════════════════════════════════
// ASIGNACIONES POR DÍA (estado local)
// ══════════════════════════════════════════════════════════════════════════════

function asignacionesDelDia(diaKey) {
  if (!_asignacionesDia[diaKey]) _asignacionesDia[diaKey] = {};
  return _asignacionesDia[diaKey];
}

function asignarVehiculoEnDia(rutaId, diaKey, placas) {
  if (!_asignacionesDia[diaKey]) _asignacionesDia[diaKey] = {};
  _asignacionesDia[diaKey][rutaId] = placas;
}

// ══════════════════════════════════════════════════════════════════════════════
// CONFIG DE DÍAS
// ══════════════════════════════════════════════════════════════════════════════

/**
 * Aplica la config de días específica de la logística activa (si existe)
 * como override sobre la base global ya cargada en cargarConfigUtilizacion().
 *
 * Orden de precedencia (de menor a mayor):
 *   1. Defaults hardcodeados (lunes–sábado habilitado, 07:00–18:00)
 *   2. Config global de Configuración  ← cargada en cargarConfigUtilizacion()
 *   3. Config por logística            ← cargada aquí como override
 */
async function cargarConfigDias() {
  // Aplicar override de la logística activa (ya tenemos la base global en _configDias)
  try {
    const res = await fetch("/asignacion/config-dias");
    if (res.ok) {
      const configLogistica = await res.json();
      if (configLogistica && typeof configLogistica === "object"
          && Object.keys(configLogistica).length > 0) {
        // Override día a día: la config por logística prevalece sobre la global
        _configDias = { ..._configDias, ...configLogistica };
      }
    }
  } catch (_) {}

  // Garantizar que todos los días tengan estructura mínima
  DIAS_SEMANA.forEach(({ key }) => {
    if (!_configDias[key]) {
      _configDias[key] = {
        habilitado:  key !== "domingo",
        hora_salida: "07:00",
        hora_limite: "18:00",
      };
    }
  });
}

// ── Día activo ───────────────────────────────────────────────────────────────

function obtenerDiaHoy() {
  const nombres = ["domingo","lunes","martes","miercoles","jueves","viernes","sabado"];
  const hoy     = nombres[new Date().getDay()];
  const idx     = DIAS_SEMANA.findIndex(d => d.key === hoy);
  for (let i = 0; i < 7; i++) {
    const d = DIAS_SEMANA[(idx + i) % 7];
    if (_configDias[d.key]?.habilitado) return d.key;
  }
  return "lunes";
}

function siguienteDiaHabilitado(diaKey) {
  const idx = DIAS_SEMANA.findIndex(d => d.key === diaKey);
  for (let i = 1; i <= 7; i++) {
    const d = DIAS_SEMANA[(idx + i) % 7];
    if (_configDias[d.key]?.habilitado) return d.key;
  }
  return diaKey;
}

function activarDia(diaKey) {
  _diaActivo = diaKey;
  document.querySelectorAll(".dia-btn").forEach(btn =>
    btn.classList.toggle("activo", btn.dataset.dia === diaKey));
  renderIndicadoresDia();
  renderRutas();
}

function renderDiaSelector() {
  const cont = document.getElementById("dia-selector");
  if (!cont) return;

  const btnTodas = `<button class="dia-btn dia-btn-todas ${_diaActivo === VISTA_TODAS ? "activo" : ""}"
    data-dia="${VISTA_TODAS}">Todas</button>`;

  const btnsDias = DIAS_SEMANA.map(({ key, label }) => {
    const cfg      = _configDias[key] || {};
    const rutasCnt = _rutasActivas().filter(r => r.dia_programado === key).length;
    const badge    = rutasCnt > 0 ? `<sup class="dia-badge">${rutasCnt}</sup>` : "";
    return `<button class="dia-btn ${!cfg.habilitado ? "deshabilitado" : ""} ${_diaActivo === key ? "activo" : ""}"
      data-dia="${key}">${label}${badge}</button>`;
  }).join("");

  cont.innerHTML = btnTodas + btnsDias;
  cont.querySelectorAll(".dia-btn").forEach(btn => {
    if (!btn.classList.contains("deshabilitado"))
      btn.addEventListener("click", () => activarDia(btn.dataset.dia));
  });
}

// ══════════════════════════════════════════════════════════════════════════════
// INDICADORES DEL DÍA
// ══════════════════════════════════════════════════════════════════════════════

function renderIndicadoresDia() {
  const esTodas    = _diaActivo === VISTA_TODAS;
  const cfg        = esTodas ? {} : (_configDias[_diaActivo] || {});
  const rutasDia   = rutasDelVistaActual();
  const pesoTotal  = rutasDia.reduce((acc, r) => acc + calcularPesoRuta(r), 0);
  const volTotal   = rutasDia.reduce((acc, r) => acc + calcularVolumenRuta(r), 0);
  const asignadas  = rutasDia.filter(r => {
    const asig = esTodas ? asignacionesDelDia(r.dia_programado) : asignacionesDelDia(_diaActivo);
    return !!asig[r._id];
  }).length;

  document.getElementById("ind-rutas").textContent     = rutasDia.length;
  document.getElementById("ind-asignadas").textContent = asignadas;
  document.getElementById("ind-peso").textContent      = `${(pesoTotal / 1000).toFixed(2)} t`;
  document.getElementById("ind-volumen").textContent   = `${volTotal.toFixed(3)} m³`;
  document.getElementById("ind-salida").textContent    = esTodas ? "—" : (cfg.hora_salida || "—");
  document.getElementById("ind-limite").textContent    = esTodas
    ? "vista general" : `límite: ${cfg.hora_limite || "—"}`;
}

// ══════════════════════════════════════════════════════════════════════════════
// FILTRADO Y PESO
// ══════════════════════════════════════════════════════════════════════════════

/**
 * Rutas activas: incluidas en la selección (no excluidas, no entregadas).
 * Son las únicas que participan en asignación, conteo de días y grilla.
 */
function _rutasActivas() {
  return _rutas.filter(r =>
    _seleccionadas.has(String(r._id)) && !_entregadas.has(String(r._id))
  );
}

function rutasDelVistaActual() {
  const activas = _rutasActivas();
  if (_diaActivo === VISTA_TODAS) return activas;
  return activas.filter(r => r.dia_programado === _diaActivo);
}

function rutasDeDia(diaKey) {
  const activas = _rutasActivas();
  if (diaKey === VISTA_TODAS) return activas;
  return activas.filter(r => r.dia_programado === diaKey);
}

function calcularPesoRuta(ruta) {
  const pesoSuc = (ruta.sucursales || []).reduce((acc, s) =>
    acc + (_pesos[String(s.num_tienda)] || 0), 0);
  const pesoMay = (_mayoristas[String(ruta._id)] || []).reduce((acc, m) =>
    acc + (m.peso_kg || 0), 0);
  return pesoSuc + pesoMay;
}

function calcularVolumenRuta(ruta) {
  return (ruta.sucursales || []).reduce((acc, s) =>
    acc + (_volumenes[String(s.num_tienda)] || 0), 0);
}

// ══════════════════════════════════════════════════════════════════════════════
// SUGERENCIA / CÁLCULO DE VEHÍCULO
// ══════════════════════════════════════════════════════════════════════════════

function placasOcupadasEnDia(diaKey, excluirRutaId) {
  const mapa    = asignacionesDelDia(diaKey);
  const ocupadas = new Set();
  for (const [rutaId, placas] of Object.entries(mapa)) {
    if (placas && rutaId !== excluirRutaId) ocupadas.add(placas);
  }
  return ocupadas;
}

function _capacidadEfectivaTon(cap) {
  const c = Number(cap);
  if (!Number.isFinite(c) || c <= 0) return 0;
  return Math.abs(c - 3.5) < 0.05 ? 4 : c;
}

/**
 * Puntaje de idoneidad de un vehículo para una carga dada.
 * Usa _utilMin / _utilMax cargados desde la configuración del sistema.
 * Menor puntaje = mejor candidato.
 *
 *   • Dentro del rango  → |pct - 100|                (prefiere más cercano al 100 %)
 *   • Fuera del rango   → 100 + distancia al borde   (siempre peor que cualquier candidato dentro del rango)
 */
function _scoreVehiculo(pesoTon, vehiculo) {
  const cap = _capacidadEfectivaTon(vehiculo.capacidad_toneladas || 0);
  if (cap <= 0) return Infinity;
  const pct = (pesoTon / cap) * 100;
  if (pct >= _utilMin && pct <= _utilMax) return Math.abs(pct - 100);
  if (pct < _utilMin)  return 100 + (_utilMin - pct);
  return 100 + (pct - _utilMax);
}

function _elegirMejor(pesoTon, candidatos) {
  if (candidatos.length === 0) return null;
  return candidatos.reduce((mejor, v) =>
    _scoreVehiculo(pesoTon, v) < _scoreVehiculo(pesoTon, mejor) ? v : mejor);
}

function sugerirVehiculo(pesoKg, diaKey, excluirRutaId = null, volumenM3 = 0) {
  const pesoTon  = pesoKg / 1000;
  const ocupadas = placasOcupadasEnDia(diaKey, excluirRutaId);
  const libres   = _vehiculos.filter(v =>
    !ocupadas.has(v.placas)
    && (v.capacidad_toneladas || 0) > 0
    && (!(v.volumen_m3 || 0) || volumenM3 < v.volumen_m3)
  );
  return _elegirMejor(pesoTon, libres);
}

function calcularPct(pesoKg, vehiculo) {
  if (!vehiculo?.capacidad_toneladas) return 0;
  return (pesoKg / 1000 / _capacidadEfectivaTon(vehiculo.capacidad_toneladas)) * 100;
}

/** Devuelve true si el porcentaje está dentro del rango óptimo configurado. */
function enRangoOptimo(pct) {
  return pct >= _utilMin && pct <= _utilMax;
}

// ══════════════════════════════════════════════════════════════════════════════
// RENDER GRID DE RUTAS
// ══════════════════════════════════════════════════════════════════════════════

function renderRutas() {
  const grid     = document.getElementById("rutas-grid");
  const esTodas  = _diaActivo === VISTA_TODAS;
  const rutasVis = rutasDelVistaActual();

  if (rutasVis.length === 0) {
    grid.innerHTML = `
      <div class="sin-rutas" style="grid-column:1/-1">
        <div class="icon"><i data-lucide="map"></i></div>
        <p>${esTodas ? "No hay rutas asignadas." : "No hay rutas programadas para este día."}</p>
      </div>`;
    return;
  }

  const rutasOrdenadas = [...rutasVis].sort((a, b) => calcularPesoRuta(a) - calcularPesoRuta(b));

  grid.innerHTML = rutasOrdenadas.map((r, idx) => {
    const diaRuta = esTodas ? r.dia_programado : _diaActivo;
    const cfg     = _configDias[diaRuta] || {};
    return renderTarjetaRuta(r, cfg, diaRuta, idx + 1);
  }).join("");

  bindCardEventosTodos(grid, rutasOrdenadas);
  lucide.createIcons();
}

function bindCardEventosTodos(grid, rutas) {
  grid.querySelectorAll(".select-vehiculo").forEach(sel => {
    sel.addEventListener("change", () => {
      asignarVehiculoEnDia(sel.dataset.rutaid, sel.dataset.dia, sel.value);
      renderIndicadoresDia();
      renderDiaSelector();
      renderRutas();
    });
  });

  grid.querySelectorAll(".toggle-sucursales").forEach(btn => {
    btn.addEventListener("click", () => toggleSucursales(btn));
  });

  grid.querySelectorAll(".btn-mapa").forEach(btn => {
    btn.addEventListener("click", () => abrirMapaRuta(btn.dataset.rutaid));
  });

  grid.querySelectorAll(".btn-recalcular").forEach(btn => {
    btn.addEventListener("click", () => {
      const rutaId = btn.dataset.rutaid;
      btn.disabled = true; btn.textContent = "…";
      recalcularTiempoRuta(rutaId).finally(() => {
        btn.disabled = false;
        btn.innerHTML = '<i data-lucide="rotate-cw"></i>';
        lucide.createIcons();
      });
    });
  });

  grid.querySelectorAll(".btn-reprogramar").forEach(btn => {
    btn.addEventListener("click", () => {
      const rutaId = btn.dataset.rutaid;
      const ruta   = _rutas.find(r => r._id === rutaId);
      if (!ruta) return;
      const siguienteDia = siguienteDiaHabilitado(ruta.dia_programado);
      if (siguienteDia === ruta.dia_programado) return;
      asignarVehiculoEnDia(rutaId, ruta.dia_programado, "");
      ruta.dia_programado = siguienteDia;
      const sug = sugerirVehiculo(calcularPesoRuta(ruta), siguienteDia, rutaId);
      if (sug) asignarVehiculoEnDia(rutaId, siguienteDia, sug.placas);
      renderIndicadoresDia();
      renderDiaSelector();
      renderRutas();
    });
  });
}

function renderTarjetaRuta(ruta, cfgDia, diaRuta, orden) {
  const pesoKg    = calcularPesoRuta(ruta);
  const volumenM3 = calcularVolumenRuta(ruta);
  const tiempos   = _tiempos[ruta._id];
  const asigDia   = asignacionesDelDia(diaRuta);
  const ocupadas  = placasOcupadasEnDia(diaRuta, ruta._id);

  let vehiculoAsig = asigDia[ruta._id]
    ? _vehiculos.find(v => v.placas === asigDia[ruta._id])
    : sugerirVehiculo(pesoKg, diaRuta, ruta._id, volumenM3);

  if (!asigDia[ruta._id] && vehiculoAsig) {
    asignarVehiculoEnDia(ruta._id, diaRuta, vehiculoAsig.placas);
  }

  const pct = calcularPct(pesoKg, vehiculoAsig);

  // Utilización volumétrica
  const capVol   = vehiculoAsig?.volumen_m3 || 0;
  const pctVol   = capVol > 0 ? (volumenM3 / capVol) * 100 : null;
  const cumpleVol = capVol <= 0 || volumenM3 < capVol;

  // barClass y badge usan el rango dinámico
  const barClass  = enRangoOptimo(pct) ? "ok" : pct < _utilMin ? "warn" : "error";
  const tipoCap   = enRangoOptimo(pct)
    ? "Utilización en rango óptimo. El vehículo aprovecha bien su capacidad de carga."
    : pct < _utilMin
      ? `Vehículo subutilizado. La carga está por debajo del mínimo configurado (${_utilMin}%). Considera consolidar paradas con otra ruta.`
      : `Vehículo sobrecargado. La carga supera el máximo configurado (${_utilMax}%). Asigna un vehículo de mayor capacidad.`;
  const badgeCap  = enRangoOptimo(pct)
    ? `<i data-lucide="check"></i> ${pct.toFixed(0)}% — Dentro del rango`
    : pct < _utilMin
      ? `<i data-lucide="triangle-alert"></i> ${pct.toFixed(0)}% — Subutilizado (mín. ${_utilMin}%)`
      : `<i data-lucide="x"></i> ${pct.toFixed(0)}% — Sobrecargado (máx. ${_utilMax}%)`;

  const diaLabel    = DIAS_SEMANA.find(d => d.key === diaRuta)?.label || diaRuta;
  const badgeDiaHTML = _diaActivo === VISTA_TODAS
    ? `<span class="badge-indicador badge-neutral" data-tooltip="Esta ruta está programada para el día ${diaLabel}. Usa el filtro de días para ver solo las rutas de un día específico."><i data-lucide="calendar"></i> ${diaLabel}</span>`
    : "";

  const origenBadge = tiempos
    ? tiempos.origen_tiempo === "osrm"
      ? `<span class="badge-indicador badge-info" data-tooltip="Tiempo calculado con OpenStreetMap (OSRM). Representa el trayecto real por carretera con condiciones de tráfico estándar."><i data-lucide="map-pin"></i> OSM</span>`
      : tiempos.origen_tiempo === "haversine_fallback"
      ? `<span class="badge-indicador badge-warn" data-tooltip="Tiempo estimado por distancia en línea recta. El servicio OSRM no está disponible. El valor real puede ser mayor."><i data-lucide="triangle-alert"></i> Estimado</span>`
      : ""
    : `<span class="badge-indicador badge-neutral" data-tooltip="Calculando el tiempo de trayecto con OpenStreetMap. Espera unos segundos."><i data-lucide="loader"></i> Calculando…</span>`;

  let alertaTiempoHTML = "";
  if (tiempos && cfgDia.hora_salida && cfgDia.hora_limite) {
    const salidaMin  = parseHHMM(cfgDia.hora_salida);
    const limiteMin  = parseHHMM(cfgDia.hora_limite);
    const llegadaMin = salidaMin + tiempos.total_min;
    alertaTiempoHTML = llegadaMin > limiteMin
      ? `<div class="alerta-tiempo error" data-tooltip="La ruta excede el horario límite de regreso configurado para este día. Considera reducir paradas o asignar un vehículo más rápido."><i data-lucide="triangle-alert"></i> Regresa a las ${minutosAHHMM(llegadaMin)} — excede por ${formatMinutos(llegadaMin - limiteMin)}</div>`
      : `<div class="alerta-tiempo ok" data-tooltip="La ruta regresa antes del límite horario configurado para este día. Hay margen de tiempo disponible."><i data-lucide="check"></i> Regresa a las ${minutosAHHMM(llegadaMin)} — margen de ${formatMinutos(limiteMin - llegadaMin)}</div>`;
  }

  const pesoTon = pesoKg / 1000;
  const vehiculosOrdenados = [..._vehiculos].sort((a, b) => {
    const ocA = ocupadas.has(a.placas);
    const ocB = ocupadas.has(b.placas);
    if (ocA && !ocB) return 1;
    if (!ocA && ocB) return -1;
    return _scoreVehiculo(pesoTon, a) - _scoreVehiculo(pesoTon, b);
  });

  const opcionesVehiculos = vehiculosOrdenados.map(v => {
    const pctV     = calcularPct(pesoKg, v);
    const estaAsig = asigDia[ruta._id] === v.placas;
    const estaOcup = !estaAsig && ocupadas.has(v.placas);
    const capVolV  = v.volumen_m3 || 0;
    const excedVol = capVolV > 0 && volumenM3 >= capVolV;
    const icono    = estaOcup   ? "×"
      : excedVol                ? "!"
      : enRangoOptimo(pctV)     ? "✓"
      : pctV < _utilMin         ? "▽"
      : "△";
    const volLabel = capVolV > 0
      ? ` · ${((volumenM3 / capVolV) * 100).toFixed(0)}% vol`
      : "";
    const etiqueta = estaOcup
      ? `${h(v.placas)} — ${h(v.abreviatura)} (ocupado hoy)`
      : excedVol
      ? `${icono} ${h(v.placas)} — ${h(v.abreviatura)} (volumen excedido)`
      : `${icono} ${h(v.placas)} — ${h(v.abreviatura)} (${v.capacidad_toneladas} ton · ${pctV.toFixed(0)}%${volLabel})`;
    return `<option value="${h(v.placas)}" ${estaAsig ? "selected" : ""} ${estaOcup ? "disabled" : ""}>${etiqueta}</option>`;
  }).join("");

  const numSucs = (ruta.sucursales || []).length;
  const mayRuta = _mayoristas[String(ruta._id)] || [];
  const numParadas = numSucs + mayRuta.length;

  // Combina sucursales y mayoristas en una sola lista ordenada por `orden`
  // (calculado por cercanía), respetando la secuencia de entrega.
  const paradasCombinadas = [
    ...(ruta.sucursales || []).map(s => ({ tipo: "sucursal", orden: s.orden ?? 9999, data: s })),
    ...mayRuta.map(m => ({ tipo: "mayorista", orden: m.orden ?? 9999, data: m })),
  ].sort((a, b) => a.orden - b.orden);

  const paradasHTML = paradasCombinadas.map(p => {
    if (p.tipo === "sucursal") {
      const s = p.data;
      const peso    = _pesos[String(s.num_tienda)] || 0;
      const descMin = (peso * MIN_DESCARGA_POR_KG).toFixed(0);
      const nombreConId = s.num_tienda ? `[${s.num_tienda}] ${h(s.nombre_base || s.nombre_tienda || s.nombre_pedido || "")}` : h(s.nombre_base || s.nombre_tienda || s.nombre_pedido || "");
      return `
      <div class="sucursal-item">
        <div class="sucursal-orden">${s.orden ?? "?"}</div>
        <div class="sucursal-info">
          <div class="sucursal-nombre">${nombreConId}</div>
          <div class="sucursal-detalle">
            ${h(s.estado || "")}${s.hora_inicio ? ` · ${h(s.hora_inicio)}–${h(s.hora_fin || "")}` : ""}
            ${peso > 0 ? ` · Descarga: ${descMin} min` : ""}
          </div>
        </div>
        <div class="sucursal-peso">${peso > 0 ? `${peso.toLocaleString("es-MX")} kg` : "—"}</div>
      </div>`;
    } else {
      const m = p.data;
      return `
    <div class="mayorista-item">
      <div class="mayorista-orden">${m.orden ?? "M"}</div>
      <div class="mayorista-nombre">${h(m.nombre || `Cliente ${m.id_cliente}`)}</div>
      <div class="mayorista-peso">${m.peso_kg > 0 ? `${m.peso_kg.toLocaleString("es-MX")} kg` : "—"}</div>
    </div>`;
    }
  }).join("");

  // Badge y barra de volumen
  const badgeVolClass = !capVol ? "neutral" : cumpleVol ? "ok" : "error";
  const badgeVolText  = capVol > 0
    ? (cumpleVol
        ? `<i data-lucide="box"></i> ${volumenM3.toFixed(3)} m³ (${pctVol.toFixed(0)}%)`
        : `<i data-lucide="triangle-alert"></i> Vol: ${volumenM3.toFixed(3)} m³ excede ${capVol.toFixed(3)} m³`)
    : `<i data-lucide="box"></i> ${volumenM3.toFixed(3)} m³`;
  const volBarHTML = capVol > 0 ? `
    <div class="capacidad-bar-wrap">
      <div class="capacidad-bar-label">
        <span>Utilización volumétrica</span>
        <span>${pctVol.toFixed(1)}% de ${capVol.toFixed(3)} m³</span>
      </div>
      <div class="capacidad-bar">
        <div class="capacidad-bar-fill ${cumpleVol ? "ok" : "error"}" style="width:${Math.min(pctVol, 100)}%"></div>
      </div>
    </div>` : "";

  return `
    <div class="ruta-card status-${barClass}">
      <div class="ruta-card-header">
        <span class="ruta-nombre">
          <span class="badge-orden-peso">${orden}</span>
          <i data-lucide="map-pin"></i> ${h(ruta.nombre)}
        </span>
        <div style="display:flex;gap:6px;align-items:center;">
          <button class="btn-mapa" data-rutaid="${h(ruta._id)}" title="Ver recorrido en mapa"><i data-lucide="map"></i> Mapa</button>
          <button class="btn-recalcular btn btn-sm btn-secondary" data-rutaid="${h(ruta._id)}" title="Recalcular tiempos"><i data-lucide="rotate-cw"></i></button>
          <button class="btn-reprogramar btn btn-sm btn-secondary" data-rutaid="${h(ruta._id)}" title="Mover al siguiente día"><i data-lucide="arrow-right"></i> Sig.</button>
        </div>
      </div>
      <div class="ruta-card-body">
        <div class="ruta-indicadores">
          <span class="badge-indicador badge-${barClass}" data-tooltip="${tipoCap}">${badgeCap}</span>
          <span class="badge-indicador badge-${badgeVolClass}" data-tooltip="${!capVol ? 'Sin restricción de volumen configurada para este vehículo.' : cumpleVol ? 'Volumen de carga dentro de la capacidad volumétrica del vehículo.' : 'El volumen de carga excede la capacidad del vehículo. Considera asignar uno de mayor tamaño.'}">${badgeVolText}</span>
          <span class="badge-indicador badge-info" data-tooltip="Tiempo total estimado de la ruta: conducción + descarga + tiempo extra configurado."><i data-lucide="clock"></i> ${tiempos ? formatMinutos(tiempos.total_min) : "…"}</span>
          <span class="badge-indicador badge-neutral" data-tooltip="Número de paradas programadas en esta ruta, incluyendo sucursales y mayoristas."><i data-lucide="map-pin"></i> ${numParadas} parada${numParadas !== 1 ? "s" : ""}${mayRuta.length > 0 ? ` (${mayRuta.length} mayorista${mayRuta.length !== 1 ? "s" : ""})` : ""}</span>
          <span class="badge-indicador badge-neutral" data-tooltip="Peso total de carga asignada a esta ruta en toneladas."><i data-lucide="scale"></i> ${(pesoKg / 1000).toFixed(3)} ton</span>
          ${badgeDiaHTML}
          ${origenBadge}
          ${_badgeReacomo(String(ruta._id))}
        </div>
        ${alertaTiempoHTML}
        <div class="capacidad-bar-wrap">
          <div class="capacidad-bar-label">
            <span>Utilización del vehículo</span>
            <span>${pct.toFixed(1)}%${vehiculoAsig ? ` de ${vehiculoAsig.capacidad_toneladas} ton` : ""}</span>
          </div>
          <div class="capacidad-bar">
            <div class="capacidad-bar-fill ${barClass}" style="width:${Math.min(pct, 100)}%"></div>
          </div>
        </div>
        ${volBarHTML}
        <div class="tiempo-info">
          <div class="tiempo-dato"><div class="t-label">Conducción</div><div class="t-valor">${tiempos ? formatMinutos(tiempos.traslado_min) : "…"}</div></div>
          <div class="tiempo-dato"><div class="t-label">Descarga</div><div class="t-valor">${tiempos ? formatMinutos(tiempos.descarga_min) : "…"}</div></div>
          <div class="tiempo-dato"><div class="t-label">Extra</div><div class="t-valor">${tiempos ? formatMinutos(tiempos.extra_min || HORAS_EXTRA_RUTA_MIN) : "…"}</div></div>
          <div class="tiempo-dato"><div class="t-label">Distancia</div><div class="t-valor">${tiempos ? `${tiempos.distancia_km} km` : "…"}</div></div>
        </div>
        <div class="vehiculo-selector">
          <label><i data-lucide="truck"></i> Vehículo:</label>
          <select class="select-vehiculo" data-rutaid="${h(ruta._id)}" data-dia="${h(diaRuta)}">
            <option value="">— Sin asignar —</option>
            ${opcionesVehiculos}
          </select>
        </div>
        <button class="toggle-sucursales" data-total="${numParadas}"><i data-lucide="chevron-down"></i> Ver ${numParadas} parada${numParadas !== 1 ? "s" : ""}</button>
        <div class="sucursales-list">${paradasHTML}</div>
      </div>
    </div>`;
}

// ══════════════════════════════════════════════════════════════════════════════
// MODAL: CONFIGURAR DÍAS
// ══════════════════════════════════════════════════════════════════════════════

function abrirModalConfigDias() {
  document.getElementById("config-dias-form").innerHTML = `
    <div style="font-size:0.78rem;color:var(--texto-sub);margin-bottom:12px;">
      Define qué días opera la distribución y los horarios de salida/regreso.
    </div>
    <div style="display:grid;grid-template-columns:40px 120px 1fr 1fr;gap:8px;
      font-size:0.75rem;font-weight:700;color:var(--texto-sub);
      padding-bottom:6px;border-bottom:1px solid var(--borde);">
      <span></span><span>Día</span><span>Salida</span><span>Límite regreso</span>
    </div>
    ${DIAS_SEMANA.map(({ key, label }) => {
      const cfg = _configDias[key] || { habilitado: true, hora_salida: "07:00", hora_limite: "18:00" };
      return `
        <div class="dia-config-row">
          <div class="dia-toggle">
            <label class="switch">
              <input type="checkbox" id="chk-${key}" ${cfg.habilitado ? "checked" : ""}>
              <span class="slider"></span>
            </label>
          </div>
          <label style="font-weight:600">${label}</label>
          <input type="time" id="salida-${key}" value="${cfg.hora_salida || "07:00"}">
          <input type="time" id="limite-${key}" value="${cfg.hora_limite || "18:00"}">
        </div>`;
    }).join("")}`;
  document.getElementById("modal-config-dias").classList.remove("hidden");
}

async function guardarConfigDias() {
  const nuevo = {};
  DIAS_SEMANA.forEach(({ key }) => {
    nuevo[key] = {
      habilitado:  document.getElementById(`chk-${key}`)?.checked ?? true,
      hora_salida: document.getElementById(`salida-${key}`)?.value || "07:00",
      hora_limite: document.getElementById(`limite-${key}`)?.value || "18:00",
    };
  });
  _configDias = nuevo;
  try {
    const res = await fetch("/asignacion/config-dias", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(nuevo),
    });
    if (res.status === 400) { redirigirAlMenu("Sin logística activa."); return; }
  } catch (err) {
    console.error("[guardarConfigDias]", err);
  }
  cerrarModal("modal-config-dias");
  renderDiaSelector();
  activarDia(_diaActivo);
}

// ══════════════════════════════════════════════════════════════════════════════
// GUARDAR ASIGNACIÓN EN MONGODB
// ══════════════════════════════════════════════════════════════════════════════

async function guardarAsignacion(redirigir = false) {
  const btnCont = document.getElementById("btn-guardar-continuar");
  const btnSolo = document.getElementById("btn-solo-guardar");
  const btnActivo = redirigir ? btnCont : btnSolo;

  // Deshabilitar ambos botones mientras se guarda
  if (btnCont) btnCont.disabled = true;
  if (btnSolo) btnSolo.disabled = true;
  if (btnActivo) btnActivo.textContent = "Guardando…";

  Loader.show(
    redirigir ? 'Guardando y Continuando' : 'Guardando Asignación',
    MSG_ASIG.guardar
  );

  const payload = {
    fecha_generacion:     new Date().toISOString(),
    asignaciones_por_dia: _asignacionesDia,
    dias_programados:     Object.fromEntries(_rutas.map(r => [r._id, r.dia_programado])),
    detalle_por_dia:      {},
    util_min:             _utilMin,
    util_max:             _utilMax,
  };

  for (const diaKey of DIAS_SEMANA.map(d => d.key)) {
    const rutasDia = rutasDeDia(diaKey);
    if (rutasDia.length === 0) continue;
    const cfg = _configDias[diaKey] || {};
    payload.detalle_por_dia[diaKey] = {};

    rutasDia.forEach(ruta => {
      const pesoKg    = calcularPesoRuta(ruta);
      const volumenM3 = calcularVolumenRuta(ruta);
      const tiempos   = _tiempos[ruta._id] || {};
      const placas    = asignacionesDelDia(diaKey)[ruta._id];
      const vehiculo  = placas ? _vehiculos.find(v => v.placas === placas) : null;
      const pct       = calcularPct(pesoKg, vehiculo);
      const capVol    = vehiculo?.volumen_m3 || 0;
      const pctVol    = capVol > 0 ? parseFloat(((volumenM3 / capVol) * 100).toFixed(1)) : null;
      const salidaMin = parseHHMM(cfg.hora_salida);
      const horaLlegada = salidaMin != null && tiempos.total_min
        ? minutosAHHMM(salidaMin + tiempos.total_min) : null;

      payload.detalle_por_dia[diaKey][ruta._id] = {
        nombre_ruta:            ruta.nombre,
        dia_programado:         ruta.dia_programado,
        vehiculo_placas:        placas || null,
        vehiculo_abreviatura:   vehiculo?.abreviatura || null,
        capacidad_ton:          vehiculo?.capacidad_toneladas || null,
        peso_total_kg:          pesoKg,
        peso_total_ton:         parseFloat((pesoKg / 1000).toFixed(3)),
        porcentaje_utilizacion: parseFloat(pct.toFixed(1)),
        cumple_rango_capacidad: enRangoOptimo(pct),
        volumen_total_m3:       parseFloat(volumenM3.toFixed(6)),
        capacidad_vol_m3:       capVol || null,
        porcentaje_vol:         pctVol,
        cumple_volumen:         capVol <= 0 || volumenM3 < capVol,
        fuente_tiempo:          tiempos.origen_tiempo || "desconocido",
        distancia_km:           tiempos.distancia_km || null,
        tiempo_conduccion_min:  tiempos.traslado_min || null,
        tiempo_descarga_min:    tiempos.descarga_min || null,
        tiempo_extra_min:       tiempos.extra_min || HORAS_EXTRA_RUTA_MIN,
        tiempo_total_min:       tiempos.total_min || null,
        hora_salida:            cfg.hora_salida || null,
        hora_regreso_estimada:  horaLlegada,
        cumple_horario:         horaLlegada
          ? parseHHMM(horaLlegada) <= parseHHMM(cfg.hora_limite) : null,
        sucursales: (ruta.sucursales || []).map(s => ({
          num_tienda:   s.num_tienda,
          nombre:       s.nombre_base || s.nombre_tienda || s.nombre_pedido,
          orden:        s.orden,
          peso_kg:      _pesos[String(s.num_tienda)] || 0,
          volumen_m3:   parseFloat((_volumenes[String(s.num_tienda)] || 0).toFixed(6)),
          descarga_min: parseFloat(((_pesos[String(s.num_tienda)] || 0) * MIN_DESCARGA_POR_KG).toFixed(1)),
        })),
      };
    });
  }

  try {
    const res = await fetch("/asignacion/guardar", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (res.status === 400) { redirigirAlMenu("Sin logística activa."); return; }

    if (res.ok) {
      if (redirigir) {
        // Loader permanece visible durante la redirección
        const _slug = window.__PERFIL_SLUG__;
        window.location.href = _slug ? `/modificacion/${_slug}` : "/modificacion/";
        return;
      }
      // Solo guardar: ocultar loader y mostrar feedback visual
      Loader.hide();
      if (btnSolo) {
        btnSolo.innerHTML      = '<i data-lucide="check"></i> Guardado';
        lucide.createIcons();
        btnSolo.style.background = "#16a34a";
        btnSolo.style.color      = "#fff";
      }
    } else {
      throw new Error("Respuesta no OK");
    }
  } catch (err) {
    console.error("[guardarAsignacion]", err);
    Loader.hide();
    if (btnActivo) {
      btnActivo.textContent    = "Error";
      btnActivo.style.background = "#dc2626";
      btnActivo.style.color      = "#fff";
    }
  } finally {
    setTimeout(() => {
      if (btnCont) {
        btnCont.disabled        = false;
        btnCont.innerHTML       = '<i data-lucide="save"></i> Guardar y continuar <i data-lucide="arrow-right"></i>';
        lucide.createIcons();
        btnCont.style.background = "";
        btnCont.style.color      = "";
      }
      if (btnSolo) {
        btnSolo.disabled        = false;
        btnSolo.innerHTML       = '<i data-lucide="save"></i> Solo guardar';
        lucide.createIcons();
        btnSolo.style.background = "";
        btnSolo.style.color      = "";
      }
    }, 2500);
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// PANEL DE MAPA (Leaflet + OSRM)
// ══════════════════════════════════════════════════════════════════════════════

async function abrirMapaRuta(rutaId) {
  const ruta = _rutas.find(r => String(r._id) === rutaId);
  if (!ruta) return;

  const panel    = document.getElementById("panel-mapa");
  const backdrop = document.getElementById("panel-mapa-backdrop");
  const nombre   = document.getElementById("panel-mapa-nombre");
  const infoEl   = document.getElementById("panel-mapa-info");
  const mapaDiv  = document.getElementById("mapa-leaflet");
  const paradasEl = document.getElementById("panel-mapa-paradas");

  if (!panel) return;

  // Mostrar modal
  nombre.textContent = ruta.nombre || "Ruta";
  infoEl.textContent = "";
  if (paradasEl) paradasEl.innerHTML = '<div class="panel-mapa__spinner" style="height:80px"><div class="spinner"></div></div>';
  mapaDiv.innerHTML  = '<div class="panel-mapa__spinner"><div class="spinner"></div><span>Cargando mapa…</span></div>';
  panel.classList.add("visible");
  backdrop.classList.add("visible");

  // Destruir mapa anterior si existe
  if (_mapaLeaflet) { _mapaLeaflet.remove(); _mapaLeaflet = null; }

  try {
    const res = await fetch(`/asignacion/geometria-ruta/${encodeURIComponent(rutaId)}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const geo = await res.json();

    // Limpiar spinner e inicializar Leaflet
    mapaDiv.innerHTML = "";

    const mapa = L.map(mapaDiv, { zoomControl: true });
    _mapaLeaflet = mapa;

    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
      maxZoom: 19,
    }).addTo(mapa);

    const bounds = [];

    // Polyline de ruta
    if (geo.polyline && geo.polyline.length > 1) {
      const pl = L.polyline(geo.polyline, { color: "#3b82f6", weight: 4, opacity: 0.85 }).addTo(mapa);
      bounds.push(...geo.polyline);
    }

    // Íconos personalizados
    const mkDepot = L.divIcon({ className: "", html: '<div class="lf-marker-depot"></div>', iconSize: [14,14], iconAnchor: [7,7] });
    const mkSuc   = L.divIcon({ className: "", html: '<div class="lf-marker-sucursal"></div>', iconSize: [12,12], iconAnchor: [6,6] });
    const mkMay   = L.divIcon({ className: "", html: '<div class="lf-marker-mayorista"></div>', iconSize: [12,12], iconAnchor: [6,6] });

    // Marcadores de paradas
    (geo.paradas || []).forEach(p => {
      if (!p.lat || !p.lon) return;
      bounds.push([p.lat, p.lon]);
      let icon, label;
      if (p.tipo === "depot") {
        icon  = mkDepot;
        label = `<b>Depósito</b>${p.nombre ? `<br>${h(p.nombre)}` : ""}`;
      } else if (p.tipo === "mayorista") {
        icon  = mkMay;
        label = `<b>${h(p.nombre || "Mayorista")}</b><br><span style="color:#c2410c">${p.peso_kg ? p.peso_kg.toLocaleString("es-MX") + " kg" : ""}</span>`;
      } else {
        icon  = mkSuc;
        const peso = _pesos[String(p.num_tienda)] || p.peso_kg || 0;
        label = `<b>${h(p.nombre || "Sucursal")}</b>${peso ? `<br>${peso.toLocaleString("es-MX")} kg` : ""}`;
      }
      L.marker([p.lat, p.lon], { icon }).bindPopup(label).addTo(mapa);
    });

    // Ajustar viewport
    if (bounds.length > 0) {
      mapa.fitBounds(bounds, { padding: [30, 30] });
    }

    // Info de distancia/tiempo
    const partes = [];
    if (geo.distancia_km) partes.push(`${geo.distancia_km} km`);
    if (geo.duracion_min) partes.push(formatMinutos(geo.duracion_min));
    if (geo.origen)       partes.push(geo.origen === "osrm" ? "OSM" : "Estimado");
    infoEl.textContent = partes.join(" · ");

    // Poblar sidebar con la secuencia de paradas
    if (paradasEl && geo.paradas) {
      const tiposLabel = { depot: "Depósito", sucursal: "Sucursal", mayorista: "Mayorista" };
      paradasEl.innerHTML = geo.paradas.map((p, idx) => {
        const tipo  = p.tipo || "sucursal";
        const label = tiposLabel[tipo] || tipo;
        const peso  = tipo === "depot" ? null
          : (tipo === "mayorista" ? p.peso_kg : (_pesos[String(p.num_tienda)] || p.peso_kg || 0));
        const pesoHtml = peso > 0
          ? `<div class="pm-parada__peso">${peso.toLocaleString("es-MX")} kg</div>` : "";
        const bulletText = tipo === "depot" ? "D" : (p.orden ?? (idx));
        return `
        <div class="pm-parada pm-parada--${tipo}">
          <div class="pm-parada__bullet">${bulletText}</div>
          <div class="pm-parada__info">
            <div class="pm-parada__nombre">${h(p.nombre || label)}</div>
            <div class="pm-parada__tipo">${label}</div>
          </div>
          ${pesoHtml}
        </div>`;
      }).join("");
    }

  } catch (err) {
    console.error("[abrirMapaRuta]", err);
    mapaDiv.innerHTML = `<div class="panel-mapa__spinner"><span style="color:#dc2626">Error al cargar el mapa.</span></div>`;
    if (paradasEl) paradasEl.innerHTML = "";
  }
}

function cerrarPanelMapa() {
  const panel     = document.getElementById("panel-mapa");
  const backdrop  = document.getElementById("panel-mapa-backdrop");
  const paradasEl = document.getElementById("panel-mapa-paradas");
  if (panel)    panel.classList.remove("visible");
  if (backdrop) backdrop.classList.remove("visible");
  if (paradasEl) paradasEl.innerHTML = "";
  if (_mapaLeaflet) { _mapaLeaflet.remove(); _mapaLeaflet = null; }
}

// ══════════════════════════════════════════════════════════════════════════════
// HELPERS DE TIEMPO
// ══════════════════════════════════════════════════════════════════════════════

function parseHHMM(str) {
  if (!str) return null;
  const [h, m] = str.split(":").map(Number);
  return h * 60 + m;
}
function minutosAHHMM(min) {
  const h = Math.floor(min / 60), m = Math.round(min % 60);
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}
function formatMinutos(min) {
  if (!min || min <= 0) return "—";
  if (min < 60) return `${Math.round(min)} min`;
  const h = Math.floor(min / 60), m = Math.round(min % 60);
  return m > 0 ? `${h}h ${m}min` : `${h}h`;
}

// ── Misc ─────────────────────────────────────────────────────────────────────

/**
 * Normaliza nombre de día a minúsculas sin tildes.
 * Permite comparar "Miércoles" con "miercoles", "Lunes" con "lunes", etc.
 */
function _normalizarDia(s) {
  return (s || "").toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .trim();
}

/**
 * Resuelve el día canónico (ej. "miercoles") configurado para una ruta
 * a partir de su dia_sugerido, verificando que esté en la lista de días
 * habilitados. Devuelve null si la ruta no tiene día configurado.
 */
function _resolverDiaRuta(ruta, diasHab) {
  const ds = _normalizarDia(ruta.dia_sugerido || "");
  if (!ds) return null;
  return diasHab.find(d => _normalizarDia(d) === ds) || null;
}

function toggleSucursales(btn) {
  const lista = btn.nextElementSibling;
  lista.classList.toggle("abierta");
  const total = parseInt(btn.dataset.total, 10) || lista.children.length;
  btn.innerHTML = lista.classList.contains("abierta")
    ? '<i data-lucide="chevron-up"></i> Ocultar paradas'
    : `<i data-lucide="chevron-down"></i> Ver ${total} parada${total !== 1 ? "s" : ""}`;
  lucide.createIcons();
}

// ══════════════════════════════════════════════════════════════════════════════
// REACOMODAMIENTO LÓGICO — FASE 3
// ══════════════════════════════════════════════════════════════════════════════

/**
 * Carga la configuración del reacomodamiento desde el backend y la almacena
 * en _reacomoConfig para pre-poblar el modal.
 */
async function cargarConfigReacomodamiento() {
  try {
    const res = await fetch("/asignacion/config-reacomodamiento");
    if (res.ok) {
      _reacomoConfig = await res.json();
    }
  } catch (_) {
    _reacomoConfig = null;
  }
}

/**
 * Abre el modal de configuración de reacomodamiento y pre-carga la tolerancia.
 */
function abrirModalReacomo() {
  const cfg = _reacomoConfig || {};
  const tolPct = Math.round(((cfg.cap_tolerance ?? 0.30)) * 100);
  _setVal("rr-cap-tolerance", tolPct);
  _actualizarHintsTolerancia(tolPct);

  const tolInput = document.getElementById("rr-cap-tolerance");
  if (tolInput) {
    tolInput.oninput = () => _actualizarHintsTolerancia(parseFloat(tolInput.value) || 30);
  }

  document.getElementById("modal-reacomo")?.classList.remove("hidden");
  lucide.createIcons();
}

function _actualizarHintsTolerancia(tolPct) {
  const min = document.getElementById("rr-tol-min");
  const max = document.getElementById("rr-tol-max");
  if (min) min.textContent = Math.round(100 - tolPct);
  if (max) max.textContent = Math.round(100 + tolPct);
}

function _setVal(id, valor) {
  const el = document.getElementById(id);
  if (!el) return;
  if (el.type === "checkbox") el.checked = !!valor;
  else el.value = valor;
}

/**
 * Auto-asigna el vehículo más rentable a cada ruta usando historial + capacidad.
 * Prioridad: historial (Jaccard + recencia) → vehículo más cercano al 100 % de utilización.
 */
async function autoAsignarVehiculos() {
  const btn = document.getElementById("btn-auto-asignar");
  if (btn) btn.disabled = true;

  const rutasActivas = _rutasActivas();
  if (rutasActivas.length === 0) {
    if (btn) btn.disabled = false;
    return;
  }

  const routesInfo = rutasActivas.map(ruta => ({
    id:         String(ruta._id),
    dia:        ruta.dia_programado || "",
    peso_kg:    calcularPesoRuta(ruta),
    sucursales: (ruta.sucursales || []).map(s => s.num_tienda),
  }));

  try {
    const res = await fetch("/asignacion/sugerir-vehiculos", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ rutas: routesInfo }),
    });

    if (res.status === 400) { redirigirAlMenu("Sin logística activa."); return; }
    if (!res.ok) throw new Error(`HTTP ${res.status}`);

    const data = await res.json();
    if (data.status !== "ok") {
      alert("Error al sugerir vehículos: " + (data.mensaje || "Error desconocido."));
      return;
    }

    const sugerencias = data.sugerencias || {};
    let n_hist = 0, n_cap = 0, n_sin = 0;

    for (const ruta of rutasActivas) {
      const sug = sugerencias[String(ruta._id)];
      if (!sug) continue;
      const dia = ruta.dia_programado;
      if (sug.placas) {
        asignarVehiculoEnDia(ruta._id, dia, sug.placas);
        if (sug.fuente === "historico") n_hist++;
        else n_cap++;
      } else {
        n_sin++;
      }
    }

    renderRutas();
    actualizarIndicadoresDia();
    lucide.createIcons();

    const partes = [];
    if (n_hist > 0) partes.push(`${n_hist} por historial`);
    if (n_cap  > 0) partes.push(`${n_cap} por capacidad`);
    if (n_sin  > 0) partes.push(`${n_sin} sin vehículo disponible`);
    const msg = `Auto-asignación completada: ${partes.join(", ")}.`;
    alert(msg);

  } catch (err) {
    console.error("[autoAsignarVehiculos]", err);
    alert("Error de conexión al auto-asignar vehículos.");
  } finally {
    if (btn) btn.disabled = false;
  }
}

/**
 * Lee la tolerancia del modal y ejecuta el reacomodamiento (Fase 3).
 * Envía las asignaciones actuales al backend para reasignar vehículos.
 */
async function aplicarReacomodamiento() {
  const tolPct = parseFloat(document.getElementById("rr-cap-tolerance")?.value) || 30;
  _reacomoConfig = { cap_tolerance: tolPct / 100 };

  cerrarModal("modal-reacomo");
  _mostrarBannerReacomo(true);

  try {
    const payload = {
      asignaciones: _asignaciones,
      rutas:        _rutas,
      vehiculos:    _vehiculos,
      pesos:        _pesos,
      volumenes:    _volumenes,
      config_dias:  _configDias,
      util_min:     _utilMin,
      util_max:     _utilMax,
      reacomo_cfg:  _reacomoConfig,
    };

    const res = await fetch("/asignacion/reacomodamiento", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify(payload),
    });

    if (res.status === 400) { _mostrarBannerReacomo(false); redirigirAlMenu("Sin logística activa."); return; }
    if (!res.ok) throw new Error(`HTTP ${res.status}`);

    const data = await res.json();
    if (data.status !== "ok") {
      _mostrarBannerReacomo(false);
      alert("Error en reacomodamiento: " + (data.mensaje || "Error desconocido."));
      return;
    }

    // Aplicar nuevas asignaciones de vehículo al estado local
    const nuevasAsig = data.asignaciones || {};
    for (const [rutaId, asig] of Object.entries(nuevasAsig)) {
      // Sincronizar _asignacionesDia para render de tarjetas
      if (asig.dia) {
        asignarVehiculoEnDia(rutaId, asig.dia, asig.placas || null);
      }
      // Actualizar dict completo para futuros payloads de reacomodamiento
      if (_asignaciones[rutaId]) {
        _asignaciones[rutaId].placas  = asig.placas;
        _asignaciones[rutaId].pct     = asig.pct;
        _asignaciones[rutaId].en_rango = asig.en_rango;
      } else {
        _asignaciones[rutaId] = asig;
      }
    }

    // Guardar detalle de reacomodamiento para mostrar en tarjetas
    _reacomoDetalle = (data.reacomodamiento || {}).detalle || {};

    _mostrarBannerReacomo(false);
    _mostrarResumenReacomo(data.reacomodamiento || {});
    renderRutas();
    renderIndicadoresDia();

  } catch (err) {
    console.error("[aplicarReacomodamiento]", err);
    _mostrarBannerReacomo(false);
    alert("Error de conexión al aplicar el reacomodamiento.");
  }
}

function _mostrarBannerReacomo(mostrar) {
  let banner = document.getElementById("banner-reacomo");
  if (!banner) {
    banner = document.createElement("div");
    banner.id = "banner-reacomo";
    banner.className = "banner-reacomo";
    banner.innerHTML = `
      <div class="spinner" style="width:18px;height:18px;border-width:2px;border-top-color:#d97706;"></div>
      <span>Ejecutando reacomodamiento…</span>`;
    const grid = document.getElementById("rutas-grid");
    if (grid) grid.parentElement.insertBefore(banner, grid);
  }
  banner.style.display = mostrar ? "flex" : "none";
}

function _mostrarResumenReacomo(reacomo) {
  document.querySelectorAll(".resumen-reacomo").forEach(el => el.remove());

  const n_rea = reacomo.n_reasignadas   || 0;
  const n_ok  = reacomo.n_en_rango      || 0;
  const n_sob = reacomo.n_sobrecargadas || 0;
  const n_sin = reacomo.n_sin_vehiculo  || 0;
  const cfg   = reacomo.config || {};
  const tol   = Math.round(((cfg.cap_tolerance ?? 0.30)) * 100);

  const el = document.createElement("div");
  el.className = "resumen-reacomo";
  el.innerHTML = `
    <strong>✔ Reacomodamiento aplicado</strong>
    (tolerancia ${tol}% · rango ${100 - tol}%–${100 + tol}%) ·
    ${n_rea} reasignada${n_rea !== 1 ? "s" : ""} ·
    <span style="color:var(--verde)">${n_ok} en rango</span>
    ${n_sob > 0 ? `· <span style="color:var(--rojo)">${n_sob} sobrecargada${n_sob !== 1 ? "s" : ""}</span>` : ""}
    ${n_sin > 0 ? `· <span style="color:#d97706">${n_sin} sin vehículo</span>` : ""}.`;

  const grid = document.getElementById("rutas-grid");
  if (grid) grid.parentElement.insertBefore(el, grid);
  lucide.createIcons();
}

/** Devuelve el badge de reacomodamiento para una ruta. */
function _badgeReacomo(rutaId) {
  const det = _reacomoDetalle[rutaId];
  if (!det) return "";

  if (!det.vehicle_id) {
    return `<span class="badge-indicador badge-error" title="Ningún vehículo cumplió la restricción de capacidad">
      <i data-lucide="truck"></i> Sin vehículo
    </span>`;
  }
  if (det.overloaded) {
    return `<span class="badge-indicador badge-error" title="Utilización ${det.utilization_pct}% — excede el límite superior">
      <i data-lucide="triangle-alert"></i> Sobrecargado ${det.utilization_pct}%
    </span>`;
  }
  if (det.within_tolerance) {
    return `<span class="badge-indicador badge-ok" title="Utilización ${det.utilization_pct}% — dentro del rango válido">
      <i data-lucide="check-circle"></i> ${det.utilization_pct}%
    </span>`;
  }
  return `<span class="badge-indicador badge-reacomo-tipo" title="Utilización ${det.utilization_pct}%">
    ${det.utilization_pct}%
  </span>`;
}

function cerrarModal(id) {
  document.getElementById(id)?.classList.add("hidden");
}

function h(s) {
  return String(s ?? "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}

// ═══════════════════════════════════════════════════════════════════════════════
// PANEL VRP HÍBRIDO
// ═══════════════════════════════════════════════════════════════════════════════

(function initVrpPanel() {
  const VRP_CONSOLIDADA_TOOLTIP = "Ruta consolidada. Se agruparon sucursales de rutas con carga insuficiente para optimizar el uso de la flota.";

  const UMBRAL_PEQUEÑO_KG = 1300;

  function _vrpEstado(r) {
    const uso = r["uso_%"] ?? 0;
    const pct = `${uso}%`;

    if (r.estado === "CONSOLIDADA") {
      return { chipCls: "consol", badgeCls: "CONSOLIDADA", label: "Consolidada", badgeLabel: "Consolidada", tooltip: VRP_CONSOLIDADA_TOOLTIP };
    }
    const isSmall = r.is_small ?? (r.capacidad_kg != null && r.capacidad_kg <= UMBRAL_PEQUEÑO_KG);
    if (isSmall) {
      return { chipCls: "pequeno", badgeCls: "CARGA_PEQUEÑA", label: "Carga pequeña",
               badgeLabel: `Carga pequeña · ${pct}`,
               tooltip: `Utilización ${pct}: vehículo de carga reducida. Las reglas de utilización estándar no aplican para esta unidad.` };
    }
    if (uso >= 90 && uso <= 110) {
      return { chipCls: "excelente", badgeCls: "EXCELENTE", label: "Excelente",
               badgeLabel: `Excelente · ${pct}`,
               tooltip: `Utilización ${pct}: carga óptima (90–110% de la capacidad).` };
    }
    if (uso >= 60 && uso <= 120) {
      return { chipCls: "normal",    badgeCls: "ACEPTABLE",  label: "Aceptable",
               badgeLabel: `Aceptable · ${pct}`,
               tooltip: `Utilización ${pct}: carga aceptable (60–120% de la capacidad).` };
    }
    if (uso < 60) {
      return { chipCls: "critico",   badgeCls: "SUBCARGA",   label: "Subcarga, caso crítico",
               badgeLabel: `Subcarga, caso crítico · ${pct}`,
               tooltip: `Utilización ${pct}: carga menor al 60%. Vehículo subutilizado de forma crítica.` };
    }
    return       { chipCls: "critico",   badgeCls: "SOBRECARGA", label: "Sobrecarga, caso crítico",
               badgeLabel: `Sobrecarga, caso crítico · ${pct}`,
               tooltip: `Utilización ${pct}: carga mayor al 120%. Vehículo sobrecargado de forma crítica.` };
  }

  let _listoParaGenerar = false;

  document.addEventListener("DOMContentLoaded", async () => {
    const btnGen = document.getElementById("btn-generar-vrp");
    if (!btnGen) return;

    await verificarEstadoVRP();

    btnGen.addEventListener("click", generarVRP);
    document.getElementById("btn-regenerar-vrp")?.addEventListener("click", generarVRP);
    document.getElementById("btn-ir-modificacion")?.addEventListener("click", () => {
      window.location.href = "/modificacion";
    });
  });

  async function verificarEstadoVRP() {
    const btnGen  = document.getElementById("btn-generar-vrp");
    const genHint = document.getElementById("vrp-gen-hint");

    // 1. Verificar historial
    let histOk = false;
    try {
      const res  = await fetch("/asignacion/historial-disponible");
      const data = await res.json();
      const n    = data.n_ejemplos || 0;
      const card = document.getElementById("vrp-card-hist");
      document.getElementById("vrp-hist-val").textContent = n > 0
        ? `${n} ejemplo${n > 1 ? "s" : ""} cargado${n > 1 ? "s" : ""}`
        : "Sin historial";
      document.getElementById("vrp-hist-sub").textContent = n > 0
        ? `${data.n_sucursales || "?"} sucursales · ${data.n_rutas || "?"} rutas`
        : "Ve a Configuración → Rutas Históricas";
      if (n > 0) {
        card?.classList.add("ok");
        histOk = true;
        document.getElementById("vrp-alert-hist").style.display = "none";
      } else {
        card?.classList.add("warn");
        document.getElementById("vrp-alert-hist").style.display = "";
        document.getElementById("vrp-hist-link").style.display  = "";
      }
    } catch {
      document.getElementById("vrp-hist-val").textContent = "Error al verificar";
      document.getElementById("vrp-alert-hist").style.display = "";
    }

    // 2. Verificar pedidos (pesos en extracción)
    let pedOk = false;
    try {
      const res  = await fetch("/asignacion/pesos");
      const pesos = await res.json();
      const n    = Object.keys(pesos).length;
      const total = Object.values(pesos).reduce((a, b) => a + b, 0);
      const card  = document.getElementById("vrp-card-ped");
      document.getElementById("vrp-ped-val").textContent = n > 0
        ? `${n} sucursales`
        : "Sin pedidos";
      document.getElementById("vrp-ped-sub").textContent = n > 0
        ? `${Math.round(total).toLocaleString()} kg totales`
        : "Completa la sección Extracción";
      if (n > 0) {
        card?.classList.add("ok");
        pedOk = true;
        document.getElementById("vrp-alert-ped").style.display = "none";
      } else {
        card?.classList.add("warn");
        document.getElementById("vrp-alert-ped").style.display = "";
      }
    } catch {
      document.getElementById("vrp-ped-val").textContent = "Error al verificar";
      document.getElementById("vrp-alert-ped").style.display = "";
    }

    _listoParaGenerar = histOk && pedOk;
    if (_listoParaGenerar) {
      btnGen.disabled       = false;
      genHint.textContent   = "El sistema está listo para generar rutas.";
    } else {
      btnGen.disabled       = true;
      genHint.textContent   = !histOk
        ? "⚠ Carga el historial antes de generar."
        : "⚠ Completa la extracción antes de generar.";
    }

    // Verificar si ya existe un reporte VRP previo
    try {
      const res  = await fetch("/asignacion/reporte-vrp");
      const data = await res.json();
      if (data.reporte && data.reporte.length > 0) {
        renderReporte(data.reporte, data.consolidaciones || [], data.generado_en);
      }
    } catch { /* silencioso */ }
  }

  async function generarVRP() {
    const btnGen  = document.getElementById("btn-generar-vrp");
    const spinner = document.getElementById("vrp-spinner");
    const reporte = document.getElementById("vrp-reporte");

    btnGen.disabled        = true;
    spinner.style.display  = "";
    reporte.style.display  = "none";

    const msgs = [
      "Cargando historial de rutas…",
      "Construyendo template VRP…",
      "Calculando utilización por ruta…",
      "Consolidando rutas subocupadas…",
      "Ordenando secuencias…",
    ];
    let mi = 0;
    const hint = document.getElementById("vrp-gen-hint");
    const ticker = setInterval(() => {
      hint.textContent = msgs[mi % msgs.length];
      mi++;
    }, 900);

    try {
      const res  = await fetch("/asignacion/generar-vrp-historico", { method: "POST" });
      const data = await res.json();
      clearInterval(ticker);
      spinner.style.display = "none";

      if (data.status === "ok") {
        renderReporte(data.reporte, data.consolidaciones || [], null);
        hint.innerHTML = `<i data-lucide="check-circle" style="width:14px;height:14px;vertical-align:middle;color:#16a34a"></i> ${data.total_rutas} rutas generadas para ${data.total_sucursales} sucursales.`;
        lucide.createIcons();
      } else {
        hint.innerHTML = `<i data-lucide="x-circle" style="width:14px;height:14px;vertical-align:middle;color:#dc2626"></i> ${h(data.mensaje || "Error desconocido")}`;
        lucide.createIcons();
        mostrarAlerta(data.mensaje || "Error al generar", "error");
      }
    } catch (e) {
      clearInterval(ticker);
      spinner.style.display = "none";
      hint.innerHTML = `<i data-lucide="x-circle" style="width:14px;height:14px;vertical-align:middle;color:#dc2626"></i> Error de conexión`;
      lucide.createIcons();
      mostrarAlerta("Error de conexión: " + e.message, "error");
    } finally {
      btnGen.disabled = !_listoParaGenerar;
    }
  }

  function renderReporte(reporte, consols, timestamp) {
    const wrapper = document.getElementById("vrp-reporte");
    wrapper.style.display = "";

    // Timestamp
    if (timestamp) {
      document.getElementById("vrp-reporte-ts").textContent =
        "Generado: " + timestamp.slice(0, 16).replace("T", " ");
    }

    // Chips de estado — agrupados por nuevo estado
    const conteoChips = {};
    reporte.forEach(r => {
      const est = _vrpEstado(r);
      if (!conteoChips[est.label]) conteoChips[est.label] = { cnt: 0, chipCls: est.chipCls, tooltip: est.tooltip };
      conteoChips[est.label].cnt++;
    });
    const chipsEl = document.getElementById("vrp-estado-cards");
    chipsEl.innerHTML = Object.entries(conteoChips).map(([label, { cnt, chipCls, tooltip }]) =>
      `<span class="vrp-estado-chip ${chipCls}" data-tooltip="${tooltip}">${cnt} ${label}</span>`
    ).join("");

    // Tabla (sin columnas de historial ni desviación)
    const tbody = document.getElementById("vrp-tabla-body");
    tbody.innerHTML = reporte.map(r => {
      const uso = r["uso_%"] != null ? r["uso_%"] + "%" : "—";
      const est = _vrpEstado(r);
      return `<tr>
        <td>${h(r.vehiculo)}</td>
        <td>${h(r.dia_semana)}</td>
        <td>${r.sucursales}</td>
        <td>${r.kg_total.toLocaleString()}</td>
        <td>${uso}</td>
        <td><span class="vrp-estado-badge ${est.badgeCls}" data-tooltip="${est.tooltip}">${est.badgeLabel}</span></td>
      </tr>`;
    }).join("");

    lucide.createIcons();
  }

  function mostrarAlerta(msg, tipo) {
    const panel = document.getElementById("vrp-panel");
    const el    = document.createElement("div");
    el.className = `vrp-alert vrp-alert--${tipo === "error" ? "error" : "warn"}`;
    el.innerHTML = `<i data-lucide="alert-triangle"></i> ${h(msg)}`;
    panel.insertBefore(el, document.getElementById("vrp-gen-row") || panel.lastChild);
    lucide.createIcons();
    setTimeout(() => el.remove(), 6000);
  }

  function h(s) {
    return String(s ?? "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
  }
})();