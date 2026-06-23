// ===== UTILIDADES GLOBALES =====

// ── Sesión expirada: si cualquier fetch() responde 401, volver al login ──────
(function () {
  const _fetchOriginal = window.fetch;
  window.fetch = function (...args) {
    return _fetchOriginal.apply(this, args).then((res) => {
      if (res.status === 401) {
        window.location.href = "/auth/login";
      }
      return res;
    });
  };
})();

// ── Fullscreen Loader ────────────────────────────────────────────────────────
// Uso: Loader.show("Título", ["Mensaje 1…", "Mensaje 2…"])
//      Loader.hide()
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

// Petición POST genérica con JSON
async function postJSON(url, datos) {
    const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(datos)
    });
    return await res.json();
}

// Mostrar mensaje de estado en pantalla
function mostrarMensaje(contenedor, mensaje, tipo = 'info') {
    const el = document.querySelector(contenedor);
    if (el) {
        el.innerHTML = `<div class="mensaje ${tipo}">${mensaje}</div>`;
    }
}

// ── Footer: oculto por defecto, solo aparece tras hacer scroll ───────────────
(function () {
    const footer = document.querySelector('.app-footer');
    if (!footer) return;

    function actualizarVisibilidad() {
        footer.classList.toggle('visible', window.scrollY > 10);
    }

    window.addEventListener('scroll', actualizarVisibilidad, { passive: true });
    actualizarVisibilidad();
})();

// ── Zoom de página, guardado por usuario en el servidor ──────────────────────
(function () {
    const ZOOM_MIN  = 80;
    const ZOOM_MAX  = 150;
    const ZOOM_STEP = 10;
    const ZOOM_DEFAULT = 100;

    const control = document.getElementById('zoom-control');
    if (!control) return; // sin sesión iniciada: no se muestra el control

    const btnOut  = document.getElementById('btn-zoom-out');
    const btnIn   = document.getElementById('btn-zoom-in');
    const nivelEl = document.getElementById('zoom-nivel');

    let _zoom = ZOOM_DEFAULT;
    let _guardarTimer = null;

    function aplicarZoom(pct) {
        document.documentElement.style.zoom = pct + '%';
        if (nivelEl) nivelEl.textContent = pct + '%';
        if (btnOut)  btnOut.disabled = pct <= ZOOM_MIN;
        if (btnIn)   btnIn.disabled  = pct >= ZOOM_MAX;
    }

    function guardarZoom(pct) {
        clearTimeout(_guardarTimer);
        _guardarTimer = setTimeout(() => {
            fetch('/auth/zoom', {
                method:  'POST',
                headers: { 'Content-Type': 'application/json' },
                body:    JSON.stringify({ zoom: pct }),
            }).catch(err => console.warn('[zoom]', err));
        }, 300);
    }

    function cambiarZoom(delta) {
        _zoom = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, _zoom + delta));
        aplicarZoom(_zoom);
        guardarZoom(_zoom);
    }

    if (btnOut) btnOut.addEventListener('click', () => cambiarZoom(-ZOOM_STEP));
    if (btnIn)  btnIn.addEventListener('click',  () => cambiarZoom(ZOOM_STEP));

    // Cargar el nivel guardado para este usuario y aplicarlo de inmediato
    fetch('/auth/zoom')
        .then(res => res.ok ? res.json() : { zoom: ZOOM_DEFAULT })
        .then(data => {
            _zoom = Number(data.zoom) || ZOOM_DEFAULT;
            aplicarZoom(_zoom);
        })
        .catch(() => aplicarZoom(ZOOM_DEFAULT));
})();