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
| 0 — Configuración | `router/configuracion_router.py` | Productos (catálogos ICG, Proalmex, Bimbo, Pymsa, Viña Real), sucursales, vehículos, choferes, clientes mayoristas, rutas históricas |
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

### ⚠️ Corpus canónico de rutas históricas — no se reescribe

Las **9 semanas** de `febrero a junio 2026` (archivos `_HT.xls`) son datos de
ORIGEN: el plan que la empresa entregó, y la única referencia contra la que se
puede medir el motor. Están listadas en `config.SEMANAS_CANONICAS`, por fecha
de inicio.

**El riesgo es real y ya ocurrió.** Al guardar en el módulo de Modificación, el
front dispara `POST /modificacion/guardar-historico`
([static/js/modificacion.js](static/js/modificacion.js)) y
`guardar_en_historico` hace **UPSERT**: abrir una semana vieja y guardar
reemplaza su histórico. El 2026-08-03 pasó con la semana del 18-22 de mayo.

Protecciones vigentes:

- `guardar_en_historico` **rechaza** el guardado si la logística es una semana
  canónica y devuelve `409` con un mensaje legible. El endpoint HTTP **no
  expone** el flag que lo levanta: sólo el uso programático
  (`permitir_canon=True`) puede sobrescribir el corpus.
- El front ya no se traga el error: lo muestra en un toast.
- **Ningún script de `scripts/` llama a `guardar_en_historico` ni a
  `guardar_modificacion`.** El pipeline de la plantilla es de sólo lectura sobre
  `rutas_historicas`. Verificado por `grep`; si alguna vez se agrega uno, tiene
  que pasar `permitir_canon` explícito y decir por qué.
- `rutas_historicas` tiene auditoría de escritura (`escrito_en`, `escrito_por`,
  `origen`), agregada por `scripts/migrar_auditoria_historico.py`. Las filas
  anteriores quedan en NULL, que significa "escrita antes de que existiera la
  auditoría".

**`cargado_en` NO es marca de escritura.** Se fabrica como
`f"{fecha_inicio}T00:00:00"`, o sea la fecha de INICIO de la semana. Para saber
cuándo se escribió una fila, usar `escrito_en`.

**Dos capas con distinta procedencia.** En `rutas_historicas`, la capa de
SUCURSALES es el plan del planeador (verificado estructuralmente contra los
`_HT.xls`), pero la capa de MAYOREO viene de la distribución del sistema, no de
la hoja. Para cualquier medición de mayoreo la fuente son los 9 Excel, no la BD.
Además, las paradas operativas de 0 kg (recolecciones, aeropuerto) nunca llegan
a la BD: el front las descarta con `if (!suc.num_tienda) continue`, así que el
tiempo de ruta del histórico está subestimado en todas las semanas.

**Plantilla canónica (ConVRP):** `crear_plantilla_canonica.py` crea las tablas
`plantilla_*` (versionadas, no destructivas) y `cargar_plantilla.py` la carga o
recarga desde el Excel canónico + `datos/mapeo_no_a_numtienda.csv`; cada corrida
crea una versión nueva sin borrar la anterior. Lógica y lectores en
`logic/plantilla_canonica.py`. `smoke_convrp.py` valida el motor ConVRP contra
las 9 semanas reales (viajes vs históricos, rígidos partidos, determinismo,
verificación de llaves, utilización máxima con mayoristas y por qué vía se
resolvió cada parada de mayoreo) — correrlo tras tocar el builder, el enganche
o la plantilla.

`calibrar_unidad_ref.py` recalcula `unidad_ref` como asignación global por día
(afinidad histórica + capacidad + nº de viajes que hace la operación ese día) y
escribe `datos/unidad_ref_por_grupo.csv`, que el cargador aplica sobre la unidad
del Excel. `recalcular_zonas.py` **debe correrse después de cada carga**: el
Excel no trae el grupo núcleo ni la confianza de las zonas, y sin ellos el
enganche de mayoristas cae entero a la geografía.

**Consolidación de mayoristas:** `logic/consolidacion_mayoristas.py` agrupa los
DOCUMENTOS de una semana en PARADAS antes de rutear. Sin este paso el VRP rutea
folio por folio y parte una entrega en varios viajes (los 4 folios de un mismo
domicilio acabaron en tres días distintos). La llave es la proximidad (500 m),
no el nombre ni la población: los casos reales agrupan razones sociales distintas
en un mismo local y escriben la población de dos formas. La parada resultante es
**carga indivisible**; si no cabe en la unidad se parte a propósito por folios
completos y la división queda registrada para imprimirla.

**Mayoristas por zona:** `logic/enganche_zona.py` resuelve a qué zona pertenece
cada cliente (historia → geografía → fallback) y a qué ruta se engancha (núcleo →
segundo grupo → geografía de ruta → viaje de mayoristas solo).
`convrp_integracion.construir_rutas_con_mayoristas()` cierra el circuito: la
carga de mayoristas se ancla a una sucursal del grupo destino y **entra a las
restricciones del motor**, así que el sobrecupo que provoca dispara las palancas
(unidad → día → partir → consolidar solitarias → rellenar capacidad libre) en
vez de aparecer al pintar el PDF. El enganche y el
reparto son mutuamente dependientes, así que iteran a punto fijo con tope de
pasadas; al final `reubicar_mayoristas_por_cupo()` garantiza que ninguna ruta
quede por encima de su capacidad.

**Orden fijo de paradas:** `logic/orden_fijo_paradas.py` permite fijar, por
regla nombrada (tabla `orden_fijo_paradas`, cargada vía
`scripts/cargar_orden_fijo.py` desde `datos/orden_fijo_paradas.csv`), un
orden de visita explícito para un conjunto de sucursales. Cuando TODA una
ruta generada coincide con una regla, ese orden gana sobre el histórico y la
geografía en `ordenar_paradas_por_historico()`; si la ruta mezcla sucursales
ajenas a la regla, no se aplica ningún pin.

El motor ConVRP vive tras el interruptor `CONVRP_ACTIVO` de
`logic/historico_logic.py`, **apagado por omisión**: con el flag en `False` el
comportamiento es idéntico al motor de afinidad actual.
`scripts/pdf_convrp_preview.py` genera el PDF de una semana con el motor
ConVRP + mayoristas **sin persistir nada** (vista previa para el planeador).

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
