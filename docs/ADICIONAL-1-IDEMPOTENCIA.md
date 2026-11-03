# Adicional 1: idempotencia en `POST /orders`

Rama: `adicional-1-idempotencia` (parte de `post-refactor`). Código: [`app/idempotency.py`](../app/idempotency.py),
`OrderService.create` en [`app/orders.py`](../app/orders.py), tabla `idempotency_keys` en [`app/database.py`](../app/database.py).
Pruebas: [`tests/test_idempotency.py`](../tests/test_idempotency.py) (26 pruebas).

## Contrato

| Elemento | Definición |
| --- | --- |
| Cabecera | `Idempotency-Key: <clave>`, **opcional**. 1 a 255 caracteres ASCII visibles, sin espacios. Si es inválida: `422`. |
| Sin cabecera | Comportamiento anterior: cada petición crea un pedido. Es compatible hacia atrás. |
| Misma clave y mismo contenido | `201` con el **mismo cuerpo que la respuesta original** y la cabecera `Idempotent-Replayed: true`. No se crea otro pedido. |
| Misma clave y contenido distinto | `409` con `{"detail": "La clave de idempotencia ya se usó con un contenido distinto"}`. No se crea nada. |
| Peticiones concurrentes con la misma clave | Una crea el pedido; las demás reciben la respuesta original (o `409` si su contenido difiere). Solo existe un pedido. |
| Errores (404, 422, 500) | **No se guardan.** La clave queda libre y un reintento ejecuta la operación completa. |

### Qué es «mismo contenido»
Huella SHA-256 del cuerpo ya validado (`customer_id`, `address`, `city`, `postal_code`, `shipping`, `items`), serializado de
forma canónica. Las cadenas se comparan tras `strip`, y **el orden de las líneas no importa** (los productos son únicos por
pedido). El valor por defecto `shipping = standard` equivale a enviarlo explícitamente.

### Alcance y límites (documentados a propósito)
- Solo `POST /orders`. `quote`, `PUT`, `confirm`, etc. no usan clave (confirmar ya está protegido por la transición de estado).
- La clave es **global**: el laboratorio no tiene autenticación, así que no hay un «dueño» con el que acotarla. En un sistema con
  usuarios habría que combinarla con el identificador del cliente/tenant.
- La respuesta repetida es una **instantánea** de la original: no refleja cambios posteriores del pedido ni del catálogo. Si el
  pedido se eliminó después, repetir la clave sigue devolviendo la respuesta original y **no** lo recrea.
- No hay caducidad de claves (no exigida): la tabla crece de forma indefinida.
- El contrato OpenAPI cambia **solo de forma aditiva** (un parámetro de cabecera opcional); el snapshot de contrato se regeneró
  con `scripts/export_openapi.py` y el diff son 23 líneas añadidas, ninguna eliminada.

## Diseño: persistencia, unicidad y transacciones

```sql
CREATE TABLE idempotency_keys (
    key TEXT PRIMARY KEY,           -- unicidad garantizada por la base
    request_hash TEXT NOT NULL,     -- huella del contenido
    order_id INTEGER NOT NULL,      -- sin FK: el pedido puede eliminarse y la respuesta original se conserva
    response_json TEXT NOT NULL,    -- respuesta original (instantánea)
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

`Database.connect()` abre cada transacción con `BEGIN IMMEDIATE`, que toma el bloqueo de escritura de SQLite **al empezar**.
`OrderService.create` hace dentro de esa única transacción: (1) buscar la clave, (2) si existe, comparar huella y responder,
(3) si no, crear el pedido y sus líneas, (4) insertar la clave. Consecuencias:

- **Concurrencia**: dos peticiones con la misma clave no pueden intercalar «buscar» y «crear»; la segunda espera (timeout de 10 s)
  y, al entrar, ya ve la clave confirmada. La `PRIMARY KEY` es una segunda defensa.
- **Atomicidad**: pedido, líneas y clave se confirman juntos o ninguno. Un fallo en cualquier paso hace `ROLLBACK`: no hay pedido
  parcial ni clave huérfana que bloquee el reintento.
- **Reinicio**: todo vive en SQLite; el comportamiento se conserva (probado reiniciando la aplicación).
- **Migración**: `CREATE TABLE IF NOT EXISTS` crea la tabla en bases existentes al arrancar.

## Demostración con pruebas

| Criterio de aceptación | Pruebas |
| --- | --- |
| Misma clave y contenido → resultado original, sin otro pedido | `test_same_key_and_content_returns_original_result_without_creating_another_order`, `…snapshot_even_if_catalog_changed…`, `…deleted_afterwards`, `test_item_order_does_not_change_the_content_fingerprint` |
| Misma clave y contenido distinto → conflicto | `test_same_key_with_different_content_returns_409_and_creates_nothing` (5 variantes), `…different_customer…` |
| Concurrencia: un solo pedido | `test_concurrent_requests_with_same_key_create_a_single_order` (8 hilos vía HTTP), `…at_service_level_use_independent_connections` (10 hilos con conexiones independientes), `…different_content_and_same_key…` |
| Persiste tras reiniciar | `test_behavior_survives_application_restart`, `test_existing_database_without_the_table_is_migrated_on_startup` |
| Un error no deja clave bloqueada ni pedido parcial | `test_failed_request_does_not_block_the_key`, `test_validation_error_does_not_store_the_key`, `test_failure_after_inserting_order_rolls_back_order_and_key`, `test_failure_while_writing_lines_leaves_no_partial_order` |

Verificación de que las pruebas detectan regresiones (mutaciones aplicadas y revertidas): desactivar la búsqueda de la clave
hizo fallar 15 de 26; cambiar `BEGIN IMMEDIATE` por `BEGIN` hizo fallar las 3 pruebas de concurrencia.

## Ejemplo

```bash
curl -X POST http://127.0.0.1:8000/orders -H "Content-Type: application/json" -H "Idempotency-Key: compra-0001" \
     -d '{"customer_id":1,"address":"Av. Primavera 100","city":"Lima","postal_code":"15001","shipping":"standard","items":[{"product_id":1,"quantity":2}]}'
# Repetir el mismo comando: 201, mismo cuerpo, cabecera Idempotent-Replayed: true. Cambiar quantity: 409.
```
