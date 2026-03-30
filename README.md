# RIB Dashboard

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.x-000000?logo=flask&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-14+-4169E1?logo=postgresql&logoColor=white)
![Chart.js](https://img.shields.io/badge/Chart.js-4.4-FF6384?logo=chartdotjs&logoColor=white)
![Leaflet](https://img.shields.io/badge/Leaflet-1.9-199900?logo=leaflet&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green)

**Registro Unico de Beneficiarios** — Dashboard de monitoreo de prestaciones sociales que visualiza cobertura, distribucion geografica/demografica, incompatibilidades entre programas y datos nominales de beneficiarios. Incluye chatbot IA para consultas en lenguaje natural.

---

## Stack tecnologico

| Capa | Tecnologia | Detalle |
|------|-----------|---------|
| **Backend** | Flask 3.x (Python) | Rutas, API REST, auth por sesiones, CSRF (flask-wtf), compresion (flask-compress) |
| **Base de datos** | PostgreSQL 14+ | Connection pooling (`ThreadedConnectionPool`), queries SQL via `psycopg2` |
| **Frontend** | Jinja2 + Chart.js 4.4 | Templates server-rendered, 8 graficos interactivos con cross-filtering |
| **Mapas** | Leaflet 1.9 | Mapa coropletico por provincia (lazy-loaded) |
| **Chatbot** | Ollama (LLM local) | Generacion de SQL desde lenguaje natural, 4 capas de seguridad |
| **Ingesta** | Polars | Lectura y transformacion de CSVs masivos |
| **Autenticacion** | Sesiones Flask + werkzeug | Roles `admin` / `user`, rate limiting (5 intentos/5 min por IP) |
| **Cache** | FileSystemCache | Directorio `.cache/`, TTL 1 hora, compartido entre workers Gunicorn |
| **Temas** | CSS custom properties | Dark / Light mode con persistencia en `localStorage` |

## Quick Start

### 1. Requisitos previos

- Python 3.10+
- PostgreSQL 14+ corriendo localmente (o remoto)
- (Opcional) Ollama para el chatbot IA

### 2. Instalacion

```bash
git clone <repo-url>
cd rib-dashboard

python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Configurar base de datos

```bash
export DATABASE_URL=postgresql://user:pass@localhost/rib_dev
```

### 4. Inicializar y cargar datos

La forma mas simple es usar el menu interactivo:

```bash
python ingest.py
```

O ejecutar los pasos manualmente:

```bash
python seed_pg.py --small          # Seed rapido: 10K beneficiarios (~30s)
python create_matviews.py          # Crear tablas materializadas
python app.py                      # Servidor en http://localhost:5000
```

### Escalas de seed disponibles

| Flag | Beneficiarios | Tiempo aprox. |
|------|---------------|---------------|
| `--small` | 10K | ~30s |
| `--medium` | 100K | ~5 min |
| `--1m` | 1M | ~15-20 min |
| _(sin flag)_ | 8M | ~45 min |

El seed crea el schema completo (tablas + indices + extension `pg_trgm`), usuarios demo, 8 programas sociales agrupados en 3 secretarias, reglas de incompatibilidad, 22 provincias, y datos de 12 periodos. Insercion masiva via protocolo COPY.

## Credenciales demo

| Rol | Email | Contrasena | Acceso |
|-----|-------|------------|--------|
| Admin | `admin@demo.local` | `Demo123!` | Dashboard + Nominal + Chatbot |
| User | `user@demo.local` | `Demo123!` | Dashboard (solo lectura) |

---

## Estructura del proyecto

```
rib-dashboard/
|
|-- app.py                  # Servidor Flask: rutas, API endpoints, security headers
|-- auth.py                 # Login/logout, @login_required, @admin_required, rate limiting
|-- config.py               # Connection pooling PostgreSQL, helpers query/execute
|-- queries.py              # Todas las consultas SQL, cross-filtering, routing MV
|-- db_schema.py            # DDL compartido: 7 tablas, indices, extension pg_trgm, usuarios demo
|-- chatbot.py              # Blueprint chatbot IA (Ollama) con 4 capas de seguridad
|
|-- ingest.py               # Menu interactivo: pipeline completo de operacion
|-- ingest_config.py        # Configuracion declarativa de 4 datasets CSV reales
|-- ingest_log.py           # Logger dual: consola + archivo
|-- preview_datasets.py     # Previsualizacion rapida de CSVs
|
|-- seed_pg.py              # Generador masivo de datos demo (10K-8M) con NumPy + COPY
|-- create_matviews.py      # Crea mv_cross + mv_resumen + mv_nominal
|-- refresh_matviews.py     # Blue/green refresh atomico de MVs
|-- matviews.py             # Wrapper: auto-detecta create vs refresh, maneja las 3 MVs
|
|-- templates/
|   |-- base.html           # Layout master: sidebar, topbar, temas dark/light
|   |-- login.html          # Pantalla de autenticacion
|   |-- dashboard.html      # 16 KPIs + 8 graficos Chart.js + mapa Leaflet (lazy-loaded)
|   |-- nominal.html        # Tabla paginada de beneficiarios con modal detalle (admin)
|   +-- chatbot.html        # Interfaz de chat con IA (marked.js + DOMPurify)
|
|-- datasets/               # CSVs de datos reales (no versionados)
|-- logs/                   # Logs de ingesta
|-- .cache/                 # FileSystemCache (auto-generado)
|-- requirements.txt        # Dependencias Python
|-- CLAUDE.md               # Instrucciones para Claude Code
+-- docs/                   # Specs y planes de optimizacion
```

> **Nota:** `index.html`, `app.js`, `data.js`, `style.css` son de un prototipo client-only anterior y **no se usan** en la app Flask actual.

---

## Operacion con `ingest.py`

El punto de entrada principal para operar el sistema es el menu interactivo:

```bash
python ingest.py
```

```
  Setup:
    [I] Inicializar base de datos (schema + indices + usuarios)
    [D] Seedear datos demo (10K/100K/1M/8M)
  Datos:
    [1] Ver estado de la base de datos
    [2] Cargar dataset (CSV via ingest_config.py)
    [3] Limpiar periodo especifico
    [4] Limpiar TODAS las tablas de datos
  Pipeline:
    [5] Crear tablas materializadas (mv_cross + mv_resumen + mv_nominal)
    [6] Refrescar tablas materializadas
    [7] Ver estado de tablas materializadas
    [8] Limpiar indices redundantes
  Verificacion:
    [C] Check consistencia DB <-> MVs <-> API
  Servicios:
    [S] Servir dashboard (Flask dev)
```

### Flujo tipico de operacion

```
Inicializar [I] -> Seedear [D] o Cargar CSV [2] -> Crear MVs [5] -> Servir [S]
```

Para actualizaciones periodicas:

```
Cargar nuevos datos [2] -> Refrescar MVs [6]
```

### Datasets reales configurados (`ingest_config.py`)

| Dataset | Tamano aprox. | Descripcion |
|---------|---------------|-------------|
| VOUCHERS | 764 MB | Liquidaciones de vouchers |
| BECAS_BELGRANO | 78 MB | Becas Manuel Belgrano |
| STESS | 1.7 GB | Prestaciones laborales |
| ALIMENTAR | Variable | Tarjeta Alimentar (titulares + menores) |

Cada dataset tiene mapeo de columnas, normalizacion de provincias (30+ variantes INDEC/ANSES), separadores custom, y opciones de deduplicacion.

---

## Tablas materializadas

El dashboard no consulta las tablas base directamente. Usa tres tablas materializadas pre-agregadas:

| Tabla | Filas aprox. | Contenido | Uso |
|-------|-------------|-----------|-----|
| `mv_cross` | 130-180K | Desglose por programa, secretaria, provincia, sexo, grupo etario, concentracion | Cuando se filtra o agrupa por programa/secretaria |
| `mv_resumen` | ~23K | Personas deduplicadas (sin dimension programa/secretaria) | Totales generales, distribuciones demograficas |
| `mv_nominal` | Variable | Beneficiarios individuales con arrays de programas | Vista nominal paginada (admin) |

### Dimensiones y metricas

**Dimensiones** (columnas de agrupacion):
- `periodo_mes` — Formato `YYYY-MM`
- `nombre_programa` / `secretaria_origen` — Solo en `mv_cross`
- `provincia` / `sexo` / `grupo_etario` / `cant_prestaciones`

**Metricas** (columnas de agregacion):
- `personas` — Personas unicas (deduplicadas por CUIL)
- `beneficios` — Total de prestaciones (una persona en 3 programas = 3 beneficios)
- `montos` — Suma de montos en pesos

**Valores de grupo etario:** `0-4 anos`, `5-12 anos`, `13-17 anos`, `18-29 anos`, `30-59 anos`, `60+ anos`

**Valores de concentracion:** `1`, `2`, `3+` (cantidad de programas por persona)

### Creacion y refresh

**Creacion:** `python create_matviews.py` — Construye las 3 tablas con indices covering para Index Only Scan. Usa una temp table compartida (un solo scan de las tablas base). Memory tuning automatico (`work_mem=4GB`).

**Refresh:** `python refresh_matviews.py` — Blue/green refresh: crea shadow tables, indexa, swap atomico (~1ms de downtime). Invalida cache automaticamente si el servidor esta activo.

**Wrapper:** `python matviews.py` — Auto-detecta si debe crear o refrescar.

`queries.py` enruta automaticamente: usa `mv_resumen` para totales de personas deduplicadas, `mv_cross` para desgloses por programa, y `mv_nominal` para la vista nominal.

---

## Modelo de datos

### Glosario del dominio

| Concepto | Definicion | Ejemplo |
|----------|-----------|---------|
| **Persona** | Ser humano, 1 fila en `beneficiaries` | Maria (CUIL 20-12345678-9) |
| **Beneficiario** | Una persona dentro de un programa | Maria en "Alimentar" |
| **Beneficio/prestacion** | Cada fila de `benefits` (persona+programa+mes) | Maria en Alimentar, marzo 2026 |
| **Monto** | Plata que cobra una persona por un beneficio | $45.000 |

Una persona en 3 programas = 1 persona, 3 beneficiarios, 3 beneficios.

### Tablas

```
users                      Usuarios del sistema (email, role: admin|user)
secretarias                3 secretarias que agrupan los programas
programs                   8 programas sociales, cada uno bajo una secretaria
beneficiaries              Datos personales (CUIL unico, nombre, sexo, fecha_nacimiento, ubicacion)
benefits                   Prestaciones: 1 fila por persona+programa+periodo (estado_beneficio, cuil_raw)
payments                   Pagos liquidados: 1 fila por persona+programa+periodo (monto_prestacion)
incompatibility_rules      Pares de programas incompatibles (is_compatible boolean)
```

**Convenciones:**
- **Periodo:** siempre formato `YYYY-MM` (ej: `2026-03`)
- **CUIL:** identificador nacional unico; CUIL invalido (<8 chars) = beneficiario "no identificado"
- **Deduplicacion:** benefits por CUIL+programa+periodo, pagos por CUIL+programa+periodo+monto
- **beneficiary_id nullable:** en `benefits`, permite registros sin match en `beneficiaries` (no identificados)

---

## Variables de entorno

| Variable | Requerida | Descripcion |
|----------|-----------|-------------|
| `DATABASE_URL` | Si | Conexion PostgreSQL (`postgresql://user:pass@host/db`) |
| `FLASK_SECRET_KEY` | No | Clave para sesiones (genera una efimera si no existe) |
| `OLLAMA_URL` | No | URL de Ollama para chatbot (default: `http://localhost:11434`) |
| `CHATBOT_MODEL` | No | Modelo LLM para chatbot (default: `qwen2.5-coder:7b`) |
| `APP_URL` | No | URL de la app para invalidar cache en refresh (default: `http://localhost:5000`) |

---

## API Endpoints

### Indicadores (requiere login)

| Endpoint | Descripcion |
|----------|-------------|
| `GET /api/indicators/summary` | KPIs principales: cobertura, prestaciones, montos, concentracion |
| `GET /api/indicators/by-secretaria` | Distribucion por secretaria |
| `GET /api/indicators/by-provincia` | Distribucion provincial |
| `GET /api/indicators/by-programa` | Distribucion por programa |
| `GET /api/indicators/by-sexo` | Distribucion por sexo (F/M/X) |
| `GET /api/indicators/by-grupo-etario` | Distribucion por grupo etario (6 rangos) |
| `GET /api/indicators/evolucion` | Evolucion historica (todos los periodos) |
| `GET /api/indicators/provincia-detail` | Detalle provincial: totales + top 5 programas |

Todos aceptan `?period=YYYY-MM`, `?metric=personas|beneficios|montos|monto_persona|monto_beneficio`, y filtros cruzados: `secretaria`, `sexo`, `programa`, `provincia`, `grupo_etario`, `cant_prestaciones`.

### Nominal (requiere admin)

| Endpoint | Descripcion |
|----------|-------------|
| `GET /api/nominal/beneficiaries` | Lista paginada con filtros (cuil, provincia, sexo, programa, estado) |
| `GET /api/nominal/beneficiaries/<id>` | Detalle: datos personales + prestaciones + pagos del periodo |
| `GET /api/nominal/export` | Export CSV con filtros aplicados |

### Chatbot (requiere login)

| Endpoint | Descripcion |
|----------|-------------|
| `GET /api/chatbot/status` | Estado de Ollama y modelo disponible |
| `POST /api/chatbot/ask` | Pregunta en lenguaje natural → SQL → resultados formateados |

**Seguridad del chatbot — 4 capas de proteccion:**

El chatbot genera SQL via LLM (Ollama) y lo ejecuta contra la base. Toda query pasa por 4 capas antes de tocar PostgreSQL:

| # | Capa | Tipo | Que hace |
|---|------|------|----------|
| 1 | **System prompt** | Soft | Instruye al LLM a generar solo SELECT, preferir MVs, no devolver datos nominales |
| 2 | **Filtro post-LLM** (`_call_ollama`) | Media | Descarta cualquier respuesta que no empiece con `SELECT` |
| 3 | **Validacion pre-ejecucion** (`_execute_readonly`) | Fuerte | Prohibe `;` (multi-statement), bloquea keywords DML/DDL (`INSERT`, `UPDATE`, `DELETE`, `DROP`, `CREATE`, `ALTER`, `TRUNCATE`, `COMMIT`, `SET`, etc.), allowlist de 8 tablas, bloqueo de columnas PII (`cuil`, `nombre`, `apellido` en SELECT), inyeccion de `LIMIT 100` |
| 4 | **PostgreSQL** | Fuerte | `SET TRANSACTION READ ONLY` — el motor rechaza cualquier escritura aunque las capas anteriores fallen |

**Datos nominales:** si la query intenta devolver `cuil`, `nombre` o `apellido`, se rechaza con mensaje amigable. El LLM tambien esta instruido para responder `NO_SQL` ante pedidos de datos personales.

**Prompt del LLM:** incluye metadata del dominio (persona vs beneficiario vs beneficio vs monto), schema de las MVs con sus dimensiones/metricas, tablas raw como fallback para queries que las MVs no cubren (ej: edad exacta), y 14 ejemplos de queries frecuentes.

**Limitaciones conocidas:** keywords DML dentro de string literals pueden causar falsos positivos (ej: `WHERE provincia = 'INSERT del Norte'`). Aceptable para uso interno.

### Health checks (sin CSRF)

| Endpoint | Descripcion |
|----------|-------------|
| `GET /api/health/api` | Estado de la DB + existencia de MVs |
| `GET /api/health/ml` | Disponibilidad de Ollama y modelo |

### Admin

| Endpoint | Descripcion |
|----------|-------------|
| `POST /api/admin/clear-cache` | Invalidar FileSystemCache manualmente |

---

## Arquitectura de la aplicacion

### Capas del backend

```
app.py  ---->  queries.py  ---->  config.py  ---->  PostgreSQL
  |                |                                    |
  |  auth.py       |  mv_cross / mv_resumen / mv_nominal
  |  chatbot.py    |
```

- **`config.py`** — Pool de conexiones (`ThreadedConnectionPool`, min=2, max=10). Helpers: `get_connection()`, `put_connection()`, `query()`, `query_one()`, `execute()`. Todo parametrizado con `%s`, retorna `RealDictCursor`.
- **`queries.py`** — Toda la logica SQL centralizada. Cross-filtering dinamico via `_cross_where()`. Routing automatico entre `mv_resumen`, `mv_cross` y `mv_nominal` segun la query. 5 metricas: `personas`, `beneficios`, `montos`, `monto_persona`, `monto_beneficio`.
- **`app.py`** — Rutas Flask, conexion por request en `flask.g`, blueprints para auth y chatbot. Security headers (CSP, X-Frame-Options, X-Content-Type-Options). Custom JSON provider para `Decimal`.
- **`auth.py`** — Autenticacion por sesiones, rate limiting (5 intentos/5 min por IP), migracion transparente de hashes SHA256 a werkzeug. Cookies `HttpOnly` + `SameSite=Lax`, sesion de 1 hora.
- **`chatbot.py`** — Blueprint que conecta con Ollama, genera SQL desde lenguaje natural, valida y ejecuta read-only. Respuestas en formato NDJSON streaming.

### Frontend

Templates Jinja2 server-rendered. Sin build step, sin bundler. Librerias via CDN:
- **Chart.js 4.4** + datalabels plugin — 8 graficos interactivos con cross-filtering
- **Leaflet 1.9** — Mapa coropletico por provincia (lazy-loaded, renderizado diferido)
- **marked.js** + **DOMPurify** — Renderizado de Markdown en chatbot

El objeto `globalChartFilters` en el cliente maneja el estado de filtros cruzados. Al hacer click en un segmento de un grafico, se actualiza el filtro y se re-renderizan todos los graficos. Los charts se renderizan de forma diferida para mejorar el tiempo de carga inicial.

---

## Seguridad

| Aspecto | Implementacion |
|---------|---------------|
| **Autenticacion** | Sesiones server-side, werkzeug password hashing, migracion desde SHA256 |
| **Autorizacion** | Roles `admin`/`user`, decoradores `@login_required`/`@admin_required` |
| **Rate limiting** | 5 intentos de login por IP cada 5 minutos |
| **CSRF** | flask-wtf en formularios |
| **Headers** | CSP, X-Content-Type-Options, X-Frame-Options |
| **Cookies** | `HttpOnly`, `SameSite=Lax`, lifetime 1 hora |
| **SQL injection** | Queries parametrizadas (`%s`) en toda la app |
| **Chatbot** | 4 capas (prompt, filtro post-LLM, validacion pre-exec, READ ONLY en PG) |
| **PII** | Chatbot bloquea columnas nominales; masking de CUIL en respuestas |

---

## Produccion

```bash
pip install gunicorn
gunicorn -w 4 app:app --bind 0.0.0.0:5000
```

La cache FileSystemCache en `.cache/` es compartida entre workers. Para invalidar:

```bash
curl -X POST http://localhost:5000/api/admin/clear-cache -H "Cookie: session=..."
```

### Tuning PostgreSQL recomendado

```
shared_buffers = 4GB
work_mem = 32MB
maintenance_work_mem = 1GB
effective_cache_size = 16GB
```
