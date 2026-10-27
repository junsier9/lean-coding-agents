import unittest
from dataclasses import FrozenInstanceError
from unittest.mock import Mock

from app import process
from domain import OrderError, Record, Store


class ProcessTests(unittest.TestCase):
    def test_without_hook_saves_immutable_record(self):
        store = Store()
        payload = {"sku": "item", "quantity": 2, "labels": ["new"]}

        record = process(store, payload)

        self.assertEqual(record, Record("item", 2, ("new",)))
        self.assertEqual(store.rows, [record])
        self.assertIs(store.rows[0], record)
        self.assertEqual(payload, {"sku": "item", "quantity": 2, "labels": ["new"]})
        payload["labels"].append("later")
        self.assertEqual(record.labels, ("new",))
        with self.assertRaises(FrozenInstanceError):
            record.quantity = 3

    def test_invalid_input_never_calls_hook_or_store(self):
        invalid = [(None, "payload"), ([], "payload")]
        invalid_fields = {
            "sku": [None, "", " \t", 123],
            "quantity": [None, True, False, 0, -1, 1.5, "2"],
            "labels": [None, "new", ("new",), ["new", 1]],
        }
        for field, values in invalid_fields.items():
            missing = {"sku": "item", "quantity": 2, "labels": ["new"]}
            del missing[field]
            invalid.append((missing, field))
            for value in values:
                payload = {"sku": "item", "quantity": 2, "labels": ["new"]}
                payload[field] = value
                invalid.append((payload, field))

        for payload, error in invalid:
            for with_hook in (False, True):
                with self.subTest(payload=payload, with_hook=with_hook):
                    store = Mock(spec=Store)
                    hook = Mock() if with_hook else None

                    with self.assertRaisesRegex(OrderError, f"^{error}$"):
                        process(store, payload, hook)

                    store.save.assert_not_called()
                    if hook is not None:
                        hook.assert_not_called()

    def test_hook_gets_only_independent_business_fields(self):
        store = Store()
        extra = {"private": [1]}
        payload = {"sku": "item", "quantity": 2, "labels": ["new"], "extra": extra}
        retained = []

        def hook(owned):
            self.assertEqual(set(owned), {"sku", "quantity", "labels"})
            self.assertIsNot(owned, payload)
            self.assertIsNot(owned["labels"], payload["labels"])
            retained.append(owned)
            owned["sku"] = "changed"
            owned["quantity"] = 3
            owned["labels"].append("checked")

        record = process(store, payload, hook)

        self.assertEqual(record, Record("changed", 3, ("new", "checked")))
        self.assertEqual(store.rows, [record])
        self.assertEqual(payload, {
            "sku": "item", "quantity": 2, "labels": ["new"], "extra": extra,
        })
        self.assertIs(payload["extra"], extra)
        retained[0]["sku"] = "later"
        retained[0]["quantity"] = 99
        retained[0]["labels"].append("later")
        retained[0].clear()
        self.assertEqual(store.rows, [Record("changed", 3, ("new", "checked"))])

    def test_hook_can_replace_labels_with_valid_list(self):
        store = Store()
        payload = {"sku": "item", "quantity": 1, "labels": []}
        labels = ["replacement"]

        def hook(owned):
            owned["labels"] = labels

        record = process(store, payload, hook)
        labels.clear()

        self.assertEqual(record.labels, ("replacement",))
        self.assertEqual(payload["labels"], [])

    def test_invalid_hook_output_is_rejected_before_save(self):
        invalid_fields = {
            "sku": [None, "", " \t", 123],
            "quantity": [None, True, False, 0, -1, 1.5, "2"],
            "labels": [None, "new", ("new",), ["new", 1]],
        }
        for field, values in invalid_fields.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    store = Mock(spec=Store)
                    payload = {"sku": "item", "quantity": 2, "labels": ["new"]}

                    def hook(owned):
                        owned[field] = value

                    with self.assertRaisesRegex(OrderError, f"^{field}$"):
                        process(store, payload, hook)

                    store.save.assert_not_called()
                    self.assertEqual(payload, {
                        "sku": "item", "quantity": 2, "labels": ["new"],
                    })

    def test_hook_cannot_remove_required_fields(self):
        for field in ("sku", "quantity", "labels"):
            with self.subTest(field=field):
                store = Mock(spec=Store)
                payload = {"sku": "item", "quantity": 2, "labels": ["new"]}

                def hook(owned):
                    del owned[field]

                with self.assertRaisesRegex(OrderError, f"^{field}$"):
                    process(store, payload, hook)

                store.save.assert_not_called()

    def test_hook_exception_propagates_without_saving_or_changing_input(self):
        for error in (RuntimeError("hook failed"), KeyboardInterrupt("cancelled")):
            with self.subTest(error=type(error)):
                store = Mock(spec=Store)
                payload = {"sku": "item", "quantity": 2, "labels": ["new"]}

                def hook(owned):
                    owned["quantity"] = 3
                    owned["labels"].append("changed")
                    raise error

                with self.assertRaises(type(error)) as caught:
                    process(store, payload, hook)

                self.assertIs(caught.exception, error)
                store.save.assert_not_called()
                self.assertEqual(payload, {
                    "sku": "item", "quantity": 2, "labels": ["new"],
                })

    def test_save_failure_propagates(self):
        store = Mock(spec=Store)
        error = OSError("save failed")
        store.save.side_effect = error

        with self.assertRaises(OSError) as caught:
            process(store, {"sku": "item", "quantity": 1, "labels": []})

        self.assertIs(caught.exception, error)
        store.save.assert_called_once_with(Record("item", 1, ()))


if __name__ == "__main__":
    unittest.main()
