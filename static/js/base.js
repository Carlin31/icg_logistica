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