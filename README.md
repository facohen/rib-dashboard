# RUB Dashboard

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.x-000000?logo=flask&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-14+-4169E1?logo=postgresql&logoColor=white)
![Chart.js](https://img.shields.io/badge/Chart.js-4.4-FF6384?logo=chartdotjs&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green)

**Registro Único de Beneficiarios** — Dashboard de monitoreo de prestaciones sociales que permite visualizar cobertura, distribución geográfica/demográfica, incompatibilidades entre programas y datos nominales de beneficiarios.

---

## Stack tecnologico

| Capa | Tecnología | Detalle |
|------|-----------|---------|
| **Backend** | Flask 3.x (Python) | Rutas, API REST, auth por sesiones |
| **Base de datos** | PostgreSQL 14+ | Queries SQL vía `psycopg2` con `RealDictCursor` |
| **Frontend** | Jinja2 + Chart.js 4.4 | Templates server-rendered, 8 gráficos interactivos |
| **Visualización** | Chart.js + datalabels plugin | Doughnut, bar, horizontal bar, line con gradiente |
| **Autenticación** | Sesiones Flask | Roles `admin` / `user`, decoradores `@login_required` / `@admin_required` |
| **Temas** | CSS custom properties | Dark / Light mode con persistencia en `localStorage` |

## Quick Start

### 1. Requisitos previos

- Python 3.10+
- PostgreSQL 14+ corriendo localmente (o remoto)

### 2. Instalación

```bash
# Clonar el repositorio
git clone <repo-url>
cd rub-dashboard

# Crear entorno virtual e instalar dependencias
python -m venv venv
source venv/bin/activate        # Linux/Mac
venv\Scripts\activate           # Windows
pip install flask psycopg2-binary
```

### 3. Configurar base de datos

```bash
# Variable de entorno con la conexión a PostgreSQL
export DATABASE_URL=postgresql://user:pass@localhost/rub   # Linux/Mac
set DATABASE_URL=postgresql://user:pass@localhost/rub      # Windows CMD
$env:DATABASE_URL="postgresql://user:pass@localhost/rub"   # PowerShell
```

### 4. Seed de datos

```bash
# Seed rápido para desarrollo (10K beneficiarios, ~30s)
python seed_pg.py --small

# Seed medio (100K beneficiarios, ~5min)
python seed_pg.py --medium

# Seed completo para pruebas de carga (8M beneficiarios, ~45min)
python seed_pg.py
```

El seed crea el schema completo (tablas + índices), usuarios demo, 8 programas, reglas de incompatibilidad y datos de 12 períodos (2025-04 a 2026-03).

### 5. Ejecutar

```bash
python app.py
```

Abrir **http://localhost:5000** en el navegador.

## Credenciales demo

| Rol   | Email              | Contraseña | Acceso                          |
|-------|--------------------|------------|---------------------------------|
| Admin | admin@demo.local   | Demo123!   | Dashboard + Datos nominales     |
| User  | user@demo.local    | Demo123!   | Dashboard (solo lectura)        |

## Estructura del proyecto

```
├── app.py              # Rutas Flask, auth, API endpoints
├── config.py           # Conexión PostgreSQL y helpers de query
├── queries.py          # Todas las consultas SQL
├── seed_pg.py          # Generador de datos para PostgreSQL
├── seed.py             # Seed legacy (SQLite, no usado)
├── templates/
│   ├── base.html       # Layout con sidebar y sistema de temas
│   ├── dashboard.html  # KPIs + 8 gráficos Chart.js
│   ├── nominal.html    # Tabla de beneficiarios con detalle
│   └── login.html      # Pantalla de autenticación
├── CLAUDE.md           # Instrucciones para Claude Code
└── .gitignore
```

## API Endpoints

### Indicadores (requiere login)

| Endpoint                          | Descripción                        |
|-----------------------------------|------------------------------------|
| `GET /api/indicators/summary`     | KPIs principales del período       |
| `GET /api/indicators/by-secretaria` | Beneficiarios por secretaría     |
| `GET /api/indicators/by-provincia`  | Distribución provincial          |
| `GET /api/indicators/by-departamento` | Top 10 departamentos          |
| `GET /api/indicators/by-programa`   | Beneficiarios por programa       |
| `GET /api/indicators/by-sexo`       | Distribución por sexo            |
| `GET /api/indicators/by-grupo-etario` | Distribución por grupo etario |
| `GET /api/indicators/evolucion`     | Evolución histórica de pagos     |

Todos aceptan `?period=YYYY-MM` y filtros cruzados: `secretaria`, `sexo`, `programa`, `provincia`, `departamento`, `grupo_etario`.

### Nominal (requiere admin)

| Endpoint                              | Descripción                      |
|---------------------------------------|----------------------------------|
| `GET /api/nominal/beneficiaries`      | Lista paginada con filtros       |
| `GET /api/nominal/beneficiaries/<id>` | Detalle de un beneficiario       |

## Modelo de datos

```
users ─────────────── Usuarios del sistema (email, role)
beneficiaries ─────── Datos personales (CUIL, nombre, ubicación, nacimiento)
programs ──────────── Programas sociales (8), agrupados por secretaría
benefits ──────────── Prestaciones por beneficiario/programa/período
payments ──────────── Pagos liquidados por prestación
incompatibility_rules  Pares de programas incompatibles
```

---

## Arquitectura de archivos clave

### `config.py` — Conexión y helpers de base de datos

Punto central de acceso a PostgreSQL. Todos los demás módulos importan desde acá.

```python
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/rub")
```

| Función | Descripción |
|---------|-------------|
| `get_connection()` | Abre conexión PostgreSQL con `autocommit=False` |
| `query(conn, sql, params)` | Ejecuta SELECT y retorna lista de `RealDictCursor` (cada fila es un dict) |
| `query_one(conn, sql, params)` | Igual que `query` pero retorna solo la primera fila |
| `execute(conn, sql, params)` | Ejecuta INSERT/UPDATE/DELETE y hace `commit` |

Todas las queries usan **parámetros `%s`** (nunca interpolación directa), lo que previene SQL injection.

---

### `app.py` — Servidor Flask y API

Archivo principal que define rutas, autenticación y endpoints REST. Se estructura en 4 secciones:

**1. DB helpers** — `get_db()` almacena la conexión en `flask.g` (una por request) y la cierra en `teardown_appcontext`.

**2. Auth** — Dos decoradores:
- `@login_required` — redirige a `/login` (o retorna 401 en API)
- `@admin_required` — además verifica `role == 'admin'` (o retorna 403 en API)

Las contraseñas se hashean con SHA256 (sin salt — adecuado para demo, no para producción).

**3. Páginas** — 4 rutas de navegación:

| Ruta | Acceso | Descripción |
|------|--------|-------------|
| `/` | Público | Redirige a `/dashboard` o `/login` |
| `/login` | Público | Formulario POST, crea sesión |
| `/logout` | Público | Limpia sesión |
| `/dashboard` | Login | Dashboard con KPIs y gráficos |
| `/dashboard/nominal` | Admin | Tabla de beneficiarios |

**4. API REST** — 10 endpoints JSON que alimentan los gráficos del frontend:
- 8 endpoints de indicadores (`/api/indicators/*`) — aceptan `?period=` y filtros cruzados
- 2 endpoints nominales (`/api/nominal/*`) — paginados, solo admin

La función `_get_chart_filters()` extrae los 6 filtros cruzados del query string (`secretaria`, `sexo`, `programa`, `provincia`, `departamento`, `grupo_etario`) y los pasa a `queries.py`.

---

### `seed_pg.py` — Generador de datos para PostgreSQL

Script standalone que crea el schema completo y genera datos realistas. Ejecuta estas fases en orden:

```
1. DROP + CREATE tablas  →  Schema limpio desde cero
2. INSERT usuarios       →  admin + user demo
3. INSERT programas (8)  →  Agrupados en 3 secretarías
4. INSERT reglas (4)     →  Pares de programas incompatibles
5. INSERT beneficiarios  →  Batch de 10K con execute_values()
6. Por cada período (12):
   └─ INSERT benefits    →  1-3 prestaciones por beneficiario
   └─ INSERT payments    →  Pago por cada prestación activa
7. CREATE INDEX (9)      →  Índices para queries del dashboard
8. ANALYZE               →  Actualiza estadísticas del planner
```

**Distribución de datos:**

| Aspecto | Distribución |
|---------|-------------|
| Edad | 20% niñez, 30% jóvenes, 35% adultos, 15% mayores |
| Sexo | 30% M, 50% F, 10% X, 10% NI |
| Provincia | Ponderado por población real (Buenos Aires 38%, etc.) |
| Concentración | 50% con 1 programa, 30% con 2, 15% con 3+, 5% inválidos |
| Incompatibilidades | ~2% de beneficiarios violan reglas por período |
| Estado | ~8% de prestaciones son INACTIVO |

Escalas disponibles: `--small` (10K), `--medium` (100K), sin flag (8M beneficiarios).

Los montos varían por secretaría: Inclusión Social ~$80K, Desarrollo Humano ~$95K, Economía Social ~$60K (con variación aleatoria de -$10K a +$20K).

### `queries.py` — Capa de consultas SQL

Contiene **toda** la lógica SQL del proyecto. Ningún otro archivo escribe queries.

**Filtros cruzados** — El corazón del sistema interactivo. La función `_apply_filters()` recibe los filtros activos y genera dinámicamente:
- JOINs adicionales (solo si la tabla no está ya en la query base)
- Cláusulas WHERE parametrizadas
- Lista de params para psycopg2

Los flags `has_ben` y `has_prog` evitan JOINs duplicados cuando la query base ya incluye `beneficiaries` o `programs`.

**Funciones principales:**

| Función | Indicador | Retorna |
|---------|-----------|---------|
| `get_summary()` | I-01, I-05-10, I-14, I-15 | Dict con 7 KPIs + concentración |
| `get_by_secretaria()` | I-02 | Lista `{secretaria, total}` |
| `get_by_provincia()` | I-03 | Lista `{provincia, total}` |
| `get_by_departamento()` | I-04 | Top 10 `{departamento, provincia, total}` |
| `get_by_programa()` | I-13 | Lista `{programa, total}` |
| `get_by_sexo()` | I-12 | Lista `{sexo, label, total}` |
| `get_by_grupo_etario()` | I-11 | Lista `{grupo, total}` por rango de edad |
| `get_evolucion()` | I-16 | Serie temporal `{periodo_mes, total}` |
| `get_nominal_list()` | Nominal | Paginado `{items, total, page, pageSize}` |
| `get_nominal_detail()` | Nominal | Beneficiario + prestaciones + pagos |
