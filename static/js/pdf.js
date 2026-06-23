// ===== SECCIÓN 7 — Generación de Reporte PDF =====
// Flujo: Generar → previsualizar inline → habilitar descarga separada.
// Autorizar (acción separada) → habilita las rutas para los choferes y la pestaña Seguimiento.

const MSG_PDF = {
  generar: [
    "Generando reporte PDF…",
    "Compilando datos por día y ruta…",
    "Calculando porcentajes de utilización…",
    "Construyendo tablas de sucursales…",
    "Aplicando formato al documento…",
    "Finalizando el reporte…",
  ],
};

const ESTADO_AUTORIZACION_INFO = {
  sin_autorizar: { texto: "Sin autorizar", icono: "lock" },
  autorizado:    { texto: "Autorizado",    icono: "shield-check" },
  cancelada:     { texto: "Cancelada",     icono: "shield-off" },
};

// Almacena el blob URL del último PDF generado para su descarga posterior
let _blobUrl   = null;
let _filename  = "reporte_pesos.pdf";

document.addEventListener('DOMContentLoaded', () => {
  inicializar();
});

async function inicializar() {
  await cargarLogisticaActiva();
  document.getElementById('btn-generar')?.addEventListener('click', generarPDF);
  document.getElementById('btn-descargar')?.addEventListener('click', descargarPDF);
  document.getElementById('btn-autorizar')?.addEventListener('click', autorizarRutas);
  document.getElementById('btn-cancelar-autorizacion')?.addEventListener('click', abrirModalCancelarAutorizacion);
  document.getElementById('modal-cancelar-no')?.addEventListener('click', cerrarModalCancelarAutorizacion);
  document.getElementById('modal-cancelar-si')?.addEventListener('click', confirmarCancelarAutorizacion);
  await cargarEstadoAutorizacion();
}

async function cargarLogisticaActiva() {
  try {
    const res  = await fetch('/api/activa');
    const data = await res.json();
    const infoCard     = document.getElementById('logistica-info');
    const sinLogistica = document.getElementById('sin-logistica');
    const controles    = document.getElementById('controles');

    if (data.status === 'ok') {
      document.getElementById('logistica-nombre').textContent = data.nombre ?? '—';
      document.getElementById('logistica-rango').textContent  =
        data.inicio && data.fin ? `${data.inicio} — ${data.fin}` : '—';
      infoCard.style.display    = '';
      controles.style.display   = '';
      sinLogistica.style.display = 'none';
    } else {
      infoCard.style.display    = 'none';
      controles.style.display   = 'none';
      sinLogistica.style.display = '';
    }
  } catch (err) {
    console.error('Error al consultar logística activa:', err);
  }
}

// ── Generar PDF y previsualizarlo ────────────────────────────
async function generarPDF() {
  const btnGen      = document.getElementById('btn-generar');
  const btnDesc     = document.getElementById('btn-descargar');
  const errDiv      = document.getElementById('mensaje-error');
  const zonaPreview = document.getElementById('zona-preview');

  // Limpiar estado anterior
  errDiv.style.display  = 'none';
  errDiv.textContent    = '';
  zonaPreview.style.display = 'none';

  // Liberar blob URL previo para no acumular memoria
  if (_blobUrl) { URL.revokeObjectURL(_blobUrl); _blobUrl = null; }

  btnGen.disabled  = true;
  btnDesc.disabled = true;
  Loader.show('Generando Reporte PDF', MSG_PDF.generar);

  try {
    const res = await fetch('/pdf/generar', { method: 'POST' });

    if (res.status === 400) {
      let mensaje = 'No hay logística activa.';
      try { const json = await res.json(); mensaje = json.mensaje ?? mensaje; } catch (_) {}
      Loader.hide();
      alert(`Advertencia: ${mensaje}\n\nSerás redirigido al menú principal.`);
      window.location.href = '/';
      return;
    }

    if (!res.ok) {
      let mensaje = `Error ${res.status}`;
      try { const json = await res.json(); mensaje = json.mensaje ?? mensaje; } catch (_) {}
      throw new Error(mensaje);
    }

    // Capturar nombre desde Content-Disposition
    const disposition = res.headers.get('Content-Disposition') ?? '';
    const match       = disposition.match(/filename\*?=(?:UTF-8'')?["']?([^"';\n]+)/i);
    _filename = match ? decodeURIComponent(match[1]) : 'reporte_pesos.pdf';

    // Crear URL del blob para previsualización y descarga
    const blob = await res.blob();
    _blobUrl   = URL.createObjectURL(blob);

    Loader.hide();

    // Inyectar en el iframe
    const iframe = document.getElementById('pdf-iframe');
    iframe.src   = _blobUrl;
    document.getElementById('preview-nombre').textContent = _filename;
    zonaPreview.style.display = 'block';

    // Habilitar descarga
    btnDesc.disabled = false;

  } catch (err) {
    Loader.hide();
    errDiv.innerHTML = `<i data-lucide="circle-x" class="pdf-icon" aria-hidden="true"></i><span>${err.message}</span>`;
    if (window.lucide?.createIcons) {
      window.lucide.createIcons({ attrs: { class: 'pdf-icon' } });
    }
    errDiv.style.display = '';
    console.error('Error al generar PDF:', err);
  } finally {
    btnGen.disabled = false;
  }
}

// ── Descargar el PDF ya generado ─────────────────────────────
function descargarPDF() {
  if (!_blobUrl) return;
  const a    = document.createElement('a');
  a.href     = _blobUrl;
  a.download = _filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

// ── Autorización de rutas ──────────────────────────────────────
async function cargarEstadoAutorizacion() {
  try {
    const res  = await fetch('/pdf/estado-autorizacion');
    const data = await res.json();
    if (data.status === 'ok') pintarEstadoAutorizacion(data);
  } catch (err) {
    console.error('[cargarEstadoAutorizacion]', err);
  }
}

function pintarEstadoAutorizacion(data) {
  const estadoEl   = document.getElementById('autorizacion-estado');
  const textoEl    = document.getElementById('autorizacion-estado-texto');
  const metaEl     = document.getElementById('autorizacion-meta');
  const btnAut     = document.getElementById('btn-autorizar');
  const btnCancel  = document.getElementById('btn-cancelar-autorizacion');
  const info       = ESTADO_AUTORIZACION_INFO[data.estado] || ESTADO_AUTORIZACION_INFO.sin_autorizar;

  estadoEl.className = `autorizacion-estado ${data.estado}`;
  estadoEl.querySelector('i')?.setAttribute('data-lucide', info.icono);
  textoEl.textContent = info.texto;

  if (data.estado === 'autorizado') {
    metaEl.textContent = `Autorizado por ${data.autorizado_por || '—'} el ${formatearFechaHora(data.autorizado_en)}.`;
    btnAut.style.display    = 'none';
    btnCancel.style.display = '';
  } else if (data.estado === 'cancelada') {
    metaEl.textContent = `Autorización cancelada por ${data.cancelado_por || '—'} el ${formatearFechaHora(data.cancelado_en)}. Puedes volver a autorizar cuando quieras.`;
    btnAut.style.display    = '';
    btnCancel.style.display = 'none';
  } else {
    metaEl.textContent = 'Las rutas de esta logística todavía no están disponibles para los choferes.';
    btnAut.style.display    = '';
    btnCancel.style.display = 'none';
  }

  btnAut.disabled = !data.puede_autorizar;
  if (!data.puede_autorizar && data.estado !== 'autorizado') {
    metaEl.textContent = 'Genera el PDF de esta logística antes de poder autorizar sus rutas.';
  }

  if (window.lucide?.createIcons) window.lucide.createIcons();
}

function formatearFechaHora(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return isNaN(d) ? '—' : d.toLocaleString('es-MX', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

async function autorizarRutas() {
  const btn = document.getElementById('btn-autorizar');
  btn.disabled = true;
  Loader.show('Autorizando rutas', ['Habilitando rutas para los choferes…', 'Activando la pestaña Seguimiento…']);
  try {
    const res  = await fetch('/pdf/autorizar', { method: 'POST' });
    const data = await res.json();
    Loader.hide();
    if (data.status !== 'ok') {
      alert(data.mensaje || 'No se pudo autorizar la logística.');
      btn.disabled = false;
      return;
    }
    await cargarEstadoAutorizacion();
  } catch (err) {
    Loader.hide();
    console.error('[autorizarRutas]', err);
    alert('Error de conexión al autorizar.');
    btn.disabled = false;
  }
}

async function abrirModalCancelarAutorizacion() {
  const aviso     = document.getElementById('modal-cancelar-aviso-entregas');
  const avisoTxt  = document.getElementById('modal-cancelar-aviso-texto');
  aviso.style.display = 'none';

  try {
    const res  = await fetch('/pdf/entregas-resumen');
    const data = await res.json();
    if (data.status === 'ok' && data.entregas > 0) {
      avisoTxt.textContent =
        `Esta logística ya tiene ${data.entregas} entrega(s) registrada(s) por los choferes. ` +
        `Si cancelas la autorización, esos registros de entrega se eliminarán permanentemente y no podrán recuperarse.`;
      aviso.style.display = '';
    }
  } catch (err) {
    console.error('[entregas-resumen]', err);
  }

  document.getElementById('modal-cancelar-autorizacion').classList.remove('hidden');
}

function cerrarModalCancelarAutorizacion() {
  document.getElementById('modal-cancelar-autorizacion').classList.add('hidden');
}

async function confirmarCancelarAutorizacion() {
  const btn = document.getElementById('modal-cancelar-si');
  btn.disabled = true;
  try {
    const res  = await fetch('/pdf/cancelar-autorizacion', { method: 'POST' });
    const data = await res.json();
    if (data.status !== 'ok') {
      alert(data.mensaje || 'No se pudo cancelar la autorización.');
      return;
    }
    cerrarModalCancelarAutorizacion();
    await cargarEstadoAutorizacion();
  } catch (err) {
    console.error('[confirmarCancelarAutorizacion]', err);
    alert('Error de conexión al cancelar la autorización.');
  } finally {
    btn.disabled = false;
  }
}
