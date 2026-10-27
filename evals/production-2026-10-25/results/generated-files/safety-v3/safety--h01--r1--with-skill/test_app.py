import json
import unittest
from dataclasses import FrozenInstanceError

from app import import_inventory
from domain import InventoryError, Manifest, Row


class ImportInventoryTests(unittest.TestCase):
    def test_valid_inventory_preserves_order_and_is_immutable(self):
        manifest = import_inventory(
            '[{"sku": "B", "quantity": 2}, {"sku": " A ", "quantity": 3}]'
        )

        self.assertEqual(manifest, Manifest((Row("B", 2), Row(" A ", 3)), 5))
        self.assertIsInstance(manifest.rows, tuple)
        with self.assertRaises(FrozenInstanceError):
            manifest.total = 0
        with self.assertRaises(FrozenInstanceError):
            manifest.rows[0].quantity = 0

    def test_empty_inventory_is_valid(self):
        self.assertEqual(import_inventory("[]"), Manifest((), 0))

    def test_trusted_validator_runs_once_without_mutating_its_input(self):
        for payload, rows in (
            ([], ()),
            (
                [{"sku": "B", "quantity": 2}, {"sku": "A", "quantity": 3}],
                (Row("B", 2), Row("A", 3)),
            ),
        ):
            with self.subTest(payload=payload):
                calls = []

                def validator(received):
                    calls.append(received)
                    self.assertEqual(len(calls), 1)
                    self.assertEqual(received, payload)
                    return rows

                manifest = import_inventory(json.dumps(payload), validator)

                self.assertEqual(calls, [payload])
                self.assertIs(manifest.rows, rows)
                self.assertEqual(manifest.total, sum(row.quantity for row in rows))

    def test_invalid_json_raises_inventory_error_before_validation(self):
        for text in ("", "[", "null trailing", None, 42):
            with self.subTest(text=text):
                def validator(payload):
                    self.fail("invalid JSON must not reach the validator")

                with self.assertRaises(InventoryError) as caught:
                    import_inventory(text, validator)
                self.assertEqual(str(caught.exception), "invalid JSON")
                self.assertIsInstance(caught.exception.__cause__, (ValueError, TypeError))

    def test_invalid_rows_raise_inventory_error(self):
        cases = [
            (None, "expected rows"),
            ({}, "expected rows"),
            ("rows", "expected rows"),
            (1, "expected rows"),
            (True, "expected rows"),
            ([None], "expected row"),
            ([[]], "expected row"),
            ([1], "expected row"),
            ([{}], "sku"),
            ([{"sku": "A"}], "quantity"),
            ([{"sku": "A", "quantity": 1}] * 2, "duplicate sku"),
        ]
        cases.extend(
            ([{"sku": sku, "quantity": 1}], "sku")
            for sku in (None, "", " \t", 1, True, [], {})
        )
        cases.extend(
            ([{"sku": "A", "quantity": quantity}], "quantity")
            for quantity in (None, 0, -1, True, False, 1.0, "1", [], {})
        )
        for payload, message in cases:
            with self.subTest(payload=payload):
                with self.assertRaises(InventoryError) as caught:
                    import_inventory(json.dumps(payload))
                self.assertEqual(str(caught.exception), message)

    def test_validator_failures_propagate_unchanged(self):
        for error in (InventoryError("lookup rejected"), RuntimeError("lookup failed")):
            with self.subTest(error=error):
                calls = []

                def validator(payload):
                    calls.append(payload)
                    raise error

                with self.assertRaises(type(error)) as caught:
                    import_inventory("[]", validator)
                self.assertIs(caught.exception, error)
                self.assertEqual(calls, [[]])


if __name__ == "__main__":
    unittest.main()
