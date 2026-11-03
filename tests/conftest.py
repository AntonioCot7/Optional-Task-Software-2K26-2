"""Fixtures y utilidades compartidas. Cada prueba usa su propia base SQLite temporal."""
import pytest
from fastapi.testclient import TestClient

from app.database import Database
from main import create_app


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
