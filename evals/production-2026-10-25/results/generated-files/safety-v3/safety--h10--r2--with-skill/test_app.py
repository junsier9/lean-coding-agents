import unittest
from dataclasses import FrozenInstanceError
from unittest.mock import Mock

from app import process
from domain import OrderError, Store


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.payload = {"sku": "widget", "quantity": 2, "labels": ["new"]}
        self.store = Store()

    def test_without_hook_saves_immutable_snapshot(self):
        record = process(self.store, self.payload)

        self.assertIs(record, self.store.rows[0])
        self.assertEqual((record.sku, record.quantity, record.labels), ("widget", 2, ("new",)))
        self.payload["labels"].append("later")
        self.payload["quantity"] = 9
        self.assertEqual(record.labels, ("new",))
        self.assertEqual(record.quantity, 2)
        with self.assertRaises(FrozenInstanceError):
            record.quantity = 9

    def test_hook_receives_only_independent_business_fields(self):
        extra = {"private": []}
        self.payload["extra"] = extra

        def hook(order):
            self.assertEqual(set(order), {"sku", "quantity", "labels"})
            self.assertIsNot(order, self.payload)
            self.assertIsNot(order["labels"], self.payload["labels"])
            order["quantity"] = 3
            order["labels"].append("reviewed")

        record = process(self.store, self.payload, hook)

        self.assertEqual(record.quantity, 3)
        self.assertEqual(record.labels, ("new", "reviewed"))
        self.assertEqual(self.payload, {"sku": "widget", "quantity": 2, "labels": ["new"], "extra": extra})
        self.assertIs(self.payload["extra"], extra)

    def test_retained_hook_references_cannot_change_saved_record(self):
        retained = []

        def hook(order):
            order["labels"] = ["replacement"]
            retained.extend((order, order["labels"]))

        record = process(self.store, self.payload, hook)
        retained[0]["sku"] = "changed"
        retained[0]["quantity"] = 7
        retained[1].append("later")
        retained[0]["labels"] = []

        self.assertEqual((record.sku, record.quantity, record.labels), ("widget", 2, ("replacement",)))
        self.assertIs(record, self.store.rows[0])
        self.assertEqual(self.payload["labels"], ["new"])

    def test_empty_labels_are_valid_with_and_without_hook(self):
        for hook in (None, lambda order: None):
            with self.subTest(hook=hook):
                self.payload["labels"] = []
                self.assertEqual(process(self.store, self.payload, hook).labels, ())

    def test_invalid_external_payload_never_calls_hook_or_store(self):
        invalid = [None, [], {}, "order"]
        for field, values in (
            ("sku", (None, "", " \t", 1)),
            ("quantity", (None, 0, -1, True, 1.5, "2")),
            ("labels", (None, "new", ("new",), ["new", 1])),
        ):
            invalid.extend({**self.payload, field: value} for value in values)
        for field in self.payload:
            invalid.append({key: value for key, value in self.payload.items() if key != field})

        for payload in invalid:
            for with_hook in (False, True):
                with self.subTest(payload=payload, with_hook=with_hook):
                    store = Mock(spec=Store)
                    hook = Mock(return_value=None)
                    with self.assertRaises(OrderError):
                        process(store, payload, hook if with_hook else None)
                    hook.assert_not_called()
                    store.save.assert_not_called()

    def test_invalid_hook_changes_are_rejected_before_save(self):
        for field, values in (
            ("sku", (None, "", " \t", 1)),
            ("quantity", (None, 0, -1, True, 1.5, "2")),
            ("labels", (None, "new", ("new",), ["new", 1])),
        ):
            for value in values:
                with self.subTest(field=field, value=value):
                    store = Mock(spec=Store)

                    def hook(order):
                        order[field] = value

                    with self.assertRaises(OrderError) as caught:
                        process(store, self.payload, hook)
                    self.assertEqual(str(caught.exception), field)
                    store.save.assert_not_called()
                    self.assertEqual(self.payload, {"sku": "widget", "quantity": 2, "labels": ["new"]})

    def test_hook_deleting_required_fields_raises_order_error(self):
        for field in self.payload:
            with self.subTest(field=field):
                store = Mock(spec=Store)

                def hook(order):
                    del order[field]

                with self.assertRaises(OrderError) as caught:
                    process(store, self.payload, hook)
                self.assertEqual(str(caught.exception), field)
                store.save.assert_not_called()

    def test_in_place_invalid_label_change_leaves_input_untouched(self):
        def hook(order):
            order["labels"].append(1)

        store = Mock(spec=Store)
        with self.assertRaisesRegex(OrderError, "labels"):
            process(store, self.payload, hook)

        store.save.assert_not_called()
        self.assertEqual(self.payload["labels"], ["new"])

    def test_hook_exceptions_propagate_unchanged_without_saving(self):
        for error in (RuntimeError("hook failed"), KeyboardInterrupt()):
            with self.subTest(error=error):
                store = Mock(spec=Store)

                def hook(order):
                    order["quantity"] = 7
                    order["labels"].append("temporary")
                    raise error

                with self.assertRaises(type(error)) as caught:
                    process(store, self.payload, hook)

                self.assertIs(caught.exception, error)
                store.save.assert_not_called()
                self.assertEqual(self.payload, {"sku": "widget", "quantity": 2, "labels": ["new"]})


if __name__ == "__main__":
    unittest.main()
