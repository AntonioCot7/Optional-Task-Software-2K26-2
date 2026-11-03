import pytest
from fastapi.testclient import TestClient

from main import create_app
from tests.helpers import drain_notifications


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "test.sqlite3")) as session:
        yield session


def prepare(client, stock=5):
    customer = client.post("/customers", json={"name": "Ana", "email": "ana@example.com", "category": "premium"})
    assert customer.status_code == 201
    product = client.post("/products", json={"name": "Libro", "price_cents": 2500, "stock": stock})
    assert product.status_code == 201
    return {"customer_id": customer.json()["id"], "address": "Calle 10", "city": "Lima",
            "postal_code": "15001", "shipping": "standard",
            "items": [{"product_id": product.json()["id"], "quantity": 2}]}


def test_confirmation_keeps_price_and_deducts_stock_once(client):
    payload = prepare(client)
    created = client.post("/orders", json=payload)
    assert created.status_code == 201
    order = created.json()
    assert order["total_cents"] == 5500
    product_id = payload["items"][0]["product_id"]
    assert client.put(f"/products/{product_id}", json={"name": "Libro", "price_cents": 9000, "stock": 5}).status_code == 200
    assert client.post(f"/orders/{order['id']}/confirm").status_code == 200
    assert client.post(f"/orders/{order['id']}/confirm").status_code == 409
    assert client.get(f"/products/{product_id}").json()["stock"] == 3
    assert client.get(f"/orders/{order['id']}").json()["total_cents"] == 5500
    drain_notifications(client)   # Adicional 2: la notificación la registra el worker, no confirm
    assert len(client.get(f"/orders/{order['id']}/notifications").json()) == 1


def test_insufficient_stock_rolls_back_all_lines(client):
    payload = prepare(client)
    second = client.post("/products", json={"name": "Agenda", "price_cents": 1000, "stock": 0}).json()
    payload["items"].append({"product_id": second["id"], "quantity": 1})
    order = client.post("/orders", json=payload).json()
    assert client.post(f"/orders/{order['id']}/confirm").status_code == 409
    assert client.get(f"/products/{payload['items'][0]['product_id']}").json()["stock"] == 5
    assert client.get(f"/orders/{order['id']}").json()["status"] == "pending"
    assert client.get(f"/orders/{order['id']}/notifications").json() == []


def test_duplicate_lines_are_rejected(client):
    payload = prepare(client)
    payload["items"].append(payload["items"][0])
    assert client.post("/orders", json=payload).status_code == 422


def test_orders_survive_restart(tmp_path):
    path = tmp_path / "persistent.sqlite3"
    with TestClient(create_app(path)) as client:
        payload = prepare(client)
        order = client.post("/orders", json=payload).json()
    with TestClient(create_app(path)) as client:
        assert client.get(f"/orders/{order['id']}").json()["total_cents"] == 5500
