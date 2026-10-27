import dataclasses
import unittest
from app import process
from domain import Store, Record, OrderError

class Contract(unittest.TestCase):
    def setUp(self):
        self.store = Store()
    def payload(self):
        return {"sku": "A", "quantity": 2, "labels": ["new"]}

    def test_normal(self):
        self.assertEqual(process(self.store, self.payload()), Record("A", 2, ("new",)))

    def test_hook_valid_changes(self):
        calls = []
        def hook(value):
            calls.append(1)
            value["quantity"] = 4
            value["labels"].append("plugin")
        result = process(self.store, self.payload(), hook)
        self.assertEqual(result, Record("A", 4, ("new", "plugin")))
        self.assertEqual(calls, [1])

    def test_invalid_initial_skips_hook(self):
        payload, calls = self.payload(), []
        payload["quantity"] = 0
        with self.assertRaises(OrderError):
            process(self.store, payload, lambda value: calls.append(1))
        self.assertEqual(calls, [])
        self.assertEqual(self.store.rows, [])

    def test_invalid_after_hook(self):
        def hook(value):
            value["quantity"] = 0
        with self.assertRaises(OrderError):
            process(self.store, self.payload(), hook)
        self.assertEqual(self.store.rows, [])

    def test_bool_after_hook(self):
        def hook(value):
            value["quantity"] = True
        with self.assertRaises(OrderError):
            process(self.store, self.payload(), hook)
        self.assertEqual(self.store.rows, [])

    def test_bad_labels_after_hook(self):
        def hook(value):
            value["labels"] = "oops"
        with self.assertRaises(OrderError):
            process(self.store, self.payload(), hook)

    def test_hook_error_propagates(self):
        error = RuntimeError("plugin")
        def hook(value):
            raise error
        with self.assertRaises(RuntimeError) as found:
            process(self.store, self.payload(), hook)
        self.assertIs(found.exception, error)
        self.assertEqual(self.store.rows, [])

    def test_input_ownership(self):
        payload = self.payload()
        def hook(value):
            value["labels"].append("changed")
        process(self.store, payload, hook)
        self.assertEqual(payload, self.payload())

    def test_stashed_hook_reference(self):
        captured = []
        result = process(self.store, self.payload(), lambda value: captured.append(value))
        captured[0]["labels"].append("later")
        captured[0]["quantity"] = 999
        self.assertEqual(result, Record("A", 2, ("new",)))

    def test_result_frozen(self):
        result = process(self.store, self.payload())
        with self.assertRaises(dataclasses.FrozenInstanceError):
            result.quantity = 0
