# Performance Optimization — RUB Dashboard

## Context

The RUB Dashboard takes ~60 seconds to load with 20M beneficiaries (~600M benefits, ~480M payments) on a single 32GB machine running PostgreSQL + Flask + AI model. The root causes are: 9 sequential unoptimized queries per dashboard load, no caching, no materialized views, missing indexes, duplicate API calls, and no connection pooling. Data is loaded monthly (immutable within period), making aggressive materialization ideal.

**Target:** Dashboard <2s, Nominal <2s. POC-friendly (no Redis, no extra services).

**Approach:** Materialized Views + Flask SimpleCache (Redis optional) + Index optimization + Frontend fixes.

---

## Implementation Status

| Step | Description | Status |
|------|-------------|--------|
| Step 0 | Physical table cleanup (duplicate/redundant indexes) | **Ready** (`ingest.py` menu [8]) |
| Step 1 | Connection pooling (`config.py`) | **Done** |
| Step 2 | Materialized views (`create_matviews.py`) | **Done** |
| Step 3 | Missing indexes (`seed_pg.py`) | **Done** |
| Step 4 | Rewrite `queries.py` to use MVs | **Done** |
| Step 5 | Lookup tables (periods, provincias) | **Done** |
| Step 6 | Flask-Caching (Redis/SimpleCache) | **Done** |
| Step 7 | Frontend fixes (dashboard.html, base.html) | **Done** |
| Step 8 | Nominal tab optimization | **Done** (age in SQL, estado bug fixed) |
| Step 9 | Refresh strategy (`refresh_matviews.py`) | **Done** |
| 0c | Table partitioning (benefits/payments) | **Pending** (requires data reload) |

### Key Implementation Decisions

- **MV creation is a standalone Python script** (`create_matviews.py`) instead of `migrations/create_matviews.sql`. Allows programmatic error handling, row count reporting, and is consistent with `seed_pg.py` pattern.
- **mv_cross uses a CTE** to avoid positional `GROUP BY` references and includes a `sexo_label` column for display.
- **mv_by_grupo_etario uses a CTE** to compute age once per row instead of repeating `EXTRACT(YEAR FROM AGE(...))` in the CASE statement.
- **Age groups use 6 buckets** (0-4, 5-12, 13-17, 18-29, 30-59, 60+) instead of the original 5.
- **All dimension MVs store 3 aggregates**: `personas` (COUNT DISTINCT), `beneficios` (COUNT *), `montos` (SUM). Ratios `monto_persona` and `monto_beneficio` are computed at query time.
- **Concentration in filtered path** falls back to raw SQL on `benefits` table because mv_cross aggregates can't compute per-beneficiary program counts.
- **Nominal endpoints are NOT cached** (PII security — data contains personal information).
- **Redundant index `idx_payments_benid_period`** removed from seed_pg.py — covered by `idx_payments_compound`.
- **Filter badge XSS** fixed with HTML entity escaping in `renderFilterBadges()`.
- **`_check_mvs()` caches globally per process** — MVs are created once and don't change during runtime, so no invalidation needed.
- **`_extra_where()` helper** extracts the repeated `(" AND " + " AND ".join(fw)) if fw else ""` pattern (was 9 inline copies).
- **`_get_by_dimension_raw()` generic function** consolidates 4 near-duplicate raw fallback functions (`_get_by_secretaria_raw`, `_get_by_provincia_raw`, `_get_by_programa_raw`, `_get_by_sexo_raw`) into one parametric function + thin wrappers.
- **`themeColors()` JS helper** extracts theme-dependent color computation (was duplicated in `makeChart`, `makeHorizBar`, `renderEvolucion`).

---

## Metric System (5 metrics)

All dimension MVs and mv_cross store 3 base columns: `personas`, `beneficios`, `montos`. The application layer computes 5 metrics from these:

| Metric key | MV column (direct) | mv_cross (aggregated) | Display |
|------------|--------------------|-----------------------|---------|
| `personas` | `personas` | `SUM(personas)` | Count format (es-AR locale) |
| `beneficios` | `beneficios` | `SUM(beneficios)` | Count format |
| `montos` | `montos` | `SUM(montos)` | Currency format ($1.2M) |
| `monto_persona` | `montos/personas` | `SUM(montos)/SUM(personas)` | Currency format |
| `monto_beneficio` | `montos/beneficios` | `SUM(montos)/SUM(beneficios)` | Currency format |

**Frontend toggle:** 5 buttons — Personas | Beneficios | Montos | $/Persona | $/Beneficio

**queries.py helpers:**
- `_MV_COL[metric]` — SQL expression for single-MV queries (no SUM needed)
- `_METRIC_COL[metric]` — SQL expression for mv_cross queries (with SUM/aggregation)
- `_metric_expr(metric)` — raw SQL fallback (returns SELECT expression + JOIN clause)

**Default metric:** `personas` (was `beneficiarios` before this change)

---

## Bugs & Critical Flaws Found

1. **`get_periods()` (queries.py:100)** — `SELECT DISTINCT periodo_mes FROM benefits` scans **600M rows** to get 12 values. **Fixed:** lookup table `periods` + fallback to DISTINCT scan.

2. **`get_provincias()` (queries.py:109)** — `SELECT DISTINCT provincia FROM beneficiaries` scans **20M rows** for ~24 values. **Fixed:** lookup table `provincias_lookup` + fallback.

3. **Nominal COUNT per page** — `COUNT(DISTINCT ben.id)` with JOIN on 600M rows runs on every pagination click. **Mitigated:** partial index on ACTIVO benefits.

4. **Nominal subquery scans all benefits** — LEFT JOIN subquery scans ~50M rows per period. **Mitigated:** compound index on payments.

5. **Bug: Nominal subquery ignores estado filter** — Subquery hardcodes `estado_beneficio='ACTIVO'`. **Fixed:** subquery now uses the `estado` parameter.

6. **Age calculated in Python loop** — Per-row Python date arithmetic. **Fixed:** `EXTRACT(YEAR FROM AGE(...))` in SQL.

7. **`get_evolucion` slow joins** — JOINs payments (480M) with benefits (600M). **Fixed:** uses mv_evolucion or mv_cross.

8. **AGE() computed 5x per row** — CASE statement repeats AGE(). **Fixed:** mv_by_grupo_etario uses CTE.

9. **Incompatibility self-join** — JOINs benefits x benefits. **Fixed:** mv_incompatibilidades pre-computes (12 rows).

10. **No connection pooling** — New TCP connection per request. **Fixed:** ThreadedConnectionPool (min=2, max=10).

---

## Step 0: Physical Table Cleanup

> **Status: Ready** — Available from `ingest.py` menu option [8]. Auto-detects which indexes exist before dropping.

### 0a. Duplicate Indexes (DROP immediately, ~2+ GB freed)

| Table | Keep | Drop (duplicate) |
|-------|------|-------------------|
| `benefits` | `idx_benefits_benid` | `idx_benefits_bid` (identical) |
| `payments` | `idx_payments_period` | `idx_payments_periodo` (identical) |
| `beneficiaries` | `beneficiaries_cuil_key` (UNIQUE) | `idx_ben_cuil` (redundant) |

```sql
DROP INDEX idx_benefits_bid;
DROP INDEX idx_payments_periodo;
DROP INDEX idx_ben_cuil;
```

### 0b. Redundant Indexes (benefits: from 10 to 5)

| Drop | Covered by |
|------|------------|
| `idx_benefits_periodo` | `idx_benefits_periodo_estado` |
| `idx_benefits_estado` | `idx_benefits_period_state_benid` |

```sql
DROP INDEX idx_benefits_periodo;
DROP INDEX idx_benefits_estado;
```

### 0c. Partitioning on benefits/payments (future)

```sql
CREATE TABLE benefits_partitioned (...) PARTITION BY LIST (periodo_mes);
CREATE TABLE benefits_2026_01 PARTITION OF benefits_partitioned FOR VALUES IN ('2026-01');
-- etc.
```

---

## Step 1: Connection Pooling — DONE

**File:** `config.py`

- `psycopg2.pool.ThreadedConnectionPool` (min=2, max=10)
- Thread-safe initialization with `threading.Lock`
- `get_connection()` → `pool.getconn()` with `rollback()` for clean state
- `put_connection()` → `pool.putconn()` with `rollback()` + error fallback
- `close_pool()` for graceful shutdown
- `query()`, `query_one()`, `execute()` helpers with RealDictCursor

PostgreSQL config recommendations:
```
shared_buffers = 4GB
work_mem = 32MB              # per-sort per-query per-connection
maintenance_work_mem = 1GB   # for MV refresh and index builds
effective_cache_size = 16GB
random_page_cost = 1.1       # SSD
```

**Memory budget (32 GB shared with Flask + ML model + Redis):**

| Component | Memory |
|-----------|--------|
| PostgreSQL shared_buffers | 4 GB |
| PostgreSQL work_mem (10 conn x 3 ops x 32 MB worst case) | ~1 GB |
| PostgreSQL maintenance_work_mem (during MV refresh) | 1 GB |
| PostgreSQL base overhead | ~1 GB |
| Flask app + SimpleCache | ~0.5 GB |
| Redis cache | ~0.5 GB |
| ML model (qwen2.5-coder:7b via Ollama) | ~4 GB |
| OS + filesystem cache | ~20 GB |
| **Total** | **~32 GB** |

**IMPORTANT:** `work_mem = 256MB` (original value) would cause OOM — 10 conn x 3 sorts x 256 MB = 7.7 GB just in work_mem. Use `SET LOCAL work_mem = '256MB'` for heavy one-off queries only.

---

## Step 2: Materialized Views — DONE

**File:** `create_matviews.py`

Pipeline (via `ingest.py` menu or direct):
```bash
python ingest.py           # Interactive menu (options [2], [5], [6], [S])
# or direct:
python seed_pg.py          # 1. Schema + data + indexes
python create_matviews.py  # 2. Create + populate MVs
python refresh_matviews.py # 3. Monthly refresh
```

### MVs implemented (11 total)

| MV | Rows | Aggregate columns |
|----|------|-------------------|
| `mv_summary` | 12 | total_benef, cobertura, total_prest, cant_programas, no_identificados, identificados |
| `mv_pagos_summary` | 12 | total_monto, benef_con_pago |
| `mv_concentracion` | 12 | con_una, con_dos, con_tres_mas |
| `mv_incompatibilidades` | 12 | cant_incompatibles |
| `mv_by_programa` | ~120 | **personas, beneficios, montos** |
| `mv_by_secretaria` | ~36 | **personas, beneficios, montos** |
| `mv_by_provincia` | ~288 | **personas, beneficios, montos** |
| `mv_by_sexo` | ~48 | **personas, beneficios, montos** + label |
| `mv_by_grupo_etario` | ~72 | **personas, beneficios, montos** |
| `mv_evolucion` | 12 | **personas, beneficios, montos** |
| `mv_cross` | ~43K | **personas, beneficios, montos** + all dimensions |

All MVs have `UNIQUE INDEX` on their GROUP BY columns for `REFRESH CONCURRENTLY`.

### mv_cross detail

Uses CTE to avoid positional GROUP BY references (age CASE still evaluates EXTRACT 5x per row, but this is a one-time MV creation cost):
```sql
CREATE MATERIALIZED VIEW mv_cross AS
WITH base AS (
    SELECT b.periodo_mes, p.nombre_programa, p.secretaria_origen,
           ben.provincia, ben.sexo,
           CASE WHEN EXTRACT(YEAR FROM AGE(...)) <= 4 THEN '0-4 años'
                WHEN ... <= 12 THEN '5-12 años'
                WHEN ... <= 17 THEN '13-17 años'
                WHEN ... <= 29 THEN '18-29 años'
                WHEN ... <= 59 THEN '30-59 años'
                ELSE '60+ años'
           END AS grupo_etario,
           CASE ben.sexo WHEN 'M' THEN 'Masculino' ... END AS sexo_label,
           b.beneficiary_id, pay.monto_prestacion
    FROM benefits b
    JOIN programs p ON b.program_id = p.id
    JOIN beneficiaries ben ON b.beneficiary_id = ben.id
    LEFT JOIN payments pay ON pay.beneficiary_id = b.beneficiary_id
        AND pay.program_id = b.program_id AND pay.periodo_mes = b.periodo_mes
    WHERE b.estado_beneficio = 'ACTIVO'
)
SELECT periodo_mes, nombre_programa, secretaria_origen,
       provincia, sexo, grupo_etario, sexo_label,
       COUNT(DISTINCT beneficiary_id) AS personas,
       COUNT(*) AS beneficios,
       COALESCE(SUM(monto_prestacion), 0) AS montos
FROM base
GROUP BY periodo_mes, nombre_programa, secretaria_origen,
         provincia, sexo, grupo_etario, sexo_label;

CREATE UNIQUE INDEX ON mv_cross(periodo_mes, nombre_programa, secretaria_origen, provincia, sexo, grupo_etario);
CREATE INDEX ON mv_cross(periodo_mes);
CREATE INDEX ON mv_cross(periodo_mes, nombre_programa);
CREATE INDEX ON mv_cross(periodo_mes, provincia);
CREATE INDEX ON mv_cross(periodo_mes, secretaria_origen);
```

### Refresh function
```sql
CREATE OR REPLACE FUNCTION refresh_all_matviews() RETURNS void AS $$
BEGIN
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_summary;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_pagos_summary;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_concentracion;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_incompatibilidades;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_by_programa;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_by_secretaria;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_by_provincia;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_by_sexo;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_by_grupo_etario;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_evolucion;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_cross;
END;
$$ LANGUAGE plpgsql;
```

### Lookup tables
```sql
CREATE TABLE IF NOT EXISTS periods (periodo_mes TEXT PRIMARY KEY);
INSERT INTO periods SELECT DISTINCT periodo_mes FROM benefits ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS provincias_lookup (provincia TEXT PRIMARY KEY);
INSERT INTO provincias_lookup SELECT DISTINCT provincia FROM beneficiaries ON CONFLICT DO NOTHING;
```

---

## Step 3: Missing Indexes — DONE

**File:** `seed_pg.py`

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX idx_benefits_active_benid ON benefits(beneficiary_id, program_id, periodo_mes)
    WHERE estado_beneficio = 'ACTIVO';
CREATE INDEX idx_payments_compound ON payments(beneficiary_id, program_id, periodo_mes);
CREATE INDEX idx_ben_departamento ON beneficiaries(departamento);
CREATE INDEX idx_ben_sexo ON beneficiaries(sexo);
CREATE INDEX idx_ben_fecha_nacimiento ON beneficiaries(fecha_nacimiento);
CREATE INDEX idx_ben_cuil_trgm ON beneficiaries USING gin(cuil gin_trgm_ops);
```

---

## Step 4: Rewrite queries.py — DONE

**File:** `queries.py`

### Architecture

```
API endpoint (cached) → queries.py func → _check_mvs()
                                           ├── MVs exist + no filters → read individual MV
                                           ├── MVs exist + filters    → query mv_cross
                                           └── no MVs                 → raw SQL fallback
```

- `_check_mvs(conn)` — checks once per process if MVs exist
- `_cross_where(filters, exclude)` — builds WHERE for mv_cross from filter dict
- `_extra_where(fw)` — joins filter WHERE parts with AND prefix (DRY helper)
- `_MV_COL[metric]` — direct column expression for individual MVs
- `_METRIC_COL[metric]` — aggregated expression for mv_cross (with SUM)
- `_metric_expr(metric)` — raw SQL SELECT expression + JOIN for fallback
- `_get_by_dimension_raw()` — generic raw fallback for dimension queries (select, group, join, conditions)

### Concentration in filtered path

mv_cross can't compute per-beneficiary program counts. Falls back to raw SQL on `benefits` table.

### get_summary filtered path

Now reads `SUM(personas)`, `SUM(beneficios)`, `SUM(montos)`, `COUNT(DISTINCT nombre_programa)` from mv_cross. Computes `promedioPrestaciones = total_prest / total_benef`.

### Nominal optimizations

- Age in SQL: `EXTRACT(YEAR FROM AGE(corte_date, ben.fecha_nacimiento))::int`
- Estado bug fixed: subquery uses `estado` parameter
- OFFSET-based pagination maintained (keyset deferred)

---

## Step 5: Lookup Queries — DONE

- `get_periods()` → `periods` table (12 rows), fallback to DISTINCT scan
- `get_provincias()` → `provincias_lookup` table (24 rows), fallback to DISTINCT scan
- Tables created by `create_matviews.py`, refreshed by `refresh_matviews.py`

---

## Step 6: Flask-Caching — DONE

**File:** `app.py`

```python
# Redis if available, SimpleCache as fallback
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
try:
    import redis; redis.from_url(REDIS_URL).ping()
    cache_config = {"CACHE_TYPE": "RedisCache", "CACHE_REDIS_URL": REDIS_URL, "CACHE_DEFAULT_TIMEOUT": 3600}
except Exception:
    cache_config = {"CACHE_TYPE": "SimpleCache", "CACHE_DEFAULT_TIMEOUT": 3600}
```

- 8 indicator endpoints cached with `@cache.cached(key_prefix=_cache_key)`
- Cache key: endpoint + period + metric + sorted filter params
- Nominal NOT cached (PII)
- `POST /api/admin/clear-cache` to invalidate

---

## Step 7: Frontend Fixes — DONE

**File:** `templates/dashboard.html`

- **7a. Duplicate API call removed** — `loadProvincia()` passes data to `renderGeoMap(d)`
- **7b. Single API call** — Deferred (caching is fast enough)
- **7c. Theme toggle fixed** — `cachedData` + `rerenderFromCache()`, no network on toggle
- **7d. Script defer** — `defer` on Chart.js, Leaflet, datalabels in `base.html`
- **7e. XSS fixed** — HTML entity escaping in filter badges
- **7f. Metric toggle** — 5 buttons: Personas, Beneficios, Montos, $/Persona, $/Beneficio
- **7g. Money formatting** — `isMoneyMetric()` helper for montos/monto_persona/monto_beneficio → `fmtBig()`
- **7h. Geo tooltip** — Shows metric name dynamically (`globalMetric`)

---

## Step 8: Nominal Tab — PARTIALLY DONE

- Estado bug fixed
- Age in SQL
- Keyset pagination: NOT DONE (OFFSET maintained)
- Count subquery: NOT DONE (mitigated by compound index)

---

## Step 9: Refresh Strategy — DONE

**File:** `refresh_matviews.py`

1. `SELECT refresh_all_matviews()` — refreshes all 11 MVs concurrently
2. `TRUNCATE + INSERT` on lookup tables
3. `ANALYZE` on all MVs + lookup tables
4. Row count report
5. Attempts `POST /api/admin/clear-cache`

---

## Files

| File | Purpose |
|------|---------|
| **`ingest.py`** | **Interactive pipeline menu** — data loading, matview creation/refresh, index cleanup, Redis check, serve dashboard |
| `ingest_config.py` | Dataset configurations (column mappings, paths, province normalization) |
| `ingest_log.py` | Logging setup for ingest pipeline |
| `config.py` | Connection pooling (ThreadedConnectionPool), thread-safe init, query helpers |
| `queries.py` | All get_* use MVs with raw fallback; 5 metrics; _extra_where + _get_by_dimension_raw DRY helpers; lookup tables; nominal optimizations |
| `app.py` | Flask-Caching (Redis/SimpleCache), pool integration, cache clear endpoint, Decimal JSON, metric param default=personas |
| `templates/dashboard.html` | 5-metric toggle, cachedData + rerenderFromCache, geo dedup, filter badge XSS fix, DOMContentLoaded wrap, isMoneyMetric(), themeColors() |
| `templates/base.html` | `defer` on script tags |
| `seed_pg.py` | pg_trgm extension, partial index, compound index, filtering indexes |
| `create_matviews.py` | Creates 11 MVs (personas/beneficios/montos) + refresh function + lookup tables |
| `refresh_matviews.py` | Monthly refresh script |
| `requirements.txt` | flask, flask-caching, psycopg2-binary, redis, requests, gunicorn, polars |

---

## Expected Results

| Metric | Before | After |
|--------|--------|-------|
| Dashboard load (no filters) | ~60s | **<1s** (MV queries <10ms + Flask cache) |
| Dashboard load (with filters) | ~60s | **<2s** (mv_cross queries on ~43K rows) |
| Nominal page load | ~5-10s | **<2s** (partial indexes + age in SQL) |
| Theme toggle | ~60s (full reload) | **<100ms** (client-side re-render, no network) |
| Period change | ~60s | **<1s** (cached or MV lookup) |
| Metric change | ~60s | **<1s** (cached per metric+period+filters) |
| Memory overhead | baseline | **+~50 MB** (Flask cache + pool) |
| MV storage | 0 | **~5 MB** (negligible) |

---

## Verification

### Interactive pipeline (recommended)
```bash
python ingest.py
```
Menu options:
```
  Datos:
    [1] Ver estado de la base de datos
    [2] Cargar dataset (CSV via ingest_config.py)
    [3] Limpiar periodo específico
    [4] Limpiar TODAS las tablas de datos
  Pipeline:
    [5] Crear vistas materializadas
    [6] Refrescar vistas materializadas
    [7] Ver estado de matviews
    [8] Limpiar índices redundantes (Step 0)
  Servicios:
    [9] Verificar conexión Redis
    [S] Servir dashboard (Flask dev)
```

### Direct commands
```bash
python seed_pg.py          # 1. Schema + data + indexes
python create_matviews.py  # 2. Create + populate MVs
python app.py              # 3. Dev server
gunicorn -w 4 app:app      # 3. Production
python refresh_matviews.py # Monthly refresh
```

### Checklist
- [ ] Dashboard loads in <2s
- [ ] All 5 metric toggles work (Personas, Beneficios, Montos, $/Persona, $/Beneficio)
- [ ] Period change in <1s
- [ ] Theme toggle instant (no network in DevTools)
- [ ] Cross-chart filters work in <2s
- [ ] Nominal tab loads in <2s
- [ ] Filter badges display correctly (no XSS)
- [ ] `EXPLAIN ANALYZE` on key queries shows index usage
- [ ] Step 0 index cleanup via menu [8]
- [ ] Redis connected (verify via menu [9])

---

## Code Review (/simplify) — DONE

Three review agents analyzed all changed files. Fixes applied:

| Finding | Fix | Files |
|---------|-----|-------|
| 9x repeated `(" AND " + " AND ".join(fw)) if fw else ""` | Extracted `_extra_where(fw)` helper | `queries.py` |
| 4 near-duplicate `_get_by_*_raw` functions | Consolidated into `_get_by_dimension_raw()` + thin wrappers | `queries.py` |
| Theme color computation duplicated 3x | Extracted `themeColors()` function | `dashboard.html` |

Skipped (false positive or low-value):
- SQL injection from f-strings: values come from hardcoded dicts, not user input
- Age CASE repeated: different contexts (DDL vs query), coupling not worth it
- Chart.js destroy+recreate: <50ms overhead, bottleneck is API latency
- `renderEvolucion` not using `makeChart`: line chart config too different
- Concentration query could use MV: unfiltered path already does; filtered path correctly needs raw SQL

---

## Remaining Work (Performance)

1. **Step 0: Physical table cleanup** — Execute `DROP INDEX` on live DB (~2+ GB freed)
2. **Step 0c: Table partitioning** — Evaluate partitioning by `periodo_mes` during next data load
3. **Step 8: Keyset pagination** — Consider if nominal becomes slow at high page numbers
4. **Step 7b: Consolidated API call** — Consider `/api/indicators/all` if network latency is significant

---

## Step 10: Menú unificado (ingest.py) — Pending

### Estado actual

El menú de `ingest.py` ya tiene opciones [1]-[9]+[S]. Faltan 3 opciones para cubrir todo el ciclo de vida:

| Falta | Descripción |
|-------|-------------|
| [I] Inicializar DB | Schema + índices + usuarios (sin datos) |
| [D] Seedear datos demo | 10K/100K/1M/8M via seed_pg.py |
| [C] Check consistencia | DB ↔ MVs ↔ API |

### Menú completo propuesto

```
  Setup:
    [I] Inicializar base de datos (schema + índices + usuarios)
    [D] Seedear datos demo (10K/100K/1M/8M)
  Datos:
    [1] Ver estado de la base de datos
    [2] Cargar dataset (CSV via ingest_config.py) — por fuente o todos
    [3] Limpiar periodo específico
    [4] Limpiar TODAS las tablas de datos
  Pipeline:
    [5] Crear vistas materializadas
    [6] Refrescar vistas materializadas
    [7] Ver estado de matviews
    [8] Limpiar índices redundantes (Step 0)
  Verificación:
    [C] Check consistencia DB ↔ MVs ↔ API
  Servicios:
    [9] Verificar conexión Redis
    [S] Servir dashboard (Flask dev)
    [0] Salir
```

### Implementación

**[I] Inicializar DB** — llama `seed_pg.py --schema-only` (flag nuevo, ~5 lín en `__main__`). Ejecuta SCHEMA + INDEXES + INSERT users sin generar beneficiarios.

**[D] Seedear datos demo** — submenu:
```
  [1] 10K (test rápido, ~12s)
  [2] 100K (~2min)
  [3] 1M (~5min)
  [4] 8M completo (~30min)
  [0] Volver
```

**[2] Cargar dataset** — ya existe con submenu por fuente (VOUCHERS, BELGRANO, STESS, ALIMENTAR, [T]odos, [0] Volver).

**[C] Check consistencia** — ver Step 11.

**Compatibilidad:** Todos los scripts siguen funcionando por separado (`python seed_pg.py --small`, `python app.py`, etc.).

---

## Step 11: Check de consistencia E2E — Pending

### Qué valida

**1. Schema** — tablas, matviews y lookups existen:
```sql
SELECT tablename FROM pg_tables WHERE schemaname='public'
  AND tablename IN ('beneficiaries','benefits','payments','programs',
                    'secretarias','users','incompatibility_rules');
SELECT matviewname FROM pg_matviews WHERE schemaname='public';
SELECT tablename FROM pg_tables WHERE tablename IN ('periods','provincias_lookup');
```

**2. DB ↔ MVs** — datos consistentes:
- `mv_summary.total_benef` == `COUNT(DISTINCT beneficiary_id) FROM benefits WHERE ACTIVO` (por periodo)
- `SUM(personas)` en `mv_by_provincia` == `total_benef` en `mv_summary` (por periodo)
- `SUM(personas)` en `mv_by_programa` == `total_benef` en `mv_summary` (por periodo)
- `SUM(personas)` en `mv_by_sexo` == `total_benef` en `mv_summary` (por periodo)
- `con_una + con_dos + con_tres_mas` en `mv_concentracion` == `total_benef`
- `SUM(montos)` en `mv_by_programa` == `SUM(monto_prestacion)` en `payments JOIN benefits WHERE ACTIVO`
- `SUM` across `mv_cross GROUP BY provincia` == `mv_by_provincia`
- Periodos en `mv_summary` == filas en `periods` lookup
- Provincias en `mv_by_provincia` ⊆ `provincias_lookup`

**3. MVs ↔ API** (requiere dashboard corriendo, skip con warning si no):
- `GET /api/indicators/summary?period=X` vs `mv_summary`
- `GET /api/indicators/by-provincia?period=X` vs `mv_by_provincia`
- `GET /api/indicators/evolucion` vs `mv_evolucion`

**4. Invariantes matemáticas:**
- `identificados + no_identificados == total_prest`
- `montos > 0` cuando `personas > 0`
- Todo periodo tiene al menos 1 beneficiario
- Todo programa tiene al menos 1 beneficiario por periodo

### Output
```
  Check consistencia:
  ✓ Schema: 7/7 tablas, 11/11 matviews, 2/2 lookups
  ✓ mv_summary vs benefits: OK (3 periodos)
  ✓ mv_by_provincia sums: OK
  ✓ mv_by_programa sums: OK
  ✓ mv_by_sexo sums: OK
  ✓ mv_concentracion sums: OK
  ✓ mv_cross vs mv_by_provincia: OK
  ✓ mv_montos vs payments: OK
  ✓ Invariantes: OK
  ⚠ API check: dashboard no corriendo (skip)

  9/10 checks passed, 1 skipped
```

### Implementación
- Función `_check_consistency(conn)` en ingest.py (~80 líneas)
- Cada check: par de queries SQL comparadas
- Output via `log.info` (consistente con el resto del menú)

---

## Step 12: Health endpoints — Pending

Dos endpoints en `app.py` (sin auth — para monitoring):

### GET /api/health/api
```python
@app.route("/api/health/api")
def health_api():
    checks = {}
    try:
        conn = get_db()
        conn.cursor().execute("SELECT 1")
        checks["db"] = "ok"
    except Exception:
        checks["db"] = "error"
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM pg_matviews WHERE schemaname='public'")
        checks["matviews"] = f"{cur.fetchone()[0]}/11"
    except Exception:
        checks["matviews"] = "error"
    checks["cache"] = cache.config.get("CACHE_TYPE", "unknown")
    checks["status"] = "ok" if checks["db"] == "ok" else "degraded"
    return jsonify(checks)
```

### GET /api/health/ml
```python
@app.route("/api/health/ml")
def health_ml():
    checks = {}
    try:
        r = http_requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        models = [m["name"] for m in r.json().get("models", [])]
        checks["ollama"] = "ok"
        checks["models"] = models
        checks["target_model"] = CHATBOT_MODEL
        checks["model_loaded"] = any(CHATBOT_MODEL in m for m in models)
    except Exception:
        checks["ollama"] = "unreachable"
        checks["model_loaded"] = False
    checks["status"] = "ok" if checks.get("model_loaded") else "degraded"
    return jsonify(checks)
```

---

## Step 13: Hardening de seguridad — Pending

> Sin TLS → no usar `SESSION_COOKIE_SECURE=True`. Mitigaciones en capa de aplicación.

### 13a. Secretos y hashing (CRITICAL)

| Hallazgo | Archivo:línea | Fix |
|----------|---------------|-----|
| Flask secret key hardcoded | app.py:30 | Leer de `FLASK_SECRET_KEY` env, generar random si no existe + warning |
| SHA256 sin salt para passwords | app.py:106, seed_pg.py:89 | `werkzeug.security.generate_password_hash()` + migración transparente en login |
| DB creds hardcoded en fallback | config.py:18 | Quitar default, `RuntimeError` si no hay `DATABASE_URL` |
| Timing attack en comparación | app.py:153 | `check_password_hash()` es constant-time |

### 13b. Session hardening

| Fix | Detalle |
|-----|---------|
| Cookie config | `SESSION_COOKIE_HTTPONLY=True`, `SAMESITE="Lax"`, `PERMANENT_SESSION_LIFETIME=3600` |
| Session fixation | `session.clear()` antes de setear valores en login |
| Debug mode | `debug=os.environ.get("FLASK_DEBUG","").lower() in ("1","true")` |

### 13c. Security headers + CSRF

| Fix | Detalle |
|-----|---------|
| Headers | `@app.after_request`: X-Content-Type-Options, X-Frame-Options, Referrer-Policy, CSP |
| CSRF | `flask-wtf` + `CSRFProtect(app)` + tokens en login.html, base.html, chatbot.html |
| Dep nueva | `flask-wtf` en requirements.txt |

CSP permite CDNs existentes (Chart.js, Leaflet, marked.js, Google Fonts) + inline scripts/styles.

### 13d. Redis security

| Fix | Detalle |
|-----|---------|
| Cache key hashing | `_cache_key()` → `"rub:" + sha256(path)[:16]` en vez de path crudo |
| Key prefix | `CACHE_KEY_PREFIX: "rub:"` en cache_config |

### 13e. XSS en templates

| Fix | Archivo | Detalle |
|-----|---------|---------|
| innerHTML con PII | nominal.html | Agregar `sanitize()` helper, wrappear interpolaciones |
| LLM output | chatbot.html | DOMPurify desde CDN + `DOMPurify.sanitize(marked.parse(content))` |

### 13f. Chatbot SQL safety

| Fix | Detalle |
|-----|---------|
| Table allowlist | Regex para extraer tablas, validar contra `{beneficiaries, benefits, payments, programs, incompatibility_rules, secretarias}`. Rechazar si menciona `users` |
| LIMIT enforcement | Si SQL no tiene LIMIT → agregar `LIMIT 100` |
| CUIL masking | En `_format_sql_result()`: columnas `cuil`/`cuil_raw` → `*******1234` |

### 13g. Rate limiting en login

Rate limiter simple in-memory (~15 líneas): 5 intentos por IP en ventana de 5 minutos.

### Fuera de scope (requiere infra)
- TLS/HTTPS, `SESSION_COOKIE_SECURE`, PostgreSQL SSL server-side, Redis `requirepass`

---

## Etapas de ejecución (agentes paralelos)

```
Etapa 1: Menú unificado (Step 10) + Health endpoints (Step 12)
         → ingest.py, seed_pg.py (--schema-only), app.py (health)

Etapa 2: Seguridad core (Step 13a + 13b + 13g)
         → config.py, app.py, seed_pg.py

Etapa 3: Seguridad frontend (Step 13c + 13e)
         → app.py, requirements.txt, templates/*.html
         → depende de flask-wtf (requirements.txt de Etapa 2)

Etapa 4: Redis + chatbot safety (Step 13d + 13f)
         → app.py

Etapa 5: Check de consistencia (Step 11)
         → ingest.py (depende de Etapa 1)
```

Etapas 1, 2 y 4 pueden ejecutarse en paralelo. Etapa 3 después de 2. Etapa 5 después de 1.
