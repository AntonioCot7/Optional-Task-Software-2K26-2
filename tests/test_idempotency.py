"""Adicional 1: idempotencia de POST /orders con la cabecera Idempotency-Key."""
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from app import idempotency, order_repository
from app.orders import ItemInput, OrderInput, OrderService
from main import create_app
from tests.helpers import make_customer, make_product, order_body

KEY = "pedido-123"


def post(client, body, key=KEY):
    headers = {} if key is None else {"Idempotency-Key": key}
    return client.post("/orders", json=body, headers=headers)


@pytest.fixture
def setup(client):
    customer = make_customer(client, "premium")
    product = make_product(client, price=2500, stock=10)
    return client, customer, product, order_body(customer["id"], [(product["id"], 2)])


def count_orders(client):
    return len(client.get("/orders").json())


def count_keys(db_path):
    with closing(sqlite3.connect(db_path)) as connection:
        return connection.execute("SELECT COUNT(*) FROM idempotency_keys").fetchone()[0]


# ------------------------------------------------------------- misma clave y mismo contenido
def test_same_key_and_content_returns_original_result_without_creating_another_order(setup):
    client, _, _, body = setup
    first = post(client, body)
    second = post(client, body)

    assert first.status_code == 201 and second.status_code == 201
    assert second.json() == first.json()
    assert "Idempotent-Replayed" not in first.headers
    assert second.headers["Idempotent-Replayed"] == "true"
    assert count_orders(client) == 1


def test_replay_returns_original_snapshot_even_if_catalog_changed_meanwhile(setup):
    client, _, product, body = setup
    first = post(client, body).json()
    client.put(f"/products/{product['id']}", json={"name": "Caro", "price_cents": 9000, "stock": 10})
    replay = post(client, body)
    assert replay.json() == first
    assert replay.json()["total_cents"] == 5500
    assert count_orders(client) == 1


def test_replay_does_not_recreate_an_order_that_was_deleted_afterwards(setup):
    client, _, _, body = setup
    first = post(client, body).json()
    assert client.delete(f"/orders/{first['id']}").status_code == 204
    replay = post(client, body)
    # Alcance documentado: la clave identifica la petición de creación; se devuelve su respuesta original.
    assert replay.status_code == 201 and replay.json() == first
    assert count_orders(client) == 0


def test_item_order_does_not_change_the_content_fingerprint(client):
    customer = make_customer(client)
    first_product, second_product = make_product(client), make_product(client)
    forward = order_body(customer["id"], [(first_product["id"], 1), (second_product["id"], 2)])
    backward = order_body(customer["id"], [(second_product["id"], 2), (first_product["id"], 1)])
    original = post(client, forward)
    replay = post(client, backward)
    assert replay.json() == original.json()
    assert count_orders(client) == 1


def test_different_keys_create_different_orders(setup):
    client, _, _, body = setup
    assert post(client, body, "k1").json()["id"] != post(client, body, "k2").json()["id"]
    assert count_orders(client) == 2


def test_without_key_every_request_creates_an_order(setup):
    client, _, _, body = setup
    post(client, body, None)
    post(client, body, None)
    assert count_orders(client) == 2


# ------------------------------------------------------------- misma clave y contenido distinto
@pytest.mark.parametrize("change", [
    lambda b: b["items"][0].update(quantity=3),
    lambda b: b.update(shipping="express"),
    lambda b: b.update(address="Otra calle 5"),
    lambda b: b.update(city="Cusco"),
    lambda b: b.update(postal_code="99999"),
])
def test_same_key_with_different_content_returns_409_and_creates_nothing(setup, change):
    client, _, _, body = setup
    first = post(client, body)
    altered = {**body, "items": [dict(item) for item in body["items"]]}
    change(altered)

    conflict = post(client, altered)

    assert conflict.status_code == 409
    assert conflict.json() == {"detail": idempotency.CONFLICT_MESSAGE}
    assert count_orders(client) == 1
    assert post(client, body).json() == first.json()      # la clave sigue ligada al contenido original


def test_same_key_with_different_customer_returns_409(setup):
    client, _, product, body = setup
    post(client, body)
    other = make_customer(client)
    assert post(client, order_body(other["id"], [(product["id"], 2)])).status_code == 409


# ------------------------------------------------------------- validación de la clave
@pytest.mark.parametrize("key", ["", "con espacio", "a" * 256, "ñandú"])
def test_invalid_key_is_rejected_with_422(setup, key):
    client, _, _, body = setup
    try:
        response = post(client, body, key)
    except UnicodeEncodeError:   # httpx no puede ni enviar cabeceras no ASCII
        return
    assert response.status_code == 422
    assert count_orders(client) == 0


def test_boundary_key_lengths_are_accepted(setup):
    client, _, _, body = setup
    assert post(client, body, "k").status_code == 201
    assert post(client, body, "x" * 255).status_code == 201


# ------------------------------------------------------------- concurrencia
def test_concurrent_requests_with_same_key_create_a_single_order(db_path):
    app = create_app(db_path)
    with TestClient(app) as client:
        customer = make_customer(client)
        product = make_product(client)
        body = order_body(customer["id"], [(product["id"], 1)])
        barrier = threading.Barrier(8)

        def attempt(_):
            barrier.wait()
            return post(client, body, "carrera")

        with ThreadPoolExecutor(max_workers=8) as pool:
            responses = list(pool.map(attempt, range(8)))

        assert [r.status_code for r in responses] == [201] * 8
        assert len({r.json()["id"] for r in responses}) == 1
        assert sum("Idempotent-Replayed" in r.headers for r in responses) == 7
        assert count_orders(client) == 1
        assert count_keys(db_path) == 1


def test_concurrent_requests_at_service_level_use_independent_connections(database):
    with database.connect() as connection:
        customer = connection.execute("INSERT INTO customers(name, email, category) VALUES ('C','c@x.com','regular')").lastrowid
        product = connection.execute("INSERT INTO products(name, price_cents, stock) VALUES ('P', 100, 5)").lastrowid
    data = OrderInput(customer_id=customer, address="a", city="b", postal_code="c",
                      items=[ItemInput(product_id=product, quantity=1)])
    barrier = threading.Barrier(10)

    def attempt(_):
        barrier.wait()
        return OrderService(database).create(data, "k-servicio")

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(attempt, range(10)))

    assert sorted(replayed for _, replayed in results) == [False] + [True] * 9
    assert len({order["id"] for order, _ in results}) == 1
    assert len(OrderService(database).list()) == 1


def test_concurrent_requests_with_different_content_and_same_key_yield_one_order_and_conflicts(database):
    with database.connect() as connection:
        customer = connection.execute("INSERT INTO customers(name, email, category) VALUES ('C','c@x.com','regular')").lastrowid
        product = connection.execute("INSERT INTO products(name, price_cents, stock) VALUES ('P', 100, 5)").lastrowid
    barrier = threading.Barrier(6)

    def attempt(quantity):
        data = OrderInput(customer_id=customer, address="a", city="b", postal_code="c",
                          items=[ItemInput(product_id=product, quantity=quantity)])
        barrier.wait()
        try:
            return OrderService(database).create(data, "k-mixta")[0]["id"]
        except Exception as error:   # HTTPException 409
            return error.status_code

    with ThreadPoolExecutor(max_workers=6) as pool:
        outcomes = list(pool.map(attempt, range(1, 7)))

    assert sum(1 for outcome in outcomes if outcome != 409) >= 1
    assert len(OrderService(database).list()) == 1
    winners = {outcome for outcome in outcomes if outcome != 409}
    assert len(winners) == 1


# ------------------------------------------------------------- persistencia
def test_behavior_survives_application_restart(db_path):
    with TestClient(create_app(db_path)) as first:
        customer = make_customer(first)
        product = make_product(first)
        body = order_body(customer["id"], [(product["id"], 1)])
        original = post(first, body)
    with TestClient(create_app(db_path)) as second:
        replay = post(second, body)
        conflict = post(second, {**body, "city": "Otra"})
        assert replay.status_code == 201 and replay.json() == original.json()
        assert replay.headers["Idempotent-Replayed"] == "true"
        assert conflict.status_code == 409
        assert count_orders(second) == 1


def test_existing_database_without_the_table_is_migrated_on_startup(tmp_path):
    path = tmp_path / "old.sqlite3"
    with TestClient(create_app(path)) as client:
        make_customer(client)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("DROP TABLE idempotency_keys")
        connection.commit()
    with TestClient(create_app(path)) as client:      # la inicialización la vuelve a crear
        customer = client.get("/customers").json()[0]
        product = make_product(client)
        body = order_body(customer["id"], [(product["id"], 1)])
        assert post(client, body).status_code == 201
        assert post(client, body).headers["Idempotent-Replayed"] == "true"


# ------------------------------------------------------------- errores: sin clave bloqueada ni pedido parcial
def test_failed_request_does_not_block_the_key(client):
    product = make_product(client)
    body = order_body(999, [(product["id"], 1)])            # cliente inexistente
    assert post(client, body).status_code == 404
    customer = make_customer(client)
    good = order_body(customer["id"], [(product["id"], 1)])
    assert post(client, good).status_code == 201             # la misma clave sirve después del error
    assert count_orders(client) == 1


def test_validation_error_does_not_store_the_key(setup, db_path):
    client, _, _, body = setup
    assert post(client, {**body, "items": []}).status_code == 422
    assert count_keys(db_path) == 0
    assert post(client, body).status_code == 201


def test_failure_after_inserting_order_rolls_back_order_and_key(db_path, monkeypatch):
    app = create_app(db_path)
    with TestClient(app, raise_server_exceptions=False) as client:
        customer = make_customer(client)
        product = make_product(client)
        body = order_body(customer["id"], [(product["id"], 1)])

        def explode(*args, **kwargs):
            raise RuntimeError("fallo simulado al guardar la clave")

        monkeypatch.setattr(idempotency.IdempotencyRepository, "save", explode)
        assert post(client, body).status_code == 500
        assert count_orders(client) == 0                      # no quedó pedido parcial
        assert count_keys(db_path) == 0                       # ni clave bloqueada

        monkeypatch.undo()
        retried = post(client, body)
        assert retried.status_code == 201 and "Idempotent-Replayed" not in retried.headers
        assert count_orders(client) == 1


def test_failure_while_writing_lines_leaves_no_partial_order(db_path, monkeypatch):
    app = create_app(db_path)
    with TestClient(app, raise_server_exceptions=False) as client:
        customer = make_customer(client)
        product = make_product(client)
        body = order_body(customer["id"], [(product["id"], 1)])
        original = order_repository.OrderRepository.replace_items

        def explode(self, *args, **kwargs):
            raise RuntimeError("fallo simulado")

        monkeypatch.setattr(order_repository.OrderRepository, "replace_items", explode)
        assert post(client, body).status_code == 500
        assert count_orders(client) == 0 and count_keys(db_path) == 0
        monkeypatch.setattr(order_repository.OrderRepository, "replace_items", original)
        assert post(client, body).status_code == 201
