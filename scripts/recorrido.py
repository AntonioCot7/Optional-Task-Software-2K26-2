"""Reproduce el 'Recorrido inicial' del README contra un servidor en ejecución.

Uso (con el servidor levantado):  uv run python scripts/recorrido.py [URL_BASE]
Sale con código 1 si algún resultado difiere de lo esperado por el README.
"""
import json
import sys

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
failures = []


def show(step, response, expected_status, check=None):
    ok = response.status_code == expected_status and (check is None or check(response.json()))
    body = response.text if response.content else "(sin cuerpo)"
    print(f"\n[{'OK' if ok else 'FALLO'}] {step}\n    HTTP {response.status_code} (esperado {expected_status})\n    {body}")
    if not ok:
        failures.append(step)
    return response


with httpx.Client(base_url=BASE) as http:
    customer = show("1. POST /customers", http.post("/customers", json={
        "name": "Ana Torres", "email": "ana@example.com", "category": "premium"}), 201).json()
    product = show("2. POST /products", http.post("/products", json={
        "name": "Libro de algoritmos", "price_cents": 2500, "stock": 5}), 201).json()
    body = {"customer_id": customer["id"], "address": "Av. Primavera 100", "city": "Lima", "postal_code": "15001",
            "shipping": "standard", "items": [{"product_id": product["id"], "quantity": 2}]}
    expected = {"subtotal_cents": 5000, "discount_cents": 500, "shipping_cents": 1000, "total_cents": 5500}
    show("3a. POST /orders/quote (subtotal 5000, descuento 500, envio 1000, total 5500)",
         http.post("/orders/quote", json=body), 200, lambda r: r == expected)
    order = show("3b. POST /orders", http.post("/orders", json=body), 201,
                 lambda r: all(r[k] == v for k, v in expected.items())).json()
    show(f"4. POST /orders/{order['id']}/confirm", http.post(f"/orders/{order['id']}/confirm"), 200)
    show(f"5. GET /products/{product['id']} (stock debe ser 3)", http.get(f"/products/{product['id']}"), 200,
         lambda r: r["stock"] == 3)
    show(f"6. GET /orders/{order['id']}/notifications (1 notificacion)",
         http.get(f"/orders/{order['id']}/notifications"), 200, lambda r: len(r) == 1)
    show(f"7. POST /orders/{order['id']}/confirm otra vez (409, sin descontar mas stock)",
         http.post(f"/orders/{order['id']}/confirm"), 409)
    show(f"7b. GET /products/{product['id']} (stock sigue en 3)", http.get(f"/products/{product['id']}"), 200,
         lambda r: r["stock"] == 3)

print("\nRESULTADO:", "TODO CORRECTO" if not failures else f"FALLARON: {failures}")
sys.exit(1 if failures else 0)
