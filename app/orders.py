import sqlite3
import time
from typing import Annotated, Literal, NamedTuple

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain import OrderStatus, ShippingMethod
from app.notification_queue import NotificationQueue
from app.order_repository import OrderRepository
from app.pricing import PriceBreakdown, price_order


class ItemInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: int = Field(gt=0, strict=True)
    quantity: int = Field(gt=0, le=10_000, strict=True)


class OrderInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    customer_id: int = Field(gt=0, strict=True)
    address: str = Field(min_length=1, max_length=200)
    city: str = Field(min_length=1, max_length=100)
    postal_code: str = Field(min_length=1, max_length=20)
    shipping: Literal[ShippingMethod.PICKUP, ShippingMethod.STANDARD, ShippingMethod.EXPRESS] = (
        ShippingMethod.STANDARD
    )
    items: list[ItemInput] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_products(self):
        if len({item.product_id for item in self.items}) != len(self.items):
            raise ValueError("No repita productos; indique la cantidad en una sola línea")
        return self


class PricedOrder(NamedTuple):
    """Resultado de validar y valorar un pedido: cliente, líneas (product_id, cantidad, precio) y precio."""
    customer: sqlite3.Row
    lines: tuple
    price: PriceBreakdown


class OrderService:
    """Casos de uso de pedidos. Las reglas de precio viven en app.pricing y el SQL en OrderRepository."""

    def __init__(self, database):
        self.database = database

    def save(self, data: OrderInput, order_id=None):
        with self.database.connect() as connection:
            orders = OrderRepository(connection)
            if order_id is not None:
                self._require_pending(orders, order_id, "Solo puede editar pedidos pendientes")
            priced = self._price(orders, data)
            category = priced.customer["category"]
            if order_id is None:
                order_id = orders.insert(data, category, priced.price)
            else:
                orders.update(order_id, data, category, priced.price)
            orders.replace_items(order_id, priced.lines)
            return orders.find_detail(order_id)

    def quote(self, data: OrderInput):
        with self.database.connect() as connection:
            return self._price(OrderRepository(connection), data).price.as_quote()

    def get(self, order_id):
        with self.database.connect() as connection:
            return self._require_detail(OrderRepository(connection), order_id)

    def list(self):
        with self.database.connect() as connection:
            return OrderRepository(connection).list_all()

    def confirm(self, order_id):
        with self.database.connect() as connection:
            orders = OrderRepository(connection)
            order = self._require_pending(orders, order_id, "El pedido ya no está pendiente")
            for item in orders.find_items(order_id):
                if not orders.deduct_stock(item["product_id"], item["quantity"]):
                    raise HTTPException(409, f"Stock insuficiente para producto {item['product_id']}")
            customer = orders.find_customer(order["customer_id"])
            message = f"Pedido {order_id} confirmado. Total: {order['total_cents']} centavos."
            # Patrón outbox: el mensaje se encola en la misma transacción que el stock y el estado; un worker
            # lo procesa por separado. La dedup_key evita encolar dos veces la misma confirmación.
            NotificationQueue(connection).enqueue(
                order_id, customer["email"], message, f"order-confirmed:{order_id}", time.time(),
            )
            orders.set_status(order_id, OrderStatus.CONFIRMED)
            return {"id": order_id, "status": OrderStatus.CONFIRMED, "message": message}

    def cancel(self, order_id):
        with self.database.connect() as connection:
            orders = OrderRepository(connection)
            self._require_pending(orders, order_id, "Solo puede cancelar pedidos pendientes")
            orders.set_status(order_id, OrderStatus.CANCELLED)
            return {"id": order_id, "status": OrderStatus.CANCELLED}

    def delete(self, order_id):
        with self.database.connect() as connection:
            orders = OrderRepository(connection)
            self._require_pending(orders, order_id, "Solo puede eliminar pedidos pendientes")
            orders.delete(order_id)

    def notifications(self, order_id):
        with self.database.connect() as connection:
            orders = OrderRepository(connection)
            self._require_detail(orders, order_id)
            return orders.notifications(order_id)

    # ---- pasos compartidos
    @staticmethod
    def _require_detail(orders: OrderRepository, order_id):
        detail = orders.find_detail(order_id)
        if detail is None:
            raise HTTPException(404, "Pedido no encontrado")
        return detail

    @staticmethod
    def _require_pending(orders: OrderRepository, order_id, conflict_message):
        order = orders.find(order_id)
        if order is None:
            raise HTTPException(404, "Pedido no encontrado")
        if order["status"] != OrderStatus.PENDING:
            raise HTTPException(409, conflict_message)
        return order

    @staticmethod
    def _price(orders: OrderRepository, data: OrderInput) -> PricedOrder:
        """Valida cliente y productos y calcula el precio con los valores actuales del catálogo."""
        customer = orders.find_customer(data.customer_id)
        if customer is None:
            raise HTTPException(404, "Cliente no encontrado")
        subtotal = 0
        lines = []
        for item in data.items:
            product = orders.find_product(item.product_id)
            if product is None:
                raise HTTPException(404, f"Producto {item.product_id} no encontrado")
            subtotal += product["price_cents"] * item.quantity
            lines.append((item.product_id, item.quantity, product["price_cents"]))
        return PricedOrder(customer, tuple(lines), price_order(customer["category"], data.shipping, subtotal))


def get_order_service(request: Request) -> OrderService:
    return OrderService(request.app.state.database)


OrderServiceDep = Annotated[OrderService, Depends(get_order_service)]

router = APIRouter(prefix="/orders", tags=["Orders"])


@router.post("/quote")
def quote_order(data: OrderInput, service: OrderServiceDep):
    return service.quote(data)


@router.post("", status_code=201)
def create_order(data: OrderInput, service: OrderServiceDep):
    return service.save(data)


@router.get("")
def list_orders(service: OrderServiceDep):
    return service.list()


@router.get("/{order_id}")
def get_order(order_id: int, service: OrderServiceDep):
    return service.get(order_id)


@router.put("/{order_id}")
def update_order(order_id: int, data: OrderInput, service: OrderServiceDep):
    return service.save(data, order_id)


@router.delete("/{order_id}", status_code=204)
def delete_order(order_id: int, service: OrderServiceDep):
    service.delete(order_id)
    return Response(status_code=204)


@router.post("/{order_id}/confirm")
def confirm_order(order_id: int, service: OrderServiceDep):
    return service.confirm(order_id)


@router.post("/{order_id}/cancel")
def cancel_order(order_id: int, service: OrderServiceDep):
    return service.cancel(order_id)


@router.get("/{order_id}/notifications")
def list_notifications(order_id: int, service: OrderServiceDep):
    return service.notifications(order_id)
