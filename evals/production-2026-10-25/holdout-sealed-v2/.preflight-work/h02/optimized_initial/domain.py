from dataclasses import dataclass

class CartError(ValueError):
    pass

@dataclass(frozen=True)
class Cart:
    unit_cents: int
    quantity: int
    discount: int = 0

def validate_cart(cart):
    if not isinstance(cart, Cart):
        raise CartError("cart")
    if type(cart.unit_cents) is not int or cart.unit_cents < 0:
        raise CartError("unit price")
    if type(cart.quantity) is not int or cart.quantity <= 0:
        raise CartError("quantity")
    if type(cart.discount) is not int or not 0 <= cart.discount <= 100:
        raise CartError("discount")
    return cart

def parse_cart(payload):
    if not isinstance(payload, dict):
        raise CartError("payload")
    try:
        cart = Cart(payload["unit_cents"], payload["quantity"], payload.get("discount", 0))
    except (KeyError, TypeError) as exc:
        raise CartError("fields") from exc
    return validate_cart(cart)
