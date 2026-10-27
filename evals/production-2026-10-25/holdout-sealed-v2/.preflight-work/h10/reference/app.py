from domain import OrderError, Record, Store, validate

def process(store, payload, hook=None):
    validate(payload)
    owned = {"sku": payload["sku"], "quantity": payload["quantity"], "labels": list(payload["labels"])}
    if hook is not None:
        hook(owned)
        validate(owned)
    record = Record(owned["sku"], owned["quantity"], tuple(owned["labels"]))
    return store.save(record)
