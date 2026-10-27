import unittest
from dataclasses import FrozenInstanceError
from unittest.mock import Mock

from app import process
from domain import OrderError, Record, Store


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.payload = {"sku": "ABC", "quantity": 2, "labels": ["new"]}

    def test_without_hook_saves_immutable_record(self):
        store = Store()

        record = process(store, self.payload)

        self.assertEqual(record, Record("ABC", 2, ("new",)))
        self.assertIs(store.rows[0], record)
        self.assertEqual(len(store.rows), 1)
        self.payload["labels"].append("later")
        self.payload["quantity"] = 99
        self.assertEqual(record, Record("ABC", 2, ("new",)))
        with self.assertRaises(FrozenInstanceError):
            record.quantity = 99

    def test_hook_receives_only_independent_business_fields(self):
        store = Store()
        self.payload["metadata"] = {"private": True}
        received = []

        def hook(owned):
            received.append(owned)
            self.assertEqual(set(owned), {"sku", "quantity", "labels"})
            self.assertIsNot(owned, self.payload)
            self.assertIsNot(owned["labels"], self.payload["labels"])
            owned["quantity"] += 1
            owned["labels"].append("hooked")

        record = process(store, self.payload, hook)

        self.assertEqual(len(received), 1)
        self.assertEqual(record, Record("ABC", 3, ("new", "hooked")))
        self.assertEqual(
            self.payload,
            {"sku": "ABC", "quantity": 2, "labels": ["new"],
             "metadata": {"private": True}},
        )
        self.assertIs(store.rows[0], record)

        retained = received[0]
        retained["labels"].append("later")
        retained["labels"] = ["replacement"]
        retained["sku"] = "changed"
        retained["quantity"] = 99
        self.assertEqual(store.rows, [Record("ABC", 3, ("new", "hooked"))])

    def test_hook_can_replace_labels_with_a_valid_list(self):
        store = Store()
        labels = ["replacement"]

        def hook(owned):
            owned["labels"] = labels

        record = process(store, self.payload, hook)
        labels.clear()

        self.assertEqual(record, Record("ABC", 2, ("replacement",)))
        self.assertEqual(self.payload["labels"], ["new"])

    def test_empty_labels_and_positive_quantity_are_valid_on_both_paths(self):
        for hook in (None, Mock(return_value=None)):
            with self.subTest(hook=hook):
                store = Store()
                record = process(
                    store, {"sku": "A", "quantity": 1, "labels": []}, hook
                )
                self.assertEqual(record, Record("A", 1, ()))
                self.assertEqual(store.rows, [record])

    def test_invalid_payload_never_calls_hook_or_store(self):
        invalid_payloads = [None, [], "order", 123, {}]
        invalid_fields = [
            ("sku", None), ("sku", ""), ("sku", "  "), ("sku", 123),
            ("quantity", None), ("quantity", 0), ("quantity", -1),
            ("quantity", True), ("quantity", 1.0), ("quantity", "2"),
            ("labels", None), ("labels", "new"), ("labels", ("new",)),
            ("labels", ["new", 1]),
        ]
        invalid_payloads.extend(
            dict(self.payload, **{field: value}) for field, value in invalid_fields
        )
        for field in self.payload:
            missing = dict(self.payload)
            del missing[field]
            invalid_payloads.append(missing)

        for payload in invalid_payloads:
            for with_hook in (False, True):
                with self.subTest(payload=payload, with_hook=with_hook):
                    store = Mock(spec=Store)
                    hook = Mock(return_value=None)
                    with self.assertRaises(OrderError):
                        process(store, payload, hook if with_hook else None)
                    hook.assert_not_called()
                    store.save.assert_not_called()

    def test_invalid_hook_changes_raise_order_error_without_saving(self):
        invalid_fields = [
            ("sku", None), ("sku", ""), ("sku", "  "), ("sku", 123),
            ("quantity", None), ("quantity", 0), ("quantity", -1),
            ("quantity", True), ("quantity", 1.0), ("quantity", "2"),
            ("labels", None), ("labels", "new"), ("labels", ("new",)),
            ("labels", ["new", 1]),
        ]
        for field, value in invalid_fields:
            with self.subTest(field=field, value=value):
                store = Mock(spec=Store)

                def hook(owned):
                    owned[field] = value

                with self.assertRaisesRegex(OrderError, f"^{field}$"):
                    process(store, self.payload, hook)
                store.save.assert_not_called()
                self.assertEqual(
                    self.payload, {"sku": "ABC", "quantity": 2, "labels": ["new"]}
                )

    def test_hook_deleting_required_fields_raises_order_error(self):
        for field in self.payload:
            with self.subTest(field=field):
                store = Mock(spec=Store)

                def hook(owned):
                    del owned[field]

                with self.assertRaisesRegex(OrderError, f"^{field}$"):
                    process(store, self.payload, hook)
                store.save.assert_not_called()
                self.assertIn(field, self.payload)

    def test_in_place_invalid_label_change_is_revalidated(self):
        store = Mock(spec=Store)

        def hook(owned):
            owned["labels"].append(123)

        with self.assertRaisesRegex(OrderError, "^labels$"):
            process(store, self.payload, hook)
        store.save.assert_not_called()
        self.assertEqual(self.payload["labels"], ["new"])

    def test_hook_exception_propagates_unchanged_without_saving(self):
        store = Mock(spec=Store)
        failure = RuntimeError("hook failed")

        def hook(owned):
            owned["quantity"] = 99
            owned["labels"].append("changed")
            raise failure

        with self.assertRaises(RuntimeError) as raised:
            process(store, self.payload, hook)

        self.assertIs(raised.exception, failure)
        store.save.assert_not_called()
        self.assertEqual(
            self.payload, {"sku": "ABC", "quantity": 2, "labels": ["new"]}
        )


if __name__ == "__main__":
    unittest.main()
