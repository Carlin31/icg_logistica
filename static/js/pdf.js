// ===== SECCIÓN 7 — Generación de Reporte PDF =====
// Flujo: Generar → previsualizar inline → habilitar descarga separada.

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