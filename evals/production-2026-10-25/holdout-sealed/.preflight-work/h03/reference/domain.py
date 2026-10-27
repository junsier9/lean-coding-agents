from dataclasses import dataclass

class QuoteError(ValueError):
    pass

@dataclass(frozen=True)
class Line:
    sku: str
    unit_cents: int
    quantity: int

@dataclass(frozen=True)
class Quote:
    lines: tuple
    total: int

def check(cart, catalog):
    if not isinstance(cart, list) or not isinstance(catalog, dict):
        raise QuoteError("container")
    for row in cart:
        if not isinstance(row, dict):
            raise QuoteError("row")
        sku, quantity = row.get("sku"), row.get("quantity")
        if not isinstance(sku, str) or sku not in catalog:
            raise QuoteError("sku")
        if type(quantity) is not int or quantity <= 0:
            raise QuoteError("quantity")
        price = catalog[sku]
        if type(price) is not int or price < 0:
            raise QuoteError("price")
