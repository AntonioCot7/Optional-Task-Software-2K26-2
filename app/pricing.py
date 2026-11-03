"""Reglas de precio de un pedido. Funciones puras: no tocan la base de datos ni HTTP.

Todos los importes son centavos enteros. Es el único lugar donde viven las reglas de descuento
y envío; antes estaban copiadas en ``OrderService.save`` y ``OrderService.quote``.
"""
from dataclasses import dataclass

from app.domain import CustomerCategory, ShippingMethod

DISCOUNT_PERCENT_BY_CATEGORY = {
    CustomerCategory.REGULAR: 0,
    CustomerCategory.PREMIUM: 10,
    CustomerCategory.BUSINESS: 15,
}
STANDARD_SHIPPING_CENTS = 1_000
FREE_STANDARD_SHIPPING_FROM_CENTS = 10_000
EXPRESS_SHIPPING_CENTS = 2_500


@dataclass(frozen=True)
class PriceBreakdown:
    subtotal_cents: int
    discount_cents: int
    shipping_cents: int

    @property
    def total_cents(self) -> int:
        return self.subtotal_cents - self.discount_cents + self.shipping_cents

    def as_quote(self) -> dict:
        return {"subtotal_cents": self.subtotal_cents, "discount_cents": self.discount_cents,
                "shipping_cents": self.shipping_cents, "total_cents": self.total_cents}


def calculate_discount(category: str, subtotal_cents: int) -> int:
    """Descuento por categoría sobre el subtotal, truncado a centavos enteros."""
    return subtotal_cents * DISCOUNT_PERCENT_BY_CATEGORY.get(category, 0) // 100


def calculate_shipping(method: str, subtotal_cents: int) -> int:
    """Costo de envío. El umbral de envío gratis usa el subtotal anterior al descuento."""
    if method == ShippingMethod.PICKUP:
        return 0
    if method == ShippingMethod.STANDARD:
        return 0 if subtotal_cents >= FREE_STANDARD_SHIPPING_FROM_CENTS else STANDARD_SHIPPING_CENTS
    return EXPRESS_SHIPPING_CENTS


def price_order(category: str, method: str, subtotal_cents: int) -> PriceBreakdown:
    return PriceBreakdown(
        subtotal_cents=subtotal_cents,
        discount_cents=calculate_discount(category, subtotal_cents),
        shipping_cents=calculate_shipping(method, subtotal_cents),
    )
