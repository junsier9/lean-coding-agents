import copy
import unittest
from dataclasses import FrozenInstanceError
from unittest.mock import Mock

from app import import_inventory
from domain import InventoryError, Manifest, Row, validate_rows


class ImportInventoryTests(unittest.TestCase):
    def test_valid_inventory_preserves_order_and_total(self):
        manifest = import_inventory(
            '[{"sku": "second", "quantity": 2}, {"sku": "first", "quantity": 3}]'
        )

        self.assertEqual(
            manifest, Manifest((Row("second", 2), Row("first", 3)), 5)
        )
        self.assertIsInstance(manifest.rows, tuple)
        with self.assertRaises(FrozenInstanceError):
            manifest.total = 0
        with self.assertRaises(FrozenInstanceError):
            manifest.rows[0].quantity = 0

    def test_empty_inventory_is_valid(self):
        self.assertEqual(import_inventory("[]"), Manifest((), 0))

    def test_injected_validator_is_called_once_without_mutating_its_input(self):
        original = [
            {"sku": "second", "quantity": 2},
            {"sku": "first", "quantity": 3},
        ]
        received = []
        validated = []

        def validator(payload):
            self.assertIsInstance(payload, list)
            self.assertEqual(payload, original)
            received.append(payload)
            validated.append(validate_rows(payload))
            return validated[-1]

        spy = Mock(side_effect=validator)
        snapshot = copy.deepcopy(original)
        manifest = import_inventory(
            '[{"sku": "second", "quantity": 2}, {"sku": "first", "quantity": 3}]',
            validator=spy,
        )

        spy.assert_called_once()
        self.assertEqual(received[0], snapshot)
        self.assertIs(manifest.rows, validated[0])
        self.assertEqual(manifest.total, 5)

    def test_empty_inventory_still_calls_validator_once(self):
        validator = Mock(side_effect=validate_rows)

        self.assertEqual(import_inventory("[]", validator=validator), Manifest((), 0))

        validator.assert_called_once_with([])

    def test_invalid_json_is_wrapped_before_validation(self):
        for text in ("", "[", "[{]", "[] trailing", None, 42):
            with self.subTest(text=text):
                validator = Mock()
                with self.assertRaises(InventoryError) as caught:
                    import_inventory(text, validator=validator)
                self.assertIsNotNone(caught.exception.__cause__)
                validator.assert_not_called()

    def test_invalid_rows_raise_inventory_error(self):
        invalid_payloads = (
            "null",
            "{}",
            '"rows"',
            "1",
            "true",
            "[null]",
            "[[]]",
            '[{"quantity": 1}]',
            '[{"sku": null, "quantity": 1}]',
            '[{"sku": 1, "quantity": 1}]',
            '[{"sku": "", "quantity": 1}]',
            '[{"sku": "  ", "quantity": 1}]',
            '[{"sku": "a"}]',
            '[{"sku": "a", "quantity": null}]',
            '[{"sku": "a", "quantity": true}]',
            '[{"sku": "a", "quantity": false}]',
            '[{"sku": "a", "quantity": 0}]',
            '[{"sku": "a", "quantity": -1}]',
            '[{"sku": "a", "quantity": 1.0}]',
            '[{"sku": "a", "quantity": "1"}]',
            '[{"sku": "a", "quantity": 1}, {"sku": "a", "quantity": 2}]',
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(InventoryError):
                    import_inventory(payload)

    def test_validator_error_is_propagated_without_retry(self):
        error = InventoryError("lookup failed")
        validator = Mock(side_effect=error)

        with self.assertRaises(InventoryError) as caught:
            import_inventory("[]", validator=validator)

        self.assertIs(caught.exception, error)
        validator.assert_called_once_with([])


if __name__ == "__main__":
    unittest.main()
