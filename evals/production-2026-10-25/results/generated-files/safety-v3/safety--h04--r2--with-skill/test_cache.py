import unittest
from unittest.mock import Mock

from app import API
from cache import ProductCache, RepositoryError, ServiceUnavailable


class ProductCacheTests(unittest.TestCase):
    def setUp(self):
        self.repository = Mock(return_value=object())
        self.clock = Mock(return_value=100.0)
        self.cache = ProductCache(self.repository, ttl=10, clock=self.clock)

    def test_fresh_hit_does_not_query_repository(self):
        value = self.cache.get("product")
        self.clock.return_value = 109.999

        self.assertIs(self.cache.get("product"), value)
        self.repository.assert_called_once_with("product")

    def test_none_is_cached_until_expiry(self):
        self.repository.return_value = None
        self.assertIsNone(self.cache.get("product"))
        self.clock.return_value = 109.999
        self.assertIsNone(self.cache.get("product"))
        self.repository.assert_called_once_with("product")

        self.clock.return_value = 110.0
        value = object()
        self.repository.return_value = value
        self.assertIs(self.cache.get("product"), value)
        self.assertEqual(self.repository.call_count, 2)

    def test_refresh_at_exact_expiry_restarts_ttl(self):
        self.cache.get("product")
        self.clock.return_value = 110.0
        value = object()
        self.repository.return_value = value

        self.assertIs(self.cache.get("product"), value)
        self.clock.return_value = 119.999
        self.assertIs(self.cache.get("product"), value)
        self.assertEqual(self.repository.call_count, 2)
        self.clock.return_value = 120.0
        self.cache.get("product")
        self.assertEqual(self.repository.call_count, 3)

    def test_force_refreshes_fresh_entry_and_restarts_ttl(self):
        self.cache.get("product")
        self.clock.return_value = 105.0
        value = object()
        self.repository.return_value = value

        self.assertIs(self.cache.get("product", force=True), value)
        self.clock.return_value = 114.999
        self.assertIs(self.cache.get("product"), value)
        self.assertEqual(self.repository.call_count, 2)
        self.clock.return_value = 115.0
        self.cache.get("product")
        self.assertEqual(self.repository.call_count, 3)

    def test_refresh_evicts_old_entry_before_call_and_does_not_cache_failure(self):
        for force in (False, True):
            for old_value in (object(), None):
                for error_type in (RepositoryError, ValueError, KeyboardInterrupt):
                    with self.subTest(force=force, old_value=old_value, error=error_type):
                        clock = Mock(return_value=100.0)
                        repository = Mock(return_value=old_value)
                        cache = ProductCache(repository, ttl=10, clock=clock)
                        cache.get("product")
                        clock.return_value = 105.0 if force else 110.0
                        error = error_type("failed")

                        def fail(key):
                            self.assertNotIn(key, cache.entries)
                            raise error

                        repository.side_effect = fail
                        with self.assertRaises(error_type) as raised:
                            cache.get("product", force=force)
                        self.assertIs(raised.exception, error)
                        self.assertNotIn("product", cache.entries)

                        repository.side_effect = None
                        value = object()
                        repository.return_value = value
                        self.assertIs(cache.get("product"), value)
                        self.assertIs(cache.get("product"), value)
                        self.assertEqual(repository.call_count, 3)

    def test_missing_key_failure_propagates_and_next_call_retries(self):
        error = RepositoryError("failed")
        self.repository.side_effect = error
        with self.assertRaises(RepositoryError) as raised:
            self.cache.get("product")
        self.assertIs(raised.exception, error)
        self.assertNotIn("product", self.cache.entries)

        self.repository.side_effect = None
        value = self.repository.return_value
        self.assertIs(self.cache.get("product"), value)
        self.assertEqual(self.repository.call_count, 2)

    def test_invalidate_is_idempotent_and_keys_are_isolated(self):
        first, second, replacement = object(), object(), object()
        self.repository.side_effect = [first, second, replacement]
        self.assertIs(self.cache.get("first"), first)
        self.assertIs(self.cache.get("second"), second)

        self.cache.invalidate("missing")
        self.cache.invalidate("first")
        self.cache.invalidate("first")
        self.assertIs(self.cache.get("second"), second)
        self.assertIs(self.cache.get("first"), replacement)
        self.assertEqual(self.repository.call_count, 3)

    def test_failed_refresh_does_not_evict_other_keys(self):
        self.cache.get("first")
        second = self.cache.get("second")
        self.repository.side_effect = RepositoryError("failed")

        with self.assertRaises(RepositoryError):
            self.cache.get("first", force=True)
        self.assertIs(self.cache.get("second"), second)
        self.assertEqual(self.repository.call_count, 3)

    def test_ttl_rejects_nonpositive_nonfinite_and_nonnumeric_values(self):
        for ttl in (0, -1, float("inf"), float("-inf"), float("nan"), True, False, None, "10"):
            with self.subTest(ttl=ttl):
                with self.assertRaisesRegex(ValueError, "^ttl$"):
                    ProductCache(self.repository, ttl=ttl, clock=self.clock)
        self.repository.assert_not_called()
        self.clock.assert_not_called()

    def test_positive_integer_and_fractional_ttls(self):
        for ttl in (1, 0.25):
            with self.subTest(ttl=ttl):
                clock = Mock(return_value=100.0)
                repository = Mock(return_value=object())
                cache = ProductCache(repository, ttl=ttl, clock=clock)
                value = cache.get("product")
                self.assertIs(cache.get("product"), value)
                repository.assert_called_once_with("product")
                clock.return_value += ttl
                cache.get("product")
                self.assertEqual(repository.call_count, 2)


class APITests(unittest.TestCase):
    def test_lookup_preserves_values_including_none(self):
        for value in (object(), None):
            with self.subTest(value=value):
                repository = Mock(return_value=value)
                api = API(ProductCache(repository, clock=lambda: 100.0))
                self.assertIs(api.lookup("product"), value)
                self.assertIs(api.lookup("product"), value)
                repository.assert_called_once_with("product")

    def test_lookup_forwards_force(self):
        first, second = object(), object()
        repository = Mock(side_effect=[first, second])
        api = API(ProductCache(repository, clock=lambda: 100.0))

        self.assertIs(api.lookup("product"), first)
        self.assertIs(api.lookup("product", force=True), second)
        self.assertEqual(repository.call_count, 2)

    def test_only_repository_errors_are_translated_with_cause(self):
        error = RepositoryError("database unavailable")
        api = API(ProductCache(Mock(side_effect=error), clock=lambda: 100.0))

        with self.assertRaisesRegex(ServiceUnavailable, "^unavailable$") as raised:
            api.lookup("product")
        self.assertIs(raised.exception.__cause__, error)

    def test_other_exceptions_propagate_unchanged(self):
        for error_type in (ValueError, RuntimeError, ServiceUnavailable, KeyboardInterrupt):
            with self.subTest(error=error_type):
                error = error_type("failed")
                api = API(ProductCache(Mock(side_effect=error), clock=lambda: 100.0))
                with self.assertRaises(error_type) as raised:
                    api.lookup("product")
                self.assertIs(raised.exception, error)


if __name__ == "__main__":
    unittest.main()
