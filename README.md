# FEKA STORE

Laboratorio de pruebas, análisis estático y refactorización de una API de pedidos e inventario. El código contiene problemas de diseño intencionales. El objetivo es identificarlos y mejorar su estructura sin cambiar las reglas de negocio.

> **Entrega.** El informe (smells, refactorizaciones, SonarQube antes/después, diagramas, comandos y limitaciones) está en [`docs/INFORME.md`](docs/INFORME.md). Análisis VHS (Adicional 3): [`docs/ADICIONAL-3-VHS.md`](docs/ADICIONAL-3-VHS.md). Los Adicionales 1 y 2 están en las ramas `adicional-1-idempotencia` y `adicional-2-cola-dlq`. Revisiones: `estado-inicial` → `pre-refactor` → `post-refactor`.

## Requisitos

- Git y uv.
- Python 3.13 o superior. uv puede instalar la versión indicada si no está disponible.
- Docker con Compose para ejecutar SonarQube local.
- SonarScanner CLI para enviar el análisis a SonarQube. Instalación: https://docs.sonarsource.com/sonarqube-server/analyzing-source-code/scanners/sonarscanner

La API utiliza FastAPI y SQLite. No requiere una base de datos externa. Usa datos ficticios: la aplicación es un laboratorio local sin autenticación.

## Ejecutar

Desde la raíz del repositorio:

```bash
uv sync --locked
uv run main.py
```

`uv sync` crea `.venv` e instala las dependencias de aplicación y desarrollo declaradas en `pyproject.toml`. `uv.lock` fija sus versiones. No hace falta activar el entorno ni crear un `requirements.txt`.

- Swagger: http://127.0.0.1:8000/docs
- OpenAPI: http://127.0.0.1:8000/openapi.json
- Detener: Ctrl+C.

Los datos se guardan en `data/store.sqlite3` y sobreviven al reinicio. Para usar otra ubicación, define `LAB_DB_PATH` antes de iniciar. Para comenzar de cero, detén el servidor y elimina únicamente esa base de datos; se perderán sus registros.

## Caso de negocio

Una tienda registra clientes y productos. Cada pedido contiene uno o más productos, una dirección y un tipo de envío. Al crearlo queda pendiente; al confirmarlo se descuentan las existencias y se registra una notificación local. No se envían correos reales.

Todos los importes se expresan en centavos enteros de una misma moneda. Por ejemplo, `2500` representa 25,00.

| Regla | Comportamiento |
| --- | --- |
| Cliente regular | Sin descuento |
| Cliente premium | 10 % de descuento sobre el subtotal |
| Cliente business | 15 % de descuento sobre el subtotal |
| Redondeo | El descuento se trunca a centavos enteros |
| Envío pickup | Gratis |
| Envío standard | 1000 centavos; gratis si el subtotal es al menos 10000 |
| Envío express | 2500 centavos |
| Total | Subtotal menos descuento más envío |

El umbral de envío gratis se aplica al subtotal anterior al descuento.

- Crear y cotizar pedidos no reserva ni descuenta stock. La falta de stock se comprueba al confirmar.
- Cada producto aparece una sola vez por pedido, con cantidad positiva.
- El pedido conserva precios, categoría y totales calculados al guardarlo. Cambiar después un producto o la categoría del cliente no recalcula ese pedido.
- `PUT /orders/{id}` reemplaza un pedido pendiente y recalcula todas sus líneas con los precios y la categoría actuales.
- Solo los pedidos pendientes pueden editarse, eliminarse o cancelarse. Los pedidos confirmados y cancelados son finales.
- Confirmar es una transacción: si falta stock en cualquier línea, no cambia ninguna existencia, no se confirma el pedido ni se registra una notificación.
- Repetir una confirmación devuelve `409`. Esto protege la transición de estado, pero no implementa la extensión de idempotencia de este laboratorio.
- Un producto o cliente asociado a un pedido no puede eliminarse. Los correos de clientes son únicos.
- Las listas se devuelven ordenadas por identificador; el listado de pedidos es un resumen y su detalle incluye las líneas.

## Recorrido inicial

En Swagger, ejecuta cada operación con **Try it out**. En una base vacía, los primeros identificadores serán `1`; si ya tienes datos, utiliza los que devuelvan las respuestas.

1. Crea un cliente con `POST /customers`:

```json
{
  "name": "Ana Torres",
  "email": "ana@example.com",
  "category": "premium"
}
```

2. Crea un producto con `POST /products`:

```json
{
  "name": "Libro de algoritmos",
  "price_cents": 2500,
  "stock": 5
}
```

3. Cotiza mediante `POST /orders/quote` y crea mediante `POST /orders`, usando este cuerpo en ambas operaciones:

```json
{
  "customer_id": 1,
  "address": "Av. Primavera 100",
  "city": "Lima",
  "postal_code": "15001",
  "shipping": "standard",
  "items": [
    {"product_id": 1, "quantity": 2}
  ]
}
```

El resultado debe tener subtotal `5000`, descuento `500`, envío `1000` y total `5500`.

4. Confirma con `POST /orders/1/confirm`.
5. Consulta `GET /products/1`: el stock debe ser `3`.
6. Consulta `GET /orders/1/notifications`: debe existir una notificación.
7. Repite la confirmación: debe responder `409` sin otro descuento de stock.

## Operaciones

| Recurso | Operaciones |
| --- | --- |
| `/products` | POST, GET |
| `/products/{id}` | GET, PUT, DELETE |
| `/customers` | POST, GET |
| `/customers/{id}` | GET, PUT, DELETE |
| `/orders` | POST, GET |
| `/orders/quote` | POST |
| `/orders/{id}` | GET, PUT, DELETE |
| `/orders/{id}/confirm` | POST |
| `/orders/{id}/cancel` | POST |
| `/orders/{id}/notifications` | GET |

`PUT` recibe el cuerpo completo. Los errores usan `detail`: `404` para recursos inexistentes, `409` para conflictos, `422` para entradas inválidas y `503` cuando SQLite no está disponible. Las eliminaciones exitosas responden `204` sin cuerpo.

## Organización

Se usa una organización básica VSA (*Vertical Slice Architecture*, arquitectura por cortes verticales): productos, clientes y pedidos agrupan sus rutas y comportamiento por funcionalidad. `app/database.py` comparte la persistencia y `main.py` inicia la API. La organización por archivos no garantiza que las responsabilidades internas estén bien distribuidas; eso forma parte del análisis.

## Trabajo obligatorio

### 1. Ejecutar la aplicación

Reproduce el recorrido inicial y adjunta evidencia de la API en funcionamiento. Registra las versiones de Python y uv utilizadas.

### 2. Escribir pruebas

Amplía las pruebas iniciales hasta alcanzar **al menos 80 % de cobertura de líneas del código de aplicación**, tanto antes como después de refactorizar.

```bash
uv run pytest --cov --cov-report=term-missing --cov-report=xml --cov-report=html --cov-fail-under=80
```

El comando genera `coverage.xml` para SonarQube y `htmlcov/index.html` para inspección local. La suite inicial es un punto de partida; no alcanza necesariamente la meta.

Incluye pruebas unitarias de reglas de negocio y pruebas de integración de API y persistencia. Usa bases temporales y datos independientes. Las pruebas deben comprobar resultados y efectos, no limitarse a ejecutar líneas. Considera descuentos, límites, envíos, validación, recursos inexistentes, operaciones CRUD, estados, stock insuficiente y persistencia. Las pruebas HTTP iniciales son de integración; no sustituyen las unitarias que debes escribir.

No excluyas código de aplicación, elimines validaciones ni reduzcas la meta para alcanzar el porcentaje. No necesitas tener el servidor encendido para ejecutar pytest.

### 3. Ejecutar SonarQube local

```bash
docker compose up -d
```

Espera a que http://localhost:9000 esté disponible. El primer inicio puede tardar varios minutos. En una instalación nueva, ingresa con `admin` / `admin` y cambia la contraseña.

1. Crea un proyecto local con clave `tienda-lab`.
2. Selecciona análisis local/manual y genera un token de análisis del proyecto.
3. Desde la raíz del repositorio, genera primero `coverage.xml` con el comando de pruebas anterior.
4. Ejecuta SonarScanner CLI con el token en una variable de entorno.

macOS / Linux:

```bash
export SONAR_HOST_URL=http://localhost:9000
read -s SONAR_TOKEN
export SONAR_TOKEN
sonar-scanner
unset SONAR_TOKEN
```

Después de `read`, pega el token y pulsa Enter; no aparecerá en pantalla.

PowerShell:

```powershell
$env:SONAR_HOST_URL = "http://localhost:9000"
$secret = Read-Host "Token de SonarQube" -AsSecureString
$env:SONAR_TOKEN = [System.Net.NetworkCredential]::new("", $secret).Password
sonar-scanner
Remove-Item Env:SONAR_TOKEN
```

El archivo `sonar-project.properties` ya define fuentes, pruebas y ubicación del reporte. SonarQube importa la cobertura generada por pytest; no ejecuta las pruebas. Si la ejecución de pytest falla por cobertura insuficiente, el reporte igualmente permite revisar el avance, pero la entrega debe superar la meta.

Adjunta capturas del análisis inicial y final: resumen del proyecto, cobertura de **todo el código** y hallazgos relevantes. No confundas la cobertura de código nuevo con la cobertura global. Registra la versión de SonarQube y el perfil de calidad. Explica los hallazgos aunque el Quality Gate use otros criterios.

No subas tokens ni contraseñas. Para detener SonarQube conservando sus datos:

```bash
docker compose down
```

Si no inicia, consulta `docker compose logs sonarqube`. Docker debe tener recursos suficientes y cumplir los requisitos del host indicados por SonarQube: https://docs.sonarsource.com/sonarqube-server/server-installation/pre-installation/installation-requirements

### 4. Identificar code smells

Documenta **al menos cuatro**. Para cada uno indica:

- Nombre y categoría.
- Archivo, clase o función y líneas del código inicial.
- Fragmento que lo demuestra.
- Problema de mantenimiento concreto y cambio que lo haría evidente.

Los resultados de SonarQube son evidencia complementaria. No todos los code smells de diseño aparecen como hallazgos automáticos y no todo hallazgo corresponde a uno de los smells estudiados.

### 5. Refactorizar

Propón y **aplica al menos tres técnicas distintas de refactorización**. Para cada técnica presenta el problema, el lugar donde la aplicaste, un ejemplo antes/después y la razón de la mejora. El caso permite explorar más de cuatro técnicas; no necesitas aplicarlas todas.

Mantén rutas, contratos HTTP, reglas de negocio, persistencia e integridad transaccional. Ejecuta las mismas pruebas antes y después, amplíalas cuando corresponda y repite el análisis de SonarQube. No cambies las expectativas de las pruebas para ocultar una regresión. No es obligatorio eliminar todos los hallazgos ni introducir patrones que el dominio no necesite.

### 6. Diagramar y entregar

Entrega un diagrama de clases inicial y otro final, con atributos y métodos relevantes, asociaciones y dependencias. No representes funciones o módulos como clases ficticias. Puedes usar Mermaid, PlantUML o una herramienta equivalente.

En el repositorio de entrega incluye:

- Código, pruebas, `pyproject.toml` y `uv.lock`.
- Informe con los cuatro smells y las tres técnicas aplicadas.
- Diagramas inicial y final, como fuente editable y vista exportada.
- Capturas de ejecución, pruebas, cobertura y SonarQube antes y después.
- Comandos para reproducir los resultados y explicación de limitaciones pendientes.

Conserva un commit o etiqueta del estado previo a la refactorización e identifica en el informe las revisiones inicial y final. No entregues `.venv`, bases de datos, tokens ni cachés.

## Adicional 1: idempotencia

Implementa idempotencia en `POST /orders` mediante una clave enviada por el cliente. Define el contrato y documenta su alcance.

Criterios de aceptación:

- Misma clave y mismo contenido: devuelve el resultado original, sin crear otro pedido.
- Misma clave con contenido distinto: devuelve un conflicto.
- Peticiones concurrentes con la misma clave: solo se crea un pedido.
- El comportamiento se conserva después de reiniciar la aplicación.
- Un error no deja una clave bloqueada permanentemente ni un pedido parcial.

Demuestra cada caso con pruebas. Considera persistencia, unicidad y transacciones. La protección existente contra confirmar dos veces un pedido no resuelve la repetición de su creación. No se exige caducidad de claves para este adicional.

## Adicional 2: cola y dead letter queue

Sustituye el registro síncrono de notificaciones por procesamiento asíncrono mediante una cola y un worker. Puedes utilizar SQLite; no se requiere AWS, LocalStack ni un broker externo.

Criterios de aceptación:

- Confirmar deja una notificación pendiente y el worker la procesa por separado.
- Una notificación puede fallar temporalmente o de forma permanente mediante una simulación reproducible.
- Los fallos temporales se reintentan con un máximo documentado y una espera entre intentos.
- Al agotar los intentos, el mensaje pasa a una DLQ conservando contenido, intentos y causa del último error.
- Es posible consultar la DLQ y solicitar un reprocesamiento explícito.
- Los pendientes sobreviven a un reinicio y un mensaje en procesamiento puede recuperarse si el worker se interrumpe.
- Procesar un mensaje duplicado no duplica el efecto de notificación.

Empieza con un único worker y documenta ese alcance. No prometas entrega exactamente una vez: explica cómo manejas los duplicados. La notificación puede seguir siendo un registro local; no necesitas enviar correos reales.

## Adicional 3: análisis de arquitectura VHS

Investiga la propuesta VHS (*Vertical, Hexagonal y Screaming Architecture*) y evalúa si sería adecuada para este proyecto ante un crecimiento de funcionalidades e integraciones.

Presenta:

- Qué aporta cada enfoque y cómo se relacionan.
- Qué características de la arquitectura actual facilitarían o dificultarían adoptarla, con referencias al código.
- Un escenario concreto de crecimiento y una propuesta breve de organización para ese escenario.
- Tu conclusión: ¿la adoptarías completa, parcialmente o mantendrías la estructura actual? Justifica los beneficios y costos.
- Las fuentes consultadas.

No se requiere implementarla. Se evaluará la justificación; concluir que no conviene también es válido. Distingue los beneficios de la arquitectura de los problemas que podrían resolverse con una refactorización más sencilla.

Los tres adicionales son independientes. Sus soluciones no forman parte de la implementación base.
