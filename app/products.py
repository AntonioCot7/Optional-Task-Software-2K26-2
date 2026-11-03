import sqlite3

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from app.dependencies import DatabaseDep, conflict_on_integrity_error

PRODUCT_NOT_FOUND = "Producto no encontrado"


class ProductInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=120)
    price_cents: int = Field(gt=0, le=100_000_000, strict=True)
    stock: int = Field(ge=0, le=1_000_000, strict=True)


class ProductRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def create(self, data: ProductInput) -> dict:
        cursor = self.connection.execute(
            "INSERT INTO products(name, price_cents, stock) VALUES (?, ?, ?)",
            (data.name, data.price_cents, data.stock),
        )
        return self.find(cursor.lastrowid)

    def list_all(self) -> list[dict]:
        return [dict(row) for row in self.connection.execute("SELECT * FROM products ORDER BY id")]

    def find(self, product_id) -> dict | None:
        row = self.connection.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        return None if row is None else dict(row)

    def update(self, product_id, data: ProductInput) -> dict | None:
        cursor = self.connection.execute(
            "UPDATE products SET name = ?, price_cents = ?, stock = ? WHERE id = ?",
            (data.name, data.price_cents, data.stock, product_id),
        )
        return self.find(product_id) if cursor.rowcount else None

    def delete(self, product_id) -> bool:
        return self.connection.execute("DELETE FROM products WHERE id = ?", (product_id,)).rowcount > 0


router = APIRouter(prefix="/products", tags=["Products"])


@router.post("", status_code=201)
def create_product(data: ProductInput, database: DatabaseDep):
    with database.connect() as connection:
        return ProductRepository(connection).create(data)


@router.get("")
def list_products(database: DatabaseDep):
    with database.connect() as connection:
        return ProductRepository(connection).list_all()


@router.get("/{product_id}")
def get_product(product_id: int, database: DatabaseDep):
    with database.connect() as connection:
        product = ProductRepository(connection).find(product_id)
    if product is None:
        raise HTTPException(404, PRODUCT_NOT_FOUND)
    return product


@router.put("/{product_id}")
def update_product(product_id: int, data: ProductInput, database: DatabaseDep):
    with database.connect() as connection:
        product = ProductRepository(connection).update(product_id, data)
    if product is None:
        raise HTTPException(404, PRODUCT_NOT_FOUND)
    return product


@router.delete("/{product_id}", status_code=204)
def delete_product(product_id: int, database: DatabaseDep):
    with conflict_on_integrity_error("El producto pertenece a un pedido"), database.connect() as connection:
        deleted = ProductRepository(connection).delete(product_id)
    if not deleted:
        raise HTTPException(404, PRODUCT_NOT_FOUND)
    return Response(status_code=204)
