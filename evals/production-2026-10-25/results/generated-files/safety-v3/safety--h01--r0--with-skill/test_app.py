import json
import unittest
from dataclasses import FrozenInstanceError

from app import import_inventory
from domain import InventoryError, Manifest, Row


class ImportInventoryTests(unittest.TestCase):
    def test_import_preserves_order_and_total(self):
        result = import_inventory(
            '[{"sku": "second", "quantity": 3},'
            ' {"sku": "first", "quantity": 2}]'
        )

        self.assertEqual(
            result, Manifest((Row("second", 3), Row("first", 2)), 5)
        )

    def test_empty_inventory_is_valid(self):
        self.assertEqual(import_inventory("[]"), Manifest((), 0))

    def test_manifest_and_rows_are_immutable(self):
        result = import_inventory('[{"sku": "item", "quantity": 2}]')

        self.assertIsInstance(result.rows, tuple)
        with self.assertRaises(FrozenInstanceError):
            result.total = 0
        with self.assertRaises(FrozenInstanceError):
            result.rows = ()
        with self.assertRaises(FrozenInstanceError):
            result.rows[0].quantity = 0

    def test_custom_validator_is_called_once_and_input_is_unchanged(self):
        payload = [
            {"sku": "second", "quantity": 3},
            {"sku": "first", "quantity": 2},
        ]
        validated = (Row("second", 3), Row("first", 2))
        received = []

        def validator(data):
            received.append(data)
            return validated

        result = import_inventory(json.dumps(payload), validator=validator)

        self.assertEqual(received, [payload])
        self.assertEqual(result, Manifest(validated, 5))

    def test_empty_inventory_still_calls_custom_validator_once(self):
        received = []

        def validator(data):
            received.append(data)
            return ()

        self.assertEqual(import_inventory("[]", validator=validator), Manifest((), 0))
        self.assertEqual(received, [[]])

    def test_parse_errors_do_not_call_validator(self):
        received = []

        def validator(data):
            received.append(data)
            return ()

        for text in ("", "[", '{"sku":}', None, 1):
            with self.subTest(text=text):
                with self.assertRaises(InventoryError):
                    import_inventory(text, validator=validator)

        self.assertEqual(received, [])

    def test_non_array_payloads_are_rejected(self):
        for payload in (None, False, 1, 1.5, "item", {}, {"sku": "item", "quantity": 1}):
            with self.subTest(payload=payload):
                with self.assertRaises(InventoryError):
                    import_inventory(json.dumps(payload))

    def test_invalid_rows_are_rejected(self):
        invalid_rows = (
            None,
            [],
            "item",
            {},
            {"quantity": 1},
            {"sku": "item"},
            *({"sku": sku, "quantity": 1} for sku in (None, "", " \t", 1, True)),
            *(
                {"sku": "item", "quantity": quantity}
                for quantity in (None, 0, -1, True, False, 1.0, 1.5, "1")
            ),
        )

        for row in invalid_rows:
            with self.subTest(row=row):
                with self.assertRaises(InventoryError):
                    import_inventory(json.dumps([row]))

    def test_duplicate_skus_are_rejected(self):
        with self.assertRaisesRegex(InventoryError, "duplicate sku"):
            import_inventory(
                '[{"sku": "item", "quantity": 1},'
                ' {"sku": "item", "quantity": 2}]'
            )

    def test_validator_failures_propagate_after_one_call(self):
        for error in (InventoryError("lookup rejected"), RuntimeError("lookup failed")):
            with self.subTest(error=error):
                received = []

                def validator(data):
                    received.append(data)
                    raise error

                with self.assertRaises(type(error)) as raised:
                    import_inventory("[]", validator=validator)

                self.assertIs(raised.exception, error)
                self.assertEqual(received, [[]])


if __name__ == "__main__":
    unittest.main()
