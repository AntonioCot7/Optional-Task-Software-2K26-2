"""Contrato HTTP: el OpenAPI generado debe coincidir con el snapshot tomado del estado inicial.

El snapshot (tests/openapi_snapshot.json) se generó con el código previo a la refactorización,
de modo que cualquier cambio de rutas, métodos, esquemas o códigos de estado la haría fallar.
Para regenerarlo deliberadamente: uv run python scripts/export_openapi.py tests/openapi_snapshot.json
"""
import json
from pathlib import Path

from main import create_app

SNAPSHOT = Path(__file__).parent / "openapi_snapshot.json"


def test_openapi_matches_snapshot_of_initial_contract(tmp_path):
    current = create_app(tmp_path / "x.sqlite3").openapi()
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert current == expected


def test_all_documented_operations_exist(tmp_path):
    paths = create_app(tmp_path / "x.sqlite3").openapi()["paths"]
    operations = {(path, method.upper()) for path, item in paths.items() for method in item}
    assert operations == {
        ("/products", "POST"), ("/products", "GET"),
        ("/products/{product_id}", "GET"), ("/products/{product_id}", "PUT"), ("/products/{product_id}", "DELETE"),
        ("/customers", "POST"), ("/customers", "GET"),
        ("/customers/{customer_id}", "GET"), ("/customers/{customer_id}", "PUT"),
        ("/customers/{customer_id}", "DELETE"),
        ("/orders", "POST"), ("/orders", "GET"), ("/orders/quote", "POST"),
        ("/orders/{order_id}", "GET"), ("/orders/{order_id}", "PUT"), ("/orders/{order_id}", "DELETE"),
        ("/orders/{order_id}/confirm", "POST"), ("/orders/{order_id}/cancel", "POST"),
        ("/orders/{order_id}/notifications", "GET"),
        ("/notifications/queue", "GET"), ("/notifications/dlq", "GET"),
        ("/notifications/dlq/{dlq_id}/reprocess", "POST"),
    }
