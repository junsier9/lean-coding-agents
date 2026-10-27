import unittest
from unittest.mock import Mock, patch

import app
from storage import MemoryStore, MissingRecord, StoreUnavailable


class GetProfileTests(unittest.TestCase):
    def test_success_returns_normalized_name_and_age(self):
        for user_id, age in [("user", 18), (" ", 0)]:
            with self.subTest(user_id=user_id, age=age):
                store = MemoryStore(
                    {user_id: {"name": "  Alice  ", "age": age, "extra": "ignored"}}
                )
                self.assertEqual(
                    app.get_profile(store, user_id), {"name": "Alice", "age": age}
                )
                self.assertEqual(store.calls, [user_id])

    def test_invalid_user_ids_do_not_access_storage(self):
        for user_id in [None, "", 0, 1, True, b"user", [], {}]:
            with self.subTest(user_id=user_id):
                store = Mock(spec=MemoryStore)
                with self.assertRaises(app.RequestError):
                    app.get_profile(store, user_id)
                store.fetch.assert_not_called()

    def test_missing_record_returns_none(self):
        store = MemoryStore({})
        self.assertIsNone(app.get_profile(store, "missing"))
        self.assertEqual(store.calls, ["missing"])

    def test_store_unavailable_is_translated_with_original_cause(self):
        error = StoreUnavailable("offline")
        store = Mock(spec=MemoryStore)
        store.fetch.side_effect = error
        with self.assertRaises(app.ServiceUnavailable) as raised:
            app.get_profile(store, "user")
        self.assertIs(raised.exception.__cause__, error)
        store.fetch.assert_called_once_with("user")

    def test_corrupt_records_propagate(self):
        records = [
            None,
            [],
            {},
            {"name": 1, "age": 1},
            {"name": "   ", "age": 1},
            {"name": "Alice"},
        ]
        records.extend(
            {"name": "Alice", "age": age}
            for age in [None, -1, True, False, 1.5, "1"]
        )
        for record in records:
            with self.subTest(record=record):
                store = MemoryStore({"user": record})
                with self.assertRaises(app.CorruptRecord):
                    app.get_profile(store, "user")
                self.assertEqual(store.calls, ["user"])

    def test_unexpected_storage_errors_propagate(self):
        for error in [RuntimeError("unexpected"), KeyError("key"), OSError("io")]:
            with self.subTest(error=error):
                store = Mock(spec=MemoryStore)
                store.fetch.side_effect = error
                with self.assertRaises(type(error)) as raised:
                    app.get_profile(store, "user")
                self.assertIs(raised.exception, error)
                store.fetch.assert_called_once_with("user")

    def test_normalization_errors_propagate_without_translation(self):
        for error in [
            app.CorruptRecord("corrupt"),
            RuntimeError("unexpected"),
            MissingRecord("unexpected during normalization"),
            StoreUnavailable("unexpected during normalization"),
        ]:
            with self.subTest(error=error):
                record = {"name": "Alice", "age": 18}
                store = MemoryStore({"user": record})
                with patch.object(app, "normalize", side_effect=error) as normalize:
                    with self.assertRaises(type(error)) as raised:
                        app.get_profile(store, "user")
                self.assertIs(raised.exception, error)
                normalize.assert_called_once_with(record)
                self.assertEqual(store.calls, ["user"])


if __name__ == "__main__":
    unittest.main()
