import os
import sqlite3
import threading
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app import customers, notifications, orders, products
from app.database import Database
from app.notification_worker import NotificationWorker, settings_from_environment


def create_app(database_path=None, run_worker=None, worker_settings=None):
    """run_worker=None lee LAB_NOTIFICATION_WORKER (por defecto activo; "0" lo desactiva)."""
    database = Database(database_path if database_path is not None else os.getenv("LAB_DB_PATH", "data/store.sqlite3"))

    worker_enabled = run_worker if run_worker is not None else os.getenv("LAB_NOTIFICATION_WORKER", "1") != "0"

    @asynccontextmanager
    async def lifespan(application):
        database.initialize()
        stop = threading.Event()
        thread = None
        if worker_enabled:   # un único worker, en un hilo del propio proceso
            worker = NotificationWorker(database, settings=worker_settings or settings_from_environment())
            thread = threading.Thread(target=worker.run_forever, args=(stop,), name="notification-worker", daemon=True)
            thread.start()
        try:
            yield
        finally:
            stop.set()
            if thread is not None:
                thread.join(timeout=5)

    application = FastAPI(title="Tienda Lab", version="1.0.0", lifespan=lifespan)
    application.state.database = database
    application.include_router(products.router)
    application.include_router(customers.router)
    application.include_router(orders.router)
    application.include_router(notifications.router)

    @application.exception_handler(sqlite3.OperationalError)
    async def database_error(request: Request, error: sqlite3.OperationalError):
        return JSONResponse(status_code=503, content={"detail": "Base de datos no disponible; reintente más tarde"})

    return application


app = create_app()


if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
