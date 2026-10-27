from itertools import islice
from exporter import export

def chunks(records, size, limit=None):
    """Consume only the current batch and preserve source exception timing."""
    if type(size) is not int:
        raise TypeError("size")
    if size <= 0:
        raise ValueError("size")
    if limit is not None and type(limit) is not int:
        raise TypeError("limit")
    if limit is not None and limit < 0:
        raise ValueError("limit")
    if limit == 0:
        return
    iterator = iter(records)
    count = 0
    while limit is None or count < limit:
        batch = tuple(islice(iterator, size))
        if not batch:
            return
        yield batch
        count += 1
