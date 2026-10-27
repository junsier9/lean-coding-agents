from domain import parse_cart, validate_cart

def _price(cart):
    return cart.unit_cents * cart.quantity * (100 - cart.discount) // 100

def quote_web(payload, parser=parse_cart):
    """The parser owns runtime validation on this route."""
    return _price(parser(payload))

def quote_partner(cart):
    """The bypass route validates before entering the trusted helper."""
    return _price(validate_cart(cart))
