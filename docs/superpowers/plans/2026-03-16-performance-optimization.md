# Plan: Optimización de performance para 8M beneficiarios

**Fecha:** 2026-03-16/17
**Branch:** `refactor-mv-cross-resumen`
**Escala:** 8M beneficiarios, 160M benefits, 143M payments, 12 periodos

---

## Problema

Con 8M beneficiarios las MVs tardaban ~1h en crearse. Causas identificadas:
1. **Sin índice útil:** benefits no tenía índice por `(periodo_mes)` solo — todos incluían `estado_beneficio` que ya no filtramos en MVs. Cada subquery hacía Bitmap Heap Scan de 12M filas + Sort.
2. **3 CTEs materializadas redundantes:** `benef_prog_count`, `payment_agg`, `benefit_enriched` cada uno escaneaba benefits por separado.
3. **CREATE TABLE AS monolítico:** procesaba todos los periodos en una sola query gigante, forzando trabajo simultáneo con 12x los datos.
4. **Índices innecesarios:** 6 índices sobre tablas raw que ya no se usan (KPIs migrados a MVs).

---

## Cambios implementados

### Fase 1: Arquitectura de MVs (2026-03-16) ✅ COMMITTED

**Commits:** `e635800`, `6b88d28`, `f19b708`

#### 1a. CREATE TABLE AS → UNLOGGED + INSERT periodo por periodo
- Antes: `CREATE TABLE mv_cross AS (query con TODOS los periodos)`
- Después: `CREATE UNLOGGED TABLE` + `INSERT INTO mv_cross ... WHERE periodo_mes = %s` x12
- **Beneficio:** Reduce presión de memoria, permite progreso visible, commit por periodo

#### 1b. Tuning de sesión para queries pesadas
```sql
SET work_mem = '1GB';           -- default 4MB spilla a disco
SET maintenance_work_mem = '2GB';
SET max_parallel_workers_per_gather = 8;
```

#### 1c. Eliminación filtro estado_beneficio
- Antes: `WHERE estado_beneficio = 'ACTIVO'` en todas las CTEs
- Después: Sin filtro de estado — las MVs incluyen todos los beneficios
- **Razón:** estado_beneficio es un campo de negocio que el dashboard no filtra

#### 1d. UNION ALL para no-identificados
- Antes: beneficiary_id IS NULL se ignoraba silenciosamente
- Después: UNION ALL agrega filas con `provincia='Sin dato'`, `sexo='Sin dato'` etc.
- **Impacto:** Cierra la brecha entre totales de MVs y tablas raw

#### 1e. Eliminación query raw de departamento
- `get_by_departamento()` ahora retorna `[]` (departamento no está en MVs)
- Se eliminó `_apply_filters_raw()` y helpers asociados (~60 líneas)

#### 1f. Eliminación query de incompatibilidades
- `casosIncompatibilidad` ahora retorna 0 (costaba ~30s en raw con 8M)
- Se eliminó la query directa contra `incompatibility_rules + benefits x2`

#### 1g. Cleanup de queries.py
- Eliminados fallbacks try/except para `get_periods()` y `get_provincias()`
- Ya no hay queries contra tablas raw desde el dashboard

### Fase 2: Índices + queries optimizadas (2026-03-17) 🔧 PENDIENTE COMMIT

#### 2a. Nuevo covering index para MVs (`seed_pg.py`)
```sql
CREATE INDEX idx_benefits_periodo_benid_progid ON benefits(periodo_mes, beneficiary_id, program_id);
```
- **Ya creado en la DB live** (valid=True, 4.7GB)
- Cubre los subqueries de bpc y el scan principal con Index Only Scan
- Elimina Sort en bpc (index ya ordenado por beneficiary_id)

#### 2b. Simplificación de queries de MV (`create_matviews.py`)

**Antes (3 CTEs materializadas):**
```
benef_prog_count CTE → scan benefits WHERE periodo=%s (Bitmap Heap Scan + Sort)
payment_agg CTE      → scan payments WHERE periodo=%s
benefit_enriched CTE → scan benefits WHERE periodo=%s + JOIN con las 2 CTEs
per_person           → GROUP BY para deduplicar personas
```

**Después (1 CTE base + subquery joins):**
```
base CTE → scan benefits WHERE periodo=%s (Index Only Scan, sin Sort)
         + JOIN bpc subquery (Index Only Scan, sin Sort)
         + LEFT JOIN payments subquery (Index Scan)
per_person → GROUP BY para deduplicar personas
```

- **Key insight:** `COUNT(DISTINCT) OVER (PARTITION BY)` no existe en PostgreSQL. Se usa subquery join para bpc.
- Ambas queries (mv_cross y mv_resumen) usan la misma estructura optimizada.

#### 2c. Reducción de índices en `seed_pg.py`

**ELIMINADOS (6):**
| Índice | Razón |
|---|---|
| `idx_benefits_period_state_cuil` | No hay queries por cuil_raw en benefits |
| `idx_benefits_program_period` | Redundante con covering index |
| `idx_benefits_active_benid` | Parcial ACTIVO, cubierto por period_state_benid |
| `idx_ben_provincia` | Filtros van por MVs |
| `idx_ben_departamento` | Filtros van por MVs |
| `idx_ben_sexo` | Filtros van por MVs |
| `idx_ben_fecha_nacimiento` | Filtros van por MVs |

**MANTENIDOS (8):**
| Índice | Uso |
|---|---|
| `idx_benefits_periodo_benid_progid` | **NUEVO** — covering para MVs |
| `idx_benefits_period_state_benid` | Queries nominales + LLM con estado_beneficio |
| `idx_benefits_benid` | JOINs por beneficiary_id |
| `idx_ben_apellido_nombre` | Búsqueda nominal |
| `idx_ben_cuil` | Búsqueda nominal |
| `idx_ben_cuil_trgm` | Búsqueda fuzzy nominal |
| `idx_payments_period` | Filtro por periodo en payments |
| `idx_payments_compound` | JOIN en MVs |

#### 2d. Fix param count en `refresh_matviews.py`
- Antes: `cur.execute(shadow_insert, [p, p, p])` — hardcodeado 3 params
- Después: `n_params = shadow_insert.count('%s'); cur.execute(shadow_insert, [p] * n_params)`
- **Bug fix:** Las queries ahora tienen 4 params, el hardcoded 3 fallaba

---

## Resultados EXPLAIN (un periodo, 12M benefits)

### bpc subquery (COUNT DISTINCT programs por persona)

| Métrica | Antes | Después |
|---|---|---|
| Scan type | Bitmap Heap Scan | **Index Only Scan** |
| Sort | Sort 12M rows (3.4M cost) | **Eliminado** (index pre-sorted) |
| Costo total | 3,490,511 | **540,351 (6.5x mejor)** |

### Main benefits scan

| Métrica | Antes | Después |
|---|---|---|
| Scan type | Parallel Bitmap Heap Scan | **Parallel Index Only Scan** |
| Heap access | Sí (random I/O) | **No (index only)** |
| Costo | 1,772,601 | **376,349 (4.7x mejor)** |

### Query completa mv_cross

| Métrica | Antes | Después |
|---|---|---|
| Costo total estimado | 13,970,962 | **8,799,520 (37% menos)** |

> Nota: Los costos son estimados del planner. La mejora real en tiempo de ejecución debería ser mayor porque los costos subestiman el I/O del Bitmap Heap Scan.

---

## Pasos para aplicar

### Ya hecho:
- [x] Covering index creado en la DB live
- [x] Queries simplificadas en create_matviews.py
- [x] Índices reducidos en seed_pg.py
- [x] Param count arreglado en refresh_matviews.py
- [x] EXPLAIN verificado en mv_cross y mv_resumen

### Pendiente:
- [ ] Correr `python create_matviews.py` para medir tiempo real (estimado: ~20-30 min vs ~60 min anterior)
- [ ] Verificar consistencia: `ingest.py` opción `[C]` (check DB ↔ MVs ↔ API)
- [ ] Verificar dashboard funciona sin queries raw
- [ ] Commit cambios de Fase 2+3
- [ ] Eliminar índices innecesarios de la DB live (los que sacamos de seed_pg.py)

### Para eliminar índices viejos de la DB live:
```sql
DROP INDEX IF EXISTS idx_benefits_period_state_cuil;
DROP INDEX IF EXISTS idx_benefits_program_period;
DROP INDEX IF EXISTS idx_benefits_active_benid;
DROP INDEX IF EXISTS idx_ben_provincia;
DROP INDEX IF EXISTS idx_ben_departamento;
DROP INDEX IF EXISTS idx_ben_sexo;
DROP INDEX IF EXISTS idx_ben_fecha_nacimiento;
-- Libera ~20GB de espacio en disco
```

---

### Fase 3: Temp table compartida + covering index (2026-03-17) 🔧 PENDIENTE COMMIT

#### 3a. Temp table compartida en `create_matviews.py`
- **Antes:** Cada MV (mv_cross, mv_resumen) tenía su propia INSERT SQL con queries independientes → 2 scans completos de benefits/payments/beneficiaries por periodo
- **Después:** Una sola temp table `_pp` por periodo agrupa a nivel persona con todas las dimensiones. Luego:
  - `INSERT_CROSS_FROM_PP`: re-agrega `_pp` → mv_cross (solo lee temp table, ~instantáneo)
  - `INSERT_RESUMEN_FROM_PP`: deduplica personas y re-agrega → mv_resumen
  - `NULL_CROSS_SQL` / `NULL_RESUMEN_SQL`: inserta no-identificados (beneficiary_id IS NULL)
- **Beneficio:** Reduce scans de tablas raw a la mitad — 1 scan por periodo en vez de 2
- **Implementación:** `_process_period(cur, conn, period, cross_table, resumen_table)` encapsula todo el flujo

#### 3b. `refresh_matviews.py` adaptado a temp table compartida
- **Antes:** Iteraba por cada entrada en TABLES separadamente, cada una con su INSERT SQL propia
- **Después:** Importa `_apply_tuning` y `_process_period` de `create_matviews`. Crea ambas shadow tables vacías, luego procesa periodos con `_process_period(cur, conn, p, cross_table="mv_cross_new", resumen_table="mv_resumen_new")`
- **Beneficio:** Código DRY — misma lógica en create y refresh. Misma optimización de 1 scan por periodo.

#### 3c. Covering index en payments (`seed_pg.py`)
```sql
-- Antes (2 índices):
CREATE INDEX idx_payments_period ON payments(periodo_mes);
CREATE INDEX idx_payments_compound ON payments(beneficiary_id, program_id, periodo_mes);

-- Después (1 covering index):
CREATE INDEX idx_payments_covering ON payments(periodo_mes, beneficiary_id, program_id) INCLUDE (monto_prestacion);
```
- **Beneficio:** Un solo índice cubre tanto el filtro `WHERE periodo_mes = %s` como el `GROUP BY beneficiary_id, program_id, periodo_mes` con `SUM(monto_prestacion)` → **Index Only Scan** en el subquery de payments dentro de `_pp`
- **Ahorro:** Elimina un índice (~4-5GB menos en disco)

### Fase 4: Query optimization + covering indexes en MVs (2026-03-17) 🔧 PENDIENTE COMMIT

#### 4a. Eliminar CROSS JOIN LATERAL para cálculo de edad
- **Antes:** Cada fila (~16M por periodo) hacía `SUBSTRING + concatenación + ::date + AGE()` para construir la fecha del periodo
- **Después:** `period_date` se precalcula en Python y se pasa como parámetro `%s::date`
- **Impacto:** Elimina ~16M operaciones de string parsing por periodo (192M en total)

#### 4b. Integrar beneficiary_id IS NULL en la query principal
- **Antes:** 4 queries separadas (`NULL_CROSS_SQL`, `NULL_RESUMEN_SQL`) escaneaban benefits de nuevo para no-identificados → 2 scans extra por periodo = 24 scans adicionales
- **Después:** `LEFT JOIN beneficiaries` + `COALESCE(provincia, 'Sin dato')` integra NULLs en una sola query
- **Impacto:** Elimina `NULL_CROSS_SQL` y `NULL_RESUMEN_SQL` por completo. `_process_period` pasa de 10 líneas a 6.

#### 4c. Tuning de sesión más agresivo
- `work_mem`: 1GB → 2GB (evitar spill a disco en sorts/hashes)
- `maintenance_work_mem`: 2GB → 4GB (index builds más rápidos)
- Se mantiene `SET` (no `SET LOCAL`) porque `_process_period` hace commit por periodo

#### 4d. Covering indexes en MVs con INCLUDE
```sql
-- Todos los indexes secundarios de mv_cross y mv_resumen ahora incluyen:
INCLUDE (personas, beneficios, montos)
```
- **Beneficio:** Queries del dashboard hacen Index Only Scan en vez de Index Scan + heap lookup
- **Overhead despreciable:** mv_cross ~180K rows, mv_resumen ~23K rows

---

## Brainstorm: optimizaciones adicionales pendientes

### Paso 5: Paralelismo con ThreadPoolExecutor
- Procesar múltiples periodos en paralelo (cada uno en su propia conexión)
- Estimación: 3-4x speedup adicional (12 periodos ÷ 4 workers)
- Riesgo: presión de memoria — cada worker consume ~1GB de work_mem
- Requiere: tabla UNLOGGED (sin WAL) para evitar contención de I/O en escritura

### Paso 6: COPY para inserts en MVs
- Reemplazar `INSERT INTO ... SELECT` por `COPY FROM` con buffer en memoria
- Evita overhead del executor de PostgreSQL para inserts masivos
- Complejidad: requiere materializar resultados del SELECT en Python

### Paso 7: Particionamiento de benefits por periodo
- `CREATE TABLE benefits ... PARTITION BY LIST (periodo_mes)`
- Beneficio: partition pruning elimina 11/12 particiones en cada query
- Tradeoff: complejidad operacional en ingesta de datos
