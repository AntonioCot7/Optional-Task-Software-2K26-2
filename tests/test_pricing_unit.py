"""Unitarias puras de app.pricing y app.domain: sin base de datos ni HTTP."""
import dataclasses

import pytest

from app.domain import CustomerCategory, OrderStatus, ShippingMethod
from app.pricing import (
    DISCOUNT_PERCENT_BY_CATEGORY,
    PriceBreakdown,
    calculate_discount,
    calculate_shipping,
    price_order,
)


@pytest.mark.parametrize("category, subtotal, expected", [
    (CustomerCategory.REGULAR, 12_345, 0),
    (CustomerCategory.PREMIUM, 10_000, 1_000),
    (CustomerCategory.BUSINESS, 10_000, 1_500),
    (CustomerCategory.PREMIUM, 2_505, 250),     # 250,5 -> 250
    (CustomerCategory.BUSINESS, 2_505, 375),    # 375,75 -> 375
    (CustomerCategory.PREMIUM, 9, 0),
    (CustomerCategory.BUSINESS, 0, 0),
    ("premium", 100, 10),                       # acepta el valor como cadena
    ("categoria-desconocida", 5_000, 0),        # una categoría desconocida no da descuento
])
def test_calculate_discount(category, subtotal, expected):
    assert calculate_discount(category, subtotal) == expected


@pytest.mark.parametrize("method, subtotal, expected", [
    (ShippingMethod.PICKUP, 1, 0),
    (ShippingMethod.PICKUP, 99_999, 0),
    (ShippingMethod.STANDARD, 9_999, 1_000),
    (ShippingMethod.STANDARD, 10_000, 0),
    (ShippingMethod.STANDARD, 10_001, 0),
    (ShippingMethod.EXPRESS, 1, 2_500),
    (ShippingMethod.EXPRESS, 10_000, 2_500),
    ("standard", 100, 1_000),
])
def test_calculate_shipping(method, subtotal, expected):
    assert calculate_shipping(method, subtotal) == expected


def test_price_order_total_and_quote_shape():
    price = price_order(CustomerCategory.PREMIUM, ShippingMethod.STANDARD, 5_000)
    assert price == PriceBreakdown(subtotal_cents=5_000, discount_cents=500, shipping_cents=1_000)
    assert price.total_cents == 5_500
    assert list(price.as_quote()) == ["subtotal_cents", "discount_cents", "shipping_cents", "total_cents"]
    assert price.as_quote()["total_cents"] == 5_500


def test_free_shipping_threshold_ignores_discount():
    price = price_order(CustomerCategory.BUSINESS, ShippingMethod.STANDARD, 10_000)
    assert (price.discount_cents, price.shipping_cents, price.total_cents) == (1_500, 0, 8_500)


def test_price_breakdown_is_immutable():
    price = price_order(CustomerCategory.REGULAR, ShippingMethod.PICKUP, 100)
    with pytest.raises(dataclasses.FrozenInstanceError):
        price.subtotal_cents = 1


def test_every_customer_category_has_a_discount_rule():
    assert set(DISCOUNT_PERCENT_BY_CATEGORY) == set(CustomerCategory)


def test_domain_enums_compare_equal_to_their_stored_strings():
    assert OrderStatus.PENDING == "pending"
    assert [s.value for s in OrderStatus] == ["pending", "confirmed", "cancelled"]
    assert [m.value for m in ShippingMethod] == ["pickup", "standard", "express"]
    assert [c.value for c in CustomerCategory] == ["regular", "premium", "business"]
