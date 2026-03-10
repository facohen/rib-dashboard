---
name: adapt-query
description: Adaptar una función de queries.py al schema real de la DB, manteniendo los contratos JSON
argument-hint: "<nombre_funcion>"
allowed-tools: Read, Edit, Bash, Grep
---

# Adapt Query

Adaptar la función `$ARGUMENTS` de `queries.py` para que funcione con el schema real de la DB.

## Contratos JSON (NO CAMBIAR los campos de salida)

| Endpoint | Campos |
|----------|--------|
| summary | cobertura, promedioPrestaciones, promedioMontoPorBenef, montoTotal, tasaNoIdentificados, casosIncompatibilidad, concentracion:{conUna,conDos,conTresMas} |
| by-secretaria | [{secretaria, total}] |
| by-provincia | [{provincia, total}] |
| by-departamento | [{label, total}] |
| by-programa | [{programa, total}] |
| by-sexo | [{label, total}] |
| by-grupo-etario | [{grupo, total}] |
| evolucion | [{periodo_mes, total}] |
| nominal/beneficiaries | {total, items:[...]} |
| nominal/beneficiaries/:id | {cuil,apellido,nombre,...} |

## Proceso

1. Leer la función actual en `queries.py`
2. Inspeccionar el schema real de las tablas involucradas:
   ```bash
   psql "$DATABASE_URL" -c "\d+ <tabla>"
   ```
3. Identificar diferencias (nombres columnas, tipos, tablas)
4. Adaptar el SQL usando `AS alias` para mantener el contrato
5. Verificar: `python -c "import queries; print('OK')"`
6. Probar el endpoint correspondiente:
   ```bash
   curl -s -b /tmp/rub-cookies.txt "http://localhost:5000/api/..." | python -m json.tool | head -20
   ```
