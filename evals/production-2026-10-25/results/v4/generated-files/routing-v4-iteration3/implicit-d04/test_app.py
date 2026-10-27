import unittest
from unittest.mock import Mock, patch

from app import CorruptRecord, RequestError, ServiceUnavailable, get_profile
from storage import MemoryStore, MissingRecord, StoreUnavailable


class GetProfileTests(unittest.TestCase):
    def test_invalid_user_ids_do_not_access_storage(self):
        for user_id in ("", None, 0, False, b"user", [], {}):
            with self.subTest(user_id=user_id):
                store = Mock()
                with self.assertRaises(RequestError):
                    get_profile(store, user_id)
                store.fetch.assert_not_called()

    def test_success_normalizes_fields_with_one_fetch(self):
        store = MemoryStore({
            "user": {"name": "  Alice \t", "age": 0, "extra": "ignored"}
        })

        self.assertEqual(get_profile(store, "user"), {"name": "Alice", "age": 0})
        self.assertEqual(store.calls, ["user"])

    def test_missing_record_returns_none_with_one_fetch(self):
        store = MemoryStore({})

        self.assertIsNone(get_profile(store, "missing"))
        self.assertEqual(store.calls, ["missing"])

    def test_store_unavailable_preserves_cause_with_one_fetch(self):
        error = StoreUnavailable("offline")
        store = Mock()
        store.fetch.side_effect = error

        with self.assertRaises(ServiceUnavailable) as raised:
            get_profile(store, "user")

        self.assertIs(raised.exception.__cause__, error)
        store.fetch.assert_called_once_with("user")

    def test_unexpected_storage_errors_propagate(self):
        for error in (RuntimeError("unexpected"), OSError("io"), KeyError("key")):
            with self.subTest(error=error):
                store = Mock()
                store.fetch.side_effect = error
                with self.assertRaises(type(error)) as raised:
                    get_profile(store, "user")
                self.assertIs(raised.exception, error)
                store.fetch.assert_called_once_with("user")

    def test_corrupt_records_propagate_with_one_fetch(self):
        records = [None, [], {}, {"name": "  ", "age": 1}, {"name": 1, "age": 1}]
        records.extend({"name": "Alice", "age": age}
                       for age in (None, -1, True, 1.0, "1"))
        for record in records:
            with self.subTest(record=record):
                store = MemoryStore({"user": record})
                with self.assertRaises(CorruptRecord):
                    get_profile(store, "user")
                self.assertEqual(store.calls, ["user"])

    def test_normalization_errors_propagate_unchanged(self):
        for error in (RuntimeError("unexpected"), MissingRecord("field"),
                      StoreUnavailable("normalization")):
            with self.subTest(error=error):
                store = MemoryStore({"user": {"name": "Alice", "age": 1}})
                with patch("app.normalize", side_effect=error):
                    with self.assertRaises(type(error)) as raised:
                        get_profile(store, "user")
                self.assertIs(raised.exception, error)
                self.assertEqual(store.calls, ["user"])


if __name__ == "__main__":
    unittest.main()
