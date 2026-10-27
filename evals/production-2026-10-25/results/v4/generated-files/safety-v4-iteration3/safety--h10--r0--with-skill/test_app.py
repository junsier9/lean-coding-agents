import unittest
from dataclasses import FrozenInstanceError
from unittest.mock import Mock, sentinel

from app import process
from domain import OrderError, Record, Store


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.payload = {"sku": "sku-1", "quantity": 2, "labels": ["new"]}
        self.store = Store()
        self.store.save = Mock(wraps=self.store.save)

    def test_without_hook_saves_an_independent_record(self):
        record = process(self.store, self.payload)

        self.assertEqual(record, Record("sku-1", 2, ("new",)))
        self.assertIs(self.store.rows[0], record)
        self.store.save.assert_called_once_with(record)
        self.payload["labels"].append("later")
        self.assertEqual(record.labels, ("new",))

    def test_empty_labels_are_valid_with_or_without_hook(self):
        for hook in (None, lambda owned: None):
            with self.subTest(hook=hook):
                record = process(self.store, {"sku": "sku-1", "quantity": 1, "labels": []}, hook)
                self.assertEqual(record, Record("sku-1", 1, ()))

    def test_invalid_input_never_calls_hook_or_store(self):
        invalid_payloads = [None, [], "order", {}]
        invalid_payloads += [
            {**self.payload, field: value}
            for field, value in (
                ("sku", ""), ("sku", " \t"), ("sku", None), ("sku", 1),
                ("quantity", 0), ("quantity", -1), ("quantity", True),
                ("quantity", False), ("quantity", 1.0), ("quantity", "1"),
                ("labels", None), ("labels", ("new",)), ("labels", "new"),
                ("labels", [1]), ("labels", [["new"]]),
            )
        ]
        invalid_payloads += [
            {key: value for key, value in self.payload.items() if key != field}
            for field in self.payload
        ]
        for payload in invalid_payloads:
            for use_hook in (False, True):
                with self.subTest(payload=payload, use_hook=use_hook):
                    hook = Mock() if use_hook else None
                    with self.assertRaises(OrderError):
                        process(self.store, payload, hook)
                    if hook is not None:
                        hook.assert_not_called()
                    self.store.save.assert_not_called()
                    self.assertEqual(self.store.rows, [])

    def test_hook_receives_only_independent_business_fields(self):
        self.payload["extra"] = {"private": True}

        def hook(owned):
            self.assertEqual(set(owned), {"sku", "quantity", "labels"})
            self.assertIsNot(owned, self.payload)
            self.assertIsNot(owned["labels"], self.payload["labels"])
            owned["quantity"] = 3
            owned["labels"].append("hook")

        record = process(self.store, self.payload, hook)

        self.assertEqual(record, Record("sku-1", 3, ("new", "hook")))
        self.assertEqual(self.payload, {
            "sku": "sku-1", "quantity": 2, "labels": ["new"],
            "extra": {"private": True},
        })
        self.store.save.assert_called_once_with(record)

    def test_invalid_hook_changes_are_rejected_before_saving(self):
        for field, value in (
            ("sku", ""), ("sku", " \t"), ("sku", None), ("sku", 1),
            ("quantity", 0), ("quantity", -1), ("quantity", True),
            ("quantity", False), ("quantity", 1.0), ("quantity", "1"),
            ("labels", None), ("labels", ("new",)), ("labels", "new"),
            ("labels", [1]), ("labels", [["new"]]),
        ):
            with self.subTest(field=field, value=value):
                def hook(owned):
                    owned[field] = value

                with self.assertRaisesRegex(OrderError, f"^{field}$"):
                    process(self.store, self.payload, hook)
                self.store.save.assert_not_called()
                self.assertEqual(self.store.rows, [])
                self.assertEqual(self.payload, {"sku": "sku-1", "quantity": 2, "labels": ["new"]})

    def test_hook_cannot_remove_required_fields(self):
        for field in self.payload:
            with self.subTest(field=field):
                def hook(owned):
                    del owned[field]

                with self.assertRaisesRegex(OrderError, f"^{field}$"):
                    process(self.store, self.payload, hook)
                self.store.save.assert_not_called()
                self.assertEqual(self.store.rows, [])

    def test_invalid_hook_mutation_does_not_change_input(self):
        def hook(owned):
            owned["labels"].append("hook")
            owned["quantity"] = 0

        with self.assertRaisesRegex(OrderError, "^quantity$"):
            process(self.store, self.payload, hook)
        self.assertEqual(self.payload, {"sku": "sku-1", "quantity": 2, "labels": ["new"]})
        self.store.save.assert_not_called()

    def test_hook_failures_propagate_unchanged_without_saving(self):
        for error in (RuntimeError("hook"), OrderError("plugin"), KeyboardInterrupt("cancelled")):
            with self.subTest(error=error):
                def hook(owned):
                    owned["labels"].append("hook")
                    raise error

                with self.assertRaises(type(error)) as caught:
                    process(self.store, self.payload, hook)
                self.assertIs(caught.exception, error)
                self.assertEqual(self.payload, {"sku": "sku-1", "quantity": 2, "labels": ["new"]})
                self.store.save.assert_not_called()
                self.assertEqual(self.store.rows, [])

    def test_retained_hook_references_cannot_change_saved_record(self):
        retained = []
        record = process(self.store, self.payload, retained.append)
        owned = retained[0]

        owned["labels"].append("later")
        owned["labels"] = ["replacement"]
        owned["sku"] = "changed"
        owned["quantity"] = 100

        self.assertEqual(record, Record("sku-1", 2, ("new",)))
        self.assertIs(self.store.rows[0], record)
        self.assertEqual(self.payload, {"sku": "sku-1", "quantity": 2, "labels": ["new"]})

    def test_saved_record_is_immutable(self):
        record = process(self.store, self.payload)

        with self.assertRaises(FrozenInstanceError):
            record.quantity = 3
        with self.assertRaises(TypeError):
            record.labels[0] = "changed"

    def test_store_failure_propagates_unchanged(self):
        error = RuntimeError("save")
        self.store.save.side_effect = error

        with self.assertRaises(RuntimeError) as caught:
            process(self.store, self.payload)
        self.assertIs(caught.exception, error)
        self.assertEqual(self.store.rows, [])

    def test_process_preserves_store_return_value(self):
        self.store.save.return_value = sentinel.saved

        self.assertIs(process(self.store, self.payload), sentinel.saved)
        self.store.save.assert_called_once_with(Record("sku-1", 2, ("new",)))


if __name__ == "__main__":
    unittest.main()
