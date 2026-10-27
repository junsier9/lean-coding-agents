import unittest
from app import API
from cache import ProductCache, RepositoryError, ServiceUnavailable

class Repository:
    def __init__(self):
        self.calls = []
        self.value = "first"
        self.error = None
    def __call__(self, key):
        self.calls.append(key)
        if self.error:
            raise self.error
        return self.value

class Contract(unittest.TestCase):
    def setUp(self):
        self.now = [0]
        self.repo = Repository()
        self.cache = ProductCache(self.repo, 10, lambda: self.now[0])

    def test_fresh_hit(self):
        self.assertEqual(self.cache.get("A"), "first")
        self.repo.value = "second"
        self.assertEqual(self.cache.get("A"), "first")
        self.assertEqual(self.repo.calls, ["A"])

    def test_expiry(self):
        self.cache.get("A")
        self.now[0] = 10
        self.repo.value = "new"
        self.assertEqual(self.cache.get("A"), "new")

    def test_none_is_normal(self):
        self.repo.value = None
        self.assertIsNone(self.cache.get("A"))
        self.assertIsNone(self.cache.get("A"))
        self.assertEqual(self.repo.calls, ["A"])

    def test_force_failure_evicts_stale(self):
        self.cache.get("A")
        self.repo.error = RepositoryError("down")
        with self.assertRaises(RepositoryError):
            self.cache.get("A", force=True)
        self.assertNotIn("A", self.cache.entries)

    def test_failed_miss_not_cached(self):
        error = RepositoryError("down")
        self.repo.error = error
        with self.assertRaises(RepositoryError) as found:
            self.cache.get("A")
        self.assertIs(found.exception, error)
        self.assertEqual(self.cache.entries, {})

    def test_expected_boundary_translation(self):
        error = RepositoryError("down")
        self.repo.error = error
        with self.assertRaises(ServiceUnavailable) as found:
            API(self.cache).lookup("A")
        self.assertIs(found.exception.__cause__, error)

    def test_unexpected_propagates(self):
        error = ValueError("bug")
        self.repo.error = error
        with self.assertRaises(ValueError) as found:
            API(self.cache).lookup("A")
        self.assertIs(found.exception, error)

    def test_invalidate(self):
        self.cache.get("A")
        self.cache.invalidate("A")
        self.repo.value = "new"
        self.assertEqual(self.cache.get("A"), "new")

    def test_key_isolation(self):
        self.cache.get("A")
        self.repo.value = "B"
        self.assertEqual(self.cache.get("B"), "B")
        self.assertEqual(self.cache.get("A"), "first")

    def test_invalid_ttl(self):
        for value in (0, -1, True, "10", float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ProductCache(self.repo, value)
