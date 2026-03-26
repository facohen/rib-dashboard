# Arquitectura y Flujo de Datos End-to-End

Este diagrama describe el funcionamiento integral del sistema **RIB Dashboard**, abarcando desde la ingesta de datos en bruto hasta la visualización en el frontend y la interacción con la IA.

```mermaid
graph TD
    subgraph "Capa de Datos (Ingesta)"
        RAW[Archivos CSV/Excel] -->|Polars ETL| INGEST[ingest.py]
        INGEST -->|Bulk Insert| DB_TABLES[(Tablas Crudas PostgreSQL)]
        DB_TABLES -->|matviews.py| MVs[(Vistas Materializadas / Unlogged)]
    end

    subgraph "Capa de Servidor (Flask Backend)"
        AUTH[auth.py: Autenticación] -->|Login/Session| APP[app.py: Rutas & API]
        APP -->|Consultas SQL| QRI[queries.py: Lógica de Datos]
        QRI -->|Fetch| MVs
        
        subgraph "IA Ruby"
            CHAT[chatbot.py: Motor Chatbot] -->|Prompt Engineering| OLLAMA[Ollama: Qwen2.5-Coder]
            OLLAMA -->|Genera SQL| CHAT
            CHAT -->|Ejecución Read-Only| DB_TABLES
        end
        APP -.->|API Requests| CHAT
    end

    subgraph "Capa de Cliente (Frontend)"
        NAV[Navegador del Usuario] -->|HTTP/GET| TEMPLATES[Jinja2 Templates: base/dashboard/nominal]
        TEMPLATES -->|fetch API| APP
        
        U_DASH[Dashboard View] -->|Chart.js / Leaflet| UI[Interfaz de Usuario]
        U_NOM[Nominal View] -->|DataTables / Drawer| UI
        U_CHAT[Chatbot Interface] -->|Streaming NDJSON| UI
    end

    %% Relaciones de Flujo
    UI -->|Acciones/Filtros| TEMPLATES
    TEMPLATES -.->|Solicitud Datos| APP
    APP -->|JSON Response| TEMPLATES
```

## Descripción del Flujo

1.  **Ingesta**: El proceso comienza en `ingest.py`, que utiliza **Polars** para procesar archivos masivos y cargarlos en PostgreSQL. Luego, `matviews.py` transforma estos datos en tablas optimizadas para lectura rápida.
2.  **Autenticación**: `auth.py` gestiona el acceso mediante sesiones (actualmente del lado del cliente). Solo usuarios autenticados pueden acceder a las rutas de `app.py`.
3.  **Consumo de API**: El frontend (Jinja2 + JS Vanilla) realiza peticiones `fetch` a endpoints de indicadores. `queries.py` traduce estas peticiones en SQL eficiente que consulta las vistas materializadas.
4.  **Visualización**: Los datos se renderizan dinámicamente usando **Chart.js** para gráficos, **Leaflet** para el mapa de Argentina y componentes custom para la vista nominal.
5.  **Interacción IA**: Cuando el usuario consulta a "Ruby", `chatbot.py` interactúa con un modelo local (**Ollama**) para generar SQL. Este SQL se ejecuta bajo restricciones estrictas de "solo lectura" para devolver resultados estructurados al chat.

---
**Generado para RIB Dashboard Architecture Audit**
