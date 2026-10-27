import copy
import json
import unittest
from dataclasses import FrozenInstanceError
from unittest.mock import Mock

from app import import_inventory
from domain import InventoryError, Manifest, Row, validate_rows


class ImportInventoryTests(unittest.TestCase):
    def test_preserves_order_total_and_immutability(self):
        manifest = import_inventory(
            '[{"sku": "B", "quantity": 2}, {"sku": "A", "quantity": 3}]'
        )

        self.assertEqual(manifest, Manifest((Row("B", 2), Row("A", 3)), 5))
        with self.assertRaises(FrozenInstanceError):
            manifest.total = 0
        with self.assertRaises(FrozenInstanceError):
            manifest.rows[0].quantity = 0
        with self.assertRaises(TypeError):
            manifest.rows[0] = Row("C", 1)

    def test_empty_array_is_valid(self):
        self.assertEqual(import_inventory("[]"), Manifest((), 0))

    def test_validator_runs_once_and_its_data_is_unchanged(self):
        payload = [
            {"sku": "B", "quantity": 2},
            {"sku": "A", "quantity": 3},
        ]
        for original in (payload, []):
            with self.subTest(payload=original):
                received = []
                snapshots = []
                validated = []

                def validator(rows):
                    received.append(rows)
                    snapshots.append(copy.deepcopy(rows))
                    checked = validate_rows(rows)
                    validated.append(checked)
                    return checked

                spy = Mock(side_effect=validator)
                manifest = import_inventory(json.dumps(original), validator=spy)

                spy.assert_called_once()
                self.assertEqual(received[0], original)
                self.assertEqual(received[0], snapshots[0])
                self.assertIs(manifest.rows, validated[0])
                self.assertEqual(manifest.total, sum(row.quantity for row in validated[0]))

    def test_invalid_fields_raise_inventory_error(self):
        invalid_payloads = [
            [{"quantity": 1}],
            [{"sku": None, "quantity": 1}],
            [{"sku": "", "quantity": 1}],
            [{"sku": "  ", "quantity": 1}],
            [{"sku": 1, "quantity": 1}],
            [{"sku": "A"}],
            [{"sku": "A", "quantity": None}],
            [{"sku": "A", "quantity": 0}],
            [{"sku": "A", "quantity": -1}],
            [{"sku": "A", "quantity": True}],
            [{"sku": "A", "quantity": False}],
            [{"sku": "A", "quantity": 1.0}],
            [{"sku": "A", "quantity": "1"}],
            [{"sku": "A", "quantity": 1}, {"sku": "A", "quantity": 2}],
            [None],
            [[]],
            [1],
            ["A"],
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(InventoryError):
                    import_inventory(json.dumps(payload))

    def test_non_array_json_raises_inventory_error(self):
        for text in ('{}', 'null', '1', 'true', '"rows"'):
            with self.subTest(text=text):
                with self.assertRaises(InventoryError):
                    import_inventory(text)

    def test_parse_errors_are_translated_before_validation(self):
        for text in ("", "[", "[{]", None, 1):
            with self.subTest(text=text):
                validator = Mock()
                with self.assertRaisesRegex(InventoryError, "invalid JSON") as error:
                    import_inventory(text, validator)
                self.assertIsInstance(error.exception.__cause__, (ValueError, TypeError))
                validator.assert_not_called()

    def test_validator_errors_propagate_without_retry(self):
        for error in (InventoryError("lookup rejected"), RuntimeError("lookup failed")):
            with self.subTest(error=error):
                validator = Mock(side_effect=error)
                with self.assertRaises(type(error)) as raised:
                    import_inventory("[]", validator=validator)
                self.assertIs(raised.exception, error)
                validator.assert_called_once_with([])


if __name__ == "__main__":
    unittest.main()
