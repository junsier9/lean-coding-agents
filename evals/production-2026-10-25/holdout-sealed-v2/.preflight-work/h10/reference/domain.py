from dataclasses import dataclass

class OrderError(ValueError):
    pass

@dataclass(frozen=True)
class Record:
    sku: str
    quantity: int
    labels: tuple

def validate(payload):
    if not isinstance(payload, dict):
        raise OrderError("payload")
    sku, quantity, labels = payload.get("sku"), payload.get("quantity"), payload.get("labels")
    if not isinstance(sku, str) or not sku.strip():
        raise OrderError("sku")
    if type(quantity) is not int or quantity <= 0:
        raise OrderError("quantity")
    if not isinstance(labels, list) or any(not isinstance(item, str) for item in labels):
        raise OrderError("labels")

class Store:
    def __init__(self):
        self.rows = []

    def save(self, record):
        self.rows.append(record)
        return record
