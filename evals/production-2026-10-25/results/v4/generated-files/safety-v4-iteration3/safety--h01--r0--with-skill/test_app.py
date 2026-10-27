import json
import unittest
from dataclasses import FrozenInstanceError

from app import import_inventory
from domain import InventoryError, Manifest, Row, validate_rows


class ImportInventoryTests(unittest.TestCase):
    def test_valid_inventory_preserves_order_and_values(self):
        manifest = import_inventory(
            '[{"sku": "B", "quantity": 3}, {"sku": " A ", "quantity": 2}]'
        )

        self.assertEqual(manifest, Manifest((Row("B", 3), Row(" A ", 2)), 5))

    def test_empty_inventory_is_valid(self):
        self.assertEqual(import_inventory("[]"), Manifest((), 0))

    def test_manifest_and_rows_are_immutable(self):
        manifest = import_inventory('[{"sku": "A", "quantity": 1}]')

        self.assertIsInstance(manifest.rows, tuple)
        with self.assertRaises(FrozenInstanceError):
            manifest.total = 2
        with self.assertRaises(FrozenInstanceError):
            manifest.rows = ()
        with self.assertRaises(FrozenInstanceError):
            manifest.rows[0].quantity = 2

    def test_injected_validator_runs_once_and_its_data_is_preserved(self):
        for payload in ([], [{"sku": "B", "quantity": 3}, {"sku": "A", "quantity": 2}]):
            with self.subTest(payload=payload):
                calls = []
                checked = validate_rows(payload)

                def validator(data):
                    calls.append(data)
                    return checked

                manifest = import_inventory(json.dumps(payload), validator=validator)

                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0], payload)
                self.assertIs(manifest.rows, checked)
                self.assertEqual(manifest.total, sum(row.quantity for row in checked))

    def test_invalid_json_does_not_call_validator(self):
        for text in ("", "[", "not JSON", None):
            with self.subTest(text=text):
                calls = []

                def validator(data):
                    calls.append(data)
                    return ()

                with self.assertRaisesRegex(InventoryError, "^invalid JSON$"):
                    import_inventory(text, validator)
                self.assertEqual(calls, [])

    def test_invalid_shapes_are_rejected(self):
        for payload in ({}, None, True, 1, "rows", [None], [[]], [1]):
            with self.subTest(payload=payload):
                with self.assertRaises(InventoryError):
                    import_inventory(json.dumps(payload))

    def test_invalid_fields_are_rejected(self):
        invalid_rows = [{"quantity": 1}, {"sku": "A"}]
        invalid_rows.extend(
            {"sku": sku, "quantity": 1} for sku in (None, "", " \t", 1, True)
        )
        invalid_rows.extend(
            {"sku": "A", "quantity": quantity}
            for quantity in (None, True, False, 0, -1, 1.0, "1")
        )
        for row in invalid_rows:
            with self.subTest(row=row):
                with self.assertRaises(InventoryError):
                    import_inventory(json.dumps([row]))

    def test_duplicate_skus_are_rejected(self):
        with self.assertRaisesRegex(InventoryError, "^duplicate sku$"):
            import_inventory('[{"sku": "A", "quantity": 1}, {"sku": "A", "quantity": 2}]')

    def test_validator_errors_propagate_without_retry(self):
        for error in (InventoryError("lookup rejected"), RuntimeError("lookup failed")):
            with self.subTest(error=error):
                calls = []

                def validator(data):
                    calls.append(data)
                    raise error

                with self.assertRaises(type(error)) as raised:
                    import_inventory("[]", validator=validator)
                self.assertIs(raised.exception, error)
                self.assertEqual(calls, [[]])


if __name__ == "__main__":
    unittest.main()
