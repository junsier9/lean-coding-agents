import unittest
from dataclasses import FrozenInstanceError
from unittest.mock import Mock

from app import process
from domain import OrderError, Record, Store


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.payload = {"sku": "item-1", "quantity": 2, "labels": ["new"]}
        self.store = Store()

    def test_without_hook_saves_immutable_record_and_returns_save_result(self):
        result = process(self.store, self.payload)

        self.assertEqual(result, Record("item-1", 2, ("new",)))
        self.assertIs(self.store.rows[0], result)
        with self.assertRaises(FrozenInstanceError):
            result.quantity = 3
        self.payload["labels"].append("later")
        self.assertEqual(result.labels, ("new",))

    def test_returns_store_result(self):
        store = Mock()

        result = process(store, self.payload)

        self.assertIs(result, store.save.return_value)
        store.save.assert_called_once_with(Record("item-1", 2, ("new",)))

    def test_hook_gets_only_independent_business_fields(self):
        self.payload["metadata"] = {"private": []}
        observed = []

        def hook(owned):
            self.assertEqual(set(owned), {"sku", "quantity", "labels"})
            self.assertIsNot(owned, self.payload)
            self.assertIsNot(owned["labels"], self.payload["labels"])
            observed.append(owned)
            owned["quantity"] = 5
            owned["labels"].append("checked")

        result = process(self.store, self.payload, hook)

        self.assertEqual(result, Record("item-1", 5, ("new", "checked")))
        self.assertEqual(
            self.payload,
            {"sku": "item-1", "quantity": 2, "labels": ["new"],
             "metadata": {"private": []}},
        )
        observed[0]["labels"].append("late mutation")
        observed[0]["quantity"] = 99
        observed[0].clear()
        self.assertEqual(self.store.rows, [Record("item-1", 5, ("new", "checked"))])

    def test_hook_replacement_labels_are_detached_before_saving(self):
        replacement = ["replacement"]

        def hook(owned):
            owned["labels"] = replacement

        result = process(self.store, self.payload, hook)
        replacement.append("late mutation")

        self.assertEqual(result.labels, ("replacement",))
        self.assertEqual(self.payload["labels"], ["new"])

    def test_invalid_input_never_calls_hook_or_store(self):
        invalid_payloads = [None, [], "order", 1, {},
                            {"quantity": 1, "labels": []},
                            {"sku": "item-1", "labels": []},
                            {"sku": "item-1", "quantity": 1}]
        invalid_values = {
            "sku": [None, "", " \t", 7, []],
            "quantity": [None, True, False, 0, -1, 1.0, "1"],
            "labels": [None, (), "new", {}, [1], ["new", None]],
        }
        for field, values in invalid_values.items():
            for value in values:
                invalid_payloads.append({**self.payload, field: value})

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                for use_hook in (False, True):
                    with self.subTest(use_hook=use_hook):
                        hook, store = Mock(), Mock()
                        with self.assertRaises(OrderError):
                            process(store, payload, hook if use_hook else None)
                        hook.assert_not_called()
                        store.save.assert_not_called()

    def test_invalid_hook_output_is_revalidated_before_save(self):
        invalid_values = {
            "sku": [None, "", " \t", 7],
            "quantity": [None, True, False, 0, -1, 1.0, "1"],
            "labels": [None, (), "new", {}, [1], ["new", None]],
        }
        for field, values in invalid_values.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    store = Mock()

                    def hook(owned):
                        owned[field] = value

                    with self.assertRaises(OrderError):
                        process(store, self.payload, hook)
                    store.save.assert_not_called()
                    self.assertEqual(
                        self.payload,
                        {"sku": "item-1", "quantity": 2, "labels": ["new"]},
                    )

    def test_hook_cannot_remove_required_fields(self):
        for field in self.payload:
            with self.subTest(field=field):
                store = Mock()

                def hook(owned):
                    del owned[field]

                with self.assertRaises(OrderError):
                    process(store, self.payload, hook)
                store.save.assert_not_called()

    def test_hook_exception_propagates_unchanged_without_saving(self):
        error = RuntimeError("hook failed")
        store = Mock()

        def hook(owned):
            owned["labels"].append("changed")
            owned["quantity"] = 4
            raise error

        with self.assertRaises(RuntimeError) as caught:
            process(store, self.payload, hook)

        self.assertIs(caught.exception, error)
        store.save.assert_not_called()
        self.assertEqual(
            self.payload, {"sku": "item-1", "quantity": 2, "labels": ["new"]}
        )

    def test_empty_labels_and_positive_integer_are_valid(self):
        result = process(self.store, {"sku": "item-2", "quantity": 1, "labels": []})

        self.assertEqual(result, Record("item-2", 1, ()))


if __name__ == "__main__":
    unittest.main()
