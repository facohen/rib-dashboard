---
name: check-endpoints
description: Verificar que todos los endpoints de la API funcionan correctamente
allowed-tools: Bash, Read
---

# Check Endpoints

Verificar todos los endpoints del RUB Dashboard contra el servidor corriendo en localhost:5000.

## Proceso

1. Verificar que el servidor responde:
   ```bash
   curl -s -o /dev/null -w "%{http_code}" http://localhost:5000/login
   ```
   Si no responde, indicar al usuario que lo levante.

2. Login como admin:
   ```bash
   curl -s -c /tmp/rub-cookies.txt -d "email=admin@demo.local&password=Demo123!" -L -o /dev/null http://localhost:5000/login
   ```

3. Probar cada endpoint y reportar status:
   ```bash
   for ep in \
     "/api/indicators/summary?period=2026-03" \
     "/api/indicators/by-secretaria?period=2026-03" \
     "/api/indicators/by-provincia?period=2026-03" \
     "/api/indicators/by-departamento?period=2026-03" \
     "/api/indicators/by-programa?period=2026-03" \
     "/api/indicators/by-sexo?period=2026-03" \
     "/api/indicators/by-grupo-etario?period=2026-03" \
     "/api/indicators/evolucion" \
     "/api/nominal/beneficiaries?period=2026-03&page=1&pageSize=5" \
   ; do
     CODE=$(curl -s -o /tmp/rub-resp.json -w "%{http_code}" -b /tmp/rub-cookies.txt "http://localhost:5000${ep}")
     echo "$CODE $ep"
   done
   ```

4. Para cada error (status != 200), mostrar el body de respuesta.

5. Limpiar: `rm -f /tmp/rub-cookies.txt /tmp/rub-resp.json`
