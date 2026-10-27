from exporter import export

def chunks(records, size, limit=None):
    """Batch an export source, optionally limiting the number of batches.

    Each batch is immutable and records keep their original order.
    Consumers may stop after any yielded batch.
    A source can be a one-shot iterator with observable side effects.
    Errors belong to the stage which encounters them.
    """
    if type(size) is not int:
        raise TypeError("size")
    if size <= 0:
        raise ValueError("size")
    if limit is not None and type(limit) is not int:
        raise TypeError("limit")
    if limit is not None and limit < 0:
        raise ValueError("limit")
    data = list(records)
    count = 0
    for offset in range(0, len(data), size):
        if limit is not None and count >= limit:
            return
        yield tuple(data[offset:offset + size])
        count += 1
