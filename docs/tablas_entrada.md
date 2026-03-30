# Tablas de Entrada — RIB Dashboard

Especificación de las tablas PostgreSQL que deben existir en la base de datos para que el dashboard funcione.

Fecha: 2026-03-27

---

## 1. `secretarias`

Catálogo de secretarías de gobierno que agrupan programas sociales.

```sql
CREATE TABLE secretarias (
    id SERIAL PRIMARY KEY,
    nombre TEXT UNIQUE NOT NULL
);
```

| Columna | Tipo | Restricciones | Descripción |
|---------|------|---------------|-------------|
| `id` | SERIAL | PK | ID autoincremental |
| `nombre` | TEXT | UNIQUE, NOT NULL | Nombre de la secretaría |

---

## 2. `programs`

Catálogo de programas sociales. Cada programa pertenece a una secretaría.

```sql
CREATE TABLE programs (
    id SERIAL PRIMARY KEY,
    secretaria_id INTEGER NOT NULL REFERENCES secretarias(id),
    nombre_programa TEXT NOT NULL
);
```

| Columna | Tipo | Restricciones | Descripción |
|---------|------|---------------|-------------|
| `id` | SERIAL | PK | ID autoincremental |
| `secretaria_id` | INTEGER | FK → `secretarias(id)`, NOT NULL | Secretaría a la que pertenece |
| `nombre_programa` | TEXT | NOT NULL | Nombre del programa |

---

## 3. `beneficiaries`

Personas físicas beneficiarias (Titulares de Derecho). Cada persona se identifica por su CUIL.

```sql
CREATE TABLE beneficiaries (
    id SERIAL PRIMARY KEY,
    cuil TEXT UNIQUE NOT NULL,
    nombre TEXT NOT NULL,
    apellido TEXT NOT NULL,
    sexo TEXT NOT NULL,
    fecha_nacimiento DATE NOT NULL,
    provincia TEXT NOT NULL,
    codigo_provincia_indec TEXT NOT NULL,
    cp TEXT
);
```

| Columna | Tipo | Restricciones | Descripción |
|---------|------|---------------|-------------|
| `id` | SERIAL | PK | ID autoincremental |
| `cuil` | TEXT | UNIQUE, NOT NULL | CUIL — 11 dígitos |
| `nombre` | TEXT | NOT NULL | Nombre de pila |
| `apellido` | TEXT | NOT NULL | Apellido |
| `sexo` | TEXT | NOT NULL | `M`, `F`, `X` o `NI` (no informado) |
| `fecha_nacimiento` | DATE | NOT NULL | Fecha de nacimiento |
| `provincia` | TEXT | NOT NULL | Provincia normalizada (sin tildes, ej: `Cordoba`) |
| `codigo_provincia_indec` | TEXT | NOT NULL | Código INDEC de provincia (2 dígitos, ej: `06`) |
| `cp` | TEXT | nullable | Código postal |

**Índices recomendados:**

```sql
CREATE INDEX idx_ben_cuil ON beneficiaries(cuil);
CREATE INDEX idx_ben_cuil_trgm ON beneficiaries USING gin(cuil gin_trgm_ops);  -- requiere: CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX idx_ben_apellido_nombre ON beneficiaries(apellido, nombre);
```

---

## 4. `benefits`

Prestaciones otorgadas. Cada fila = un beneficio asignado a una persona en un programa y periodo.

```sql
CREATE TABLE benefits (
    id SERIAL PRIMARY KEY,
    beneficiary_id INTEGER REFERENCES beneficiaries(id),
    cuil_raw TEXT,
    program_id INTEGER NOT NULL REFERENCES programs(id),
    periodo_mes TEXT NOT NULL,
    estado_beneficio TEXT NOT NULL DEFAULT 'ACTIVO',
    cuil_titular TEXT,
    nombre_titular TEXT,
    apellido_titular TEXT,
    sexo_titular TEXT,
    fecha_nacimiento_titular DATE,
    provincia_titular TEXT
);
```

| Columna | Tipo | Restricciones | Descripción |
|---------|------|---------------|-------------|
| `id` | SERIAL | PK | ID autoincremental |
| `beneficiary_id` | INTEGER | FK → `beneficiaries(id)`, **nullable** | ID del beneficiario. NULL = persona no identificada (CUIL inválido/ausente) |
| `cuil_raw` | TEXT | nullable | CUIL original tal como vino en el CSV. Se usa como referencia cuando `beneficiary_id` es NULL |
| `program_id` | INTEGER | FK → `programs(id)`, NOT NULL | Programa al que corresponde |
| `periodo_mes` | TEXT | NOT NULL | Periodo de liquidación, formato `YYYY-MM` (ej: `2026-03`) |
| `estado_beneficio` | TEXT | NOT NULL, DEFAULT 'ACTIVO' | `ACTIVO` o `INACTIVO` |
| `cuil_titular` | TEXT | nullable | CUIL del Titular de Cobro (TC) — puede ser el mismo beneficiario o un adulto responsable (tutor de menor) |
| `nombre_titular` | TEXT | nullable | Nombre del TC |
| `apellido_titular` | TEXT | nullable | Apellido del TC |
| `sexo_titular` | TEXT | nullable | Sexo del TC |
| `fecha_nacimiento_titular` | DATE | nullable | Fecha de nacimiento del TC |
| `provincia_titular` | TEXT | nullable | Provincia del TC |


**Nota sobre TD vs TC:**
- **TD (Titular de Derecho):** La persona que recibe el beneficio → `beneficiary_id` + datos en `beneficiaries`
- **TC (Titular de Cobro):** La persona que cobra el dinero → columnas `*_titular` en esta tabla. Para menores, típicamente es madre/padre/tutor

**Índices recomendados:**

```sql
CREATE INDEX idx_benefits_periodo_benid_progid ON benefits(periodo_mes, beneficiary_id, program_id);
CREATE INDEX idx_benefits_period_state_benid ON benefits(periodo_mes, estado_beneficio, beneficiary_id);
CREATE INDEX idx_benefits_benid ON benefits(beneficiary_id);
CREATE INDEX idx_benefits_periodo_cuil_titular ON benefits(periodo_mes, cuil_titular) INCLUDE (program_id, beneficiary_id);
```

---

## 5. `payments`

Pagos efectivamente liquidados.

```sql
CREATE TABLE payments (
    id SERIAL PRIMARY KEY,
    beneficiary_id INTEGER NOT NULL REFERENCES beneficiaries(id),
    program_id INTEGER NOT NULL REFERENCES programs(id),
    fecha_pago DATE NOT NULL,
    periodo_mes TEXT NOT NULL,
    monto_prestacion NUMERIC(12,2) NOT NULL
);
```

| Columna | Tipo | Restricciones | Descripción |
|---------|------|---------------|-------------|
| `id` | SERIAL | PK | ID autoincremental |
| `beneficiary_id` | INTEGER | FK → `beneficiaries(id)`, NOT NULL | Beneficiario |
| `program_id` | INTEGER | FK → `programs(id)`, NOT NULL | Programa |
| `fecha_pago` | DATE | NOT NULL | Fecha del pago |
| `periodo_mes` | TEXT | NOT NULL | Periodo liquidado, formato `YYYY-MM` |
| `monto_prestacion` | NUMERIC(12,2) | NOT NULL | Monto en pesos |

**Índice recomendado:**

```sql
CREATE INDEX idx_payments_covering ON payments(periodo_mes, beneficiary_id, program_id) INCLUDE (monto_prestacion);
```

---

## 6. `users`

Usuarios del sistema para autenticación.

```sql
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    nombre TEXT
);
```

| Columna | Tipo | Restricciones | Descripción |
|---------|------|---------------|-------------|
| `id` | SERIAL | PK | ID autoincremental |
| `email` | TEXT | UNIQUE, NOT NULL | Email de login |
| `password_hash` | TEXT | NOT NULL | Hash de contraseña (Werkzeug/SHA256) |
| `role` | TEXT | NOT NULL, DEFAULT 'user' | `admin` (acceso completo) o `user` (solo lectura) |
| `nombre` | TEXT | nullable | Nombre para mostrar |

---

## Diagrama de Relaciones

```
secretarias ─── 1:N ──→ programs ─── 1:N ──→ benefits ←── N:1 ─── beneficiaries
                                     │                              │
                                     └─── 1:N ──→ payments ←── N:1 ┘
```

---

## Extensiones requeridas

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;  -- Para búsqueda parcial de CUIL
```

---

## Volúmenes esperados

| Tabla | Orden de magnitud |
|-------|-------------------|
| `secretarias` | ~5 filas |
| `programs` | ~10 filas |
| `beneficiaries` | 1M – 8M filas |
| `benefits` | 10M – 200M filas (N programas × M periodos × personas) |
| `payments` | Similar a benefits (1 pago por benefit activo) |
| `users` | < 50 filas |
