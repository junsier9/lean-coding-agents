import unittest
from unittest.mock import Mock, call

from app import API
from cache import ProductCache, RepositoryError, ServiceUnavailable


class ProductCacheTests(unittest.TestCase):
    def setUp(self):
        self.clock = Mock(return_value=100)
        self.repository = Mock()
        self.cache = ProductCache(self.repository, ttl=10, clock=self.clock)

    def test_fresh_hit_and_exact_expiration(self):
        first, second = object(), object()
        self.repository.side_effect = [first, second]

        self.assertIs(self.cache.get("product"), first)
        self.clock.return_value = 109.999
        self.assertIs(self.cache.get("product"), first)
        self.repository.assert_called_once_with("product")

        self.clock.return_value = 110
        self.assertIs(self.cache.get("product"), second)
        self.assertEqual(self.repository.call_args_list, [call("product"), call("product")])

    def test_none_is_cached_until_expiration(self):
        product = object()
        self.repository.side_effect = [None, product]

        self.assertIsNone(self.cache.get("product"))
        self.clock.return_value = 109
        self.assertIsNone(self.cache.get("product"))
        self.repository.assert_called_once_with("product")

        self.clock.return_value = 110
        self.assertIs(self.cache.get("product"), product)

    def test_force_removes_fresh_entry_before_loading(self):
        old, new = object(), object()
        self.repository.return_value = old
        self.assertIs(self.cache.get("product"), old)

        def refresh(key):
            self.assertNotIn(key, self.cache.entries)
            return new

        self.repository.side_effect = refresh
        self.assertIs(self.cache.get("product", force=True), new)
        self.assertIs(self.cache.get("product"), new)
        self.assertEqual(self.repository.call_count, 2)

    def test_failed_refresh_discards_old_entry_and_allows_retry(self):
        for force in (False, True):
            for old in (None, object()):
                for error in (RepositoryError("offline"), ValueError("bad data")):
                    with self.subTest(force=force, old=old, error=type(error)):
                        clock = Mock(return_value=100)
                        repository = Mock(return_value=old)
                        cache = ProductCache(repository, ttl=10, clock=clock)
                        cache.get("product")
                        if not force:
                            clock.return_value = 110

                        def fail(key):
                            self.assertNotIn(key, cache.entries)
                            raise error

                        repository.side_effect = fail
                        with self.assertRaises(type(error)) as caught:
                            cache.get("product", force=force)
                        self.assertIs(caught.exception, error)
                        self.assertNotIn("product", cache.entries)

                        recovered = object()
                        repository.side_effect = None
                        repository.return_value = recovered
                        self.assertIs(cache.get("product"), recovered)
                        self.assertIs(cache.get("product"), recovered)
                        self.assertEqual(repository.call_count, 3)

    def test_failed_miss_is_not_cached(self):
        for error in (RepositoryError("offline"), ValueError("bad data")):
            with self.subTest(error=type(error)):
                recovered = object()
                self.repository.side_effect = [error, recovered]
                with self.assertRaises(type(error)) as caught:
                    self.cache.get("product")
                self.assertIs(caught.exception, error)
                self.assertNotIn("product", self.cache.entries)
                self.assertIs(self.cache.get("product"), recovered)
                self.cache.invalidate("product")

    def test_invalidation_and_failed_refresh_are_isolated_by_key(self):
        first, second, refreshed = object(), object(), object()
        self.repository.side_effect = [first, second, refreshed, RepositoryError("offline")]
        self.assertIs(self.cache.get("first"), first)
        self.assertIs(self.cache.get("second"), second)

        self.cache.invalidate("missing")
        self.cache.invalidate("first")
        self.assertIs(self.cache.get("second"), second)
        self.assertIs(self.cache.get("first"), refreshed)
        with self.assertRaises(RepositoryError):
            self.cache.get("first", force=True)
        self.assertIs(self.cache.get("second"), second)
        self.assertEqual(self.repository.call_args_list,
                         [call("first"), call("second"), call("first"), call("first")])

    def test_ttl_must_be_finite_and_positive(self):
        for ttl in (0, -1, float("inf"), float("-inf"), float("nan"), True, False, None, "10"):
            with self.subTest(ttl=ttl), self.assertRaises(ValueError):
                ProductCache(self.repository, ttl=ttl, clock=self.clock)
        for ttl in (1, 0.5):
            with self.subTest(ttl=ttl):
                cache = ProductCache(self.repository, ttl=ttl, clock=self.clock)
                cache.get("product")
                self.assertEqual(cache.entries["product"][0], 100 + ttl)


class APITests(unittest.TestCase):
    def test_lookup_forwards_arguments_and_returns_results(self):
        for result in (None, object()):
            for force in (False, True):
                with self.subTest(result=result, force=force):
                    cache = Mock()
                    cache.get.return_value = result
                    self.assertIs(API(cache).lookup("product", force=force), result)
                    cache.get.assert_called_once_with("product", force=force)

    def test_repository_error_is_translated_with_cause(self):
        error = RepositoryError("offline")
        repository = Mock(side_effect=error)
        cache = ProductCache(repository, clock=lambda: 100)
        with self.assertRaises(ServiceUnavailable) as caught:
            API(cache).lookup("product")
        self.assertIs(caught.exception.__cause__, error)
        self.assertNotIn("product", cache.entries)

    def test_other_exceptions_propagate_unchanged(self):
        for error in (ValueError("bad key"), TypeError("bug"), ServiceUnavailable("existing")):
            with self.subTest(error=type(error)):
                cache = Mock()
                cache.get.side_effect = error
                with self.assertRaises(type(error)) as caught:
                    API(cache).lookup("product", force=True)
                self.assertIs(caught.exception, error)


if __name__ == "__main__":
    unittest.main()
