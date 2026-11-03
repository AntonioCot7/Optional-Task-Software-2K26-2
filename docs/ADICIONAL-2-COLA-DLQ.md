# Adicional 2: cola de notificaciones, worker y DLQ

Rama: `adicional-2-cola-dlq` (parte de `post-refactor`). Código: [`app/notification_queue.py`](../app/notification_queue.py)
(cola, DLQ y política), [`app/notification_worker.py`](../app/notification_worker.py) (worker y canal simulado),
[`app/notifications.py`](../app/notifications.py) (rutas). Pruebas: [`tests/test_notification_queue.py`](../tests/test_notification_queue.py)
(33 pruebas). Demostración real: [`docs/evidencias/10-demo-cola-dlq.txt`](evidencias/10-demo-cola-dlq.txt).

## Arquitectura

```
POST /orders/{id}/confirm ──(misma transacción)──► stock − qty · estado = confirmed · INSERT notification_queue (pending)
                                                                   │
                                          worker (un hilo) ◄───────┘  claim → send → ┬─ éxito  → INSERT notifications (efecto local) + DELETE de la cola
                                                                                      ├─ temporal → pending + espera exponencial (hasta max_attempts)
                                                                                      └─ permanente / intentos agotados → notification_dlq
```

- **Outbox transaccional.** `confirm` encola el mensaje dentro de la misma transacción que descuenta stock y cambia el estado:
  si falta stock, no se encola nada (hay prueba).
- **Un único worker** (alcance documentado). Corre como hilo daemon dentro de la API (`LAB_NOTIFICATION_WORKER=0` lo desactiva) o
  por separado con `uv run python -m app.notification_worker` (en ese caso desactive el hilo de la API para no tener dos).
  Cada `claim` es una transacción `BEGIN IMMEDIATE`, pero la operación con varios consumidores no se ha probado ni se promete.
- **Cambio de comportamiento deliberado:** `confirm` ya no registra la notificación; `GET /orders/{id}/notifications` la muestra
  cuando el worker la procesa. Por eso 5 pruebas existentes se adaptaron para ejecutar el worker antes de comprobar el efecto
  (`tests/test_orders.py`, `test_orders_api.py`, `test_order_rules_unit.py`, `test_database.py`); el resto no cambió.

## Tablas

| Tabla | Contenido |
| --- | --- |
| `notification_queue` | Pendientes (`pending`) y en procesamiento (`processing`, con `locked_until`). Campos: contenido, `dedup_key` única, `attempts` (ciclo actual), `total_attempts` (acumulado), `next_attempt_at`, `last_error`. |
| `notification_dlq` | Mensajes muertos: **contenido, intentos (`attempts`, `total_attempts`) y causa del último error (`last_error`)**, `failed_at` y `reprocessed_at`. |
| `notifications` | El «efecto»: el registro local de la notificación (ya existía). Gana `dedup_key` con índice único. Una migración liviana al arrancar adapta bases anteriores. |

## Política de reintentos (documentada)

| Parámetro | Valor por defecto | Variable de entorno |
| --- | --- | --- |
| Máximo de entregas por ciclo (`max_attempts`) | **3** (la primera incluida) | `LAB_NOTIFICATION_MAX_ATTEMPTS` |
| Espera tras el intento *n* | `base · 2^(n−1)`: **2 s, luego 4 s** | `LAB_NOTIFICATION_BASE_DELAY` (2.0) |
| *Lease* de procesamiento | 30 s | `LAB_NOTIFICATION_LEASE` |
| Sondeo del worker | 1 s | `LAB_NOTIFICATION_POLL` |

Un fallo **permanente** no se reintenta: va directo a la DLQ. Al agotar el intento 3 de un fallo temporal, el mensaje pasa a la DLQ.

## Simulación reproducible de fallos

`SimulatedSender` no envía nada: decide por la parte local del correo del destinatario y por el número de entrega acumulado.

| Destinatario | Resultado |
| --- | --- |
| `temp-fail-N@...` | Las **N primeras** entregas fallan de forma temporal; la siguiente tiene éxito. |
| `perm-fail@...` | Falla siempre, de forma permanente. |
| cualquier otro | Éxito inmediato. |

Ejemplos: `temp-fail-1` → falla, reintenta a los 2 s y entrega. `temp-fail-9` → 3 fallos y DLQ. Como el contador de la simulación es el
acumulado (`total_attempts`), tras reprocesar un `temp-fail-4` desde la DLQ las entregas 4 (falla) y 5 (éxito) completan el ciclo.

## DLQ: consulta y reprocesamiento explícito

| Ruta | Función |
| --- | --- |
| `GET /notifications/queue` | Mensajes pendientes o en procesamiento (con intentos y último error). |
| `GET /notifications/dlq` | Mensajes muertos aún sin reprocesar. |
| `POST /notifications/dlq/{id}/reprocess` | Devuelve el mensaje a la cola con el ciclo de intentos en 0 (`total_attempts` se conserva). `404` si no existe, `409` si ya se reprocesó o ya hay uno equivalente en la cola. La entrada de la DLQ queda marcada con `reprocessed_at`. |

## Recuperación y reinicios

- **Reinicio:** pendientes, calendario de reintentos y DLQ viven en SQLite; sobreviven (probado con reinicios de la aplicación).
- **Worker interrumpido:** al reclamar, el mensaje queda `processing` con `locked_until = ahora + lease` y **cuenta un intento**. Si el
  worker muere, al vencer el lease cualquier `claim` lo recupera. Si ya había agotado sus intentos, se envía a la DLQ con la causa
  «Procesamiento interrumpido…» en vez de reintentarse indefinidamente (un mensaje que mata al worker no puede ciclar para siempre).

## Duplicados: sin prometer «exactamente una vez»

La entrega es **al menos una vez**. Un mensaje puede procesarse dos veces (p. ej. el worker cae después de entregar y antes de confirmar,
o se reencola a mano). El efecto no se duplica por tres capas:

1. `enqueue` usa `dedup_key = "order-confirmed:<id>"` con `UNIQUE` y `ON CONFLICT DO NOTHING`: confirmar no puede encolar dos veces lo mismo.
2. El efecto local (`INSERT INTO notifications … ON CONFLICT(dedup_key) DO NOTHING`) y el borrado del mensaje de la cola ocurren en
   **una sola transacción**: nunca queda «efecto sin retirar» ni «retirado sin efecto».
3. Con un proveedor real (correo/SMS) la entrega externa no es transaccional con la base: se le pasaría la misma `dedup_key` como clave
   de idempotencia y, si no la soporta, un duplicado de la llamada externa es el costo aceptado de «al menos una vez».

## Pruebas por criterio de aceptación

| Criterio | Pruebas (`tests/test_notification_queue.py`) |
| --- | --- |
| Confirmar deja pendiente; el worker procesa aparte | `test_confirm_leaves_a_pending_message_and_the_worker_processes_it_separately`, `test_failed_confirmation_enqueues_nothing` |
| Fallo temporal o permanente reproducible | `test_simulated_sender_is_deterministic` (7 casos), `test_permanent_failure_goes_straight_to_dlq_without_retries` |
| Reintentos con máximo y espera | `test_temporary_failures_are_retried_with_exponential_wait_until_success`, `test_max_attempts_is_configurable`, `test_retry_delay_doubles_each_attempt` |
| Agotados los intentos → DLQ con contenido, intentos y causa | `test_exhausting_attempts_moves_message_to_dlq_keeping_content_attempts_and_last_error` |
| Consultar y reprocesar la DLQ | `test_dlq_reprocess_requeues_with_fresh_attempt_cycle_and_can_then_succeed`, `…fails_again…`, `…unknown…404`, `…twice…409`, `…already_queued…` |
| Pendientes sobreviven al reinicio | `test_pending_messages_survive_restart_and_are_processed_afterwards`, `test_retry_schedule_and_dlq_survive_restart` |
| Mensaje en procesamiento recuperable | `test_message_in_processing_is_recovered_after_lease_expires`, `test_interrupted_message_that_exhausted_attempts_goes_to_dlq_instead_of_looping` |
| Duplicado no duplica el efecto | `test_enqueueing_the_same_confirmation_twice_keeps_one_message`, `test_processing_a_duplicate_message_does_not_duplicate_the_notification_effect`, `test_complete_reports_whether_the_effect_was_created` |
| Worker real en la aplicación | `test_background_worker_thread_processes_confirmations_automatically`, `test_run_forever_survives_errors_and_stops_on_event` |

Mutaciones verificadas (aplicadas y revertidas): sin recuperación de lease → fallan 2 pruebas; efecto sin `ON CONFLICT` → fallan 2.

## Limitaciones

- Un solo worker; no se ha probado concurrencia entre varios consumidores.
- La entrega es simulada; no hay orden garantizado entre mensajes tras reintentos (un mensaje con espera puede ser adelantado).
- No hay purga de la DLQ ni de entradas reprocesadas, ni métricas o alertas.
- La API expone la cola y la DLQ sin autenticación (como el resto del laboratorio).
- El contrato OpenAPI cambió solo de forma aditiva (3 rutas nuevas; el snapshot añadió 81 líneas y no eliminó ninguna).
