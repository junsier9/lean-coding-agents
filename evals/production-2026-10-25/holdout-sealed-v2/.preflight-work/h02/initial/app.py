from domain import parse_cart, validate_cart

def _price(cart):
    return cart.unit_cents * cart.quantity * (100 - cart.discount) // 100

def quote_web(payload, parser=parse_cart):
    """The parser owns runtime validation on this route."""
    return _price(parser(payload))

def quote_partner(cart):
    """Partners can construct Cart objects outside our parser."""
    return _price(cart)
