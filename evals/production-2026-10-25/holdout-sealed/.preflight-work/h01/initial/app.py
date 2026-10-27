import json
from domain import InventoryError, Manifest, validate_rows

def _render(rows, validator):
    checked = validator(rows)
    return Manifest(checked, sum(row.quantity for row in checked))

def import_inventory(text, validator=validate_rows):
    """Parse external inventory and produce an immutable manifest."""
    try:
        payload = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise InventoryError("invalid JSON") from exc
    checked = validator(payload)
    return _render(checked, validator)
