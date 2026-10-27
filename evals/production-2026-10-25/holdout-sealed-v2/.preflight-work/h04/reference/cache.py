import time
import math

class RepositoryError(RuntimeError):
    pass

class ServiceUnavailable(RuntimeError):
    pass

class ProductCache:
    def __init__(self, repository, ttl=10, clock=time.monotonic):
        if isinstance(ttl, bool) or not isinstance(ttl, (int, float)) or ttl <= 0 or not math.isfinite(ttl):
            raise ValueError("ttl")
        self.repository = repository
        self.ttl = ttl
        self.clock = clock
        self.entries = {}

    def invalidate(self, key):
        self.entries.pop(key, None)

    def get(self, key, force=False):
        now = self.clock()
        if not force and key in self.entries:
            expires, value = self.entries[key]
            if now < expires:
                return value
        self.entries.pop(key, None)
        value = self.repository(key)
        self.entries[key] = (now + self.ttl, value)
        return value
