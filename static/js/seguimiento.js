// ===== Sección — Seguimiento de Entregas =====

const DIA_LABEL_SEG = {
  lunes: "Lunes", martes: "Martes", miercoles: "Miércoles",
  jueves: "Jueves", viernes: "Viernes", sabado: "Sábado", domingo: "Domingo",
};
const ESTADO_LABEL_SEG = { sin_iniciar: "Sin iniciar", en_curso: "En curso", completada: "Completada" };
const ACCION_LABEL_SEG = {
  autorizar_rutas:       "Rutas autorizadas",
  cancelar_autorizacion: "Autorización cancelada",
  cancelar_entrega:      "Entrega cancelada",
  reautorizar_entrega:   "Entrega re-autorizada",
};
const ACCION_CLASE_SEG = {
  autorizar_rutas:       "seg-accion--autorizar",
  cancelar_autorizacion: "seg-accion--cancelar",
  cancelar_entrega:      "seg-accion--cancelar-ent",
  reautorizar_entrega:   "seg-accion--reautorizar",
};

const ACCION_ICON_SEG = {
  autorizar_rutas:       "check-circle-2",
  cancelar_autorizacion: "x-circle",
  cancelar_entrega:      "ban",
  reautorizar_entrega:   "refresh-cw",
};

// Limpia registros en formato antiguo que contienen IDs crudos de MongoDB
function limpiarDetalle(detalle) {
  if (!detalle) return "";
  // Formato antiguo: "Parada X (tipo) acción en ruta manual_hexid"
  if (detalle.includes(" en ruta ")) {
    return detalle
      .replace(/\s+en ruta\s+\S+/gi, "")
      .replace(/^Parada\s+(\S+)\s+\(sucursal\)\s*/i, "Sucursal $1 — ")
      .replace(/^Parada\s+(\S+)\s+\(mayorista\)\s*/i, "Mayorista $1 — ");
  }
  return detalle;
}

let _segRutas          = [];
let _rutaDetalleActiva = null;
const SEG_REFRESH_MS   = 8000;
let _segRefreshTimer   = null;

document.addEventListener("DOMContentLoaded", () => {
  cargarSeguimiento();
  document.getElementById("seg-buscar")?.addEventListener("input", renderSeguimiento);
  document.getElementById("seg-filtro-dia")?.addEventListener("change", renderSeguimiento);
  document.getElementById("seg-filtro-estado")?.addEventListener("change", renderSeguimiento);
  document.getElementById("seg-detalle-cerrar")?.addEventListener("click", cerrarDetalleParadas);
  document.querySelectorAll(".seg-tab").forEach(btn =>
    btn.addEventListener("click", () => cambiarTab(btn.dataset.tab))
  );

  iniciarActualizacionAutomatica();
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      cargarSeguimiento();
      iniciarActualizacionAutomatica();
    } else {
      clearInterval(_segRefreshTimer);
    }
  });
});

function iniciarActualizacionAutomatica() {
  clearInterval(_segRefreshTimer);
  _segRefreshTimer = setInterval(() => {
    if (document.visibilityState === "visible") cargarSeguimiento();
  }, SEG_REFRESH_MS);
}

async function cargarSeguimiento() {
  const sinAutorizar  = document.getElementById("seg-sin-autorizar");
  const tabsContainer = document.getElementById("seg-tabs-container");

  try {
    const res  = await fetch("/seguimiento/datos", { cache: "no-store" });
    const data = await res.json();

    if (data.status !== "ok" || !data.autorizado) {
      sinAutorizar.style.display  = "";
      tabsContainer.style.display = "none";
      return;
    }

    sinAutorizar.style.display  = "none";
    tabsContainer.style.display = "";
    _segRutas = data.rutas || [];
    renderSeguimiento();
    actualizarMarcaTiempo();
    cargarAuditoria();
  } catch (err) {
    console.error("[cargarSeguimiento]", err);
    sinAutorizar.style.display  = "";
    tabsContainer.style.display = "none";
  }
}

function actualizarMarcaTiempo() {
  const el = document.getElementById("seg-actualizado");
  if (!el) return;
  const ahora = new Date().toLocaleTimeString("es-MX", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  el.textContent = `Actualizado ${ahora}`;
}

function renderSeguimiento() {
  const tbody  = document.querySelector("#tabla-seguimiento tbody");
  const q      = (document.getElementById("seg-buscar")?.value || "").toLowerCase().trim();
  const diaSel = document.getElementById("seg-filtro-dia")?.value || "";
  const estSel = document.getElementById("seg-filtro-estado")?.value || "";

  const filtradas = _segRutas.filter(r => {
    if (diaSel && r.dia !== diaSel) return false;
    if (estSel && r.estado !== estSel) return false;
    if (q) {
      const texto = `${r.nombre || ""} ${r.chofer || ""} ${r.vehiculo_abrev || ""} ${r.vehiculo_placas || ""}`.toLowerCase();
      if (!texto.includes(q)) return false;
    }
    return true;
  });

  if (filtradas.length === 0) {
    tbody.innerHTML = `<tr><td colspan="9" style="text-align:center;color:#999">Sin resultados para este filtro.</td></tr>`;
    return;
  }

  tbody.innerHTML = filtradas.map(r => {
    const canceladasLabel = r.canceladas > 0
      ? ` <span class="seg-canceladas-label">${r.canceladas}✗</span>` : "";
    const esActivo = _rutaDetalleActiva === r.id;
    return `
      <tr class="${esActivo ? "seg-fila--activa" : ""}">
        <td>${DIA_LABEL_SEG[r.dia] || r.dia || "—"}</td>
        <td>${h(r.nombre || "—")}</td>
        <td>${h(r.chofer || "Sin asignar")}</td>
        <td>${h(r.vehiculo_abrev || "—")}</td>
        <td><span class="seg-badge seg-badge--${r.estado}">${ESTADO_LABEL_SEG[r.estado] || r.estado}</span></td>
        <td>
          <span class="seg-pct-track"><span class="seg-pct-fill" style="width:${r.pct_avance || 0}%"></span></span>
          ${r.entregadas}/${r.total_paradas} (${r.pct_avance || 0}%)${canceladasLabel}
        </td>
        <td>${fmtHora(r.hora_inicio_real)}</td>
        <td>${fmtHora(r.hora_fin_real)}</td>
        <td>
          <button class="seg-btn-detalle ${esActivo ? "seg-btn-detalle--activo" : ""}"
                  data-ruta-id="${h(r.id)}" data-ruta-nombre="${h(r.nombre || '')}"
                  title="${esActivo ? "Cerrar detalle" : "Ver detalle de paradas"}">
            <i data-lucide="${esActivo ? 'chevron-right' : 'list-checks'}"></i>
          </button>
        </td>
      </tr>`;
  }).join("");

  lucide.createIcons();
  tbody.querySelectorAll(".seg-btn-detalle").forEach(btn => {
    btn.addEventListener("click", () => {
      const rid    = btn.dataset.rutaId;
      const rnombre = btn.dataset.rutaNombre;
      if (_rutaDetalleActiva === rid) {
        cerrarDetalleParadas();
      } else {
        abrirDetalleParadas(rid, rnombre);
      }
    });
  });
}

// ── Detalle de paradas (panel derecho) ────────────────────────────
async function abrirDetalleParadas(rutaId, rutaNombre) {
  _rutaDetalleActiva = rutaId;
  renderSeguimiento();

  const body   = document.getElementById("seg-detalle-body");
  const vacio  = document.getElementById("seg-detalle-vacio");
  const cerrar = document.getElementById("seg-detalle-cerrar");

  document.getElementById("seg-detalle-ruta-nombre").textContent = rutaNombre || "Detalle de paradas";
  vacio.style.display  = "none";
  cerrar.style.display = "";
  body.innerHTML = `<div class="seg-spinner">Cargando paradas…</div>`;
  body.style.display   = "flex";

  try {
    const res  = await fetch(`/seguimiento/detalle/${encodeURIComponent(rutaId)}`, { cache: "no-store" });
    const data = await res.json();

    if (data.status !== "ok" || !data.paradas || data.paradas.length === 0) {
      body.innerHTML = `<p class="seg-sin-datos">Sin registros de entrega para esta ruta todavía.</p>`;
      lucide.createIcons();
      return;
    }

    body.innerHTML = data.paradas.map(p => {
      const estadoClass = p.estado === "entregado" ? "seg-parada--entregada"
                        : p.estado === "cancelada"  ? "seg-parada--cancelada"
                        : "";
      const estadoIcon  = p.estado === "entregado" ? "check-circle-2"
                        : p.estado === "cancelada"  ? "ban"
                        : "circle";
      const historialHTML = (p.historial || []).map(ev => `
        <div class="seg-historial-ev seg-historial-ev--${ev.estado}">
          <i data-lucide="${ev.estado === "entregado" ? "package-check" : "x-circle"}"></i>
          <span class="seg-historial-ev-estado">${ev.estado === "entregado" ? "Entregado" : "Cancelado"}</span>
          <span class="seg-historial-ev-fecha">${fmtHora(ev.fecha)}</span>
          <span class="seg-historial-ev-usuario">${h(ev.usuario_nombre || "")}</span>
          ${ev.observacion ? `<span class="seg-historial-ev-obs">"${h(ev.observacion)}"</span>` : ""}
        </div>`).join("");

      return `
        <div class="seg-parada-item ${estadoClass}">
          <div class="seg-parada-item-header">
            <i data-lucide="${estadoIcon}" class="seg-parada-icon"></i>
            <span class="seg-parada-key">${h(p.nombre || p.parada_key)}</span>
            <span class="seg-badge seg-badge--${p.estado || "sin_iniciar"}" style="font-size:.6rem;padding:1px 6px">
              ${p.estado === "entregado" ? "Entregado" : p.estado === "cancelada" ? "Cancelada" : "Pendiente"}
            </span>
          </div>
          ${historialHTML
            ? `<div class="seg-historial-lista">${historialHTML}</div>`
            : `<p class="seg-sin-datos" style="font-size:.68rem;margin:.2rem 0 0">Sin historial aún.</p>`}
        </div>`;
    }).join("");

    lucide.createIcons();
  } catch (err) {
    console.error("[abrirDetalleParadas]", err);
    body.innerHTML = `<p class="seg-sin-datos">Error al cargar el detalle.</p>`;
  }
}

function cerrarDetalleParadas() {
  _rutaDetalleActiva = null;
  const body   = document.getElementById("seg-detalle-body");
  const vacio  = document.getElementById("seg-detalle-vacio");
  const cerrar = document.getElementById("seg-detalle-cerrar");
  body.style.display   = "none";
  body.innerHTML       = "";
  vacio.style.display  = "";
  cerrar.style.display = "none";
  document.getElementById("seg-detalle-ruta-nombre").textContent = "Detalle de paradas";
  renderSeguimiento();
}

// ── Pestañas ───────────────────────────────────────────────────────
function cambiarTab(tabId) {
  document.querySelectorAll(".seg-tab").forEach(btn => {
    const activa = btn.dataset.tab === tabId;
    btn.classList.toggle("seg-tab--activa", activa);
    btn.setAttribute("aria-selected", String(activa));
  });
  document.querySelectorAll(".seg-tab-panel").forEach(panel => {
    panel.style.display = panel.id === `seg-panel-${tabId}` ? "" : "none";
  });
  if (tabId === "historial") cargarAuditoria();
}

// ── Historial de autorizaciones ────────────────────────────────────
async function cargarAuditoria() {
  try {
    const res  = await fetch("/seguimiento/auditoria", { cache: "no-store" });
    const data = await res.json();
    if (data.status !== "ok") return;

    const lista  = document.getElementById("seg-auditoria-lista");
    const total  = document.getElementById("seg-hist-total");
    const tsEl   = document.getElementById("seg-hist-actualizado");

    if (!data.eventos || !data.eventos.length) {
      lista.innerHTML = `<p class="seg-sin-datos">Sin eventos registrados.</p>`;
      if (total) total.textContent = "";
      return;
    }

    if (total)  total.textContent  = `${data.eventos.length} evento${data.eventos.length !== 1 ? "s" : ""}`;
    if (tsEl) {
      const ahora = new Date().toLocaleTimeString("es-MX", { hour: "2-digit", minute: "2-digit" });
      tsEl.textContent = `Actualizado ${ahora}`;
    }

    lista.innerHTML = data.eventos.map(ev => {
      const claseAccion   = ACCION_CLASE_SEG[ev.accion] || "";
      const labelAccion   = ACCION_LABEL_SEG[ev.accion] || ev.accion;
      const icon          = ACCION_ICON_SEG[ev.accion]  || "info";
      const detalleLimpio = limpiarDetalle(ev.detalle || "");
      return `
        <div class="seg-audit-ev">
          <div class="seg-audit-ev-icono seg-audit-ev-icono--${ev.accion}">
            <i data-lucide="${icon}"></i>
          </div>
          <div class="seg-audit-ev-contenido">
            <div class="seg-audit-ev-top">
              <span class="seg-accion-badge ${claseAccion}">${h(labelAccion)}</span>
              <time class="seg-audit-ev-fecha">${fmtHora(ev.fecha)}</time>
            </div>
            <span class="seg-audit-ev-usuario">${h(ev.usuario_nombre || "—")}</span>
            ${detalleLimpio ? `<p class="seg-audit-ev-detalle">${h(detalleLimpio)}</p>` : ""}
          </div>
        </div>`;
    }).join("");
    lucide.createIcons();
  } catch (err) {
    console.error("[cargarAuditoria]", err);
  }
}

// ── Utilidades ─────────────────────────────────────────────────────
function h(s) { return String(s ?? "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;"); }

function fmtHora(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d) ? "—" : d.toLocaleString("es-MX", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}
