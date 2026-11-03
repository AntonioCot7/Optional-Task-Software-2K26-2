# Adicional 3: ¿conviene VHS (Vertical, Hexagonal y Screaming Architecture) en FEKA STORE?

Análisis, sin implementación. Las referencias de código apuntan al estado final de la refactorización (tag `post-refactor`).

> **Nota sobre el término.** En mi búsqueda **no encontré «VHS» definido como un patrón único con autor y especificación**. Lo
> interpreto, como sugiere el enunciado, como la **combinación de tres ideas independientes**: *Vertical Slice* (organizar por
> capacidad), *Hexagonal* (aislar el núcleo con puertos y adaptadores) y *Screaming* (que la estructura grite el dominio). La
> combinación de las dos primeras sí aparece en proyectos de ejemplo (ver fuentes, p. ej. *HexaVerticalSlice*). Todo lo que sigue
> evalúa esa combinación, no un estándar con reglas cerradas.

## 1. Qué aporta cada enfoque y cómo se relacionan

| Enfoque | Idea central | Qué resuelve | Qué cuesta |
| --- | --- | --- | --- |
| **Screaming Architecture** (R. C. Martin, 2011) | La estructura de más alto nivel debe «gritar» los casos de uso («Sistema de pedidos»), no el framework («FastAPI», «SQLite»). Los marcos son herramientas, no la arquitectura; las decisiones de framework/BD deben poder diferirse. | Legibilidad y descubrimiento: abrir el repo y entender de qué trata. | Casi nada; es una regla de nombres y agrupación. Martin no prescribe un layout ni da costos. |
| **Vertical Slice** (J. Bogard, 2018) | Organizar por petición/caso de uso y no por capa técnica: «minimizar el acoplamiento entre *slices* y maximizar el acoplamiento dentro de un *slice*». Evitar abstracciones compartidas (repositorios, servicios) salvo necesidad. | Cohesión por cambio: una funcionalidad nueva *añade* código en vez de tocar capas compartidas. Cada slice puede elegir su enfoque (de *transaction script* a dominio rico). | Duplicación entre slices; presupone un equipo que sepa detectar smells y refactorizar (Bogard lo advierte expresamente). |
| **Hexagonal / Puertos y Adaptadores** (A. Cockburn, 2005) | El núcleo de aplicación expone **puertos** (interfaces); los **adaptadores** (HTTP, BD, colas, proveedores) viven fuera y se conectan a ellos. Los adaptadores *conductores* llaman al núcleo; los *conducidos* son llamados por él. | Aislar reglas de negocio de la entrega y la infraestructura: pruebas sin BD ni red, cambio de adaptador sin tocar el núcleo. | Indirección y más piezas (interfaces, mapeos); se nota poco mientras haya una sola implementación de cada puerto. |

**Cómo se relacionan.** Ejes distintos y complementarios: *Screaming* decide **cómo se nombra y agrupa** (el eje del dominio);
*Vertical Slice* decide **cómo se corta** el sistema (por capacidad, no por capa); *Hexagonal* decide **hacia dónde apuntan las
dependencias** dentro de cada corte (hacia el núcleo, con las tecnologías en la periferia). Un slice «Pedidos» con su propio
hexágono (dominio + casos de uso + puertos + adaptadores) es lo que el ejemplo *HexaVerticalSlice* demuestra; sus propios autores
anotan los costos de la mezcla: «duplicación de puertos por caso de uso» y «sin separación física: es muy fácil crear una
dependencia entre una clase de negocio y una de infraestructura».

## 2. Qué de la arquitectura actual facilita o dificulta adoptarla

### Facilita
| Característica | Dónde | Por qué ayuda |
| --- | --- | --- |
| Ya hay cortes verticales «ligeros»: un archivo por funcionalidad con su ruta y su comportamiento | `app/products.py`, `app/customers.py`, `app/orders.py` | Es el paso 1 de VSA y de Screaming (los nombres hablan de la tienda, no de capas). |
| Existe un **núcleo puro** sin imports de framework | `app/pricing.py`, `app/domain.py` (solo `dataclasses`/`enum`) | Es ya el interior de un hexágono: reglas de precio testeables sin BD (22 pruebas unitarias puras). |
| La persistencia está detrás de una clase con una sola responsabilidad | `OrderRepository` (`app/order_repository.py`), `ProductRepository`, `CustomerRepository` | Son *puertos de facto*: extraer una interfaz después es mecánico. |
| La BD llega por inyección | `get_database` / `DatabaseDep` (`app/dependencies.py`) | Los adaptadores conductores no construyen sus dependencias. |
| Red de seguridad para reestructurar | 184 pruebas + snapshot del contrato OpenAPI (`tests/test_api_contract.py`) | Se puede mover código sin cambiar comportamiento ni rutas, que es lo que exige adoptar la arquitectura por pasos. |
| Tamaño pequeño y un solo despliegue | ~400 líneas de aplicación | Migrar es barato *ahora*; el costo de hacerlo crece con el código. |

### Dificulta
| Característica | Dónde | Por qué molesta |
| --- | --- | --- |
| **El núcleo conoce HTTP**: los casos de uso lanzan `HTTPException` | `app/orders.py:81`, `:112`, `:119`, `:121`, `:129`, `:135` | Rompe la regla central de Hexagonal (los adaptadores quedan fuera del núcleo). Un segundo adaptador conductor (CLI, mensajería) heredaría códigos HTTP. Es la mayor deuda real. |
| **Los modelos de entrada son a la vez DTO HTTP y entrada del dominio**: `OrderInput`/`ItemInput` heredan de `pydantic.BaseModel` y se pasan al servicio | `app/orders.py:12-30`, `OrderService.save/create(data: OrderInput)` | Acopla casos de uso a Pydantic; habría que separar *schema de transporte* y *comando de aplicación* (y mapear entre ambos). |
| **Tipos de persistencia que se filtran al servicio**: `PricedOrder.customer` es un `sqlite3.Row` | `app/orders.py:38` | El servicio depende de la forma de la fila; un puerto real devolvería tipos del dominio. |
| **Acoplamiento entre slices a través del esquema**: el repositorio de pedidos lee las tablas de clientes y productos | `app/order_repository.py:35-39` (`SELECT * FROM customers/products`) | VSA pide minimizar el acoplamiento entre slices; aquí pedidos conoce las columnas ajenas. En hexagonal serían puertos hacia otros slices (p. ej. `CustomerDirectory`, `ProductCatalog`). |
| **Esquema y migraciones únicos y compartidos** | `app/database.py` (`initialize()` crea las tablas de todos los slices) | Impide que un slice evolucione su almacenamiento de forma independiente. |
| **Integraciones incrustadas en el caso de uso**: la notificación es un `INSERT` dentro de `confirm` | `app/orders.py:82-84`, `OrderRepository.add_notification` | Cada integración nueva (pago, transporte, ERP) se añadiría al mismo método. Es el lugar natural de un puerto. |
| **Datos externos y reloj acoplados** (`time.time`, SQLite como único almacén) | `OrderService.confirm`, `Database.connect` | Dificulta pruebas deterministas y sustituir infraestructura. |

*Qué ya se resolvió sin VHS:* extraer precios (`pricing.py`), repositorios, enums y la inyección de la BD fueron refactorizaciones
locales (Extract Class/Function, Replace Type Code), no exigieron puertos ni reorganización de carpetas.

## 3. Escenario concreto de crecimiento y propuesta de organización

**Escenario.** La tienda pasa de un monolito de un solo equipo a **tres equipos** y suma integraciones: **pasarela de pagos**
(autorizar al confirmar, reembolsar al cancelar), **cotización y guías de un operador logístico** (el envío deja de ser una tarifa
fija), **notificaciones multicanal** (correo, SMS, webhook) y **eventos hacia un ERP**; además aparecen un **CLI de back-office** y
un consumidor de mensajes como segundas puertas de entrada. Cada integración tiene proveedores sustituibles y fallos propios.

**Propuesta breve** (cada slice con su pequeño hexágono; las dependencias van hacia adentro):

```
app/
  orders/                       # el slice «grita» el negocio
    domain/        order.py  pricing.py  status.py   # sin imports de framework ni de BD
    application/   create_order.py  confirm_order.py  cancel_order.py   # casos de uso; lanzan errores de dominio
    ports.py       OrderRepository · CustomerDirectory · ProductCatalog · PaymentGateway · ShippingQuoter · EventPublisher
    adapters/
      http.py            # FastAPI + schemas Pydantic + traducción error de dominio → HTTP
      sqlite_orders.py   # implementa OrderRepository
  catalog/        (productos)        ← expone CatalogService/ProductCatalog a otros slices
  customers/      (clientes)         ← expone CustomerDirectory
  payments/       adapters/ fake_gateway.py  provider_x.py
  shipping/       adapters/ flat_rate.py  carrier_y.py
  notifications/  cola, worker y canales (email/sms/webhook) como adaptadores
  shared/         database.py (conexión/UoW) · errors.py
```

Reglas: (1) un slice **no lee tablas de otro**; se habla por puertos/servicios de aplicación; (2) los casos de uso levantan errores
de dominio y solo `adapters/http.py` los traduce a 404/409; (3) se crea un puerto **cuando hay una integración externa o una segunda
implementación**, no por cada tabla; (4) las pruebas del núcleo usan adaptadores en memoria.

## 4. Conclusión: **adopción parcial y gradual**; no la completa ahora

**Recomendación:** mantener la estructura vertical actual (ya cumple en lo esencial VSA y Screaming) y adoptar de Hexagonal
**solo los puertos que justifiquen las integraciones**, en el momento en que aparezcan. No introducir hoy una capa de puertos para
SQLite ni reorganizar todo en `domain/application/adapters`.

**Beneficios que sí se obtienen pronto**
- Sustituir `HTTPException` por **errores de dominio** traducidos en el adaptador HTTP: desacopla el núcleo y habilita CLI/mensajería sin duplicar reglas.
- Un puerto `PaymentGateway`/`ShippingQuoter`/`Notifier` con un adaptador falso: pruebas deterministas y cambio de proveedor sin tocar `confirm`.
- Separar *schema HTTP* de *comando de aplicación* solo en el slice donde exista un segundo adaptador conductor.

**Costos que se evitan por no adoptarla completa**
- Indirección y archivos extra para un sistema de ~400 líneas con una sola base de datos (puertos de un solo implementador = ceremonia).
- Duplicación de puertos por caso de uso y mapeos DTO↔dominio↔fila (los costos que los propios autores de *HexaVerticalSlice* reconocen).
- Riesgo de «arquitectura aspiracional»: Bogard advierte que el enfoque asume un equipo que detecta smells y refactoriza; sin esa disciplina, la mezcla empeora en vez de mejorar.

**Qué es beneficio de la arquitectura y qué es solo refactorización.** Se resuelven con **refactorización sencilla** (sin VHS) la
duplicación de precios, el SQL disperso, los literales mágicos y la inyección de dependencias (ya hecho) y también el reemplazo de
`HTTPException` por errores de dominio, que es un cambio local de unas decenas de líneas. **Solo la arquitectura** aporta valor
cuando hay *varios equipos*, *varios adaptadores por puerto* e *integraciones con ciclos de vida distintos*: límites explícitos entre
slices, adaptadores intercambiables y pruebas de núcleo sin infraestructura. **Criterio de decisión:** pasar de «parcial» a
«completa» cuando se cumplan dos de estas señales: más de un equipo toca `orders`, existe una segunda puerta de entrada real (CLI o
mensajería), o hay más de una implementación de pago/transporte en producción. Mientras no, el costo supera el beneficio.

## 5. Fuentes consultadas

Leídas directamente (verificadas):
- Jimmy Bogard, [*Vertical Slice Architecture*](https://www.jimmybogard.com/vertical-slice-architecture/) (19 abr. 2018).
- Robert C. Martin, [*Screaming Architecture*](https://blog.cleancoder.com/uncle-bob/2011/09/30/Screaming-Architecture.html), The Clean Code Blog (30 sep. 2011).
- Repositorio `pierregillon/HexaVerticalSlice` en GitHub: [*HexaVerticalSlice*](https://github.com/pierregillon/HexaVerticalSlice) (ejemplo C#/.NET que combina Hexagonal y Vertical Slice; fuente de las ventajas/desventajas citadas).

Consultadas solo como resultados de búsqueda (secundarias, no verificadas a fondo):
- Alistair Cockburn, [*Hexagonal Architecture*](https://alistair.cockburn.us/hexagonal-architecture/) (2005): es la fuente original, pero **su página no cargó** (verificación anti-bot), así que su contenido lo tomo de resúmenes:
  [keynote de Cockburn en SAG 2025 (iSAQB)](https://conferences.isaqb.org/software-architecture-gathering/sag-2025-keynote-alistair-cockburn-the-hexagonal-or-ports-adapters-architecture/),
  [*Ports & Adapters Architecture* (Software Architecture Chronicles)](https://medium.com/the-software-architecture-chronicles/ports-adapters-architecture-d19f2d476eca),
  [Hexagonal architecture (Wikipedia)](https://en.wikipedia.org/wiki/Hexagonal_architecture_(software)).
- Robert C. Martin, *Clean Architecture* (2017), cap. 21 «Screaming Architecture» ([O'Reilly](https://www.oreilly.com/library/view/clean-architecture-a/9780134494272/ch21.xhtml)).
- Comparativas y artículos de apoyo: [*Vertical slice architecture vs Screaming architecture*](https://medium.com/@bsvrj9320/vertical-slice-architecture-vs-screaming-architecture-366da5bfae48), [TalkingBit: Vertical Slice Architecture](https://franiglesias.github.io/vertical-slice-architecture/), [Baeldung: Vertical Slice Architecture](https://www.baeldung.com/java-vertical-slice-architecture), [Vertical Slice vs Clean Architecture (nadirbad.dev)](https://nadirbad.dev/vertical-slice-vs-clean-architecture).

**Limitación de la investigación:** no hallé una fuente primaria que defina «VHS» como patrón unificado; la evaluación se apoya en las
tres fuentes de cada componente y en un ejemplo de combinación. Las afirmaciones sobre Cockburn provienen de fuentes secundarias.
