import unittest
from dataclasses import asdict
from unittest.mock import Mock, patch

from app import quote_partner, quote_web
from domain import Cart, CartError, parse_cart


class QuoteTests(unittest.TestCase):
    def test_both_routes_price_valid_carts_without_mutation(self):
        for cart, expected in (
            (Cart(199, 3, 15), 507),
            (Cart(101, 2), 202),
            (Cart(0, 1), 0),
            (Cart(199, 3, 100), 0),
        ):
            with self.subTest(cart=cart):
                payload = asdict(cart)
                original = payload.copy()
                for result in (quote_web(payload), quote_partner(cart)):
                    self.assertIs(type(result), int)
                    self.assertEqual(result, expected)
                self.assertEqual(payload, original)
                self.assertEqual(asdict(cart), original)

    def test_both_routes_reject_invalid_fields_without_mutation(self):
        invalid_fields = {
            "unit_cents": (-1, True, False, 1.0, "1", None),
            "quantity": (-1, 0, True, False, 1.0, "1", None),
            "discount": (-1, 101, True, False, 1.0, "1", None),
        }
        for field, values in invalid_fields.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    payload = dict(unit_cents=199, quantity=3, discount=15)
                    payload[field] = value
                    original = payload.copy()
                    cart = Cart(**payload)
                    with self.assertRaises(CartError):
                        quote_web(payload)
                    with self.assertRaises(CartError):
                        quote_partner(cart)
                    self.assertEqual(payload, original)
                    self.assertEqual(asdict(cart), original)

    def test_partner_rejects_values_outside_cart_boundary(self):
        for value in (None, {}, {"unit_cents": 199, "quantity": 3}):
            with self.subTest(value=value):
                with self.assertRaisesRegex(CartError, "^cart$"):
                    quote_partner(value)

    def test_web_rejects_invalid_payloads(self):
        for payload in (None, [], {}, {"unit_cents": 199}, {"quantity": 3}):
            with self.subTest(payload=payload):
                with self.assertRaises(CartError):
                    quote_web(payload)

    def test_web_defaults_discount_to_zero(self):
        self.assertEqual(quote_web({"unit_cents": 101, "quantity": 2}), 202)

    def test_web_trusted_parser_runs_once_and_owns_validation(self):
        payload = {"unit_cents": 199, "quantity": 3, "discount": 15}
        parser = Mock(wraps=parse_cart)
        with patch("app.validate_cart") as redundant_validation:
            self.assertEqual(quote_web(payload, parser), 507)
            redundant_validation.assert_not_called()
        parser.assert_called_once_with(payload)
        self.assertIs(parser.call_args.args[0], payload)

    def test_web_propagates_parser_failures_without_retry(self):
        for error in (CartError("invalid"), RuntimeError("parser failed")):
            with self.subTest(error=error):
                parser = Mock(side_effect=error)
                with self.assertRaises(type(error)) as raised:
                    quote_web({}, parser=parser)
                self.assertIs(raised.exception, error)
                parser.assert_called_once_with({})


if __name__ == "__main__":
    unittest.main()
