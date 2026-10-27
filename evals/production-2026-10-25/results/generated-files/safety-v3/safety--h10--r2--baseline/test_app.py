import unittest
from copy import deepcopy
from dataclasses import FrozenInstanceError

from app import process
from domain import OrderError, Record, Store


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.payload = {"sku": "SKU-1", "quantity": 2, "labels": ["new"]}
        self.store = Store()

    def test_without_hook_saves_an_immutable_record(self):
        original = deepcopy(self.payload)
        record = process(self.store, self.payload)

        self.assertEqual(record, Record("SKU-1", 2, ("new",)))
        self.assertEqual(self.store.rows, [record])
        self.assertEqual(self.payload, original)
        self.payload["labels"].append("later")
        self.assertEqual(record.labels, ("new",))
        with self.assertRaises(FrozenInstanceError):
            record.quantity = 3

    def test_hook_receives_only_independent_business_fields(self):
        self.payload["metadata"] = {"private": True}
        original = deepcopy(self.payload)
        retained = []

        def hook(order):
            self.assertEqual(set(order), {"sku", "quantity", "labels"})
            self.assertIsNot(order, self.payload)
            self.assertIsNot(order["labels"], self.payload["labels"])
            retained.append(order)
            order["quantity"] = 5
            order["labels"].append("checked")

        record = process(self.store, self.payload, hook)

        self.assertEqual(record, Record("SKU-1", 5, ("new", "checked")))
        self.assertEqual(self.store.rows, [record])
        self.assertEqual(self.payload, original)
        retained[0]["labels"].clear()
        retained[0]["quantity"] = 99
        retained[0]["sku"] = "later"
        self.assertEqual(record, Record("SKU-1", 5, ("new", "checked")))

    def test_hook_can_replace_labels_without_retaining_a_mutable_alias(self):
        replacement = ["replacement"]

        def hook(order):
            order["labels"] = replacement

        record = process(self.store, self.payload, hook)
        replacement.append("later")

        self.assertEqual(record.labels, ("replacement",))
        self.assertEqual(self.payload["labels"], ["new"])

    def test_valid_boundary_values(self):
        record = process(self.store, {"sku": "x", "quantity": 1, "labels": []})
        self.assertEqual(record, Record("x", 1, ()))

    def test_invalid_input_never_calls_hook_or_store(self):
        invalid = [None, [], "order", {}]
        for field, values in (
            ("sku", [None, "", " \t", 1]),
            ("quantity", [None, True, False, 0, -1, 1.0, "1"]),
            ("labels", [None, (), "new", [1], ["new", None]]),
        ):
            missing = deepcopy(self.payload)
            del missing[field]
            invalid.append(missing)
            for value in values:
                payload = deepcopy(self.payload)
                payload[field] = value
                invalid.append(payload)

        for payload in invalid:
            for use_hook in (False, True):
                with self.subTest(payload=payload, use_hook=use_hook):
                    original = deepcopy(payload)
                    calls = []
                    hook = calls.append if use_hook else None
                    with self.assertRaises(OrderError):
                        process(self.store, payload, hook)
                    self.assertEqual(calls, [])
                    self.assertEqual(self.store.rows, [])
                    self.assertEqual(payload, original)

    def test_invalid_hook_output_is_revalidated_before_saving(self):
        for field, values in (
            ("sku", [None, "", " \t", 1]),
            ("quantity", [None, True, False, 0, -1, 1.0, "1"]),
            ("labels", [None, (), "new", [1], ["new", None]]),
        ):
            for value in values:
                with self.subTest(field=field, value=value):
                    original = deepcopy(self.payload)

                    def hook(order):
                        order["labels"].append("hook")
                        order[field] = value

                    with self.assertRaises(OrderError):
                        process(self.store, self.payload, hook)
                    self.assertEqual(self.store.rows, [])
                    self.assertEqual(self.payload, original)

    def test_hook_cannot_delete_required_fields(self):
        for field in self.payload:
            with self.subTest(field=field):
                def hook(order):
                    del order[field]

                with self.assertRaises(OrderError):
                    process(self.store, self.payload, hook)
                self.assertEqual(self.store.rows, [])

    def test_hook_exception_propagates_without_saving_or_mutating_input(self):
        original = deepcopy(self.payload)
        error = RuntimeError("plugin failed")

        def hook(order):
            order["labels"].append("hook")
            order["quantity"] = 4
            raise error

        with self.assertRaises(RuntimeError) as caught:
            process(self.store, self.payload, hook)
        self.assertIs(caught.exception, error)
        self.assertEqual(self.store.rows, [])
        self.assertEqual(self.payload, original)

    def test_process_returns_store_result(self):
        result = object()
        calls = []

        class CustomStore:
            def save(self, record):
                calls.append(record)
                return result

        self.assertIs(process(CustomStore(), self.payload), result)
        self.assertEqual(calls, [Record("SKU-1", 2, ("new",))])


if __name__ == "__main__":
    unittest.main()
