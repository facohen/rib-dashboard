# Performance Optimization — RUB Dashboard

## Context

Dashboard tarda ~80s con filtros. La arquitectura actual tiene 11 MVs separadas + Redis opcional — innecesariamente compleja. La concentración (1/2/3+ prestaciones) cae a raw SQL porque mv_cross no tiene esa dimensión. Se anticipan dimensiones nuevas (incompatibilidades, hijos menores/mayores, progenitor único) que harían explotar la cantidad de MVs individuales.

**Objetivo:** Simplificar a `mv_cross` (por programa) + `mv_resumen` (personas deduplicadas) + FileSystemCache. Eliminar Redis. Eliminar las 10 MVs individuales.

**Target:** Dashboard <2s con cualquier combinación de filtros. Zero dependencias externas más allá de PostgreSQL.

**Escape hatch:** Si las dimensiones crecen al punto de que la MV explota en filas (>500K), migrar el motor analítico a DuckDB read-only manteniendo PostgreSQL para OLTP. DuckDB escanea 500M filas en 1-3s sin materializar — elimina la necesidad de pre-computar combinaciones.

**Qué no cambia:** Connection pooling (`config.py`), frontend (`dashboard.html`), nominal (tablas raw con índices), lookups (periods, provincias_lookup), chatbot, índices en `seed_pg.py`.

---

## Tablas materializadas: `mv_cross` + `mv_resumen`

### `mv_cross` — desglose por programa

### Dimensiones (7)

| Dimensión | Cardinalidad | Fuente |
|-----------|-------------|--------|
| `periodo_mes` | 12 | `benefits.periodo_mes` |
| `nombre_programa` | ~8 | `programs.nombre_programa` |
| `secretaria_origen` | ~3 | `programs.secretaria_origen` |
| `provincia` | ~23 | `beneficiaries.provincia` |
| `sexo` | 4 (M/F/X/NI) | `beneficiaries.sexo` |
| `grupo_etario` | 7 | Calculado: 0-4, 5-12, 13-17, 18-29, 30-59, 60+, Sin dato |
| `cant_prestaciones` | 3 | Calculado: '1', '2', '3+' |

`sexo_label` NO está en la tabla — se calcula en query time con `CASE sexo WHEN 'M' THEN 'Masculino' ...`. Esto mantiene el GROUP BY limpio y el índice único sin ambigüedad. **Migración:** El `mv_cross` actual tiene `sexo_label` como columna; `get_by_sexo()` hace `SELECT sexo_label AS label`. Hay que cambiar esa query para computar el label con CASE en vez de leer la columna. El response sigue devolviendo `label` — el frontend no cambia.

### Agregados (3)

| Columna | Expresión |
|---------|-----------|
| `personas` | `COUNT(*)` (pre-agregado por beneficiario — sin DISTINCT) |
| `beneficios` | `SUM(cant_benefits)` |
| `montos` | `COALESCE(SUM(total_monto), 0)` |

### Estimación

Producto cartesiano teórico: 12 × 8 × 3 × 23 × 4 × 7 × 3 = ~232K. En práctica muchas combinaciones no existen (sparse). **Estimación conservadora: ~130-180K filas, <10MB.** Validar con datos reales.

### SQL (creación inicial)

```sql
CREATE TABLE mv_cross AS
WITH
-- 1. Contar programas por beneficiario-periodo (para cant_prestaciones)
benef_prog_count AS (
    SELECT beneficiary_id, periodo_mes,
           COUNT(DISTINCT program_id) AS cant_prog
    FROM benefits
    WHERE estado_beneficio = 'ACTIVO' AND beneficiary_id IS NOT NULL
    GROUP BY beneficiary_id, periodo_mes
),
-- 2. Pre-agregar pagos para evitar fan-out (1 benefit con N pagos inflaría SUM)
payment_agg AS (
    SELECT beneficiary_id, program_id, periodo_mes,
           SUM(monto_prestacion) AS total_monto
    FROM payments
    GROUP BY beneficiary_id, program_id, periodo_mes
),
-- 3. Una fila por benefit, con dimensiones resueltas y monto pre-agregado
--    Edad se calcula una sola vez (no 6 veces en cada WHEN del CASE)
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
-- 4. Pre-agregar por beneficiario + dimensiones → elimina COUNT(DISTINCT)
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
-- 5. Agregar final: COUNT(*) = personas (ya es 1 fila por persona-programa), SUM = totales
SELECT periodo_mes, nombre_programa, secretaria_origen,
       provincia, sexo, grupo_etario, cant_prestaciones,
       COUNT(*) AS personas,
       SUM(cant_benefits) AS beneficios,
       SUM(person_monto) AS montos
FROM per_person
GROUP BY periodo_mes, nombre_programa, secretaria_origen,
         provincia, sexo, grupo_etario, cant_prestaciones;
```

**Por qué pre-agregar por beneficiario:** `COUNT(DISTINCT beneficiary_id)` requiere sort/hash sobre millones de filas. Con el CTE `per_person` ya hay 1 fila por persona-programa, así que `COUNT(*)` = personas sin DISTINCT. Esto acelera el refresh **3-5x**.

**Nota sobre double-counting:** Un beneficiario en AUH + PROGRESAR genera 2 filas en `mv_cross` (una por programa). `SUM(personas)` sin filtro de programa lo cuenta 2 veces. Esto es correcto para `get_by_programa()`, pero no para totales globales. Las queries que necesitan personas deduplicadas usan `mv_resumen` (ver sección siguiente).

### Índices

```sql
CREATE UNIQUE INDEX ON mv_cross(periodo_mes, nombre_programa, secretaria_origen, provincia, sexo, grupo_etario, cant_prestaciones);
-- sexo_label NO está en la tabla — se calcula en query time
CREATE INDEX ON mv_cross(periodo_mes);
CREATE INDEX ON mv_cross(periodo_mes, nombre_programa);
CREATE INDEX ON mv_cross(periodo_mes, provincia);
CREATE INDEX ON mv_cross(periodo_mes, secretaria_origen);
CREATE INDEX ON mv_cross(periodo_mes, cant_prestaciones);
```

### Todo el dashboard se deriva de estas tablas

| Indicador | Query | Tabla |
|-----------|-------|-------|
| Total beneficiarios | `SUM(personas) WHERE periodo` | `mv_resumen` |
| Total beneficios | `SUM(beneficios) WHERE periodo` | `mv_resumen` |
| Monto total | `SUM(montos) WHERE periodo` | `mv_resumen` |
| Monto promedio por beneficiario | `SUM(montos) / SUM(personas)` | `mv_resumen` |
| Monto promedio por beneficio | `SUM(montos) / SUM(beneficios)` | `mv_resumen` |
| Promedio prestaciones por persona | `SUM(beneficios) / SUM(personas)` | `mv_resumen` |
| Cantidad programas | `COUNT(DISTINCT nombre_programa)` | `mv_cross` |
| Monto promedio por programa | `SUM(montos) / COUNT(DISTINCT nombre_programa)` | `mv_cross` |
| Por programa | `GROUP BY nombre_programa` | `mv_cross` |
| Por secretaría | `GROUP BY secretaria_origen` | `mv_cross` |
| Por provincia/sexo/etario | `GROUP BY dimensión WHERE periodo` | `mv_resumen` |
| Evolución temporal | `GROUP BY periodo_mes` (sin filtro periodo) | `mv_resumen` |
| Concentración | `GROUP BY cant_prestaciones WHERE periodo` | `mv_resumen` |
| Filtros cruzados | WHERE clauses adicionales | según routing (*) |
| Incompatibilidades | Query directa sobre benefits (no MV) | raw |
| Departamento (top 10) | Raw SQL fallback — no está en las MVs | raw |

(*) **Routing de cross-filters:** Cuando hay un filtro de `programa` o `secretaria` activo, todas las queries usan `mv_cross` (el filtro ya restringe a un programa/secretaria, así que `SUM(personas)` es correcto sin dedup). Sin esos filtros, queries demográficas usan `mv_resumen`.

**Mapeo de campos legacy en `get_summary()`:** `cobertura` → `SUM(personas)`, `tasaNoIdentificados` → 0 (artefacto del generador demo, eliminado). No hay `mv_cobertura` — todo sale de `mv_resumen`/`mv_cross`.

### Extensibilidad

Para agregar una dimensión nueva (ej: `progenitor_unico`):
1. Agregar columna al CTE `benefit_enriched`
2. Agregar al `GROUP BY` de ambas tablas (`mv_cross` y `mv_resumen`)
3. Actualizar índices únicos
4. `queries.py` no cambia si se usa `_cross_query()`

Cuando las filas superen ~500K, evaluar DuckDB como motor analítico.

### Refresh: patrón Blue/Green

En vez de `REFRESH MATERIALIZED VIEW CONCURRENTLY` (lockea lectores, consume CPU en la DB transaccional), se usa una tabla física con intercambio atómico **desde Python** (`refresh_matviews.py`):

```python
# refresh_matviews.py (lógica principal)

# 1. Crear shadow tables (transacción larga pero no bloquea lectores)
cur.execute("DROP TABLE IF EXISTS mv_cross_new")
cur.execute("CREATE TABLE mv_cross_new AS ...")  # misma query que create_matviews.py
cur.execute("DROP TABLE IF EXISTS mv_resumen_new")
cur.execute("CREATE TABLE mv_resumen_new AS ...")
conn.commit()

# 2. Crear índices (transacción separada)
cur.execute("CREATE UNIQUE INDEX ON mv_cross_new(...)")
cur.execute("CREATE INDEX ON mv_cross_new(periodo_mes)")
cur.execute("CREATE UNIQUE INDEX ON mv_resumen_new(...)")
cur.execute("CREATE INDEX ON mv_resumen_new(periodo_mes)")
# etc.
conn.commit()

# 3. Swap atómico de ambas tablas (transacción cortísima — ~1ms)
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

**Por qué Python y no plpgsql:** Una stored function `plpgsql` mantiene una sola transacción abierta durante todo el `CREATE TABLE AS` (minutos de lectura sobre 200M filas). Con Python, cada paso es una transacción separada — el paso 1 (lento) no bloquea el swap, y el paso 3 (atómico) es instantáneo.

**Atomicidad del swap:** Los 8 renames (4 por tabla) van en una sola transacción, así que ambas tablas se actualizan atómicamente. Sin embargo, `get_summary()` hace 2 sub-queries (una a `mv_resumen`, otra a `mv_cross`). Si usan conexiones distintas del pool, podrían ver versiones inconsistentes durante el swap (~1ms window). Mitigación: `get_summary()` debe ejecutar ambas sub-queries en la misma conexión.

**Ventajas vs REFRESH CONCURRENTLY:**
- Zero downtime para lectores — el dashboard nunca ve datos parciales
- No requiere UNIQUE INDEX previo (requisito de CONCURRENTLY)
- La tabla shadow se construye sin afectar queries en curso
- El swap es un rename atómico (~1ms)
- No hay stored function que mantener — toda la lógica está en Python

**Nota:** `mv_cross` y `mv_resumen` no son `MATERIALIZED VIEW` — son tablas físicas regulares. `create_matviews.py` las crea inicialmente como tablas. Los nombres con prefijo `mv_` se mantienen por convención. No hay stored function — el refresh es 100% Python.

### `mv_resumen` — personas deduplicadas

Tabla complementaria sin dimensión de programa. Cada beneficiario aparece exactamente 1 vez por periodo, independientemente de cuántos programas tenga.

#### Dimensiones (5)

| Dimensión | Cardinalidad | Fuente |
|-----------|-------------|--------|
| `periodo_mes` | 12 | `benefits.periodo_mes` |
| `provincia` | ~23 | `beneficiaries.provincia` |
| `sexo` | 4 (M/F/X/NI) | `beneficiaries.sexo` |
| `grupo_etario` | 7 | Calculado: 0-4, 5-12, 13-17, 18-29, 30-59, 60+, Sin dato |
| `cant_prestaciones` | 3 | Calculado: '1', '2', '3+' |

#### Agregados (3)

| Columna | Expresión |
|---------|-----------|
| `personas` | `COUNT(*)` (1 fila por persona en `per_person_total` — deduplicada) |
| `beneficios` | `SUM(cant_benefits)` (total cross-programa por persona) |
| `montos` | `SUM(person_monto)` (total cross-programa por persona) |

#### Estimación

12 periodos × 23 provincias × 4 sexo × 7 etario × 3 cant = **~23K filas máximo, <1MB**

#### SQL (creación inicial)

Reutiliza los mismos CTEs que `mv_cross` (benef_prog_count, payment_agg, benefit_enriched, per_person). Agrega un CTE extra que colapsa programas:

```sql
CREATE TABLE mv_resumen AS
WITH
-- CTEs 1-4: idénticos a mv_cross (benef_prog_count, payment_agg, benefit_enriched, per_person)
...,
-- 5. Colapsar programas: 1 fila por beneficiario con totales cross-programa
per_person_total AS (
    SELECT periodo_mes, provincia, sexo, grupo_etario, cant_prestaciones,
           beneficiary_id,
           SUM(cant_benefits) AS cant_benefits,
           SUM(person_monto) AS person_monto
    FROM per_person
    GROUP BY periodo_mes, provincia, sexo, grupo_etario, cant_prestaciones, beneficiary_id
)
-- 6. Agregar final: COUNT(*) = personas únicas
SELECT periodo_mes, provincia, sexo, grupo_etario, cant_prestaciones,
       COUNT(*) AS personas,
       SUM(cant_benefits) AS beneficios,
       SUM(person_monto) AS montos
FROM per_person_total
GROUP BY periodo_mes, provincia, sexo, grupo_etario, cant_prestaciones;
```

**Por qué funciona:** `per_person_total` reagrupa `per_person` (ya en memoria como CTE) eliminando programa/secretaria. Un beneficiario en AUH + PROGRESAR que tenía 2 filas en `per_person` queda con 1 fila en `per_person_total`, sumando sus benefits y montos de ambos programas. No hay scan adicional de tablas.

**Consistencia cross-tabla:** `SUM(beneficios)` y `SUM(montos)` dan el mismo total en ambas tablas — los benefits/montos son aditivos. Solo `SUM(personas)` difiere: `mv_resumen` tiene el total correcto (deduplicado), `mv_cross` tiene el inflado por programa.

#### Índices

```sql
CREATE UNIQUE INDEX ON mv_resumen(periodo_mes, provincia, sexo, grupo_etario, cant_prestaciones);
CREATE INDEX ON mv_resumen(periodo_mes);
```

#### Routing en `queries.py`

```python
def _use_resumen(filters, group_col):
    """Decide si usar mv_resumen (dedup) o mv_cross (por programa)."""
    # Siempre mv_cross para desglose por programa o secretaria
    if group_col in ("nombre_programa", "secretaria_origen"):
        return False
    # Si hay filtro de programa/secretaria activo, mv_cross
    # (el filtro ya restringe a un programa → personas es correcto)
    if filters and (filters.get("programa") or filters.get("secretaria")):
        return False
    return True
```

`get_summary()` hace 2 queries: personas/beneficios/montos/concentración desde `mv_resumen`, `COUNT(DISTINCT nombre_programa)` desde `mv_cross`. Ambas sub-milisegundo sobre tablas de <130K filas.

---

## Generador de datos (`seed_pg.py`)

Genera datos realistas para desarrollo y testing a 4 escalas. Cada beneficiario genera ~1.5 benefits por periodo x 12 periodos, más pagos correspondientes.

### Escalas

| Flag | Beneficiarios | Benefits estimados | Tiempo aprox |
|------|--------------|-------------------|-------------|
| `--small` | 10K | ~240K | ~12s |
| `--medium` | 100K | ~2.4M | ~2min |
| `--1m` | 1M | ~24M | ~5min |
| (default) | 8M | ~200M | ~30min |

### Distribuciones

**Geográfica (23 provincias):** Pesos poblacionales reales — Buenos Aires 38%, Córdoba/Santa Fe 8% c/u, bajando hasta Tierra del Fuego 0.5%. Cada provincia tiene departamentos reales con códigos INDEC.

**Demográfica:** 4 grupos de generación de edad (niñez 0-12: 20%, jóvenes 13-29: 30%, adultos 30-59: 35%, mayores 60-85: 15%) que alimentan `birth_date()` con edades continuas. La MV luego las agrupa en 6 buckets más finos (0-4, 5-12, 13-17, 18-29, 30-59, 60+) — no es un mapeo 1:1. Sexo con sesgo femenino como en programas sociales reales: pool `[M,M,M,F,F,F,F,F,X,NI]`.

**Concentración de prestaciones:** 50% con 1 programa, 30% con 2, 15% con 3+, 5% inválidos (sin beneficiary_id — artefacto de testing, no presente en DB real).

**Incompatibilidades forzadas:** 2% de beneficiarios por periodo reciben pares incompatibles:
- AUH ↔ Pensiones no Contributivas
- PROGRESAR ↔ Potenciar Trabajo
- Alimentar ↔ Hacemos Futuro
- ARGENTA ↔ Potenciar Trabajo

**Montos:** Base por secretaría ($80K / $95K / $60K) ± variación aleatoria de -$10K a +$20K.

**Estado:** 92% ACTIVO, 8% INACTIVO.

### Decisiones de diseño

- `random.seed(42)` — resultados reproducibles
- Batch insert de 10K filas con `execute_values` — óptimo para psycopg2
- CUIL determinístico por índice: prefijo cíclico `[20,23,24,27]` + padding
- Índices se crean DESPUÉS del bulk insert (más rápido)
- `--schema-only` para inicializar schema + usuarios sin datos
- 8 programas distribuidos en 3 secretarías
- 12 periodos: 2025-04 a 2026-03
- Nombres/apellidos argentinos reales (~36 nombres M, ~36 F, ~45 apellidos)

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

Se usa un directorio relativo al proyecto (`.cache/`) en vez de `/tmp` para que el cache sobreviva restarts del host. Agregar `.cache/` a `.gitignore`.

- Sin Redis — zero dependencias externas
- Compartido entre todos los workers de Gunicorn (a diferencia de SimpleCache que es per-process)
- Elimina "parpadeo" de números en el dashboard cuando hay múltiples workers
- Con MV respondiendo en <10ms, el cache es safety net, no necesidad. Cache stampede (N workers regenerando simultáneamente al expirar) es inofensivo a <10ms; si crece, agregar jitter al TTL (±60s)
- Endpoints nominales NO se cachean (PII)
- `POST /api/admin/clear-cache` para invalidar

---

## Pipeline E2E y menú de `ingest.py`

### Menú

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
    [5] Crear tablas materializadas (mv_cross + mv_resumen + lookups)
    [6] Refrescar tablas materializadas
    [7] Ver estado de tablas materializadas
    [8] Limpiar índices redundantes
  Verificación:
    [C] Check consistencia DB ↔ MV ↔ API
  Servicios:
    [S] Servir dashboard (Flask dev)
    [0] Salir
```

### Cambios vs arquitectura actual

- **Eliminado:** `[9] Verificar conexión Redis` (no hay Redis)
- **Actualizado:** [5][6][7] texto para mv_cross + mv_resumen
- **Actualizado:** Consistencia valida contra mv_cross + mv_resumen (no contra 11 MVs)

### Flujo E2E completo

```bash
python ingest.py
# [I] → schema creado
# [D] → seed 10K
# [5] → mv_cross + mv_resumen + lookups creadas
# [C] → consistencia OK
# [S] → dashboard en localhost:5000
```

---

## Verificación

```sql
-- Filas en tablas
SELECT COUNT(*) FROM mv_cross;          -- ~130-180K
SELECT COUNT(*) FROM mv_resumen;        -- ~23K
SELECT COUNT(*) FROM periods;           -- 12
SELECT COUNT(*) FROM provincias_lookup; -- ~23

-- Indicadores derivados (personas deduplicadas desde mv_resumen)
SELECT SUM(personas), SUM(beneficios), SUM(montos)
FROM mv_resumen WHERE periodo_mes = '2026-03';

-- Concentración
SELECT cant_prestaciones, SUM(personas)
FROM mv_resumen WHERE periodo_mes = '2026-03' GROUP BY 1;

-- Consistencia: mv_resumen personas = raw distinct count
SELECT SUM(personas) FROM mv_resumen WHERE periodo_mes = '2026-03';
-- debe coincidir con:
SELECT COUNT(DISTINCT beneficiary_id) FROM benefits
WHERE periodo_mes = '2026-03' AND estado_beneficio = 'ACTIVO'
  AND beneficiary_id IS NOT NULL;

-- Consistencia cross-tabla: beneficios/montos deben coincidir (solo válido sin filtros)
SELECT SUM(beneficios) FROM mv_resumen WHERE periodo_mes = '2026-03';
-- debe coincidir con:
SELECT SUM(beneficios) FROM mv_cross WHERE periodo_mes = '2026-03';

-- mv_cross personas (inflado por diseño — para vistas por programa)
SELECT SUM(personas) FROM mv_cross WHERE periodo_mes = '2026-03';
-- >= total de mv_resumen (un beneficiario con 2 programas cuenta 2 veces aquí)
```

---

## Archivos a modificar

| Archivo | Cambios |
|---------|---------|
| `create_matviews.py` | De 11 MVs a tablas `mv_cross` + `mv_resumen` (blue/green) + lookups |
| `refresh_matviews.py` | Refresh blue/green de ambas tablas (shadow + swap atómico) + lookups |
| `queries.py` | Routing `_use_resumen()`, `get_summary()` con 2 queries, eliminar MVs individuales y raw fallbacks |
| `app.py` | Eliminar Redis, solo FileSystemCache. Actualizar health endpoint (matviews count 11→2) |
| `requirements.txt` | Confirmar que `redis` no está (ya fue removido) |
| `ingest.py` | Quitar opción [9] Redis, actualizar textos, consistencia contra mv_cross + mv_resumen |
| `CLAUDE.md` | Actualizar: 11 MVs→2 tablas, quitar referencias a Redis, actualizar comandos |

### Nota de transición

`create_matviews.py` debe hacer `DROP MATERIALIZED VIEW IF EXISTS` de las 10 MVs viejas y `DROP TABLE IF EXISTS mv_cross; DROP TABLE IF EXISTS mv_resumen` antes de crear las tablas nuevas. No hay stored function — el refresh es 100% Python en `refresh_matviews.py`.
