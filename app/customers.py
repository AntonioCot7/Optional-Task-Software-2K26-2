import sqlite3
from typing import Literal

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.dependencies import DatabaseDep, conflict_on_integrity_error
from app.domain import CustomerCategory

CUSTOMER_NOT_FOUND = "Cliente no encontrado"
DUPLICATE_EMAIL = "El correo ya está registrado"


class CustomerInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr = Field(max_length=254)
    category: Literal[CustomerCategory.REGULAR, CustomerCategory.PREMIUM, CustomerCategory.BUSINESS] = (
        CustomerCategory.REGULAR
    )


class CustomerRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def create(self, data: CustomerInput) -> dict:
        cursor = self.connection.execute(
            "INSERT INTO customers(name, email, category) VALUES (?, ?, ?)",
            (data.name, str(data.email), data.category),
        )
        return self.find(cursor.lastrowid)

    def list_all(self) -> list[dict]:
        return [dict(row) for row in self.connection.execute("SELECT * FROM customers ORDER BY id")]

    def find(self, customer_id) -> dict | None:
        row = self.connection.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
        return None if row is None else dict(row)

    def update(self, customer_id, data: CustomerInput) -> dict | None:
        cursor = self.connection.execute(
            "UPDATE customers SET name = ?, email = ?, category = ? WHERE id = ?",
            (data.name, str(data.email), data.category, customer_id),
        )
        return self.find(customer_id) if cursor.rowcount else None

    def delete(self, customer_id) -> bool:
        return self.connection.execute("DELETE FROM customers WHERE id = ?", (customer_id,)).rowcount > 0


router = APIRouter(prefix="/customers", tags=["Customers"])


@router.post("", status_code=201)
def create_customer(data: CustomerInput, database: DatabaseDep):
    with conflict_on_integrity_error(DUPLICATE_EMAIL), database.connect() as connection:
        return CustomerRepository(connection).create(data)


@router.get("")
def list_customers(database: DatabaseDep):
    with database.connect() as connection:
        return CustomerRepository(connection).list_all()


@router.get("/{customer_id}")
def get_customer(customer_id: int, database: DatabaseDep):
    with database.connect() as connection:
        customer = CustomerRepository(connection).find(customer_id)
    if customer is None:
        raise HTTPException(404, CUSTOMER_NOT_FOUND)
    return customer


@router.put("/{customer_id}")
def update_customer(customer_id: int, data: CustomerInput, database: DatabaseDep):
    with conflict_on_integrity_error(DUPLICATE_EMAIL), database.connect() as connection:
        customer = CustomerRepository(connection).update(customer_id, data)
    if customer is None:
        raise HTTPException(404, CUSTOMER_NOT_FOUND)
    return customer


@router.delete("/{customer_id}", status_code=204)
def delete_customer(customer_id: int, database: DatabaseDep):
    with conflict_on_integrity_error("El cliente tiene pedidos"), database.connect() as connection:
        deleted = CustomerRepository(connection).delete(customer_id)
    if not deleted:
        raise HTTPException(404, CUSTOMER_NOT_FOUND)
    return Response(status_code=204)
