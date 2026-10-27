import unittest
from unittest.mock import Mock

from app import API
from cache import ProductCache, RepositoryError, ServiceUnavailable


class Clock:
    def __init__(self):
        self.now = 100

    def __call__(self):
        return self.now


class ProductCacheTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.repository = Mock()
        self.cache = ProductCache(self.repository, ttl=10, clock=self.clock)

    def test_fresh_hits_include_none_and_expire_at_ttl_boundary(self):
        for value in ("product", None):
            with self.subTest(value=value):
                clock = Clock()
                repository = Mock(return_value=value)
                cache = ProductCache(repository, ttl=10, clock=clock)
                self.assertEqual(cache.get("a"), value)

                clock.now = 109
                self.assertEqual(cache.get("a"), value)
                repository.assert_called_once_with("a")

                clock.now = 110
                repository.return_value = "updated"
                self.assertEqual(cache.get("a"), "updated")
                self.assertEqual(repository.call_count, 2)
                self.assertEqual(cache.get("a"), "updated")
                self.assertEqual(repository.call_count, 2)

    def test_fractional_ttl_uses_injected_clock(self):
        cache = ProductCache(self.repository, ttl=0.5, clock=self.clock)
        self.repository.return_value = "first"
        self.assertEqual(cache.get("a"), "first")
        self.clock.now = 100.25
        self.assertEqual(cache.get("a"), "first")
        self.repository.assert_called_once_with("a")
        self.clock.now = 100.5
        self.repository.return_value = "second"
        self.assertEqual(cache.get("a"), "second")
        self.assertEqual(self.repository.call_count, 2)

    def test_refresh_removes_old_entry_before_repository_call(self):
        for force in (False, True):
            with self.subTest(force=force):
                clock = Clock()
                repository = Mock(return_value="old")
                cache = ProductCache(repository, ttl=10, clock=clock)
                cache.get("a")
                if not force:
                    clock.now = 110

                def refresh(key):
                    self.assertNotIn(key, cache.entries)
                    return "new"

                repository.side_effect = refresh
                self.assertEqual(cache.get("a", force=force), "new")
                self.assertEqual(cache.get("a"), "new")
                self.assertEqual(repository.call_count, 2)

    def test_refresh_failures_propagate_and_next_get_retries(self):
        for force in (False, True):
            for old_value in ("old", None):
                for error_type in (RepositoryError, ValueError):
                    with self.subTest(force=force, old_value=old_value,
                                      error_type=error_type):
                        clock = Clock()
                        error = error_type("lookup failed")
                        repository = Mock(side_effect=[old_value, error, "recovered"])
                        cache = ProductCache(repository, ttl=10, clock=clock)
                        self.assertEqual(cache.get("a"), old_value)
                        if not force:
                            clock.now = 110

                        with self.assertRaises(error_type) as caught:
                            cache.get("a", force=force)
                        self.assertIs(caught.exception, error)
                        self.assertNotIn("a", cache.entries)

                        self.assertEqual(cache.get("a"), "recovered")
                        self.assertEqual(cache.get("a"), "recovered")
                        self.assertEqual(repository.call_count, 3)

    def test_initial_failure_is_not_cached_as_none(self):
        error = RepositoryError("lookup failed")
        self.repository.side_effect = [error, None]
        with self.assertRaises(RepositoryError) as caught:
            self.cache.get("a")
        self.assertIs(caught.exception, error)
        self.assertNotIn("a", self.cache.entries)
        self.assertIsNone(self.cache.get("a"))
        self.assertIsNone(self.cache.get("a"))
        self.assertEqual(self.repository.call_count, 2)

    def test_keys_are_isolated_during_refresh_failure_and_invalidation(self):
        self.repository.side_effect = ["first", "second", RepositoryError("failed"), "new"]
        self.assertEqual(self.cache.get("a"), "first")
        self.assertEqual(self.cache.get("b"), "second")
        with self.assertRaises(RepositoryError):
            self.cache.get("a", force=True)
        self.assertEqual(self.cache.get("b"), "second")
        self.assertEqual(self.repository.call_count, 3)

        self.cache.invalidate("a")
        self.cache.invalidate("missing")
        self.assertEqual(self.cache.get("b"), "second")
        self.cache.invalidate("b")
        self.assertEqual(self.cache.get("b"), "new")
        self.assertEqual(self.repository.call_count, 4)

    def test_ttl_rejects_nonpositive_nonfinite_and_nonnumeric_values(self):
        for ttl in (0, -1, -0.5, float("nan"), float("inf"), -float("inf"),
                    True, False, None, "10", complex(1, 0)):
            with self.subTest(ttl=ttl):
                with self.assertRaises(ValueError):
                    ProductCache(self.repository, ttl=ttl, clock=self.clock)
        self.repository.assert_not_called()

    def test_invalidate_evicts_only_the_requested_key(self):
        self.repository.side_effect = ["first", "second", "new"]
        self.assertEqual(self.cache.get("a"), "first")
        self.assertEqual(self.cache.get("b"), "second")
        self.cache.invalidate("a")
        self.assertEqual(self.cache.get("b"), "second")
        self.assertEqual(self.repository.call_count, 2)
        self.assertEqual(self.cache.get("a"), "new")
        self.assertEqual(self.repository.call_args_list[-1].args, ("a",))
        self.assertEqual(self.repository.call_count, 3)


class APITests(unittest.TestCase):
    def test_lookup_returns_cache_result_and_forwards_force(self):
        for force in (False, True):
            for value in ("product", None):
                with self.subTest(force=force, value=value):
                    cache = Mock()
                    cache.get.return_value = value
                    self.assertEqual(API(cache).lookup("a", force=force), value)
                    cache.get.assert_called_once_with("a", force=force)

    def test_lookup_translates_repository_error_with_cause(self):
        class SpecificRepositoryError(RepositoryError):
            pass

        for error_type in (RepositoryError, SpecificRepositoryError):
            with self.subTest(error_type=error_type):
                cache = Mock()
                error = error_type("database unavailable")
                cache.get.side_effect = error
                with self.assertRaises(ServiceUnavailable) as caught:
                    API(cache).lookup("a")
                self.assertIs(caught.exception.__cause__, error)

    def test_lookup_propagates_other_exceptions_unchanged(self):
        for error_type in (ValueError, TypeError, RuntimeError, ServiceUnavailable):
            with self.subTest(error_type=error_type):
                cache = Mock()
                error = error_type("unexpected")
                cache.get.side_effect = error
                with self.assertRaises(error_type) as caught:
                    API(cache).lookup("a")
                self.assertIs(caught.exception, error)

    def test_repository_failure_through_api_is_retried(self):
        clock = Clock()
        error = RepositoryError("database unavailable")
        repository = Mock(side_effect=["old", error, "recovered"])
        cache = ProductCache(repository, ttl=10, clock=clock)
        api = API(cache)
        self.assertEqual(api.lookup("a"), "old")
        clock.now = 110
        with self.assertRaises(ServiceUnavailable) as caught:
            api.lookup("a")
        self.assertIs(caught.exception.__cause__, error)
        self.assertEqual(api.lookup("a"), "recovered")
        self.assertEqual(api.lookup("a"), "recovered")
        self.assertEqual(repository.call_count, 3)


if __name__ == "__main__":
    unittest.main()
