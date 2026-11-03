"""Demostración de la cola de notificaciones contra un servidor en ejecución (Adicional 2).

Crea tres clientes cuyo destinatario decide el resultado de la entrega simulada:
  ok@...            -> entrega inmediata
  temp-fail-1@...   -> falla una vez (temporal), se reintenta y entrega
  perm-fail@...     -> falla de forma permanente: va directo a la DLQ
Uso: uv run python scripts/demo_cola.py [URL_BASE]
"""
import json
import sys
import time

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"


def show(title, data):
    print(f"\n== {title}\n{json.dumps(data, indent=2, ensure_ascii=False)}")


with httpx.Client(base_url=BASE) as http:
    product = http.post("/products", json={"name": "Libro", "price_cents": 2500, "stock": 50}).json()
    orders = {}
    for name in ("ok", "temp-fail-1", "perm-fail"):
        customer = http.post("/customers", json={"name": name, "email": f"{name}@example.com"}).json()
        order = http.post("/orders", json={"customer_id": customer["id"], "address": "Av. 1", "city": "Lima",
                                           "postal_code": "15001", "items": [{"product_id": product["id"], "quantity": 1}]}).json()
        http.post(f"/orders/{order['id']}/confirm").raise_for_status()
        orders[name] = order["id"]
    show("1. Justo tras confirmar: cola con 3 pendientes y ninguna notificación registrada", {
        "queue": [(m["recipient"], m["status"], m["attempts"]) for m in http.get("/notifications/queue").json()],
        "notificaciones_del_pedido_ok": http.get(f"/orders/{orders['ok']}/notifications").json()})
    time.sleep(3)   # el worker (hilo) procesa por separado; temp-fail-1 espera su reintento
    show("2. Tras unos segundos", {
        "queue": http.get("/notifications/queue").json(),
        "dlq": http.get("/notifications/dlq").json(),
        "notificaciones": {n: [m["recipient"] for m in http.get(f"/orders/{i}/notifications").json()]
                           for n, i in orders.items()}})
    dead = http.get("/notifications/dlq").json()[0]
    show("3. Reprocesamiento explícito de la DLQ", http.post(f"/notifications/dlq/{dead['id']}/reprocess").json())
    time.sleep(2)
    show("4. perm-fail vuelve a fallar y regresa a la DLQ (nueva entrada)", http.get("/notifications/dlq").json())
