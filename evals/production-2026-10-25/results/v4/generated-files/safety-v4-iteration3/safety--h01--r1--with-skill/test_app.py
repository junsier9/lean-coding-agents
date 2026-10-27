import json
import unittest
from dataclasses import FrozenInstanceError
from unittest.mock import Mock

from app import import_inventory
from domain import InventoryError, Manifest, Row


class ImportInventoryTests(unittest.TestCase):
    def test_manifest_preserves_values_order_and_immutability(self):
        manifest = import_inventory(
            text='[{"sku": " B ", "quantity": 2}, {"sku": "A", "quantity": 3}]'
        )

        self.assertEqual(manifest, Manifest((Row(" B ", 2), Row("A", 3)), 5))
        self.assertIsInstance(manifest.rows, tuple)
        with self.assertRaises(FrozenInstanceError):
            manifest.total = 0
        with self.assertRaises(FrozenInstanceError):
            manifest.rows[0].quantity = 0
        with self.assertRaises(TypeError):
            manifest.rows[0] = Row("C", 1)

    def test_empty_array(self):
        self.assertEqual(import_inventory("[]"), Manifest((), 0))

    def test_invalid_json_is_translated_before_validation(self):
        for text in ("", "[", '{"sku":', None, 42):
            with self.subTest(text=text):
                validator = Mock()
                with self.assertRaises(InventoryError) as caught:
                    import_inventory(text, validator=validator)
                self.assertIsInstance(caught.exception.__cause__, (ValueError, TypeError))
                validator.assert_not_called()

    def test_non_array_json_is_rejected(self):
        for payload in (None, True, 1, "rows", {}, {"sku": "A", "quantity": 1}):
            with self.subTest(payload=payload):
                with self.assertRaises(InventoryError):
                    import_inventory(json.dumps(payload))

    def test_invalid_fields_and_row_types_are_rejected(self):
        invalid_rows = [None, True, 1, "row", []]
        invalid_rows += [
            {"sku": sku, "quantity": 1} for sku in (None, "", " \t", 1, True)
        ]
        invalid_rows += [
            {"sku": "A", "quantity": quantity}
            for quantity in (None, True, False, 0, -1, 1.0, 1.5, "2")
        ]
        invalid_rows += [{}, {"sku": "A"}, {"quantity": 1}]
        for row in invalid_rows:
            with self.subTest(row=row):
                with self.assertRaises(InventoryError):
                    import_inventory(json.dumps([row]))

    def test_duplicate_sku_is_rejected(self):
        with self.assertRaises(InventoryError):
            import_inventory('[{"sku": "A", "quantity": 1}, {"sku": "A", "quantity": 2}]')

    def test_injected_validator_is_called_once_without_mutating_input(self):
        for checked in ((), (Row("B", 2), Row("A", 3))):
            payload = [
                {"sku": row.sku, "quantity": row.quantity, "extra": {"tags": ["keep"]}}
                for row in checked
            ]
            for keyword in (False, True):
                with self.subTest(checked=checked, keyword=keyword):
                    validator = Mock(return_value=checked)
                    text = json.dumps(payload)
                    if keyword:
                        manifest = import_inventory(text=text, validator=validator)
                    else:
                        manifest = import_inventory(text, validator)

                    validator.assert_called_once_with(payload)
                    self.assertIs(manifest.rows, checked)
                    self.assertEqual(manifest.total, sum(row.quantity for row in checked))

    def test_validator_failures_propagate_unchanged_without_retry(self):
        for error in (InventoryError("invalid rows"), RuntimeError("lookup failed")):
            with self.subTest(error=error):
                validator = Mock(side_effect=error)
                with self.assertRaises(type(error)) as caught:
                    import_inventory("[]", validator=validator)
                self.assertIs(caught.exception, error)
                validator.assert_called_once_with([])


if __name__ == "__main__":
    unittest.main()
