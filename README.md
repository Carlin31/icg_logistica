# ICG Logística — app_9.1.0

Sistema web interno de ICG Logística para planificar, asignar y dar seguimiento a rutas
de reparto (entregas a sucursales y clientes mayoristas), con un portal dedicado para
conductores.

## Índice

- [Descripción general](#descripción-general)
- [Roles y flujo de trabajo](#roles-y-flujo-de-trabajo)
- [Módulos del sistema](#módulos-del-sistema)
- [Stack tecnológico](#stack-tecnológico)
- [Estructura del proyecto](#estructura-del-proyecto)
- [Variables de entorno](#variables-de-entorno)
- [Puesta en marcha local](#puesta-en-marcha-local)
- [Despliegue](#despliegue)
- [Scripts de mantenimiento](#scripts-de-mantenimiento)
- [Pruebas](#pruebas)
- [Documentación adicional](#documentación-adicional)

## Descripción general

La app organiza el trabajo alrededor de **"logísticas"**: perfiles semanales/periódicos
(por ejemplo, un proveedor o cliente como Bimbo o Proalmex) que agrupan todo el ciclo de
una operación de reparto, desde la carga del pedido hasta la entrega en punto de venta:

1. **Extracción** de pedidos desde archivos Excel de distintos proveedores.
2. **Asignación** de sucursales/mayoristas a vehículos y rutas, con optimización VRP.
3. **Modificación manual** de rutas cuando el ajuste automático no es suficiente.
4. **Generación de PDF** de reparto y **autorización** de las rutas hacia los conductores.
5. **Seguimiento en tiempo real** del avance de cada ruta.
6. **Portal del conductor** para marcar entregas (y cancelarlas/reautorizarlas) desde el
   celular o una vista de escritorio.

Todo el histórico de acciones sensibles (autorizar, cancelar, reautorizar) queda
registrado en una colección de auditoría consultable desde el panel de administración.

## Roles y flujo de trabajo

- **Logística**: usuario de oficina que crea la logística de la semana, ejecuta las
  secciones 0 a 6 (configuración → extracción → asignación → modificación → PDF →
  seguimiento) y autoriza las rutas para que los conductores puedan verlas.
- **Conductor**: solo ve rutas explícitamente autorizadas y solo puede operar dentro de
  `/conductor/*`. No tiene acceso al panel de Logística ni viceversa (la separación de
  roles se valida en cada request, en [app.py](app.py)).
- **Administrador**: además de operar como Logística, tiene acceso exclusivo a
  `/usuarios/*` (gestión de usuarios del sistema).

## Módulos del sistema

| Sección | Blueprint | Responsabilidad |
|---|---|---|
| Auth | `router/auth_router.py` | Selección de tipo de acceso, login/logout |
| Menú principal | `router/menu_router.py` | Alta/baja de logísticas, activar/completar |
| 0 — Configuración | `router/configuracion_router.py` | Productos, sucursales, vehículos, choferes, clientes mayoristas, rutas históricas |
| 1 — Extracción | `router/extraccion_router.py` | Lee Excel por proveedor (ICG/Bimbo/Proalmex) y clientes mayoristas; calcula peso y volumen |
| 3 — Asignación | `router/asignacion_router.py` | Asigna sucursales/mayoristas a vehículos y rutas; VRP híbrido; geometría OSRM; nombres de ruta vía LLM (Groq) |
| 6 — Modificación | `router/modificacion_router.py` | Edición manual de rutas ya asignadas |
| PDF | `router/pdf_router.py` | Genera el reporte de reparto y autoriza/retira autorización hacia conductores |
| Seguimiento | `router/seguimiento_router.py` | Avance en tiempo real por ruta/parada + historial de auditoría (solo tras autorizar) |
| Conductor | `router/conductor_router.py` | Portal del chofer: ver rutas propias, marcar/cancelar entregas, historial |
| Usuarios | `router/usuarios_router.py` | Gestión de usuarios (solo administradores) |

La lógica de negocio de cada sección vive en `logic/`, con nombres espejo
(`asignacion_logic.py`, `conductor_logic.py`, etc.). Las rutas Flask (`router/`) no
acceden a MongoDB directamente: siempre delegan en su módulo de `logic/` correspondiente.

## Stack tecnológico

- **Backend**: Flask 3 (Blueprints), Gunicorn en producción.
- **Base de datos**: MongoDB (PyMongo).
- **Frontend**: Jinja2 (SSR) + CSS/JS propio por módulo (sin framework JS) + Leaflet para mapas (asignación, configuración, modificación y portal del conductor).
- **Optimización de rutas**: algoritmo VRP propio (`logic/vrp_logic.py`,
  `logic/vrp_afinidad/`) + [OSRM](http://project-osrm.org/) para geometría de rutas.
- **IA generativa**: [Groq API](https://groq.com/) para nombrar rutas automáticamente
  (con fallback local si no hay API key configurada).
- **Reportes**: ReportLab (PDF), openpyxl/pandas (Excel).
- **Autenticación**: bcrypt (hash + salt) con bloqueo temporal tras intentos fallidos.

## Estructura del proyecto

```
app.py                  Punto de entrada Flask (create_app, blueprints, middlewares)
config.py               Configuración y validación de variables de entorno
db.py                   Conexión MongoDB por request

router/                 Blueprints Flask (una vista por sección)
logic/                  Lógica de negocio (una por sección) + submódulos:
  logic_extraccion/        Cálculo de peso/volumen, lectura de mayoristas
  vrp_afinidad/            Algoritmo VRP de afinidad histórica

templates/              Vistas Jinja2, una carpeta por módulo + base.html
static/                 CSS/JS por módulo

scripts/                Scripts de mantenimiento y migración de datos (uso manual)
tests/                  Pruebas automatizadas (VRP, integración)
testing/                Excel y utilidades de comparación/depuración manual
docs/                   Documentación complementaria (auditorías, notas técnicas)
```

## Variables de entorno

Copiar `.env.example` a `.env` y completar:

| Variable | Descripción |
|---|---|
| `MONGO_URI` | Cadena de conexión a MongoDB Atlas (o local) |
| `MONGO_DB_NAME` | Nombre de la base de datos |
| `SECRET_KEY` | Clave secreta de Flask (sesiones/cookies) |
| `OSRM_HOST` | Instancia OSRM (pública o propia) para geometría de rutas |
| `GROQ_API_KEY` | (Opcional, no está en `.env.example`) API key de Groq para nombrar rutas con LLM |

`Config.validar()` corta el arranque si falta `MONGO_URI` o `MONGO_DB_NAME`, y advierte si
la app corre en producción con la `SECRET_KEY` de desarrollo.

## Puesta en marcha local

```bash
pip install -r requirements.txt
cp .env.example .env               # completar valores
python scripts/crear_usuarios_iniciales.py   # solo la primera vez, si la colección "usuarios" está vacía
python app.py                       # http://localhost:5000
```

## Despliegue

Producción corre en Render con Gunicorn:

```bash
gunicorn app:app
```

`FLASK_DEBUG` no debe definirse en producción (queda `False` por defecto).

## Scripts de mantenimiento

En `scripts/` hay utilidades de uso manual (no se ejecutan automáticamente):
importar catálogos desde CSV (productos, sucursales, vehículos, clientes mayoristas,
rutas históricas), limpiar caché de OSRM, arreglar índices de MongoDB, crear los
usuarios iniciales del sistema, y generar reportes de comparación Excel ↔ MongoDB.

## Pruebas

```bash
pytest tests/
```

Cubren principalmente el algoritmo VRP (afinidad, Clarke-Wright, aprendizaje histórico)
y pruebas de integración de la asignación de rutas.

## Documentación adicional

- [docs/auditoria-ux.md](docs/auditoria-ux.md) — Auditoría UX/Accesibilidad (WCAG 2.1 AA)
  de la arquitectura CSS/plantillas.

---

> **Nota de mantenimiento:** este README se actualiza junto con cada cambio relevante en
> el sistema (nuevos módulos, endpoints, variables de entorno, dependencias). Ver
> instrucción correspondiente en `CLAUDE.md`.
