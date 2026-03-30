# Registro de Decisiones Arquitectónicas (ADR) - RIB Dashboard

Este documento registra las decisiones técnicas y arquitectónicas estratégicas adoptadas para la evolución del prototipo de la aplicación **RIB Dashboard** hacia un entorno productivo institucional.

---

## ADR 001: Capa de Abstracción de Datos (Patrón Repository)

*   **Estado:** Tentativo / Propuesto
*   **Contexto:** Actualmente, la lógica de acceso a datos (`queries.py`) utiliza SQL crudo embebido en funciones, lo que acopla estrechamente la lógica de aplicación con la persistencia.
*   **Decisión:** Se implementará una Capa de Abstracción basada en el patrón **Repository**. Toda interacción con la base de datos se realizará mediante objetos de acceso a datos (DAOs) que definan interfaces claras.
*   **Consecuencias:**
    *   **Positivas:** Mayor facilidad para realizar pruebas unitarias (*mocking*), desacoplamiento de la base de datos y mejora en la legibilidad del código.
    *   **Negativas:** Incremento inicial en el volumen de código y complejidad estructural.

---

## ADR 002: Gestión de Sesiones y Persistencia en el Lado del Servidor

*   **Estado:** Tentativo / Propuesto
*   **Contexto:** La aplicación utiliza sesiones de Flask basadas en *cookies* firmadas del lado del cliente, lo cual no es óptimo para la seguridad y auditoría en entornos estatales.
*   **Decisión:** Se migrará a un esquema de **Sesiones en el Lado del Servidor (Server-Side Sessions)**. Se evaluará el uso de **Redis** o una tabla específica en PostgreSQL para el almacenamiento de estados de sesión activos.
*   **Consecuencias:**
    *   **Positivas:** Capacidad de invalidar sesiones de forma centralizada y mayor seguridad ante ataques de red.
    *   **Negativas:** Requiere un componente adicional de infraestructura (ej. instancia de Redis).

---

## ADR 003: Control de Acceso Granular (RBAC) y Datos Nominales

*   **Estado:** Tentativo / Propuesto
*   **Contexto:** El acceso a datos nominales (CUIL, nombres, etc.) es crítico. El sistema actual solo diferencia entre "admin" y "analista" de forma binaria.
*   **Decisión:** Implementar un sistema de **Control de Acceso Basado en Roles (RBAC)** con una granularidad que permita autorizar el acceso a campos específicos. Cada acceso a datos nominales generará un registro de auditoría (*Audit Log*) obligatorio en una tabla inmutable.
*   **Consecuencias:**
    *   **Positivas:** Cumplimiento estricto con normativas de protección de datos personales.
    *   **Negativas:** Sobrecarga mínima en consultas de lectura para validar permisos.

---

## ADR 004: Comunicación en Tiempo Real mediante WebSockets

*   **Estado:** Tentativo / Propuesto
*   **Contexto:** Se requiere monitoreo en tiempo real y persistencia de estado de sesión activa.
*   **Decisión:** Integrar **WebSockets** (vía Flask-SocketIO) para gestionar la comunicación bidireccional. Este canal se utilizará para notificaciones de sistema, actualizaciones de ingesta y validación de estado de sesión.
*   **Consecuencias:**
    *   **Positivas:** Experiencia de usuario más dinámica y respuesta inmediata ante eventos del sistema.
    *   **Negativas:** Mayor complejidad en la gestión de conexiones y requerimiento de balanceadores de carga consistentes (*sticky sessions*).

---

## ADR 005: Transformación a Arquitectura Modular

*   **Estado:** Tentativo / Propuesto
*   **Contexto:** La aplicación actual es un monolito que dificulta la escalabilidad horizontal y el mantenimiento individual de componentes (ej. el módulo de ingesta no debería competir por recursos con la visualización).
*   **Decisión:** Reestructurar el proyecto en módulos lógicos independientes (*Backend API*, *ETL Services*, *Frontend*). Se priorizará el despliegue mediante contenedores (Docker).
*   **Consecuencias:**
    *   **Positivas:** Despliegue independiente, mayor resiliencia ante fallos sectorizados.
    *   **Negativas:** Mayor complejidad en la orquestación del entorno de producción.

---

## ADR 006: Patrón Check-Effects-Interactions (CEI)

*   **Estado:** Tentativo / Propuesto
*   **Contexto:** Se requiere garantizar la integridad de las transacciones y la seguridad en el acceso a datos sensibles, evitando efectos colaterales inesperados o ataques de reentrada de lógica.
*   **Decisión:** Se adoptará de forma transversal el patrón **Check-Effects-Interactions (CEI)** en todos los servicios de backend:
    1.  **Checks:** Validar pre-condiciones, autorizaciones (RBAC) y parámetros de entrada *antes* de realizar cualquier cambio.
    2.  **Effects:** Actualizar el estado interno de la aplicación y la base de datos (ej. registro en Audit Log).
    3.  **Interactions:** Realizar llamadas externas o retornar la respuesta al usuario final *solo después* de que los efectos hayan sido confirmados.
*   **Consecuencias:**
    *   **Positivas:** Mejora sustancial en la robustez transaccional y mayor facilidad para auditar fallos.
    *   **Negativas:** Requiere una disciplina estricta de desarrollo y puede aumentar levemente la verbosidad de los controladores/servidores.

---

## ADR 007: Desacoplamiento de las Capas de ETL y Presentación

*   **Estado:** Tentativo / Propuesto
*   **Contexto:** Actualmente, los scripts de ingesta (`ingest.py`) y gestión de vistas (`matviews.py`) residen en el mismo repositorio y entorno que la aplicación web, aunque son procesos de administración independientes.
*   **Decisión:** Se formalizará la separación total de la **Capa de Ingesta/ETL** de la **Capa de Presentación (Dashboard)**.
    1.  La aplicación Dashboard será estrictamente de **solo lectura** (salvo gestión de usuarios y auditoría).
    2.  Los procesos de ingesta no estarán accesibles ni serán disparables desde la interfaz de usuario (GUI).
    3.  Se prohibirá el uso de submódulos de ingesta dentro del servidor de aplicaciones Flask.
*   **Consecuencias:**
    *   **Positivas:** Reducción drástica de la superficie de ataque, eliminación de riesgos de ejecución accidental de truncates y mejor gestión de recursos.
    *   **Negativas:** Requiere procesos de despliegue y monitoreo separados para ambos componentes.


## ADR 008: Separación Física de DB de Gestión y DWH
**Estado: Aceptado**
*   **Contexto:** La seguridad de los datos nominales (PII) es crítica y el manejo de sesiones en cookies es insuficiente para los objetivos del proyecto.
*   **Decisión:** Dividir el almacenamiento en dos bases de datos PostgreSQL independientes:
    1. **db_mgmt**: Operacional, para gestión de usuarios y caché de sesiones (Server-side). Acceso RW desde la app.
    2. **db_rib_dash**: Data Warehouse (DWH) con esquemas `nominal` y `anonimizado`. Acceso controlado mediante roles diferenciados (RO/RW) por usuario de aplicación.
*   **Consecuencia:** Aislamiento físico de PII, mayor seguridad en sesiones y facilidad de mantenimiento/escalabilidad.

---

## ADR 009: Modularización Simétrica de Queries y Endpoints TD/TC

*   **Estado:** Aceptado
*   **Contexto:** El sistema expone datos de dos perspectivas complementarias del mismo pago social:
    - **Titular de Derecho (TD):** La persona que tiene el derecho al beneficio (el beneficiario registrado).
    - **Titular de Cobro (TC):** La persona que recibe físicamente el pago (ej: madre que cobra AUH por sus hijos menores).

    Las 5 tablas materializadas (3 TD + 2 TC + 1 nominal TC) ya existen y se crean con un single-scan del pipeline (`_pp` temp table). Sin embargo, la capa de aplicación (queries + endpoints) era monolítica: un solo `queries.py` con lógica TD y un solo `app.py` con rutas API mezcladas con infraestructura Flask.

    Se evaluó la modularización en 3 rondas de comités de expertos (DBA, Data Architect, DevOps, Especialista de Negocio, PM, Software Architect, Backend Developer). Las alternativas descartadas fueron:
    - **Solo blueprint TC (asimétrico):** `queries.py` para TD + `queries_tc.py` para TC. Descartado porque un profesional no puede identificar qué archivo es TD sin abrir y leer el código.
    - **Flag booleano `is_tc`:** Funciones genéricas con parámetro para switchear TD/TC. Descartado porque crea branching invisible y dificulta la evolución independiente.
    - **Separación del pipeline TD/TC:** Descartado porque duplicaría el scan de `benefits` (8M+ filas) y rompería el atomic swap de 6 tablas.

*   **Decisión:** Modularización simétrica en la capa de aplicación:
    1. **`queries_helpers.py`** — Infraestructura compartida: filtros (`cross_where`), routing MV (`use_resumen`), métricas (`METRIC_COL`), query genérica (`cross_query`), lookups (`get_periods`, `get_programs`, `get_provincias`), auth (`get_user_by_email`).
    2. **`queries_td.py`** (rename de `queries.py`) — Funciones TD: `get_summary()`, `get_by_programa()`, `get_nominal_list()`, etc. contra `mv_cross`, `mv_resumen`, `mv_nominal`.
    3. **`queries_tc.py`** — Funciones TC: `get_summary_tc()`, `get_by_programa_tc()`, `get_nominal_tc_list()`, etc. contra `mv_cross_tc`, `mv_resumen_tc`, `mv_nominal_tc`.
    4. **`endpoints_td.py`** — Flask Blueprint con prefix `/api`. Rutas TD extraídas de `app.py`.
    5. **`endpoints_tc.py`** — Flask Blueprint con prefix `/api/tc`. Rutas TC nuevas.
    6. **`app.py`** — Pura infraestructura Flask (~90 lín): init, security headers, cache, DB helpers, health checks, page routes, blueprint registration.

    El pipeline (`create_matviews.py`, `refresh_matviews.py`) **NO se separa**: la tabla temporal `_pp` computa TD y TC en un solo scan, y el refresh blue/green swapea 6 tablas en un solo COMMIT atómico.

*   **Consecuencias:**
    *   **Positivas:** Estructura de directorio autoexplicativa (un dev entiende TD/TC viendo nombres de archivos). Evolución independiente de cada dominio. Trazabilidad SQL via `pg_stat_statements` con comment headers por módulo. Cada blueprint es autocontenido (~100 lín).
    *   **Negativas:** ~12 líneas de factory duplicadas entre blueprints TD y TC (decisión deliberada: autocontención > DRY). Pérdida de `git blame` directo en el rename de `queries.py` (mitigable con `git log --follow`).

*   **Principios adoptados:**
    *   Simetría de nombres: si existe `_tc`, existe `_td`
    *   Funciones con nombres explícitos, nunca abstracciones genéricas con flags booleanos
    *   Human-in-the-loop: un profesional debe poder trabajar sobre cualquier archivo sin conocimiento tribal

---

## ADR 010: Modelo de Datos y Materialización de Titulares de Cobro (TC)

*   **Estado:** Implementado
*   **Contexto:** El sistema RIB Dashboard registra prestaciones sociales donde intervienen dos figuras:
    - **Titular de Derecho (TD):** El beneficiario legal (ej: menor de edad que tiene derecho a AUH).
    - **Titular de Cobro (TC):** Quien recibe físicamente el pago (ej: madre/tutora que cobra la AUH).

    En programas como AUH y Tarjeta Alimentar, el TC es un adulto distinto al TD (generalmente la madre, para menores de 18 años). En programas como STESS o Potenciar Trabajo, el TC es el mismo TD (relación 1:1). El sistema necesitaba soportar análisis desde ambas perspectivas sin duplicar el pipeline de procesamiento.

*   **Decisión:** Se implementó el soporte TC en 4 capas:

    **1. Schema de origen (`db_schema.py`):**
    Se agregaron 7 columnas TC a la tabla `benefits`:
    `cuil_titular`, `nombre_titular`, `apellido_titular`, `sexo_titular`, `fecha_nacimiento_titular`, `provincia_titular`.
    Índice: `idx_benefits_periodo_cuil_titular ON benefits(periodo_mes, cuil_titular) INCLUDE (program_id, beneficiary_id)`.
    Cuando TC = TD, estos campos replican los datos del beneficiario. Cuando TC ≠ TD (menores en AUH/Alimentar), contienen los datos del adulto cobrador.

    **2. Generación de datos demo (`seed_pg.py`):**
    - Pool de TCs: ~20% de los beneficiarios reciben un TC externo (`n_tc = n // 5`).
    - `TC_PROGRAMS`: AUH (prog 0) y Tarjeta Alimentar (prog 2) usan TCs externos para menores.
    - Asignación estable: cada TC maneja 1-4 menores (simulando hogares). La asignación se computa una vez y se reutiliza en los 12 períodos.
    - Función `_tc_suffix()`: decide por cada benefit si usar datos TC externos (menor + programa elegible) o auto-referencia (TD = TC).
    - Demografía TC: 100% femenino, edad 25-56 (adultas cobradoras).

    **3. Ingesta de datos reales (`ingest_config.py`):**
    Las columnas TC son opcionales en los mapeos CSV: `cuil_titular`, `nombre_titular`, `apellido_titular`, `sexo_titular`, `fecha_nacimiento_titular`, `provincia_titular`. Cuando están ausentes, el sistema usa los valores del TD como fallback.

    **4. Materialización (`create_matviews.py`, `matviews.py`, `refresh_matviews.py`):**

    *Estrategia single-scan:* Una tabla temporal `_pp` (90 líneas SQL, 8 JOINs, 2 CROSS JOIN LATERAL para cálculo de edad) escanea `benefits` **una sola vez** y computa 25 columnas: 7 TD-only, 9 TC-only, 9 compartidas. Los 5 INSERTs posteriores leen de `_pp` en RAM.

    *Tablas materializadas creadas:*

    | Tabla | Perspectiva | Granularidad | Columnas clave | Filas aprox. |
    |-------|-------------|-------------|----------------|-------------|
    | `mv_cross` | TD | programa × provincia × sexo × grupo_etario × cant_prestaciones | personas, beneficios, montos | ~130-180K |
    | `mv_resumen` | TD | provincia × sexo × grupo_etario × cant_prestaciones (dedup) | personas, beneficios, montos | ~23K |
    | `mv_nominal` | TD | 1 fila por beneficiario × período | cuil, nombre, edad, arrays program_ids | ~8M |
    | `mv_cross_tc` | TC | programa × provincia × sexo × grupo_etario × cant_prestaciones | personas (TCs únicos), beneficios, montos | ~100-150K |
    | `mv_resumen_tc` | TC | provincia × sexo × grupo_etario × cant_prestaciones (dedup) | personas (TCs únicos), beneficios, montos | ~15-20K |
    | `mv_nominal_tc` | TC | 1 fila por TC × período | cuil_titular, cant_td, cuils_td[], program_ids[] | variable |

    *Nota:* `mv_cross_tc` y `mv_resumen_tc` comparten la misma estructura de columnas que sus equivalentes TD (`provincia`, `sexo`, `grupo_etario`, no `_titular`). Esto permite reutilizar la infraestructura de filtros y queries. Solo `mv_nominal_tc` usa sufijos `_titular`.

    *Refresh blue/green:* `refresh_matviews.py` crea shadow tables (`*_new`), procesa todos los períodos contra ellas, y ejecuta un swap atómico de las 6 tablas en un solo `COMMIT` vía `ALTER TABLE RENAME` (~1-3ms de downtime).

    *Verificación:* El check de consistencia en `ingest.py` (opción `[C]`) valida las 5 MVs (excluye `mv_nominal` del conteo legacy). El health check `/api/health/api` reporta `5/5 matviews`.

*   **Consecuencias:**
    *   **Positivas:** Cero costo adicional de I/O para TC (single-scan). Consistencia atómica TD+TC (un solo COMMIT). Datos demo realistas con relaciones familiares. Ingesta de datos reales con fallback transparente. Dimensiones TC idénticas a TD (reutilización de filtros).
    *   **Negativas:** La tabla `benefits` creció en 7 columnas (trade-off aceptable vs tabla separada de TCs que requeriría JOINs adicionales). `create_matviews.py` y `matviews.py` tienen SQL duplicado (~80 líneas del TEMP_TABLE_SQL) — pendiente de consolidación.

---

## ADR 009: Toggle TD/TC en Dashboard y Nominal (página única con hidratación client-side)

*   **Estado:** Aprobado
*   **Fecha:** 2026-03-27
*   **Contexto:** El backend ya soporta dos perspectivas de datos — TD (Titular de Derecho, el beneficiario) y TC (Titular de Cobro, quien cobra en nombre del beneficiario) — con endpoints separados (`/api/indicators/*` y `/api/tc/indicators/*`) y tablas materializadas separadas (`mv_cross`/`mv_cross_tc`, `mv_resumen`/`mv_resumen_tc`, `mv_nominal`/`mv_nominal_tc`). El frontend solo mostraba la perspectiva TD. Se evaluaron 3 opciones: (A) toggle intra-página en ambas vistas, (B) páginas separadas para TD y TC, (C) híbrido (toggle en dashboard, páginas separadas en nominal).

*   **Decisión:** Se adoptó la **Opción A: toggle intra-página** tanto para el dashboard como para el nominal, tras análisis de un comité de 4 expertos (API, Frontend/UX, Materialización, Contratos JSON).

    **Fundamento técnico del comité:**

    *Dashboard:* Los 7 endpoints de indicadores devuelven JSON con estructura 100% idéntica entre TD y TC (mismas keys, mismos tipos, mismo nesting). Las 9 instancias de Chart.js renderizan sin cambios — solo se modifica el prefijo de URL (`/api` → `/api/tc`). Duplicar `dashboard.html` (~1000 líneas) no tiene justificación técnica.

    *Nominal:* Aunque los contratos JSON difieren (TD: `{id, cantPrestaciones, prestaciones[], pagos[]}` vs TC: `{cuil, cantTD, cantBeneficios, titulares_derecho:[{prestaciones:[]}]}`), el análisis del código muestra que ~93% de `nominal.html` (650 de 699 líneas) es compartido: todo el CSS, la barra de filtros (6 de 7 filtros idénticos), la paginación y las funciones helper. Solo 5 funciones JS (~25-30 líneas) requieren branching condicional. Duplicar 650+ líneas para evitar 25 líneas de `if/else` es peor para el mantenimiento a 6-12 meses.

    **Implementación dashboard:**
    - Variable `viewMode` + constante `API_PREFIX = {td: '/api', tc: '/api/tc'}`
    - Toggle pill-button en topbar (reutiliza CSS `.metric-toggle` existente)
    - 8 `fetch()` calls parametrizados con `API_PREFIX[viewMode]`
    - `cachedData` keyed por viewMode para theme re-render sin refetch

    **Implementación nominal:**
    - 5 puntos de branching: `loadTable()`, `buildParams()`, `renderTable()`, `renderThead()` (nueva), `openDrawer()`
    - Toggle pill-button en barra de filtros
    - Show/hide filtro `cant_prestaciones` según modo
    - Drawer TD: info-grid + prestaciones + pagos (flat)
    - Drawer TC: info-grid + titulares_derecho anidados con sus prestaciones (jerárquico)

    **Estrategia de hidratación:**
    - Re-fetch on toggle (no cachear ambas vistas — data paginada es barata)
    - Sin SSR — mantener patrón actual (Jinja estructura + fetch data)
    - Preservar filtros compartidos al toggle, reset page a 1, limpiar `cant_prestaciones` en TC
    - Sin prefetch de la vista alternativa
    - URL state: `?view=td|tc` vía `history.replaceState` para deep linking

    **Riesgo identificado:** `queries_tc.py` `get_nominal_tc_detail()` tiene un problema N+1 (2 queries por cada TD en loop). Optimización pendiente con batch `WHERE cuil IN (...)`.

*   **Consecuencias:**
    *   **Positivas:** Cero duplicación de templates. Sidebar limpio (sin items adicionales). Toggle es extensión natural del patrón existente (período, métrica). Mantenimiento centralizado — un bug fix se aplica una vez. Deep linking soportado.
    *   **Negativas:** ~25-30 líneas de branching condicional en nominal.html. El drawer TC es código nuevo (~45 líneas) que no existía antes.

---

## ADR 010: Renaming RUB → RIB y aislamiento de base de datos de desarrollo

*   **Estado:** Implementado
*   **Fecha:** 2026-03-27
*   **Contexto:** El proyecto usaba el acrónimo "RUB" (Registro Único de Beneficiarios) en todo el codebase — docstrings, mensajes de consola, nombres de usuario demo, archivos temporales, y la base de datos se llamaba `rub`. Además, múltiples archivos del pipeline (`create_matviews.py`, `matviews.py`, `refresh_matviews.py`, `ingest_original.py`) tenían la URL de conexión hardcodeada como fallback (`postgresql://postgres:postgres@localhost/rub`) en lugar de leer del `.env`.

*   **Decisiones:**

    **1. Renaming global RUB → RIB:**
    Se reemplazó toda instancia de "RUB" por "RIB" y "rub" por "rib" en:
    - Docstrings y comentarios de todos los `.py`
    - Nombres de usuario demo ("Administrador RIB")
    - Mensajes de consola del pipeline
    - Templates HTML (`chatbot.html`)
    - Archivos legacy (`app.js`, `data.js`)
    - Documentación (`README.md`, `CLAUDE.md`, ADRs)
    - Configuración de agentes y skills de Claude (`.claude/`)
    - Archivos temporales de cookies (`/tmp/rib-cookies.txt`)
    - Cache keys del backend (`rib:` prefix)
    - Carpeta de datasets (`datasets/RIB`)
    - Legacy SQLite path (`rib.db`)

    **2. Base de datos de desarrollo `rib_dev`:**
    - `.env` ahora apunta a `postgresql://postgres:postgres@localhost/rib_dev`
    - Todos los archivos del pipeline que tenían fallback hardcodeado a `/rub` ahora usan `config.load_dotenv()` y fallan con error explícito si `DATABASE_URL` no está seteada
    - Archivos migrados: `create_matviews.py`, `matviews.py`, `refresh_matviews.py`, `ingest_original.py`
    - Archivos que ya usaban `load_dotenv()` correctamente: `ingest.py`, `seed_pg.py`, `ingest_optimizado.py`, `db_schema.py`

    **3. Centralización de conexión vía `.env`:**
    - El `.env` es la única fuente de verdad para `DATABASE_URL`
    - `config.py` lo carga con `load_dotenv()` al importarse
    - Los scripts standalone ahora importan `from config import load_dotenv` antes de leer `DATABASE_URL`
    - Se eliminaron todos los fallbacks hardcodeados — si falta `.env`, el error es inmediato y claro

*   **Consecuencias:**
    *   **Positivas:** Consistencia de naming en todo el proyecto. Aislamiento de la DB de desarrollo (`rib_dev`) respecto de producción. Imposible conectarse accidentalmente a la DB equivocada (sin fallbacks silenciosos). Mensajes de error claros.
    *   **Negativas:** Requiere que el `.env` exista para cualquier operación (trade-off aceptable por seguridad).

---
*Documento en evolución. Última actualización: 2026-03-27. Próximo ADR: 011.*
