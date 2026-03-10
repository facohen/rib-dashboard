#!/bin/bash
# Hook: Advierte cuando se edita queries.py sobre los contratos JSON
INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('tool_input',{}).get('file_path',''))" 2>/dev/null)

if [[ -z "$FILE_PATH" ]]; then
  exit 0
fi

BASENAME=$(basename "$FILE_PATH")

if [[ "$BASENAME" == "queries.py" ]]; then
  echo "RECORDATORIO: queries.py tiene contratos JSON con el frontend. Los campos de retorno NO deben cambiar:"
  echo "  summary: cobertura, promedioPrestaciones, promedioMontoPorBenef, montoTotal, tasaNoIdentificados, casosIncompatibilidad, concentracion"
  echo "  by-secretaria: [{secretaria, total}]"
  echo "  by-provincia: [{provincia, total}]"
  echo "  by-departamento: [{label, total}]"
  echo "  by-programa: [{programa, total}]"
  echo "  by-sexo: [{label, total}]"
  echo "  by-grupo-etario: [{grupo, total}]"
  echo "  evolucion: [{periodo_mes, total}]"
  echo "  Usar AS alias en SQL si los nombres de columna cambian."
fi

exit 0
