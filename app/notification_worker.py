"""Worker de notificaciones: un único consumidor de la cola, con reintentos, DLQ y recuperación.

Se ejecuta como hilo dentro de la API (``create_app``) o por separado: ``uv run python -m app.notification_worker``.
Garantía: entrega *al menos una vez*, no «exactamente una vez». Un duplicado no repite el efecto local gracias a
``UNIQUE(dedup_key)`` (ver ``NotificationQueue.complete``) y, hacia un proveedor real, se le pasaría ``dedup_key`` como
clave de idempotencia.
"""
import logging
import os
import re
import threading
import time
from collections.abc import Callable

from app.database import Database
from app.notification_queue import NotificationQueue, WorkerSettings

logger = logging.getLogger("notification_worker")


class DeliveryError(Exception):
    """Fallo al entregar una notificación."""


class TemporaryDeliveryError(DeliveryError):
    """Fallo transitorio: se reintenta."""


class PermanentDeliveryError(DeliveryError):
    """Fallo definitivo: no tiene sentido reintentar; el mensaje va directo a la DLQ."""


class SimulatedSender:
    """Canal de entrega simulado y reproducible. No envía nada: decide el resultado por el destinatario.

    Reglas sobre la parte local del correo (antes de la @) y el número de entrega ``attempt`` (1-based, acumulado
    entre reprocesamientos):
      - ``temp-fail-N``: las N primeras entregas fallan temporalmente; la siguiente tiene éxito.
      - ``perm-fail``: falla siempre y de forma permanente.
      - cualquier otro: éxito inmediato.
    """
    TEMP_PATTERN = re.compile(r"^temp-fail-(\d+)$")

    def send(self, recipient: str, message: str, *, attempt: int) -> None:
        local = recipient.split("@", 1)[0]
        if local == "perm-fail":
            raise PermanentDeliveryError("Buzón inexistente (simulado)")
        match = self.TEMP_PATTERN.match(local)
        if match and attempt <= int(match.group(1)):
            raise TemporaryDeliveryError(f"Servicio de correo no disponible (simulado), entrega {attempt}")


class NotificationWorker:
    def __init__(self, database: Database, sender=None, settings: WorkerSettings | None = None,
                 clock: Callable[[], float] = time.time):
        self.database = database
        self.sender = sender or SimulatedSender()
        self.settings = settings or WorkerSettings()
        self.clock = clock

    def run_once(self) -> str | None:
        """Procesa como máximo un mensaje. Devuelve 'sent', 'retry', 'dead' o None si no había nada elegible."""
        with self.database.connect() as connection:
            message = NotificationQueue(connection).claim_next(self.clock(), self.settings)
        if message is None:
            return None
        try:
            self.sender.send(message["recipient"], message["message"], attempt=message["total_attempts"])
        except PermanentDeliveryError as error:
            return self._fail(message, str(error), permanent=True)
        except Exception as error:   # fallo temporal o imprevisto: se trata como transitorio
            return self._fail(message, str(error) or error.__class__.__name__, permanent=False)
        with self.database.connect() as connection:
            NotificationQueue(connection).complete(
                message["id"], message["order_id"], message["recipient"], message["message"], message["dedup_key"],
            )
        return "sent"

    def drain(self, limit: int = 1000) -> list[str]:
        """Procesa mensajes elegibles hasta vaciar lo disponible (los reintentos con espera quedan pendientes)."""
        outcomes = []
        while len(outcomes) < limit and (outcome := self.run_once()) is not None:
            outcomes.append(outcome)
        return outcomes

    def _fail(self, message, error: str, permanent: bool) -> str:
        with self.database.connect() as connection:
            queue = NotificationQueue(connection)
            if permanent or message["attempts"] >= self.settings.max_attempts:
                queue.dead_letter(message["id"], error)
                logger.warning("Notificación %s a la DLQ tras %s intento(s): %s", message["id"], message["attempts"], error)
                return "dead"
            delay = self.settings.retry_delay(message["attempts"])
            queue.schedule_retry(message["id"], error, self.clock() + delay)
            return "retry"

    def run_forever(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                if self.run_once() is None:
                    stop.wait(self.settings.poll_interval_seconds)
            except Exception:   # la base puede estar ocupada o no disponible: el worker sigue vivo
                logger.exception("Error en el worker de notificaciones; se reintenta")
                stop.wait(self.settings.poll_interval_seconds)


def settings_from_environment() -> WorkerSettings:
    defaults = WorkerSettings()
    return WorkerSettings(
        max_attempts=int(os.getenv("LAB_NOTIFICATION_MAX_ATTEMPTS", defaults.max_attempts)),
        base_delay_seconds=float(os.getenv("LAB_NOTIFICATION_BASE_DELAY", defaults.base_delay_seconds)),
        lease_seconds=float(os.getenv("LAB_NOTIFICATION_LEASE", defaults.lease_seconds)),
        poll_interval_seconds=float(os.getenv("LAB_NOTIFICATION_POLL", defaults.poll_interval_seconds)),
    )


if __name__ == "__main__":   # worker independiente: desactive el hilo de la API con LAB_NOTIFICATION_WORKER=0
    logging.basicConfig(level=logging.INFO)
    store = Database(os.getenv("LAB_DB_PATH", "data/store.sqlite3"))
    store.initialize()
    stop_event = threading.Event()
    try:
        NotificationWorker(store, settings=settings_from_environment()).run_forever(stop_event)
    except KeyboardInterrupt:
        stop_event.set()
