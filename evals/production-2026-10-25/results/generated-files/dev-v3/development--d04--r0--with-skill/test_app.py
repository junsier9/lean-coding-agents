import unittest
from unittest.mock import Mock, patch

from app import CorruptRecord, RequestError, ServiceUnavailable, get_profile
from storage import MemoryStore, MissingRecord, StoreUnavailable


class ProfileTests(unittest.TestCase):
    def test_invalid_user_ids_do_not_access_storage(self):
        for user_id in (None, "", 0, 1, False, [], {}, b"user"):
            with self.subTest(user_id=user_id):
                store = Mock(spec=MemoryStore)
                with self.assertRaises(RequestError):
                    get_profile(store, user_id)
                store.fetch.assert_not_called()

    def test_success_normalizes_fields_and_fetches_once(self):
        for user_id, age in (("user", 0), (" user ", 42), (" ", 10)):
            with self.subTest(user_id=user_id, age=age):
                record = {"name": " \tAlice\n", "age": age, "extra": "ignored"}
                store = MemoryStore({user_id: record})
                self.assertEqual(get_profile(store, user_id), {"name": "Alice", "age": age})
                self.assertEqual(store.calls, [user_id])

    def test_missing_profile_returns_none_after_one_fetch(self):
        store = MemoryStore({})
        self.assertIsNone(get_profile(store, "missing"))
        self.assertEqual(store.calls, ["missing"])

    def test_store_unavailable_is_translated_with_original_cause(self):
        error = StoreUnavailable("offline")
        store = Mock(spec=MemoryStore)
        store.fetch.side_effect = error
        with self.assertRaises(ServiceUnavailable) as caught:
            get_profile(store, "user")
        self.assertIs(caught.exception.__cause__, error)
        store.fetch.assert_called_once_with("user")

    def test_unexpected_storage_errors_propagate(self):
        for error in (KeyError("unexpected"), OSError("unexpected"), RuntimeError("unexpected")):
            with self.subTest(error=error):
                store = Mock(spec=MemoryStore)
                store.fetch.side_effect = error
                with self.assertRaises(type(error)) as caught:
                    get_profile(store, "user")
                self.assertIs(caught.exception, error)
                store.fetch.assert_called_once_with("user")

    def test_corrupt_records_propagate(self):
        class IntSubclass(int):
            pass

        records = [None, [], {}, {"name": "Alice"}, {"age": 10}]
        records.extend({"name": name, "age": 10} for name in (None, "", " \t", 123))
        records.extend(
            {"name": "Alice", "age": age}
            for age in (None, -1, True, False, 1.0, "1", IntSubclass(1))
        )
        for record in records:
            with self.subTest(record=record):
                store = MemoryStore({"user": record})
                with self.assertRaises(CorruptRecord):
                    get_profile(store, "user")
                self.assertEqual(store.calls, ["user"])

    def test_normalization_errors_are_not_treated_as_storage_errors(self):
        for error in (MissingRecord("name"), StoreUnavailable("unexpected"), RuntimeError("unexpected")):
            with self.subTest(error=error):
                store = MemoryStore({"user": {"name": "Alice", "age": 10}})
                with patch("app.normalize", side_effect=error):
                    with self.assertRaises(type(error)) as caught:
                        get_profile(store, "user")
                self.assertIs(caught.exception, error)
                self.assertEqual(store.calls, ["user"])


if __name__ == "__main__":
    unittest.main()
