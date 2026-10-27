import unittest
from unittest.mock import Mock

from app import API
from cache import ProductCache, RepositoryError, ServiceUnavailable


class ProductCacheTests(unittest.TestCase):
    def setUp(self):
        self.clock = Mock(return_value=100)
        self.repository = Mock(return_value="product")
        self.cache = ProductCache(self.repository, ttl=10, clock=self.clock)

    def test_fresh_hit_uses_ttl_seconds_without_repository_call(self):
        self.assertEqual(self.cache.get("a"), "product")
        self.clock.return_value = 109.999
        self.assertEqual(self.cache.get("a"), "product")
        self.repository.assert_called_once_with("a")

    def test_none_is_cached_until_expiry(self):
        self.repository.return_value = None
        self.assertIsNone(self.cache.get("a"))
        self.clock.return_value = 109
        self.assertIsNone(self.cache.get("a"))
        self.repository.assert_called_once_with("a")
        self.clock.return_value = 110
        self.repository.return_value = "created"
        self.assertEqual(self.cache.get("a"), "created")
        self.assertEqual(self.repository.call_count, 2)

    def test_refresh_removes_old_entry_before_repository_call(self):
        for force in (False, True):
            with self.subTest(force=force):
                self.setUp()
                self.cache.get("a")
                self.clock.return_value = 105 if force else 110

                def refresh(key):
                    self.assertNotIn(key, self.cache.entries)
                    return "updated"

                self.repository.side_effect = refresh
                self.assertEqual(self.cache.get("a", force=force), "updated")
                self.clock.return_value += 9
                self.assertEqual(self.cache.get("a"), "updated")
                self.assertEqual(self.repository.call_count, 2)

    def test_failed_fetch_propagates_and_leaves_no_cached_result(self):
        for state in ("missing", "expired", "forced", "cached_none"):
            for error_type in (RepositoryError, ValueError, KeyboardInterrupt):
                with self.subTest(state=state, error_type=error_type):
                    self.setUp()
                    if state != "missing":
                        if state == "cached_none":
                            self.repository.return_value = None
                        self.cache.get("a")
                    self.clock.return_value = 110 if state == "expired" else 105
                    error = error_type("failed")

                    def fail(key):
                        self.assertNotIn(key, self.cache.entries)
                        raise error

                    self.repository.side_effect = fail
                    with self.assertRaises(error_type) as raised:
                        self.cache.get("a", force=state in ("forced", "cached_none"))
                    self.assertIs(raised.exception, error)
                    self.assertNotIn("a", self.cache.entries)
                    self.repository.side_effect = None
                    self.repository.return_value = "recovered"
                    self.assertEqual(self.cache.get("a"), "recovered")

    def test_keys_and_invalidation_are_isolated(self):
        self.repository.side_effect = lambda key: "product-" + key
        self.assertEqual(self.cache.get("a"), "product-a")
        self.assertEqual(self.cache.get("b"), "product-b")
        self.repository.side_effect = RepositoryError("failed")
        with self.assertRaises(RepositoryError):
            self.cache.get("a", force=True)
        self.assertEqual(self.cache.get("b"), "product-b")
        self.assertEqual(self.repository.call_count, 3)
        self.cache.invalidate("a")
        self.cache.invalidate("b")
        self.repository.side_effect = None
        self.repository.return_value = "new-b"
        self.assertEqual(self.cache.get("b"), "new-b")
        self.assertEqual(self.repository.call_count, 4)

    def test_ttl_requires_finite_positive_number(self):
        for ttl in (0, -1, float("inf"), -float("inf"), float("nan"), True, False, None, "10"):
            with self.subTest(ttl=ttl):
                with self.assertRaises(ValueError):
                    ProductCache(self.repository, ttl=ttl, clock=self.clock)
        for ttl in (1, 0.5):
            with self.subTest(ttl=ttl):
                cache = ProductCache(self.repository, ttl=ttl, clock=self.clock)
                cache.get("a")
                self.clock.return_value += ttl
                cache.get("a")
                self.assertEqual(self.repository.call_count, 2)
                self.repository.reset_mock()


class APITests(unittest.TestCase):
    def test_lookup_preserves_result_and_forwards_force(self):
        for value in ("product", None):
            with self.subTest(value=value):
                cache = Mock()
                cache.get.return_value = value
                self.assertEqual(API(cache).lookup("a", force=True), value)
                cache.get.assert_called_once_with("a", force=True)

    def test_repository_error_is_translated_with_cause(self):
        error = RepositoryError("database unavailable")
        cache = ProductCache(Mock(side_effect=error))
        with self.assertRaises(ServiceUnavailable) as raised:
            API(cache).lookup("a")
        self.assertEqual(str(raised.exception), "unavailable")
        self.assertIs(raised.exception.__cause__, error)
        self.assertNotIn("a", cache.entries)

    def test_other_errors_propagate_unchanged(self):
        for error in (ValueError("invalid"), RuntimeError("unexpected"), KeyboardInterrupt()):
            with self.subTest(error=error):
                cache = ProductCache(Mock(side_effect=error))
                with self.assertRaises(type(error)) as raised:
                    API(cache).lookup("a")
                self.assertIs(raised.exception, error)
                self.assertNotIn("a", cache.entries)


if __name__ == "__main__":
    unittest.main()
