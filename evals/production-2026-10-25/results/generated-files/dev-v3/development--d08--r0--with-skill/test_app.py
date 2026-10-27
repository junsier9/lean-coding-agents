import unittest
from unittest.mock import Mock

from app import process_charge
from plugins import loyalty_discount
from policy import InputError


class AmountSubclass(int):
    pass


class CurrencySubclass(str):
    pass


INVALID_ORDERS = [
    {},
    {"amount": 10},
    {"currency": "USD"},
    {"amount": 10, "currency": "USD", "extra": None},
    *({"amount": amount, "currency": "USD"} for amount in (
        -1, 100001, True, False, 1.0, "1", None, AmountSubclass(1),
    )),
    *({"amount": 10, "currency": currency} for currency in (
        "usd", "GBP", "", None, 1, ["USD"], CurrencySubclass("USD"),
    )),
]


class ProcessChargeTests(unittest.TestCase):
    def test_valid_orders_without_hook(self):
        for amount in (0, 42, 100000):
            for currency in ("USD", "EUR"):
                with self.subTest(amount=amount, currency=currency):
                    payload = {"amount": amount, "currency": currency}
                    result = object()
                    charge = Mock(return_value=result)

                    self.assertIs(process_charge(payload, charge), result)

                    charge.assert_called_once_with(amount, currency)
                    self.assertEqual(payload, {"amount": amount, "currency": currency})

    def test_hook_changes_owned_request_and_ignores_return_value(self):
        payload = {"amount": 100, "currency": "USD"}

        def adjust(order):
            self.assertIsNot(order, payload)
            self.assertEqual(order, payload)
            order.update(amount=95, currency="EUR")
            self.assertEqual(payload, {"amount": 100, "currency": "USD"})
            return {"amount": -1, "currency": "INVALID"}

        hook = Mock(side_effect=adjust)
        result = object()
        charge = Mock(return_value=result)

        self.assertIs(process_charge(payload, charge, hook), result)

        hook.assert_called_once()
        charge.assert_called_once_with(95, "EUR")
        self.assertEqual(payload, {"amount": 100, "currency": "USD"})

    def test_example_loyalty_discount(self):
        for amount, expected in ((3, 0), (10, 5)):
            with self.subTest(amount=amount):
                payload = {"amount": amount, "currency": "USD"}
                charge = Mock()

                process_charge(payload, charge, loyalty_discount)

                charge.assert_called_once_with(expected, "USD")
                self.assertEqual(payload, {"amount": amount, "currency": "USD"})

    def test_invalid_external_input_never_reaches_hook_or_charge(self):
        for payload in [None, [], [("amount", 10), ("currency", "USD")], *INVALID_ORDERS]:
            for use_hook in (False, True):
                with self.subTest(payload=payload, use_hook=use_hook):
                    hook = Mock()
                    charge = Mock()

                    with self.assertRaises(InputError):
                        process_charge(payload, charge, hook if use_hook else None)

                    hook.assert_not_called()
                    charge.assert_not_called()

    def test_invalid_hook_mutation_never_reaches_charge(self):
        for invalid in INVALID_ORDERS:
            with self.subTest(order=invalid):
                payload = {"amount": 100, "currency": "USD"}

                def invalidate(order):
                    self.assertIsNot(order, payload)
                    order.clear()
                    order.update(invalid)

                hook = Mock(side_effect=invalidate)
                charge = Mock()

                with self.assertRaises(InputError):
                    process_charge(payload, charge, hook)

                hook.assert_called_once()
                charge.assert_not_called()
                self.assertEqual(payload, {"amount": 100, "currency": "USD"})

    def test_hook_exception_propagates_without_charging_or_changing_input(self):
        payload = {"amount": 100, "currency": "USD"}
        error = RuntimeError("hook failed")

        def fail(order):
            order.clear()
            raise error

        charge = Mock()

        with self.assertRaises(RuntimeError) as caught:
            process_charge(payload, charge, fail)

        self.assertIs(caught.exception, error)
        charge.assert_not_called()
        self.assertEqual(payload, {"amount": 100, "currency": "USD"})

    def test_charge_exception_propagates_without_changing_input(self):
        for hook, expected_amount in ((None, 100), (loyalty_discount, 95)):
            with self.subTest(hook=hook):
                payload = {"amount": 100, "currency": "USD"}
                error = RuntimeError("charge failed")
                charge = Mock(side_effect=error)

                with self.assertRaises(RuntimeError) as caught:
                    process_charge(payload, charge, hook)

                self.assertIs(caught.exception, error)
                charge.assert_called_once_with(expected_amount, "USD")
                self.assertEqual(payload, {"amount": 100, "currency": "USD"})


if __name__ == "__main__":
    unittest.main()
