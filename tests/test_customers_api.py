"""Integración: API de clientes y su persistencia."""
import pytest
from fastapi.testclient import TestClient

from main import create_app
from tests.helpers import make_customer, make_product, order_body


def test_create_customer_defaults_to_regular_category(client):
    response = client.post("/customers", json={"name": "Ana", "email": "ana@example.com"})
    assert response.status_code == 201
    body = response.json()
    assert body["category"] == "regular"
    assert body["email"] == "ana@example.com"
    assert client.get(f"/customers/{body['id']}").json() == body


@pytest.mark.parametrize("category", ["regular", "premium", "business"])
def test_create_customer_accepts_every_category(client, category):
    assert make_customer(client, category)["category"] == category


def test_list_is_ordered_by_id(client):
    created = [make_customer(client) for _ in range(3)]
    assert [c["id"] for c in client.get("/customers").json()] == [c["id"] for c in created]


def test_get_unknown_customer_returns_404(client):
    response = client.get("/customers/42")
    assert response.status_code == 404
    assert response.json() == {"detail": "Cliente no encontrado"}


def test_duplicate_email_on_create_returns_409_and_keeps_single_record(client):
    make_customer(client, email="dup@example.com")
    response = client.post("/customers", json={"name": "Otro", "email": "dup@example.com"})
    assert response.status_code == 409
    assert response.json() == {"detail": "El correo ya está registrado"}
    assert len(client.get("/customers").json()) == 1


def test_update_replaces_customer_data(client):
    customer = make_customer(client, "regular", "a@example.com")
    response = client.put(
        f"/customers/{customer['id']}", json={"name": "Nuevo", "email": "b@example.com", "category": "business"}
    )
    assert response.status_code == 200
    assert response.json() == {"id": customer["id"], "name": "Nuevo", "email": "b@example.com", "category": "business"}


def test_update_keeping_own_email_is_allowed(client):
    customer = make_customer(client, email="same@example.com")
    response = client.put(f"/customers/{customer['id']}",
                          json={"name": "Cambio", "email": "same@example.com", "category": "premium"})
    assert response.status_code == 200


def test_update_to_email_of_another_customer_returns_409(client):
    make_customer(client, email="uno@example.com")
    other = make_customer(client, email="dos@example.com")
    response = client.put(f"/customers/{other['id']}",
                          json={"name": "X", "email": "uno@example.com", "category": "regular"})
    assert response.status_code == 409
    assert client.get(f"/customers/{other['id']}").json()["email"] == "dos@example.com"


def test_update_unknown_customer_returns_404(client):
    response = client.put("/customers/9", json={"name": "X", "email": "x@example.com", "category": "regular"})
    assert response.status_code == 404


def test_delete_customer_returns_204_and_removes_it(client):
    customer = make_customer(client)
    response = client.delete(f"/customers/{customer['id']}")
    assert response.status_code == 204
    assert response.content == b""
    assert client.get(f"/customers/{customer['id']}").status_code == 404


def test_delete_unknown_customer_returns_404(client):
    assert client.delete("/customers/9").status_code == 404


def test_cannot_delete_customer_with_orders(client):
    customer = make_customer(client)
    product = make_product(client)
    assert client.post("/orders", json=order_body(customer["id"], [(product["id"], 1)])).status_code == 201
    response = client.delete(f"/customers/{customer['id']}")
    assert response.status_code == 409
    assert client.get(f"/customers/{customer['id']}").status_code == 200


@pytest.mark.parametrize("payload", [
    {"name": "", "email": "a@example.com"},
    {"name": "A", "email": "no-es-correo"},
    {"name": "A", "email": "a@example.com", "category": "vip"},
    {"name": "A"},
    {"email": "a@example.com"},
    {"name": "A", "email": "a@example.com", "extra": 1},
    {"name": "x" * 121, "email": "a@example.com"},
])
def test_invalid_customer_is_rejected_with_422(client, payload):
    assert client.post("/customers", json=payload).status_code == 422
    assert client.get("/customers").json() == []


def test_customers_survive_restart(db_path):
    with TestClient(create_app(db_path)) as first:
        customer = make_customer(first, "premium", "persist@example.com")
    with TestClient(create_app(db_path)) as second:
        assert second.get(f"/customers/{customer['id']}").json() == customer
