"""Cola de notificaciones sobre SQLite y su cola de mensajes muertos (DLQ).

Estados de un mensaje en ``notification_queue``: ``pending`` (esperando, con ``next_attempt_at``) y ``processing``
(reclamado por el worker con un *lease* hasta ``locked_until``). Un mensaje terminado con éxito se elimina de la cola y
deja su efecto en ``notifications``; uno que agota intentos o falla de forma permanente se mueve a ``notification_dlq``.
"""
import sqlite3
from dataclasses import dataclass

INTERRUPTED_ERROR = "Procesamiento interrumpido: el worker no confirmó el mensaje antes de vencer el lease"


@dataclass(frozen=True)
class WorkerSettings:
    """Política de reintentos. ``max_attempts`` cuenta entregas por ciclo (la primera incluida)."""
    max_attempts: int = 3
    base_delay_seconds: float = 2.0
    lease_seconds: float = 30.0
    poll_interval_seconds: float = 1.0

    def retry_delay(self, attempt: int) -> float:
        """Espera tras el intento ``attempt`` (1-based): base, 2*base, 4*base, ..."""
        return self.base_delay_seconds * 2 ** (attempt - 1)


class NotificationQueue:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    # ---- productor
    def enqueue(self, order_id, recipient, message, dedup_key, now: float) -> bool:
        """Encola un mensaje. Devuelve False si ya existía uno con la misma ``dedup_key`` (duplicado ignorado)."""
        cursor = self.connection.execute(
            """INSERT INTO notification_queue(order_id, recipient, message, dedup_key, next_attempt_at)
               VALUES (?, ?, ?, ?, ?) ON CONFLICT(dedup_key) DO NOTHING""",
            (order_id, recipient, message, dedup_key, now),
        )
        return cursor.rowcount > 0

    # ---- consumidor
    def claim_next(self, now: float, settings: WorkerSettings):
        """Reclama el siguiente mensaje elegible y le suma un intento; None si no hay.

        Elegible: pendiente con ``next_attempt_at`` vencido, o en procesamiento con el lease vencido (un worker
        interrumpido). Un mensaje recuperado que ya agotó sus intentos va a la DLQ en lugar de reintentarse.
        """
        while True:
            row = self.connection.execute(
                """SELECT * FROM notification_queue
                   WHERE (status = 'pending' AND next_attempt_at <= ?)
                      OR (status = 'processing' AND locked_until <= ?)
                   ORDER BY id LIMIT 1""",
                (now, now),
            ).fetchone()
            if row is None:
                return None
            if row["status"] == "processing" and row["attempts"] >= settings.max_attempts:
                self.dead_letter(row["id"], INTERRUPTED_ERROR)
                continue
            self.connection.execute(
                """UPDATE notification_queue SET status = 'processing', attempts = attempts + 1,
                   total_attempts = total_attempts + 1, locked_until = ? WHERE id = ?""",
                (now + settings.lease_seconds, row["id"]),
            )
            return self.connection.execute("SELECT * FROM notification_queue WHERE id = ?", (row["id"],)).fetchone()

    def complete(self, queue_id, order_id, recipient, message, dedup_key) -> bool:
        """Registra el efecto local y retira el mensaje, en una sola transacción.

        El efecto es idempotente: ``UNIQUE(dedup_key)`` con ``DO NOTHING`` impide duplicarlo si el mensaje se
        procesa más de una vez. Devuelve True si el efecto se creó ahora, False si ya existía.
        """
        cursor = self.connection.execute(
            """INSERT INTO notifications(order_id, recipient, message, dedup_key) VALUES (?, ?, ?, ?)
               ON CONFLICT(dedup_key) DO NOTHING""",
            (order_id, recipient, message, dedup_key),
        )
        self.connection.execute("DELETE FROM notification_queue WHERE id = ?", (queue_id,))
        return cursor.rowcount > 0

    def schedule_retry(self, queue_id, error: str, next_attempt_at: float) -> None:
        self.connection.execute(
            """UPDATE notification_queue SET status = 'pending', next_attempt_at = ?, locked_until = NULL,
               last_error = ? WHERE id = ?""",
            (next_attempt_at, error, queue_id),
        )

    def dead_letter(self, queue_id, error: str) -> None:
        """Mueve el mensaje a la DLQ conservando contenido, intentos y la causa del último error."""
        self.connection.execute(
            """INSERT INTO notification_dlq(queue_id, order_id, recipient, message, dedup_key, attempts,
                                            total_attempts, last_error)
               SELECT id, order_id, recipient, message, dedup_key, attempts, total_attempts, ?
               FROM notification_queue WHERE id = ?""",
            (error, queue_id),
        )
        self.connection.execute("DELETE FROM notification_queue WHERE id = ?", (queue_id,))

    # ---- consulta y reprocesamiento
    def list_queue(self) -> list[dict]:
        return [dict(row) for row in self.connection.execute(
            """SELECT id, order_id, recipient, message, status, attempts, total_attempts, next_attempt_at,
                      last_error FROM notification_queue ORDER BY id""")]

    def list_dlq(self) -> list[dict]:
        return [dict(row) for row in self.connection.execute(
            """SELECT id, queue_id, order_id, recipient, message, dedup_key, attempts, total_attempts, last_error,
                      failed_at FROM notification_dlq WHERE reprocessed_at IS NULL ORDER BY id""")]

    def find_dlq(self, dlq_id):
        return self.connection.execute("SELECT * FROM notification_dlq WHERE id = ?", (dlq_id,)).fetchone()

    def requeue_from_dlq(self, dlq_id, now: float):
        """Devuelve un mensaje de la DLQ a la cola con el ciclo de intentos en cero. None si no cabe (duplicado)."""
        entry = self.find_dlq(dlq_id)
        cursor = self.connection.execute(
            """INSERT INTO notification_queue(order_id, recipient, message, dedup_key, next_attempt_at,
                                              total_attempts, last_error)
               VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(dedup_key) DO NOTHING""",
            (entry["order_id"], entry["recipient"], entry["message"], entry["dedup_key"], now,
             entry["total_attempts"], entry["last_error"]),
        )
        if cursor.rowcount == 0:
            return None
        self.connection.execute(
            "UPDATE notification_dlq SET reprocessed_at = CURRENT_TIMESTAMP WHERE id = ?", (dlq_id,),
        )
        return cursor.lastrowid
