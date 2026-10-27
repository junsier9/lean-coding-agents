import json
import unittest
from dataclasses import FrozenInstanceError
from unittest.mock import Mock

from app import import_inventory
from domain import InventoryError, Manifest, Row


class ImportInventoryTests(unittest.TestCase):
    def test_preserves_row_order_and_computes_total(self):
        manifest = import_inventory(
            '[{"sku": "B", "quantity": 2}, {"sku": "A", "quantity": 3}]'
        )

        self.assertEqual(manifest, Manifest((Row("B", 2), Row("A", 3)), 5))
        self.assertIsInstance(manifest.rows, tuple)

    def test_empty_inventory_is_valid(self):
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

    def test_uses_trusted_validator_once_without_modifying_its_input(self):
        payload = [
            {"sku": "B", "quantity": 2, "metadata": {"tags": ["keep"]}},
            {"sku": "A", "quantity": 3},
        ]
        rows = (Row("B", 2), Row("A", 3))
        validator = Mock(return_value=rows)

        manifest = import_inventory(json.dumps(payload), validator=validator)

        validator.assert_called_once_with(payload)
        self.assertIs(manifest.rows, rows)
        self.assertEqual(manifest.total, 5)

    def test_empty_inventory_calls_validator_once(self):
        validator = Mock(return_value=())

        self.assertEqual(import_inventory("[]", validator=validator), Manifest((), 0))

        validator.assert_called_once_with([])

    def test_validator_error_is_propagated_after_one_call(self):
        error = InventoryError("lookup failed")
        validator = Mock(side_effect=error)

        with self.assertRaises(InventoryError) as caught:
            import_inventory("[]", validator=validator)

        self.assertIs(caught.exception, error)
        validator.assert_called_once_with([])

    def test_invalid_json_never_calls_validator(self):
        for text in ("", "[", '[{"sku": "A",}]', None, 42):
            with self.subTest(text=text):
                validator = Mock()
                with self.assertRaises(InventoryError):
                    import_inventory(text, validator=validator)
                validator.assert_not_called()

    def test_rejects_non_array_payloads(self):
        for payload in (None, {}, "inventory", 1, True):
            with self.subTest(payload=payload):
                with self.assertRaises(InventoryError):
                    import_inventory(json.dumps(payload))

    def test_rejects_non_object_rows(self):
        for item in (None, [], "A", 1, True):
            with self.subTest(item=item):
                with self.assertRaises(InventoryError):
                    import_inventory(json.dumps([item]))

    def test_rejects_missing_or_invalid_sku(self):
        items = [{"quantity": 1}]
        items.extend(
            {"sku": sku, "quantity": 1}
            for sku in (None, "", " \t\n", 1, True, [], {})
        )
        for item in items:
            with self.subTest(item=item):
                with self.assertRaises(InventoryError):
                    import_inventory(json.dumps([item]))

    def test_rejects_missing_or_non_positive_integer_quantity(self):
        items = [{"sku": "A"}]
        items.extend(
            {"sku": "A", "quantity": quantity}
            for quantity in (None, 0, -1, 1.0, 1.5, True, False, "1", [], {})
        )
        for item in items:
            with self.subTest(item=item):
                with self.assertRaises(InventoryError):
                    import_inventory(json.dumps([item]))

    def test_rejects_duplicate_sku(self):
        with self.assertRaises(InventoryError):
            import_inventory(
                '[{"sku": "A", "quantity": 1}, {"sku": "A", "quantity": 2}]'
            )


if __name__ == "__main__":
    unittest.main()
