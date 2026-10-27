import json
import unittest
from copy import deepcopy
from dataclasses import FrozenInstanceError
from unittest.mock import Mock

from app import import_inventory
from domain import InventoryError, Manifest, Row, validate_rows


class ImportInventoryTests(unittest.TestCase):
    def test_valid_inventory_preserves_order_and_total(self):
        manifest = import_inventory(
            '[{"sku": "B", "quantity": 2}, {"sku": " A ", "quantity": 3}]'
        )

        self.assertEqual(manifest, Manifest((Row("B", 2), Row(" A ", 3)), 5))
        self.assertIsInstance(manifest.rows, tuple)

    def test_empty_inventory(self):
        self.assertEqual(import_inventory("[]"), Manifest((), 0))

    def test_manifest_and_rows_are_immutable(self):
        manifest = import_inventory('[{"sku": "A", "quantity": 1}]')

        with self.assertRaises(FrozenInstanceError):
            manifest.total = 2
        with self.assertRaises(FrozenInstanceError):
            manifest.rows = ()
        with self.assertRaises(FrozenInstanceError):
            manifest.rows[0].quantity = 2
        with self.assertRaises(TypeError):
            manifest.rows[0] = Row("B", 2)

    def test_trusted_validator_called_once_and_payload_not_modified(self):
        for payload in (
            [],
            [
                {"sku": "B", "quantity": 2, "metadata": {"tags": ["keep"]}},
                {"sku": "A", "quantity": 3},
            ],
        ):
            with self.subTest(payload=payload):
                observed = {}

                def validate_once(received):
                    self.assertNotIn("payload", observed, "validator called again")
                    observed["payload"] = received
                    observed["snapshot"] = deepcopy(received)
                    observed["checked"] = validate_rows(received)
                    return observed["checked"]

                validator = Mock(side_effect=validate_once)
                manifest = import_inventory(json.dumps(payload), validator=validator)

                validator.assert_called_once()
                self.assertEqual(observed["payload"], payload)
                self.assertEqual(observed["payload"], observed["snapshot"])
                self.assertIs(manifest.rows, observed["checked"])
                self.assertEqual(manifest.total, sum(row["quantity"] for row in payload))

    def test_invalid_json_does_not_call_validator(self):
        for text in ("", "[", '[{"sku": "A", "quantity": 1},]', None, 1):
            with self.subTest(text=text):
                validator = Mock()
                with self.assertRaises(InventoryError):
                    import_inventory(text, validator=validator)
                validator.assert_not_called()

    def test_non_array_payloads_are_rejected(self):
        for payload in (None, {}, "inventory", 1, 1.5, True, False):
            with self.subTest(payload=payload):
                with self.assertRaises(InventoryError):
                    import_inventory(json.dumps(payload))

    def test_invalid_row_shapes_are_rejected(self):
        for row in (None, [], "A", 1, True):
            with self.subTest(row=row):
                with self.assertRaises(InventoryError):
                    import_inventory(json.dumps([row]))

    def test_missing_fields_are_rejected(self):
        for row in ({}, {"sku": "A"}, {"quantity": 1}):
            with self.subTest(row=row):
                with self.assertRaises(InventoryError):
                    import_inventory(json.dumps([row]))

    def test_invalid_skus_are_rejected(self):
        for sku in (None, "", " \t\n", 1, True, [], {}):
            with self.subTest(sku=sku):
                with self.assertRaises(InventoryError):
                    import_inventory(json.dumps([{"sku": sku, "quantity": 1}]))

    def test_quantity_requires_strictly_positive_integer(self):
        for quantity in (None, True, False, 0, -1, 1.0, 1.5, "1", [], {}):
            with self.subTest(quantity=quantity):
                with self.assertRaises(InventoryError):
                    import_inventory(json.dumps([{"sku": "A", "quantity": quantity}]))

    def test_large_positive_integer_is_preserved(self):
        quantity = 10**100
        manifest = import_inventory(json.dumps([{"sku": "A", "quantity": quantity}]))

        self.assertEqual(manifest, Manifest((Row("A", quantity),), quantity))

    def test_duplicate_sku_is_rejected(self):
        with self.assertRaises(InventoryError):
            import_inventory(
                '[{"sku": "A", "quantity": 1}, {"sku": "A", "quantity": 2}]'
            )

    def test_validator_errors_propagate_after_one_call(self):
        error = InventoryError("lookup failed")
        validator = Mock(side_effect=error)

        with self.assertRaises(InventoryError) as raised:
            import_inventory("[]", validator=validator)

        self.assertIs(raised.exception, error)
        validator.assert_called_once_with([])


if __name__ == "__main__":
    unittest.main()
