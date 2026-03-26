# Informe de Auditoría RIB Dashboard

## 1. Resumen Ejecutivo
Se ha realizado una auditoría integral del repositorio **RIB Dashboard** (Rama: main) comparando el estado actual con el `analisis-funcional.md` y los registros de decisiones arquitectónicas (`Architecture decision record.md`).

El sistema es funcional y sigue una arquitectura de 3 capas, pero presenta **desviaciones críticas** respecto a los objetivos de seguridad y modularidad definidos en los ADRs, principalmente en la gestión de sesiones, el acoplamiento de la lógica de datos y la integración del proceso ETL.

---

## 2. Comparación vs. Análisis Funcional

| Componente | Estado | Observaciones |
| :--- | :--- | :--- |
| **Arquitectura de 3 Capas** | Implementado | Estructura Flask (Backend) + HTML/JS (Frontend) + PostgreSQL (DB). |
| **Integración de Datos (ETL)** | Parcial | El flujo funciona, pero `ingest.py` está integrado en el servidor, no desacoplado. |
| **Dashboard de Indicadores** | Implementado | Visualizaciones con Chart.js y Leaflet funcionando sobre datos reales (vía API). |
| **Chatbot SQL (Ruby)** | Implementado | Integración con Ollama y ejecución segura en modo Read-Only. |
| **Seguridad de Sesiones** | Incumplido | El análisis funcional pide sesiones de servidor, pero se usan cookies firmadas (Client-side). |

---

## 3. Estado de Adopción de ADRs

| ADR | Título | Estado Actual | Brecha Identificada |
| :--- | :--- | :--- | :--- |
| **001** | Repository Pattern | No adoptado | `queries.py` contiene SQL embebido y lógica de conexión directa. |
| **002** | Server-Side Sessions | No adoptado | Se usa `session` nativo de Flask (Client-side JSON Web Signatures). |
| **003** | RBAC Granular | No adoptado | Control binario `admin`/`user`. No hay permisos por objeto/acción. |
| **004** | WebSockets p/ MVs | No adoptado | El refresco de vistas es por polling o manual; no hay comunicación bi-direccional. |
| **005** | Modularidad Frontend | No adoptado | Lógica de templates muy extensa en JS embebido (`dashboard.html` > 900 líneas). |
| **007** | Desacoplamiento ETL | No adoptado | `ingest.py` reside en el mismo repo y entorno que el Dashboard. |

---

## 4. Hallazgos Críticos y Deuda Técnica

### 4.1. Seguridad
*   **Gestión de Sesiones**: El uso de sesiones del lado del cliente expone el sistema a ataques si la `SECRET_KEY` se ve comprometida y dificulta la invalidación de sesiones desde el servidor (ADR 002).
*   **Control de Acceso (RBAC)**: La lógica en `auth.py` es demasiado simple para un sistema gubernamental/sensible. Se requiere una tabla de permisos y roles dinámicos (ADR 003).

### 4.2. Arquitectura y Código
*   **Acoplamiento de Datos**: No existe una capa de persistencia abstracta. Cualquier cambio en el esquema de la DB requiere modificar múltiples puntos en `queries.py` y `matviews.py` (ADR 001).
*   **Dualidad de Frontend**: Existe un archivo `index.html` con `app.js` y `data.js` que utiliza datos **mock**. Esto genera confusión y riesgo de desplegar una versión de "demo" por error. Las plantillas reales están en `templates/`.
*   **Lógica en Vistas**: Las plantillas Jinja2 contienen lógica de negocio y configuraciones de gráficos muy extensas, dificultando el mantenimiento y testing unitario del frontend (ADR 005).

### 4.3. Proceso de Datos (ETL)
*   **Violación de Capas**: El servidor web tiene acceso directo al módulo de ingesta y carga de datos. En un entorno productivo, el ETL debería ser un servicio independiente que solo comparta la DB con el Dashboard (ADR 007).

---

## 5. Recomendaciones de Refactorización (Roadmap)

1.  **Prioridad 1 (Seguridad)**: Migrar a `Flask-Session` (Redis/DB) para sesiones del lado del servidor y expandir `auth.py` para soportar RBAC.
2.  **Prioridad 2 (Arquitectura)**: Implementar la clase `Repository` en una nueva carpeta `src/repository/` y migrar gradualmente las funciones de `queries.py`.
3.  **Prioridad 3 (Modularización)**: Separar `ingest.py` en un repositorio/contenedor independiente. Extraer la lógica de gráficos en `dashboard.html` a módulos JS externos.
4.  **Prioridad 4 (Limpieza)**: Eliminar o mover a una carpeta `docs/prototype/` los archivos `index.html`, `app.js` y `data.js` para evitar confusiones.

---
**Auditado por AANDRE**
