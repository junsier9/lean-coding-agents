from cache import RepositoryError, ServiceUnavailable

class API:
    def __init__(self, cache):
        self.cache = cache

    def lookup(self, key, force=False):
        try:
            return self.cache.get(key, force=force)
        except Exception as exc:
            raise ServiceUnavailable("unavailable") from exc
