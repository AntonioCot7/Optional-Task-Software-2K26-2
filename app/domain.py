"""Vocabulario del dominio: sustituye las cadenas sueltas ("premium", "pending", ...) por tipos."""
from enum import StrEnum


class CustomerCategory(StrEnum):
    REGULAR = "regular"
    PREMIUM = "premium"
    BUSINESS = "business"


class ShippingMethod(StrEnum):
    PICKUP = "pickup"
    STANDARD = "standard"
    EXPRESS = "express"


class OrderStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
