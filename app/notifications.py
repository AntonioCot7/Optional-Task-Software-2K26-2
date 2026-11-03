"""Rutas para inspeccionar la cola y la DLQ de notificaciones y reprocesar explícitamente un mensaje muerto."""
import time

from fastapi import APIRouter, HTTPException

from app.dependencies import DatabaseDep
from app.notification_queue import NotificationQueue

router = APIRouter(prefix="/notifications", tags=["Notifications"])


@router.get("/queue")
def list_queue(database: DatabaseDep):
    """Mensajes pendientes o en procesamiento."""
    with database.connect() as connection:
        return NotificationQueue(connection).list_queue()


@router.get("/dlq")
def list_dead_letters(database: DatabaseDep):
    """Mensajes que agotaron sus intentos o fallaron de forma permanente (aún sin reprocesar)."""
    with database.connect() as connection:
        return NotificationQueue(connection).list_dlq()


@router.post("/dlq/{dlq_id}/reprocess")
def reprocess_dead_letter(dlq_id: int, database: DatabaseDep):
    """Devuelve el mensaje a la cola con su ciclo de intentos en cero."""
    with database.connect() as connection:
        queue = NotificationQueue(connection)
        entry = queue.find_dlq(dlq_id)
        if entry is None:
            raise HTTPException(404, "Mensaje no encontrado en la DLQ")
        if entry["reprocessed_at"] is not None:
            raise HTTPException(409, "El mensaje ya fue reprocesado")
        queue_id = queue.requeue_from_dlq(dlq_id, time.time())
        if queue_id is None:
            raise HTTPException(409, "Ya existe un mensaje equivalente en la cola")
        return {"dlq_id": dlq_id, "queue_id": queue_id, "status": "requeued"}
