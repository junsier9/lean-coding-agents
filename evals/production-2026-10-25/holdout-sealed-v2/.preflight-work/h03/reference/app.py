from domain import Line, Quote, QuoteError, check

def prepare_quote(cart, catalog):
    check(cart, catalog)
    lines = tuple(Line(row["sku"], catalog[row["sku"]], row["quantity"]) for row in cart)
    return Quote(lines, sum(line.unit_cents * line.quantity for line in lines))
