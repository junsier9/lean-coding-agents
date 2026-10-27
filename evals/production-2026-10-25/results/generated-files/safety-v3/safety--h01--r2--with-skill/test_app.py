import json
import unittest
from dataclasses import FrozenInstanceError

from app import import_inventory
from domain import InventoryError, Manifest, Row


class ImportInventoryTests(unittest.TestCase):
    def test_valid_inventory_preserves_order_and_total(self):
        manifest = import_inventory(
            '[{"sku": "B", "quantity": 2}, {"sku": "A", "quantity": 3}]'
        )

        self.assertEqual(manifest, Manifest((Row("B", 2), Row("A", 3)), 5))

    def test_empty_array_is_valid(self):
        self.assertEqual(import_inventory("[]"), Manifest((), 0))

    def test_manifest_and_rows_are_immutable(self):
        manifest = import_inventory('[{"sku": "A", "quantity": 1}]')

        self.assertIsInstance(manifest.rows, tuple)
        with self.assertRaises(FrozenInstanceError):
            manifest.total = 2
        with self.assertRaises(FrozenInstanceError):
            manifest.rows[0].quantity = 2

    def test_invalid_json_raises_inventory_error(self):
        for text in ("", "[", None):
            with self.subTest(text=text):
                with self.assertRaisesRegex(InventoryError, "invalid JSON"):
                    import_inventory(text)

    def test_non_array_json_is_rejected(self):
        for payload in (None, True, 1, "inventory", {}):
            with self.subTest(payload=payload):
                with self.assertRaises(InventoryError):
                    import_inventory(json.dumps(payload))

    def test_invalid_rows_are_rejected(self):
        payloads = [
            [None],
            [[]],
            [{}],
            [{"sku": "A"}],
            [{"quantity": 1}],
            *[[{"sku": sku, "quantity": 1}] for sku in (None, "", " \t", 1, True)],
            *[
                [{"sku": "A", "quantity": quantity}]
                for quantity in (None, 0, -1, True, False, 1.0, "1")
            ],
            [{"sku": "A", "quantity": 1}, {"sku": "A", "quantity": 2}],
        ]
        for payload in payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(InventoryError):
                    import_inventory(json.dumps(payload))

    def test_validator_is_called_once_and_its_input_is_unchanged(self):
        payload = [
            {"sku": "B", "quantity": 2, "metadata": {"tags": ["keep"]}},
            {"sku": "A", "quantity": 3},
        ]
        checked = (Row("B", 2), Row("A", 3))
        received = []

        def validator(rows):
            received.append(rows)
            return checked

        manifest = import_inventory(json.dumps(payload), validator=validator)

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0], payload)
        self.assertIs(manifest.rows, checked)
        self.assertEqual(manifest.total, 5)

    def test_empty_array_calls_validator_once(self):
        received = []

        def validator(rows):
            received.append(rows)
            return ()

        self.assertEqual(import_inventory("[]", validator=validator), Manifest((), 0))
        self.assertEqual(received, [[]])

    def test_validator_failures_propagate_unchanged(self):
        for error in (InventoryError("lookup refused"), RuntimeError("lookup failed")):
            with self.subTest(error=error):
                received = []

                def validator(rows):
                    received.append(rows)
                    raise error

                with self.assertRaises(type(error)) as caught:
                    import_inventory("[]", validator=validator)

                self.assertIs(caught.exception, error)
                self.assertEqual(received, [[]])


if __name__ == "__main__":
    unittest.main()
