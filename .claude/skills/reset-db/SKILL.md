---
name: reset-db
description: Recrear la base de datos PostgreSQL desde cero y seedear con datos de prueba
argument-hint: "[--small|--medium|--full]"
allowed-tools: Bash, Read
disable-model-invocation: true
---

# Reset DB

Dropear y recrear la base de datos, luego seedear con datos frescos.

## Proceso

1. Verificar que `DATABASE_URL` está configurado:
   ```bash
   echo $DATABASE_URL
   ```

2. Ejecutar seed (incluye DROP + CREATE de tablas):
   ```bash
   python seed_pg.py $ARGUMENTS
   ```
   `seed_pg.py` ya hace `DROP TABLE IF EXISTS CASCADE` al inicio, así que no hace falta dropear manualmente.

3. Verificar que la DB quedó bien:
   ```bash
   python -c "
   from config import get_connection, query
   conn = get_connection()
   for t in ['beneficiaries','benefits','programs','payments','users']:
       rows = query(conn, f'SELECT COUNT(*) as n FROM {t}')
       print(f'{t}: {rows[0][\"n\"]:,}')
   conn.close()
   "
   ```

4. Reportar conteos de cada tabla.
