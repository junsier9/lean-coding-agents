import unittest
from unittest.mock import Mock, patch

from app import CorruptRecord, RequestError, ServiceUnavailable, get_profile
from storage import MemoryStore, MissingRecord, StoreUnavailable


class GetProfileTests(unittest.TestCase):
    def test_invalid_user_ids_do_not_access_storage(self):
        store = MemoryStore({})
        for user_id in (None, "", 0, False, b"user", [], {}):
            with self.subTest(user_id=user_id):
                with self.assertRaises(RequestError):
                    get_profile(store, user_id)
        self.assertEqual(store.calls, [])

    def test_success_normalizes_profile_and_fetches_once(self):
        record = {"name": "  Alice \t", "age": 0, "extra": "ignored"}
        store = MemoryStore({"user": record})

        self.assertEqual(get_profile(store, "user"), {"name": "Alice", "age": 0})
        self.assertEqual(store.calls, ["user"])
        self.assertEqual(record, {"name": "  Alice \t", "age": 0, "extra": "ignored"})

    def test_nonempty_user_id_is_passed_through(self):
        store = MemoryStore({" ": {"name": "Alice", "age": 20}})

        self.assertEqual(get_profile(store, " "), {"name": "Alice", "age": 20})
        self.assertEqual(store.calls, [" "])

    def test_missing_record_returns_none_after_one_fetch(self):
        store = MemoryStore({})

        self.assertIsNone(get_profile(store, "missing"))
        self.assertEqual(store.calls, ["missing"])

    def test_unavailable_store_translates_error_and_preserves_cause(self):
        error = StoreUnavailable("offline")
        store = Mock()
        store.fetch.side_effect = error

        with self.assertRaises(ServiceUnavailable) as caught:
            get_profile(store, "user")

        self.assertIs(caught.exception.__cause__, error)
        store.fetch.assert_called_once_with("user")

    def test_unexpected_storage_errors_propagate_unchanged(self):
        for error in (KeyError("unexpected"), OSError("disk"), RuntimeError("bug")):
            with self.subTest(error=error):
                store = Mock()
                store.fetch.side_effect = error

                with self.assertRaises(type(error)) as caught:
                    get_profile(store, "user")

                self.assertIs(caught.exception, error)
                store.fetch.assert_called_once_with("user")

    def test_corrupt_records_propagate(self):
        class IntSubclass(int):
            pass

        records = [None, [], "", {}, {"name": "Alice"}]
        records += [{"name": name, "age": 20} for name in (None, 1, "", " \t")]
        records += [
            {"name": "Alice", "age": age}
            for age in (None, -1, True, False, 1.0, "1", IntSubclass(1))
        ]
        for record in records:
            with self.subTest(record=record):
                store = MemoryStore({"user": record})

                with self.assertRaises(CorruptRecord):
                    get_profile(store, "user")

                self.assertEqual(store.calls, ["user"])

    def test_normalization_errors_are_not_handled_as_storage_errors(self):
        for error in (
            CorruptRecord("corrupt"),
            MissingRecord("normalization"),
            StoreUnavailable("normalization"),
            RuntimeError("normalization"),
        ):
            with self.subTest(error=error):
                store = MemoryStore({"user": {"name": "Alice", "age": 20}})

                with patch("app.normalize", side_effect=error):
                    with self.assertRaises(type(error)) as caught:
                        get_profile(store, "user")

                self.assertIs(caught.exception, error)
                self.assertEqual(store.calls, ["user"])


if __name__ == "__main__":
    unittest.main()
