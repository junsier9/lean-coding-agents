import math
import unittest
from unittest.mock import Mock, call

from app import API
from cache import ProductCache, RepositoryError, ServiceUnavailable


class ProductCacheTests(unittest.TestCase):
    def setUp(self):
        self.now = 100
        self.repository = Mock(return_value="product")
        self.cache = ProductCache(self.repository, ttl=10, clock=lambda: self.now)

    def test_fresh_hit_does_not_query_repository(self):
        product = object()
        self.repository.return_value = product
        self.assertIs(self.cache.get("a"), product)
        self.now = 109
        self.assertIs(self.cache.get("a"), product)
        self.repository.assert_called_once_with("a")

    def test_none_is_cached_until_expiry(self):
        self.repository.return_value = None
        self.assertIsNone(self.cache.get("a"))
        self.now = 109
        self.assertIsNone(self.cache.get("a"))
        self.repository.assert_called_once_with("a")
        self.repository.return_value = "created"
        self.now = 110
        self.assertEqual(self.cache.get("a"), "created")
        self.assertEqual(self.repository.call_count, 2)

    def test_expiry_refreshes_and_renews_ttl(self):
        self.assertEqual(self.cache.get("a"), "product")
        self.repository.return_value = "updated"
        self.now = 110
        self.assertEqual(self.cache.get("a"), "updated")
        self.now = 119
        self.assertEqual(self.cache.get("a"), "updated")
        self.assertEqual(self.repository.call_count, 2)
        self.now = 120
        self.cache.get("a")
        self.assertEqual(self.repository.call_count, 3)

    def test_force_refreshes_fresh_entry_and_caches_result(self):
        self.cache.get("a")
        self.repository.return_value = None
        self.now = 101
        self.assertIsNone(self.cache.get("a", force=True))
        self.assertIsNone(self.cache.get("a"))
        self.assertEqual(self.repository.call_count, 2)

    def test_refresh_evicts_before_repository_call(self):
        for force in (False, True):
            with self.subTest(force=force):
                repository = Mock(return_value="old")
                cache = ProductCache(repository, ttl=10, clock=lambda: self.now)
                cache.get("a")
                if not force:
                    self.now += 10

                def refresh(key):
                    self.assertNotIn(key, cache.entries)
                    return "new"

                repository.side_effect = refresh
                self.assertEqual(cache.get("a", force=force), "new")
                self.assertEqual(cache.get("a"), "new")
                self.assertEqual(repository.call_count, 2)

    def test_refresh_failures_propagate_and_next_call_retries(self):
        for force in (False, True):
            for old_value in ("old", None):
                for error_type in (RepositoryError, ValueError):
                    with self.subTest(force=force, old_value=old_value, error=error_type):
                        repository = Mock(return_value=old_value)
                        cache = ProductCache(repository, ttl=10, clock=lambda: self.now)
                        cache.get("a")
                        if not force:
                            self.now += 10
                        error = error_type("refresh failed")
                        repository.side_effect = error
                        with self.assertRaises(error_type) as caught:
                            cache.get("a", force=force)
                        self.assertIs(caught.exception, error)
                        repository.side_effect = None
                        repository.return_value = "recovered"
                        self.assertEqual(cache.get("a"), "recovered")
                        self.assertEqual(repository.call_count, 3)

    def test_cold_failure_is_not_cached_as_none(self):
        error = RepositoryError("offline")
        self.repository.side_effect = error
        with self.assertRaises(RepositoryError) as caught:
            self.cache.get("a")
        self.assertIs(caught.exception, error)
        self.repository.side_effect = None
        self.assertEqual(self.cache.get("a"), "product")
        self.assertEqual(self.repository.call_args_list, [call("a"), call("a")])

    def test_invalidate_is_idempotent_and_isolates_keys(self):
        self.repository.side_effect = lambda key: object()
        first = self.cache.get("a")
        second = self.cache.get("b")
        self.cache.invalidate("a")
        self.cache.invalidate("a")
        self.cache.invalidate("missing")
        self.assertIs(self.cache.get("b"), second)
        self.assertIsNot(self.cache.get("a"), first)
        self.assertEqual(self.repository.call_count, 3)

    def test_failed_forced_refresh_does_not_affect_other_keys(self):
        self.cache.get("a")
        second = self.cache.get("b")
        self.repository.side_effect = RepositoryError("offline")
        with self.assertRaises(RepositoryError):
            self.cache.get("a", force=True)
        self.assertIs(self.cache.get("b"), second)
        self.assertEqual(self.repository.call_count, 3)

    def test_ttl_requires_finite_positive_number(self):
        for ttl in (True, False, 0, -1, math.inf, -math.inf, math.nan, None, "10"):
            with self.subTest(ttl=ttl):
                with self.assertRaises(ValueError):
                    ProductCache(self.repository, ttl=ttl)

    def test_fractional_ttl_uses_injected_clock(self):
        cache = ProductCache(self.repository, ttl=0.5, clock=lambda: self.now)
        cache.get("a")
        self.now += 0.25
        cache.get("a")
        self.repository.assert_called_once_with("a")
        self.now += 0.25
        cache.get("a")
        self.assertEqual(self.repository.call_count, 2)


class APITests(unittest.TestCase):
    def test_class_calls_keep_receiver_keyword_and_positional_force(self):
        repository = Mock(return_value="product")
        cache = ProductCache(repository)
        api = API(cache)
        self.assertEqual(ProductCache.get(self=cache, key="a"), "product")
        self.assertEqual(API.lookup(self=api, key="a"), "product")
        repository.assert_called_once_with("a")
        self.assertEqual(ProductCache.get(cache, "a", True), "product")
        self.assertEqual(API.lookup(api, "a", True), "product")
        self.assertEqual(repository.call_count, 3)

    def test_lookup_forwards_force_and_returns_cached_none(self):
        repository = Mock(return_value=None)
        api = API(ProductCache(repository))
        self.assertIsNone(api.lookup("a", force=True))
        self.assertIsNone(api.lookup("a"))
        self.assertEqual(repository.call_count, 1)

    def test_repository_error_becomes_service_unavailable_with_cause(self):
        error = RepositoryError("offline")
        repository = Mock(side_effect=error)
        api = API(ProductCache(repository))
        with self.assertRaises(ServiceUnavailable) as caught:
            api.lookup("a")
        self.assertEqual(str(caught.exception), "unavailable")
        self.assertIs(caught.exception.__cause__, error)

    def test_unexpected_exception_propagates_unchanged(self):
        error = ValueError("bad product")
        repository = Mock(side_effect=error)
        api = API(ProductCache(repository))
        with self.assertRaises(ValueError) as caught:
            api.lookup("a")
        self.assertIs(caught.exception, error)

    def test_forced_refresh_error_does_not_expose_old_value(self):
        repository = Mock(return_value="old")
        api = API(ProductCache(repository))
        self.assertEqual(api.lookup("a"), "old")
        error = RepositoryError("offline")
        repository.side_effect = error
        with self.assertRaises(ServiceUnavailable) as caught:
            api.lookup("a", force=True)
        self.assertIs(caught.exception.__cause__, error)
        repository.side_effect = None
        repository.return_value = "new"
        self.assertEqual(api.lookup("a"), "new")
        self.assertEqual(repository.call_count, 3)


if __name__ == "__main__":
    unittest.main()
