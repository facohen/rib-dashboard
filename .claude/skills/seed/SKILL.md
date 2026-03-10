---
name: seed
description: Seedear la base de datos PostgreSQL con datos de prueba
argument-hint: "[--small|--medium|--full]"
allowed-tools: Bash, Read
---

# Seed PostgreSQL

Seedear la base de datos con `seed_pg.py`.

## Opciones

| Flag | Cantidad | Tiempo aprox |
|------|----------|-------------|
| `--small` | 10K beneficiarios | ~12s |
| `--medium` | 100K beneficiarios | ~2min |
| (sin flag) | 8M beneficiarios | ~30min+ |

## Proceso

1. Verificar que `DATABASE_URL` está configurado:
   ```bash
   echo $DATABASE_URL
   ```
   Si está vacío, decirle al usuario: `export DATABASE_URL=postgresql://...`

2. Ejecutar el seed con el argumento recibido:
   ```bash
   python seed_pg.py $ARGUMENTS
   ```

3. Reportar resultado: cantidad de registros creados y tiempo.
