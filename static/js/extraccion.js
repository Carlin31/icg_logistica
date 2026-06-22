/**
 * static/js/extraccion.js — v4
 *
 * Gestión de perfiles de extracción:
 *   • Tiendas Lores: ICG / Proalmex / Bimbo
 *     - Carga independiente por proveedor
 *     - Vista Consolidada por Peso y Consolidada por Volumen (m³)
 *     - Vista por perfil con columnas de peso y volumen
 *     - Edición inline de kilos en tablas de perfil
 *     - Eliminar / recargar por perfil
 *     - Guardar en MongoDB (peso + volumen)
 *   • Clientes Mayoristas
 *     - Carga de archivo Excel de mayoristas
 *     - Consolidado peso por cliente (código + nombre + kg)
 *     - Guardar en MongoDB
 *   • Confirmar selección → redirige a Asignación
 */

'use strict';

// ══════════════════════════════════════════════════════════════════════════════
// ESTADO GLOBAL
// ══════════════════════════════════════════════════════════════════════════════

/**
 * Estado por perfil (Tiendas Lores).
 * datos:   { "Nombre Tienda": { id_sucursal, kg }, … }          ← peso
 * volumen: { "Nombre Tienda": { id_sucursal, m3 }, … }          ← volumen
 */
const state = {
  perfiles: {
    icg:      { nombre: null, datos: null, volumen: null, status: 'vacio', advertencias: null },
    proalmex: { nombre: null, datos: null, volumen: null, status: 'vacio', advertencias: null },
    bimbo:    { nombre: null, datos: null, volumen: null, status: 'vacio', advertencias: null },
  },
  consolidadoPeso:    null,
  consolidadoVolumen: null,
  // Clientes Mayoristas (estado independiente)
  mayoristas: {
    nombre:       null,
    consolidado:  null,   // [{ codigo, nombre, peso_total_kg }]
    status:       'vacio',
    advertencias: null,
  },
  tabActiva:    'consolidado',
  subtabActiva: 'peso',
  hayUnsaved:   false,
  fuentesEliminadas: new Set(),   // fuentes eliminadas pendientes de persistir
};

const PERFILES = ['icg', 'proalmex', 'bimbo'];

// Mapeo perfil → claves del campo `advertencias` devuelto por el backend
const ADV_KEYS = {
  icg:      { total: 'total_claves_icg',      faltantes: 'claves_no_encontradas_icg' },
  proalmex: { total: 'total_claves_proalmex', faltantes: 'claves_no_encontradas_proalmex' },
};

// ── Mensajes contextuales del loader ─────────────────────────────────────────
const MSG_EXT = {
  procesar: {
    icg: [
      "Leyendo archivo ICG…",
      "Cruzando con catálogo de productos…",
      "Calculando pesos por sucursal…",
      "Calculando volúmenes…",
    ],
    proalmex: [
      "Leyendo archivo Proalmex…",
      "Cruzando con catálogo de productos…",
      "Calculando pesos por sucursal…",
      "Calculando volúmenes…",
    ],
    bimbo: [
      "Leyendo archivo Bimbo…",
      "Cruzando con catálogo de productos…",
      "Calculando pesos por sucursal…",
      "Calculando volúmenes…",
    ],
    mayoristas: [
      "Leyendo archivo Mayoristas…",
      "Consolidando peso por cliente…",
      "Buscando nombres en la base de datos…",
    ],
  },
  guardar: [
    "Registrando datos de extracción…",
    "Guardando en la base de datos…",
    "Vinculando con la logística activa…",
    "Finalizando guardado…",
  ],
  confirmar: [
    "Verificando datos extraídos…",
    "Preparando módulo de Asignación…",
    "Redirigiendo a Asignación…",
  ],
};

// ══════════════════════════════════════════════════════════════════════════════
// INIT
// ══════════════════════════════════════════════════════════════════════════════

// ── Confirm overlay mayorista ─────────────────────────────────────────────────
let _extConfirmResolve = null;

function mostrarConfirmMay(nombre) {
  const msg = document.getElementById('ext-confirm-msg');
  msg.innerHTML = `¿Seguro que deseas quitar a <span class="ext-confirm-nombre">${nombre}</span> de la lista? Esta acción solo afecta la sesión actual.`;
  document.getElementById('ext-confirm-overlay').classList.remove('hidden');
  lucide.createIcons();
  return new Promise(resolve => { _extConfirmResolve = resolve; });
}

document.addEventListener('DOMContentLoaded', () => {
  lucide.createIcons();

  document.getElementById('ext-confirm-cancel').addEventListener('click', () => {
    document.getElementById('ext-confirm-overlay').classList.add('hidden');
    if (_extConfirmResolve) { _extConfirmResolve(false); _extConfirmResolve = null; }
  });
  document.getElementById('ext-confirm-ok').addEventListener('click', () => {
    document.getElementById('ext-confirm-overlay').classList.add('hidden');
    if (_extConfirmResolve) { _extConfirmResolve(true); _extConfirmResolve = null; }
  });

  // Confirm genérico (captura manual)
  document.getElementById('ext-confirm-gen-cancel').addEventListener('click', () => {
    document.getElementById('ext-confirm-gen-overlay').classList.add('hidden');
    if (_genConfirmResolve) { _genConfirmResolve(false); _genConfirmResolve = null; }
  });
  document.getElementById('ext-confirm-gen-ok').addEventListener('click', () => {
    document.getElementById('ext-confirm-gen-overlay').classList.add('hidden');
    if (_genConfirmResolve) { _genConfirmResolve(true); _genConfirmResolve = null; }
  });

  // Modal agregar manualmente
  document.getElementById('modal-manual-close').addEventListener('click',   cerrarModalManual);
  document.getElementById('modal-manual-cancel').addEventListener('click',  cerrarModalManual);
  document.getElementById('modal-manual-guardar').addEventListener('click', guardarManual);
  document.getElementById('modal-manual').addEventListener('click', e => {
    if (e.target === e.currentTarget) cerrarModalManual();
  });
  document.getElementById('modal-manual-buscar').addEventListener('input', e => {
    renderFilasManual(e.target.value);
  });
  // Resaltar fila cuando el input tiene valor > 0
  document.getElementById('body-manual').addEventListener('input', e => {
    if (!e.target.classList.contains('manual-kg-input')) return;
    const kg = parseFloat(e.target.value) || 0;
    e.target.classList.toggle('manual-kg-input--con-valor', kg > 0);
  });

  verificarLogisticaActiva().then(activa => {
    if (!activa) return;
    inicializarTabs();
    inicializarUploads();
    inicializarUploadMayoristas();
    cargarDatosGuardados();
  });
});

// ── Verificar sesión activa ───────────────────────────────────────────────────
async function verificarLogisticaActiva() {
  try {
    const res  = await fetch('/api/activa');
    const data = await res.json();
    if (data.status !== 'ok') { mostrarAvisoSinLogistica(); return false; }
    return true;
  } catch {
    mostrarAvisoSinLogistica();
    return false;
  }
}

function mostrarAvisoSinLogistica() {
  const el = document.getElementById('aviso-sin-logistica');
  el.style.display = 'block';
  el.innerHTML = `
    <p><i data-lucide="triangle-alert"></i> No hay ninguna logística activa.</p>
    <p>Selecciona o crea una desde el <a href="/">menú principal</a> antes de continuar.</p>`;
  lucide.createIcons();
}

// ── Cargar datos previamente guardados ───────────────────────────────────────
async function cargarDatosGuardados() {
  // Tiendas Lores
  try {
    const res = await fetch('/extraccion/datos');
    if (res.ok) {
      const result = await res.json();
      if (result.status === 'ok' && (result.desglose || result.desglose_volumen)) {
        for (const perfil of PERFILES) {
          const datosPeso = result.desglose?.[perfil];
          const datosVol  = result.desglose_volumen?.[perfil];
          if ((datosPeso && Object.keys(datosPeso).length > 0) ||
              (datosVol  && Object.keys(datosVol).length  > 0)) {
            state.perfiles[perfil].datos   = datosPeso || null;
            state.perfiles[perfil].volumen = datosVol  || null;
            state.perfiles[perfil].nombre  = '(guardado)';
            state.perfiles[perfil].status  = 'guardado';
            renderPerfilCargado(perfil);
          }
        }
        recalcularConsolidados();
        actualizarUI();
      }
    }
  } catch (err) {
    console.warn('Sin datos de Tiendas Lores guardados previos:', err);
  }

  // Clientes Mayoristas
  try {
    const res = await fetch('/extraccion/datos-mayoristas');
    if (res.ok) {
      const result = await res.json();
      if (result.status === 'ok' && Array.isArray(result.consolidado) && result.consolidado.length > 0) {
        state.mayoristas.consolidado = result.consolidado;
        state.mayoristas.nombre      = '(guardado)';
        state.mayoristas.status      = 'guardado';
        renderMayoristasCargado();
        actualizarUI();
      }
    }
  } catch (err) {
    console.warn('Sin datos de Mayoristas guardados previos:', err);
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// TABS PRINCIPALES
// ══════════════════════════════════════════════════════════════════════════════

function inicializarTabs() {
  document.querySelectorAll('.ext-tab').forEach(btn => {
    btn.addEventListener('click', () => cambiarTab(btn.dataset.tab));
  });
}

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

// ══════════════════════════════════════════════════════════════════════════════
// SUB-TABS CONSOLIDADO (Peso / Volumen)
// ══════════════════════════════════════════════════════════════════════════════

window.cambiarSubtab = function (subtab) {
  state.subtabActiva = subtab;
  document.querySelectorAll('.ext-subtab').forEach(b =>
    b.classList.toggle('active', b.dataset.subtab === subtab)
  );
  document.querySelectorAll('.ext-subpanel').forEach(p =>
    p.classList.toggle('active', p.id === `subpanel-${subtab}`)
  );
};

// ══════════════════════════════════════════════════════════════════════════════
// UPLOAD POR PERFIL
// ══════════════════════════════════════════════════════════════════════════════

function inicializarUploads() {
  for (const perfil of PERFILES) {
    const input = document.getElementById(`file_${perfil}`);
    const zona  = document.getElementById(`upload-${perfil}`);

    input.addEventListener('change', e => {
      if (e.target.files[0]) procesarArchivo(perfil, e.target.files[0]);
    });

    zona.addEventListener('dragover',  e => { e.preventDefault(); zona.classList.add('dragover'); });
    zona.addEventListener('dragleave', ()  => zona.classList.remove('dragover'));
    zona.addEventListener('drop', e => {
      e.preventDefault();
      zona.classList.remove('dragover');
      const file = e.dataTransfer.files[0];
      if (file) procesarArchivo(perfil, file);
    });
  }
}

// ── Enviar archivo al backend y actualizar estado ────────────────────────────
async function procesarArchivo(perfil, archivo) {
  document.getElementById(`upload-${perfil}`).style.display = 'none';
  Loader.show(`Procesando ${perfil.toUpperCase()}`, MSG_EXT.procesar[perfil]);

  const formData = new FormData();
  formData.append(`file_${perfil}`, archivo);

  try {
    const res  = await fetch('/extraccion/procesar', { method: 'POST', body: formData });
    const data = await res.json();

    const tienePeso = data.desglose?.[perfil] && Object.keys(data.desglose[perfil]).length > 0;
    const tieneVol  = data.desglose_volumen?.[perfil] && Object.keys(data.desglose_volumen[perfil]).length > 0;

    if (res.ok && data.status === 'ok' && (tienePeso || tieneVol)) {
      state.perfiles[perfil].nombre       = archivo.name;
      state.perfiles[perfil].datos        = tienePeso ? data.desglose[perfil]         : null;
      state.perfiles[perfil].volumen      = tieneVol  ? data.desglose_volumen[perfil] : null;
      state.perfiles[perfil].status       = 'cargado';
      state.perfiles[perfil].advertencias = data.advertencias || null;
      state.fuentesEliminadas.delete(perfil);  // cancela eliminación si el usuario recargó
      state.hayUnsaved = true;

      renderPerfilCargado(perfil);
      recalcularConsolidados();
      actualizarUI();
      mostrarToast(`${perfil.toUpperCase()} cargado correctamente`, 'ok');
    } else {
      mostrarToast(data.mensaje || 'El archivo no produjo datos para este perfil.', 'error');
      document.getElementById(`upload-${perfil}`).style.display = 'block';
    }
  } catch (err) {
    console.error(err);
    mostrarToast('Error de conexión con el servidor.', 'error');
    document.getElementById(`upload-${perfil}`).style.display = 'block';
  } finally {
    Loader.hide();
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// RENDER POR PERFIL
// ══════════════════════════════════════════════════════════════════════════════

function renderPerfilCargado(perfil) {
  const { nombre, datos, volumen, status, advertencias } = state.perfiles[perfil];

  document.getElementById(`upload-${perfil}`).style.display  = 'none';
  document.getElementById(`loaded-${perfil}`).style.display  = 'block';
  document.getElementById(`filename-${perfil}`).textContent  = nombre || '';

  const advCfg = ADV_KEYS[perfil];
  renderAdvertenciaBanner(
    perfil,
    advCfg && advertencias ? (advertencias[advCfg.total] || 0) : 0,
    advCfg && advertencias ? advertencias[advCfg.faltantes]    : []
  );

  const statusEl = document.getElementById(`status-${perfil}`);
  statusEl.className = `ext-perfil-status ${status === 'guardado' ? 'status-guardado' : 'status-cargado'}`;
  statusEl.innerHTML = status === 'guardado'
    ? '<i data-lucide="check"></i> Guardado'
    : '<i data-lucide="circle"></i> Sin guardar';

  // Construir un mapa unificado de sucursales (unión de peso y volumen)
  const sucursales = new Set([
    ...Object.keys(datos  || {}),
    ...Object.keys(volumen || {}),
  ]);

  const tbody = document.getElementById(`body-${perfil}`);
  tbody.innerHTML = '';

  for (const sucursal of sucursales) {
    const infoPeso = datos?.[sucursal]   || { id_sucursal: 'N/A', kg: 0 };
    const infoVol  = volumen?.[sucursal] || { m3: 0 };

    const tr = document.createElement('tr');
    const displaySucursal = `Tiendas Lores ${sucursal}`;
    tr.innerHTML = `
      <td>${infoPeso.id_sucursal}</td>
      <td>${displaySucursal}</td>
      <td>
        <span
          class="editable-kg"
          contenteditable="true"
          data-perfil="${perfil}"
          data-sucursal="${encodeURIComponent(sucursal)}"
          onblur="editarKg(this)"
          onkeydown="if(event.key==='Enter'){event.preventDefault();this.blur();}"
        >${infoPeso.kg}</span> kg
      </td>
      <td class="col-m3">${formatM3(infoVol.m3)}</td>`;
    tbody.appendChild(tr);
  }

  actualizarDotTab(perfil);
  lucide.createIcons();
}

function formatM3(val) {
  const n = parseFloat(val) || 0;
  return n > 0 ? n.toFixed(4) + ' m³' : '—';
}

// ══════════════════════════════════════════════════════════════════════════════
// ACCIONES POR PERFIL: Recargar / Eliminar
// ══════════════════════════════════════════════════════════════════════════════

window.recargarPerfil = function (perfil) {
  const input = document.getElementById(`file_${perfil}`);
  input.value = '';
  input.click();
};

window.eliminarPerfil = function (perfil) {
  const nombre = perfil.toUpperCase();
  if (!confirm(`¿Eliminar los datos de ${nombre}?\nEsta acción se aplicará al guardar.`)) return;

  state.perfiles[perfil] = { nombre: null, datos: null, volumen: null, status: 'vacio', advertencias: null };
  state.fuentesEliminadas.add(perfil);
  state.hayUnsaved = true;

  document.getElementById(`loaded-${perfil}`).style.display = 'none';
  document.getElementById(`upload-${perfil}`).style.display  = 'block';
  document.getElementById(`body-${perfil}`).innerHTML = '';
  document.getElementById(`file_${perfil}`).value     = '';
  renderAdvertenciaBanner(perfil, 0, []);

  actualizarDotTab(perfil);
  recalcularConsolidados();
  actualizarUI();
  mostrarToast(`Datos de ${nombre} eliminados. Guarda para confirmar.`, 'info');
};

// ══════════════════════════════════════════════════════════════════════════════
// EDICIÓN INLINE DE KILOS
// ══════════════════════════════════════════════════════════════════════════════

window.editarKg = function (el) {
  const perfil   = el.dataset.perfil;
  const sucursal = decodeURIComponent(el.dataset.sucursal);
  const valor    = parseFloat(el.textContent.trim());

  if (isNaN(valor) || valor < 0) {
    el.textContent = state.perfiles[perfil].datos?.[sucursal]?.kg ?? 0;
    mostrarToast('Valor inválido. Ingresa un número ≥ 0.', 'error');
    return;
  }

  const redondeado = Math.round(valor);
  el.textContent = redondeado;

  if (!state.perfiles[perfil].datos) state.perfiles[perfil].datos = {};
  if (!state.perfiles[perfil].datos[sucursal]) {
    state.perfiles[perfil].datos[sucursal] = { id_sucursal: 'N/A', kg: 0 };
  }
  state.perfiles[perfil].datos[sucursal].kg = redondeado;

  if (state.perfiles[perfil].status === 'guardado') {
    state.perfiles[perfil].status = 'cargado';
    actualizarChipEstado(perfil);
    actualizarDotTab(perfil);
  }
  state.hayUnsaved = true;

  recalcularConsolidados();
  actualizarUnsavedIndicator();
};

function actualizarChipEstado(perfil) {
  const el = document.getElementById(`status-${perfil}`);
  if (!el) return;
  el.className = 'ext-perfil-status status-cargado';
  el.innerHTML = '<i data-lucide="circle"></i> Sin guardar';
  lucide.createIcons();
}

// ══════════════════════════════════════════════════════════════════════════════
// CONSOLIDADOS (Peso + Volumen)
// ══════════════════════════════════════════════════════════════════════════════

function recalcularConsolidados() {
  const consPeso = {};
  const consVol  = {};

  for (const [perfil, ps] of Object.entries(state.perfiles)) {
    // ── Peso ──
    if (ps.datos) {
      for (const [suc, info] of Object.entries(ps.datos)) {
        if (!consPeso[suc]) {
          consPeso[suc] = { id_sucursal: info.id_sucursal, icg_kg: 0, proalmex_kg: 0, bimbo_kg: 0, total_kg: 0 };
        }
        consPeso[suc][`${perfil}_kg`]  = info.kg;
        consPeso[suc].total_kg        += info.kg;
      }
    }
    // ── Volumen ──
    if (ps.volumen) {
      for (const [suc, info] of Object.entries(ps.volumen)) {
        if (!consVol[suc]) {
          const idSuc = consPeso[suc]?.id_sucursal ?? info.id_sucursal ?? 'N/A';
          consVol[suc] = { id_sucursal: idSuc, icg_m3: 0, proalmex_m3: 0, bimbo_m3: 0, total_m3: 0 };
        }
        consVol[suc][`${perfil}_m3`]   = info.m3;
        consVol[suc].total_m3 = parseFloat((consVol[suc].total_m3 + info.m3).toFixed(4));
      }
    }
  }

  state.consolidadoPeso    = consPeso;
  state.consolidadoVolumen = consVol;

  renderConsolidadoPeso();
  renderConsolidadoVolumen();
  actualizarVisibilidadConsolidado();
}

function actualizarVisibilidadConsolidado() {
  const hayDatos = (state.consolidadoPeso    && Object.keys(state.consolidadoPeso).length    > 0) ||
                   (state.consolidadoVolumen  && Object.keys(state.consolidadoVolumen).length > 0);

  document.getElementById('empty-consolidado').style.display    = hayDatos ? 'none'  : 'flex';
  document.getElementById('consolidado-content').style.display  = hayDatos ? 'block' : 'none';
}

// ── Tabla Peso ────────────────────────────────────────────────────────────────
function renderConsolidadoPeso() {
  const tbody = document.getElementById('body-consolidado');
  tbody.innerHTML = '';

  if (!state.consolidadoPeso) return;

  for (const [suc, v] of Object.entries(state.consolidadoPeso)) {
    const tr = document.createElement('tr');
    const displaySucursal = `Tiendas Lores ${suc}`;
    tr.innerHTML = `
      <td>${v.id_sucursal}</td>
      <td>${displaySucursal}</td>
      <td>${v.icg_kg      > 0 ? v.icg_kg      + ' kg' : '—'}</td>
      <td>${v.proalmex_kg > 0 ? v.proalmex_kg + ' kg' : '—'}</td>
      <td>${v.bimbo_kg    > 0 ? v.bimbo_kg    + ' kg' : '—'}</td>
      <td class="col-total"><strong>${v.total_kg} kg</strong></td>`;
    tbody.appendChild(tr);
  }

  _reasignarSortListeners('tabla-consolidado');
}

// ── Tabla Volumen ─────────────────────────────────────────────────────────────
function renderConsolidadoVolumen() {
  const tbody  = document.getElementById('body-volumen');
  const wrap   = document.getElementById('tabla-wrap-volumen');
  const aviso  = document.getElementById('empty-volumen');
  tbody.innerHTML = '';

  const datos = state.consolidadoVolumen;
  const tieneVolumen = datos && Object.keys(datos).length > 0 &&
    Object.values(datos).some(v => v.total_m3 > 0);

  if (!tieneVolumen) {
    aviso.style.display = 'block';
    wrap.style.display  = 'none';
    return;
  }

  aviso.style.display = 'none';
  wrap.style.display  = 'block';

  for (const [suc, v] of Object.entries(datos)) {
    const tr = document.createElement('tr');
    const displaySucursal = `Tiendas Lores ${suc}`;
    tr.innerHTML = `
      <td>${v.id_sucursal}</td>
      <td>${displaySucursal}</td>
      <td>${v.icg_m3      > 0 ? v.icg_m3.toFixed(4)      + ' m³' : '—'}</td>
      <td>${v.proalmex_m3 > 0 ? v.proalmex_m3.toFixed(4) + ' m³' : '—'}</td>
      <td>${v.bimbo_m3    > 0 ? v.bimbo_m3.toFixed(4)    + ' m³' : '—'}</td>
      <td class="col-total"><strong>${v.total_m3.toFixed(4)} m³</strong></td>`;
    tbody.appendChild(tr);
  }

  _reasignarSortListeners('tabla-volumen');
}

// ══════════════════════════════════════════════════════════════════════════════
// HELPERS DE UI
// ══════════════════════════════════════════════════════════════════════════════

function actualizarDotTab(perfil) {
  const dot = document.getElementById(`dot-${perfil}`);
  if (!dot) return;
  const st = state.perfiles[perfil].status;
  dot.className = `ext-tab__dot ${
    st === 'guardado' ? 'dot-guardado' : st === 'cargado' ? 'dot-cargado' : ''
  }`;
}

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

// ══════════════════════════════════════════════════════════════════════════════
// GUARDAR EN MONGODB
// ══════════════════════════════════════════════════════════════════════════════

window.guardarDatos = async function () {
  const hayPeso       = state.consolidadoPeso    && Object.keys(state.consolidadoPeso).length    > 0;
  const hayVol        = state.consolidadoVolumen && Object.keys(state.consolidadoVolumen).length > 0;
  const hayMayoristas = state.mayoristas.consolidado !== null;
  const hayEliminadas = state.fuentesEliminadas.size > 0;

  if (!hayPeso && !hayVol && !hayMayoristas && !hayEliminadas) {
    mostrarToast('No hay datos para guardar.', 'error');
    return;
  }

  const btn = document.getElementById('btn-guardar');
  btn.disabled    = true;
  btn.textContent = 'Guardando…';

  Loader.show('Guardando Datos', MSG_EXT.guardar);

  try {
    // ── Eliminar fuentes removidas de MongoDB y limpiar asignaciones ──────
    if (hayEliminadas) {
      for (const fuente of state.fuentesEliminadas) {
        try {
          const resDel = await fetch('/extraccion/eliminar-fuente', {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ fuente }),
          });
          const dtDel = await resDel.json();
          if (!resDel.ok || dtDel.status !== 'ok') {
            console.warn(`[eliminar-fuente] ${fuente}:`, dtDel.mensaje || 'error desconocido');
          }
        } catch (errDel) {
          console.warn(`[eliminar-fuente] ${fuente}:`, errDel);
        }
      }
      state.fuentesEliminadas.clear();
    }

    // ── Guardar Tiendas Lores ──────────────────────────────────────────────
    if (hayPeso || hayVol) {
      const payload = {
        datos:    state.consolidadoPeso    ?? {},
        desglose: {
          icg:      state.perfiles.icg.datos      ?? {},
          proalmex: state.perfiles.proalmex.datos  ?? {},
          bimbo:    state.perfiles.bimbo.datos     ?? {},
        },
        datos_volumen: state.consolidadoVolumen ?? {},
        desglose_volumen: {
          icg:      state.perfiles.icg.volumen      ?? {},
          proalmex: state.perfiles.proalmex.volumen  ?? {},
          bimbo:    state.perfiles.bimbo.volumen     ?? {},
        },
      };

      const res    = await fetch('/extraccion/guardar', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify(payload),
      });
      const result = await res.json();

      if (res.ok && result.status === 'ok') {
        for (const perfil of PERFILES) {
          if (state.perfiles[perfil].datos || state.perfiles[perfil].volumen) {
            state.perfiles[perfil].status = 'guardado';
            actualizarDotTab(perfil);
            actualizarChipEstadoGuardado(perfil);
          }
        }
      } else if (res.status === 400) {
        alert(result.mensaje || 'No hay logística activa.');
        window.location.href = '/';
        return;
      } else {
        mostrarToast('Error al guardar Tiendas Lores: ' + (result.mensaje || 'Error desconocido.'), 'error');
      }
    }

    // ── Guardar Clientes Mayoristas ────────────────────────────────────────
    if (hayMayoristas) {
      const resMay = await fetch('/extraccion/guardar-mayoristas', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ consolidado: state.mayoristas.consolidado }),
      });
      const resultMay = await resMay.json();

      if (resMay.ok && resultMay.status === 'ok') {
        state.mayoristas.status = 'guardado';
        actualizarDotTabMayoristas();
        actualizarChipEstadoGuardado('mayoristas');
      } else {
        mostrarToast('Error al guardar Mayoristas: ' + (resultMay.mensaje || ''), 'error');
      }
    }

    state.hayUnsaved = false;
    actualizarUnsavedIndicator();
    mostrarToast(
      (hayPeso || hayVol || hayMayoristas)
        ? 'Datos guardados correctamente'
        : 'Fuentes eliminadas. Regenera las rutas VRP para actualizar las asignaciones.',
      'ok'
    );

  } catch (err) {
    console.error(err);
    mostrarToast('Error de conexión al guardar.', 'error');
  } finally {
    Loader.hide();
    btn.disabled  = false;
    btn.innerHTML = '<i data-lucide="save"></i> Guardar datos';
    lucide.createIcons();
  }
};

function actualizarChipEstadoGuardado(perfil) {
  const el = document.getElementById(`status-${perfil}`);
  if (!el) return;
  el.className = 'ext-perfil-status status-guardado';
  el.innerHTML = '<i data-lucide="check"></i> Guardado';
  lucide.createIcons();
}

// ══════════════════════════════════════════════════════════════════════════════
// CONFIRMAR SELECCIÓN → ASIGNACIÓN
// ══════════════════════════════════════════════════════════════════════════════

window.confirmarSeleccion = async function () {
  const hayAlgo = PERFILES.some(
    p => state.perfiles[p].datos !== null || state.perfiles[p].volumen !== null
  ) || state.mayoristas.consolidado !== null;
  if (!hayAlgo) {
    mostrarToast('Carga al menos un archivo antes de confirmar.', 'error');
    return;
  }

  if (state.hayUnsaved) {
    const ok = confirm('Tienes cambios sin guardar.\n¿Guardarlos antes de continuar?');
    if (ok) {
      await guardarDatos();
      if (state.hayUnsaved) return;
    }
  }

  const btn = document.getElementById('btn-confirmar');
  btn.disabled    = true;
  btn.textContent = 'Redirigiendo…';

  Loader.show('Confirmando Selección', MSG_EXT.confirmar);
  const _slug = window.__PERFIL_SLUG__;
  setTimeout(() => { window.location.href = _slug ? `/asignacion/${_slug}` : '/asignacion/'; }, 1200);
};

// ══════════════════════════════════════════════════════════════════════════════
// ORDENAR TABLA
// ══════════════════════════════════════════════════════════════════════════════

function _reasignarSortListeners(tablaId) {
  const tabla = document.getElementById(tablaId);
  if (!tabla) return;
  tabla.querySelectorAll('thead th.sortable').forEach(th => {
    th.onclick = () => ordenarTabla(tablaId, parseInt(th.dataset.col));
  });
}

function ordenarTabla(tablaId, colIdx) {
  const tabla = document.getElementById(tablaId);
  if (!tabla) return;

  const filas = Array.from(tabla.querySelectorAll('tbody tr'));

  const obtenerValor = tr => {
    const td = tr.cells[colIdx];
    if (!td) return '';
    const raw = td.textContent.replace(/kg|m³|—/gi, '').trim();
    return isNaN(raw) || raw === '' ? raw.toLowerCase() : parseFloat(raw);
  };

  const dirActual = tabla.dataset.sortDir || 'none';
  const dir = dirActual === 'asc' ? -1 : 1;
  tabla.dataset.sortDir = dir === 1 ? 'asc' : 'desc';

  filas.sort((a, b) => {
    const va = obtenerValor(a);
    const vb = obtenerValor(b);
    if (typeof va === 'number' && typeof vb === 'number') return dir * (va - vb);
    return dir * String(va).localeCompare(String(vb), 'es');
  });

  const tbody = tabla.querySelector('tbody');
  filas.forEach(f => tbody.appendChild(f));
}

// ══════════════════════════════════════════════════════════════════════════════
// CLIENTES MAYORISTAS — Upload, Render, Eliminar
// ══════════════════════════════════════════════════════════════════════════════

function inicializarUploadMayoristas() {
  const input = document.getElementById('file_mayoristas');
  const zona  = document.getElementById('upload-mayoristas');
  if (!input || !zona) return;

  input.addEventListener('change', e => {
    if (e.target.files[0]) procesarArchivoMayoristas(e.target.files[0]);
  });

  zona.addEventListener('dragover',  e => { e.preventDefault(); zona.classList.add('dragover'); });
  zona.addEventListener('dragleave', ()  => zona.classList.remove('dragover'));
  zona.addEventListener('drop', e => {
    e.preventDefault();
    zona.classList.remove('dragover');
    const file = e.dataTransfer.files[0];
    if (file) procesarArchivoMayoristas(file);
  });
}

async function procesarArchivoMayoristas(archivo) {
  document.getElementById('upload-mayoristas').style.display = 'none';
  Loader.show('Procesando Mayoristas', MSG_EXT.procesar.mayoristas);

  const formData = new FormData();
  formData.append('file_mayoristas', archivo);

  try {
    const res  = await fetch('/extraccion/procesar-mayoristas', { method: 'POST', body: formData });
    const data = await res.json();

    if (res.ok && data.status === 'ok' && Array.isArray(data.consolidado) && data.consolidado.length > 0) {
      state.mayoristas.nombre       = archivo.name;
      state.mayoristas.consolidado  = data.consolidado;
      state.mayoristas.status       = 'cargado';
      state.mayoristas.advertencias = data.advertencias || null;
      state.fuentesEliminadas.delete('mayoristas');  // cancela eliminación si el usuario recargó
      state.hayUnsaved = true;

      renderMayoristasCargado();
      actualizarUI();
      mostrarToast('Mayoristas cargado correctamente', 'ok');
    } else {
      mostrarToast(data.mensaje || 'El archivo no produjo datos válidos.', 'error');
      document.getElementById('upload-mayoristas').style.display = 'block';
    }
  } catch (err) {
    console.error(err);
    mostrarToast('Error de conexión con el servidor.', 'error');
    document.getElementById('upload-mayoristas').style.display = 'block';
  } finally {
    Loader.hide();
  }
}

function renderMayoristasCargado() {
  const { nombre, consolidado, status, advertencias } = state.mayoristas;

  document.getElementById('upload-mayoristas').style.display  = 'none';
  document.getElementById('loaded-mayoristas').style.display  = 'block';
  document.getElementById('filename-mayoristas').textContent  = nombre || '';

  renderAdvertenciaBanner(
    'mayoristas',
    advertencias ? (advertencias.total_codigos_mayoristas || 0) : 0,
    advertencias ? advertencias.codigos_no_encontrados        : []
  );

  const statusEl = document.getElementById('status-mayoristas');
  statusEl.className = `ext-perfil-status ${status === 'guardado' ? 'status-guardado' : 'status-cargado'}`;
  statusEl.innerHTML = status === 'guardado'
    ? '<i data-lucide="check"></i> Guardado'
    : '<i data-lucide="circle"></i> Sin guardar';

  const tbody = document.getElementById('body-mayoristas');
  tbody.innerHTML = '';

  let pesoTotal = 0;
  for (const cliente of (consolidado || [])) {
    pesoTotal += cliente.peso_total_kg;
    const doc = cliente.documento || String(cliente.codigo);
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td><strong>${doc}</strong></td>
      <td>${cliente.codigo}</td>
      <td>${cliente.nombre}</td>
      <td class="col-total"><strong>${Math.round(cliente.peso_total_kg).toLocaleString("es-MX")} kg</strong></td>
      <td><button class="btn-may-eliminar" data-doc="${doc}" data-nombre="${cliente.nombre.replace(/"/g,'&quot;')}" title="Quitar de la lista">✕</button></td>`;
    tbody.appendChild(tr);
  }

  // Fila de totales
  if ((consolidado || []).length > 0) {
    const trTotal = document.createElement('tr');
    trTotal.className = 'ext-fila-total';
    trTotal.innerHTML = `
      <td colspan="3"><strong>Total general</strong></td>
      <td class="col-total"><strong>${Math.round(pesoTotal).toLocaleString("es-MX")} kg</strong></td>
      <td></td>`;
    tbody.appendChild(trTotal);
  }

  tbody.querySelectorAll('.btn-may-eliminar').forEach(btn => {
    btn.addEventListener('click', () => eliminarFilaMayorista(btn.dataset.doc, btn.dataset.nombre));
  });

  actualizarDotTabMayoristas();
  _reasignarSortListeners('tabla-mayoristas');
  lucide.createIcons();
}

async function eliminarFilaMayorista(documento, nombre) {
  const ok = await mostrarConfirmMay(documento || nombre);
  if (!ok) return;
  if (!state.mayoristas.consolidado) return;
  state.mayoristas.consolidado = state.mayoristas.consolidado.filter(
    c => (c.documento || String(c.codigo)) !== documento
  );
  if (state.mayoristas.status === 'guardado') state.mayoristas.status = 'cargado';
  state.hayUnsaved = true;
  renderMayoristasCargado();
  actualizarUI();
}

function actualizarDotTabMayoristas() {
  const dot = document.getElementById('dot-mayoristas');
  if (!dot) return;
  const st = state.mayoristas.status;
  dot.className = `ext-tab__dot ${
    st === 'guardado' ? 'dot-guardado' : st === 'cargado' ? 'dot-cargado' : ''
  }`;
}

window.recargarMayoristas = function () {
  const input = document.getElementById('file_mayoristas');
  input.value = '';
  input.click();
};

window.eliminarMayoristas = function () {
  if (!confirm('¿Eliminar los datos de Mayoristas?\nEsta acción se aplicará al guardar.')) return;

  state.mayoristas = { nombre: null, consolidado: null, status: 'vacio', advertencias: null };
  state.fuentesEliminadas.add('mayoristas');
  state.hayUnsaved = true;

  document.getElementById('loaded-mayoristas').style.display = 'none';
  document.getElementById('upload-mayoristas').style.display  = 'block';
  document.getElementById('body-mayoristas').innerHTML        = '';
  document.getElementById('file_mayoristas').value            = '';
  renderAdvertenciaBanner('mayoristas', 0, []);

  actualizarDotTabMayoristas();
  actualizarUI();
  mostrarToast('Datos de Mayoristas eliminados. Guarda para confirmar.', 'info');
};

// ══════════════════════════════════════════════════════════════════════════════
// TOAST
// ══════════════════════════════════════════════════════════════════════════════

// ══════════════════════════════════════════════════════════════════════════════
// AGREGAR MANUALMENTE
// ══════════════════════════════════════════════════════════════════════════════

let _manualPerfilActivo  = null;
let _sucursalesCache     = null;
let _manualKgMap         = {};  // { nombre_base → kg } — persiste entre cambios de filtro
let _manualModalAbierto  = false;
let _genConfirmResolve   = null;

function _onBeforeUnload(e) {
  e.preventDefault();
  e.returnValue = '';
}

function mostrarConfirmGen(titulo, mensaje, textoOk = 'Confirmar') {
  document.getElementById('ext-confirm-gen-titulo').textContent = titulo;
  document.getElementById('ext-confirm-gen-msg').textContent   = mensaje;
  document.getElementById('ext-confirm-gen-ok').textContent    = textoOk;
  document.getElementById('ext-confirm-gen-overlay').classList.remove('hidden');
  return new Promise(resolve => { _genConfirmResolve = resolve; });
}

function _cerrarModalForzado() {
  document.getElementById('modal-manual').classList.add('hidden');
  _manualModalAbierto = false;
  _manualPerfilActivo = null;
  window.removeEventListener('beforeunload', _onBeforeUnload);
  document.querySelector('.ext-tabs-bar')?.classList.remove('ext-tabs-bar--bloqueado');
}

// Vuelca los valores actuales del DOM al mapa antes de re-renderizar
function _syncKgDesdeDOM() {
  document.querySelectorAll('#body-manual .manual-kg-input').forEach(inp => {
    const nombre = decodeURIComponent(inp.dataset.nombre);
    _manualKgMap[nombre] = Math.round(parseFloat(inp.value) || 0);
  });
}

window.abrirModalManual = async function (perfil) {
  _manualPerfilActivo = perfil;
  const label = { icg: 'ICG', proalmex: 'Proalmex', bimbo: 'Bimbo' }[perfil] || perfil.toUpperCase();

  // Inicializar el mapa con datos ya existentes en el estado (para re-edición)
  const existente = state.perfiles[perfil]?.datos || {};
  _manualKgMap = Object.fromEntries(
    Object.entries(existente).map(([nom, info]) => [nom, info.kg || 0])
  );

  document.getElementById('modal-manual-titulo').textContent = `Agregar manualmente — ${label}`;
  document.getElementById('modal-manual-buscar').value       = '';
  document.getElementById('body-manual').innerHTML =
    '<tr><td colspan="3" class="manual-loading">Cargando sucursales…</td></tr>';
  document.getElementById('modal-manual').classList.remove('hidden');
  _manualModalAbierto = true;
  window.addEventListener('beforeunload', _onBeforeUnload);
  document.querySelector('.ext-tabs-bar')?.classList.add('ext-tabs-bar--bloqueado');

  if (!_sucursalesCache) {
    try {
      const res  = await fetch('/extraccion/sucursales');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (!Array.isArray(data.sucursales)) throw new Error('Respuesta inesperada del servidor');
      _sucursalesCache = data.sucursales;
      console.info(`[manual] ${_sucursalesCache.length} sucursales cargadas`);
    } catch (err) {
      console.error('[sucursalesCache]', err);
      _sucursalesCache = null;  // null → reintentará la próxima vez
      document.getElementById('body-manual').innerHTML =
        `<tr><td colspan="3" class="manual-loading" style="color:#dc2626">
          Error al cargar sucursales: ${err.message}
         </td></tr>`;
      return;
    }
  }

  renderFilasManual('');
  lucide.createIcons();
};

function renderFilasManual(query) {
  if (!Array.isArray(_sucursalesCache)) return;
  _syncKgDesdeDOM();

  const q     = query.toLowerCase().trim();
  const tbody = document.getElementById('body-manual');
  tbody.innerHTML = '';

  const lista = q
    ? _sucursalesCache.filter(s => (s.nombre_base || '').toLowerCase().includes(q))
    : _sucursalesCache;

  if (!lista.length) {
    tbody.innerHTML = '<tr><td colspan="3" class="manual-loading">Sin resultados</td></tr>';
    return;
  }

  for (const suc of lista) {
    const nombre   = suc.nombre_base || '';
    const idSuc    = suc.num_tienda  ?? 'N/A';
    const kgActual = _manualKgMap[nombre] ?? 0;
    const tr = document.createElement('tr');
    const inputClass = `manual-kg-input${kgActual > 0 ? ' manual-kg-input--con-valor' : ''}`;
    tr.innerHTML = `
      <td class="manual-id-cell">${idSuc}</td>
      <td>${nombre}</td>
      <td>
        <div class="manual-kg-wrap">
          <input type="number" class="${inputClass}"
                 min="0" step="1" value="${kgActual || ''}"
                 placeholder="0"
                 data-nombre="${encodeURIComponent(nombre)}"
                 data-id-suc="${idSuc}">
          <span class="manual-kg-unit">kg</span>
        </div>
      </td>`;
    tbody.appendChild(tr);
  }
}

async function cerrarModalManual() {
  const ok = await mostrarConfirmGen(
    'Salir de la captura manual',
    '¿Está seguro de que desea salir de la pestaña? Los cambios no guardados se perderán.',
    'Salir'
  );
  if (!ok) return;
  _cerrarModalForzado();
}

async function guardarManual() {
  if (!_manualPerfilActivo || !Array.isArray(_sucursalesCache)) return;
  _syncKgDesdeDOM();

  const perfil = _manualPerfilActivo;
  const datos  = {};

  for (const [nombre, kg] of Object.entries(_manualKgMap)) {
    if (kg <= 0) continue;
    const suc   = _sucursalesCache.find(s => s.nombre_base === nombre);
    const idRaw = suc?.num_tienda ?? 'N/A';
    datos[nombre] = {
      id_sucursal: isNaN(Number(idRaw)) ? idRaw : Number(idRaw),
      kg,
    };
  }

  if (Object.keys(datos).length === 0) {
    mostrarToast('Ingresa al menos un peso mayor a 0.', 'error');
    return;
  }

  const ok = await mostrarConfirmGen(
    'Guardar datos manuales',
    '¿Está seguro de que desea guardar los cambios realizados?',
    'Guardar'
  );
  if (!ok) return;

  state.perfiles[perfil].datos   = datos;
  state.perfiles[perfil].volumen = null;
  state.perfiles[perfil].nombre  = '(manual)';
  state.perfiles[perfil].status  = 'cargado';
  state.hayUnsaved = true;

  _cerrarModalForzado();
  renderPerfilCargado(perfil);
  recalcularConsolidados();
  actualizarUI();
  mostrarToast(`${perfil.toUpperCase()} cargado manualmente`, 'ok');
}

function mostrarToast(msg, tipo = 'info') {
  const colores = { ok: '#16a34a', error: '#dc2626', info: '#0056b3', warn: '#d97706' };

  let toast = document.querySelector('.ext-toast');
  if (!toast) {
    toast = document.createElement('div');
    toast.className = 'ext-toast';
    document.body.appendChild(toast);
  }

  toast.style.background = colores[tipo] || colores.info;
  toast.style.opacity    = '1';
  toast.textContent      = msg;

  clearTimeout(toast._timer);
  // Las advertencias con listas de claves/códigos necesitan más tiempo de lectura.
  const duracion = tipo === 'warn' ? 9000 : 3500;
  toast._timer = setTimeout(() => { toast.style.opacity = '0'; }, duracion);
}

/**
 * Pinta de forma persistente en la interfaz (no como notificación) el banner
 * "encontrados/total productos procesados" del perfil indicado, y si hay
 * elementos sin localizar agrega "Códigos o claves SAE no encontrados: …".
 * perfil debe corresponder a un banner existente en el HTML (#advertencia-<perfil>).
 */
function renderAdvertenciaBanner(perfil, total, faltantes) {
  const banner    = document.getElementById(`advertencia-${perfil}`);
  const resumenEl = document.getElementById(`advertencia-${perfil}-resumen`);
  const listaEl   = document.getElementById(`advertencia-${perfil}-lista`);
  if (!banner || !resumenEl || !listaEl) return;

  if (!total) {
    banner.style.display = 'none';
    return;
  }

  faltantes = faltantes || [];
  const encontrados     = total - faltantes.length;
  const tieneFaltantes  = faltantes.length > 0;

  banner.style.display = 'flex';
  banner.classList.toggle('ext-advertencia-banner--ok', !tieneFaltantes);
  resumenEl.textContent = `${encontrados}/${total} productos procesados`;
  listaEl.textContent   = tieneFaltantes
    ? `Códigos o claves SAE no encontrados: ${faltantes.join(', ')}`
    : '';

  const icon = banner.querySelector('[data-lucide]');
  if (icon) icon.setAttribute('data-lucide', tieneFaltantes ? 'alert-triangle' : 'check-circle-2');
  lucide.createIcons();
}
