"""Unitarias: reglas de negocio de pedidos ejercitadas sobre OrderService, sin HTTP.

Solo usan la interfaz pública estable del servicio (quote, get, list, confirm, cancel, delete,
notifications) para que sigan siendo válidas, sin modificarse, tras refactorizar.
"""
import pytest
from fastapi import HTTPException

from app.orders import ItemInput, OrderInput, OrderService


def insert_customer(database, category="regular", email="c@example.com"):
    with database.connect() as connection:
        return connection.execute(
            "INSERT INTO customers(name, email, category) VALUES ('C', ?, ?)", (email, category)
        ).lastrowid


def insert_product(database, price=1000, stock=10):
    with database.connect() as connection:
        return connection.execute(
            "INSERT INTO products(name, price_cents, stock) VALUES ('P', ?, ?)", (price, stock)
        ).lastrowid


def order_input(customer_id, items, shipping="standard"):
    return OrderInput(
        customer_id=customer_id, address="Calle 1", city="Lima", postal_code="15001", shipping=shipping,
        items=[ItemInput(product_id=pid, quantity=qty) for pid, qty in items],
    )


def insert_pending_order(database, customer_id, product_id, quantity=1, total=1000):
    with database.connect() as connection:
        order_id = connection.execute(
            """INSERT INTO orders(customer_id, customer_category, address, city, postal_code, shipping,
               subtotal_cents, discount_cents, shipping_cents, total_cents)
               VALUES (?, 'regular', 'a', 'b', 'c', 'pickup', ?, 0, 0, ?)""",
            (customer_id, total, total),
        ).lastrowid
        connection.execute(
            "INSERT INTO order_items(order_id, product_id, quantity, unit_price_cents) VALUES (?, ?, ?, 1000)",
            (order_id, product_id, quantity),
        )
        return order_id


# ----------------------------------------------------------------------------- descuentos
@pytest.mark.parametrize("category, subtotal, expected_discount", [
    ("regular", 10_000, 0),
    ("premium", 10_000, 1_000),
    ("business", 10_000, 1_500),
    ("premium", 99, 9),       # 9,9 -> 9
    ("business", 99, 14),     # 14,85 -> 14
    ("premium", 9, 0),        # 0,9 -> 0
    ("business", 6, 0),       # 0,9 -> 0
    ("business", 7, 1),       # 1,05 -> 1
])
def test_discount_by_category_is_truncated_to_whole_cents(database, category, subtotal, expected_discount):
    customer = insert_customer(database, category)
    product = insert_product(database, price=subtotal)
    result = OrderService(database).quote(order_input(customer, [(product, 1)], "pickup"))
    assert result["discount_cents"] == expected_discount
    assert result["total_cents"] == subtotal - expected_discount


# ----------------------------------------------------------------------------- envío
@pytest.mark.parametrize("shipping, subtotal, expected", [
    ("pickup", 1, 0),
    ("pickup", 50_000, 0),
    ("standard", 1, 1_000),
    ("standard", 9_999, 1_000),
    ("standard", 10_000, 0),
    ("standard", 10_001, 0),
    ("express", 1, 2_500),
    ("express", 10_000, 2_500),
    ("express", 1_000_000, 2_500),
])
def test_shipping_cost_rules(database, shipping, subtotal, expected):
    customer = insert_customer(database)
    product = insert_product(database, price=subtotal)
    result = OrderService(database).quote(order_input(customer, [(product, 1)], shipping))
    assert result["shipping_cents"] == expected


def test_total_is_subtotal_minus_discount_plus_shipping(database):
    customer = insert_customer(database, "business")
    first, second = insert_product(database, price=1_234), insert_product(database, price=777)
    result = OrderService(database).quote(order_input(customer, [(first, 3), (second, 2)], "express"))
    subtotal = 1_234 * 3 + 777 * 2
    discount = subtotal * 15 // 100
    assert result == {"subtotal_cents": subtotal, "discount_cents": discount,
                      "shipping_cents": 2_500, "total_cents": subtotal - discount + 2_500}


def test_quote_uses_current_catalog_price(database):
    customer = insert_customer(database)
    product = insert_product(database, price=1_000)
    service = OrderService(database)
    assert service.quote(order_input(customer, [(product, 1)], "pickup"))["subtotal_cents"] == 1_000
    with database.connect() as connection:
        connection.execute("UPDATE products SET price_cents = 3000 WHERE id = ?", (product,))
    assert service.quote(order_input(customer, [(product, 1)], "pickup"))["subtotal_cents"] == 3_000


def test_quote_rejects_missing_customer_and_product(database):
    customer = insert_customer(database)
    product = insert_product(database)
    service = OrderService(database)
    with pytest.raises(HTTPException) as no_customer:
        service.quote(order_input(99, [(product, 1)]))
    with pytest.raises(HTTPException) as no_product:
        service.quote(order_input(customer, [(99, 1)]))
    assert (no_customer.value.status_code, no_product.value.status_code) == (404, 404)


# ----------------------------------------------------------------------------- validación de entrada
def test_order_input_requires_unique_products():
    with pytest.raises(ValueError, match="No repita productos"):
        order_input(1, [(5, 1), (5, 2)])


def test_order_input_strips_whitespace():
    data = OrderInput(customer_id=1, address="  Calle  ", city=" Lima ", postal_code=" 1 ",
                      items=[{"product_id": 1, "quantity": 1}])
    assert (data.address, data.city, data.postal_code, data.shipping) == ("Calle", "Lima", "1", "standard")


# ----------------------------------------------------------------------------- estados y stock
def test_confirm_is_atomic_when_a_later_line_lacks_stock(database):
    customer = insert_customer(database)
    plenty, scarce = insert_product(database, stock=10), insert_product(database, stock=0)
    order_id = insert_pending_order(database, customer, plenty, quantity=3)
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO order_items(order_id, product_id, quantity, unit_price_cents) VALUES (?, ?, 1, 1000)",
            (order_id, scarce),
        )
    service = OrderService(database)

    with pytest.raises(HTTPException) as error:
        service.confirm(order_id)

    assert error.value.status_code == 409
    with database.connect() as connection:
        assert connection.execute("SELECT stock FROM products WHERE id = ?", (plenty,)).fetchone()[0] == 10
        assert connection.execute("SELECT status FROM orders WHERE id = ?", (order_id,)).fetchone()[0] == "pending"
    assert service.notifications(order_id) == []


def test_status_transitions_are_final(database):
    customer = insert_customer(database)
    product = insert_product(database, stock=10)
    service = OrderService(database)
    confirmed = insert_pending_order(database, customer, product)
    cancelled = insert_pending_order(database, customer, product)

    service.confirm(confirmed)
    service.cancel(cancelled)

    for order_id in (confirmed, cancelled):
        for action in (service.confirm, service.cancel, service.delete):
            with pytest.raises(HTTPException) as error:
                action(order_id)
            assert error.value.status_code == 409


def test_confirm_records_exactly_one_notification_with_customer_email(database):
    customer = insert_customer(database, email="who@example.com")
    product = insert_product(database, stock=2)
    order_id = insert_pending_order(database, customer, product, total=4321)
    service = OrderService(database)

    result = service.confirm(order_id)

    assert result == {"id": order_id, "status": "confirmed",
                      "message": f"Pedido {order_id} confirmado. Total: 4321 centavos."}
    notifications = service.notifications(order_id)
    assert [(n["recipient"], n["message"]) for n in notifications] == [("who@example.com", result["message"])]


def test_get_list_and_missing_orders(database):
    customer = insert_customer(database)
    product = insert_product(database)
    service = OrderService(database)
    first = insert_pending_order(database, customer, product, quantity=2)
    second = insert_pending_order(database, customer, product)

    assert [o["id"] for o in service.list()] == [first, second]
    assert service.get(first)["items"] == [{"product_id": product, "quantity": 2, "unit_price_cents": 1000}]
    for action in (service.get, service.confirm, service.cancel, service.delete, service.notifications):
        with pytest.raises(HTTPException) as error:
            action(999)
        assert error.value.status_code == 404
