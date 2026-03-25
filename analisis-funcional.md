# Documento de Análisis Funcional: RIB Dashboard

## 1. Introducción
El presente documento tiene como finalidad el relevamiento técnico y funcional del sistema **RIB Dashboard**, una herramienta institucional destinada al monitoreo y análisis de prestaciones sociales. Se detalla a continuación la arquitectura del sistema, los flujos de datos y las interacciones entre los componentes de la interfaz de usuario, la capa de servicios y la persistencia en base de datos.

## 2. Arquitectura del Sistema
El sistema se estructura bajo una arquitectura de tres capas, optimizada para el procesamiento de grandes volúmenes de datos nominales:

### 2.1. Capa de Presentación (Frontend)
- **Tecnologías**: HTML5, CSS3, JavaScript (Vanilla), Jinja2 (Templates).
- **Componentes**: Tablero de indicadores dinámico, grillas de datos nominales con paginado y componentes de visualización geográfica (Leaflet) y estadística (Chart.js).

### 2.2. Capa de Aplicación (Backend)
- **Tecnologías**: Python 3.x, Flask.
- **Responsabilidad**: Exposición de una API RESTful para la hidratación de la interfaz, gestión de sesiones mediante `auth.py` y coordinación de la lógica de negocio a través de `queries.py`.

### 2.3. Capa de Datos (Persistencia)
- **Motor**: PostgreSQL.
- **Optimización**: Uso intensivo de Vistas Materializadas (`mv_resumen`, `mv_cross`, `mv_nominal`) para garantizar tiempos de respuesta sub-segundo en agregaciones masivas.

---

## 3. Flujos de Datos End-to-End

### 3.1. Proceso de Autenticación y Control de Acceso
1. **Interfaz**: El usuario ingresa credenciales en el formulario de acceso (`login.html`).
2. **Backend**: La solicitud es procesada por el Blueprint de autenticación en `auth.py`, que valida la existencia del usuario en la tabla `users` mediante `queries.get_user_by_email`.
3. **Persistencia**: Se verifica el hash del password y se recupera el rol institucional (admin/analista).
4. **Sesión**: Se establece una sesión segura y se redirige al usuario según su nivel de privilegios.

### 3.2. Hidratación del Tablero de Indicadores (Dashboard)
El ciclo de vida del dato para la visualización de métricas sigue la siguiente secuencia:
1. **Request GUI**: Al cargar `dashboard.html`, se disparan múltiples solicitudes asincrónicas (`fetch`) hacia los endpoints de la API (ej. `/api/indicators/summary`).
2. **Lógica de Consulta**: El backend recibe el período y los filtros cruzados (sexo, provincia, programa, etc.) y llama a la función correspondiente en `queries.py`.
3. **Ejecución SQL**: `queries.py` selecciona la fuente de datos optimizada:
    - `mv_resumen`: Para totales deduplicados de personas.
    - `mv_cross`: Para desgloses por programas y secretarías.
4. **Respuesta API**: Los resultados se retornan en formato JSON.
5. **Renderizado**: La lógica de JavaScript en el cliente instancia los objetos de `Chart.js`, vinculando los datos JSON a las propiedades de los gráficos y actualizando los valores de las tarjetas KPI.

### 3.3. Gestión de Datos Nominales y Exportación
1. **Filtros**: El administrador aplica filtros en `nominal.html`.
2. **Paginado**: La solicitud al backend incluye parámetros de desplazamiento (`limit`/`offset`).
3. **Consulta de Alta Performance**: Se utiliza la vista `mv_nominal`, que ya posee pre-calculadas las edades y la cantidad de prestaciones por beneficiario, evitando joins costosos en tiempo de ejecución.
4. **Visualización**: Se hidrata la tabla de resultados y se habilita la visualización de detalles mediante un "drawer" lateral que realiza una nueva consulta puntual a `/api/nominal/beneficiaries/<id>`.

### 3.4. Asistente Basado en Inteligencia Artificial (Chatbot)
El sistema integra una interfaz de consulta en lenguaje natural (`chatbot.html` y `chatbot.py`):
1. **Interfaz**: El usuario formula una pregunta técnica (ej. "¿Cual es el monto total liquidado por provincia en marzo?")
2. **Generación de SQL**: El backend utiliza el modelo **Ollama** (`qwen2.5-coder`) con un prompt de sistema que describe el esquema de base de datos para traducir la pregunta a una consulta SELECT válida.
3. **Validación y Ejecución**: Se implementa un mecanismo de seguridad "Read-Only" que bloquea cualquier operación destructiva y restringe el acceso a datos nominales sensibles (PII).
4. **Respuesta**: El resultado se formatea dinámicamente en tablas markdown o texto enriquecido para el usuario.

---

## 4. Proceso de Ingesta y ETL (Back-office)
La actualización de la información se realiza mediante el módulo `ingest.py`, el cual implementa la siguiente lógica:
1. **Extracción**: Lectura de archivos CSV maestros mediante la librería **Polars**.
2. **Transformación**:
    - Normalización de provincias y departamentos según estándares institucionales.
    - Limpieza de CUILs y validación de formatos de fecha.
    - Desdoblado de períodos liquidados (multi-periodos).
3. **Carga (Load)**: Inserción masiva en PostgreSQL utilizando `execute_values` para minimizar el overhead de red.
4. **Refresco de Vistas**: Posterior a la ingesta, se ejecutan scripts de mantenimiento para actualizar las Vistas Materializadas, asegurando la consistencia de los indicadores en el dashboard.

## 5. Consideraciones de Performance y Escalabilidad
Para el manejo eficiente de millones de registros, el sistema implementa:
- **Cache de Aplicación**: En el backend mediante `flask_caching` para almacenar resultados de indicadores frecuentes.
- **Materialized Views**: Estrategia de "Pre-computación" de agregados para evitar escaneos de tablas completas durante la navegación del usuario.
- **Compresión**: Uso de `flask_compress` para reducir el tamaño de las respuestas JSON transmitidas.

---
*Fin del documento formal de análisis funcional.*
