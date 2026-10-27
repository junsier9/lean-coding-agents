import unittest
from unittest.mock import Mock

from app import API
from cache import ProductCache, RepositoryError, ServiceUnavailable


class ProductCacheTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.repository = Mock()
        self.cache = ProductCache(
            self.repository, ttl=10, clock=lambda: self.now
        )

    def test_fresh_hit_uses_injected_clock_without_repository_call(self):
        value = object()
        self.repository.return_value = value

        self.assertIs(self.cache.get("product"), value)
        self.now = 109.999
        self.assertIs(self.cache.get("product"), value)
        self.repository.assert_called_once_with("product")

    def test_none_is_cached_until_expiration(self):
        self.repository.side_effect = [None, "available"]

        self.assertIsNone(self.cache.get("product"))
        self.now = 109
        self.assertIsNone(self.cache.get("product"))
        self.repository.assert_called_once_with("product")

        self.now = 110
        self.assertEqual(self.cache.get("product"), "available")
        self.assertEqual(self.repository.call_count, 2)

    def test_successful_refresh_removes_old_entry_before_repository_call(self):
        for force in (False, True):
            with self.subTest(force=force):
                self.setUp()
                self.repository.return_value = "old"
                self.cache.get("product")
                self.now = 101 if force else 110

                def refresh(key):
                    self.assertNotIn(key, self.cache.entries)
                    return "new"

                self.repository.side_effect = refresh
                self.assertEqual(self.cache.get("product", force=force), "new")
                self.assertEqual(self.cache.get("product"), "new")
                self.assertEqual(self.repository.call_count, 2)

    def test_failures_propagate_and_leave_no_cached_result(self):
        for error_type in (RepositoryError, ValueError):
            for state in ("cold", "expired", "forced"):
                for old_value in (None, "old"):
                    with self.subTest(
                        error_type=error_type, state=state, old_value=old_value
                    ):
                        self.setUp()
                        if state != "cold":
                            self.repository.return_value = old_value
                            self.cache.get("product")
                        self.now = 110 if state == "expired" else 101
                        error = error_type("lookup failed")
                        self.repository.side_effect = error

                        with self.assertRaises(error_type) as caught:
                            self.cache.get("product", force=state == "forced")
                        self.assertIs(caught.exception, error)
                        self.assertNotIn("product", self.cache.entries)

                        self.repository.side_effect = None
                        self.repository.return_value = "recovered"
                        calls_before_retry = self.repository.call_count
                        self.assertEqual(self.cache.get("product"), "recovered")
                        self.assertEqual(
                            self.repository.call_count, calls_before_retry + 1
                        )

    def test_invalidate_and_failed_refresh_are_isolated_by_key(self):
        self.repository.side_effect = lambda key: key + " value"
        self.cache.get("first")
        self.cache.get("second")
        second_entry = self.cache.entries["second"]
        self.repository.side_effect = RepositoryError("unavailable")

        with self.assertRaises(RepositoryError):
            self.cache.get("first", force=True)
        self.assertEqual(self.cache.get("second"), "second value")
        self.assertIs(self.cache.entries["second"], second_entry)
        self.cache.invalidate("first")
        self.assertEqual(self.cache.get("second"), "second value")

        self.cache.invalidate("second")
        self.cache.invalidate("missing")
        self.repository.side_effect = None
        self.repository.return_value = "new value"
        self.assertEqual(self.cache.get("second"), "new value")
        self.assertEqual(self.repository.call_count, 4)

    def test_ttl_must_be_a_finite_positive_number(self):
        for ttl in (0, -1, float("nan"), float("inf"), -float("inf"),
                    True, False, None, "10"):
            with self.subTest(ttl=ttl):
                with self.assertRaises(ValueError):
                    ProductCache(self.repository, ttl=ttl)

        for ttl in (1, 0.5):
            with self.subTest(ttl=ttl):
                cache = ProductCache(self.repository, ttl=ttl, clock=lambda: 0)
                self.repository.return_value = "value"
                self.assertEqual(cache.get("product"), "value")
                self.assertEqual(cache.entries["product"], (ttl, "value"))


class APITests(unittest.TestCase):
    def test_lookup_forwards_key_force_and_result(self):
        for value in (None, object()):
            with self.subTest(value=value):
                cache = Mock()
                cache.get.return_value = value
                self.assertIs(API(cache).lookup("product", force=True), value)
                cache.get.assert_called_once_with("product", force=True)

    def test_repository_error_is_translated_with_original_cause(self):
        class SpecificRepositoryError(RepositoryError):
            pass

        for error_type in (RepositoryError, SpecificRepositoryError):
            with self.subTest(error_type=error_type):
                error = error_type("database failed")
                cache = ProductCache(Mock(side_effect=error))
                with self.assertRaises(ServiceUnavailable) as caught:
                    API(cache).lookup("product")
                self.assertIs(caught.exception.__cause__, error)
                self.assertNotIn("product", cache.entries)

    def test_other_exceptions_propagate_unchanged(self):
        for error in (ValueError("bad value"), TypeError("bad type"),
                      RuntimeError("bug"), ServiceUnavailable("existing")):
            with self.subTest(error_type=type(error)):
                cache = ProductCache(Mock(side_effect=error))
                with self.assertRaises(type(error)) as caught:
                    API(cache).lookup("product")
                self.assertIs(caught.exception, error)


if __name__ == "__main__":
    unittest.main()
