# Especificación de Base de Datos — RIB Dashboard
## Documento técnico para DBA

**Versión:** 1.0
**Fecha:** 2026-03-30
**Preparado por:** Equipo RIB Dashboard
**Destinatario:** Administrador de Base de Datos (DBA)

---

## 1. Descripción del sistema

El **RIB Dashboard** ("Registro Integral de Beneficiarios") es un sistema web de monitoreo de prestaciones sociales del Estado argentino. Permite visualizar, filtrar y analizar información sobre beneficiarios de programas sociales y los pagos asociados.

### Rol de la base de datos

La base de datos es el único componente de persistencia del sistema. La aplicación web (Flask, Python) se conecta a ella en modo **principalmente lectura**. Las escrituras ocurren únicamente durante el proceso de ingestión de datos (pipeline ETL separado que carga archivos CSV).

### Flujo general de datos

```
Archivos CSV (fuentes externas)
        ↓
  Pipeline ETL (ingest.py)
        ↓
  Base de datos PostgreSQL  ←── Este documento describe esta capa
        ↓
  Vistas materializadas (generadas automáticamente, no documentadas aquí)
        ↓
  Aplicación web (solo lectura)
        ↓
  Usuario final (dashboard)
```

> **Nota para el DBA:** Este documento cubre exclusivamente las **tablas de origen** (fuente de verdad). Las vistas materializadas (`mv_resumen`, `mv_cross`, etc.) son generadas por la aplicación a partir de estas tablas y **no deben crearse manualmente**.

---

## 2. Requisitos del servidor

### PostgreSQL

| Requisito | Valor |
|-----------|-------|
| Versión mínima | **PostgreSQL 14** (recomendado: 16 o superior) |
| Encoding | **UTF8** |
| Locale | `es_AR.UTF-8` o `en_US.UTF-8` (ambos funcionan) |
| Collation | `default` es aceptable |

### Extensiones requeridas

Deben instalarse **antes** de crear las tablas:

```sql
-- Búsqueda parcial por CUIL (trigrama). Requerida.
CREATE EXTENSION IF NOT EXISTS pg_trgm;
```

### Recursos estimados

| Recurso | Estimación |
|---------|------------|
| Espacio en disco | 50 GB – 500 GB según volumen de datos cargados |
| RAM recomendada | 8 GB mínimo, 16 GB recomendado |
| Conexiones concurrentes | < 20 (app web de baja concurrencia) |

---

## 3. Nomenclatura y convenciones

El esquema mezcla **inglés y español** de forma intencional:

- Tablas y columnas de infraestructura (usuarios, roles) → inglés: `users`, `email`, `password_hash`
- Tablas y columnas de dominio del negocio → español: `beneficiaries`, `periodo_mes`, `secretaria_id`
- No existe un esquema separado; todo está en el esquema `public` por defecto

### Campos críticos — no modificar el tipo sin consultar

| Campo | Tipo en DB | Por qué NO es otro tipo |
|-------|-----------|-------------------------|
| `periodo_mes` | `TEXT` | Formato fijo `YYYY-MM`. No es `DATE` para evitar ambigüedad de día y facilitar comparaciones exactas |
| `cuil` / `cuil_raw` / `cuil_titular` | `TEXT` | El CUIL puede tener ceros a la izquierda. Como `INTEGER` se perderían |
| `provincia` | `TEXT` | Normalizada sin tildes (`Cordoba`, no `Córdoba`). Hay ~40 variantes de fuente que se mapean a un valor canónico |
| `codigo_provincia_indec` | `TEXT` | Código de 2 dígitos. `06` como `INTEGER` sería `6`, rompiendo joins y filtros |

---

## 4. Conceptos de dominio

Antes de revisar las tablas es útil entender dos conceptos clave del negocio:

**TD — Titular de Derecho**
La persona que tiene derecho a recibir un beneficio social. Es el beneficiario real. Sus datos están en la tabla `beneficiaries` y se referencian desde `benefits` via `beneficiary_id`.

**TC — Titular de Cobro**
La persona que efectivamente cobra el dinero. Puede ser el mismo TD o un tercero (por ejemplo, la madre de un menor beneficiario). Sus datos están desnormalizados dentro de la tabla `benefits` en las columnas `*_titular`.

Esta distinción es fundamental: el sistema produce reportes separados por perspectiva TD y perspectiva TC.

---

## 5. Definición de tablas

### Prerequisito

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;
```

---

### 5.1 `secretarias`

Catálogo de secretarías de gobierno que agrupan programas sociales.
Tabla de referencia estática. Se carga una sola vez y rara vez cambia.

```sql
CREATE TABLE secretarias (
    id     SERIAL PRIMARY KEY,
    nombre TEXT UNIQUE NOT NULL
);
```

| Columna | Tipo | Restricciones | Descripción |
|---------|------|---------------|-------------|
| `id` | SERIAL | PK | ID autoincremental |
| `nombre` | TEXT | UNIQUE, NOT NULL | Nombre de la secretaría (ej: `"Ministerio de Desarrollo Social"`) |

**Volumen esperado:** ~5 filas
**Modificaciones en producción:** Inserción ocasional al incorporar nueva secretaría

---

### 5.2 `programs`

Catálogo de programas sociales. Cada programa pertenece a una secretaría.
Tabla de referencia estática. Se carga una sola vez y rara vez cambia.

```sql
CREATE TABLE programs (
    id               SERIAL PRIMARY KEY,
    secretaria_id    INTEGER NOT NULL REFERENCES secretarias(id),
    nombre_programa  TEXT NOT NULL
);
```

| Columna | Tipo | Restricciones | Descripción |
|---------|------|---------------|-------------|
| `id` | SERIAL | PK | ID autoincremental |
| `secretaria_id` | INTEGER | FK → `secretarias(id)`, NOT NULL | Secretaría a la que pertenece el programa |
| `nombre_programa` | TEXT | NOT NULL | Nombre del programa (ej: `"Asignación Universal por Hijo"`) |

**Volumen esperado:** ~10–30 filas
**Modificaciones en producción:** Inserción ocasional al incorporar nuevo programa

---

### 5.3 `beneficiaries`

Personas físicas beneficiarias (**Titulares de Derecho / TD**).
Es la tabla de personas más grande del sistema. Cada persona aparece **una sola vez**, identificada por su CUIL.

```sql
CREATE TABLE beneficiaries (
    id                       SERIAL PRIMARY KEY,
    cuil                     TEXT UNIQUE NOT NULL,
    nombre                   TEXT NOT NULL,
    apellido                 TEXT NOT NULL,
    sexo                     TEXT NOT NULL,
    fecha_nacimiento         DATE NOT NULL,
    provincia                TEXT NOT NULL,
    codigo_provincia_indec   TEXT NOT NULL,
    cp                       TEXT
);
```

| Columna | Tipo | Restricciones | Descripción |
|---------|------|---------------|-------------|
| `id` | SERIAL | PK | ID autoincremental |
| `cuil` | TEXT | UNIQUE, NOT NULL | CUIL del beneficiario — exactamente 11 dígitos como texto (ej: `"20123456789"`) |
| `nombre` | TEXT | NOT NULL | Nombre de pila |
| `apellido` | TEXT | NOT NULL | Apellido |
| `sexo` | TEXT | NOT NULL | Valores posibles: `M`, `F`, `X`, `NI` (NI = no informado) |
| `fecha_nacimiento` | DATE | NOT NULL | Fecha de nacimiento |
| `provincia` | TEXT | NOT NULL | Nombre de provincia normalizado, sin tildes (ej: `Cordoba`, `Buenos Aires`, `Entre Rios`) |
| `codigo_provincia_indec` | TEXT | NOT NULL | Código INDEC de 2 dígitos (ej: `06` = Buenos Aires, `14` = Córdoba) |
| `cp` | TEXT | nullable | Código postal — puede estar ausente |

**Índices — crear obligatoriamente:**

```sql
CREATE INDEX idx_ben_cuil             ON beneficiaries(cuil);
CREATE INDEX idx_ben_cuil_trgm        ON beneficiaries USING gin(cuil gin_trgm_ops);
CREATE INDEX idx_ben_apellido_nombre  ON beneficiaries(apellido, nombre);
```

> El índice `idx_ben_cuil_trgm` requiere la extensión `pg_trgm`. Permite búsqueda parcial de CUIL desde el dashboard (función "buscar por CUIL incompleto").

**Volumen esperado:** 1 millón – 8 millones de filas
**Modificaciones en producción:** INSERT masivo durante ingestión. No se eliminan filas.

---

### 5.4 `benefits`

Prestaciones otorgadas. **Tabla central y más voluminosa del sistema.**

Cada fila representa un beneficio asignado a una persona, en un programa específico, para un período de liquidación. Una misma persona puede tener múltiples filas si cobra en varios períodos o en varios programas.

Esta tabla contiene **dos identidades** por fila:
- Datos del **TD** (quien recibe): `beneficiary_id` → referencia a `beneficiaries`
- Datos del **TC** (quien cobra): columnas `*_titular` desnormalizadas en la misma fila

```sql
CREATE TABLE benefits (
    id                         SERIAL PRIMARY KEY,
    beneficiary_id             INTEGER REFERENCES beneficiaries(id),
    cuil_raw                   TEXT,
    program_id                 INTEGER NOT NULL REFERENCES programs(id),
    periodo_mes                TEXT NOT NULL,
    estado_beneficio           TEXT NOT NULL DEFAULT 'ACTIVO',
    cuil_titular               TEXT,
    nombre_titular             TEXT,
    apellido_titular           TEXT,
    sexo_titular               TEXT,
    fecha_nacimiento_titular   DATE,
    provincia_titular          TEXT
);
```

| Columna | Tipo | Restricciones | Descripción |
|---------|------|---------------|-------------|
| `id` | SERIAL | PK | ID autoincremental |
| `beneficiary_id` | INTEGER | FK → `beneficiaries(id)`, **nullable** | ID del beneficiario (TD). **NULL cuando el CUIL del CSV es inválido o está ausente** — estas filas se llaman "no identificadas" |
| `cuil_raw` | TEXT | nullable | CUIL original tal como vino en el archivo fuente. Se preserva como referencia cuando `beneficiary_id` es NULL |
| `program_id` | INTEGER | FK → `programs(id)`, NOT NULL | Programa al que pertenece la prestación |
| `periodo_mes` | TEXT | NOT NULL | Período de liquidación en formato `YYYY-MM` (ej: `2026-03`) |
| `estado_beneficio` | TEXT | NOT NULL, DEFAULT `'ACTIVO'` | Estado: `ACTIVO` o `INACTIVO` |
| `cuil_titular` | TEXT | nullable | CUIL del TC (Titular de Cobro). Para menores, es el CUIL del adulto responsable |
| `nombre_titular` | TEXT | nullable | Nombre del TC |
| `apellido_titular` | TEXT | nullable | Apellido del TC |
| `sexo_titular` | TEXT | nullable | Sexo del TC: `M`, `F`, `X` o `NI` |
| `fecha_nacimiento_titular` | DATE | nullable | Fecha de nacimiento del TC |
| `provincia_titular` | TEXT | nullable | Provincia del TC (mismo formato normalizado que `beneficiaries.provincia`) |

**Índices — crear obligatoriamente:**

```sql
CREATE INDEX idx_benefits_periodo_benid_progid    ON benefits(periodo_mes, beneficiary_id, program_id);
CREATE INDEX idx_benefits_period_state_benid      ON benefits(periodo_mes, estado_beneficio, beneficiary_id);
CREATE INDEX idx_benefits_benid                   ON benefits(beneficiary_id);
CREATE INDEX idx_benefits_periodo_cuil_titular    ON benefits(periodo_mes, cuil_titular) INCLUDE (program_id, beneficiary_id);
```

**Volumen esperado:** 10 millones – 200 millones de filas (N programas × M períodos × cantidad de personas)
**Modificaciones en producción:** INSERT masivo mensual al cargar nuevo período. No se eliminan filas históricas.

---

### 5.5 `payments`

Pagos efectivamente liquidados. Cada fila = un pago real acreditado a un beneficiario.

En la práctica, existe un pago por cada benefit activo por período. La diferencia conceptual con `benefits` es que `benefits` registra el derecho a cobrar, mientras que `payments` registra el cobro efectivo con monto y fecha.

```sql
CREATE TABLE payments (
    id                SERIAL PRIMARY KEY,
    beneficiary_id    INTEGER NOT NULL REFERENCES beneficiaries(id),
    program_id        INTEGER NOT NULL REFERENCES programs(id),
    fecha_pago        DATE NOT NULL,
    periodo_mes       TEXT NOT NULL,
    monto_prestacion  NUMERIC(12,2) NOT NULL
);
```

| Columna | Tipo | Restricciones | Descripción |
|---------|------|---------------|-------------|
| `id` | SERIAL | PK | ID autoincremental |
| `beneficiary_id` | INTEGER | FK → `beneficiaries(id)`, NOT NULL | Beneficiario que recibió el pago |
| `program_id` | INTEGER | FK → `programs(id)`, NOT NULL | Programa que originó el pago |
| `fecha_pago` | DATE | NOT NULL | Fecha en que se acreditó el pago |
| `periodo_mes` | TEXT | NOT NULL | Período liquidado en formato `YYYY-MM` |
| `monto_prestacion` | NUMERIC(12,2) | NOT NULL | Monto en pesos argentinos (ej: `15000.00`) |

**Índice — crear obligatoriamente:**

```sql
CREATE INDEX idx_payments_covering ON payments(periodo_mes, beneficiary_id, program_id) INCLUDE (monto_prestacion);
```

**Volumen esperado:** Similar a `benefits` (aproximadamente 1 pago por benefit activo)
**Modificaciones en producción:** INSERT masivo mensual al liquidar nuevo período.

---

### 5.6 `incompatibility_rules`

Reglas de incompatibilidad entre programas. Define qué pares de programas no pueden ser cobrados simultáneamente por la misma persona.

Se usa para detectar beneficiarios que figuran en más de un programa cuando eso no debería ser posible.

```sql
CREATE TABLE incompatibility_rules (
    id             SERIAL PRIMARY KEY,
    program_a_id   INTEGER NOT NULL REFERENCES programs(id),
    program_b_id   INTEGER NOT NULL REFERENCES programs(id),
    is_compatible  INTEGER NOT NULL DEFAULT 0,
    descripcion    TEXT
);
```

| Columna | Tipo | Restricciones | Descripción |
|---------|------|---------------|-------------|
| `id` | SERIAL | PK | ID autoincremental |
| `program_a_id` | INTEGER | FK → `programs(id)`, NOT NULL | Primer programa del par |
| `program_b_id` | INTEGER | FK → `programs(id)`, NOT NULL | Segundo programa del par |
| `is_compatible` | INTEGER | NOT NULL, DEFAULT `0` | `0` = incompatibles (no pueden coexistir). Reservado para futuro: `1` = compatibles |
| `descripcion` | TEXT | nullable | Descripción legible de la regla (ej: `"AUH es incompatible con Progresar"`) |

**Volumen esperado:** < 100 filas
**Modificaciones en producción:** Carga manual inicial. Cambios excepcionales.

---

### 5.7 `users`

Usuarios del sistema para autenticación en el dashboard web. **No contiene datos de beneficiarios.**

```sql
CREATE TABLE users (
    id             SERIAL PRIMARY KEY,
    email          TEXT UNIQUE NOT NULL,
    password_hash  TEXT NOT NULL,
    role           TEXT NOT NULL DEFAULT 'user',
    nombre         TEXT
);
```

| Columna | Tipo | Restricciones | Descripción |
|---------|------|---------------|-------------|
| `id` | SERIAL | PK | ID autoincremental |
| `email` | TEXT | UNIQUE, NOT NULL | Email usado para login |
| `password_hash` | TEXT | NOT NULL | Hash de la contraseña. Formato: `pbkdf2:sha256:...` (Werkzeug). Los hashes legacy en SHA256 puro se migran automáticamente al primer login |
| `role` | TEXT | NOT NULL, DEFAULT `'user'` | `admin` = acceso completo; `user` = solo lectura |
| `nombre` | TEXT | nullable | Nombre para mostrar en la interfaz |

**Volumen esperado:** < 50 filas
**Modificaciones en producción:** Alta/baja manual de usuarios.

---

## 6. Diagrama de relaciones

```
secretarias
    │
    │ 1:N
    ▼
programs ──────────────────────────┐
    │                              │
    │ 1:N                          │ 1:N
    ▼                              ▼
benefits ◄──── N:1 ──── beneficiaries
    │                              ▲
    │ (también)                    │
    └── program_id ──► payments ───┘ (beneficiary_id)

incompatibility_rules ──► programs (program_a_id, program_b_id)

users  (tabla independiente, solo autenticación)
```

---

## 7. Orden de creación

Las tablas deben crearse en este orden para respetar las claves foráneas:

```sql
-- 1. Extensión
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- 2. Catálogos (sin dependencias)
CREATE TABLE secretarias (...);
CREATE TABLE programs (...);

-- 3. Personas
CREATE TABLE beneficiaries (...);
-- Crear índices de beneficiaries aquí

-- 4. Tablas transaccionales
CREATE TABLE benefits (...);
-- Crear índices de benefits aquí

CREATE TABLE payments (...);
-- Crear índice de payments aquí

-- 5. Reglas
CREATE TABLE incompatibility_rules (...);

-- 6. Usuarios (independiente)
CREATE TABLE users (...);
```

---

## 8. Permisos recomendados

La aplicación web se conecta con un usuario dedicado (ej: `rib_app`). Permisos mínimos necesarios:

```sql
-- Crear usuario de aplicación
CREATE USER rib_app WITH PASSWORD 'contraseña_segura';

-- Lectura en todas las tablas
GRANT SELECT ON ALL TABLES IN SCHEMA public TO rib_app;

-- Escritura solo en tablas que el pipeline ETL necesita
GRANT INSERT, UPDATE ON beneficiaries, benefits, payments TO rib_app;
GRANT INSERT ON secretarias, programs, incompatibility_rules TO rib_app;

-- Secuencias (para SERIAL / autoincrement)
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO rib_app;

-- Permisos sobre vistas materializadas (cuando se creen)
-- GRANT SELECT ON mv_resumen, mv_cross, mv_resumen_tc, mv_cross_tc, mv_nominal_tc TO rib_app;
```

> **Recomendación de seguridad:** El usuario `rib_app` **no debe** tener permisos `DELETE` ni `DROP`. Los datos históricos son inmutables por diseño.

---

## 9. Consideraciones de performance

- **`benefits`** es la tabla más consultada y la más grande. Los índices listados en la sección 5.4 son **obligatorios** — sin ellos las consultas de dashboard tomarán minutos.
- **`beneficiaries`** requiere el índice trigrama (`gin_trgm_ops`) para la búsqueda por CUIL parcial. Sin él esa función del dashboard no funcionará.
- Las **vistas materializadas** son el mecanismo principal de performance para consultas agregadas. Se generan por separado después de la carga inicial de datos.
- `VACUUM` y `ANALYZE` periódicos son importantes dado el volumen de inserciones masivas mensuales.

---

## 10. Checklist de verificación para el DBA

Una vez creadas las tablas, verificar:

- [ ] Extensión `pg_trgm` instalada: `SELECT * FROM pg_extension WHERE extname = 'pg_trgm';`
- [ ] Las 7 tablas existen: `\dt` en psql
- [ ] Los índices existen: `\di` en psql
- [ ] Las claves foráneas están activas: `SELECT conname FROM pg_constraint WHERE contype = 'f';`
- [ ] El usuario de aplicación puede conectarse y hacer SELECT
- [ ] El encoding de la base es UTF8: `SHOW server_encoding;`
- [ ] No existen vistas materializadas (se crean post-carga): `SELECT matviewname FROM pg_matviews;` → debe retornar 0 filas

---

## 11. Preguntas frecuentes

**¿Por qué `beneficiary_id` es nullable en `benefits`?**
Porque los archivos CSV de origen contienen CUILes inválidos, duplicados o ausentes. Esas filas se cargan igual para no perder información, pero no se pueden asociar a un beneficiario válido. Representan un porcentaje menor del total y se reportan como "no identificados".

**¿Por qué los datos del TC están desnormalizados en `benefits` en lugar de una tabla separada?**
Simplificación de diseño para el volumen y casos de uso actuales. El TC no tiene identidad propia en el sistema; solo interesa en relación al benefit que cobra.

**¿Puede haber dos filas en `benefits` con el mismo `beneficiary_id` + `program_id` + `periodo_mes`?**
No debería, pero no hay constraint UNIQUE que lo impida actualmente. El pipeline ETL controla la deduplicación durante la carga.

**¿Qué es un "período"?**
Un mes de liquidación de beneficios, identificado como texto `YYYY-MM`. Cada vez que el Estado liquita un mes, se insertan nuevas filas en `benefits` y `payments` con ese período. Los datos históricos de períodos anteriores no se modifican.

---

*Documento generado a partir del código fuente del sistema RIB Dashboard v2026-03.*
