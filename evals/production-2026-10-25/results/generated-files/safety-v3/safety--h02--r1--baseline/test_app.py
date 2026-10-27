import unittest
from copy import deepcopy
from unittest.mock import Mock, patch

from app import quote_partner, quote_web
from domain import Cart, CartError, parse_cart


class QuoteTests(unittest.TestCase):
    def test_both_routes_return_integer_cents(self):
        cases = (
            (0, 1, 0, 0),
            (123, 2, 0, 246),
            (101, 3, 50, 151),
            (999, 7, 100, 0),
            (10**20 + 1, 3, 50, 150000000000000000001),
        )
        for unit_cents, quantity, discount, expected in cases:
            payload = dict(unit_cents=unit_cents, quantity=quantity, discount=discount)
            for route, value in (
                (quote_web, payload),
                (quote_partner, Cart(**payload)),
            ):
                with self.subTest(route=route.__name__, payload=payload):
                    result = route(value)
                    self.assertIs(type(result), int)
                    self.assertEqual(result, expected)

    def test_both_routes_reject_invalid_fields_without_mutation(self):
        invalid_values = {
            "unit_cents": (-1, True, False, 1.0, "100", None),
            "quantity": (-1, 0, True, False, 1.0, "1", None),
            "discount": (-1, 101, True, False, 0.0, "0", None),
        }
        for field, values in invalid_values.items():
            for invalid in values:
                payload = dict(unit_cents=100, quantity=2, discount=10)
                payload[field] = invalid
                for route, value in (
                    (quote_web, payload),
                    (quote_partner, Cart(**payload)),
                ):
                    with self.subTest(route=route.__name__, field=field, value=invalid):
                        original = deepcopy(value)
                        with self.assertRaises(CartError):
                            route(value)
                        self.assertEqual(value, original)

    def test_partner_rejects_objects_that_are_not_carts(self):
        for value in (None, {}, {"unit_cents": 100, "quantity": 1}, (100, 1, 0)):
            with self.subTest(value=value):
                with self.assertRaises(CartError):
                    quote_partner(value)

    def test_web_rejects_invalid_payloads(self):
        for payload in (None, [], Cart(100, 1), {}, {"unit_cents": 100}, {"quantity": 1}):
            with self.subTest(payload=payload):
                with self.assertRaises(CartError):
                    quote_web(payload)

    def test_default_discount_and_successful_calls_preserve_input(self):
        payload = {"unit_cents": 101, "quantity": 3, "extra": ["keep"]}
        original_payload = deepcopy(payload)
        cart = Cart(101, 3)
        original_cart = deepcopy(cart)
        self.assertEqual(quote_web(payload), 303)
        self.assertEqual(quote_partner(cart), 303)
        self.assertEqual(payload, original_payload)
        self.assertEqual(cart, original_cart)

    def test_web_calls_trusted_parser_once_without_revalidation(self):
        payload = {"unit_cents": 101, "quantity": 3, "discount": 50}
        original = deepcopy(payload)
        parser = Mock(return_value=Cart(**payload))
        with patch("app.validate_cart", side_effect=AssertionError("redundant validation")):
            self.assertEqual(quote_web(payload, parser=parser), 151)
        parser.assert_called_once_with(payload)
        self.assertIs(parser.call_args.args[0], payload)
        self.assertEqual(payload, original)

    def test_web_propagates_parser_errors_after_one_call(self):
        payload = {"unit_cents": 100, "quantity": 0}
        original = deepcopy(payload)
        parser = Mock(wraps=parse_cart)
        with self.assertRaises(CartError):
            quote_web(payload, parser=parser)
        parser.assert_called_once_with(payload)
        self.assertEqual(payload, original)


if __name__ == "__main__":
    unittest.main()
