from domain import Line, Quote, QuoteError, check

class LiveQuote:
    """Legacy view; callers currently keep this for delayed settlement."""
    def __init__(self, cart, catalog):
        self.cart = cart
        self.catalog = catalog

    @property
    def lines(self):
        return tuple(Line(row["sku"], self.catalog[row["sku"]], row["quantity"]) for row in self.cart)

    @property
    def total(self):
        return sum(line.unit_cents * line.quantity for line in self.lines)

def prepare_quote(cart, catalog):
    check(cart, catalog)
    return LiveQuote(cart, catalog)
