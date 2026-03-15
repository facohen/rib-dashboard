"""
ingest_log.py — Logging para el pipeline de ingesta.

Escribe a consola + archivo logs/ingest_YYYYMMDD_HHMMSS.log.
"""

import os
import logging
from datetime import datetime

LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")


def setup_logger():
    """Configura logger dual: consola + archivo."""
    os.makedirs(LOG_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(LOG_DIR, f"ingest_{ts}.log")

    logger = logging.getLogger("ingest")
    logger.setLevel(logging.INFO)

    # Evitar handlers duplicados si se llama más de una vez
    if logger.handlers:
        logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S")

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    return logger, log_path


def log_report(logger, dataset_name, totals, elapsed, log_path):
    """Escribe reporte resumen al final de una ingesta."""
    logger.info("=" * 55)
    logger.info(f"  REPORTE DE INGESTA")
    logger.info("=" * 55)
    logger.info(f"  {'Dataset:':<30} {dataset_name}")
    for key, val in totals.items():
        label = key.replace("_", " ").capitalize() + ":"
        if isinstance(val, (int, float)):
            logger.info(f"  {label:<30} {val:>12,}")
        else:
            logger.info(f"  {label:<30} {val!s:>12}")
    logger.info(f"  {'Tiempo total:':<30} {elapsed:>11.1f}s")
    logger.info(f"  {'Log guardado en:':<30} {log_path}")
    logger.info("=" * 55)
