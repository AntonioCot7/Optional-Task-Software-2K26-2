"""Integración: API de productos y su persistencia."""
import pytest
from fastapi.testclient import TestClient

from main import create_app
from tests.helpers import make_customer, make_product, order_body


def test_create_product_returns_201_and_persists_values(client):
    response = client.post("/products", json={"name": "  Libro  ", "price_cents": 2500, "stock": 5})
    assert response.status_code == 201
    body = response.json()
    assert body == {"id": body["id"], "name": "Libro", "price_cents": 2500, "stock": 5}
    assert client.get(f"/products/{body['id']}").json() == body


def test_first_ids_are_sequential_and_list_is_ordered_by_id(client):
    created = [make_product(client, name=f"P{i}") for i in range(3)]
    listed = client.get("/products").json()
    assert [p["id"] for p in listed] == [p["id"] for p in created]
    assert [p["id"] for p in listed] == sorted(p["id"] for p in listed)


def test_list_is_empty_on_new_database(client):
    assert client.get("/products").json() == []


def test_get_unknown_product_returns_404_with_detail(client):
    response = client.get("/products/999")
    assert response.status_code == 404
    assert response.json() == {"detail": "Producto no encontrado"}


def test_update_replaces_whole_product(client):
    product = make_product(client, price=1000, stock=3, name="Viejo")
    response = client.put(f"/products/{product['id']}", json={"name": "Nuevo", "price_cents": 2000, "stock": 7})
    assert response.status_code == 200
    assert response.json() == {"id": product["id"], "name": "Nuevo", "price_cents": 2000, "stock": 7}
    assert client.get(f"/products/{product['id']}").json()["name"] == "Nuevo"


def test_update_unknown_product_returns_404(client):
    response = client.put("/products/999", json={"name": "X", "price_cents": 100, "stock": 1})
    assert response.status_code == 404


def test_delete_product_returns_204_without_body_and_removes_it(client):
    product = make_product(client)
    response = client.delete(f"/products/{product['id']}")
    assert response.status_code == 204
    assert response.content == b""
    assert client.get(f"/products/{product['id']}").status_code == 404


def test_delete_unknown_product_returns_404(client):
    assert client.delete("/products/999").status_code == 404


def test_cannot_delete_product_used_by_an_order(client):
    customer = make_customer(client)
    product = make_product(client)
    assert client.post("/orders", json=order_body(customer["id"], [(product["id"], 1)])).status_code == 201
    response = client.delete(f"/products/{product['id']}")
    assert response.status_code == 409
    assert client.get(f"/products/{product['id']}").status_code == 200


@pytest.mark.parametrize("payload", [
    {"name": "", "price_cents": 100, "stock": 1},
    {"name": "   ", "price_cents": 100, "stock": 1},
    {"name": "x" * 121, "price_cents": 100, "stock": 1},
    {"name": "P", "price_cents": 0, "stock": 1},
    {"name": "P", "price_cents": -5, "stock": 1},
    {"name": "P", "price_cents": 100_000_001, "stock": 1},
    {"name": "P", "price_cents": 10.5, "stock": 1},
    {"name": "P", "price_cents": "100", "stock": 1},
    {"name": "P", "price_cents": 100, "stock": -1},
    {"name": "P", "price_cents": 100, "stock": 1_000_001},
    {"name": "P", "price_cents": 100},
    {"name": "P", "price_cents": 100, "stock": 1, "extra": True},
])
def test_invalid_product_is_rejected_with_422_and_nothing_is_stored(client, payload):
    assert client.post("/products", json=payload).status_code == 422
    assert client.get("/products").json() == []


def test_boundary_values_are_accepted(client):
    assert client.post("/products", json={"name": "x" * 120, "price_cents": 1, "stock": 0}).status_code == 201
    assert client.post("/products", json={"name": "P", "price_cents": 100_000_000, "stock": 1_000_000}).status_code == 201


def test_invalid_update_does_not_modify_product(client):
    product = make_product(client, price=1000)
    assert client.put(f"/products/{product['id']}", json={"name": "X", "price_cents": 0, "stock": 1}).status_code == 422
    assert client.get(f"/products/{product['id']}").json() == product


def test_non_integer_path_id_is_rejected(client):
    assert client.get("/products/abc").status_code == 422


def test_products_survive_restart(db_path):
    with TestClient(create_app(db_path)) as first:
        product = make_product(first, price=777, stock=4, name="Persistente")
    with TestClient(create_app(db_path)) as second:
        assert second.get(f"/products/{product['id']}").json() == product
