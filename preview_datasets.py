"""Lee las primeras 10 líneas de cada CSV en datasets/ y muestra columnas + preview."""
import os, csv

DATASETS_DIR = os.path.join(os.path.dirname(__file__), "datasets")

for root, dirs, files in os.walk(DATASETS_DIR):
    for f in sorted(files):
        if not f.lower().endswith(".csv"):
            continue
        path = os.path.join(root, f)
        rel = os.path.relpath(path, DATASETS_DIR)
        print("=" * 80)
        print(f"ARCHIVO: {rel}")
        print(f"TAMAÑO: {os.path.getsize(path):,} bytes")
        print("-" * 80)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                # Detect delimiter
                sample = fh.read(4096)
                fh.seek(0)
                try:
                    dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
                    delim = dialect.delimiter
                except csv.Error:
                    delim = ","
                reader = csv.reader(fh, delimiter=delim)
                for i, row in enumerate(reader):
                    if i == 0:
                        print(f"COLUMNAS ({len(row)}): {delim.join(row)}")
                        print("-" * 80)
                    else:
                        print(f"  [{i}] {delim.join(row[:20])}")  # max 20 cols shown
                    if i >= 10:
                        break
        except Exception as e:
            print(f"  ERROR: {e}")
        print()
