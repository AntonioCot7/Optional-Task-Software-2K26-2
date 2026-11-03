"""Funciones auxiliares para crear datos de prueba vía API."""
import itertools

_sequence = itertools.count(1)


def make_customer(client, category="regular", email=None):
    email = email or f"cliente{next(_sequence)}@example.com"
    response = client.post("/customers", json={"name": "Cliente", "email": email, "category": category})
    assert response.status_code == 201, response.text
    return response.json()


def make_product(client, price=1000, stock=10, name=None):
    name = name or f"Producto {next(_sequence)}"
    response = client.post("/products", json={"name": name, "price_cents": price, "stock": stock})
    assert response.status_code == 201, response.text
    return response.json()


def order_body(customer_id, items, shipping="standard"):
    """items: lista de (product_id, quantity)."""
    return {
        "customer_id": customer_id,
        "address": "Av. Primavera 100",
        "city": "Lima",
        "postal_code": "15001",
        "shipping": shipping,
        "items": [{"product_id": pid, "quantity": qty} for pid, qty in items],
    }
