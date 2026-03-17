# Spec: Optimización de queries de MVs

**Fecha:** 2026-03-17
**Branch:** `refactor-mv-cross-resumen`
**Scope:** Solo `create_matviews.py` y `refresh_matviews.py`. No se tocan tablas base ni schema.

---

## Problema

Con 8M beneficiarios y ~200M benefit rows, `create_matviews.py` tarda ~30min. La query principal (`TEMP_TABLE_SQL`) tiene overhead innecesario por fila, queries redundantes para NULLs, y el tuning de sesión es subóptimo.

## Cambios

### 1. Eliminar CROSS JOIN LATERAL para cálculo de edad

**Antes:** Cada fila (~16M por periodo) hace SUBSTRING + concatenación + cast para construir la fecha del periodo:
```sql
CROSS JOIN LATERAL (
    SELECT EXTRACT(YEAR FROM AGE(
        (SUBSTRING(b.periodo_mes FROM 1 FOR 4) || '-' ||
         SUBSTRING(b.periodo_mes FROM 6 FOR 2) || '-01')::date,
        ben.fecha_nacimiento
    ))::int AS edad
) calc
```

**Después:** Un parámetro `%s` precalculado en Python:
```sql
EXTRACT(YEAR FROM AGE(%s::date, ben.fecha_nacimiento))::int AS edad
```

**Impacto en código:**
- `_process_period()` calcula `period_date = f"{period[:4]}-{period[5:]}-01"` y lo pasa como parámetro
- El `%s::date` aparece en el CASE de grupo_etario (antes de los subqueries en el texto SQL), así que el orden de parámetros es: `[period_date, period, period, period]`
- Elimina el CROSS JOIN LATERAL y el alias `calc` — `edad` se calcula inline en el CASE

### 2. Integrar beneficiary_id IS NULL en la query principal

**Antes:** 4 queries separadas por periodo para no-identificados:
- `TEMP_TABLE_SQL` filtra `WHERE b.beneficiary_id IS NOT NULL`
- `NULL_CROSS_SQL` escanea benefits de nuevo para NULLs → inserta en mv_cross
- `NULL_RESUMEN_SQL` escanea benefits de nuevo para NULLs → inserta en mv_resumen
- Total: 2 scans extra de benefits por periodo = 24 scans adicionales en 12 periodos

**Después:** Una sola query que incluye todos los registros:
- Quitar `WHERE b.beneficiary_id IS NOT NULL`
- `LEFT JOIN beneficiaries` en vez de `JOIN beneficiaries` (para que NULLs no se pierdan)
- `LEFT JOIN` en el subquery `bpc` (mismo motivo)
- `COALESCE(ben.provincia, 'Sin dato')`, `COALESCE(ben.sexo, 'Sin dato')`, etc.
- Para NULLs sin fecha de nacimiento: `CASE WHEN ben.fecha_nacimiento IS NULL THEN 'Sin dato' ...` (ya existe)
- `cant_prestaciones = '1'` para NULLs (no tienen beneficiary_id para contar programas): `COALESCE(bpc.cant_prog, 1)`
- Eliminar `NULL_CROSS_SQL` y `NULL_RESUMEN_SQL` por completo
- `_process_period()` se simplifica (ya no ejecuta queries de NULLs)

**Impacto:** Elimina 24 scans de benefits en total. Simplifica `_process_period` de 10 líneas a 6.

### 3. Tuning de sesión más agresivo

**Antes:**
```python
SET work_mem = '1GB'
SET maintenance_work_mem = '2GB'
```

**Después:**
```python
SET work_mem = '2GB'
SET maintenance_work_mem = '4GB'
```

Se mantienen sin cambios: `max_parallel_workers_per_gather = 8`, `effective_cache_size = '16GB'`, `random_page_cost = '1.1'`, `effective_io_concurrency = '100'`, `temp_buffers = '256MB'`.

Justificación: server tiene 24GB RAM y 10 cores. Un solo worker (sin paralelismo), así que podemos dedicar más RAM a evitar spill a disco.

**Nota:** Se usa `SET` (no `SET LOCAL`) porque `_process_period` hace `conn.commit()` al final de cada periodo (para DROP del temp table ON COMMIT DROP). `SET LOCAL` resetearía los valores después del primer commit, causando que los 11 periodos restantes corran con `work_mem=4MB` (default). La conexión es de uso único en estos scripts, así que no hay riesgo de fuga.

### 4. Covering indexes en MVs con INCLUDE

Agregar `INCLUDE (personas, beneficios, montos)` a los indexes que usa el dashboard para queries, habilitando Index Only Scan.

**mv_cross — antes:**
```sql
CREATE INDEX ON mv_cross(periodo_mes)
CREATE INDEX ON mv_cross(periodo_mes, nombre_programa)
CREATE INDEX ON mv_cross(periodo_mes, provincia)
CREATE INDEX ON mv_cross(periodo_mes, secretaria_origen)
CREATE INDEX ON mv_cross(periodo_mes, cant_prestaciones)
```

**mv_cross — después:**
```sql
CREATE INDEX ON mv_cross(periodo_mes) INCLUDE (personas, beneficios, montos)
CREATE INDEX ON mv_cross(periodo_mes, nombre_programa) INCLUDE (personas, beneficios, montos)
CREATE INDEX ON mv_cross(periodo_mes, provincia) INCLUDE (personas, beneficios, montos)
CREATE INDEX ON mv_cross(periodo_mes, secretaria_origen) INCLUDE (personas, beneficios, montos)
CREATE INDEX ON mv_cross(periodo_mes, cant_prestaciones) INCLUDE (personas, beneficios, montos)
```

**mv_resumen — antes:**
```sql
CREATE INDEX ON mv_resumen(periodo_mes)
```

**mv_resumen — después:**
```sql
CREATE INDEX ON mv_resumen(periodo_mes) INCLUDE (personas, beneficios, montos)
```

**Trade-off:** Indexes más grandes en disco, pero mv_cross tiene ~180K rows y mv_resumen ~23K rows — el overhead es despreciable. La ganancia en queries del dashboard (Index Only Scan) justifica el espacio.

**Nota:** `refresh_matviews.py` importa `TABLES` de `create_matviews.py` y crea indexes en shadow tables usando esas definiciones, así que los INCLUDE se propagan automáticamente.

## Archivos modificados

| Archivo | Cambios |
|---|---|
| `create_matviews.py` | TEMP_TABLE_SQL (eliminar LATERAL, integrar NULLs, agregar param), `_apply_tuning` (nuevos valores work_mem/maintenance_work_mem), `_process_period` (calcular period_date, eliminar queries NULL), eliminar `NULL_CROSS_SQL` y `NULL_RESUMEN_SQL`, TABLES indexes (INCLUDE) |
| `refresh_matviews.py` | Ninguno — importa `_apply_tuning`, `_process_period` y `TABLES` de create_matviews; los cambios se propagan automáticamente |

## Lo que NO se toca

- Schema de tablas base (beneficiaries, benefits, payments)
- Subquery `bpc` (ya optimizado con covering index)
- Subquery `pa` (payments, ya optimizado)
- queries.py / app.py / config.py
- Sin paralelismo (un solo worker, máxima RAM)
- seed_pg.py (ya tiene el covering index de la fase anterior)

## Verificación

1. `python create_matviews.py` — medir tiempo (esperado: reducción de ~30min a ~20-25min)
2. `python ingest.py` → `[C]` check consistencia DB <> MVs <> API
3. Dashboard endpoints — verificar que siguen funcionando
