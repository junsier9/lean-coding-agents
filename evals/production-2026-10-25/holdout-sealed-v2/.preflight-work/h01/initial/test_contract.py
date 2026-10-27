import dataclasses
import json
import unittest
from app import import_inventory
from domain import InventoryError, Row, validate_rows

class Contract(unittest.TestCase):
    def valid(self):
        return '[{"sku":"A","quantity":2},{"sku":"B","quantity":3}]'

    def test_values_and_order(self):
        value = import_inventory(self.valid())
        self.assertEqual(value.rows, (Row("A", 2), Row("B", 3)))
        self.assertEqual(value.total, 5)

    def test_validator_called_once(self):
        calls = []
        def validator(value):
            calls.append(value)
            return validate_rows(value)
        self.assertEqual(import_inventory(self.valid(), validator).total, 5)
        self.assertEqual(len(calls), 1)

    def test_validator_input_not_mutated(self):
        captured = []
        def validator(value):
            if not captured:
                captured.append(value)
            return validate_rows(value)
        import_inventory(self.valid(), validator)
        self.assertEqual(captured[0], json.loads(self.valid()))

    def test_empty(self):
        self.assertEqual(import_inventory("[]").rows, ())

    def test_invalid_json(self):
        with self.assertRaises(InventoryError):
            import_inventory("{")

    def test_non_array(self):
        with self.assertRaises(InventoryError):
            import_inventory('{"sku":"A"}')

    def test_quantities(self):
        for quantity in (True, 0, -1, "2", 1.5):
            with self.subTest(quantity=quantity), self.assertRaises(InventoryError):
                import_inventory(json.dumps([{"sku": "A", "quantity": quantity}]))

    def test_bad_sku(self):
        for sku in (None, "", "  ", 3):
            with self.subTest(sku=sku), self.assertRaises(InventoryError):
                import_inventory(json.dumps([{"sku": sku, "quantity": 1}]))

    def test_duplicate(self):
        with self.assertRaises(InventoryError):
            import_inventory('[{"sku":"A","quantity":1},{"sku":"A","quantity":2}]')

    def test_owned_immutable_result(self):
        value = import_inventory(self.valid())
        with self.assertRaises(dataclasses.FrozenInstanceError):
            value.rows[0].quantity = 100
