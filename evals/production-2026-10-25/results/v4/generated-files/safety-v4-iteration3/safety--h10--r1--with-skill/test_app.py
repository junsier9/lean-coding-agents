import unittest
from dataclasses import FrozenInstanceError
from unittest.mock import Mock

from app import process
from domain import OrderError, Record, Store


def payload(**changes):
    values = {"sku": "item-1", "quantity": 2, "labels": ["new"]}
    values.update(changes)
    return values


class ProcessTests(unittest.TestCase):
    def test_without_hook_saves_immutable_record(self):
        store = Store()
        source = payload()

        record = process(store, source)

        self.assertEqual(record, Record("item-1", 2, ("new",)))
        self.assertEqual(store.rows, [record])
        self.assertIs(record, store.rows[0])
        with self.assertRaises(FrozenInstanceError):
            record.quantity = 3
        source["labels"].append("later")
        self.assertEqual(record.labels, ("new",))

    def test_hook_gets_only_independent_business_fields(self):
        source = payload(extra={"private": []})
        retained = []

        def hook(owned):
            self.assertEqual(set(owned), {"sku", "quantity", "labels"})
            self.assertIsNot(owned, source)
            self.assertIsNot(owned["labels"], source["labels"])
            owned["quantity"] = 3
            owned["labels"].append("hook")
            retained.append(owned)

        store = Store()
        record = process(store=store, payload=source, hook=hook)

        self.assertEqual(record, Record("item-1", 3, ("new", "hook")))
        self.assertEqual(source, payload(extra={"private": []}))
        retained[0]["labels"].append("later")
        retained[0]["quantity"] = 99
        retained[0].clear()
        self.assertEqual(store.rows, [Record("item-1", 3, ("new", "hook"))])

    def test_hook_can_replace_labels_with_a_valid_list(self):
        labels = ["replacement"]

        def hook(owned):
            owned["labels"] = labels

        record = process(Store(), payload(), hook)
        labels.append("later")
        self.assertEqual(record.labels, ("replacement",))

    def test_invalid_input_does_not_call_hook_or_save(self):
        invalid = [
            ("payload", None),
            ("payload", []),
            ("sku", {}),
            ("sku", payload(sku="")),
            ("sku", payload(sku="  ")),
            ("sku", payload(sku=1)),
            ("quantity", payload(quantity=0)),
            ("quantity", payload(quantity=-1)),
            ("quantity", payload(quantity=True)),
            ("quantity", payload(quantity=1.0)),
            ("quantity", payload(quantity="2")),
            ("labels", payload(labels=None)),
            ("labels", payload(labels=())),
            ("labels", payload(labels="new")),
            ("labels", payload(labels=["new", 1])),
        ]
        for field, source in invalid:
            with self.subTest(field=field, source=source):
                hook = Mock()
                store = Mock(spec=Store)
                with self.assertRaisesRegex(OrderError, "^" + field + "$"):
                    process(store, source, hook)
                hook.assert_not_called()
                store.save.assert_not_called()

    def test_invalid_hook_output_does_not_save_or_change_source(self):
        invalid = [
            ("sku", None),
            ("sku", "  "),
            ("quantity", 0),
            ("quantity", -1),
            ("quantity", True),
            ("quantity", 1.0),
            ("quantity", "3"),
            ("labels", None),
            ("labels", ("tuple",)),
            ("labels", "text"),
            ("labels", ["new", 1]),
        ]
        for field, value in invalid:
            with self.subTest(field=field, value=value):
                source = payload()
                store = Mock(spec=Store)

                def hook(owned):
                    owned["labels"].append("hook")
                    owned[field] = value

                with self.assertRaisesRegex(OrderError, "^" + field + "$"):
                    process(store, source, hook)
                store.save.assert_not_called()
                self.assertEqual(source, payload())

    def test_hook_deleting_a_required_field_raises_order_error(self):
        for field in ("sku", "quantity", "labels"):
            with self.subTest(field=field):
                store = Mock(spec=Store)

                def hook(owned):
                    del owned[field]

                with self.assertRaisesRegex(OrderError, "^" + field + "$"):
                    process(store, payload(), hook)
                store.save.assert_not_called()

    def test_hook_failure_propagates_without_saving_or_changing_source(self):
        for failure in (RuntimeError("hook failed"), KeyboardInterrupt()):
            with self.subTest(failure=type(failure).__name__):
                source = payload()
                store = Mock(spec=Store)

                def hook(owned):
                    owned["labels"].append("hook")
                    owned["quantity"] = 3
                    raise failure

                with self.assertRaises(type(failure)) as raised:
                    process(store, source, hook)
                self.assertIs(raised.exception, failure)
                store.save.assert_not_called()
                self.assertEqual(source, payload())

    def test_empty_labels_are_valid(self):
        self.assertEqual(process(Store(), payload(labels=[])).labels, ())

    def test_returns_store_result(self):
        store = Mock(spec=Store)
        result = process(store, payload())
        store.save.assert_called_once_with(Record("item-1", 2, ("new",)))
        self.assertIs(result, store.save.return_value)

    def test_store_failure_propagates(self):
        failure = RuntimeError("save failed")
        store = Mock(spec=Store)
        store.save.side_effect = failure
        with self.assertRaises(RuntimeError) as raised:
            process(store, payload())
        self.assertIs(raised.exception, failure)
        store.save.assert_called_once()


if __name__ == "__main__":
    unittest.main()
