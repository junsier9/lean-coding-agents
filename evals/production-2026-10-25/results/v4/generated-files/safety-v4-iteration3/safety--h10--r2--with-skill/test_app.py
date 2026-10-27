import unittest
from dataclasses import FrozenInstanceError
from unittest.mock import Mock

from app import process
from domain import OrderError, Record, Store


INVALID_FIELDS = (
    ("sku", ""),
    ("sku", "   "),
    ("sku", None),
    ("sku", 1),
    ("quantity", 0),
    ("quantity", -1),
    ("quantity", True),
    ("quantity", 1.5),
    ("quantity", "1"),
    ("quantity", None),
    ("labels", None),
    ("labels", "tag"),
    ("labels", ("tag",)),
    ("labels", ["tag", 1]),
)


class ProcessTests(unittest.TestCase):
    def test_no_hook_saves_immutable_record(self):
        payload = {"sku": "item", "quantity": 1, "labels": ["tag"]}
        store = Store()

        record = process(store, payload)

        self.assertEqual(record, Record("item", 1, ("tag",)))
        self.assertEqual(store.rows, [record])
        self.assertIs(store.rows[0], record)
        payload["labels"].append("later")
        self.assertEqual(record.labels, ("tag",))
        with self.assertRaises(FrozenInstanceError):
            record.quantity = 2

    def test_hook_owns_only_business_fields_and_cannot_change_saved_record(self):
        payload = {
            "sku": "item", "quantity": 1, "labels": ["tag"],
            "extra": {"private": True},
        }
        retained = []

        def hook(owned):
            self.assertEqual(set(owned), {"sku", "quantity", "labels"})
            self.assertIsNot(owned, payload)
            self.assertIsNot(owned["labels"], payload["labels"])
            owned["quantity"] = 2
            owned["labels"].append("hook")
            retained.append(owned)

        store = Store()
        record = process(store, payload, hook)

        self.assertEqual(record, Record("item", 2, ("tag", "hook")))
        self.assertIs(store.rows[0], record)
        self.assertEqual(payload, {
            "sku": "item", "quantity": 1, "labels": ["tag"],
            "extra": {"private": True},
        })
        retained[0]["labels"].append("later")
        retained[0]["quantity"] = 99
        retained[0]["sku"] = "changed"
        self.assertEqual(record, Record("item", 2, ("tag", "hook")))

    def test_hook_can_replace_labels_with_empty_list(self):
        payload = {"sku": "item", "quantity": 1, "labels": ["tag"]}

        def hook(owned):
            owned["labels"] = []

        record = process(Store(), payload, hook=hook)

        self.assertEqual(record, Record("item", 1, ()))
        self.assertEqual(payload["labels"], ["tag"])

    def test_invalid_input_does_not_call_hook_or_store(self):
        valid = {"sku": "item", "quantity": 1, "labels": ["tag"]}
        cases = [(None, "payload"), ([], "payload"), ({}, "sku")]
        cases.extend(({**valid, field: value}, field) for field, value in INVALID_FIELDS)
        for payload, field in cases:
            with self.subTest(payload=payload):
                store = Mock(spec=Store)
                hook = Mock()
                with self.assertRaisesRegex(OrderError, f"^{field}$"):
                    process(store, payload, hook)
                hook.assert_not_called()
                store.save.assert_not_called()

    def test_invalid_hook_changes_do_not_save_or_change_input(self):
        for field, value in INVALID_FIELDS:
            with self.subTest(field=field, value=value):
                payload = {"sku": "item", "quantity": 1, "labels": ["tag"]}
                store = Mock(spec=Store)

                def hook(owned):
                    owned["labels"].append("hook")
                    owned[field] = value

                with self.assertRaisesRegex(OrderError, f"^{field}$"):
                    process(store, payload, hook)
                store.save.assert_not_called()
                self.assertEqual(payload, {
                    "sku": "item", "quantity": 1, "labels": ["tag"],
                })

    def test_hook_removing_required_field_raises_order_error(self):
        for field in ("sku", "quantity", "labels"):
            with self.subTest(field=field):
                payload = {"sku": "item", "quantity": 1, "labels": []}
                store = Mock(spec=Store)

                def hook(owned):
                    del owned[field]

                with self.assertRaisesRegex(OrderError, f"^{field}$"):
                    process(store, payload, hook)
                store.save.assert_not_called()
                self.assertIn(field, payload)

    def test_hook_exception_propagates_unchanged_without_saving(self):
        payload = {"sku": "item", "quantity": 1, "labels": ["tag"]}
        store = Mock(spec=Store)
        failure = RuntimeError("plugin failed")

        def hook(owned):
            owned["labels"].append("hook")
            raise failure

        with self.assertRaises(RuntimeError) as caught:
            process(store, payload, hook)

        self.assertIs(caught.exception, failure)
        store.save.assert_not_called()
        self.assertEqual(payload, {"sku": "item", "quantity": 1, "labels": ["tag"]})


if __name__ == "__main__":
    unittest.main()
