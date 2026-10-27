import unittest
from unittest.mock import Mock

from app import API
from cache import ProductCache, RepositoryError, ServiceUnavailable


class ProductCacheTests(unittest.TestCase):
    def setUp(self):
        self.clock = Mock(return_value=100)
        self.repository = Mock()
        self.cache = ProductCache(self.repository, ttl=10, clock=self.clock)

    def test_fresh_hit_uses_injected_clock_without_repository_call(self):
        value = object()
        self.repository.return_value = value
        self.assertIs(self.cache.get("product"), value)
        self.clock.return_value = 109.9
        self.assertIs(self.cache.get("product"), value)
        self.repository.assert_called_once_with("product")

    def test_none_is_cached_until_expiry(self):
        self.repository.side_effect = [None, "available"]
        self.assertIsNone(self.cache.get("product"))
        self.clock.return_value = 109
        self.assertIsNone(self.cache.get("product"))
        self.repository.assert_called_once_with("product")
        self.clock.return_value = 110
        self.assertEqual(self.cache.get("product"), "available")
        self.assertEqual(self.repository.call_count, 2)

    def test_expiry_refreshes_at_deadline_and_renews_ttl(self):
        self.repository.side_effect = ["first", "second", "third"]
        self.assertEqual(self.cache.get("product"), "first")
        self.clock.return_value = 110
        self.assertEqual(self.cache.get("product"), "second")
        self.clock.return_value = 119
        self.assertEqual(self.cache.get("product"), "second")
        self.assertEqual(self.repository.call_count, 2)
        self.clock.return_value = 120
        self.assertEqual(self.cache.get("product"), "third")

    def test_force_refreshes_fresh_value_and_can_cache_none(self):
        self.repository.side_effect = ["first", None]
        self.assertEqual(self.cache.get("product"), "first")
        self.assertIsNone(self.cache.get("product", force=True))
        self.assertIsNone(self.cache.get("product"))
        self.assertEqual(self.repository.call_count, 2)

    def test_old_entry_is_removed_before_repository_refresh(self):
        for force in (False, True):
            with self.subTest(force=force):
                self.clock.return_value = 100
                self.cache.entries.clear()
                self.repository.side_effect = None
                self.repository.return_value = "old"
                self.cache.get("product")
                if not force:
                    self.clock.return_value = 110

                def refresh(key):
                    self.assertNotIn(key, self.cache.entries)
                    return "new"

                self.repository.side_effect = refresh
                self.assertEqual(self.cache.get("product", force=force), "new")

    def test_initial_failure_propagates_without_caching_and_can_retry(self):
        for error in (RepositoryError("offline"), ValueError("bad data")):
            with self.subTest(error=type(error).__name__):
                self.cache.invalidate("product")
                self.repository.reset_mock()
                self.repository.side_effect = [error, "recovered"]
                with self.assertRaises(type(error)) as caught:
                    self.cache.get("product")
                self.assertIs(caught.exception, error)
                self.assertNotIn("product", self.cache.entries)
                self.assertEqual(self.cache.get("product"), "recovered")
                self.assertEqual(self.repository.call_count, 2)

    def test_failed_refresh_removes_old_value_and_retries_on_next_get(self):
        for force in (False, True):
            for old_value in ("old", None):
                with self.subTest(force=force, old_value=old_value):
                    self.clock.return_value = 100
                    self.cache.entries.clear()
                    self.repository.reset_mock()
                    error = RepositoryError("offline")
                    self.repository.side_effect = [old_value, error, "recovered"]
                    self.assertEqual(self.cache.get("product"), old_value)
                    if not force:
                        self.clock.return_value = 110
                    with self.assertRaises(RepositoryError) as caught:
                        self.cache.get("product", force=force)
                    self.assertIs(caught.exception, error)
                    self.assertNotIn("product", self.cache.entries)
                    self.assertEqual(self.cache.get("product"), "recovered")
                    self.assertEqual(self.repository.call_count, 3)

    def test_failed_refresh_preserves_other_keys(self):
        self.repository.side_effect = ["first", "second", RepositoryError("offline")]
        self.cache.get("one")
        self.cache.get("two")
        with self.assertRaises(RepositoryError):
            self.cache.get("one", force=True)
        self.assertEqual(self.cache.get("two"), "second")
        self.repository.assert_called_with("one")
        self.assertEqual(self.repository.call_count, 3)

    def test_invalidate_only_removes_requested_key(self):
        self.repository.side_effect = ["first", "second", "new first"]
        self.cache.get("one")
        self.cache.get("two")
        self.cache.invalidate("missing")
        self.cache.invalidate("one")
        self.assertEqual(self.cache.get("two"), "second")
        self.assertEqual(self.cache.get("one"), "new first")
        self.assertEqual(self.repository.call_count, 3)

    def test_ttl_rejects_invalid_values(self):
        for ttl in (0, -1, float("inf"), float("-inf"), float("nan"), True,
                    False, None, "10"):
            with self.subTest(ttl=ttl):
                with self.assertRaisesRegex(ValueError, "^ttl$"):
                    ProductCache(self.repository, ttl=ttl, clock=self.clock)

    def test_ttl_accepts_positive_integer_and_fraction(self):
        for ttl in (1, 0.5):
            with self.subTest(ttl=ttl):
                cache = ProductCache(self.repository, ttl=ttl, clock=self.clock)
                self.repository.reset_mock()
                self.repository.side_effect = ["first", "second"]
                self.clock.return_value = 100
                self.assertEqual(cache.get("product"), "first")
                self.clock.return_value = 100 + ttl
                self.assertEqual(cache.get("product"), "second")


class APITests(unittest.TestCase):
    def test_lookup_forwards_key_and_force_and_preserves_result(self):
        for result in (object(), None):
            for force in (False, True):
                with self.subTest(result=result, force=force):
                    cache = Mock()
                    cache.get.return_value = result
                    self.assertIs(API(cache).lookup("product", force=force), result)
                    cache.get.assert_called_once_with("product", force=force)

    def test_repository_error_becomes_service_unavailable_with_cause(self):
        error = RepositoryError("offline")
        cache = Mock()
        cache.get.side_effect = error
        with self.assertRaisesRegex(ServiceUnavailable, "^unavailable$") as caught:
            API(cache).lookup("product")
        self.assertIs(caught.exception.__cause__, error)

    def test_other_exceptions_propagate_unchanged(self):
        for error in (ValueError("bad data"), RuntimeError("bug"),
                      ServiceUnavailable("existing"), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                cache = Mock()
                cache.get.side_effect = error
                with self.assertRaises(type(error)) as caught:
                    API(cache).lookup("product")
                self.assertIs(caught.exception, error)

    def test_failed_forced_refresh_is_translated_and_does_not_restore_old_value(self):
        error = RepositoryError("offline")
        repository = Mock(side_effect=["old", error, "recovered"])
        api = API(ProductCache(repository, clock=lambda: 100))
        self.assertEqual(api.lookup("product"), "old")
        with self.assertRaises(ServiceUnavailable) as caught:
            api.lookup("product", force=True)
        self.assertIs(caught.exception.__cause__, error)
        self.assertEqual(api.lookup("product"), "recovered")
        self.assertEqual(repository.call_count, 3)


if __name__ == "__main__":
    unittest.main()
