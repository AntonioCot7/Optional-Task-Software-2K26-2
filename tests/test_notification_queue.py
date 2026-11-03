"""Adicional 2: cola de notificaciones, worker, reintentos, DLQ, recuperación y duplicados."""
import sqlite3
import threading
import time
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from app.database import Database
from app.notification_queue import INTERRUPTED_ERROR, NotificationQueue, WorkerSettings
from app.notification_worker import (
    NotificationWorker,
    PermanentDeliveryError,
    SimulatedSender,
    TemporaryDeliveryError,
    settings_from_environment,
)
from main import create_app
from tests.helpers import make_customer, make_product, order_body

SETTINGS = WorkerSettings(max_attempts=3, base_delay_seconds=2.0, lease_seconds=30.0, poll_interval_seconds=0.01)


class FakeClock:
    def __init__(self):
        self.now = time.time() + 1   # confirm encola con la hora real; el reloj falso parte justo después

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def worker(client, clock):
    return NotificationWorker(client.app.state.database, settings=SETTINGS, clock=clock)


def confirmed_order(client, email="cliente@example.com", stock=10):
    customer = make_customer(client, email=email)
    product = make_product(client, stock=stock)
    order = client.post("/orders", json=order_body(customer["id"], [(product["id"], 1)])).json()
    assert client.post(f"/orders/{order['id']}/confirm").status_code == 200
    return order


def queue_rows(client):
    return client.get("/notifications/queue").json()


def dlq_rows(client):
    return client.get("/notifications/dlq").json()


def local_notifications(client, order):
    return client.get(f"/orders/{order['id']}/notifications").json()


# ------------------------------------------------------------- confirmar deja pendiente; el worker procesa aparte
def test_confirm_leaves_a_pending_message_and_the_worker_processes_it_separately(client, worker):
    order = confirmed_order(client, "ana@example.com")

    pending = queue_rows(client)
    assert len(pending) == 1
    assert (pending[0]["order_id"], pending[0]["recipient"], pending[0]["status"], pending[0]["attempts"]) == (
        order["id"], "ana@example.com", "pending", 0)
    assert local_notifications(client, order) == []

    assert worker.drain() == ["sent"]

    assert queue_rows(client) == []
    notifications = local_notifications(client, order)
    assert len(notifications) == 1
    assert notifications[0]["recipient"] == "ana@example.com"
    assert notifications[0]["message"].startswith(f"Pedido {order['id']} confirmado")
    assert set(notifications[0]) == {"id", "order_id", "recipient", "message"}   # el contrato no cambia


def test_failed_confirmation_enqueues_nothing(client):
    customer = make_customer(client)
    product = make_product(client, stock=0)
    order = client.post("/orders", json=order_body(customer["id"], [(product["id"], 1)])).json()
    assert client.post(f"/orders/{order['id']}/confirm").status_code == 409
    assert queue_rows(client) == []


def test_worker_with_empty_queue_does_nothing(worker):
    assert worker.run_once() is None
    assert worker.drain() == []


# ------------------------------------------------------------- reintentos con espera y máximo
def test_temporary_failures_are_retried_with_exponential_wait_until_success(client, worker, clock):
    order = confirmed_order(client, "temp-fail-2@example.com")

    assert worker.run_once() == "retry"                       # intento 1 falla
    first = queue_rows(client)[0]
    assert (first["status"], first["attempts"], first["next_attempt_at"]) == ("pending", 1, clock.now + 2.0)
    assert "entrega 1" in first["last_error"]
    assert worker.run_once() is None                          # aún no toca: hay que esperar

    clock.advance(1.9)
    assert worker.run_once() is None
    clock.advance(0.1)
    assert worker.run_once() == "retry"                       # intento 2 falla, espera el doble
    second = queue_rows(client)[0]
    assert (second["attempts"], second["next_attempt_at"]) == (2, clock.now + 4.0)

    clock.advance(4.0)
    assert worker.run_once() == "sent"                        # intento 3 tiene éxito
    assert queue_rows(client) == [] and dlq_rows(client) == []
    assert len(local_notifications(client, order)) == 1


def test_exhausting_attempts_moves_message_to_dlq_keeping_content_attempts_and_last_error(client, worker, clock):
    order = confirmed_order(client, "temp-fail-9@example.com")
    original = queue_rows(client)[0]

    outcomes = []
    for _ in range(3):
        outcomes.append(worker.run_once())
        clock.advance(10)

    assert outcomes == ["retry", "retry", "dead"]
    assert queue_rows(client) == []
    (dead,) = dlq_rows(client)
    assert dead["order_id"] == order["id"]
    assert dead["recipient"] == "temp-fail-9@example.com"
    assert dead["message"] == original["message"]
    assert (dead["attempts"], dead["total_attempts"]) == (3, 3)
    assert "entrega 3" in dead["last_error"]
    assert dead["dedup_key"] == f"order-confirmed:{order['id']}"
    assert local_notifications(client, order) == []          # nunca llegó a entregarse


def test_permanent_failure_goes_straight_to_dlq_without_retries(client, worker):
    confirmed_order(client, "perm-fail@example.com")
    assert worker.run_once() == "dead"
    (dead,) = dlq_rows(client)
    assert dead["attempts"] == 1
    assert dead["last_error"] == "Buzón inexistente (simulado)"
    assert queue_rows(client) == []


def test_unexpected_sender_exception_is_treated_as_temporary(client, clock):
    class Exploding:
        def send(self, recipient, message, *, attempt):
            raise RuntimeError("socket roto")

    confirmed_order(client)
    worker = NotificationWorker(client.app.state.database, sender=Exploding(), settings=SETTINGS, clock=clock)
    assert worker.run_once() == "retry"
    assert queue_rows(client)[0]["last_error"] == "socket roto"


def test_max_attempts_is_configurable(client, clock):
    confirmed_order(client, "temp-fail-9@example.com")
    worker = NotificationWorker(client.app.state.database, clock=clock,
                                settings=WorkerSettings(max_attempts=1, base_delay_seconds=1))
    assert worker.run_once() == "dead"
    assert dlq_rows(client)[0]["attempts"] == 1


# ------------------------------------------------------------- DLQ: consulta y reprocesamiento explícito
def test_dlq_reprocess_requeues_with_fresh_attempt_cycle_and_can_then_succeed(client, worker, clock):
    order = confirmed_order(client, "temp-fail-4@example.com")
    for _ in range(3):
        worker.run_once()
        clock.advance(10)
    (dead,) = dlq_rows(client)

    response = client.post(f"/notifications/dlq/{dead['id']}/reprocess")

    assert response.status_code == 200
    assert response.json()["status"] == "requeued" and response.json()["dlq_id"] == dead["id"]
    assert dlq_rows(client) == []                              # ya no figura como pendiente de atención
    requeued = queue_rows(client)[0]
    assert (requeued["attempts"], requeued["total_attempts"], requeued["message"]) == (0, 3, dead["message"])

    assert worker.run_once() == "retry"                        # entrega 4 (acumulada) aún falla
    clock.advance(10)
    assert worker.run_once() == "sent"                         # entrega 5 tiene éxito
    assert len(local_notifications(client, order)) == 1


def test_reprocessing_a_permanent_failure_fails_again_and_lands_in_dlq_again(client, worker):
    confirmed_order(client, "perm-fail@example.com")
    worker.run_once()
    (dead,) = dlq_rows(client)
    assert client.post(f"/notifications/dlq/{dead['id']}/reprocess").status_code == 200
    assert worker.run_once() == "dead"
    (again,) = dlq_rows(client)
    assert again["id"] != dead["id"] and again["total_attempts"] == 2


def test_reprocess_unknown_dlq_entry_returns_404(client):
    assert client.post("/notifications/dlq/99/reprocess").status_code == 404


def test_reprocess_twice_returns_409(client, worker):
    confirmed_order(client, "perm-fail@example.com")
    worker.run_once()
    dlq_id = dlq_rows(client)[0]["id"]
    assert client.post(f"/notifications/dlq/{dlq_id}/reprocess").status_code == 200
    assert client.post(f"/notifications/dlq/{dlq_id}/reprocess").status_code == 409


def test_reprocess_conflicts_when_equivalent_message_is_already_queued(client, worker, db_path):
    order = confirmed_order(client, "perm-fail@example.com")
    worker.run_once()
    dlq_id = dlq_rows(client)[0]["id"]
    with closing(sqlite3.connect(db_path)) as connection:      # alguien ya reencoló el mismo mensaje
        connection.execute(
            "INSERT INTO notification_queue(order_id, recipient, message, dedup_key, next_attempt_at) "
            "VALUES (?, 'x@example.com', 'm', ?, 0)", (order["id"], f"order-confirmed:{order['id']}"))
        connection.commit()
    assert client.post(f"/notifications/dlq/{dlq_id}/reprocess").status_code == 409
    assert len(dlq_rows(client)) == 1


# ------------------------------------------------------------- pendientes sobreviven al reinicio
def test_pending_messages_survive_restart_and_are_processed_afterwards(db_path, clock):
    with TestClient(create_app(db_path)) as first:
        order = confirmed_order(first, "reinicio@example.com")
        assert len(queue_rows(first)) == 1
    with TestClient(create_app(db_path)) as second:
        assert len(queue_rows(second)) == 1                    # sigue pendiente
        NotificationWorker(second.app.state.database, settings=SETTINGS, clock=clock).drain()
        assert queue_rows(second) == []
        assert len(local_notifications(second, order)) == 1


def test_retry_schedule_and_dlq_survive_restart(db_path, clock):
    with TestClient(create_app(db_path)) as first:
        confirmed_order(first, "temp-fail-9@example.com")
        NotificationWorker(first.app.state.database, settings=SETTINGS, clock=clock).run_once()
    with TestClient(create_app(db_path)) as second:
        retry = queue_rows(second)[0]
        assert (retry["attempts"], retry["status"]) == (1, "pending")
        worker = NotificationWorker(second.app.state.database, settings=SETTINGS, clock=clock)
        for _ in range(2):
            clock.advance(10)
            worker.run_once()
    with TestClient(create_app(db_path)) as third:
        assert len(dlq_rows(third)) == 1


# ------------------------------------------------------------- worker interrumpido: recuperación por lease
def test_message_in_processing_is_recovered_after_lease_expires(client, worker, clock):
    order = confirmed_order(client)
    with client.app.state.database.connect() as connection:    # el worker reclama y «muere» sin confirmar
        claimed = NotificationQueue(connection).claim_next(clock.now, SETTINGS)
    assert claimed["status"] == "processing" and claimed["attempts"] == 1
    assert queue_rows(client)[0]["status"] == "processing"

    assert worker.run_once() is None                           # el lease sigue vigente
    clock.advance(SETTINGS.lease_seconds + 1)
    assert worker.run_once() == "sent"                         # lease vencido: otro intento lo recupera
    assert len(local_notifications(client, order)) == 1
    assert queue_rows(client) == []


def test_interrupted_message_that_exhausted_attempts_goes_to_dlq_instead_of_looping(client, worker, clock):
    confirmed_order(client)
    for _ in range(SETTINGS.max_attempts):                     # el worker muere en cada intento
        with client.app.state.database.connect() as connection:
            assert NotificationQueue(connection).claim_next(clock.now, SETTINGS) is not None
        clock.advance(SETTINGS.lease_seconds + 1)

    assert worker.run_once() is None                           # al reclamar, lo detecta y lo manda a la DLQ
    (dead,) = dlq_rows(client)
    assert dead["last_error"] == INTERRUPTED_ERROR and dead["attempts"] == SETTINGS.max_attempts
    assert queue_rows(client) == []


# ------------------------------------------------------------- duplicados
def test_enqueueing_the_same_confirmation_twice_keeps_one_message(database):
    with database.connect() as connection:
        connection.execute("INSERT INTO customers(name, email, category) VALUES ('C', 'c@x.com', 'regular')")
        connection.execute("INSERT INTO orders(customer_id, customer_category, address, city, postal_code, shipping,"
                           " subtotal_cents, discount_cents, shipping_cents, total_cents)"
                           " VALUES (1, 'regular', 'a', 'b', 'c', 'pickup', 1, 0, 0, 1)")
        queue = NotificationQueue(connection)
        assert queue.enqueue(1, "c@x.com", "hola", "order-confirmed:1", 0.0) is True
        assert queue.enqueue(1, "c@x.com", "hola", "order-confirmed:1", 0.0) is False
        assert len(queue.list_queue()) == 1


def test_processing_a_duplicate_message_does_not_duplicate_the_notification_effect(client, worker, db_path):
    order = confirmed_order(client, "dup@example.com")
    assert worker.drain() == ["sent"]
    message = local_notifications(client, order)[0]["message"]

    with closing(sqlite3.connect(db_path)) as connection:      # redelivery: el mismo mensaje vuelve a la cola
        connection.execute(
            "INSERT INTO notification_queue(order_id, recipient, message, dedup_key, next_attempt_at) "
            "VALUES (?, 'dup@example.com', ?, ?, 0)", (order["id"], message, f"order-confirmed:{order['id']}"))
        connection.commit()

    assert worker.drain() == ["sent"]                          # se «procesa» de nuevo...
    assert len(local_notifications(client, order)) == 1        # ...pero el efecto no se duplica
    assert queue_rows(client) == []


def test_complete_reports_whether_the_effect_was_created(database):
    with database.connect() as connection:
        connection.execute("INSERT INTO customers(name, email, category) VALUES ('C', 'c@x.com', 'regular')")
        connection.execute("INSERT INTO orders(customer_id, customer_category, address, city, postal_code, shipping,"
                           " subtotal_cents, discount_cents, shipping_cents, total_cents)"
                           " VALUES (1, 'regular', 'a', 'b', 'c', 'pickup', 1, 0, 0, 1)")
        queue = NotificationQueue(connection)
        assert queue.complete(1, 1, "c@x.com", "m", "k") is True
        assert queue.complete(1, 1, "c@x.com", "m", "k") is False


# ------------------------------------------------------------- worker en la aplicación
def test_background_worker_thread_processes_confirmations_automatically(db_path):
    fast = WorkerSettings(max_attempts=3, base_delay_seconds=0.01, poll_interval_seconds=0.01)
    with TestClient(create_app(db_path, run_worker=True, worker_settings=fast)) as client:
        order = confirmed_order(client, "temp-fail-1@example.com")
        deadline = time.time() + 10
        while time.time() < deadline and not local_notifications(client, order):
            time.sleep(0.02)
        assert len(local_notifications(client, order)) == 1
        assert queue_rows(client) == []
    assert not [t for t in threading.enumerate() if t.name == "notification-worker"]   # se detuvo al cerrar


def test_run_forever_survives_errors_and_stops_on_event(database, clock):
    worker = NotificationWorker(database, settings=SETTINGS, clock=clock)
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise sqlite3.OperationalError("database is locked")
        stop.set()
        return None

    worker.run_once = flaky
    stop = threading.Event()
    thread = threading.Thread(target=worker.run_forever, args=(stop,))
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive() and len(calls) == 2


# ------------------------------------------------------------- simulación, política y migración
@pytest.mark.parametrize("recipient, attempt, expected", [
    ("ok@example.com", 1, None),
    ("temp-fail-2@example.com", 1, TemporaryDeliveryError),
    ("temp-fail-2@example.com", 2, TemporaryDeliveryError),
    ("temp-fail-2@example.com", 3, None),
    ("perm-fail@example.com", 1, PermanentDeliveryError),
    ("perm-fail@example.com", 7, PermanentDeliveryError),
    ("temp-fail@example.com", 1, None),         # sin número no es una regla
])
def test_simulated_sender_is_deterministic(recipient, attempt, expected):
    sender = SimulatedSender()
    if expected is None:
        assert sender.send(recipient, "m", attempt=attempt) is None
    else:
        with pytest.raises(expected):
            sender.send(recipient, "m", attempt=attempt)


def test_retry_delay_doubles_each_attempt():
    settings = WorkerSettings(base_delay_seconds=3)
    assert [settings.retry_delay(n) for n in (1, 2, 3, 4)] == [3, 6, 12, 24]


def test_settings_can_be_overridden_from_environment(monkeypatch):
    monkeypatch.setenv("LAB_NOTIFICATION_MAX_ATTEMPTS", "5")
    monkeypatch.setenv("LAB_NOTIFICATION_BASE_DELAY", "0.5")
    monkeypatch.setenv("LAB_NOTIFICATION_LEASE", "9")
    monkeypatch.setenv("LAB_NOTIFICATION_POLL", "0.25")
    assert settings_from_environment() == WorkerSettings(5, 0.5, 9.0, 0.25)


def test_defaults_are_the_documented_policy():
    assert WorkerSettings() == WorkerSettings(max_attempts=3, base_delay_seconds=2.0, lease_seconds=30.0,
                                              poll_interval_seconds=1.0)


def test_database_created_before_the_queue_is_migrated(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with closing(sqlite3.connect(path)) as connection:          # esquema anterior: sin dedup_key ni cola
        connection.executescript("""
            CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE,
                                    category TEXT NOT NULL);
            CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL, status TEXT NOT NULL
                DEFAULT 'pending', customer_category TEXT NOT NULL, address TEXT NOT NULL, city TEXT NOT NULL,
                postal_code TEXT NOT NULL, shipping TEXT NOT NULL, subtotal_cents INTEGER NOT NULL,
                discount_cents INTEGER NOT NULL, shipping_cents INTEGER NOT NULL, total_cents INTEGER NOT NULL);
            CREATE TABLE notifications (id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL, recipient TEXT NOT NULL,
                                        message TEXT NOT NULL);
            INSERT INTO notifications(order_id, recipient, message) VALUES (1, 'a@x.com', 'vieja');
        """)
    database = Database(path)
    database.initialize()
    database.initialize()                                       # idempotente
    with database.connect() as connection:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(notifications)")}
        assert "dedup_key" in columns
        assert connection.execute("SELECT message FROM notifications").fetchone()[0] == "vieja"
        tables = {row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"notification_queue", "notification_dlq"} <= tables
