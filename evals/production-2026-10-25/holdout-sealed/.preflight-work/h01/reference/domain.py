from dataclasses import dataclass

class InventoryError(ValueError):
    pass

@dataclass(frozen=True)
class Row:
    sku: str
    quantity: int

@dataclass(frozen=True)
class Manifest:
    rows: tuple
    total: int

def validate_rows(payload):
    if not isinstance(payload, (list, tuple)):
        raise InventoryError("expected rows")
    result, seen = [], set()
    for item in payload:
        if isinstance(item, Row):
            sku, quantity = item.sku, item.quantity
        elif isinstance(item, dict):
            sku, quantity = item.get("sku"), item.get("quantity")
        else:
            raise InventoryError("expected row")
        if not isinstance(sku, str) or not sku.strip():
            raise InventoryError("sku")
        if type(quantity) is not int or quantity <= 0:
            raise InventoryError("quantity")
        if sku in seen:
            raise InventoryError("duplicate sku")
        seen.add(sku)
        result.append(Row(sku, quantity))
    return tuple(result)
