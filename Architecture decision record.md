# Registro de Decisiones Arquitectónicas (ADR) - RIB Dashboard

Este documento registra las decisiones técnicas y arquitectónicas estratégicas adoptadas para la evolución del prototipo de la aplicación **RIB Dashboard** hacia un entorno productivo institucional.

---

## ADR 001: Capa de Abstracción de Datos (Patrón Repository)

*   **Estado:** Tentativo / Propuesto
*   **Contexto:** Actualmente, la lógica de acceso a datos (`queries.py`) utiliza SQL crudo embebido en funciones, lo que acopla estrechamente la lógica de aplicación con la persistencia.
*   **Decisión:** Se implementará una Capa de Abstracción basada en el patrón **Repository**. Toda interacción con la base de datos se realizará mediante objetos de acceso a datos (DAOs) que definan interfaces claras.
*   **Consecuencias:**
    *   **Positivas:** Mayor facilidad para realizar pruebas unitarias (*mocking*), desacoplamiento de la base de datos y mejora en la legibilidad del código.
    *   **Negativas:** Incremento inicial en el volumen de código y complejidad estructural.

---

## ADR 002: Gestión de Sesiones y Persistencia en el Lado del Servidor

*   **Estado:** Tentativo / Propuesto
*   **Contexto:** La aplicación utiliza sesiones de Flask basadas en *cookies* firmadas del lado del cliente, lo cual no es óptimo para la seguridad y auditoría en entornos estatales.
*   **Decisión:** Se migrará a un esquema de **Sesiones en el Lado del Servidor (Server-Side Sessions)**. Se evaluará el uso de **Redis** o una tabla específica en PostgreSQL para el almacenamiento de estados de sesión activos.
*   **Consecuencias:**
    *   **Positivas:** Capacidad de invalidar sesiones de forma centralizada y mayor seguridad ante ataques de red.
    *   **Negativas:** Requiere un componente adicional de infraestructura (ej. instancia de Redis).

---

## ADR 003: Control de Acceso Granular (RBAC) y Datos Nominales

*   **Estado:** Tentativo / Propuesto
*   **Contexto:** El acceso a datos nominales (CUIL, nombres, etc.) es crítico. El sistema actual solo diferencia entre "admin" y "analista" de forma binaria.
*   **Decisión:** Implementar un sistema de **Control de Acceso Basado en Roles (RBAC)** con una granularidad que permita autorizar el acceso a campos específicos. Cada acceso a datos nominales generará un registro de auditoría (*Audit Log*) obligatorio en una tabla inmutable.
*   **Consecuencias:**
    *   **Positivas:** Cumplimiento estricto con normativas de protección de datos personales.
    *   **Negativas:** Sobrecarga mínima en consultas de lectura para validar permisos.

---

## ADR 004: Comunicación en Tiempo Real mediante WebSockets

*   **Estado:** Tentativo / Propuesto
*   **Contexto:** Se requiere monitoreo en tiempo real y persistencia de estado de sesión activa.
*   **Decisión:** Integrar **WebSockets** (vía Flask-SocketIO) para gestionar la comunicación bidireccional. Este canal se utilizará para notificaciones de sistema, actualizaciones de ingesta y validación de estado de sesión.
*   **Consecuencias:**
    *   **Positivas:** Experiencia de usuario más dinámica y respuesta inmediata ante eventos del sistema.
    *   **Negativas:** Mayor complejidad en la gestión de conexiones y requerimiento de balanceadores de carga consistentes (*sticky sessions*).

---

## ADR 005: Transformación a Arquitectura Modular

*   **Estado:** Tentativo / Propuesto
*   **Contexto:** La aplicación actual es un monolito que dificulta la escalabilidad horizontal y el mantenimiento individual de componentes (ej. el módulo de ingesta no debería competir por recursos con la visualización).
*   **Decisión:** Reestructurar el proyecto en módulos lógicos independientes (*Backend API*, *ETL Services*, *Frontend*). Se priorizará el despliegue mediante contenedores (Docker).
*   **Consecuencias:**
    *   **Positivas:** Despliegue independiente, mayor resiliencia ante fallos sectorizados.
    *   **Negativas:** Mayor complejidad en la orquestación del entorno de producción.

---

## ADR 006: Patrón Check-Effects-Interactions (CEI)

*   **Estado:** Tentativo / Propuesto
*   **Contexto:** Se requiere garantizar la integridad de las transacciones y la seguridad en el acceso a datos sensibles, evitando efectos colaterales inesperados o ataques de reentrada de lógica.
*   **Decisión:** Se adoptará de forma transversal el patrón **Check-Effects-Interactions (CEI)** en todos los servicios de backend:
    1.  **Checks:** Validar pre-condiciones, autorizaciones (RBAC) y parámetros de entrada *antes* de realizar cualquier cambio.
    2.  **Effects:** Actualizar el estado interno de la aplicación y la base de datos (ej. registro en Audit Log).
    3.  **Interactions:** Realizar llamadas externas o retornar la respuesta al usuario final *solo después* de que los efectos hayan sido confirmados.
*   **Consecuencias:**
    *   **Positivas:** Mejora sustancial en la robustez transaccional y mayor facilidad para auditar fallos.
    *   **Negativas:** Requiere una disciplina estricta de desarrollo y puede aumentar levemente la verbosidad de los controladores/servidores.

---

## ADR 007: Desacoplamiento de las Capas de ETL y Presentación

*   **Estado:** Tentativo / Propuesto
*   **Contexto:** Actualmente, los scripts de ingesta (`ingest.py`) y gestión de vistas (`matviews.py`) residen en el mismo repositorio y entorno que la aplicación web, aunque son procesos de administración independientes.
*   **Decisión:** Se formalizará la separación total de la **Capa de Ingesta/ETL** de la **Capa de Presentación (Dashboard)**.
    1.  La aplicación Dashboard será estrictamente de **solo lectura** (salvo gestión de usuarios y auditoría).
    2.  Los procesos de ingesta no estarán accesibles ni serán disparables desde la interfaz de usuario (GUI).
    3.  Se prohibirá el uso de submódulos de ingesta dentro del servidor de aplicaciones Flask.
*   **Consecuencias:**
    *   **Positivas:** Reducción drástica de la superficie de ataque, eliminación de riesgos de ejecución accidental de truncates y mejor gestión de recursos.
    *   **Negativas:** Requiere procesos de despliegue y monitoreo separados para ambos componentes.

---
*Documento en evolución. Última actualización: 2026-03-25*
