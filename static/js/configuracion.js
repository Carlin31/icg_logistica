// ===== Configuración — Productos, Sucursales, Vehículos y General =====

const DIAS_SEMANA = [
  { key: "lunes",     label: "Lunes"     },
  { key: "martes",    label: "Martes"    },
  { key: "miercoles", label: "Miércoles" },
  { key: "jueves",    label: "Jueves"    },
  { key: "viernes",   label: "Viernes"   },
  { key: "sabado",    label: "Sábado"    },
  { key: "domingo",   label: "Domingo"   },
];

function getEndpoint(tipo) {
  if (tipo === "producto")          return "productos";
  if (tipo === "producto_proalmex") return "productos-proalmex";
  if (tipo === "producto_bimbo")    return "productos-bimbo";
  if (tipo === "sucursal")          return "sucursales";
  if (tipo === "cliente_mayorista") return "clientes-mayoristas";
  return tipo + "s";
}

// ── Unidades de medida ICG ────────────────────────────────────────
const _UNIDADES_DEFAULT = ["PZA", "KG", "LT", "CAJA", "BULTO", "COSTAL", "ROLLO", "PAQUETE", "BOLSA", "TONELADA"];
let UNIDADES_MEDIDA = (() => {
  try {
    const saved = JSON.parse(localStorage.getItem("icg_unidades") || "null");
    if (Array.isArray(saved) && saved.length) return saved;
  } catch {}
  return [..._UNIDADES_DEFAULT];
})();

function _opcionesUnidad(actual) {
  return UNIDADES_MEDIDA.map(u =>
    `<option value="${h(u)}"${u === actual ? " selected" : ""}>${h(u)}</option>`
  ).join("");
}

function _onUnidadChange(sel) {
  if (sel.value !== "__nueva__") return;
  const nueva = prompt("Nueva unidad de medida (ej. PZA, KG, LT):")?.trim().toUpperCase();
  if (!nueva) { sel.value = ""; return; }
  if (!UNIDADES_MEDIDA.includes(nueva)) {
    UNIDADES_MEDIDA.push(nueva);
    localStorage.setItem("icg_unidades", JSON.stringify(UNIDADES_MEDIDA));
    const opt = document.createElement("option");
    opt.value = nueva; opt.textContent = nueva;
    sel.insertBefore(opt, sel.lastElementChild);
  }
  sel.value = nueva;
}

// Esquemas alineados con MongoDB (¡AQUÍ SE AGREGAN LOS CAMPOS PARA EL MODAL!)
const CAMPOS_PRODUCTO_ICG = [
  { key: "clave_sae",      label: "Clave SAE",         type: "number" },
  { key: "codigo_barras",  label: "Código de Barras",  type: "text"   },
  { key: "marca",          label: "Marca",             type: "text"   },
  { key: "descripcion",    label: "Descripción",       type: "text"   },
  { key: "unidad_medida",  label: "Unidad de Medida",  type: "select" },
  { key: "costo",          label: "Costo",             type: "number" },
  { key: "peso",           label: "Peso (kg)",         type: "number", step: "1", min: "0" },
  { key: "largo",          label: "Largo (cm)",        type: "number" },
  { key: "ancho",          label: "Ancho (cm)",        type: "number" },
  { key: "alto",           label: "Alto (cm)",         type: "number" },
  { key: "volumen",        label: "Volumen (m³)",      type: "number", readonly: true },
];

const CAMPOS_PRODUCTO_PROALMEX = [
  { key: "marca",   label: "Marca",   type: "text"   },
  { key: "linea",   label: "Descripción", type: "text"   },
  { key: "tamano",  label: "Tamaño",  type: "text"   },
  { key: "costo",   label: "Costo",   type: "number" },
  { key: "peso",    label: "Peso (kg)", type: "number" },
];

const CAMPOS_SUCURSAL = [
  { key: "num_tienda",         label: "Núm. Tienda",      type: "number" },
  { key: "estado",             label: "Estado",           type: "text"   },
  { key: "nombre_base",        label: "Nombre Base",      type: "text"   },
  { key: "nombre_icg-proalmex",label: "Nombre ICG-Proalmex", type: "text" },
  { key: "nombre_bimbo",       label: "Nombre Bimbo",     type: "text"   },
  { key: "latitud",            label: "Latitud",          type: "number" },
  { key: "longitud",           label: "Longitud",         type: "number" },
  { key: "hora_inicio",        label: "Hora Inicio",      type: "text"   },
  { key: "hora_fin",           label: "Hora Fin",         type: "text"   },
];

const CAMPOS_VEHICULO = [
  { key: "descripcion",         label: "Descripción",          type: "text"   },
  { key: "abreviatura",         label: "Abreviatura",          type: "text"   },
  { key: "placas",              label: "Placas",               type: "text"   },
  { key: "chofer",              label: "Chofer",               type: "select" },
  { key: "largo_volumetria",    label: "Largo Volumetría (m)", type: "number" },
  { key: "ancho_volumetria",    label: "Ancho Volumetría (m)", type: "number" },
  { key: "alto_volumetria",     label: "Alto Volumetría (m)",  type: "number" },
  { key: "capacidad_toneladas", label: "Capacidad (ton)",      type: "number" },
  { key: "categoria",           label: "Categoría",            type: "text"   },
  { key: "volumen_m3",          label: "Volumen (m³)",         type: "number", readonly: true },
];

const CAMPOS_PRODUCTO_BIMBO = [
  { key: "codigo_barra", label: "Código de Barra", type: "text"   },
  { key: "descripcion",  label: "Descripción",     type: "text"   },
  { key: "peso",         label: "Peso (kg)",        type: "number", step: "any", min: "0" },
  { key: "altura",       label: "Altura (cm)",      type: "number" },
  { key: "ancho",        label: "Ancho (cm)",       type: "number" },
  { key: "largo",        label: "Largo (cm)",       type: "number" },
  { key: "volumen",      label: "Volumen (m³)",     type: "number", readonly: true },
];

const CAMPOS_CLIENTE_MAYORISTA = [
  { key: "id_cliente", label: "ID Cliente", type: "number" },
  { key: "nombre",     label: "Nombre",     type: "text"   },
  { key: "poblacion",  label: "Población",  type: "text"   },
  { key: "latitud",    label: "Latitud",    type: "number" },
  { key: "longitud",   label: "Longitud",   type: "number" },
];

// ── Mensajes contextuales del loader ────────────────────────────
const MSG_CFG = {
  guardar_general: [
    "Actualizando parámetros de operación…",
    "Guardando configuración de días y horarios…",
    "Sincronizando con los módulos del sistema…",
    "Aplicando cambios…",
  ],
  crear: [
    "Registrando en la base de datos…",
    "Validando datos del nuevo registro…",
    "Aplicando configuraciones…",
  ],
  editar: [
    "Actualizando registro…",
    "Guardando cambios en la base de datos…",
    "Aplicando modificaciones…",
  ],
  eliminar: [
    "Eliminando registro…",
    "Borrando de la base de datos…",
    "Limpiando datos asociados…",
  ],
};

const _TIPO_LABEL = {
  producto:          "Producto ICG",
  producto_proalmex: "Producto Proalmex",
  producto_bimbo:    "Producto Bimbo",
  sucursal:          "Sucursal",
  vehiculo:          "Vehículo",
  cliente_mayorista: "Cliente Mayorista",
};

let modalMode  = null;
let modalTipo  = null;
let modalDocId = null;
let _choferes  = [];   // [{ _id, nombre }]

// ── Choferes ─────────────────────────────────────────────────
async function cargarChoferes() {
  try {
    const res = await fetch("/configuracion/choferes", { cache: "no-store" });
    _choferes = res.ok ? await res.json() : [];
  } catch (err) {
    console.error("[cargarChoferes]", err);
    _choferes = [];
  }
  renderListaChoferes();
  return _choferes;
}

function renderListaChoferes() {
  const ul = document.getElementById("lista-choferes");
  if (!ul) return;
  if (_choferes.length === 0) {
    ul.innerHTML = `<li class="choferes-empty">Sin choferes registrados.</li>`;
    return;
  }
  ul.innerHTML = _choferes.map(c => `
    <li>
      <span>${h(c.nombre)}</span>
      <span class="choferes-li-acciones">
        ${c.tiene_acceso
          ? `<span class="chofer-acceso-badge" title="Ya tiene credenciales para el portal del Conductor">
               <i data-lucide="check-circle"></i> Acceso generado
             </span>`
          : `<button type="button" class="btn-generar-acceso" data-id="${c._id}" data-nombre="${ha(c.nombre)}" title="Generar acceso al portal del Conductor">
               <i data-lucide="key-round"></i> Generar acceso
             </button>`}
        <button type="button" data-id="${c._id}" title="Eliminar chofer">
          <i data-lucide="trash-2"></i>
        </button>
      </span>
    </li>`).join("");
  lucide.createIcons();
}

async function generarAccesoChofer(choferId, nombre, btn) {
  if (btn) { btn.disabled = true; btn.innerHTML = `<i data-lucide="loader-circle"></i> Generando…`; lucide.createIcons(); }
  try {
    const res = await fetch(`/configuracion/choferes/${choferId}/generar-acceso`, { method: "POST" });
    const data = await res.json().catch(() => null);
    if (!res.ok || data?.status === "error") {
      cfgToast(data?.mensaje || "Error al generar el acceso.");
      await cargarChoferes();
      return;
    }
    mostrarCredencialesChofer(nombre, data.username, data.password);
    await cargarChoferes();
  } catch (err) {
    console.error("[generarAccesoChofer]", err);
    cfgToast("Error de conexión al generar el acceso.");
    await cargarChoferes();
  }
}

/** Muestra usuario/contraseña UNA SOLA VEZ (no se pueden recuperar después). */
function mostrarCredencialesChofer(nombre, username, password) {
  const errEl = document.getElementById("choferes-error");
  errEl.classList.remove("hidden");
  errEl.classList.add("choferes-credenciales");
  errEl.innerHTML = `
    <strong>Acceso creado para ${h(nombre)}</strong> — cópialo ahora, no podrás verlo de nuevo:<br>
    Usuario: <code>${h(username)}</code> &nbsp; Contraseña: <code>${h(password)}</code>`;
}

async function agregarChofer() {
  const input = document.getElementById("input-nuevo-chofer");
  const errEl = document.getElementById("choferes-error");
  const nombre = input.value.trim();
  errEl.classList.add("hidden");
  errEl.classList.remove("choferes-credenciales");
  if (!nombre) return;

  try {
    const res = await fetch("/configuracion/choferes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ nombre }),
    });
    const data = await res.json().catch(() => null);
    if (!res.ok || data?.status === "error") {
      errEl.textContent = data?.mensaje || "Error al agregar el chofer.";
      errEl.classList.remove("hidden");
      return;
    }
    input.value = "";
    await cargarChoferes();
    cargarDatos("vehiculo");
  } catch (err) {
    console.error("[agregarChofer]", err);
    errEl.textContent = "Error de conexión al agregar el chofer.";
    errEl.classList.remove("hidden");
  }
}

async function eliminarChofer(choferId) {
  const confirmed = await mostrarConfirm(
    "Eliminar chofer",
    "Este chofer se eliminará de la lista. Los vehículos que ya lo tengan asignado conservarán el nombre hasta que se cambie manualmente.",
    "Eliminar"
  );
  if (!confirmed) return;
  try {
    const res = await fetch(`/configuracion/choferes/${choferId}`, { method: "DELETE" });
    if (res.ok) {
      await cargarChoferes();
      cargarDatos("vehiculo");
    }
  } catch (err) {
    console.error("[eliminarChofer]", err);
  }
}

async function actualizarChoferVehiculo(vehiculoId, chofer, choferId = null) {
  try {
    const res = await fetch(`/configuracion/vehiculos/${vehiculoId}/chofer`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chofer, chofer_id: choferId }),
    });
    if (res.ok) {
      localStorage.setItem("icg_flota_actualizada", Date.now().toString());
      cfgToast("Chofer actualizado.", "ok");
      cargarDatos("vehiculo");
    } else {
      cfgToast("Error al actualizar el chofer.");
      cargarDatos("vehiculo");
    }
  } catch (err) {
    console.error("[actualizarChoferVehiculo]", err);
    cfgToast("Error de conexión al actualizar el chofer.");
    cargarDatos("vehiculo");
  }
}

/** Genera las <option> del selector de chofer; incluye el valor actual aunque no esté en la lista oficial. */
function _opcionesChofer(actual) {
  const nombres = _choferes.map(c => c.nombre);
  let extra = "";
  if (actual && !nombres.includes(actual)) {
    extra = `<option value="${ha(actual)}" data-no-registrado selected>${h(actual)} (no registrado)</option>`;
  }
  const opciones = _choferes.map(c =>
    `<option value="${ha(c.nombre)}" data-chofer-id="${c._id}" ${c.nombre === actual ? "selected" : ""}>${h(c.nombre)}</option>`
  ).join("");
  return `<option value="" ${!actual ? "selected" : ""}>— Sin asignar —</option>${extra}${opciones}`;
}

// ── Toast de notificación ───────────────────────────────────────
function cfgToast(mensaje, tipo = "error", duracion = 4500) {
  const container = document.getElementById("cfg-toast-container");
  if (!container) return;
  const toast = document.createElement("div");
  toast.className   = `cfg-toast ${tipo}`;
  toast.textContent = mensaje;
  container.appendChild(toast);
  const dismiss = () => {
    toast.style.animation = "toast-out .22s ease forwards";
    toast.addEventListener("animationend", () => toast.remove(), { once: true });
  };
  toast.addEventListener("click", dismiss);
  setTimeout(dismiss, duracion);
}

// ── Error dentro del modal de edición/creación ──────────────────
function mostrarModalError(mensaje) {
  const el = document.getElementById("modal-error");
  if (!el) return;
  el.textContent = mensaje;
  el.classList.remove("hidden");
}

// ── Confirmación genérica (reutiliza el confirm-overlay) ────────
let _confirmResolve = null;

function mostrarConfirm(titulo, mensaje, okLabel = "Confirmar", tipo = "danger") {
  const iconEl = document.getElementById("confirm-icon");
  iconEl.className = `confirm-icon ${tipo}`;
  iconEl.innerHTML = '<i data-lucide="trash-2"></i>';
  lucide.createIcons();
  document.getElementById("confirm-title").textContent = titulo;
  document.getElementById("confirm-msg").textContent   = mensaje;
  document.getElementById("confirm-meta").innerHTML    = "";
  const okBtn = document.getElementById("confirm-ok");
  okBtn.className   = `btn btn-confirm-ok ${tipo === "danger" ? "btn-danger" : "btn-primary"}`;
  okBtn.textContent = okLabel;
  document.getElementById("confirm-overlay").classList.remove("hidden");
  return new Promise(resolve => { _confirmResolve = resolve; });
}

// ── Confirmación de estado de vehículo ──────────────────────────

function mostrarConfirmEstado(descripcion, abreviatura, placas, activo) {
  const esActivar = !activo;
  document.getElementById("confirm-icon").className   = `confirm-icon ${esActivar ? "activar" : "desactivar"}`;
  document.getElementById("confirm-icon").innerHTML   = esActivar
    ? '<i data-lucide="play"></i>'
    : '<i data-lucide="pause"></i>';
  lucide.createIcons();
  document.getElementById("confirm-title").textContent = esActivar ? "Activar vehículo" : "Desactivar vehículo";
  document.getElementById("confirm-msg").textContent   = esActivar
    ? "El vehículo quedará disponible para ser asignado a rutas de reparto."
    : "El vehículo quedará inactivo y no podrá ser asignado a ninguna ruta.";

  const meta = document.getElementById("confirm-meta");
  meta.innerHTML = `<span>Vehículo</span>${h(descripcion)}&nbsp;&nbsp;·&nbsp;&nbsp;${h(abreviatura)}&nbsp;&nbsp;·&nbsp;&nbsp;${h(placas)}`;

  const okBtn = document.getElementById("confirm-ok");
  okBtn.className = `btn btn-confirm-ok ${esActivar ? "btn-success" : "btn-secondary"}`;
  okBtn.textContent = esActivar ? "Activar" : "Desactivar";

  document.getElementById("confirm-overlay").classList.remove("hidden");
  return new Promise(resolve => { _confirmResolve = resolve; });
}

document.addEventListener("DOMContentLoaded", async () => {
  lucide.createIcons();
  initTabs();
  initSortableTables();
  await cargarConfigGeneral();
  await cargarChoferes();
  cargarDatos("producto");
  cargarDatos("producto_proalmex");
  cargarDatos("producto_bimbo");
  cargarDatos("sucursal");
  cargarDatos("vehiculo");
  cargarDatos("cliente_mayorista");

  // Confirmación de estado vehículo
  document.getElementById("confirm-cancel").addEventListener("click", () => {
    document.getElementById("confirm-overlay").classList.add("hidden");
    if (_confirmResolve) { _confirmResolve(false); _confirmResolve = null; }
  });
  document.getElementById("confirm-ok").addEventListener("click", () => {
    document.getElementById("confirm-overlay").classList.add("hidden");
    if (_confirmResolve) { _confirmResolve(true); _confirmResolve = null; }
  });

  document.getElementById("btn-nuevo-producto-icg").addEventListener("click", () => abrirModal("create", "producto"));
  document.getElementById("btn-nuevo-producto-proalmex").addEventListener("click", () => abrirModal("create", "producto_proalmex"));
  document.getElementById("btn-nuevo-producto-bimbo").addEventListener("click", () => abrirModal("create", "producto_bimbo"));
  document.getElementById("btn-nueva-sucursal").addEventListener("click", () => abrirModal("create", "sucursal"));
  document.getElementById("btn-nuevo-vehiculo").addEventListener("click", () => abrirModal("create", "vehiculo"));
  document.getElementById("btn-nuevo-cliente-mayorista").addEventListener("click", () => abrirModal("create", "cliente_mayorista"));
  document.getElementById("modal-cancel").addEventListener("click", cerrarModal);
  document.getElementById("modal-save").addEventListener("click", guardarModal);

  ["producto", "producto_proalmex", "producto_bimbo", "sucursal", "vehiculo", "cliente_mayorista"].forEach(tipo => {
    const tableEl = document.querySelector(`#tabla-${getEndpoint(tipo)} tbody`);
    if (tableEl) {
      tableEl.addEventListener("click", e => {
        const btn = e.target.closest("button[data-id]");
        if (!btn) return;
        const { id, accion } = btn.dataset;
        if (accion === "editar")                 abrirModal("edit", tipo, id);
        if (accion === "eliminar")               eliminar(tipo, id);
        if (accion === "toggle-activo-mayorista") toggleActivoMayorista(id, btn.dataset.activo === "true");
        if (accion === "toggle-activo") toggleActivo(
          id,
          btn.dataset.descripcion || "",
          btn.dataset.abreviatura || "",
          btn.dataset.placas      || "",
          btn.dataset.activo === "true"
        );
      });
      if (tipo === "vehiculo") {
        tableEl.addEventListener("change", e => {
          const sel = e.target.closest("select.chofer-select");
          if (sel) {
            const choferId = sel.selectedOptions[0]?.dataset.choferId || null;
            actualizarChoferVehiculo(sel.dataset.id, sel.value, choferId);
          }
        });
      }
    }
    document.getElementById(`buscar-${tipo}-nombre`).addEventListener("input", () => debounceCarga(tipo));
    document.getElementById(`buscar-${tipo}-fecha`).addEventListener("change", () => cargarDatos(tipo));
  });

  // Mini interfaz: gestión de choferes
  document.getElementById("btn-gestionar-choferes").addEventListener("click", async () => {
    const errEl = document.getElementById("choferes-error");
    errEl.classList.add("hidden");
    errEl.classList.remove("choferes-credenciales");
    document.getElementById("input-nuevo-chofer").value = "";
    await cargarChoferes();
    document.getElementById("modal-choferes-overlay").classList.remove("hidden");
  });
  document.getElementById("btn-cerrar-choferes").addEventListener("click", () => {
    document.getElementById("modal-choferes-overlay").classList.add("hidden");
  });
  document.getElementById("form-nuevo-chofer").addEventListener("submit", agregarChofer);
  document.getElementById("lista-choferes").addEventListener("click", e => {
    const btnAcceso = e.target.closest(".btn-generar-acceso");
    if (btnAcceso) {
      generarAccesoChofer(btnAcceso.dataset.id, btnAcceso.dataset.nombre, btnAcceso);
      return;
    }
    const btn = e.target.closest("button[data-id]");
    if (btn) eliminarChofer(btn.dataset.id);
  });

  // Formulario general
  document.getElementById("form-general").addEventListener("submit", guardarConfigGeneral);
});

// ── Config General ──────────────────────────────────────────────
async function cargarConfigGeneral() {
  try {
    const res = await fetch("/configuracion/");
    // La ruta GET / renderiza HTML, así que buscamos el endpoint de config
    const res2 = await fetch("/configuracion/config-general");
    if (res2.ok) {
      const cfg = await res2.json();
      poblarFormularioGeneral(cfg);
    }
  } catch (err) {
    // Sin configuración previa — usar defaults
    poblarFormularioGeneral({});
  }
}

function poblarFormularioGeneral(cfg) {
  const form = document.getElementById("form-general");
  if (!form) return;

  // Campos simples
  const setVal = (name, val) => {
    const el = form.querySelector(`[name="${name}"]`);
    if (el && val != null) el.value = val;
  };
  setVal("velocidad_kmh",      cfg.velocidad_kmh      ?? 24);
  setVal("min_descarga_por_kg",cfg.min_descarga_por_kg ?? 0.20);
  setVal("utilizacion_min",    cfg.utilizacion_min    ?? 80);
  setVal("utilizacion_max",    cfg.utilizacion_max    ?? 120);

  // Días
  renderDiasConfig(cfg.config_dias || {});
}

function renderDiasConfig(configDias) {
  const cont = document.getElementById("dias-rows");
  if (!cont) return;

  cont.innerHTML = DIAS_SEMANA.map(({ key, label }) => {
    const d = configDias[key] || { habilitado: key !== "domingo", hora_salida: "07:00", hora_limite: "18:00" };
    return `
      <div class="dia-row" id="dia-row-${key}">
        <label class="switch">
          <input type="checkbox" id="dia-chk-${key}" ${d.habilitado ? "checked" : ""}
            onchange="toggleDiaRow('${key}', this.checked)">
          <span class="slider"></span>
        </label>
        <span class="dia-row-label">${label}</span>
        <div class="dia-time-group">
          <span class="dia-time-label">Salida</span>
          <input type="time" id="dia-salida-${key}" value="${d.hora_salida || "07:00"}" ${!d.habilitado ? "disabled" : ""}>
        </div>
        <div class="dia-time-group">
          <span class="dia-time-label">Regreso</span>
          <input type="time" id="dia-limite-${key}" value="${d.hora_limite || "18:00"}" ${!d.habilitado ? "disabled" : ""}>
        </div>
      </div>`;
  }).join("");
}

function toggleDiaRow(key, habilitado) {
  document.getElementById(`dia-salida-${key}`).disabled = !habilitado;
  document.getElementById(`dia-limite-${key}`).disabled = !habilitado;
}

async function guardarConfigGeneral(e) {
  if (e) e.preventDefault();
  const form = document.getElementById("form-general");
  const datos = {
    matriz_lat:          18.87329315661368,
    matriz_lon:          -96.9491574270346,
    velocidad_kmh:       parseFloat(form.querySelector('[name="velocidad_kmh"]')?.value) || 24,
    min_descarga_por_kg: parseFloat(form.querySelector('[name="min_descarga_por_kg"]')?.value) || 0.20,
    utilizacion_min:     parseInt(form.querySelector('[name="utilizacion_min"]')?.value)  || 80,
    utilizacion_max:     parseInt(form.querySelector('[name="utilizacion_max"]')?.value)  || 120,
    config_dias: {},
  };

  DIAS_SEMANA.forEach(({ key }) => {
    datos.config_dias[key] = {
      habilitado:  document.getElementById(`dia-chk-${key}`)?.checked ?? true,
      hora_salida: document.getElementById(`dia-salida-${key}`)?.value || "07:00",
      hora_limite: document.getElementById(`dia-limite-${key}`)?.value || "18:00",
    };
  });

  Loader.show("Guardando Configuración", MSG_CFG.guardar_general);
  try {
    const [r1, r2] = await Promise.all([
      fetch("/configuracion/guardar", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(datos),
      }),
      // Sincronizar config_dias con la logística activa (si hay una).
      // Esto es opcional: si no hay logística activa (r2 → 400), la config
      // global en 'configuracion' sigue siendo la fuente de verdad y será
      // leída correctamente por el módulo de Asignación al cargar.
      fetch("/asignacion/config-dias", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(datos.config_dias),
      }).catch(() => null),  // nunca propaga error de red
    ]);

    if (r1.ok) {
      const btn = form.querySelector('button[type="submit"]');
      const orig = btn.textContent;
      btn.textContent = "✓ Guardado";
      btn.style.background = "#16a34a";
      setTimeout(() => { btn.textContent = orig; btn.style.background = ""; }, 2000);
    } else {
      cfgToast("Error al guardar la configuración.");
    }
  } catch (err) {
    console.error(err);
  } finally {
    Loader.hide();
  }
}

// ── Tabs ─────────────────────────────────────────────────────
const timers = {};
function debounceCarga(tipo) {
  clearTimeout(timers[tipo]);
  timers[tipo] = setTimeout(() => cargarDatos(tipo), 350);
}

function initTabs() {
  const dropdownLi  = document.getElementById("nav-dropdown-productos");
  const dropdownBtn = document.getElementById("btn-nav-dropdown-productos");

  // Toggle del dropdown "Productos"
  if (dropdownLi && dropdownBtn) {
    dropdownBtn.addEventListener("click", e => {
      e.preventDefault();
      const isOpen = dropdownLi.classList.toggle("open");
      dropdownBtn.setAttribute("aria-expanded", isOpen);
    });
    document.addEventListener("click", e => {
      if (!dropdownLi.contains(e.target)) {
        dropdownLi.classList.remove("open");
        dropdownBtn.setAttribute("aria-expanded", "false");
      }
    });
  }

  document.querySelectorAll(".nav-tabs a").forEach(a => {
    a.addEventListener("click", e => {
      e.preventDefault();
      // Cerrar dropdown
      dropdownLi?.classList.remove("open");
      dropdownBtn?.setAttribute("aria-expanded", "false");

      // Quitar activo de todos los items
      document.querySelectorAll(".nav-tabs > li:not(.nav-tab-dropdown)").forEach(li => li.classList.remove("active"));
      if (dropdownLi) {
        dropdownLi.classList.remove("active");
        dropdownLi.querySelectorAll("li").forEach(li => li.classList.remove("active"));
      }
      document.querySelectorAll(".tab-content").forEach(s => s.classList.add("hidden"));

      // Activar el item correcto
      if (dropdownLi?.contains(a)) {
        a.parentElement.classList.add("active");
        dropdownLi.classList.add("active");
      } else {
        a.parentElement.classList.add("active");
      }

      const target = document.getElementById("tab-" + a.dataset.tab);
      if (target) target.classList.remove("hidden");
    });
  });
}

// ── CRUD ─────────────────────────────────────────────────────
async function cargarDatos(tipo) {
  const nombre = document.getElementById(`buscar-${tipo}-nombre`).value;
  const fecha  = document.getElementById(`buscar-${tipo}-fecha`).value;
  const tbody  = document.querySelector(`#tabla-${getEndpoint(tipo)} tbody`);

  try {
    // Se agregó { cache: "no-store" } para evitar que el navegador muestre datos desactualizados
    const res = await fetch(`/configuracion/${getEndpoint(tipo)}?nombre=${encodeURIComponent(nombre)}&fecha=${encodeURIComponent(fecha)}`, { cache: "no-store" });
    const data = await res.json();

    if (!Array.isArray(data) || data.length === 0) {
      // AQUÍ SUMAMOS +1 A LAS COLUMNAS PARA QUE LA TABLA VACÍA NO SE DESCUADRE
      const cols = { producto: 13, producto_proalmex: 7, producto_bimbo: 9, sucursal: 11, vehiculo: 13, cliente_mayorista: 8 };
      tbody.innerHTML = `<tr><td colspan="${cols[tipo]}" style="text-align:center;color:#999">Sin registros</td></tr>`;
      return;
    }

    if (tipo === "producto") {
      tbody.innerHTML = data.map(p => `
        <tr>
          <td>${p.clave_sae ?? ""}</td>
          <td>${h(p.codigo_barras ?? "")}</td>
          <td>${h(p.marca)}</td>
          <td>${h(p.descripcion)}</td>
          <td>${h(p.unidad_medida ?? "")}</td>
          <td>$${Number(p.costo || 0).toFixed(2)}</td>
          <td>${p.peso ?? ""} kg</td>
          <td>${p.largo ?? ""}</td>
          <td>${p.ancho ?? ""}</td>
          <td>${p.alto ?? ""}</td>
          <td>${p.volumen != null && p.volumen !== 0 ? Number(p.volumen).toFixed(6) : "—"}</td>
          <td>${p.ultima_modificacion ?? "-"}</td>
          <td>
            <button class="btn btn-sm btn-warning" data-id="${p._id}" data-accion="editar">Editar</button>
            <button class="btn btn-sm btn-danger"  data-id="${p._id}" data-accion="eliminar">Eliminar</button>
          </td>
        </tr>`).join("");
    } else if (tipo === "producto_proalmex") {
      tbody.innerHTML = data.map(p => `
        <tr>
          <td>${h(p.marca)}</td>
          <td>${h(p.linea)}</td>
          <td>${h(p.tamano)}</td>
          <td>$${Number(p.costo || 0).toFixed(2)}</td>
          <td>${p.peso != null ? p.peso + " kg" : ""}</td>
          <td>${p.ultima_modificacion ?? "-"}</td>
          <td>
            <button class="btn btn-sm btn-warning" data-id="${p._id}" data-accion="editar">Editar</button>
            <button class="btn btn-sm btn-danger"  data-id="${p._id}" data-accion="eliminar">Eliminar</button>
          </td>
        </tr>`).join("");
    } else if (tipo === "producto_bimbo") {
      tbody.innerHTML = data.map(p => `
        <tr>
          <td>${h(p.codigo_barra)}</td>
          <td>${h(p.descripcion)}</td>
          <td>${p.peso != null ? p.peso + " kg" : ""}</td>
          <td>${p.altura ?? ""}</td>
          <td>${p.ancho  ?? ""}</td>
          <td>${p.largo  ?? ""}</td>
          <td>${p.volumen != null && p.volumen !== 0 ? Number(p.volumen).toFixed(6) : "—"}</td>
          <td>${p.ultima_modificacion ?? "-"}</td>
          <td>
            <button class="btn btn-sm btn-warning" data-id="${p._id}" data-accion="editar">Editar</button>
            <button class="btn btn-sm btn-danger"  data-id="${p._id}" data-accion="eliminar">Eliminar</button>
          </td>
        </tr>`).join("");
    } else if (tipo === "sucursal") {
      tbody.innerHTML = data.map(s => `
        <tr>
          <td>${s.num_tienda ?? ""}</td>
          <td>${h(s.estado)}</td>
          <td>${s.num_tienda && s.nombre_base ? `[${s.num_tienda}] ${h(s.nombre_base)}` : h(s.nombre_base)}</td>
          <td>${h(s["nombre_icg-proalmex"])}</td>
          <td>${h(s.nombre_bimbo)}</td>
          <td>${s.latitud ?? ""}</td>
          <td>${s.longitud ?? ""}</td>
          <td>${h(s.hora_inicio)}</td>
          <td>${h(s.hora_fin)}</td>
          <td>${s.ultima_modificacion ?? "-"}</td>
          <td>
            <button class="btn btn-sm btn-warning" data-id="${s._id}" data-accion="editar">Editar</button>
            <button class="btn btn-sm btn-danger"  data-id="${s._id}" data-accion="eliminar">Eliminar</button>
          </td>
        </tr>`).join("");
    } else if (tipo === "vehiculo") {
      tbody.innerHTML = data.map(v => {
        const activo = v.activo !== false;
        return `
        <tr>
          <td>${h(v.descripcion)}</td>
          <td>${h(v.abreviatura)}</td>
          <td>${h(v.placas)}</td>
          <td>
            <select class="chofer-select" data-id="${v._id}">
              ${_opcionesChofer(v.chofer || "")}
            </select>
          </td>
          <td>${v.largo_volumetria ?? ""}</td>
          <td>${v.ancho_volumetria ?? ""}</td>
          <td>${v.alto_volumetria ?? ""}</td>
          <td>${v.capacidad_toneladas ?? ""} ton</td>
          <td>${h(v.categoria)}</td>
          <td>${v.volumen_m3 != null && v.volumen_m3 !== 0 ? Number(v.volumen_m3).toFixed(4) : "—"}</td>
          <td>${v.ultima_modificacion ?? "-"}</td>
          <td>
            <button class="btn btn-sm ${activo ? "btn-success" : "btn-secondary"}"
                    data-id="${v._id}"
                    data-accion="toggle-activo"
                    data-descripcion="${ha(v.descripcion ?? "")}"
                    data-abreviatura="${ha(v.abreviatura ?? "")}"
                    data-placas="${ha(v.placas ?? "")}"
                    data-activo="${activo}">
              ${activo ? "Activo" : "Inactivo"}
            </button>
          </td>
          <td>
            <button class="btn btn-sm btn-warning" data-id="${v._id}" data-accion="editar">Editar</button>
            <button class="btn btn-sm btn-danger"  data-id="${v._id}" data-accion="eliminar">Eliminar</button>
          </td>
        </tr>`}).join("");
    } else if (tipo === "cliente_mayorista") {
      tbody.innerHTML = data.map(c => {
        const activo = c.activo !== false;
        return `
        <tr>
          <td>${c.id_cliente ?? ""}</td>
          <td>${h(c.nombre)}</td>
          <td>${h(c.poblacion)}</td>
          <td>${c.latitud  ?? "—"}</td>
          <td>${c.longitud ?? "—"}</td>
          <td>${c.ultima_modificacion ?? "-"}</td>
          <td>
            <button class="btn btn-sm ${activo ? "btn-success" : "btn-secondary"}"
                    data-id="${c._id}"
                    data-accion="toggle-activo-mayorista"
                    data-activo="${activo}">
              ${activo ? "Incluido" : "Excluido"}
            </button>
          </td>
          <td>
            <button class="btn btn-sm btn-warning" data-id="${c._id}" data-accion="editar">Editar</button>
            <button class="btn btn-sm btn-danger"  data-id="${c._id}" data-accion="eliminar">Eliminar</button>
          </td>
        </tr>`}).join("");
    }

    _reaplicarOrdenSiActivo(`#tabla-${getEndpoint(tipo)}`);
  } catch (err) {
    console.error(`[cargarDatos:${tipo}]`, err);
    // Asegúrate de cambiar el colspan aquí también si ajustas las columnas
    tbody.innerHTML = `<tr><td colspan="16" style="text-align:center;color:#ef4444">Error de conexión.</td></tr>`;
  }
}

async function abrirModal(mode, tipo, docId = null) {
  modalMode = mode; modalTipo = tipo; modalDocId = docId;
  const campos = tipo === "producto"          ? CAMPOS_PRODUCTO_ICG
               : tipo === "producto_proalmex" ? CAMPOS_PRODUCTO_PROALMEX
               : tipo === "producto_bimbo"    ? CAMPOS_PRODUCTO_BIMBO
               : tipo === "sucursal"          ? CAMPOS_SUCURSAL
               : tipo === "cliente_mayorista" ? CAMPOS_CLIENTE_MAYORISTA
               : CAMPOS_VEHICULO;
  document.getElementById("modal-title").textContent = (mode === "create" ? "Nuevo " : "Editar ") + tipo;

  let existente = {};
  if (mode === "edit" && docId) {
    const res = await fetch(`/configuracion/${getEndpoint(tipo)}/${docId}`);
    existente = await res.json();
  }

  if (tipo === "vehiculo") await cargarChoferes();

  document.getElementById("modal-form").innerHTML = campos.map(c => {
    if (c.type === "select" && c.key === "chofer") {
      return `
      <div class="form-group">
        <label>${h(c.label)}</label>
        <select name="${c.key}" class="form-control" id="modal-field-${c.key}">
          ${_opcionesChofer(existente[c.key] || "")}
        </select>
      </div>`;
    }
    if (c.type === "select" && c.key === "unidad_medida") {
      return `
      <div class="form-group">
        <label>${h(c.label)}</label>
        <select name="${c.key}" class="form-control" id="modal-field-${c.key}" onchange="_onUnidadChange(this)">
          <option value="">— Seleccionar —</option>
          ${_opcionesUnidad(existente[c.key] || "")}
          <option value="__nueva__">+ Agregar nueva unidad…</option>
        </select>
      </div>`;
    }
    return `
    <div class="form-group">
      <label>${h(c.label)}${c.readonly ? ' <span class="config-computed-badge">auto</span>' : ''}</label>
      <input name="${c.key}" type="${c.type}" value="${existente[c.key] != null ? ha(String(existente[c.key])) : ""}" class="form-control${c.readonly ? ' config-field-readonly' : ''}" id="modal-field-${c.key}"${c.readonly ? ' readonly tabindex="-1"' : ''}${c.step != null ? ` step="${c.step}"` : ''}${c.min != null ? ` min="${c.min}"` : ''}>
    </div>`;
  }).join("");

  if (tipo === "producto") {
    ['largo', 'ancho', 'alto'].forEach(k => {
      const el = document.getElementById(`modal-field-${k}`);
      if (el) el.addEventListener('input', _actualizarVolumenModal);
    });
    _actualizarVolumenModal();
  }

  if (tipo === "producto_bimbo") {
    ['largo', 'ancho', 'altura'].forEach(k => {
      const el = document.getElementById(`modal-field-${k}`);
      if (el) el.addEventListener('input', _actualizarVolumenBimboModal);
    });
    _actualizarVolumenBimboModal();
  }

  if (tipo === "vehiculo") {
    ['largo_volumetria', 'ancho_volumetria', 'alto_volumetria'].forEach(k => {
      const el = document.getElementById(`modal-field-${k}`);
      if (el) el.addEventListener('input', _actualizarVolumenVehiculoModal);
    });
    _actualizarVolumenVehiculoModal();
  }

  document.getElementById("modal-overlay").classList.remove("hidden");
}

function _actualizarVolumenModal() {
  const largo = parseFloat(document.getElementById('modal-field-largo')?.value) || 0;
  const ancho = parseFloat(document.getElementById('modal-field-ancho')?.value) || 0;
  const alto  = parseFloat(document.getElementById('modal-field-alto')?.value)  || 0;
  const volEl = document.getElementById('modal-field-volumen');
  if (volEl) volEl.value = ((largo * ancho * alto) / 1_000_000).toFixed(6);
}

function _actualizarVolumenBimboModal() {
  const largo  = parseFloat(document.getElementById('modal-field-largo')?.value)  || 0;
  const ancho  = parseFloat(document.getElementById('modal-field-ancho')?.value)  || 0;
  const altura = parseFloat(document.getElementById('modal-field-altura')?.value) || 0;
  const volEl  = document.getElementById('modal-field-volumen');
  if (volEl) volEl.value = ((largo * ancho * altura) / 1_000_000).toFixed(6);
}

function _actualizarVolumenVehiculoModal() {
  const largo = parseFloat(document.getElementById('modal-field-largo_volumetria')?.value) || 0;
  const ancho = parseFloat(document.getElementById('modal-field-ancho_volumetria')?.value) || 0;
  const alto  = parseFloat(document.getElementById('modal-field-alto_volumetria')?.value)  || 0;
  const volEl = document.getElementById('modal-field-volumen_m3');
  if (volEl) volEl.value = (largo * ancho * alto).toFixed(6);
}

function cerrarModal() {
  document.getElementById("modal-overlay").classList.add("hidden");
  const errEl = document.getElementById("modal-error");
  if (errEl) errEl.classList.add("hidden");
}

async function guardarModal(e) {
    // 1. Evitamos que el botón recargue la vista y aborte el fetch
    if (e) e.preventDefault(); 

    const campos = modalTipo === "producto"          ? CAMPOS_PRODUCTO_ICG
               : modalTipo === "producto_proalmex" ? CAMPOS_PRODUCTO_PROALMEX
               : modalTipo === "producto_bimbo"    ? CAMPOS_PRODUCTO_BIMBO
               : modalTipo === "sucursal"          ? CAMPOS_SUCURSAL
               : modalTipo === "cliente_mayorista" ? CAMPOS_CLIENTE_MAYORISTA
               : CAMPOS_VEHICULO;

    const payload = {};
    campos.forEach(({ key, type }) => {
        const el = document.getElementById(`modal-field-${key}`);
        if (!el) return;
        const raw = el.value.trim();

        if (raw === "" || raw === "__nueva__") {
            // Campo vacío → null (permite múltiples nulos en MongoDB)
            payload[key] = null;
        } else if (type === "number") {
            const num = Number(raw);
            payload[key] = isNaN(num) ? null : num;
        } else {
            payload[key] = raw;
        }

        if (key === "chofer" && type === "select") {
            payload.chofer_id = el.selectedOptions[0]?.dataset.choferId || null;
        }
    });

    const endpoint = getEndpoint(modalTipo);
    const url = modalMode === "create"
        ? `/configuracion/${endpoint}`
        : `/configuracion/${endpoint}/${modalDocId}`;
    const method = modalMode === "create" ? "POST" : "PUT";

    const label  = _TIPO_LABEL[modalTipo] || modalTipo;
    const titulo = modalMode === "create" ? `Creando ${label}` : `Guardando ${label}`;
    const msgs   = modalMode === "create" ? MSG_CFG.crear : MSG_CFG.editar;
    Loader.show(titulo, msgs);

    try {
        const res = await fetch(url, {
            method,
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });

        const data = await res.json().catch(() => null);

        if (!res.ok || data?.status === "error") {
            mostrarModalError(data?.mensaje || `Error del servidor: ${res.statusText}`);
            return;
        }

        cerrarModal();
        cargarDatos(modalTipo);
        if (modalTipo === "vehiculo") {
            localStorage.setItem("icg_flota_actualizada", Date.now().toString());
        }
    } catch (error) {
        console.error("Error al guardar:", error);
        mostrarModalError("Ocurrió un error de red o de servidor al guardar.");
    } finally {
        Loader.hide();
    }
}


async function toggleActivoMayorista(docId, activo) {
  try {
    const res = await fetch(`/configuracion/clientes-mayoristas/${docId}/activo`, { method: "PUT" });
    if (res.ok) cargarDatos("cliente_mayorista");
    else cfgToast("Error al cambiar el estado del mayorista.");
  } catch (err) {
    console.error("[toggleActivoMayorista]", err);
    cfgToast("Error de conexión al cambiar estado.");
  }
}

async function toggleActivo(docId, descripcion, abreviatura, placas, activo) {
  const confirmed = await mostrarConfirmEstado(descripcion, abreviatura, placas, activo);
  if (!confirmed) return;
  try {
    const res = await fetch(`/configuracion/vehiculos/${docId}/activo`, { method: "PUT" });
    if (res.ok) {
      cargarDatos("vehiculo");
      localStorage.setItem("icg_flota_actualizada", Date.now().toString());
    }
  } catch (err) {
    console.error("[toggleActivo]", err);
  }
}

async function eliminar(tipo, docId) {
  const label = _TIPO_LABEL[tipo] || tipo;
  const confirmed = await mostrarConfirm(
    `Eliminar ${label}`,
    "Este registro se eliminará de forma permanente y no podrá recuperarse.",
    "Eliminar"
  );
  if (!confirmed) return;
  Loader.show(`Eliminando ${label}`, MSG_CFG.eliminar);
  try {
    const res = await fetch(`/configuracion/${getEndpoint(tipo)}/${docId}`, { method: "DELETE" });
    if (res.ok) {
      cargarDatos(tipo);
      if (tipo === "vehiculo") localStorage.setItem("icg_flota_actualizada", Date.now().toString());
    }
  } finally {
    Loader.hide();
  }
}

/** Ordena un array de <tr> según una columna; mult=1 ascendente, mult=-1 descendente. */
function _ordenarFilas(rows, colIndex, isNum, mult) {
  rows.sort((a, b) => {
    let va = a.cells[colIndex]?.innerText.trim() || "";
    let vb = b.cells[colIndex]?.innerText.trim() || "";
    if (isNum) {
      va = parseFloat(va.replace(/[^0-9.-]+/g, "")) || 0;
      vb = parseFloat(vb.replace(/[^0-9.-]+/g, "")) || 0;
    } else { va = va.toLowerCase(); vb = vb.toLowerCase(); }
    return va < vb ? -mult : va > vb ? mult : 0;
  });
  return rows;
}

function initSortableTables() {
  document.querySelectorAll("th.sortable").forEach(th => {
    th.addEventListener("click", () => {
      const table = th.closest("table");
      const tbody = table.querySelector("tbody");
      const rows  = Array.from(tbody.rows);
      const colIndex = Array.from(th.parentNode.children).indexOf(th);
      const isNum  = th.classList.contains("col-num");
      const wasAsc = th.classList.contains("asc");

      table.querySelectorAll("th").forEach(t => t.classList.remove("asc", "desc"));
      th.classList.add(wasAsc ? "desc" : "asc");
      const mult = wasAsc ? -1 : 1;

      tbody.append(..._ordenarFilas(rows, colIndex, isNum, mult));
    });
  });
}

/**
 * Reaplica el orden activo (columna + dirección) de una tabla tras recargar
 * sus datos vía AJAX. El <thead> no se reemplaza al refrescar, así que la
 * clase "asc"/"desc" del <th> sobrevive; solo hace falta reordenar las
 * filas nuevas para que el filtro seleccionado por el usuario no se pierda.
 */
function _reaplicarOrdenSiActivo(tableSelector) {
  const table = document.querySelector(tableSelector);
  if (!table) return;
  const th = table.querySelector("th.asc, th.desc");
  if (!th) return;

  const tbody = table.querySelector("tbody");
  const rows  = Array.from(tbody.rows);
  // No reordenar el placeholder de "Sin registros" / "Error de conexión"
  if (rows.length === 0 || (rows.length === 1 && rows[0].cells.length === 1)) return;

  const colIndex = Array.from(th.parentNode.children).indexOf(th);
  const isNum    = th.classList.contains("col-num");
  const mult     = th.classList.contains("asc") ? 1 : -1;

  tbody.append(..._ordenarFilas(rows, colIndex, isNum, mult));
}

function h(s)  { return String(s ?? "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;"); }
function ha(s) { return String(s ?? "").replace(/"/g,"&quot;").replace(/'/g,"&#39;"); }

// ═══════════════════════════════════════════════════════════════
// TAB — RUTAS HISTÓRICAS
// ═══════════════════════════════════════════════════════════════

(function initHistorico() {
  const zone      = document.getElementById("hist-upload-zone");
  const fileInput  = document.getElementById("hist-file-input");
  const preview   = document.getElementById("hist-upload-preview");
  const fileName  = document.getElementById("hist-file-name");
  const msg       = document.getElementById("hist-msg");
  const lista     = document.getElementById("hist-lista");
  const spinner   = document.getElementById("hist-spinner");
  const empty     = document.getElementById("hist-empty");
  const badge     = document.getElementById("hist-resumen-badge");
  if (!zone) return;

  let _selectedFile = null;
  let _todosLosDocs = [];     // copia del último fetch para re-filtrar sin ir al servidor

  // ── Activar al seleccionar el tab ────────────────────────────
  document.getElementById("config-tabs").addEventListener("click", e => {
    const a = e.target.closest("a");
    if (a && a.dataset.tab === "rutas-historicas") {
      cargarHistoricos();
      cargarResumen();
    }
  });

  // ── Drag & Drop ──────────────────────────────────────────────
  zone.addEventListener("click", () => fileInput.click());
  zone.addEventListener("dragover",  e => { e.preventDefault(); zone.classList.add("drag-over"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("drag-over"));
  zone.addEventListener("drop", e => {
    e.preventDefault();
    zone.classList.remove("drag-over");
    const f = e.dataTransfer.files[0];
    if (f) seleccionarArchivo(f);
  });

  document.getElementById("btn-hist-seleccionar").addEventListener("click", e => {
    e.stopPropagation();
    fileInput.click();
  });
  fileInput.addEventListener("change", () => {
    if (fileInput.files[0]) seleccionarArchivo(fileInput.files[0]);
  });

  document.getElementById("btn-hist-cancelar").addEventListener("click", () => {
    _selectedFile = null;
    preview.style.display = "none";
    zone.style.display    = "";
    fileInput.value       = "";
    ocultarMsg();
  });

  document.getElementById("btn-hist-cargar").addEventListener("click", subirArchivo);

  function seleccionarArchivo(f) {
    if (!f.name.endsWith(".csv")) {
      mostrarMsg("Solo se aceptan archivos CSV.", "error");
      return;
    }
    _selectedFile        = f;
    fileName.textContent = f.name;
    zone.style.display   = "none";
    preview.style.display = "";
    ocultarMsg();
  }

  async function subirArchivo() {
    if (!_selectedFile) return;
    const fd = new FormData();
    fd.append("archivo", _selectedFile);
    fd.append("nombre",  _selectedFile.name.replace(/\.csv$/i, ""));

    document.getElementById("btn-hist-cargar").disabled    = true;
    document.getElementById("btn-hist-cargar").textContent = "Cargando…";
    ocultarMsg();

    try {
      const res  = await fetch("/configuracion/rutas-historicas/cargar", { method: "POST", body: fd });
      const data = await res.json();
      if (data.status === "ok") {
        mostrarMsg(`✓ Historial cargado: ${data.n_sucursales} sucursales · ${data.n_rutas} rutas`, "ok");
        _selectedFile         = null;
        preview.style.display = "none";
        zone.style.display    = "";
        fileInput.value       = "";
        await cargarHistoricos();
        await cargarResumen();
      } else {
        mostrarMsg(data.mensaje || "Error al cargar", "error");
      }
    } catch (e) {
      mostrarMsg("Error de conexión: " + e.message, "error");
    } finally {
      const btnCargar = document.getElementById("btn-hist-cargar");
      btnCargar.disabled  = false;
      btnCargar.innerHTML = '<i data-lucide="upload"></i> Cargar historial';
      lucide.createIcons();
    }
  }

  const _DIA_LABEL = {
    LUNES: "Lunes", MARTES: "Martes", MIERCOLES: "Miércoles", MIÉRCOLES: "Miércoles",
    JUEVES: "Jueves", VIERNES: "Viernes", SABADO: "Sábado", SÁBADO: "Sábado", DOMINGO: "Domingo",
  };

  // ── Cargar lista de historiales ──────────────────────────────
  async function cargarHistoricos() {
    spinner.style.display = "";
    lista.innerHTML       = "";
    empty.style.display   = "none";
    try {
      const res  = await fetch("/configuracion/rutas-historicas");
      const docs = await res.json();
      _todosLosDocs         = docs;
      spinner.style.display = "none";

      if (!docs.length) {
        empty.style.display = "";
        return;
      }

      docs.forEach((doc, i) => {
        const peso  = i + 1;
        const fecha = doc.cargado_en ? doc.cargado_en.slice(0, 16).replace("T", " ") : "—";
        const item  = document.createElement("div");
        item.className       = "hist-item";
        item.dataset.dias    = (doc.dias || []).join(",");
        const isConf         = doc.confirmada;

        // Chips de días del historial
        const diasChips = (doc.dias || []).map(d =>
          `<span class="hist-dia-chip">${_DIA_LABEL[d] || d}</span>`
        ).join("");

        item.innerHTML = `
          <div class="hist-item-num ${isConf ? "confirmada" : ""}">${peso}</div>
          <div class="hist-item-info">
            <div class="hist-item-nombre">${h(doc.nombre)}</div>
            <div class="hist-item-meta">
              ${doc.n_sucursales ?? "?"} suc${doc.n_mayoristas ? ` · ${doc.n_mayoristas} may` : ""} · ${doc.n_rutas ?? "?"} rutas · ${fecha}
            </div>
            ${diasChips ? `<div class="hist-item-dias">${diasChips}</div>` : ""}
          </div>
          ${isConf ? '<span class="hist-item-confirmada-tag">✓ confirmada</span>' : ""}
          <span class="hist-item-peso-tag">Peso: ${peso}</span>
          <button class="btn-hist-mapa" data-id="${doc._id}" data-nombre="${ha(doc.nombre)}" title="Ver rutas en el mapa">
            <i data-lucide="map-pin"></i> Ver mapa
          </button>
          <button class="btn-hist-eliminar" data-id="${doc._id}" title="Eliminar historial">✕ Eliminar</button>
        `;
        lista.appendChild(item);
      });

      lista.querySelectorAll(".btn-hist-mapa").forEach(btn => {
        btn.addEventListener("click", () => {
          abrirMapaHistorico(btn.dataset.id, btn.dataset.nombre, null);
          lucide.createIcons();
        });
      });

      lista.querySelectorAll(".btn-hist-eliminar").forEach(btn => {
        btn.addEventListener("click", async () => {
          const confirmed = await mostrarConfirm(
            "Eliminar historial",
            "Este historial se eliminará de forma permanente y no podrá recuperarse.",
            "Eliminar"
          );
          if (!confirmed) return;
          const id   = btn.dataset.id;
          const res2 = await fetch(`/configuracion/rutas-historicas/${id}`, { method: "DELETE" });
          const d    = await res2.json();
          if (d.status === "ok") {
            await cargarHistoricos();
            await cargarResumen();
          } else {
            cfgToast("Error al eliminar: " + (d.mensaje || "?"));
          }
        });
      });

      lucide.createIcons();

    } catch (e) {
      spinner.style.display = "none";
      empty.style.display   = "";
      console.error("[hist] error cargando lista:", e);
    }
  }

  async function cargarResumen() {
    try {
      const res  = await fetch("/configuracion/rutas-historicas/resumen");
      const data = await res.json();
      if (badge) {
        const n = data.n_ejemplos || 0;
        badge.textContent = n
          ? `${n} ejemplo${n > 1 ? "s" : ""} · ${data.n_sucursales} suc · ${data.n_rutas} rutas`
          : "Sin historiales";
      }
    } catch { /* silencioso */ }
  }

  function mostrarMsg(txt, tipo) {
    msg.textContent   = txt;
    msg.className     = `hist-msg ${tipo}`;
    msg.style.display = "";
  }
  function ocultarMsg() {
    msg.style.display = "none";
  }
})();

// ── Mapa de Rutas Históricas ──────────────────────────────────────────────────

const _HIST_COLORES = [
  '#2563eb', '#16a34a', '#dc2626', '#f59e0b', '#7c3aed',
  '#0891b2', '#be185d', '#84cc16', '#ea580c', '#0d9488',
];

let _histMap      = null;
let _histSource   = null; // EventSource activo
let _histLayers   = {};   // rutaId → { polyline: L.Polyline|null, markers: L.Layer[] }
let _histRutas    = [];
let _histActive   = null; // null = todas; string = ruta aislada
let _histDia      = null; // null = todos los días; string = día filtrado en el mapa
let _histTotal    = 0;
let _histBounds   = [];   // acumulación de coords para fitBounds

const _DIA_LABEL_MAP = {
  LUNES: "Lunes", MARTES: "Martes", MIERCOLES: "Miércoles", MIÉRCOLES: "Miércoles",
  JUEVES: "Jueves", VIERNES: "Viernes", SABADO: "Sábado", SÁBADO: "Sábado", DOMINGO: "Domingo",
};

// ── Icono de depósito (cuadrado oscuro, visible sobre cualquier fondo) ──
function _depotIcon() {
  return L.divIcon({
    html: `<div style="
      width:14px;height:14px;
      background:#0f2744;
      border:2.5px solid #fff;
      border-radius:3px;
      box-shadow:0 0 0 1.5px rgba(0,0,0,.35),0 2px 6px rgba(0,0,0,.25)
    "></div>`,
    iconSize:   [14, 14],
    iconAnchor: [7, 7],
    className:  "",
  });
}

// ── Funciones de barra de progreso ────────────────────────────────────────────

function _mostrarProgreso(show) {
  const el = document.getElementById("hist-mapa-progreso");
  if (el) el.style.display = show ? "" : "none";
}

function _actualizarProgreso(idx, total, fromCache, etaMs, rutaNombre) {
  const pct     = total > 0 ? Math.round((idx / total) * 100) : 0;
  const fillEl  = document.getElementById("hist-prog-fill");
  const pctEl   = document.getElementById("hist-prog-pct");
  const labelEl = document.getElementById("hist-prog-label");
  const metaEl  = document.getElementById("hist-prog-meta");
  if (!fillEl) return;

  fillEl.style.width = `${pct}%`;
  fillEl.classList.toggle("cached", !!fromCache);
  pctEl.textContent  = `${pct}%`;
  labelEl.textContent = rutaNombre
    ? `Ruta ${idx} de ${total}: ${rutaNombre}`
    : `Calculando ${idx} de ${total} rutas…`;

  let meta = fromCache ? "⚡ Desde caché" : "🌐 Consultando OSRM…";
  if (etaMs != null && etaMs > 0) {
    const segs = Math.ceil(etaMs / 1000);
    meta += ` · ~${segs} s restante${segs !== 1 ? "s" : ""}`;
  }
  metaEl.textContent = meta;
}

// ── Apertura del modal ────────────────────────────────────────────────────────

function abrirMapaHistorico(histId, histNombre, diaInicial = null) {
  const overlay = document.getElementById("hist-mapa-overlay");
  if (!overlay) return;

  // Cerrar SSE previo si existe
  if (_histSource) { _histSource.close(); _histSource = null; }

  // Resetear estado
  _histLayers = {};
  _histRutas  = [];
  _histActive = null;
  _histDia    = diaInicial;
  _histTotal  = 0;
  _histBounds = [];

  overlay.classList.remove("hidden");
  document.body.style.overflow = "hidden";

  document.getElementById("hist-mapa-titulo").textContent = `Historial: ${histNombre}`;
  document.getElementById("hist-mapa-sidebar-titulo").textContent = "Rutas";
  document.getElementById("hist-mapa-sidebar-list").innerHTML = "";
  document.getElementById("btn-hist-mapa-todas").classList.toggle("active", _histDia === null);
  const _diaFiltrosEl = document.getElementById("hist-dia-filtros");
  if (_diaFiltrosEl) { _diaFiltrosEl.innerHTML = ""; _diaFiltrosEl.classList.add("hidden"); }

  // Inicializar mapa Leaflet
  const mapEl = document.getElementById("hist-mapa-map");
  if (_histMap) { _histMap.remove(); _histMap = null; }
  _histMap = L.map(mapEl, { zoomControl: true });
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "© <a href='https://www.openstreetmap.org/copyright'>OpenStreetMap</a>",
    maxZoom: 19,
  }).addTo(_histMap);

  // Mostrar barra de progreso
  _mostrarProgreso(true);
  _actualizarProgreso(0, 1, false, null, null);

  // Abrir SSE
  _histSource = new EventSource(
    `/configuracion/rutas-historicas/${encodeURIComponent(histId)}/geometrias-stream`
  );

  _histSource.onmessage = function(e) {
    let ev;
    try { ev = JSON.parse(e.data); } catch { return; }

    if (ev.type === "start") {
      _histTotal = ev.total;
      document.getElementById("hist-mapa-sidebar-titulo").textContent =
        `Rutas (${ev.total})`;
      _actualizarProgreso(0, ev.total, false, null, null);

    } else if (ev.type === "ruta") {
      _histRutas.push(ev.ruta);
      _agregarCapaHistRuta(ev.ruta, _histRutas.length - 1);
      _actualizarProgreso(ev.idx, ev.total, ev.from_cache, ev.eta_ms, ev.ruta.nombre);

    } else if (ev.type === "done") {
      _histSource.close();
      _histSource = null;
      _mostrarProgreso(false);
      _construirFiltroDiaMapa();
      if (_histDia) {
        _actualizarVisibilidadHistMapa();
      } else if (_histBounds.length && _histMap) {
        _histMap.fitBounds(_histBounds, { padding: [40, 40] });
      }
      if (!_histRutas.length) {
        document.getElementById("hist-mapa-sidebar-list").innerHTML =
          '<p style="padding:12px;color:var(--gris-texto);font-size:var(--text-sm)">Sin rutas disponibles.</p>';
      }

    } else if (ev.type === "error") {
      _histSource.close();
      _histSource = null;
      _mostrarProgreso(false);
      document.getElementById("hist-mapa-sidebar-list").innerHTML =
        `<p style="padding:12px;color:var(--rojo);font-size:var(--text-sm)">Error: ${h(ev.mensaje)}</p>`;
    }
  };

  _histSource.onerror = function() {
    if (_histSource) { _histSource.close(); _histSource = null; }
    _mostrarProgreso(false);
  };

  document.getElementById("btn-hist-mapa-cerrar").onclick = cerrarMapaHistorico;
}

// ── Añadir una ruta al mapa y al sidebar (incremental) ───────────────────────

function _agregarCapaHistRuta(ruta, idx) {
  if (!_histMap) return;
  const color = _HIST_COLORES[idx % _HIST_COLORES.length];

  // Polilínea (ruta completa: depósito → paradas → depósito)
  let polyLayer = null;
  if (ruta.polyline?.length > 1) {
    polyLayer = L.polyline(ruta.polyline, {
      color, weight: 4.5, opacity: 0.85,
    }).addTo(_histMap);
    ruta.polyline.forEach(pt => _histBounds.push(pt));
  }

  // Marcadores
  const markers = [];
  (ruta.paradas || []).forEach(p => {
    if (p.lat == null || p.lon == null) return;
    const isDepot   = p.tipo === "depot";
    const isMay     = p.tipo === "mayorista";
    let m;
    if (isDepot) {
      m = L.marker([p.lat, p.lon], { icon: _depotIcon(), zIndexOffset: 1000 })
           .addTo(_histMap);
      m.bindPopup(`<b>🏭 Depósito</b><br>Punto de salida y regreso`);
    } else if (isMay) {
      m = L.circleMarker([p.lat, p.lon], {
        radius: 7, fillColor: "#f59e0b", color: "#fff",
        weight: 2.5, fillOpacity: 0.95,
      }).addTo(_histMap);
      m.bindPopup(
        `<b>#${p.orden} ${h(p.nombre)}</b><br>Mayorista${p.kg_entrega ? `<br>${p.kg_entrega} kg` : ""}`
      );
      _histBounds.push([p.lat, p.lon]);
    } else {
      m = L.circleMarker([p.lat, p.lon], {
        radius: 6, fillColor: color, color: "#fff",
        weight: 2, fillOpacity: 0.95,
      }).addTo(_histMap);
      m.bindPopup(
        `<b>#${p.orden} ${h(p.nombre)}</b>${p.kg_entrega ? `<br>${p.kg_entrega} kg` : ""}`
      );
      _histBounds.push([p.lat, p.lon]);
    }
    markers.push(m);
  });

  _histLayers[ruta.id] = { polyline: polyLayer, markers, color };

  // Item en el sidebar
  const sidebarList = document.getElementById("hist-mapa-sidebar-list");
  const item = document.createElement("div");
  item.className      = "hist-mapa-ruta-item";
  item.dataset.rutaId = ruta.id;
  item.dataset.dia    = (ruta.dia || "").toUpperCase();
  // Aplicar filtro de día si ya hay uno activo
  if (_histDia && item.dataset.dia !== _histDia) item.style.display = "none";
  const paradasSinDepot = (ruta.paradas || []).filter(p => p.tipo !== "depot");
  const nSucs = paradasSinDepot.filter(p => p.tipo !== "mayorista").length;
  const nMays = paradasSinDepot.filter(p => p.tipo === "mayorista").length;
  const metaParadas = nSucs
    ? `${nSucs} parada${nSucs !== 1 ? "s" : ""}${nMays ? ` · ${nMays} may.` : ""}`
    : `${nMays} mayorista${nMays !== 1 ? "s" : ""}`;
  item.innerHTML = `
    <div class="hist-mapa-ruta-dot" style="background:${color}"></div>
    <div class="hist-mapa-ruta-info">
      <div class="hist-mapa-ruta-nombre">${h(ruta.nombre)}</div>
      <div class="hist-mapa-ruta-meta">${ruta.distancia_km} km · ${Math.round(ruta.duracion_min)} min · ${metaParadas}</div>
    </div>
  `;
  item.addEventListener("click", () => _seleccionarHistRuta(ruta.id));
  sidebarList.appendChild(item);
}

// ── Filtro por día en el mapa ─────────────────────────────────────────────────

function _construirFiltroDiaMapa() {
  const contenedor = document.getElementById("hist-dia-filtros");
  if (!contenedor) return;

  // Recopilar días únicos de las rutas cargadas
  const diasSet = new Set();
  _histRutas.forEach(r => { if (r.dia) diasSet.add(r.dia.toUpperCase()); });

  if (!diasSet.size) { contenedor.classList.add("hidden"); return; }

  const _DIA_ORDEN = ["LUNES","MARTES","MIERCOLES","MIÉRCOLES","JUEVES","VIERNES","SABADO","SÁBADO","DOMINGO"];
  const diasOrden  = [...diasSet].sort((a, b) => _DIA_ORDEN.indexOf(a) - _DIA_ORDEN.indexOf(b));

  contenedor.innerHTML = "";
  contenedor.classList.remove("hidden");

  // Botón "Todos" en el filtro del mapa (sincronizado con btn-hist-mapa-todas)
  const btnTodos = document.createElement("button");
  btnTodos.className   = "hist-dia-btn" + (_histDia === null ? " active" : "");
  btnTodos.textContent = "Todos";
  btnTodos.addEventListener("click", () => {
    _histDia    = null;
    _histActive = null;
    _sincronizarBtnTodas(true);
    _refrescarFiltroDiaMapa();
    _actualizarVisibilidadHistMapa();
  });
  contenedor.appendChild(btnTodos);

  diasOrden.forEach(dia => {
    const btn = document.createElement("button");
    btn.className   = "hist-dia-btn" + (_histDia === dia ? " active" : "");
    btn.textContent = _DIA_LABEL_MAP[dia] || dia;
    btn.dataset.dia = dia;
    btn.addEventListener("click", () => {
      _histDia    = dia;
      _histActive = null;
      _sincronizarBtnTodas(false);
      _refrescarFiltroDiaMapa();
      _actualizarVisibilidadHistMapa();
    });
    contenedor.appendChild(btn);
  });
}

function _refrescarFiltroDiaMapa() {
  const contenedor = document.getElementById("hist-dia-filtros");
  if (!contenedor) return;
  contenedor.querySelectorAll(".hist-dia-btn").forEach(btn => {
    const esTodos = !btn.dataset.dia;
    btn.classList.toggle("active", esTodos ? _histDia === null : btn.dataset.dia === _histDia);
  });
  // Actualizar visibilidad de items en el sidebar
  document.querySelectorAll(".hist-mapa-ruta-item").forEach(el => {
    const dia = (el.dataset.dia || "").toUpperCase();
    el.style.display = (!_histDia || dia === _histDia) ? "" : "none";
  });
}

function _sincronizarBtnTodas(activo) {
  const btn = document.getElementById("btn-hist-mapa-todas");
  if (btn) btn.classList.toggle("active", activo);
}

// ── Selección de ruta en el sidebar ──────────────────────────────────────────

function _seleccionarHistRuta(rutaId) {
  const esMisma = _histActive === rutaId;
  _histActive   = esMisma ? null : rutaId;

  document.querySelectorAll(".hist-mapa-ruta-item").forEach(el => {
    el.classList.toggle("active", !esMisma && el.dataset.rutaId === rutaId);
  });

  _sincronizarBtnTodas(_histActive === null && _histDia === null);
  _actualizarVisibilidadHistMapa();
}

function verTodasHistRutas() {
  _histActive = null;
  _histDia    = null;
  document.querySelectorAll(".hist-mapa-ruta-item").forEach(el => {
    el.classList.remove("active");
    el.style.display = "";
  });
  _sincronizarBtnTodas(true);
  _refrescarFiltroDiaMapa();
  _actualizarVisibilidadHistMapa();
}

function _actualizarVisibilidadHistMapa() {
  if (!_histMap) return;
  const bounds = [];

  _histRutas.forEach(ruta => {
    const layer = _histLayers[ruta.id];
    if (!layer) return;
    const pasaDia    = !_histDia || (ruta.dia || "").toUpperCase() === _histDia;
    const pasaRuta   = _histActive === null || _histActive === ruta.id;
    const visible    = pasaDia && pasaRuta;

    if (layer.polyline) {
      if (visible) {
        if (!_histMap.hasLayer(layer.polyline)) layer.polyline.addTo(_histMap);
        if (ruta.polyline) ruta.polyline.forEach(pt => bounds.push(pt));
      } else {
        if (_histMap.hasLayer(layer.polyline)) _histMap.removeLayer(layer.polyline);
      }
    }
    layer.markers.forEach(m => {
      if (visible) { if (!_histMap.hasLayer(m)) m.addTo(_histMap); }
      else         { if ( _histMap.hasLayer(m)) _histMap.removeLayer(m); }
    });
  });

  if (bounds.length) _histMap.fitBounds(bounds, { padding: [40, 40] });
}

// ── Cierre del modal ──────────────────────────────────────────────────────────

function cerrarMapaHistorico() {
  if (_histSource) { _histSource.close(); _histSource = null; }
  const overlay = document.getElementById("hist-mapa-overlay");
  if (overlay) overlay.classList.add("hidden");
  document.body.style.overflow = "";
  if (_histMap) { _histMap.remove(); _histMap = null; }
  _mostrarProgreso(false);
  _histLayers = {};
  _histRutas  = [];
  _histActive = null;
  _histDia    = null;
  _histTotal  = 0;
  _histBounds = [];
}