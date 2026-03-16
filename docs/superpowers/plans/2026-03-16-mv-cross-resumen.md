# Spec-Driven Development: mv_cross + mv_resumen

## Context

Dashboard pre-aggregates data into materialized views for <10ms query times. Current architecture has two problems:

1. **Person double-counting.** `mv_cross` has one row per person-program. A beneficiary in AUH + PROGRESAR = 2 rows. `SUM(personas)` over-counts by ~64%. There is no deduplicated person table.
2. **Architectural debt.** Stored function for refresh, `sexo_label` as stored column, no payment pre-aggregation (fan-out risk), SimpleCache per-process, dead Redis references.

**Solution:** Replace with two physical tables:
- `mv_cross` — per-program breakdown (7 dimensions). For program/secretaría charts.
- `mv_resumen` — deduplicated persons (5 dimensions). For global totals, demographics, evolution, concentration.

Plus: blue/green refresh (atomic swap), FileSystemCache, remove Redis.

**What does NOT change:** `config.py`, `seed_pg.py`, `dashboard.html`, `nominal.html`, `chatbot.html`, nominal queries, raw filter helpers.

---

## Table Schemas

### mv_cross — per-program (7 dimensions, 3 aggregates)

| Column | Type | Source |
|--------|------|--------|
| `periodo_mes` | text | `benefits.periodo_mes` |
| `nombre_programa` | text | `programs.nombre_programa` |
| `secretaria_origen` | text | `programs.secretaria_origen` |
| `provincia` | text | `beneficiaries.provincia` |
| `sexo` | text | `beneficiaries.sexo` (M/F/X/NI) |
| `grupo_etario` | text | Computed: 0-4, 5-12, 13-17, 18-29, 30-59, 60+, Sin dato |
| `cant_prestaciones` | text | Computed: '1', '2', '3+' |
| `personas` | bigint | `COUNT(*)` — one per person-program (no DISTINCT) |
| `beneficios` | bigint | `SUM(cant_benefits)` |
| `montos` | numeric | `SUM(person_monto)` |

**Key property:** A beneficiary in N programs appears in N rows. `SUM(personas)` is inflated. This is correct for per-program breakdowns but wrong for global totals.

**`sexo_label` is NOT a column.** Computed at query time:
```sql
CASE sexo WHEN 'M' THEN 'Masculino' WHEN 'F' THEN 'Femenino'
          WHEN 'X' THEN 'No binario' ELSE 'No informado' END AS label
```

#### SQL

```sql
CREATE TABLE mv_cross AS
WITH
benef_prog_count AS (
    SELECT beneficiary_id, periodo_mes,
           COUNT(DISTINCT program_id) AS cant_prog
    FROM benefits
    WHERE estado_beneficio = 'ACTIVO' AND beneficiary_id IS NOT NULL
    GROUP BY beneficiary_id, periodo_mes
),
payment_agg AS (
    SELECT beneficiary_id, program_id, periodo_mes,
           SUM(monto_prestacion) AS total_monto
    FROM payments
    GROUP BY beneficiary_id, program_id, periodo_mes
),
benefit_enriched AS (
    SELECT b.periodo_mes, p.nombre_programa, p.secretaria_origen,
           ben.provincia, ben.sexo,
           CASE
             WHEN ben.fecha_nacimiento IS NULL THEN 'Sin dato'
             WHEN edad <= 4  THEN '0-4 años'
             WHEN edad <= 12 THEN '5-12 años'
             WHEN edad <= 17 THEN '13-17 años'
             WHEN edad <= 29 THEN '18-29 años'
             WHEN edad <= 59 THEN '30-59 años'
             ELSE '60+ años'
           END AS grupo_etario,
           CASE
               WHEN bpc.cant_prog = 1 THEN '1'
               WHEN bpc.cant_prog = 2 THEN '2'
               ELSE '3+'
           END AS cant_prestaciones,
           b.beneficiary_id,
           pa.total_monto
    FROM benefits b
    JOIN programs p ON b.program_id = p.id
    JOIN beneficiaries ben ON b.beneficiary_id = ben.id
    JOIN benef_prog_count bpc
         ON bpc.beneficiary_id = b.beneficiary_id
         AND bpc.periodo_mes = b.periodo_mes
    LEFT JOIN payment_agg pa ON pa.beneficiary_id = b.beneficiary_id
        AND pa.program_id = b.program_id AND pa.periodo_mes = b.periodo_mes
    CROSS JOIN LATERAL (
        SELECT EXTRACT(YEAR FROM AGE(
            (SUBSTRING(b.periodo_mes FROM 1 FOR 4) || '-' ||
             SUBSTRING(b.periodo_mes FROM 6 FOR 2) || '-01')::date,
            ben.fecha_nacimiento
        ))::int AS edad
    ) calc
    WHERE b.estado_beneficio = 'ACTIVO'
),
per_person AS (
    SELECT periodo_mes, nombre_programa, secretaria_origen,
           provincia, sexo, grupo_etario, cant_prestaciones,
           beneficiary_id,
           COUNT(*) AS cant_benefits,
           COALESCE(SUM(total_monto), 0) AS person_monto
    FROM benefit_enriched
    GROUP BY periodo_mes, nombre_programa, secretaria_origen,
             provincia, sexo, grupo_etario, cant_prestaciones, beneficiary_id
)
SELECT periodo_mes, nombre_programa, secretaria_origen,
       provincia, sexo, grupo_etario, cant_prestaciones,
       COUNT(*) AS personas,
       SUM(cant_benefits) AS beneficios,
       SUM(person_monto) AS montos
FROM per_person
GROUP BY periodo_mes, nombre_programa, secretaria_origen,
         provincia, sexo, grupo_etario, cant_prestaciones;
```

#### Indexes

```sql
CREATE UNIQUE INDEX ON mv_cross(periodo_mes, nombre_programa, secretaria_origen, provincia, sexo, grupo_etario, cant_prestaciones);
CREATE INDEX ON mv_cross(periodo_mes);
CREATE INDEX ON mv_cross(periodo_mes, nombre_programa);
CREATE INDEX ON mv_cross(periodo_mes, provincia);
CREATE INDEX ON mv_cross(periodo_mes, secretaria_origen);
CREATE INDEX ON mv_cross(periodo_mes, cant_prestaciones);
```

#### Expected size

~130-180K rows with 8M beneficiaries, ~60-65K with 10K (--small). <10MB.

---

### mv_resumen — deduplicated persons (5 dimensions, 3 aggregates)

| Column | Type | Source |
|--------|------|--------|
| `periodo_mes` | text | `benefits.periodo_mes` |
| `provincia` | text | `beneficiaries.provincia` |
| `sexo` | text | `beneficiaries.sexo` |
| `grupo_etario` | text | Computed (same buckets as mv_cross) |
| `cant_prestaciones` | text | Computed: '1', '2', '3+' |
| `personas` | bigint | `COUNT(*)` — one per person (deduplicated across programs) |
| `beneficios` | bigint | `SUM(cant_benefits)` — total across all programs |
| `montos` | numeric | `SUM(person_monto)` — total across all programs |

**Key property:** Each beneficiary appears exactly once per period. `SUM(personas)` = true person count = `COUNT(DISTINCT beneficiary_id)` from raw data.

**Does NOT have** `nombre_programa` or `secretaria_origen`. If a query attempts to filter/group by these columns on mv_resumen, it will fail with a SQL error — this is intentional fail-fast behavior.

#### SQL

Same CTEs 1-4 as mv_cross, plus `per_person_total` that collapses programs:

```sql
CREATE TABLE mv_resumen AS
WITH
benef_prog_count AS (
    SELECT beneficiary_id, periodo_mes,
           COUNT(DISTINCT program_id) AS cant_prog
    FROM benefits
    WHERE estado_beneficio = 'ACTIVO' AND beneficiary_id IS NOT NULL
    GROUP BY beneficiary_id, periodo_mes
),
payment_agg AS (
    SELECT beneficiary_id, program_id, periodo_mes,
           SUM(monto_prestacion) AS total_monto
    FROM payments
    GROUP BY beneficiary_id, program_id, periodo_mes
),
benefit_enriched AS (
    SELECT b.periodo_mes, p.nombre_programa, p.secretaria_origen,
           ben.provincia, ben.sexo,
           CASE
             WHEN ben.fecha_nacimiento IS NULL THEN 'Sin dato'
             WHEN edad <= 4  THEN '0-4 años'
             WHEN edad <= 12 THEN '5-12 años'
             WHEN edad <= 17 THEN '13-17 años'
             WHEN edad <= 29 THEN '18-29 años'
             WHEN edad <= 59 THEN '30-59 años'
             ELSE '60+ años'
           END AS grupo_etario,
           CASE
               WHEN bpc.cant_prog = 1 THEN '1'
               WHEN bpc.cant_prog = 2 THEN '2'
               ELSE '3+'
           END AS cant_prestaciones,
           b.beneficiary_id,
           pa.total_monto
    FROM benefits b
    JOIN programs p ON b.program_id = p.id
    JOIN beneficiaries ben ON b.beneficiary_id = ben.id
    JOIN benef_prog_count bpc
         ON bpc.beneficiary_id = b.beneficiary_id
         AND bpc.periodo_mes = b.periodo_mes
    LEFT JOIN payment_agg pa ON pa.beneficiary_id = b.beneficiary_id
        AND pa.program_id = b.program_id AND pa.periodo_mes = b.periodo_mes
    CROSS JOIN LATERAL (
        SELECT EXTRACT(YEAR FROM AGE(
            (SUBSTRING(b.periodo_mes FROM 1 FOR 4) || '-' ||
             SUBSTRING(b.periodo_mes FROM 6 FOR 2) || '-01')::date,
            ben.fecha_nacimiento
        ))::int AS edad
    ) calc
    WHERE b.estado_beneficio = 'ACTIVO'
),
per_person AS (
    SELECT periodo_mes, nombre_programa, secretaria_origen,
           provincia, sexo, grupo_etario, cant_prestaciones,
           beneficiary_id,
           COUNT(*) AS cant_benefits,
           COALESCE(SUM(total_monto), 0) AS person_monto
    FROM benefit_enriched
    GROUP BY periodo_mes, nombre_programa, secretaria_origen,
             provincia, sexo, grupo_etario, cant_prestaciones, beneficiary_id
),
per_person_total AS (
    SELECT periodo_mes, provincia, sexo, grupo_etario, cant_prestaciones,
           beneficiary_id,
           SUM(cant_benefits) AS cant_benefits,
           SUM(person_monto) AS person_monto
    FROM per_person
    GROUP BY periodo_mes, provincia, sexo, grupo_etario, cant_prestaciones, beneficiary_id
)
SELECT periodo_mes, provincia, sexo, grupo_etario, cant_prestaciones,
       COUNT(*) AS personas,
       SUM(cant_benefits) AS beneficios,
       SUM(person_monto) AS montos
FROM per_person_total
GROUP BY periodo_mes, provincia, sexo, grupo_etario, cant_prestaciones;
```

#### Indexes

```sql
CREATE UNIQUE INDEX ON mv_resumen(periodo_mes, provincia, sexo, grupo_etario, cant_prestaciones);
CREATE INDEX ON mv_resumen(periodo_mes);
```

#### Expected size

~23K rows with 8M beneficiaries, ~14K with 10K (--small). <1MB.

---

## Invariants (must hold after every create/refresh)

| # | Invariant | SQL |
|---|-----------|-----|
| 1 | **mv_resumen persons = raw distinct** | `SUM(personas) FROM mv_resumen WHERE periodo_mes=X` = `COUNT(DISTINCT beneficiary_id) FROM benefits WHERE periodo_mes=X AND estado_beneficio='ACTIVO' AND beneficiary_id IS NOT NULL` |
| 2 | **Cross-table beneficios match** | `SUM(beneficios) FROM mv_cross WHERE periodo_mes=X` = `SUM(beneficios) FROM mv_resumen WHERE periodo_mes=X` |
| 3 | **Cross-table montos match** | `SUM(montos) FROM mv_cross WHERE periodo_mes=X` = `SUM(montos) FROM mv_resumen WHERE periodo_mes=X` |
| 4 | **mv_cross persons >= mv_resumen persons** | `SUM(personas) FROM mv_cross` >= `SUM(personas) FROM mv_resumen` (inflation from multi-program beneficiaries) |

---

## KPI Routing — Which Table Serves Each Indicator

| Indicator | Query | Table | Reason |
|-----------|-------|-------|--------|
| Total beneficiarios | `SUM(personas)` | `mv_resumen` | Deduplicated count |
| Total beneficios | `SUM(beneficios)` | `mv_resumen` | Additive (same either table) |
| Monto total | `SUM(montos)` | `mv_resumen` | Additive (same either table) |
| Promedio monto/persona | `SUM(montos)/SUM(personas)` | `mv_resumen` | Denominator must be deduplicated |
| Promedio prestaciones/persona | `SUM(beneficios)/SUM(personas)` | `mv_resumen` | Denominator must be deduplicated |
| Cantidad programas | `COUNT(DISTINCT nombre_programa)` | `mv_cross` | Only table with programa column |
| Por programa | `GROUP BY nombre_programa` | `mv_cross` | Only table with programa column |
| Por secretaría | `GROUP BY secretaria_origen` | `mv_cross` | Only table with secretaría column |
| Por provincia | `GROUP BY provincia` | `mv_resumen` | Deduplicated per-province |
| Por sexo | `GROUP BY sexo` | `mv_resumen` | Deduplicated per-sex |
| Por grupo etario | `GROUP BY grupo_etario` | `mv_resumen` | Deduplicated per-age-group |
| Evolución temporal | `GROUP BY periodo_mes` | `mv_resumen` | Deduplicated per-period |
| Concentración | `GROUP BY cant_prestaciones` | `mv_resumen` | Deduplicated per-bucket |
| Incompatibilidades | `COUNT(DISTINCT b1.beneficiary_id)` | raw SQL | Needs benefit-level JOINs |
| Departamento (top 10) | `GROUP BY departamento` | raw SQL | Not in any table |

### Cross-filter override

When programa or secretaría filter is active, ALL queries use `mv_cross` because:
- The filter restricts to a single program → no double-counting within one program
- `mv_resumen` lacks programa/secretaría columns → would fail

### Routing function

```python
def _use_resumen(filters, group_col):
    """Decide si usar mv_resumen (dedup) o mv_cross (por programa)."""
    if group_col in ("nombre_programa", "secretaria_origen"):
        return False
    if filters and (filters.get("programa") or filters.get("secretaria")):
        return False
    return True
```

---

## Blue/Green Refresh Pattern

Physical tables refreshed via shadow + atomic swap (not `REFRESH MATERIALIZED VIEW`).

```python
# Step 1: Create shadow tables (slow, non-blocking — separate txns)
for name, sql, indexes in TABLES:
    cur.execute(f"DROP TABLE IF EXISTS {name}_new")
    new_sql = sql.replace(f"CREATE TABLE {name}", f"CREATE TABLE {name}_new", 1)
    cur.execute(new_sql)
    conn.commit()
    # Create indexes on shadow
    for idx in indexes:
        cur.execute(idx.replace(f" {name}(", f" {name}_new("))
    conn.commit()

# Step 2: Atomic swap (~1ms, single transaction for both tables)
cur.execute("""
    DROP TABLE IF EXISTS mv_cross_old;
    ALTER TABLE mv_cross RENAME TO mv_cross_old;
    ALTER TABLE mv_cross_new RENAME TO mv_cross;
    DROP TABLE IF EXISTS mv_cross_old;
    DROP TABLE IF EXISTS mv_resumen_old;
    ALTER TABLE mv_resumen RENAME TO mv_resumen_old;
    ALTER TABLE mv_resumen_new RENAME TO mv_resumen;
    DROP TABLE IF EXISTS mv_resumen_old;
""")
conn.commit()
```

Both tables swap atomically. `get_summary()` queries both tables on the same connection — consistency guaranteed during the ~1ms swap window.

---

## Cache: FileSystemCache

```python
CACHE_DIR = os.path.join(os.path.dirname(__file__), ".cache")
cache_config = {
    "CACHE_TYPE": "FileSystemCache",
    "CACHE_DIR": CACHE_DIR,
    "CACHE_DEFAULT_TIMEOUT": 3600,
}
```

- Shared across Gunicorn workers (unlike SimpleCache which is per-process)
- `.cache/` added to `.gitignore`
- No Redis dependency
- 1-hour TTL, clearable via `POST /api/admin/clear-cache`

---

## API Response Contracts (no breaking changes)

### GET /api/indicators/summary

```json
{
  "period": "2025-04",
  "cobertura": 9122,
  "promedioPrestaciones": 1.64,
  "promedioMontoPorBenef": 143250,
  "montoTotal": 1306509750,
  "tasaNoIdentificados": 0,
  "casosIncompatibilidad": 187,
  "cantidadProgramas": 8,
  "concentracion": {"conUna": 4561, "conDos": 2734, "conTresMas": 1827}
}
```

Field changes:
- `cobertura`: was `COUNT(DISTINCT cuil)` from mv_cobertura → now `SUM(personas)` from mv_resumen
- `tasaNoIdentificados`: was calculated → now hardcoded `0` (frontend never rendered it)

### GET /api/indicators/by-sexo

```json
[{"sexo": "F", "label": "Femenino", "total": 4561}, ...]
```

`label` was read from `sexo_label` column → now computed with CASE. Same output.

### All other endpoints

Response shapes unchanged. Table routing is internal — frontend sees identical JSON.

---

## ingest.py Menu

```
  Setup:
    [I] Inicializar base de datos (schema + índices + usuarios)
    [D] Seedear datos demo (10K/100K/1M/8M)
  Datos:
    [1] Ver estado de la base de datos
    [2] Cargar dataset (CSV via ingest_config.py)
    [3] Limpiar periodo específico
    [4] Limpiar TODAS las tablas de datos
  Pipeline:
    [5] Crear tablas materializadas (mv_cross + mv_resumen)
    [6] Refrescar tablas materializadas
    [7] Ver estado de tablas materializadas
    [8] Limpiar índices redundantes
  Verificación:
    [C] Check consistencia DB ↔ MVs ↔ API
  Servicios:
    [S] Servir dashboard (Flask dev)
    [0] Salir
```

Removed: `[9] Verificar conexión Redis`.

Consistency check validates invariants 1-3 from above against live data.

---

## Implementation Tasks

### Task 1: create_matviews.py — CURRENT
- [ ] Replace `MATVIEWS` list with `TABLES` list: `[(name, create_sql, indexes), ...]`
- [ ] Use exact mv_cross and mv_resumen SQL from this document
- [ ] Remove `REFRESH_FUNCTION` constant and its execution
- [ ] Drop logic: `DROP MATERIALIZED VIEW IF EXISTS` for 11 legacy MVs, `DROP TABLE IF EXISTS` for new tables + shadows, `DROP FUNCTION IF EXISTS refresh_all_matviews()`
- [ ] Keep PERIODS_TABLE and PROVINCIAS_TABLE unchanged
- [ ] Update prints: "tablas materializadas"
- [ ] Verify: `seed --small && create` → mv_cross ~60-65K, mv_resumen ~14K
- [ ] Verify invariant 1: mv_resumen personas = raw distinct (zero diff all periods)

### Task 2: refresh_matviews.py
- [ ] Import `TABLES` from `create_matviews.py`
- [ ] Implement 3-step blue/green from this document
- [ ] Keep lookup refresh (TRUNCATE + INSERT for periods, provincias_lookup)
- [ ] ANALYZE: `["mv_cross", "mv_resumen", "periods", "provincias_lookup"]`
- [ ] Keep cache-clear POST (best-effort)
- [ ] Verify: swap < 5ms, row counts match create

### Task 3: queries.py
- [ ] Add `_use_resumen()` function from this document
- [ ] Add `table` parameter to `_cross_query()` (default None → auto-route)
- [ ] Rewrite `get_summary()`: agg_table routing, cant_programas from mv_cross, concentración from agg_table, cobertura=total_benef, tasaNoIdentificados=0
- [ ] Fix `get_by_sexo()`: CASE expression for label, GROUP BY sexo only, table routing
- [ ] Update `get_by_grupo_etario()`: table routing
- [ ] Update `get_evolucion()`: table routing, preserve grupo_etario exclusion
- [ ] Update module docstring
- [ ] Verify all API response shapes unchanged

### Task 4: app.py + .gitignore
- [ ] SimpleCache → FileSystemCache (code from this document)
- [ ] Health endpoint: `information_schema.tables` for mv_cross + mv_resumen, report `{count}/2`
- [ ] Add `.cache/` to `.gitignore`
- [ ] Verify: health returns `{"matviews": "2/2", "cache": "FileSystemCache"}`

### Task 5: ingest.py
- [ ] Delete `_check_redis()` function
- [ ] Remove menu option [9] and handler
- [ ] Update menu text for [5][6][7]
- [ ] Update `_matviews_status()`: query `information_schema.tables` not `pg_matviews`
- [ ] Rewrite `_check_consistency()`: validate invariants 1-3

### Task 6: CLAUDE.md
- [ ] 2 physical tables (not 11 MVs)
- [ ] FileSystemCache (not Redis/SimpleCache)
- [ ] Blue/green refresh
- [ ] `_use_resumen()` routing
- [ ] Menu without [9]
- [ ] Remove REDIS_URL from env vars

### Dependencies

```
Task 1 ──→ Task 2 (imports TABLES)
Task 1 ──→ Task 3 (table schema)
Task 1 ──→ Task 4 (table names)
Tasks 1-4 ──→ Task 5
Tasks 1-5 ──→ Task 6
```

Tasks 2, 3, 4 can run in parallel after Task 1.

---

## End-to-End Verification

```bash
python seed_pg.py --small
python create_matviews.py
python refresh_matviews.py
python app.py &
curl -s localhost:5000/api/health/api | python -m json.tool
```

Expected health: `{"matviews": "2/2", "cache": "FileSystemCache", "status": "ok"}`

Verify invariants:
```sql
-- Invariant 1: zero diff all periods
SELECT r.periodo_mes, r.mv_p, raw.raw_p, r.mv_p - raw.raw_p AS diff
FROM (SELECT periodo_mes, SUM(personas) AS mv_p FROM mv_resumen GROUP BY 1) r
JOIN (SELECT periodo_mes, COUNT(DISTINCT beneficiary_id) AS raw_p
      FROM benefits WHERE estado_beneficio='ACTIVO' AND beneficiary_id IS NOT NULL
      GROUP BY 1) raw ON r.periodo_mes = raw.periodo_mes ORDER BY 1;

-- Invariant 2: cross-table beneficios match
SELECT c.periodo_mes, c.b AS cross_b, r.b AS resumen_b, c.b - r.b AS diff
FROM (SELECT periodo_mes, SUM(beneficios) AS b FROM mv_cross GROUP BY 1) c
JOIN (SELECT periodo_mes, SUM(beneficios) AS b FROM mv_resumen GROUP BY 1) r
ON c.periodo_mes = r.periodo_mes ORDER BY 1;

-- Invariant 3: inflation exists
SELECT c.periodo_mes, c.p AS cross_p, r.p AS resumen_p,
       ROUND((c.p - r.p)::numeric / r.p * 100, 1) AS inflation_pct
FROM (SELECT periodo_mes, SUM(personas) AS p FROM mv_cross GROUP BY 1) c
JOIN (SELECT periodo_mes, SUM(personas) AS p FROM mv_resumen GROUP BY 1) r
ON c.periodo_mes = r.periodo_mes ORDER BY 1;
```
