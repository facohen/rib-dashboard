#!/bin/bash
# Hook: Después de editar un .py, verifica que importa correctamente
INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('tool_input',{}).get('file_path',''))" 2>/dev/null)

if [[ -z "$FILE_PATH" ]] || [[ "$FILE_PATH" != *.py ]]; then
  exit 0
fi

BASENAME=$(basename "$FILE_PATH" .py)

# Solo validar módulos del proyecto
case "$BASENAME" in
  app|config|queries|seed_pg|seed)
    cd "$(dirname "$FILE_PATH")"
    OUTPUT=$(python3 -c "import $BASENAME" 2>&1)
    if [[ $? -ne 0 ]]; then
      echo "ERROR de syntax/import en $BASENAME.py:" >&2
      echo "$OUTPUT" >&2
      exit 2
    fi
    echo "OK: $BASENAME.py importa correctamente"
    ;;
esac

exit 0
