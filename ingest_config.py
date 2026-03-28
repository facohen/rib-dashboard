"""
ingest_config.py — Configuración declarativa de datasets para ingesta.

Solo datos: rutas, separadores, mapeos de columnas, defaults, tablas de referencia.
Las funciones de preprocesamiento se registran en ingest.py.
"""

import os

DATASETS_ROOT = os.path.join(os.path.dirname(__file__), "datasets", "RIB")

# ─────────────────────────────────────────────────────────────
# Tabla de provincias INDEC (código 2 dígitos → nombre oficial)
# Fuente: INDEC - Codificación de provincias
# ─────────────────────────────────────────────────────────────

# Codificación INDEC oficial (código 2 dígitos → nombre como está en PostgreSQL)
PROVINCIAS_INDEC = {
    "02": "Capital Federal",
    "06": "Buenos Aires",
    "10": "Catamarca",
    "14": "Cordoba",
    "18": "Corrientes",
    "22": "Chaco",
    "26": "Chubut",
    "30": "Entre Rios",
    "34": "Formosa",
    "38": "Jujuy",
    "42": "La Pampa",
    "46": "La Rioja",
    "50": "Mendoza",
    "54": "Misiones",
    "58": "Neuquen",
    "62": "Rio Negro",
    "66": "Salta",
    "70": "San Juan",
    "74": "San Luis",
    "78": "Santa Cruz",
    "82": "Santa Fe",
    "86": "Santiago del Estero",
    "90": "Tucuman",
    "94": "Tierra del Fuego",
}

# Codificación ANSES (provincia_cd en CSV de ANSES) — distinta a INDEC
PROVINCIAS_ANSES = {
    "01": "Capital Federal",
    "02": "Buenos Aires",
    "03": "Catamarca",
    "04": "Cordoba",
    "05": "Corrientes",
    "06": "Entre Rios",
    "07": "Jujuy",
    "08": "La Rioja",
    "09": "Mendoza",
    "10": "Salta",
    "11": "San Juan",
    "12": "San Luis",
    "13": "Santa Fe",
    "14": "Santiago del Estero",
    "15": "Tucuman",
    "16": "Chaco",
    "17": "Chubut",
    "18": "Formosa",
    "19": "La Pampa",
    "20": "Misiones",
    "21": "Neuquen",
    "22": "Rio Negro",
    "23": "Santa Cruz",
    "24": "Tierra del Fuego",
    "99": "Sin dato",
}

# Normalización: cualquier variante de nombre → nombre canónico en PostgreSQL
# Cubre: MAYUSCULAS, tildes, non-breaking spaces, abreviaciones
_NORM = {v.upper(): v for v in PROVINCIAS_INDEC.values()}
_NORM.update({v.upper(): v for v in PROVINCIAS_ANSES.values()})
_NORM.update({
    "CABA": "Capital Federal",
    "C.A.B.A.": "Capital Federal",
    "CIUDAD AUTONOMA DE BUENOS AIRES": "Capital Federal",
    "CIUDAD AUTÓNOMA DE BUENOS AIRES": "Capital Federal",
    "CIUDAD DE BUENOS AIRES": "Capital Federal",
    "BUENOS\xa0AIRES": "Buenos Aires",  # non-breaking space (Becas Belgrano)
    "SANTA\xa0FE": "Santa Fe",
    "SAN\xa0LUIS": "San Luis",
    "SAN\xa0JUAN": "San Juan",
    "TIERRA DEL FUEGO, ANTARTIDA E ISLAS DEL ATLANTICO SUR": "Tierra del Fuego",
    "TIERRA DEL FUEGO ANTARTIDA E ISLAS DEL ATLANTICO SUR": "Tierra del Fuego",
    "SIN INFORMAR": "Sin dato",
    # Variantes con tildes
    "CÓRDOBA": "Cordoba",
    "TUCUMÁN": "Tucuman",
    "NEUQUÉN": "Neuquen",
    "ENTRE RÍOS": "Entre Rios",
    "RÍO NEGRO": "Rio Negro",
    # Variantes title case (Belgrano CSV)
    "CIUDAD AUTONOMA DE BUENOS AIRES": "Capital Federal",
    "SANTIAGO DEL ESTERO": "Santiago del Estero",
    "TIERRA DEL FUEGO": "Tierra del Fuego",
    "LA RIOJA": "La Rioja",
    "LA PAMPA": "La Pampa",
    "SANTA CRUZ": "Santa Cruz",
    "SAN JUAN": "San Juan",
    "SAN LUIS": "San Luis",
    "SANTA FE": "Santa Fe",
})
PROVINCIA_NORMALIZE = _NORM

# col_map keys: cuil, nombre, apellido, sexo, fecha_nacimiento, provincia,
#   codigo_provincia_indec, departamento, codigo_departamento_indec, cp,
#   programa, secretaria, periodo, estado, monto, fecha_pago.
# Optional TC (titular de cobro) keys — default to TD values when absent:
#   cuil_titular, nombre_titular, apellido_titular, sexo_titular,
#   fecha_nacimiento_titular, provincia_titular, departamento_titular.
DATASET_CONFIGS = [
    {
        "name": "VOUCHERS",
        "description": "Vouchers liquidaciones (764MB)",
        "path": "VOUCHERS/vouchers_liquidaciones_*.csv",
        "separator": ",",
        "split_name_col": "ape_nom_menor",
        "col_map": {
            "cuil": "cuil_menor",
            "monto": "monto",
            "nombre": "_nombre",
            "apellido": "_apellido",
            "periodo": "pe_liqui",
        },
        "defaults": {
            "secretaria": "Secretaría de Inclusión Social",
            "programa": "Vouchers",
        },
    },
    {
        "name": "BECAS_BELGRANO",
        "description": "Becas Belgrano (78MB)",
        "path": "BECAS BELGRANO/becas_belgrano.csv",
        "separator": ";",
        "preprocess": "pp_belgrano",
        "col_map": {
            "cuil": "cuit",
            "apellido": "apellido",
            "nombre": "nombre",
            "sexo": "sexo",
            "provincia": "provincia",
            "fecha_nacimiento": "fecha_nacimiento",
            "monto": "monto_liquidado",
            "periodo": "periodo_liquidado",
        },
        "defaults": {
            "secretaria": "Secretaría de Educación",
            "programa": "Becas Belgrano",
        },
    },
    {
        "name": "STESS",
        "description": "STESS prestaciones (1.7GB)",
        "path": "STESS/stess_*.csv",
        "separator": ",",
        "split_name_col": "apellido_y_nombre",
        "dedup_exact": True,
        "skip_dedup": True,
        "col_map": {
            "cuil": "cuil",
            "nombre": "_nombre",
            "apellido": "_apellido",
            "sexo": "sexo",
            "provincia": "provincia_desc",
            "fecha_nacimiento": "fecha_nacimiento",
            "monto": "importe_total",
            "periodo": "periodo_liquidado",
        },
        "defaults": {
            "secretaria": "Secretaría de Trabajo",
            "programa": "STESS",
        },
    },
    {
        "name": "ALIMENTAR",
        "description": "Tarjeta Alimentar — menores + titulares (prorrateo monto)",
        "path": "ALIMENTAR/Menores/unificado_menores.csv",
        "titulares_path": "ALIMENTAR/Titulares/titulares_serie_202603102327.csv",
        "loader": "load_alimentar",
        "col_map": {
            "cuil": "cuil",
            "nombre": "nombre",
            "apellido": "apellido",
            "sexo": "sexo",
            "fecha_nacimiento": "fecha_nacimiento",
            "provincia": "provincia",
            "monto": "monto",
            "periodo": "periodo",
        },
        "defaults": {
            "secretaria": "Secretaria de Inclusion Social",
            "programa": "Tarjeta Alimentar",
        },
    },
]
