// ===== Gestión de Usuarios (solo administradores) =====

const ROL_LABEL = { logistica: "Logística", conductor: "Conductor" };

let _usuarios          = [];
let _modalModo         = "crear";   // "crear" | "editar"
let _usuarioEditandoId = null;
let _usuarioEliminarId = null;

document.addEventListener("DOMContentLoaded", () => {
  lucide.createIcons();
  cargarUsuarios();

  document.getElementById("btn-nuevo-usuario").addEventListener("click", abrirModalCrear);
  document.getElementById("usr-buscar").addEventListener("input", renderTabla);
  document.getElementById("btn-cancelar-usuario").addEventListener("click", cerrarModalUsuario);
  document.getElementById("btn-guardar-usuario").addEventListener("click", guardarUsuario);
  document.getElementById("btn-toggle-password").addEventListener("click", toggleMostrarPassword);
  document.getElementById("btn-cancelar-eliminar").addEventListener("click", cerrarModalEliminar);
  document.getElementById("btn-confirmar-eliminar").addEventListener("click", confirmarEliminar);

  document.querySelector("#tabla-usuarios tbody").addEventListener("click", (e) => {
    const btnEditar = e.target.closest("[data-accion='editar']");
    if (btnEditar) { abrirModalEditar(btnEditar.dataset.id); return; }
    const btnEliminar = e.target.closest("[data-accion='eliminar']");
    if (btnEliminar) abrirConfirmEliminar(btnEliminar.dataset.id, btnEliminar.dataset.username);
  });
});

// ── Carga y render ────────────────────────────────────────────────
async function cargarUsuarios() {
  const tbody = document.querySelector("#tabla-usuarios tbody");
  tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;color:#999">Cargando…</td></tr>`;
  try {
    const res = await fetch("/usuarios/api/usuarios", { cache: "no-store" });
    _usuarios = res.ok ? await res.json() : [];
    renderTabla();
  } catch (err) {
    console.error("[cargarUsuarios]", err);
    tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;color:#ef4444">Error de conexión.</td></tr>`;
  }
}

function renderTabla() {
  const tbody = document.querySelector("#tabla-usuarios tbody");
  const q = (document.getElementById("usr-buscar").value || "").toLowerCase().trim();

  const filtrados = _usuarios.filter(u =>
    !q || u.username.toLowerCase().includes(q) || (u.nombre || "").toLowerCase().includes(q)
  );

  if (filtrados.length === 0) {
    tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;color:#999">Sin resultados.</td></tr>`;
    return;
  }

  tbody.innerHTML = filtrados.map(u => {
    const rolBadge = u.es_admin
      ? `<span class="usr-badge usr-badge--admin">Administrador</span>`
      : `<span class="usr-badge usr-badge--${u.rol}">${h(ROL_LABEL[u.rol] || u.rol)}</span>`;
    const ultimoAcceso = u.ultimo_login
      ? new Date(u.ultimo_login).toLocaleString("es-MX", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })
      : "Nunca";
    return `
      <tr>
        <td>${h(u.username)}</td>
        <td>${h(u.nombre)}</td>
        <td>${rolBadge}</td>
        <td>${ultimoAcceso}</td>
        <td>
          <button class="btn btn-sm btn-warning" data-accion="editar" data-id="${u.id}">Editar</button>
          <button class="btn btn-sm btn-danger" data-accion="eliminar" data-id="${u.id}" data-username="${ha(u.username)}">Eliminar</button>
        </td>
      </tr>`;
  }).join("");
}

// ── Modal crear/editar ──────────────────────────────────────────────
function abrirModalCrear() {
  _modalModo = "crear";
  _usuarioEditandoId = null;
  document.getElementById("modal-usuario-titulo").textContent = "Nuevo usuario";
  document.getElementById("campo-username").value = "";
  document.getElementById("campo-nombre").value = "";
  document.getElementById("campo-password").value = "";
  document.getElementById("campo-password").type = "password";
  document.getElementById("campo-password-hint").textContent = "(obligatoria, mínimo 6 caracteres)";
  document.getElementById("campo-es-admin").checked = false;
  document.getElementById("grupo-es-admin").classList.remove("hidden");
  ocultarErrorModal();
  document.getElementById("modal-usuario").classList.remove("hidden");
  document.getElementById("campo-username").focus();
}

async function abrirModalEditar(usuarioId) {
  const usuario = _usuarios.find(u => u.id === usuarioId);
  if (!usuario) return;
  _modalModo = "editar";
  _usuarioEditandoId = usuarioId;

  document.getElementById("modal-usuario-titulo").textContent = `Editar usuario — ${usuario.username}`;
  document.getElementById("campo-username").value = usuario.username;
  document.getElementById("campo-nombre").value = usuario.nombre || "";
  document.getElementById("campo-password").value = "";
  document.getElementById("campo-password").type = "password";
  document.getElementById("campo-password-hint").textContent = "(deja en blanco para no cambiarla)";

  // El concepto de "administrador" solo aplica a usuarios de Logística.
  const grupoAdmin = document.getElementById("grupo-es-admin");
  if (usuario.rol === "conductor") {
    grupoAdmin.classList.add("hidden");
  } else {
    grupoAdmin.classList.remove("hidden");
    document.getElementById("campo-es-admin").checked = usuario.es_admin;
  }

  ocultarErrorModal();
  document.getElementById("modal-usuario").classList.remove("hidden");
}

function cerrarModalUsuario() {
  document.getElementById("modal-usuario").classList.add("hidden");
}

function ocultarErrorModal() {
  document.getElementById("modal-usuario-error").classList.add("hidden");
}
function mostrarErrorModal(msg) {
  const el = document.getElementById("modal-usuario-error");
  el.textContent = msg;
  el.classList.remove("hidden");
}

async function guardarUsuario() {
  const username = document.getElementById("campo-username").value.trim();
  const nombre   = document.getElementById("campo-nombre").value.trim();
  const password = document.getElementById("campo-password").value;
  const grupoAdminVisible = !document.getElementById("grupo-es-admin").classList.contains("hidden");
  const esAdmin  = grupoAdminVisible ? document.getElementById("campo-es-admin").checked : undefined;

  if (!username) { mostrarErrorModal("El usuario es obligatorio."); return; }
  if (_modalModo === "crear" && !password) { mostrarErrorModal("La contraseña es obligatoria al crear un usuario."); return; }

  const payload = { username, nombre };
  if (password) payload.password = password;
  if (esAdmin !== undefined) payload.es_admin = esAdmin;

  const btn = document.getElementById("btn-guardar-usuario");
  btn.disabled = true;
  try {
    const url    = _modalModo === "crear" ? "/usuarios/api/usuarios" : `/usuarios/api/usuarios/${_usuarioEditandoId}`;
    const method = _modalModo === "crear" ? "POST" : "PUT";
    const res  = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    const data = await res.json().catch(() => null);

    if (!res.ok || data?.status === "error") {
      mostrarErrorModal(data?.mensaje || "No se pudo guardar el usuario.");
      return;
    }
    cerrarModalUsuario();
    await cargarUsuarios();
    mostrarToastUsr(_modalModo === "crear" ? "Usuario creado correctamente." : "Usuario actualizado correctamente.", "ok");
  } catch (err) {
    console.error("[guardarUsuario]", err);
    mostrarErrorModal("Error de conexión al guardar el usuario.");
  } finally {
    btn.disabled = false;
  }
}

function toggleMostrarPassword() {
  const input = document.getElementById("campo-password");
  const icono = document.getElementById("icono-toggle-password");
  const mostrar = input.type === "password";
  input.type = mostrar ? "text" : "password";
  icono.setAttribute("data-lucide", mostrar ? "eye-off" : "eye");
  lucide.createIcons();
}

// ── Eliminar ─────────────────────────────────────────────────────────
function abrirConfirmEliminar(usuarioId, username) {
  _usuarioEliminarId = usuarioId;
  document.getElementById("modal-eliminar-msg").textContent =
    `¿Eliminar al usuario "${username}"? Esta acción no se puede deshacer.`;
  document.getElementById("modal-eliminar-usuario").classList.remove("hidden");
}

function cerrarModalEliminar() {
  _usuarioEliminarId = null;
  document.getElementById("modal-eliminar-usuario").classList.add("hidden");
}

async function confirmarEliminar() {
  if (!_usuarioEliminarId) return;
  const btn = document.getElementById("btn-confirmar-eliminar");
  btn.disabled = true;
  try {
    const res  = await fetch(`/usuarios/api/usuarios/${_usuarioEliminarId}`, { method: "DELETE" });
    const data = await res.json().catch(() => null);
    if (!res.ok || data?.status === "error") {
      mostrarToastUsr(data?.mensaje || "No se pudo eliminar el usuario.", "error");
    } else {
      mostrarToastUsr("Usuario eliminado.", "ok");
      await cargarUsuarios();
    }
  } catch (err) {
    console.error("[confirmarEliminar]", err);
    mostrarToastUsr("Error de conexión al eliminar el usuario.", "error");
  } finally {
    btn.disabled = false;
    cerrarModalEliminar();
  }
}

// ── Utilidades ───────────────────────────────────────────────────────
function h(s)  { return String(s ?? "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;"); }
function ha(s) { return String(s ?? "").replace(/"/g,"&quot;").replace(/'/g,"&#39;"); }

function mostrarToastUsr(mensaje, tipo = "ok") {
  const container = document.getElementById("usr-toast-container");
  const toast = document.createElement("div");
  toast.className = `usr-toast ${tipo}`;
  toast.textContent = mensaje;
  container.appendChild(toast);
  const quitar = () => {
    toast.style.animation = "usr-toast-out .22s ease forwards";
    toast.addEventListener("animationend", () => toast.remove(), { once: true });
  };
  toast.addEventListener("click", quitar);
  setTimeout(quitar, 4000);
}
