import unittest
from unittest.mock import Mock, patch

from app import quote_partner, quote_web
from domain import Cart, CartError, parse_cart


class QuoteTests(unittest.TestCase):
    def test_valid_quotes_leave_inputs_unchanged(self):
        cases = [
            ({"unit_cents": 125, "quantity": 3}, 375),
            ({"unit_cents": 101, "quantity": 3, "discount": 50}, 151),
            ({"unit_cents": 0, "quantity": 1, "discount": 0}, 0),
            ({"unit_cents": 999, "quantity": 2, "discount": 100}, 0),
            ({"unit_cents": 10**20 + 1, "quantity": 1, "discount": 1},
             99000000000000000000),
        ]
        for payload, expected in cases:
            with self.subTest(payload=payload):
                original = payload.copy()
                cart = Cart(**payload)
                web_price = quote_web(payload)
                partner_price = quote_partner(cart)
                self.assertEqual(web_price, expected)
                self.assertEqual(partner_price, expected)
                self.assertIs(type(web_price), int)
                self.assertIs(type(partner_price), int)
                self.assertEqual(payload, original)
                self.assertEqual(cart, Cart(**original))

    def test_invalid_fields_are_rejected_on_both_routes(self):
        invalid_values = {
            "unit_cents": [-1, True, False, 1.0, "100", None],
            "quantity": [-1, 0, True, False, 1.0, "1", None],
            "discount": [-1, 101, True, False, 1.0, "10", None],
        }
        for field, values in invalid_values.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    payload = {"unit_cents": 100, "quantity": 2, "discount": 10}
                    payload[field] = value
                    original = payload.copy()
                    cart = Cart(**payload)
                    with self.assertRaises(CartError):
                        quote_web(payload)
                    with self.assertRaises(CartError):
                        quote_partner(cart)
                    self.assertEqual(payload, original)
                    self.assertEqual(cart, Cart(**original))

    def test_web_rejects_malformed_payloads(self):
        for payload in (None, [], {}, {"unit_cents": 100}, {"quantity": 1}):
            with self.subTest(payload=payload), self.assertRaises(CartError):
                quote_web(payload)

    def test_partner_rejects_non_cart_inputs(self):
        for cart in (None, {}, {"unit_cents": 100, "quantity": 1}, object()):
            with self.subTest(cart=cart), self.assertRaises(CartError):
                quote_partner(cart)

    def test_web_calls_trusted_parser_once_without_revalidation(self):
        payload = {"unit_cents": 101, "quantity": 3, "discount": 50}
        parser = Mock(wraps=parse_cart)
        with patch("app.validate_cart", side_effect=AssertionError("revalidation")):
            self.assertEqual(quote_web(payload, parser=parser), 151)
        parser.assert_called_once_with(payload)

    def test_web_propagates_parser_failure(self):
        error = RuntimeError("parser failed")
        parser = Mock(side_effect=error)
        with self.assertRaises(RuntimeError) as raised:
            quote_web({}, parser)
        self.assertIs(raised.exception, error)
        parser.assert_called_once_with({})


if __name__ == "__main__":
    unittest.main()
