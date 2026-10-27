import copy
from dataclasses import FrozenInstanceError
import unittest
from unittest.mock import Mock

from app import process
from domain import OrderError, Record, Store


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.payload = {"sku": "SKU-1", "quantity": 2, "labels": ["new"]}

    def test_without_hook_saves_immutable_record(self):
        store = Store()

        result = process(store, self.payload)

        self.assertEqual(result, Record("SKU-1", 2, ("new",)))
        self.assertEqual(store.rows, [result])
        self.assertIsInstance(result.labels, tuple)
        self.payload["labels"].append("later")
        self.assertEqual(result.labels, ("new",))
        with self.assertRaises(FrozenInstanceError):
            result.quantity = 3

    def test_hook_receives_only_business_fields_and_independent_labels(self):
        extra = {"private": []}
        self.payload["extra"] = extra
        original = copy.deepcopy(self.payload)
        store = Store()

        def hook(order):
            self.assertEqual(set(order), {"sku", "quantity", "labels"})
            self.assertIsNot(order, self.payload)
            self.assertIsNot(order["labels"], self.payload["labels"])
            order["quantity"] = 5
            order["labels"].append("processed")

        result = process(store, self.payload, hook)

        self.assertEqual(result, Record("SKU-1", 5, ("new", "processed")))
        self.assertEqual(self.payload, original)
        self.assertIs(self.payload["extra"], extra)
        self.assertEqual(store.rows, [result])

    def test_retained_hook_references_cannot_change_saved_record(self):
        retained = {}
        replacement = ["replacement"]
        store = Store()

        def hook(order):
            retained["order"] = order
            retained["initial_labels"] = order["labels"]
            order["labels"] = replacement

        result = process(store, self.payload, hook)
        retained["initial_labels"].append("old reference")
        replacement.append("new reference")
        retained["order"]["sku"] = "changed"
        retained["order"]["quantity"] = 9
        retained["order"]["labels"] = ["changed"]

        self.assertEqual(result, Record("SKU-1", 2, ("replacement",)))
        self.assertEqual(store.rows, [result])
        self.assertEqual(self.payload["labels"], ["new"])

    def test_invalid_inputs_do_not_call_hook_or_store(self):
        invalid_payloads = [None, [], "order", {}]
        for field in self.payload:
            missing = dict(self.payload)
            del missing[field]
            invalid_payloads.append(missing)
        for field, values in (
            ("sku", (None, "", " \t", 1)),
            ("quantity", (None, 0, -1, True, False, 1.0, "2")),
            ("labels", (None, "new", ("new",), [1], ["new", None])),
        ):
            for value in values:
                invalid_payloads.append({**self.payload, field: value})

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                original = copy.deepcopy(payload)
                store = Mock(spec=Store)
                hook = Mock()

                with self.assertRaises(OrderError):
                    process(store, payload, hook)

                hook.assert_not_called()
                store.save.assert_not_called()
                self.assertEqual(payload, original)

    def test_invalid_hook_mutations_do_not_save_or_change_input(self):
        for field, values in (
            ("sku", (None, "", " ", 1)),
            ("quantity", (None, 0, -1, True, False, 1.0, "2")),
            ("labels", (None, "new", ("new",), [1], ["new", None])),
        ):
            for value in values:
                with self.subTest(field=field, value=value):
                    store = Mock(spec=Store)
                    original = copy.deepcopy(self.payload)

                    def hook(order):
                        order[field] = value

                    with self.assertRaises(OrderError):
                        process(store, self.payload, hook)

                    store.save.assert_not_called()
                    self.assertEqual(self.payload, original)

    def test_hook_deleting_required_field_raises_order_error(self):
        for field in self.payload:
            with self.subTest(field=field):
                store = Mock(spec=Store)

                def hook(order):
                    del order[field]

                with self.assertRaises(OrderError):
                    process(store, self.payload, hook)

                store.save.assert_not_called()

    def test_hook_mutating_labels_in_place_is_revalidated(self):
        store = Mock(spec=Store)

        def hook(order):
            order["labels"].append(1)

        with self.assertRaises(OrderError):
            process(store, self.payload, hook)

        store.save.assert_not_called()
        self.assertEqual(self.payload["labels"], ["new"])

    def test_hook_exception_propagates_without_saving_or_changing_input(self):
        store = Mock(spec=Store)
        failure = RuntimeError("hook failed")
        original = copy.deepcopy(self.payload)

        def hook(order):
            order["quantity"] = 5
            order["labels"].append("processed")
            raise failure

        with self.assertRaises(RuntimeError) as caught:
            process(store, self.payload, hook)

        self.assertIs(caught.exception, failure)
        store.save.assert_not_called()
        self.assertEqual(self.payload, original)

    def test_valid_empty_labels_and_minimum_quantity_with_and_without_hook(self):
        for hook in (None, lambda order: None):
            with self.subTest(hook=hook):
                store = Store()
                result = process(store, {"sku": "A", "quantity": 1, "labels": []}, hook)

                self.assertEqual(result, Record("A", 1, ()))
                self.assertEqual(store.rows, [result])

    def test_returns_store_save_result(self):
        store = Mock(spec=Store)
        expected = object()
        store.save.return_value = expected

        result = process(store, self.payload)

        self.assertIs(result, expected)
        store.save.assert_called_once_with(Record("SKU-1", 2, ("new",)))


if __name__ == "__main__":
    unittest.main()
