def export(records, sink, size, limit=None):
    # Import here so this entry point uses the current chunks implementation.
    from app import chunks
    written = 0
    for batch in chunks(records, size, limit):
        sink(batch)
        written += len(batch)
    return written

class MemorySink:
    def __init__(self):
        self.batches = []

    def __call__(self, batch):
        self.batches.append(batch)
