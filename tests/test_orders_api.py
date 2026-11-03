"""Integración: API de pedidos (cotización, CRUD, estados, stock, snapshots de precio)."""
import pytest

from tests.helpers import drain_notifications, make_customer, make_product, order_body


def stock_of(client, product):
    return client.get(f"/products/{product['id']}").json()["stock"]


# --------------------------------------------------------------------------- reglas de precio vía API
@pytest.mark.parametrize("category, unit_price, quantity, shipping, expected", [
    # (subtotal, descuento, envío, total)
    ("regular", 2500, 2, "standard", (5000, 0, 1000, 6000)),
    ("premium", 2500, 2, "standard", (5000, 500, 1000, 5500)),     # ejemplo del README
    ("business", 2500, 2, "standard", (5000, 750, 1000, 5250)),
    ("regular", 2500, 2, "pickup", (5000, 0, 0, 5000)),
    ("regular", 2500, 2, "express", (5000, 0, 2500, 7500)),
    ("premium", 999, 1, "pickup", (999, 99, 0, 900)),               # 99,9 se trunca a 99
    ("business", 999, 1, "pickup", (999, 149, 0, 850)),             # 149,85 se trunca a 149
    ("premium", 1, 1, "pickup", (1, 0, 0, 1)),                      # 0,1 se trunca a 0
])
def test_quote_and_create_apply_discount_shipping_and_total(client, category, unit_price, quantity, shipping, expected):
    customer = make_customer(client, category)
    product = make_product(client, price=unit_price)
    body = order_body(customer["id"], [(product["id"], quantity)], shipping)

    quote = client.post("/orders/quote", json=body)
    created = client.post("/orders", json=body)

    assert quote.status_code == 200 and created.status_code == 201
    subtotal, discount, delivery, total = expected
    assert quote.json() == {"subtotal_cents": subtotal, "discount_cents": discount,
                            "shipping_cents": delivery, "total_cents": total}
    order = created.json()
    assert (order["subtotal_cents"], order["discount_cents"], order["shipping_cents"], order["total_cents"]) == expected
    assert total == subtotal - discount + delivery


@pytest.mark.parametrize("unit_price, expected_shipping", [
    (9_999, 1_000),    # justo debajo del umbral
    (10_000, 0),       # exactamente en el umbral: gratis
    (10_001, 0),
])
def test_standard_shipping_free_threshold_boundary(client, unit_price, expected_shipping):
    customer = make_customer(client, "regular")
    product = make_product(client, price=unit_price)
    quote = client.post("/orders/quote", json=order_body(customer["id"], [(product["id"], 1)])).json()
    assert quote["shipping_cents"] == expected_shipping


def test_free_shipping_threshold_uses_subtotal_before_discount(client):
    customer = make_customer(client, "premium")
    product = make_product(client, price=10_000)
    quote = client.post("/orders/quote", json=order_body(customer["id"], [(product["id"], 1)])).json()
    # El subtotal (10000) alcanza el umbral aunque tras el descuento (9000) quede por debajo.
    assert quote == {"subtotal_cents": 10_000, "discount_cents": 1_000, "shipping_cents": 0, "total_cents": 9_000}


def test_express_shipping_is_never_free(client):
    customer = make_customer(client)
    product = make_product(client, price=50_000)
    quote = client.post("/orders/quote", json=order_body(customer["id"], [(product["id"], 3)], "express")).json()
    assert quote["shipping_cents"] == 2_500


def test_multiple_lines_are_summed_into_subtotal(client):
    customer = make_customer(client, "regular")
    first = make_product(client, price=300)
    second = make_product(client, price=450)
    quote = client.post("/orders/quote", json=order_body(customer["id"], [(first["id"], 2), (second["id"], 3)], "pickup"))
    assert quote.json()["subtotal_cents"] == 300 * 2 + 450 * 3


def test_shipping_defaults_to_standard(client):
    customer = make_customer(client)
    product = make_product(client, price=1000)
    body = order_body(customer["id"], [(product["id"], 1)])
    del body["shipping"]
    created = client.post("/orders", json=body)
    assert created.status_code == 201
    assert created.json()["shipping"] == "standard"
    assert created.json()["shipping_cents"] == 1000


def test_quote_has_no_side_effects(client):
    customer = make_customer(client)
    product = make_product(client, stock=5)
    assert client.post("/orders/quote", json=order_body(customer["id"], [(product["id"], 2)])).status_code == 200
    assert client.get("/orders").json() == []
    assert stock_of(client, product) == 5


def test_quote_and_create_do_not_reserve_stock_nor_check_it(client):
    customer = make_customer(client)
    product = make_product(client, stock=1)
    body = order_body(customer["id"], [(product["id"], 50)])
    assert client.post("/orders/quote", json=body).status_code == 200
    assert client.post("/orders", json=body).status_code == 201
    assert stock_of(client, product) == 1


# --------------------------------------------------------------------------- creación y consulta
def test_created_order_is_pending_and_stores_snapshot_fields(client):
    customer = make_customer(client, "business")
    product = make_product(client, price=1200)
    created = client.post("/orders", json=order_body(customer["id"], [(product["id"], 2)], "express"))
    assert created.status_code == 201
    order = created.json()
    assert order["status"] == "pending"
    assert order["customer_id"] == customer["id"]
    assert order["customer_category"] == "business"
    assert (order["address"], order["city"], order["postal_code"]) == ("Av. Primavera 100", "Lima", "15001")
    assert order["items"] == [{"product_id": product["id"], "quantity": 2, "unit_price_cents": 1200}]


def test_get_order_includes_lines_and_list_is_summary_without_lines(client):
    customer = make_customer(client)
    product = make_product(client)
    order = client.post("/orders", json=order_body(customer["id"], [(product["id"], 1)])).json()
    detail = client.get(f"/orders/{order['id']}").json()
    assert detail["items"] == order["items"]
    listed = client.get("/orders").json()
    assert len(listed) == 1
    assert "items" not in listed[0]
    assert listed[0]["id"] == order["id"]


def test_orders_are_listed_by_id(client):
    customer = make_customer(client)
    product = make_product(client)
    ids = [client.post("/orders", json=order_body(customer["id"], [(product["id"], 1)])).json()["id"] for _ in range(3)]
    assert [o["id"] for o in client.get("/orders").json()] == ids


def test_get_unknown_order_returns_404(client):
    response = client.get("/orders/123")
    assert response.status_code == 404
    assert response.json() == {"detail": "Pedido no encontrado"}


@pytest.mark.parametrize("path", ["/orders/quote", "/orders"])
def test_unknown_customer_returns_404(client, path):
    product = make_product(client)
    response = client.post(path, json=order_body(999, [(product["id"], 1)]))
    assert response.status_code == 404
    assert response.json() == {"detail": "Cliente no encontrado"}


@pytest.mark.parametrize("path", ["/orders/quote", "/orders"])
def test_unknown_product_returns_404_and_creates_nothing(client, path):
    customer = make_customer(client)
    response = client.post(path, json=order_body(customer["id"], [(777, 1)]))
    assert response.status_code == 404
    assert response.json() == {"detail": "Producto 777 no encontrado"}
    assert client.get("/orders").json() == []


def test_unknown_product_in_second_line_leaves_no_partial_order(client):
    customer = make_customer(client)
    product = make_product(client)
    response = client.post("/orders", json=order_body(customer["id"], [(product["id"], 1), (888, 1)]))
    assert response.status_code == 404
    assert client.get("/orders").json() == []


@pytest.mark.parametrize("mutate", [
    lambda b: b.update(items=[]),
    lambda b: b["items"][0].update(quantity=0),
    lambda b: b["items"][0].update(quantity=-1),
    lambda b: b["items"][0].update(quantity=10_001),
    lambda b: b["items"][0].update(quantity="2"),
    lambda b: b["items"][0].update(product_id=0),
    lambda b: b.update(customer_id=0),
    lambda b: b.update(address=""),
    lambda b: b.update(address="  "),
    lambda b: b.update(address="x" * 201),
    lambda b: b.update(city=""),
    lambda b: b.update(city="x" * 101),
    lambda b: b.update(postal_code=""),
    lambda b: b.update(postal_code="1" * 21),
    lambda b: b.update(shipping="drone"),
    lambda b: b.update(extra="x"),
    lambda b: b["items"][0].update(extra="x"),
    lambda b: b.pop("address"),
])
def test_invalid_order_is_rejected_with_422_by_quote_and_create(client, mutate):
    customer = make_customer(client)
    product = make_product(client)
    body = order_body(customer["id"], [(product["id"], 1)])
    mutate(body)
    assert client.post("/orders/quote", json=body).status_code == 422
    assert client.post("/orders", json=body).status_code == 422
    assert client.get("/orders").json() == []


def test_more_than_one_hundred_lines_are_rejected(client):
    customer = make_customer(client)
    body = order_body(customer["id"], [(pid, 1) for pid in range(1, 102)])
    assert client.post("/orders", json=body).status_code == 422


def test_duplicate_products_are_rejected_in_quote_too(client):
    customer = make_customer(client)
    product = make_product(client)
    body = order_body(customer["id"], [(product["id"], 1), (product["id"], 2)])
    assert client.post("/orders/quote", json=body).status_code == 422


# --------------------------------------------------------------------------- snapshots
def test_order_keeps_price_and_category_snapshot_after_changes(client):
    customer = make_customer(client, "premium", "snap@example.com")
    product = make_product(client, price=2500)
    order = client.post("/orders", json=order_body(customer["id"], [(product["id"], 2)])).json()

    client.put(f"/products/{product['id']}", json={"name": "Caro", "price_cents": 9000, "stock": 10})
    client.put(f"/customers/{customer['id']}", json={"name": "C", "email": "snap@example.com", "category": "regular"})

    after = client.get(f"/orders/{order['id']}").json()
    assert after["customer_category"] == "premium"
    assert after["total_cents"] == order["total_cents"] == 5500
    assert after["items"][0]["unit_price_cents"] == 2500


# --------------------------------------------------------------------------- edición (PUT)
def test_put_recalculates_with_current_prices_and_category(client):
    customer = make_customer(client, "premium", "put@example.com")
    product = make_product(client, price=2500)
    order = client.post("/orders", json=order_body(customer["id"], [(product["id"], 2)])).json()
    assert order["total_cents"] == 5500

    client.put(f"/products/{product['id']}", json={"name": "P", "price_cents": 4000, "stock": 10})
    client.put(f"/customers/{customer['id']}", json={"name": "C", "email": "put@example.com", "category": "business"})
    updated = client.put(f"/orders/{order['id']}", json=order_body(customer["id"], [(product["id"], 2)], "pickup"))

    assert updated.status_code == 200
    body = updated.json()
    assert body["id"] == order["id"]
    assert body["status"] == "pending"
    assert body["customer_category"] == "business"
    assert (body["subtotal_cents"], body["discount_cents"], body["shipping_cents"], body["total_cents"]) == (
        8000, 1200, 0, 6800)
    assert body["items"] == [{"product_id": product["id"], "quantity": 2, "unit_price_cents": 4000}]
    assert client.get(f"/orders/{order['id']}").json() == body


def test_put_replaces_lines_and_can_change_customer(client):
    first_customer = make_customer(client)
    second_customer = make_customer(client, "business")
    p1, p2 = make_product(client, price=100), make_product(client, price=200)
    order = client.post("/orders", json=order_body(first_customer["id"], [(p1["id"], 1)])).json()

    updated = client.put(f"/orders/{order['id']}", json=order_body(second_customer["id"], [(p2["id"], 3)])).json()

    assert updated["customer_id"] == second_customer["id"]
    assert [(i["product_id"], i["quantity"]) for i in updated["items"]] == [(p2["id"], 3)]
    assert len(client.get("/orders").json()) == 1


def test_put_unknown_order_returns_404(client):
    customer = make_customer(client)
    product = make_product(client)
    response = client.put("/orders/55", json=order_body(customer["id"], [(product["id"], 1)]))
    assert response.status_code == 404


@pytest.mark.parametrize("final_action", ["confirm", "cancel"])
def test_put_on_non_pending_order_returns_409_and_changes_nothing(client, final_action):
    customer = make_customer(client)
    product = make_product(client, price=1000, stock=10)
    order = client.post("/orders", json=order_body(customer["id"], [(product["id"], 1)])).json()
    assert client.post(f"/orders/{order['id']}/{final_action}").status_code == 200
    before = client.get(f"/orders/{order['id']}").json()

    response = client.put(f"/orders/{order['id']}", json=order_body(customer["id"], [(product["id"], 5)]))

    assert response.status_code == 409
    assert client.get(f"/orders/{order['id']}").json() == before


def test_failed_put_keeps_original_lines(client):
    customer = make_customer(client)
    product = make_product(client)
    order = client.post("/orders", json=order_body(customer["id"], [(product["id"], 2)])).json()
    response = client.put(f"/orders/{order['id']}", json=order_body(customer["id"], [(product["id"], 1), (999, 1)]))
    assert response.status_code == 404
    assert client.get(f"/orders/{order['id']}").json() == order


def test_put_with_unknown_customer_returns_404(client):
    customer = make_customer(client)
    product = make_product(client)
    order = client.post("/orders", json=order_body(customer["id"], [(product["id"], 1)])).json()
    assert client.put(f"/orders/{order['id']}", json=order_body(404, [(product["id"], 1)])).status_code == 404


# --------------------------------------------------------------------------- eliminar / cancelar
def test_delete_pending_order_removes_it_and_its_lines(client):
    customer = make_customer(client)
    product = make_product(client)
    order = client.post("/orders", json=order_body(customer["id"], [(product["id"], 1)])).json()
    response = client.delete(f"/orders/{order['id']}")
    assert response.status_code == 204
    assert response.content == b""
    assert client.get(f"/orders/{order['id']}").status_code == 404
    # Con el pedido eliminado ya no hay referencias: producto y cliente pueden borrarse.
    assert client.delete(f"/products/{product['id']}").status_code == 204
    assert client.delete(f"/customers/{customer['id']}").status_code == 204


def test_delete_unknown_order_returns_404(client):
    assert client.delete("/orders/9").status_code == 404


@pytest.mark.parametrize("final_action", ["confirm", "cancel"])
def test_delete_non_pending_order_returns_409(client, final_action):
    customer = make_customer(client)
    product = make_product(client)
    order = client.post("/orders", json=order_body(customer["id"], [(product["id"], 1)])).json()
    client.post(f"/orders/{order['id']}/{final_action}")
    assert client.delete(f"/orders/{order['id']}").status_code == 409
    assert client.get(f"/orders/{order['id']}").status_code == 200


def test_cancel_pending_order_does_not_touch_stock_or_notifications(client):
    customer = make_customer(client)
    product = make_product(client, stock=5)
    order = client.post("/orders", json=order_body(customer["id"], [(product["id"], 2)])).json()
    response = client.post(f"/orders/{order['id']}/cancel")
    assert response.status_code == 200
    assert response.json() == {"id": order["id"], "status": "cancelled"}
    assert client.get(f"/orders/{order['id']}").json()["status"] == "cancelled"
    assert stock_of(client, product) == 5
    assert client.get(f"/orders/{order['id']}/notifications").json() == []


def test_cancel_unknown_order_returns_404(client):
    assert client.post("/orders/9/cancel").status_code == 404


def test_cancel_twice_returns_409(client):
    customer = make_customer(client)
    product = make_product(client)
    order = client.post("/orders", json=order_body(customer["id"], [(product["id"], 1)])).json()
    assert client.post(f"/orders/{order['id']}/cancel").status_code == 200
    assert client.post(f"/orders/{order['id']}/cancel").status_code == 409


def test_cancel_confirmed_order_returns_409_and_keeps_stock_deducted(client):
    customer = make_customer(client)
    product = make_product(client, stock=5)
    order = client.post("/orders", json=order_body(customer["id"], [(product["id"], 2)])).json()
    assert client.post(f"/orders/{order['id']}/confirm").status_code == 200
    assert client.post(f"/orders/{order['id']}/cancel").status_code == 409
    assert client.get(f"/orders/{order['id']}").json()["status"] == "confirmed"
    assert stock_of(client, product) == 3


# --------------------------------------------------------------------------- confirmación
def test_confirm_deducts_stock_registers_notification_and_returns_message(client):
    customer = make_customer(client, "premium", "notify@example.com")
    product = make_product(client, price=2500, stock=5)
    order = client.post("/orders", json=order_body(customer["id"], [(product["id"], 2)])).json()

    response = client.post(f"/orders/{order['id']}/confirm")

    assert response.status_code == 200
    message = f"Pedido {order['id']} confirmado. Total: 5500 centavos."
    assert response.json() == {"id": order["id"], "status": "confirmed", "message": message}
    assert stock_of(client, product) == 3
    assert client.get(f"/orders/{order['id']}").json()["status"] == "confirmed"
    assert client.get(f"/orders/{order['id']}/notifications").json() == []   # aún en la cola
    drain_notifications(client)
    notifications = client.get(f"/orders/{order['id']}/notifications").json()
    assert len(notifications) == 1
    assert notifications[0]["order_id"] == order["id"]
    assert notifications[0]["recipient"] == "notify@example.com"
    assert notifications[0]["message"] == message


def test_confirm_deducts_every_line(client):
    customer = make_customer(client)
    p1, p2 = make_product(client, stock=10), make_product(client, stock=4)
    order = client.post("/orders", json=order_body(customer["id"], [(p1["id"], 3), (p2["id"], 4)])).json()
    assert client.post(f"/orders/{order['id']}/confirm").status_code == 200
    assert (stock_of(client, p1), stock_of(client, p2)) == (7, 0)


def test_confirm_with_exact_stock_succeeds(client):
    customer = make_customer(client)
    product = make_product(client, stock=2)
    order = client.post("/orders", json=order_body(customer["id"], [(product["id"], 2)])).json()
    assert client.post(f"/orders/{order['id']}/confirm").status_code == 200
    assert stock_of(client, product) == 0


def test_confirm_with_insufficient_stock_returns_409_and_names_product(client):
    customer = make_customer(client)
    product = make_product(client, stock=1)
    order = client.post("/orders", json=order_body(customer["id"], [(product["id"], 2)])).json()
    response = client.post(f"/orders/{order['id']}/confirm")
    assert response.status_code == 409
    assert response.json() == {"detail": f"Stock insuficiente para producto {product['id']}"}
    assert stock_of(client, product) == 1


def test_failed_confirm_can_succeed_after_restocking(client):
    customer = make_customer(client)
    product = make_product(client, stock=1)
    order = client.post("/orders", json=order_body(customer["id"], [(product["id"], 2)])).json()
    assert client.post(f"/orders/{order['id']}/confirm").status_code == 409
    client.put(f"/products/{product['id']}", json={"name": "P", "price_cents": 1000, "stock": 2})
    assert client.post(f"/orders/{order['id']}/confirm").status_code == 200
    assert stock_of(client, product) == 0
    drain_notifications(client)
    assert len(client.get(f"/orders/{order['id']}/notifications").json()) == 1


def test_confirm_unknown_order_returns_404(client):
    assert client.post("/orders/9/confirm").status_code == 404


def test_confirm_cancelled_order_returns_409(client):
    customer = make_customer(client)
    product = make_product(client, stock=5)
    order = client.post("/orders", json=order_body(customer["id"], [(product["id"], 1)])).json()
    client.post(f"/orders/{order['id']}/cancel")
    response = client.post(f"/orders/{order['id']}/confirm")
    assert response.status_code == 409
    assert stock_of(client, product) == 5


def test_second_order_competes_for_remaining_stock(client):
    customer = make_customer(client)
    product = make_product(client, stock=3)
    first = client.post("/orders", json=order_body(customer["id"], [(product["id"], 2)])).json()
    second = client.post("/orders", json=order_body(customer["id"], [(product["id"], 2)])).json()
    assert client.post(f"/orders/{first['id']}/confirm").status_code == 200
    assert client.post(f"/orders/{second['id']}/confirm").status_code == 409
    assert stock_of(client, product) == 1


def test_cannot_delete_product_or_customer_of_confirmed_order(client):
    customer = make_customer(client)
    product = make_product(client)
    order = client.post("/orders", json=order_body(customer["id"], [(product["id"], 1)])).json()
    client.post(f"/orders/{order['id']}/confirm")
    assert client.delete(f"/products/{product['id']}").status_code == 409
    assert client.delete(f"/customers/{customer['id']}").status_code == 409


# --------------------------------------------------------------------------- notificaciones
def test_notifications_of_unknown_order_returns_404(client):
    assert client.get("/orders/9/notifications").status_code == 404


def test_pending_order_has_no_notifications(client):
    customer = make_customer(client)
    product = make_product(client)
    order = client.post("/orders", json=order_body(customer["id"], [(product["id"], 1)])).json()
    assert client.get(f"/orders/{order['id']}/notifications").json() == []
