---
name: adapt-query
description: Adaptar una función de queries_td.py o queries_tc.py al schema real de la DB, manteniendo los contratos JSON
argument-hint: "<nombre_funcion>"
allowed-tools: Read, Edit, Bash, Grep
disable-model-invocation: true
---

# Adapt Query

Adaptar la función `$ARGUMENTS` de `queries_td.py` o `queries_tc.py` para que funcione con el schema real de la DB.

## Contratos JSON (NO CAMBIAR los campos de salida)

### TD (`/api/indicators/*`, `/api/nominal/*`)

| Endpoint | Campos |
|----------|--------|
| summary | cobertura, promedioPrestaciones, promedioMontoPorBenef, montoTotal, tasaNoIdentificados, casosIncompatibilidad, concentracion:{conUna,conDos,conTresMas} |
| by-secretaria | [{secretaria, total}] |
| by-provincia | [{provincia, total}] |
| by-programa | [{programa, total}] |
| by-sexo | [{label, total}] |
| by-grupo-etario | [{grupo, total}] |
| evolucion | [{periodo_mes, total}] |
| nominal/beneficiaries | {total, items:[{id,cuil,apellido,nombre,sexo,edad,provincia,cantPrestaciones}]} |
| nominal/beneficiaries/:id | {cuil,apellido,nombre,sexo,edad,fecha_nacimiento,provincia,cp,prestaciones:[...],pagos:[...]} |

### TC (`/api/tc/indicators/*`, `/api/tc/nominal/*`)

| Endpoint | Campos |
|----------|--------|
| summary | cobertura, promedioPrestaciones, promedioMontoPorBenef, montoTotal, concentracion:{conUna,conDos,conTresMas} |
| by-secretaria | [{secretaria, total}] |
| by-provincia | [{provincia, total}] |
| by-programa | [{programa, total}] |
| by-sexo | [{label, total}] |
| by-grupo-etario | [{grupo, total}] |
| evolucion | [{periodo_mes, total}] |
| nominal/titulares | {total, items:[...]} |
| nominal/titulares/:id | {cuil_titular,nombre_titular,apellido_titular,...} |

## Proceso

1. Leer la función actual en `queries_td.py` o `queries_tc.py`
2. Inspeccionar el schema real de las tablas involucradas:
   ```bash
   psql "$DATABASE_URL" -c "\d+ <tabla>"
   ```
3. Identificar diferencias (nombres columnas, tipos, tablas)
4. Adaptar el SQL usando `AS alias` para mantener el contrato
5. Verificar: `python -c "import queries_td; import queries_tc; print('OK')"`
6. Probar el endpoint correspondiente:
   ```bash
   curl -s -b /tmp/rib-cookies.txt "http://localhost:5000/api/..." | python -m json.tool | head -20
   ```
