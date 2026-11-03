"""Acceso a datos de pedidos. Trabaja sobre una conexión ya abierta: la transacción la controla el servicio."""
import sqlite3

from app.domain import OrderStatus
from app.pricing import PriceBreakdown

ITEM_COLUMNS = "product_id, quantity, unit_price_cents"


class OrderRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    # ---- lecturas
    def find(self, order_id):
        return self.connection.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()

    def find_detail(self, order_id):
        """Pedido con sus líneas, o None si no existe."""
        order = self.find(order_id)
        if order is None:
            return None
        detail = dict(order)
        detail["items"] = [dict(row) for row in self.connection.execute(
            f"SELECT {ITEM_COLUMNS} FROM order_items WHERE order_id = ? ORDER BY id", (order_id,),
        )]
        return detail

    def list_all(self):
        return [dict(row) for row in self.connection.execute("SELECT * FROM orders ORDER BY id")]

    def find_items(self, order_id):
        return self.connection.execute("SELECT * FROM order_items WHERE order_id = ?", (order_id,)).fetchall()

    def find_customer(self, customer_id):
        return self.connection.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()

    def find_product(self, product_id):
        return self.connection.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()

    def notifications(self, order_id):
        return [dict(row) for row in self.connection.execute(
            "SELECT id, order_id, recipient, message FROM notifications WHERE order_id = ? ORDER BY id", (order_id,),
        )]

    # ---- escrituras
    def insert(self, data, customer_category: str, price: PriceBreakdown) -> int:
        cursor = self.connection.execute(
            """INSERT INTO orders(customer_id, customer_category, address, city, postal_code,
               shipping, subtotal_cents, discount_cents, shipping_cents, total_cents)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            self._values(data, customer_category, price),
        )
        return cursor.lastrowid

    def update(self, order_id, data, customer_category: str, price: PriceBreakdown) -> None:
        self.connection.execute(
            """UPDATE orders SET customer_id = ?, customer_category = ?, address = ?, city = ?,
               postal_code = ?, shipping = ?, subtotal_cents = ?, discount_cents = ?,
               shipping_cents = ?, total_cents = ? WHERE id = ?""",
            (*self._values(data, customer_category, price), order_id),
        )

    def replace_items(self, order_id, lines) -> None:
        """lines: iterable de (product_id, quantity, unit_price_cents)."""
        self.connection.execute("DELETE FROM order_items WHERE order_id = ?", (order_id,))
        self.connection.executemany(
            "INSERT INTO order_items(order_id, product_id, quantity, unit_price_cents) VALUES (?, ?, ?, ?)",
            [(order_id, *line) for line in lines],
        )

    def set_status(self, order_id, status: OrderStatus) -> None:
        self.connection.execute("UPDATE orders SET status = ? WHERE id = ?", (status.value, order_id))

    def delete(self, order_id) -> None:
        self.connection.execute("DELETE FROM orders WHERE id = ?", (order_id,))

    def deduct_stock(self, product_id, quantity) -> bool:
        """Descuenta existencias solo si alcanzan. Devuelve False cuando no hay stock suficiente."""
        cursor = self.connection.execute(
            "UPDATE products SET stock = stock - ? WHERE id = ? AND stock >= ?",
            (quantity, product_id, quantity),
        )
        return cursor.rowcount > 0

    @staticmethod
    def _values(data, customer_category, price):
        return (data.customer_id, customer_category, data.address, data.city, data.postal_code,
                data.shipping, price.subtotal_cents, price.discount_cents, price.shipping_cents,
                price.total_cents)
