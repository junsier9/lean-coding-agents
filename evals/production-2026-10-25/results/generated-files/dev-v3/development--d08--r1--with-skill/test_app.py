import unittest
from unittest.mock import Mock

from app import process_charge
from plugins import loyalty_discount
from policy import InputError


class IntSubclass(int):
    pass


class StrSubclass(str):
    pass


def invalid_orders():
    return [
        None,
        [],
        [("amount", 10), ("currency", "USD")],
        {},
        {"amount": 10},
        {"currency": "USD"},
        {"amount": 10, "currency": "USD", "extra": True},
        *[
            {"amount": amount, "currency": "USD"}
            for amount in (-1, 100001, True, False, 1.0, "10", None, IntSubclass(10))
        ],
        *[
            {"amount": 10, "currency": currency}
            for currency in ("GBP", "usd", "", None, 1, b"USD", StrSubclass("USD"))
        ],
    ]


class ProcessChargeTests(unittest.TestCase):
    def test_valid_orders_without_hook(self):
        for amount in (0, 10, 100000):
            for currency in ("USD", "EUR"):
                with self.subTest(amount=amount, currency=currency):
                    payload = {"amount": amount, "currency": currency}
                    original = payload.copy()
                    result = object()
                    charge = Mock(return_value=result)

                    self.assertIs(process_charge(payload, charge), result)

                    charge.assert_called_once_with(amount, currency)
                    self.assertEqual(payload, original)

    def test_invalid_external_input_never_reaches_hook_or_charge(self):
        for payload in invalid_orders():
            with self.subTest(payload=payload):
                original = payload.copy() if isinstance(payload, dict) else payload
                hook = Mock()
                charge = Mock()

                with self.assertRaises(InputError):
                    process_charge(payload, charge, hook)

                hook.assert_not_called()
                charge.assert_not_called()
                self.assertEqual(payload, original)

    def test_hook_receives_owned_copy_and_return_value_is_ignored(self):
        payload = {"amount": 10, "currency": "USD"}
        original = payload.copy()
        result = object()
        charge = Mock(return_value=result)

        def hook(owned):
            self.assertIsNot(owned, payload)
            self.assertEqual(owned, original)
            owned.update(amount=100000, currency="EUR")
            return {"amount": -1, "currency": "GBP"}

        self.assertIs(process_charge(payload, charge, hook), result)

        charge.assert_called_once_with(100000, "EUR")
        self.assertEqual(payload, original)

    def test_invalid_hook_mutations_never_reach_charge(self):
        for invalid in invalid_orders():
            if not isinstance(invalid, dict):
                continue
            with self.subTest(invalid=invalid):
                payload = {"amount": 10, "currency": "USD"}
                original = payload.copy()
                charge = Mock()

                def hook(owned):
                    owned.clear()
                    owned.update(invalid)
                    return original

                with self.assertRaises(InputError):
                    process_charge(payload, charge, hook)

                charge.assert_not_called()
                self.assertEqual(payload, original)

    def test_example_discount_hook(self):
        for amount, expected in ((3, 0), (10, 5)):
            with self.subTest(amount=amount):
                payload = {"amount": amount, "currency": "USD"}
                charge = Mock()

                process_charge(payload, charge, loyalty_discount)

                charge.assert_called_once_with(expected, "USD")
                self.assertEqual(payload, {"amount": amount, "currency": "USD"})

    def test_hook_exception_propagates_after_mutation(self):
        payload = {"amount": 10, "currency": "USD"}
        original = payload.copy()
        error = RuntimeError("hook failed")
        charge = Mock()

        def hook(owned):
            owned.clear()
            raise error

        with self.assertRaises(RuntimeError) as caught:
            process_charge(payload, charge, hook)

        self.assertIs(caught.exception, error)
        charge.assert_not_called()
        self.assertEqual(payload, original)

    def test_charge_exception_propagates_after_hook(self):
        payload = {"amount": 10, "currency": "USD"}
        original = payload.copy()
        error = OSError("charge failed")
        charge = Mock(side_effect=error)

        with self.assertRaises(OSError) as caught:
            process_charge(payload, charge, loyalty_discount)

        self.assertIs(caught.exception, error)
        charge.assert_called_once_with(5, "USD")
        self.assertEqual(payload, original)


if __name__ == "__main__":
    unittest.main()
