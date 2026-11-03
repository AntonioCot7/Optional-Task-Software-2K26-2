# Informe: pruebas, análisis estático y refactorización de FEKA STORE

Autor: Antonio (`AntonioCot7`). Todo el trabajo se hizo sobre este repositorio; las revisiones, evidencias y comandos están abajo.

## 0. Resumen

| | Antes de refactorizar | Después |
| --- | --- | --- |
| Pruebas | 160 (4 originales + 156 nuevas) | 184 (las 160 **sin modificar** + 24 nuevas) |
| Cobertura de líneas del código de aplicación (pytest) | 99,67 % (302/303) | 99,75 % (402/403) |
| SonarQube, cobertura global | 99,7 % | 99,8 % |
| SonarQube, hallazgos (perfil *Sonar way*) | 0 | 0 |
| SonarQube, hallazgos (perfil estricto *Sonar way comprehensive*, análisis auxiliar) | 39 | 13 |
| Complejidad cognitiva total (SonarQube) | 55 | 33 |
| Función más compleja | `OrderService.save`: 19 (límite 15, regla S3776) | ninguna sobre el límite |
| Contrato HTTP | — | OpenAPI **idéntico** al inicial (comprobado con un snapshot) |

La suite parte de **67 %** (4 pruebas del enunciado) y se llevó a >99 % *antes* de tocar el código de aplicación. La refactorización
(6 técnicas) conserva rutas, contratos, reglas de negocio, persistencia e integridad transaccional. Los tres adicionales están
resueltos, cada uno en su rama (sección 9).

### Mapa del enunciado → entrega

| Requisito | Dónde |
| --- | --- |
| 1. Ejecutar la aplicación | §3, `docs/evidencias/01-…`, `09-…`, `docs/capturas/01-swagger-inicial.png`, `09-swagger-final.png`, `scripts/recorrido.py` |
| 2. Pruebas ≥ 80 % antes y después | §4, `tests/`, `docs/evidencias/02-`, `03-`, `08-` |
| 3. SonarQube inicial y final | §5, `docs/evidencias/04-` a `07-`, `docs/capturas/04-` a `07-` |
| 4. Code smells (≥ 4) | §6 (seis, más uno no resuelto) |
| 5. Refactorizar (≥ 3 técnicas) | §7 (seis técnicas aplicadas) |
| 6. Diagramas inicial y final | §8, `docs/diagramas/` (fuente Mermaid, SVG y PNG) |
| Adicional 1 / 2 / 3 | §9: `docs/ADICIONAL-1-IDEMPOTENCIA.md` y `docs/ADICIONAL-2-COLA-DLQ.md` (en sus ramas), `docs/ADICIONAL-3-VHS.md` |
| Comandos y limitaciones | §10 y §11 |

## 1. Entorno y versiones

| Herramienta | Versión |
| --- | --- |
| Python (de la aplicación) | **3.13.18** (uv lo instaló; `.python-version` fija `3.13`) |
| uv | **0.13.7** (no estaba instalado; se instaló con `python -m pip install --user uv`) |
| FastAPI / pytest / pytest-cov | según `uv.lock` (pytest 9.1.1, pytest-cov 7.1.0) |
| SonarQube | **Community Build 26.10.0.133078** (imagen `sonarqube:community`) |
| Perfil de calidad (Python) | **Sonar way** (aparece como «Sonar way core» en la API) · análisis auxiliar: *Sonar way comprehensive* |
| Quality Gate | *Sonar way* (por defecto) |
| Scanner | SonarScanner CLI **8.1.0.6389** (imagen Docker `sonarsource/sonar-scanner-cli`) |
| Docker | 29.6.1 · SO: Windows 11 |

> **Nota de reproducibilidad.** Sin `.python-version`, `uv` elige la versión más reciente instalable (3.15) y la instalación falla
> compilando `pyyaml`; por eso se fijó 3.13, que es lo que pide el enunciado (≥ 3.13).

## 2. Revisiones (commits y etiquetas)

| Etiqueta / rama | Referencia | Qué es |
| --- | --- | --- |
| `estado-inicial` | `969cbdb` (commit original, ver `git rev-parse estado-inicial`) | Código original del enunciado (4 pruebas, 67 %). |
| **`pre-refactor`** | `git rev-parse pre-refactor` | **Revisión inicial**: código original **intacto** + 160 pruebas + ajuste de `coverage` (§5.2). Es la base de las métricas «antes». |
| **`post-refactor`** | `git rev-parse post-refactor` | **Revisión final** de la refactorización (código de aplicación). Base de las métricas «después». |
| rama `main` (entrega) | HEAD | `post-refactor` + diagramas, evidencias, informe y documentos (solo cambian archivos de `docs/`, `scripts/` y el README respecto a `post-refactor`). |
| rama `adicional-1-idempotencia` | HEAD de la rama | Adicional 1 (parte del commit «DOCS: Añade evidencias de SonarQube, diagramas de clases y scripts de apoyo» de `main`). |
| rama `adicional-2-cola-dlq` | HEAD de la rama | Adicional 2 (parte del commit «DOCS: Añade evidencias de SonarQube, diagramas de clases y scripts de apoyo» de `main`). |

```bash
git diff pre-refactor post-refactor --stat -- app main.py     # qué cambió en el código
git diff --name-status pre-refactor post-refactor -- tests    # las pruebas: solo archivos nuevos (A), ninguno modificado
```

## 3. Ejecución de la aplicación (punto 1)

Se reprodujo el recorrido del README con `uv run main.py` y el script [`scripts/recorrido.py`](../scripts/recorrido.py) (9 comprobaciones:
crear cliente y producto, cotizar y crear → subtotal 5000 / descuento 500 / envío 1000 / total 5500, confirmar, stock 3, una
notificación, repetir confirmación → 409 sin descontar más stock).

- Evidencia inicial: [`01-ejecucion-inicial.txt`](evidencias/01-ejecucion-inicial.txt) — **TODO CORRECTO**, con versiones de Python y uv. Captura: [`01-swagger-inicial.png`](capturas/01-swagger-inicial.png).
- Evidencia final: [`09-ejecucion-final.txt`](evidencias/09-ejecucion-final.txt) — mismo resultado tras refactorizar.
- Captura del estado final: [`09-swagger-final.png`](capturas/09-swagger-final.png) (tomada manualmente con `uv run main.py` sobre `post-refactor`); es idéntica a la inicial, como garantiza el snapshot OpenAPI.

## 4. Pruebas y cobertura (punto 2)

Comando exigido: `uv run pytest --cov --cov-report=term-missing --cov-report=xml --cov-report=html --cov-fail-under=80`.

| Momento | Pruebas | Cobertura | Evidencia |
| --- | --- | --- | --- |
| Suite original del enunciado | 4 | **67 %** (99 líneas sin cubrir) | [`02-…`](evidencias/02-cobertura-inicial-suite-original.txt) |
| Antes de refactorizar (`pre-refactor`) | 160 | **99,67 %** | [`03-…`](evidencias/03-cobertura-antes-refactor.txt) |
| Después de refactorizar (`post-refactor`) | 184 | **99,75 %** | [`08-…`](evidencias/08-cobertura-despues-refactor.txt) |

La única línea sin cubrir es `main.py:38` (`uvicorn.run(...)` bajo `if __name__ == "__main__"`), que no se ejecuta en pruebas. No se
excluyó código de aplicación, no se tocaron validaciones y no se bajó la meta: `omit` solo contiene `tests/`, `scripts/` y entornos.

**Tipos de prueba**
- *Unitarias* (sin HTTP): reglas de negocio sobre `OrderService.quote/confirm/cancel/delete` con datos insertados por SQL (`tests/test_order_rules_unit.py`) y, tras refactorizar, funciones puras de precios (`tests/test_pricing_unit.py`, 22 pruebas).
- *Integración de API*: productos, clientes y pedidos por HTTP con `TestClient` (`test_products_api.py`, `test_customers_api.py`, `test_orders_api.py`).
- *Persistencia*: transacciones/rollback, claves foráneas, `CHECK`, cascada, reinicio, `LAB_DB_PATH`, `503` con base no disponible (`test_database.py`).
- *Contrato*: snapshot del OpenAPI generado **desde el código previo a la refactorización** (`test_api_contract.py`).
- Cada prueba usa una base SQLite temporal (`tmp_path`) y datos propios: son independientes.

**Qué se comprueba (no solo ejecutar líneas):** descuentos por categoría con truncado (99,9→99; 149,85→149; 0,9→0), límite de envío gratis
(9 999 → 1 000; 10 000 → 0), umbral sobre el subtotal *antes* del descuento, envío express siempre 2 500, cotizar = crear, cotizar y
crear no reservan stock, instantánea de precio/categoría ante cambios posteriores, `PUT` que recalcula con valores actuales, estados
finales (confirmado/cancelado), rollback total ante falta de stock en cualquier línea, stock exacto, unicidad de correo, borrado
bloqueado por pedidos, orden por identificador, validación (`422`) con más de 35 entradas inválidas y valores límite, `404`/`409`/`503`.

**Las pruebas detectan regresiones.** Además de la cobertura, se aplicaron mutaciones manuales a los adicionales (p. ej. quitar
`BEGIN IMMEDIATE` hizo fallar las 3 pruebas de concurrencia; quitar la recuperación del *lease* hizo fallar 2). Y durante la refactorización
el snapshot OpenAPI **cazó un cambio de contrato real** (§7, técnica 3).

## 5. SonarQube (punto 3)

### 5.1 Configuración
`docker compose up -d` (tardó varios minutos solo en descargar la imagen). Se cambió la contraseña de `admin` (guardada fuera del
repositorio), se creó el proyecto `tienda-lab` y un token de análisis de proyecto, **no versionados**. El scanner corrió como contenedor
en la red del compose (`sonarsource/sonar-scanner-cli`, `-Dsonar.projectVersion=…`). Resúmenes en texto con
[`scripts/sonar_resumen.py`](../scripts/sonar_resumen.py) y capturas del dashboard (cobertura de **todo el código**, pestaña *Overall Code*).

| Análisis | Versión en SonarQube | Código | Resumen | Capturas |
| --- | --- | --- | --- | --- |
| Inicial, perfil por defecto | `1-inicial` | `pre-refactor` | [`04-…`](evidencias/04-sonarqube-inicial.txt) | [`04-sonar-inicial-*`](capturas/) |
| Inicial, perfil estricto (auxiliar) | `1-inicial-comprehensive` | `pre-refactor` | [`05-…`](evidencias/05-sonarqube-inicial-perfil-comprehensive.txt) | `05-sonar-inicial-comprehensive-*` |
| Final, perfil por defecto | `2-final` | `post-refactor` | [`06-…`](evidencias/06-sonarqube-final.txt) | `06-sonar-final-*` |
| Final, perfil estricto (auxiliar) | `2-final-comprehensive` | `post-refactor` | [`07-…`](evidencias/07-sonarqube-final-perfil-comprehensive.txt) | `07-sonar-final-comprehensive-*` |

### 5.2 Un hallazgo sobre la propia medición: la cobertura «92 %»
El primer análisis (versión `inicial`) mostró **92 %** de cobertura global y `main.py` con **0 %**, aunque pytest medía 99,67 %. Causa: con
`source = ["app", "main"]`, `coverage.xml` escribe las rutas relativas a distintos `<source>` y SonarQube no pudo asociar `main.py`.
Se corrigió en `pyproject.toml` (`source = ["."]`, rutas `app/*.py` y `main.py`; commit «BUILD: Ajusta coverage para que SonarQube resuelva main.py», incluido en `pre-refactor`) y se repitió el análisis inicial (`1-inicial`: 99,7 %).
Es un caso concreto del aviso del enunciado: el reporte de SonarQube y la realidad pueden diferir, y hay que verificarlo. (El primer análisis
queda en el historial del proyecto como `inicial`.) Además se añadió `-p no:cacheprovider` porque el scanner en Docker fallaba al leer `.pytest_cache` en el montaje de Windows.

### 5.3 Resultados

| Métrica (todo el código) | Inicial | Final |
| --- | --- | --- |
| Quality Gate (*Sonar way*, solo código nuevo) | Passed | Passed |
| Cobertura / líneas a cubrir / sin cubrir | 99,7 % / 303 / 1 | 99,8 % / 403 / 1 |
| Bugs · vulnerabilidades · hotspots | 0 · 0 · 0 | 0 · 0 · 0 |
| Duplicación | 0,0 % | 0,0 % |
| Complejidad ciclomática · cognitiva | 69 · 55 | 101 · 33 |
| Líneas de código (incluye pruebas) | 393 | 496 |
| **Hallazgos, perfil *Sonar way*** | **0** | **0** |
| **Hallazgos, perfil *comprehensive*** | **39** | **13** |

La complejidad ciclomática **sube** (69 → 101) porque hay más funciones y más código (también pruebas); lo relevante es que la complejidad
**cognitiva** baja de 55 a 33 y ninguna función supera el umbral de 15.

### 5.4 Explicación de los hallazgos
Con el perfil por defecto SonarQube **no reporta nada** ni antes ni después («No Issues. Hooray!»), a pesar de los smells de diseño de la §6:
las reglas automáticas no ven duplicación estructural de lógica de negocio con distinta forma, el acoplamiento del servicio a HTTP, los
tipos primitivos como códigos ni los *data clumps*. Para tener evidencia complementaria se repitió el análisis con el perfil más estricto:

| Regla | Qué dice | Inicial | Final | Interpretación |
| --- | --- | --- | --- | --- |
| **S3776** | Complejidad cognitiva 19 > 15 en `OrderService.save` (`orders.py:33`) | 1 | **0** | Confirma el smell *Long Method* (§6.2); desaparece al extraer `_price`, `_require_pending`, repositorio. |
| **S1192** | Literal duplicado (SQL `SELECT * FROM … WHERE id = ?` ×6/×3/×3, mensajes «Pedido no encontrado» ×5, «Cliente no encontrado» ×3, «Producto no encontrado» ×3) | 7 | **0** | Confirma el smell de código duplicado (§6.1 y §6.6); resuelto con repositorios y constantes. |
| **S8415** | «Documente este `HTTPException` 404/409 en `responses`» (rutas) | 24 | 6 | **No es una mejora real**: la regla solo inspecciona *handlers*; al mover los `raise` al servicio dejaron de contarse, pero siguen sin documentarse. Documentarlos en `responses=` **cambiaría el OpenAPI** (contrato), y se prohíbe en esta tarea; queda pendiente. |
| **S9154** | `with` anidados (lo introdujo mi primera versión de las rutas) | 0 | 0 | Detectado en un análisis intermedio y corregido (commit «REFACTOR: Combina los with anidados de las rutas», incluido en `post-refactor`). |
| **S5778 / S9073 / S3415** | Pruebas con varias llamadas que pueden lanzar, aserciones compuestas y orden de argumentos | 7 | 7 | Estilo de **mis propias pruebas**; no se cambiaron para mantener «las mismas pruebas antes y después». |

Quality Gate: *Sonar way* solo evalúa **código nuevo** (por eso pasa incluso con 39 hallazgos en el análisis estricto del código existente); con el perfil estricto el gate final marcó *Failed* por 6 hallazgos nuevos
(S8415 y estilo de pruebas), lo cual es coherente con lo anterior.

## 6. Code smells (punto 4)

Líneas del **código inicial** (`git show pre-refactor:app/orders.py`, etc.).

### 6.1 Código duplicado (*Duplicate Code*) — Dispensables
- **Dónde:** `OrderService.save` (`app/orders.py` L44-64) y `OrderService.quote` (L97-116).
- **Fragmento:**
  ```python
  # save, L52-63                                  # quote, L103-114  (idéntico)
  if customer["category"] == "premium":           if customer["category"] == "premium":
      discount = subtotal * 10 // 100                 discount = subtotal * 10 // 100
  elif customer["category"] == "business":        elif customer["category"] == "business":
      discount = subtotal * 15 // 100                 discount = subtotal * 15 // 100
  else:                                           else:
      discount = 0                                    discount = 0
  if shipping == "pickup": delivery = 0           if data.shipping == "pickup": delivery = 0
  elif shipping == "standard":                    elif data.shipping == "standard":
      delivery = 0 if subtotal >= 10_000 else 1_000   delivery = 0 if subtotal >= 10_000 else 1_000
  else: delivery = 2_500                          else: delivery = 2_500
  ```
  También el `SELECT product_id, quantity, unit_price_cents FROM order_items …` (L86-89 y L124-127) y el patrón «buscar pedido o 404» (L36-38, 120-122, 136-138, 160-162, 170-172).
- **Problema de mantenimiento:** las reglas de precio existen en dos copias. **Cambio que lo hace evidente:** subir el umbral de envío gratis a 15 000 o añadir una
  categoría `vip` con 20 %: hay que editar ambos métodos. Olvidar uno hace que la **cotización** (`/orders/quote`) difiera del **total del pedido** que se crea, y solo un prueba que compare ambos lo detecta (las pruebas añadidas lo hacen: `test_quote_and_create_apply_discount_shipping_and_total`).

### 6.2 Método largo y lista larga de parámetros (*Long Method* + *Long Parameter List*) — Bloaters
- **Dónde:** `OrderService.save`, L33-90: **58 líneas**, 7 parámetros, complejidad cognitiva 19 (Sonar S3776, perfil estricto).
- **Fragmento:** `def save(self, customer_id, address, city, postal_code, shipping, items, order_id=None):` y, dentro, en un solo cuerpo: comprobar pedido previo (404/409), cliente (404), productos (404), sumar subtotal, descuento, envío, total, `INSERT` o `UPDATE`, borrar y reinsertar líneas, releer el pedido y sus líneas.
- **Problema de mantenimiento:** hace crear **y** editar con ramas `order_id is None`; es imposible probar el precio sin una base y sin crear el pedido. **Cambio que lo hace evidente:** añadir un campo (cupón, notas) obliga a tocar la firma, las dos rutas y varias ramas del cuerpo.

### 6.3 Clase grande / Cambio divergente (*Large Class* / *Divergent Change*) — Change Preventers
- **Dónde:** `OrderService` (L29-182) mezcla **al menos cinco responsabilidades**: validación de existencia, reglas de precio, SQL, máquina de estados y notificación. `confirm` (L134-156) combina descuento de stock, redacción del mensaje, inserción de la notificación y cambio de estado:
  ```python
  message = f"Pedido {order_id} confirmado. Total: {order['total_cents']} centavos."
  connection.execute("INSERT INTO notifications(order_id, recipient, message) VALUES (?, ?, ?)", ...)
  connection.execute("UPDATE orders SET status = 'confirmed' WHERE id = ?", (order_id,))
  ```
- **Problema de mantenimiento:** razones de cambio distintas modifican la misma clase. **Cambios que lo hacen evidente:** añadir un canal (SMS) o pasar la notificación a una cola (el propio Adicional 2) toca `confirm`; cambiar una regla de precio toca `save` y `quote`; cambiar el esquema de `order_items` toca tres métodos (`save`, `get`, `confirm`) y cinco sentencias SQL.

### 6.4 Obsesión por los primitivos y números/cadenas mágicas (*Primitive Obsession*) — Bloaters
- **Dónde:** el estado `"pending"` (L39, 139, 163, 173), las categorías `"premium"`/`"business"` (L52, 54, 103, 105), los envíos `"pickup"`/`"standard"` (L58, 60, 109, 111) y los números `10`, `15`, `10_000`, `1_000`, `2_500` (L53, 55, 59-63, repetidos en `quote`).
- **Problema de mantenimiento:** cualquier error de escritura **falla en silencio**: una categoría mal escrita cae en `else: discount = 0`, un envío desconocido cae en `else: delivery = 2_500`. **Cambio que lo hace evidente:** renombrar un estado o añadir un método de envío exige buscar literales en el código y en el SQL.

### 6.5 Grupos de datos (*Data Clumps*) — Bloaters
- **Dónde:** `customer_id, address, city, postal_code, shipping, items` viajan siempre juntos: en la firma de `save` (L33) y desempaquetados a mano en las rutas (`create_order` L195-197 y `update_order` L212-214), a partir del `OrderInput` que ya los agrupa.
- **Problema de mantenimiento:** la agrupación existe (`OrderInput`) pero se rompe al llamar al servicio. **Cambio que lo hace evidente:** añadir `country` a la dirección obliga a editar la firma, ambas rutas y el cuerpo (cuatro lugares).

### 6.6 Persistencia repetida y mezclada con las rutas (*Duplicate Code* / capas) — Dispensables
- **Dónde:** `app/customers.py` (L20-28, 48-58, 63-69) y `app/products.py` (L19-24, 35-39, 45-51, 56-62): cada ruta abre la conexión con `request.app.state.database.connect()`, ejecuta SQL, vuelve a leer la fila con `SELECT * … WHERE id = ?` (customers L26, 40, 56) y repite `try/except sqlite3.IntegrityError` (cuatro veces: customers L27-28, 57-58, 68-69 y products L61-62).
- **Problema de mantenimiento:** SQL, reglas de conflicto y formato de errores viven en los *handlers*. **Cambio que lo hace evidente:** cambiar el mensaje de «correo duplicado» exige editar dos lugares (L28 y L58); el texto «Cliente no encontrado» aparece tres veces.

### 6.7 Acoplamiento del servicio a HTTP — Couplers *(identificado, **no resuelto**)*
- **Dónde:** `OrderService` lanza `HTTPException` en 14 puntos (L38, 40, 43, 49, 96, 101, 122, 138, 140, 148, 162, 164, 172, 174). Las pruebas unitarias actuales capturan `HTTPException`, así que cambiarlo habría modificado las expectativas de las pruebas. Se documenta como limitación (§11) y se trata en el Adicional 3.

## 7. Técnicas de refactorización aplicadas (punto 5)

Se aplicaron **seis** técnicas distintas (se exigían tres). Las 160 pruebas previas se ejecutaron sin cambios tras cada paso.

### 7.1 Extract Function + Extract Class (módulo de precios) → resuelve 6.1 y 6.2
**Problema:** reglas de precio duplicadas en `save` y `quote`. **Dónde:** nuevo [`app/pricing.py`](../app/pricing.py) usado por `OrderService._price`.
```python
# ANTES (orders.py, dos copias de 12 líneas)
if customer["category"] == "premium":
    discount = subtotal * 10 // 100
elif customer["category"] == "business":
    discount = subtotal * 15 // 100
else:
    discount = 0
...
# DESPUÉS (pricing.py, una sola vez; funciones puras)
def calculate_discount(category, subtotal_cents):
    return subtotal_cents * DISCOUNT_PERCENT_BY_CATEGORY.get(category, 0) // 100

def price_order(category, method, subtotal_cents) -> PriceBreakdown: ...
```
**Por qué mejora:** una sola fuente de verdad (cotizar y crear no pueden divergir), reglas probables sin base de datos (22 pruebas puras) y `save` pasa de 58 a ~10 líneas.

### 7.2 Introduce Parameter Object → resuelve 6.5 y parte de 6.2
**Problema:** siete parámetros sueltos. **Dónde:** `OrderService.save(data: OrderInput, order_id=None)`, `PricedOrder` (`NamedTuple`) y `PriceBreakdown` (`dataclass` inmutable).
```python
# ANTES
def save(self, customer_id, address, city, postal_code, shipping, items, order_id=None): ...
return OrderService(db).save(data.customer_id, data.address, data.city, data.postal_code, data.shipping, data.items)
# DESPUÉS
def save(self, data: OrderInput, order_id=None): ...
return service.save(data)
```
**Por qué mejora:** añadir `country` ya no toca la firma ni las rutas; el cálculo devuelve un objeto con nombre (`subtotal_cents`, `discount_cents`, `shipping_cents`, `total_cents`) en vez de tuplas.

### 7.3 Replace Type Code with Enum + Replace Magic Number with Symbolic Constant (+ condicional por tabla) → resuelve 6.4
**Dónde:** [`app/domain.py`](../app/domain.py) (`CustomerCategory`, `ShippingMethod`, `OrderStatus`) y constantes en `pricing.py`.
```python
# ANTES                                     # DESPUÉS
if order["status"] != "pending": ...        if order["status"] != OrderStatus.PENDING: ...
discount = subtotal * 10 // 100             DISCOUNT_PERCENT_BY_CATEGORY = {CustomerCategory.PREMIUM: 10, ...}
delivery = 0 if subtotal >= 10_000 ...      FREE_STANDARD_SHIPPING_FROM_CENTS = 10_000
```
**Por qué mejora:** el vocabulario del dominio vive en un solo lugar y las reglas son datos (una tabla), no una escalera de `if/elif`; se añade una categoría con una línea.
**Lección del contrato:** usar el `Enum` directamente en los modelos Pydantic cambió el OpenAPI (un `$ref` en lugar del enum en línea) y el **snapshot de contrato falló**. Se resolvió con `Literal[ShippingMethod.PICKUP, …]`, que valida igual y mantiene el esquema idéntico.

### 7.4 Extract Class (Repository) + Move Method → resuelve 6.3 y 6.6
**Dónde:** [`app/order_repository.py`](../app/order_repository.py) (`OrderRepository`) y `ProductRepository`/`CustomerRepository` en sus archivos. Toda la SQL salió del servicio y de las rutas; el repositorio trabaja sobre una conexión abierta, de modo que la **transacción sigue controlándola el servicio** (`with database.connect()`): se conserva la integridad (p. ej. confirmar sigue siendo atómico).
```python
# ANTES (en confirm)                                          # DESPUÉS
cursor = connection.execute("UPDATE products SET stock ...")   if not orders.deduct_stock(item["product_id"], item["quantity"]):
if cursor.rowcount == 0: raise HTTPException(409, ...)             raise HTTPException(409, ...)
```
**Por qué mejora:** `OrderService` queda con reglas y transiciones; cambiar el esquema o la BD toca un solo archivo; los literales SQL duplicados desaparecen (S1192: 7 → 0).

### 7.5 Extract Method / Consolidate Duplicate → resuelve 6.1 (resto) y 6.6
**Dónde:** `OrderService._require_pending`, `_require_detail`, `_price`; y `conflict_on_integrity_error` en [`app/dependencies.py`](../app/dependencies.py).
```python
# ANTES (repetido 4 veces)                          # DESPUÉS
try:                                                with conflict_on_integrity_error("El correo ya está registrado"), \
    with db.connect() as connection: ...                     database.connect() as connection: ...
except sqlite3.IntegrityError:
    raise HTTPException(409, "El correo ya está registrado") from None
```
**Por qué mejora:** «buscar pedido, 404 si falta, 409 si no está pendiente» pasa de cuatro copias (editar, confirmar, cancelar, eliminar) a un método; el mensaje de correo duplicado queda definido una vez (`DUPLICATE_EMAIL`).

### 7.6 Inyección de dependencias en las rutas (Replace Constructor Call with Dependency)
**Dónde:** `get_database`/`DatabaseDep` y `get_order_service`/`OrderServiceDep` (`Annotated[..., Depends(...)]`). Las rutas dejaron de hacer `OrderService(request.app.state.database)` nueve veces y de leer `request.app.state` directamente; la dependencia es reemplazable en pruebas.

### 7.7 Tropiezo útil durante la refactorización
El método del servicio llamado `list` **sombreaba el `list` nativo dentro de la clase** y rompió una anotación (`list[tuple]`) al importar el módulo. Se resolvió con un `NamedTuple` (`PricedOrder`); es el mismo problema que ya existía en el código inicial (`def list` en L130) y que ahora está contenido.

## 8. Diagramas de clases (punto 6)

Fuente editable (Mermaid) y vistas exportadas, solo con **clases reales** (las rutas y módulos de funciones se indican en notas, no como clases):

| | Fuente | Vista |
| --- | --- | --- |
| Inicial (`pre-refactor`) | [`clases-inicial.mmd`](diagramas/clases-inicial.mmd) | [SVG](diagramas/clases-inicial.svg) · [PNG](diagramas/clases-inicial.png) |
| Final (`post-refactor`) | [`clases-final.mmd`](diagramas/clases-final.mmd) | [SVG](diagramas/clases-final.svg) · [PNG](diagramas/clases-final.png) |

Resumen. **Inicial:** `OrderService` (8 métodos, `save` con 7 parámetros) → `Database`; depende de `OrderInput`/`ItemInput`
(`BaseModel`) y de `HTTPException`; `ProductInput` y `CustomerInput` aparecen sin servicio ni repositorio (la lógica está en funciones de ruta).
**Final:** `OrderService` → `Database`, crea un `OrderRepository` por transacción, calcula un `PriceBreakdown` (dataclass inmutable) y devuelve un
`PricedOrder`; tres enumeraciones (`CustomerCategory`, `ShippingMethod`, `OrderStatus`); `ProductRepository` y `CustomerRepository`.
Las asociaciones y dependencias están etiquetadas. Se exportaron con Mermaid 11 renderizado en Edge (Mermaid CLI no arrancó en este equipo).

## 9. Adicionales (resueltos, cada uno en su rama)

Son independientes entre sí y **no forman parte de la base** (no están en `main`; cada rama parte del commit «DOCS: Añade evidencias de SonarQube, diagramas de clases y scripts de apoyo»).

| Adicional | Rama | Resumen | Documento |
| --- | --- | --- | --- |
| 1. Idempotencia | `adicional-1-idempotencia` | Cabecera `Idempotency-Key`; la clave se guarda en la **misma transacción** que el pedido. Misma clave+contenido → respuesta original; contenido distinto → 409; concurrencia → un pedido (8 hilos); persiste tras reiniciar; un error no deja clave ni pedido parcial. 26 pruebas; contrato solo aditivo. | [`ADICIONAL-1-IDEMPOTENCIA.md`](https://github.com/AntonioCot7/Optional-Task-Software-2K26-2/blob/adicional-1-idempotencia/docs/ADICIONAL-1-IDEMPOTENCIA.md) (en su rama) |
| 2. Cola y DLQ | `adicional-2-cola-dlq` | *Outbox* transaccional; un worker con reintentos (máx. 3, espera 2 s y 4 s), fallos simulados reproducibles, DLQ con contenido/intentos/causa, reprocesamiento explícito, recuperación por *lease*, efecto idempotente por `dedup_key`. 33 pruebas; demo real en `docs/evidencias/10-demo-cola-dlq.txt` (en la rama). | [`ADICIONAL-2-COLA-DLQ.md`](https://github.com/AntonioCot7/Optional-Task-Software-2K26-2/blob/adicional-2-cola-dlq/docs/ADICIONAL-2-COLA-DLQ.md) (en su rama) |
| 3. Análisis VHS | `main` | Conclusión: **adopción parcial y gradual**; no completa ahora. Distingue beneficios de la arquitectura de problemas que resuelve una refactorización sencilla; fuentes y límites de la investigación. | [`ADICIONAL-3-VHS.md`](ADICIONAL-3-VHS.md) |

Las pruebas de ambos adicionales: 210 (adicional 1) y 217 (adicional 2) en total en sus ramas, todas en verde, cobertura 99,77 % y 98,43 %.

## 10. Cómo reproducir

```bash
# 1. Entorno (Python 3.13 fijado en .python-version)
uv sync --locked

# 2. Aplicación y recorrido del README (con el servidor levantado en otra terminal)
uv run main.py
uv run python scripts/recorrido.py            # imprime cada paso y falla (exit 1) si algo difiere

# 3. Pruebas y cobertura (genera coverage.xml y htmlcov/index.html)
uv run pytest --cov --cov-report=term-missing --cov-report=xml --cov-report=html --cov-fail-under=80

# 4. SonarQube (primer arranque: crear proyecto `tienda-lab` y un token; no los suba al repositorio)
docker compose up -d                           # esperar http://localhost:9000
# con SonarScanner CLI instalado:  sonar-scanner      (como indica el README)
# o con Docker, desde la raíz (red del compose y sin instalar nada):
docker run --rm --network <carpeta>_default -e SONAR_HOST_URL=http://sonarqube:9000 -e SONAR_TOKEN \
    -v "$PWD:/usr/src" sonarsource/sonar-scanner-cli -Dsonar.projectVersion=mi-version
SONAR_LOGIN=admin SONAR_PASSWORD=... uv run python scripts/sonar_resumen.py   # resumen en texto

# 5. Comparar revisiones
git checkout pre-refactor    # inicial   ·   git checkout post-refactor   # final
git diff pre-refactor post-refactor --stat -- app main.py
uv run python scripts/export_openapi.py /tmp/openapi.json   # regenerar el snapshot de contrato (solo si el cambio es deliberado)
docker compose down                            # detener SonarQube conservando sus datos
```

Evidencias en `docs/evidencias/` (texto con fecha, comando y salida) y `docs/capturas/` (imágenes). No se versionan `.venv`, bases de datos,
caches, tokens ni contraseñas (`.gitignore`; `data/`, `htmlcov/`, `coverage.xml` y `.scannerwork/` están ignorados).

## 11. Limitaciones y pendientes

- **Acoplamiento a HTTP en el servicio** (§6.7): `OrderService` sigue lanzando `HTTPException`. Reemplazarlo por errores de dominio es la mejora más valiosa y barata, pero cambia las expectativas de las pruebas unitarias actuales (capturan `HTTPException`); se dejó fuera para cumplir «mismas pruebas antes y después».
- **S8415** (documentar `HTTPException` en `responses`): requiere cambiar el OpenAPI; quedó sin aplicar para conservar el contrato.
- **Pruebas con hallazgos de estilo** (S5778, S9073, S3415) bajo el perfil estricto: no se corrigieron para no alterar la suite entre antes y después.
- **Quality Gate** *Sonar way* solo mira código nuevo: no refleja el estado global; por eso se informan métricas globales y el análisis auxiliar.
- **Capturas:** las de SonarQube se tomaron con Playwright sobre Edge y las de Swagger a mano. Un aviso de producto de SonarQube aparece en una esquina de algunas capturas.
- **Escaneo del estado final** con el scanner en Docker y `-Dsonar.scm.disabled=true` (el *worktree* de git no es visible dentro del contenedor): afecta solo a la información de autoría (*blame*), no a las métricas ni a los hallazgos.
- **SonarQube** usa la base H2 embebida (aviso del propio servidor: solo para evaluación); es un laboratorio local.
- Cobertura de línea ≠ calidad de las aserciones: se complementó con mutaciones manuales solo en los adicionales; no hay una herramienta de mutación automática.
- `main.py:38` (arranque de `uvicorn`) no se ejecuta en pruebas.
- Concurrencia: SQLite serializa escrituras con `BEGIN IMMEDIATE` y timeout de 10 s; no se hizo prueba de carga. Los adicionales documentan sus propios límites (clave global sin tenant, sin caducidad; un solo worker; entrega simulada).
- Cada análisis de SonarQube sin Developer Edition se hace sobre una sola rama (la principal del proyecto): las ramas de los adicionales no se analizaron con SonarQube.
