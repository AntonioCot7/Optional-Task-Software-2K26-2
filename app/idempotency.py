"""Idempotencia de POST /orders mediante la cabecera ``Idempotency-Key``.

Contrato (ver docs/ADICIONAL-1-IDEMPOTENCIA.md):
- La clave es opcional. Sin clave, POST /orders se comporta como siempre.
- Misma clave y mismo contenido -> se devuelve la respuesta original (201 y el mismo cuerpo) y no se crea otro pedido.
- Misma clave y contenido distinto -> 409.
- La clave se guarda en la misma transacción que el pedido: si algo falla no queda ni pedido ni clave.
"""
import hashlib
import json
import sqlite3

KEY_PATTERN = r"^[\x21-\x7E]+$"  # ASCII visible, sin espacios
KEY_MAX_LENGTH = 255
CONFLICT_MESSAGE = "La clave de idempotencia ya se usó con un contenido distinto"


def fingerprint(data) -> str:
    """Huella del contenido semántico del pedido (las líneas se ordenan: su orden no importa)."""
    content = data.model_dump(mode="json")
    content["items"] = sorted(content["items"], key=lambda item: item["product_id"])
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class IdempotencyRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def find(self, key: str):
        return self.connection.execute("SELECT * FROM idempotency_keys WHERE key = ?", (key,)).fetchone()

    def save(self, key: str, request_hash: str, order_id: int, response: dict) -> None:
        self.connection.execute(
            "INSERT INTO idempotency_keys(key, request_hash, order_id, response_json) VALUES (?, ?, ?, ?)",
            (key, request_hash, order_id, json.dumps(response, ensure_ascii=False)),
        )
