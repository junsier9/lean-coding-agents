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
        self.cache = ProductCache(self.repository, ttl=5, clock=self.clock)

    def test_fresh_hit_does_not_query_repository(self):
        product = object()
        self.repository.return_value = product

        self.assertIs(self.cache.get("a"), product)
        self.clock.now += 4.999
        self.assertIs(self.cache.get("a"), product)
        self.repository.assert_called_once_with("a")

    def test_none_is_a_cacheable_result(self):
        self.repository.return_value = None

        self.assertIsNone(self.cache.get("a"))
        self.assertIsNone(self.cache.get("a"))
        self.repository.assert_called_once_with("a")

    def test_expiry_boundary_refreshes_even_a_missing_product(self):
        for previous in (None, "old"):
            with self.subTest(previous=previous):
                clock = Clock()
                repository = Mock(side_effect=[previous, "new"])
                cache = ProductCache(repository, ttl=5, clock=clock)
                self.assertEqual(cache.get("a"), previous)

                clock.now += 5
                self.assertEqual(cache.get("a"), "new")
                self.assertEqual(cache.get("a"), "new")
                self.assertEqual(repository.call_count, 2)

    def test_force_refreshes_a_fresh_entry_and_resets_expiry(self):
        self.repository.side_effect = ["old", "new", "latest"]
        self.assertEqual(self.cache.get("a"), "old")

        self.clock.now += 2
        self.assertEqual(self.cache.get("a", force=True), "new")
        self.clock.now += 3
        self.assertEqual(self.cache.get("a"), "new")
        self.assertEqual(self.repository.call_count, 2)

        self.clock.now += 2
        self.assertEqual(self.cache.get("a"), "latest")

    def test_refresh_removes_only_its_old_entry_before_querying(self):
        for force in (False, True):
            with self.subTest(force=force):
                clock = Clock()
                repository = Mock(return_value="old")
                cache = ProductCache(repository, ttl=5, clock=clock)
                cache.get("a")
                cache.get("b")
                other_entry = cache.entries["b"]
                if not force:
                    clock.now += 5

                def refresh(key):
                    self.assertEqual(key, "a")
                    self.assertNotIn("a", cache.entries)
                    self.assertEqual(cache.entries["b"], other_entry)
                    return "new"

                repository.side_effect = refresh
                self.assertEqual(cache.get("a", force=force), "new")

    def test_refresh_failure_propagates_and_next_get_retries(self):
        for force in (False, True):
            for previous in (None, "old"):
                for error_type in (RepositoryError, ValueError):
                    with self.subTest(force=force, previous=previous,
                                      error_type=error_type):
                        clock = Clock()
                        error = error_type("repository failed")
                        repository = Mock(side_effect=[previous, error, "recovered"])
                        cache = ProductCache(repository, ttl=5, clock=clock)
                        cache.get("a")
                        if not force:
                            clock.now += 5

                        with self.assertRaises(error_type) as caught:
                            cache.get("a", force=force)
                        self.assertIs(caught.exception, error)
                        self.assertNotIn("a", cache.entries)
                        self.assertEqual(cache.get("a"), "recovered")
                        self.assertEqual(cache.get("a"), "recovered")
                        self.assertEqual(repository.call_count, 3)

    def test_initial_failure_is_not_cached_as_none(self):
        error = RepositoryError("offline")
        self.repository.side_effect = [error, None]

        with self.assertRaises(RepositoryError) as caught:
            self.cache.get("a")
        self.assertIs(caught.exception, error)
        self.assertNotIn("a", self.cache.entries)
        self.assertIsNone(self.cache.get("a"))
        self.assertIsNone(self.cache.get("a"))
        self.assertEqual(self.repository.call_count, 2)

    def test_invalidate_is_idempotent_and_keeps_other_keys(self):
        self.repository.side_effect = ["product a", "product b", "new a"]
        self.assertEqual(self.cache.get("a"), "product a")
        self.assertEqual(self.cache.get("b"), "product b")

        self.cache.invalidate("a")
        self.cache.invalidate("a")
        self.cache.invalidate("absent")
        self.assertEqual(self.cache.get("b"), "product b")
        self.assertEqual(self.cache.get("a"), "new a")
        self.assertEqual(self.repository.call_count, 3)

    def test_failure_for_one_key_does_not_evict_another_key(self):
        self.repository.side_effect = ["product b", RepositoryError("offline")]
        self.assertEqual(self.cache.get("b"), "product b")

        with self.assertRaises(RepositoryError):
            self.cache.get("a", force=True)
        self.assertEqual(self.cache.get("b"), "product b")
        self.assertEqual(self.repository.call_count, 2)

    def test_ttl_rejects_nonpositive_nonfinite_and_invalid_values(self):
        for ttl in (0, -1, -0.1, float("inf"), float("-inf"),
                    float("nan"), True, False, None, "5"):
            with self.subTest(ttl=ttl):
                with self.assertRaises(ValueError):
                    ProductCache(self.repository, ttl=ttl, clock=self.clock)
        self.repository.assert_not_called()

    def test_positive_fractional_ttl_uses_injected_clock(self):
        self.repository.side_effect = ["old", "new"]
        cache = ProductCache(self.repository, ttl=0.5, clock=self.clock)
        self.assertEqual(cache.get("a"), "old")

        self.clock.now += 0.25
        self.assertEqual(cache.get("a"), "old")
        self.clock.now += 0.25
        self.assertEqual(cache.get("a"), "new")


class APITests(unittest.TestCase):
    def test_lookup_forwards_key_and_force_and_returns_result(self):
        for force in (False, True):
            for result in (None, "product"):
                with self.subTest(force=force, result=result):
                    cache = Mock()
                    cache.get.return_value = result
                    self.assertEqual(API(cache).lookup("a", force=force), result)
                    cache.get.assert_called_once_with("a", force=force)

    def test_lookup_translates_repository_error_and_preserves_cause(self):
        class SpecificRepositoryError(RepositoryError):
            pass

        for error_type in (RepositoryError, SpecificRepositoryError):
            with self.subTest(error_type=error_type):
                error = error_type("offline")
                repository = Mock(side_effect=error)
                api = API(ProductCache(repository))

                with self.assertRaises(ServiceUnavailable) as caught:
                    api.lookup("a")
                self.assertIs(caught.exception.__cause__, error)

    def test_lookup_propagates_other_exceptions_unchanged(self):
        for error in (ValueError("invalid"), TypeError("bad type"),
                      RuntimeError("bug"), ServiceUnavailable("existing")):
            with self.subTest(error=error):
                repository = Mock(side_effect=error)
                api = API(ProductCache(repository))

                with self.assertRaises(type(error)) as caught:
                    api.lookup("a")
                self.assertIs(caught.exception, error)
                self.assertIsNone(caught.exception.__cause__)

    def test_lookup_propagates_a_clock_error_unchanged(self):
        error = ValueError("clock failed")
        repository = Mock()
        api = API(ProductCache(repository, clock=Mock(side_effect=error)))

        with self.assertRaises(ValueError) as caught:
            api.lookup("a")
        self.assertIs(caught.exception, error)
        repository.assert_not_called()


if __name__ == "__main__":
    unittest.main()
