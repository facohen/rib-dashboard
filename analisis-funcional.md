# Análisis Funcional Completo: RIB Dashboard

Este documento consolida el funcionamiento integral de la aplicación, desde la persistencia de datos hasta la interacción con el usuario final.

## 1. Arquitectura de Datos y Agregación

El sistema está diseñado como un **Data Warehouse ligero** sobre PostgreSQL. Prioriza la velocidad de consulta (dashboard) sobre la inmediatez transaccional mediante el uso de tablas agregadas que actúan como caché.

### Modelos de Agregación ([matviews.py](matviews.py))
| Tabla | Granularidad | Propósito |
| :--- | :--- | :--- |
| **`mv_cross`** | Programa + Secretaría + 5 dimensiones | Filtrado multidimensional para gráficos de barras y KPIs por rubro. |
| **`mv_resumen`** | Persona Única + 5 dimensiones | Totales nacionales deduplicados (Cobertura real). |
| **[mv_nominal](queries.py#L361-L366)** | Beneficiario Individual | Lista searchable con arrays de programas (active_program_ids). |

---

## 2. Lógica de Negocio y Entidades

La aplicación monitorea el impacto de programas sociales mediante las siguientes reglas:

### Entidades Core
- **Beneficiario**: Identificado por CUIL. Se manejan "No Identificados" con IDs negativos para auditoría de calidad de datos.
- **Prestación (Beneficio)**: Relación [Persona] + [Programa] + [Mes]. Puede ser `ACTIVO` o `INACTIVO`.
- **Combinación (Concentración)**: Lógica para detectar cuántos programas percibe una persona simultáneamente (1, 2, 3+).

### Cálculo de Dimensiones
- **Grupo Etario**: Calculado en SQL vía `AGE()` al corte del periodo. Grupos: 0-4, 5-12, 13-17, 18-29, 30-59, 60+.
- **Deduplicación**: El sistema distingue entre "Beneficios" (COUNT total) y "Personas" (COUNT DISTINCT). `mv_resumen` ya viene pre-deduplicada por `beneficiary_id`.

---

## 3. Orquestación y Refresco ([matviews.py](matviews.py))

La actualización de las vistas "materializadas" (tablas físicas) sigue un patrón de lote:

1. **Scan Eficiente**: Se crea una tabla temporal (`_pp`) por cada mes. Un solo escaneo masivo resuelve todas las dimensiones para las 3 MVs.
2. **Patrón Blue/Green (Atomic Swap)**: Se pueblan tablas shadow (`_new`), se indexan con `INCLUDE` (Index-Only Scans) y se intercambian por las activas en una sola transacción (~1ms de downtime).
3. **Tuning de DB**: Durante el refresco se forza `work_mem = '4GB'` y `UNLOGGED TABLES` para maximizar el throughput de escritura e indexación.

---

## 4. Interacción Frontend-Backend

### Dashboard Reactivo ([dashboard.html](templates/dashboard.html) + [queries.py](queries.py))
- **Cross-Filtering**: El frontend mantiene un estado global de filtros. Al interactuar con un gráfico, se refrescan todos los demás.
- **Enrutamiento de Agregados**: [queries.py](queries.py) decide dinámicamente si usar `mv_resumen` (rápida) o `mv_cross` (detallada) según los filtros activos.
- **Lazy Loading**: Los KPIs y gráficos se cargan en fases (priorizando lo visible) y el mapa descarga detalles de provincia (`provincia-detail`) solo bajo demanda (hover).

### Auditoría Nominal ([nominal.html](templates/nominal.html))
- Permite la búsqueda exacta y filtrado granular sobre la base completa.
- El **Drawer de Detalle** realiza un "deep dive" uniendo el historial de programas y pagos del beneficiario seleccionado.

---

## 5. Inteligencia de Datos y Seguridad

### Chatbot SQL ([chatbot.py](chatbot.py))
- Interfaz de lenguaje natural a SQL vía **Ollama**.
- **Seguridad**: Bloqueo estricto de PII (nombres, CUILs), solo `SELECT`, transacciones read-only y limitación de tablas permitidas.

### Control de Acceso ([auth.py](auth.py) + [app.py](app.py))
- **Roles**: `admin` (acceso a nómina, chatbot y limpieza de caché) y [user](db_schema.py#L130-L139) (solo dashboard).
- **Sesión**: Manejo de caché de servidor (Flask-Caching) con invalidación manual tras refrescos de datos.

---

## 7. Lógicas Específicas de Programas

A diferencia de la mayoría de los programas que tienen una carga directa, algunos poseen lógica de negocio compleja embebida en el proceso de ingesta ([ingest_optimizado.py](ingest_optimizado.py)):

### Programa Alimentar (Cruce de Padrones)
- **Lógica de Proporcionalidad**: El sistema no recibe el monto por niño, sino un monto total por titular.
- **Procesamiento**: Realiza un JOIN en base de datos entre el padrón de "Menores" y "Titulares". Divide el monto del titular entre la cantidad de niños + embarazos (prenatal) asociados, asignando la parte proporcional a cada registro individual en `benefits` y `payments`.

## 8. Funcionalidades Latentes o Legacy

Durante el análisis se identificaron componentes que existen en el schema pero no están operativos en la interfaz actual:

- **Reglas de Incompatibilidad**: Existe una tabla `incompatibility_rules` y datos semilla que definen cruces prohibidos (ej: AUH vs PNC). Sin embargo, el KPI de "Incompatibilidades" en el dashboard está **hardcodeado a 0** ([queries.py:L238](queries.py#L238)), lo que indica una funcionalidad pendiente de implementación o deshabilitada.
- **Georeferenciación a nivel Departamento**: El schema y las MVs capturan el departamento, pero el frontend solo implementa el mapa a nivel Provincial.

## 9. Observación sobre Contexto de Datos

El diseño asume que en producción:
- Los datos base ya residen en la base "Nominal" (PostgreSQL remoto).
- Los scripts de ingesta ([ingest.py](ingest.py)) y archivos CSV locales de la carpeta `datasets/` son artefactos de la etapa demo/desarrollo y no forman parte del flujo productivo principal.

## 10. Gestión de Usuarios y Roles

La aplicación implementa una distinción funcional clara entre perfiles:

| Rol | Alcance Funcional | Restricciones |
| :--- | :--- | :--- |
| **User (Analista)** | Dashboard de indicadores (agregados). | No puede ver datos nominales ni limpiar caché. |
| **Admin (Auditor)** | Dashboard + Nómina + Chatbot + Administración. | Puede ver nombres, CUILs y detalles individuales de beneficios/pagos. |

*Nota: El Chatbot está restringido a consultas que no devuelvan PII, incluso para administradores, como medida de seguridad adicional contra filtraciones accidentales.*
