"""Exporta el OpenAPI de la aplicación a un archivo JSON (uso: export_openapi.py DESTINO)."""
import json
import sys
import tempfile
from pathlib import Path

from main import create_app

target = Path(sys.argv[1])
with tempfile.TemporaryDirectory() as folder:
    schema = create_app(Path(folder) / "x.sqlite3").openapi()
target.write_text(json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"OpenAPI escrito en {target}")
