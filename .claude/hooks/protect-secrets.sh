#!/bin/bash
# Hook: Bloquea escritura a archivos con secrets
INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('tool_input',{}).get('file_path',''))" 2>/dev/null)

if [[ -z "$FILE_PATH" ]]; then
  exit 0
fi

BASENAME=$(basename "$FILE_PATH")

# Block .env files, credentials, keys
if [[ "$BASENAME" == ".env" ]] || \
   [[ "$BASENAME" == ".env."* ]] || \
   [[ "$BASENAME" == *"credentials"* ]] || \
   [[ "$BASENAME" == *"secret"* && "$BASENAME" == *.json ]] || \
   [[ "$BASENAME" == *.pem ]] || \
   [[ "$BASENAME" == *.key ]]; then
  echo "BLOQUEADO: No se puede escribir a '$BASENAME' (archivo de secrets)" >&2
  exit 2
fi

exit 0
