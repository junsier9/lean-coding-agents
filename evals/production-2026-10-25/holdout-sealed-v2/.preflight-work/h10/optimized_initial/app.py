from domain import OrderError, Record, Store, validate

def process(store, payload, hook=None):
    """Process an external order through an optional mutable plugin.

    Saving is the side effect; earlier failures must not persist a row.
    """
    validate(payload)
    owned = dict(payload)
    if hook is not None:
        hook(owned)
    record = Record(owned["sku"], owned["quantity"], owned["labels"])
    return store.save(record)
