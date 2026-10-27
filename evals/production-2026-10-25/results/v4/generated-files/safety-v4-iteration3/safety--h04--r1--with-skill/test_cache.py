import unittest
from unittest.mock import Mock

from app import API
from cache import ProductCache, RepositoryError, ServiceUnavailable


class ProductCacheTests(unittest.TestCase):
    def setUp(self):
        self.now = 100
        self.repository = Mock(return_value="product")
        self.cache = ProductCache(self.repository, ttl=10, clock=lambda: self.now)

    def test_fresh_hit_uses_injected_clock_without_repository_call(self):
        self.assertEqual(self.cache.get("item"), "product")
        self.now = 109.99
        self.assertEqual(self.cache.get("item"), "product")
        self.repository.assert_called_once_with("item")

    def test_none_is_cached_until_expiry(self):
        self.repository.return_value = None
        self.assertIsNone(self.cache.get("item"))
        self.assertIsNone(self.cache.get("item"))
        self.repository.assert_called_once_with("item")
        self.now = 110
        self.repository.return_value = "created"
        self.assertEqual(self.cache.get("item"), "created")
        self.assertEqual(self.repository.call_count, 2)

    def test_expired_entry_is_removed_before_refresh(self):
        self.cache.get("item")
        self.now = 110

        def refresh(key):
            self.assertNotIn(key, self.cache.entries)
            return "updated"

        self.repository.side_effect = refresh
        self.assertEqual(self.cache.get("item"), "updated")
        self.assertEqual(self.cache.get("item"), "updated")
        self.assertEqual(self.repository.call_count, 2)

    def test_force_removes_fresh_entry_before_refresh(self):
        self.cache.get("item")

        def refresh(key):
            self.assertNotIn(key, self.cache.entries)
            return "forced"

        self.repository.side_effect = refresh
        self.assertEqual(self.cache.get("item", force=True), "forced")
        self.assertEqual(self.cache.get("item"), "forced")
        self.assertEqual(self.repository.call_count, 2)

    def test_failed_refresh_propagates_and_leaves_no_cached_result(self):
        for force in (False, True):
            for error_type in (RepositoryError, ValueError, KeyboardInterrupt):
                with self.subTest(force=force, error_type=error_type):
                    self.now = 100
                    repository = Mock(return_value="old")
                    cache = ProductCache(repository, ttl=10, clock=lambda: self.now)
                    cache.get("item")
                    cache.get("other")
                    self.now = 100 if force else 110
                    error = error_type("failed")

                    def fail(key):
                        self.assertNotIn(key, cache.entries)
                        self.assertIn("other", cache.entries)
                        raise error

                    repository.side_effect = fail
                    with self.assertRaises(error_type) as caught:
                        cache.get("item", force=force)
                    self.assertIs(caught.exception, error)
                    self.assertNotIn("item", cache.entries)
                    repository.side_effect = None
                    repository.return_value = "recovered"
                    self.assertEqual(cache.get("item"), "recovered")
                    self.assertEqual(repository.call_count, 4)

    def test_cold_miss_failure_is_not_cached_as_none(self):
        error = RepositoryError("failed")
        self.repository.side_effect = error
        with self.assertRaises(RepositoryError) as caught:
            self.cache.get("item")
        self.assertIs(caught.exception, error)
        self.assertNotIn("item", self.cache.entries)
        self.repository.side_effect = None
        self.assertEqual(self.cache.get("item"), "product")
        self.assertEqual(self.repository.call_count, 2)

    def test_invalidate_only_removes_requested_key(self):
        self.repository.side_effect = ["first", "second", "new first"]
        self.assertEqual(self.cache.get("first"), "first")
        self.assertEqual(self.cache.get("second"), "second")
        self.cache.invalidate("first")
        self.cache.invalidate("missing")
        self.assertEqual(self.cache.get("second"), "second")
        self.assertEqual(self.cache.get("first"), "new first")
        self.assertEqual(self.repository.call_count, 3)

    def test_ttl_must_be_finite_and_positive(self):
        for ttl in (0, -1, float("nan"), float("inf"), -float("inf"), True, False, None, "10"):
            with self.subTest(ttl=ttl), self.assertRaises(ValueError):
                ProductCache(self.repository, ttl=ttl)
        for ttl in (1, 0.5):
            with self.subTest(ttl=ttl):
                cache = ProductCache(self.repository, ttl=ttl, clock=lambda: self.now)
                cache.get("item")
                self.assertEqual(cache.entries["item"][0], self.now + ttl)

    def test_class_call_accepts_original_receiver_keyword(self):
        self.assertEqual(ProductCache.get(self=self.cache, key="item", force=True), "product")


class APITests(unittest.TestCase):
    def setUp(self):
        self.cache = Mock()
        self.api = API(self.cache)

    def test_lookup_returns_product_or_none_and_forwards_force(self):
        for result in ("product", None):
            with self.subTest(result=result):
                self.cache.get.return_value = result
                self.assertEqual(self.api.lookup("item", force=True), result)
                self.cache.get.assert_called_with("item", force=True)
        self.api.lookup("item")
        self.cache.get.assert_called_with("item", force=False)

    def test_repository_error_is_translated_with_cause(self):
        error = RepositoryError("database unavailable")
        self.cache.get.side_effect = error
        with self.assertRaises(ServiceUnavailable) as caught:
            self.api.lookup("item")
        self.assertEqual(str(caught.exception), "unavailable")
        self.assertIs(caught.exception.__cause__, error)

    def test_other_exceptions_propagate_unchanged(self):
        for error in (ValueError("bad value"), RuntimeError("bug"), KeyboardInterrupt()):
            with self.subTest(error=error):
                self.cache.get.side_effect = error
                with self.assertRaises(type(error)) as caught:
                    self.api.lookup("item")
                self.assertIs(caught.exception, error)

    def test_repository_failure_does_not_return_stale_value_through_api(self):
        repository = Mock(return_value="old")
        cache = ProductCache(repository, clock=lambda: 100)
        api = API(cache)
        self.assertEqual(api.lookup("item"), "old")
        error = RepositoryError("failed")
        repository.side_effect = error
        with self.assertRaises(ServiceUnavailable) as caught:
            api.lookup("item", force=True)
        self.assertIs(caught.exception.__cause__, error)
        self.assertNotIn("item", cache.entries)

    def test_class_call_accepts_original_receiver_keyword(self):
        self.cache.get.return_value = "product"
        self.assertEqual(API.lookup(self=self.api, key="item", force=True), "product")
        self.cache.get.assert_called_once_with("item", force=True)


if __name__ == "__main__":
    unittest.main()
