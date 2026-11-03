"""Fixtures y utilidades compartidas. Cada prueba usa su propia base SQLite temporal."""
import pytest
from fastapi.testclient import TestClient

from app.database import Database
from main import create_app


@pytest.fixture(autouse=True)
def no_background_worker(monkeypatch):
    """Las pruebas controlan el worker a mano (drain) para que sean deterministas."""
    monkeypatch.setenv("LAB_NOTIFICATION_WORKER", "0")


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test.sqlite3"


@pytest.fixture
def client(db_path):
    with TestClient(create_app(db_path)) as session:
        yield session


@pytest.fixture
def database(db_path):
    """Base inicializada, sin pasar por HTTP (para pruebas unitarias y de persistencia)."""
    db = Database(db_path)
    db.initialize()
    return db
