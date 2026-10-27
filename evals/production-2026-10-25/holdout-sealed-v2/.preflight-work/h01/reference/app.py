import json
from domain import InventoryError, Manifest, validate_rows

def _render(rows):
    return Manifest(rows, sum(row.quantity for row in rows))

def import_inventory(text, validator=validate_rows):
    """Validate the external batch once; own the resulting rows."""
    try:
        payload = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise InventoryError("invalid JSON") from exc
    return _render(validator(payload))
