"""Persistencia: transacciones, integridad referencial, reinicio y manejo de base no disponible."""
import sqlite3
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from app.database import Database
from main import create_app
from tests.helpers import drain_notifications, make_customer, make_product, order_body


def test_initialize_creates_parent_directory_and_is_idempotent(tmp_path):
    db = Database(tmp_path / "nested" / "dir" / "store.sqlite3")
    db.initialize()
    db.initialize()
    with db.connect() as connection:
        tables = {row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"products", "customers", "orders", "order_items", "notifications"} <= tables


def test_connect_commits_on_success(database):
    with database.connect() as connection:
        connection.execute("INSERT INTO products(name, price_cents, stock) VALUES ('A', 10, 1)")
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 1


def test_connect_rolls_back_on_exception(database):
    with pytest.raises(RuntimeError):
        with database.connect() as connection:
            connection.execute("INSERT INTO products(name, price_cents, stock) VALUES ('A', 10, 1)")
            raise RuntimeError("fallo")
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0


def test_foreign_keys_are_enforced(database):
    with pytest.raises(sqlite3.IntegrityError):
        with database.connect() as connection:
            connection.execute(
                """INSERT INTO orders(customer_id, customer_category, address, city, postal_code, shipping,
                   subtotal_cents, discount_cents, shipping_cents, total_cents)
                   VALUES (999, 'regular', 'a', 'b', 'c', 'pickup', 1, 0, 0, 1)"""
            )


@pytest.mark.parametrize("statement", [
    "INSERT INTO products(name, price_cents, stock) VALUES ('A', 0, 1)",
    "INSERT INTO products(name, price_cents, stock) VALUES ('A', 10, -1)",
])
def test_check_constraints_reject_invalid_products(database, statement):
    with pytest.raises(sqlite3.IntegrityError):
        with database.connect() as connection:
            connection.execute(statement)


def test_deleting_pending_order_cascades_to_lines(client, db_path):
    customer = make_customer(client)
    product = make_product(client)
    order = client.post("/orders", json=order_body(customer["id"], [(product["id"], 1)])).json()
    assert client.delete(f"/orders/{order['id']}").status_code == 204
    with closing(sqlite3.connect(db_path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM order_items").fetchone()[0] == 0


def test_confirmed_state_stock_and_notification_survive_restart(db_path):
    with TestClient(create_app(db_path)) as first:
        customer = make_customer(first)
        product = make_product(first, stock=5)
        order = first.post("/orders", json=order_body(customer["id"], [(product["id"], 2)])).json()
        assert first.post(f"/orders/{order['id']}/confirm").status_code == 200
    with TestClient(create_app(db_path)) as second:
        assert second.get(f"/orders/{order['id']}").json()["status"] == "confirmed"
        assert second.get(f"/products/{product['id']}").json()["stock"] == 3
        assert len(second.get("/notifications/queue").json()) == 1      # el pendiente sobrevivió al reinicio
        drain_notifications(second)
        assert len(second.get(f"/orders/{order['id']}/notifications").json()) == 1
        assert second.post(f"/orders/{order['id']}/confirm").status_code == 409


def test_lab_db_path_environment_variable_selects_database(tmp_path, monkeypatch):
    path = tmp_path / "from-env.sqlite3"
    monkeypatch.setenv("LAB_DB_PATH", str(path))
    with TestClient(create_app()) as client:
        make_product(client)
    assert path.exists()


def test_database_unavailable_returns_503_with_detail(client, db_path):
    with closing(sqlite3.connect(db_path)) as connection:
        for table in ("order_items", "notifications", "orders", "products"):
            connection.execute(f"DROP TABLE {table}")
        connection.commit()
    response = client.get("/products")
    assert response.status_code == 503
    assert response.json() == {"detail": "Base de datos no disponible; reintente más tarde"}
