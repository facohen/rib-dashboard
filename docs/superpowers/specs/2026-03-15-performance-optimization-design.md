# Performance Optimization — RUB Dashboard

## Context

Dashboard tarda ~80s con filtros. La arquitectura actual tiene 11 MVs separadas + Redis opcional — innecesariamente compleja. La concentración (1/2/3+ prestaciones) cae a raw SQL porque mv_cross no tiene esa dimensión. Se anticipan dimensiones nuevas (incompatibilidades, hijos menores/mayores, progenitor único) que harían explotar la cantidad de MVs individuales.

**Objetivo:** Simplificar a una sola `mv_cross` expandida (con `cant_prestaciones`) + SimpleCache. Eliminar Redis. Eliminar las 10 MVs individuales.

**Target:** Dashboard <2s con cualquier combinación de filtros. Zero dependencias externas más allá de PostgreSQL.

**Escape hatch:** Si las dimensiones crecen al punto de que la MV explota en filas (>500K), migrar el motor analítico a DuckDB read-only manteniendo PostgreSQL para OLTP. DuckDB escanea 500M filas en 1-3s sin materializar — elimina la necesidad de pre-computar combinaciones.

**Qué no cambia:** Connection pooling (`config.py`), frontend (`dashboard.html`), nominal (tablas raw con índices), lookups (periods, provincias_lookup), chatbot, índices en `seed_pg.py`.

---

## MV única: `mv_cross`

### Dimensiones (8)

| Dimensión | Cardinalidad | Fuente |
|-----------|-------------|--------|
| `periodo_mes` | 12 | `benefits.periodo_mes` |
| `nombre_programa` | ~8 | `programs.nombre_programa` |
| `secretaria_origen` | ~3 | `programs.secretaria_origen` |
| `provincia` | ~23 | `beneficiaries.provincia` |
| `sexo` | 4 (M/F/X/NI) | `beneficiaries.sexo` |
| `grupo_etario` | 6 | Calculado: 0-4, 5-12, 13-17, 18-29, 30-59, 60+ |
| `sexo_label` | 4 | Derivado de `sexo` (display only, no multiplica filas) |
| `cant_prestaciones` | 3 | Calculado: '1', '2', '3+' |

### Agregados (3)

| Columna | Expresión |
|---------|-----------|
| `personas` | `COUNT(DISTINCT beneficiary_id)` |
| `beneficios` | `COUNT(*)` |
| `montos` | `COALESCE(SUM(monto_prestacion), 0)` |

### Estimación

~43K filas actuales (sin cant_prestaciones) x3 = **~100-130K filas, <10MB**

### SQL

```sql
CREATE MATERIALIZED VIEW mv_cross AS
WITH benef_prog_count AS (
    SELECT beneficiary_id, periodo_mes,
           COUNT(DISTINCT program_id) AS cant_prog
    FROM benefits
    WHERE estado_beneficio = 'ACTIVO' AND beneficiary_id IS NOT NULL
    GROUP BY beneficiary_id, periodo_mes
),
-- Pre-agregar pagos para evitar fan-out (1 benefit con N pagos inflaría COUNT/SUM)
payment_agg AS (
    SELECT beneficiary_id, program_id, periodo_mes,
           SUM(monto_prestacion) AS total_monto
    FROM payments
    GROUP BY beneficiary_id, program_id, periodo_mes
),
base AS (
    SELECT b.periodo_mes, p.nombre_programa, p.secretaria_origen,
           ben.provincia, ben.sexo,
           CASE
             WHEN EXTRACT(YEAR FROM AGE(
                 (SUBSTRING(b.periodo_mes FROM 1 FOR 4) || '-' ||
                  SUBSTRING(b.periodo_mes FROM 6 FOR 2) || '-01')::date,
                 ben.fecha_nacimiento
             )) <= 4  THEN '0-4 años'
             WHEN EXTRACT(YEAR FROM AGE(
                 (SUBSTRING(b.periodo_mes FROM 1 FOR 4) || '-' ||
                  SUBSTRING(b.periodo_mes FROM 6 FOR 2) || '-01')::date,
                 ben.fecha_nacimiento
             )) <= 12 THEN '5-12 años'
             WHEN EXTRACT(YEAR FROM AGE(
                 (SUBSTRING(b.periodo_mes FROM 1 FOR 4) || '-' ||
                  SUBSTRING(b.periodo_mes FROM 6 FOR 2) || '-01')::date,
                 ben.fecha_nacimiento
             )) <= 17 THEN '13-17 años'
             WHEN EXTRACT(YEAR FROM AGE(
                 (SUBSTRING(b.periodo_mes FROM 1 FOR 4) || '-' ||
                  SUBSTRING(b.periodo_mes FROM 6 FOR 2) || '-01')::date,
                 ben.fecha_nacimiento
             )) <= 29 THEN '18-29 años'
             WHEN EXTRACT(YEAR FROM AGE(
                 (SUBSTRING(b.periodo_mes FROM 1 FOR 4) || '-' ||
                  SUBSTRING(b.periodo_mes FROM 6 FOR 2) || '-01')::date,
                 ben.fecha_nacimiento
             )) <= 59 THEN '30-59 años'
             ELSE '60+ años'
           END AS grupo_etario,
           CASE ben.sexo WHEN 'M' THEN 'Masculino' WHEN 'F' THEN 'Femenino'
                         WHEN 'X' THEN 'No binario' ELSE 'No informado' END AS sexo_label,
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
    WHERE b.estado_beneficio = 'ACTIVO'
)
SELECT periodo_mes, nombre_programa, secretaria_origen,
       provincia, sexo, grupo_etario, sexo_label, cant_prestaciones,
       COUNT(DISTINCT beneficiary_id) AS personas,
       COUNT(*) AS beneficios,
       COALESCE(SUM(total_monto), 0) AS montos
FROM base
GROUP BY periodo_mes, nombre_programa, secretaria_origen,
         provincia, sexo, grupo_etario, sexo_label, cant_prestaciones;
```

### Índices

```sql
CREATE UNIQUE INDEX ON mv_cross(periodo_mes, nombre_programa, secretaria_origen, provincia, sexo, grupo_etario, cant_prestaciones);
CREATE INDEX ON mv_cross(periodo_mes);
CREATE INDEX ON mv_cross(periodo_mes, nombre_programa);
CREATE INDEX ON mv_cross(periodo_mes, provincia);
CREATE INDEX ON mv_cross(periodo_mes, secretaria_origen);
CREATE INDEX ON mv_cross(periodo_mes, cant_prestaciones);
```

### Todo el dashboard se deriva de esta MV

| Indicador | Query |
|-----------|-------|
| Total beneficiarios | `SUM(personas) WHERE periodo` |
| Total beneficios | `SUM(beneficios) WHERE periodo` |
| Monto total | `SUM(montos) WHERE periodo` |
| Monto promedio por beneficiario | `SUM(montos) / SUM(personas)` |
| Monto promedio por beneficio | `SUM(montos) / SUM(beneficios)` |
| Promedio prestaciones por persona | `SUM(beneficios) / SUM(personas)` |
| Cantidad programas | `COUNT(DISTINCT nombre_programa)` |
| Monto promedio por programa | `SUM(montos) / COUNT(DISTINCT nombre_programa)` |
| Por programa/secretaría/provincia/sexo/etario | `GROUP BY dimensión WHERE periodo` |
| Evolución temporal | `GROUP BY periodo_mes` (sin filtro periodo) |
| Concentración | `GROUP BY cant_prestaciones WHERE periodo` |
| Filtros cruzados | WHERE clauses adicionales en cualquier dimensión |
| Incompatibilidades | Query directa sobre benefits (no MV) |
| Departamento (top 10) | Raw SQL fallback — no está en mv_cross (demasiados valores explosionarían dimensiones) |

**Mapeo de campos legacy en `get_summary()`:** `cobertura` → `SUM(personas)`, `tasaNoIdentificados` → 0 (artefacto del generador demo, eliminado). No hay `mv_cobertura` — todo sale de `mv_cross`.

### Extensibilidad

Para agregar una dimensión nueva (ej: `progenitor_unico`):
1. Agregar columna al CTE `base`
2. Agregar al `GROUP BY`
3. Actualizar índice único
4. `queries.py` no cambia si se usa `_cross_query()`

Cuando las filas superen ~500K, evaluar DuckDB como motor analítico.

### Refresh

```sql
CREATE OR REPLACE FUNCTION refresh_all_matviews() RETURNS void AS $$
BEGIN
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_cross;
END;
$$ LANGUAGE plpgsql;
```

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

## Cache: SimpleCache

```python
cache_config = {
    "CACHE_TYPE": "SimpleCache",
    "CACHE_DEFAULT_TIMEOUT": 3600,
}
```

- Sin Redis — zero dependencias externas
- Con MV respondiendo en <10ms, el cache es safety net, no necesidad
- Per-process (no compartido entre workers Gunicorn) — aceptable porque las queries son baratas
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
    [5] Crear vista materializada (mv_cross + lookups)
    [6] Refrescar vista materializada
    [7] Ver estado de matview
    [8] Limpiar índices redundantes
  Verificación:
    [C] Check consistencia DB ↔ MV ↔ API
  Servicios:
    [S] Servir dashboard (Flask dev)
    [0] Salir
```

### Cambios vs arquitectura actual

- **Eliminado:** `[9] Verificar conexión Redis` (no hay Redis)
- **Actualizado:** [5][6][7] texto en singular ("vista materializada")
- **Actualizado:** Consistencia valida contra mv_cross (no contra 11 MVs)

### Flujo E2E completo

```bash
python ingest.py
# [I] → schema creado
# [D] → seed 10K
# [5] → mv_cross + lookups creadas
# [C] → consistencia OK
# [S] → dashboard en localhost:5000
```

---

## Verificación

```sql
-- Filas en MV
SELECT COUNT(*) FROM mv_cross;          -- ~100-130K
SELECT COUNT(*) FROM periods;           -- 12
SELECT COUNT(*) FROM provincias_lookup; -- ~23

-- Indicadores derivados
SELECT SUM(personas), SUM(beneficios), SUM(montos)
FROM mv_cross WHERE periodo_mes = '2026-03';

-- Concentración
SELECT cant_prestaciones, SUM(personas)
FROM mv_cross WHERE periodo_mes = '2026-03' GROUP BY 1;

-- Consistencia MV vs raw
SELECT SUM(personas) FROM mv_cross WHERE periodo_mes = '2026-03';
-- debe coincidir con:
SELECT COUNT(DISTINCT beneficiary_id) FROM benefits
WHERE periodo_mes = '2026-03' AND estado_beneficio = 'ACTIVO'
  AND beneficiary_id IS NOT NULL;
```

---

## Archivos a modificar

| Archivo | Cambios |
|---------|---------|
| `create_matviews.py` | De 11 MVs a `mv_cross` expandida + lookups |
| `refresh_matviews.py` | Refresh solo mv_cross + lookups |
| `queries.py` | Simplificar: todo pasa por mv_cross, eliminar MVs individuales y raw fallbacks |
| `app.py` | Eliminar Redis, solo SimpleCache. Actualizar health endpoint (matviews count 11→1) |
| `requirements.txt` | Confirmar que `redis` no está (ya fue removido) |
| `ingest.py` | Quitar opción [9] Redis, actualizar textos, consistencia contra mv_cross |
| `CLAUDE.md` | Actualizar: 11 MVs→1, quitar referencias a Redis, actualizar comandos |

### Nota de transición

`create_matviews.py` debe hacer `DROP MATERIALIZED VIEW IF EXISTS` de las 10 MVs viejas antes de crear `mv_cross`. La stored function `refresh_all_matviews()` se sobreescribe con `CREATE OR REPLACE` (idempotente).
