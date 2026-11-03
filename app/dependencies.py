"""Piezas compartidas por las rutas: acceso a la base y traducción de errores de integridad."""
import sqlite3
from contextlib import contextmanager
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from app.database import Database


def get_database(request: Request) -> Database:
    return request.app.state.database


DatabaseDep = Annotated[Database, Depends(get_database)]


@contextmanager
def conflict_on_integrity_error(detail: str):
    """Traduce una violación de integridad de SQLite en un 409 con el mensaje indicado."""
    try:
        yield
    except sqlite3.IntegrityError:
        raise HTTPException(409, detail) from None
