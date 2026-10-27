import unittest
from unittest.mock import Mock, patch

from app import quote_partner, quote_web
from domain import Cart, CartError, parse_cart


class QuoteTests(unittest.TestCase):
    def test_valid_quotes_use_integer_cents_without_mutating_inputs(self):
        cases = [
            ({"unit_cents": 101, "quantity": 3, "discount": 15}, 257),
            ({"unit_cents": 123, "quantity": 2}, 246),
            ({"unit_cents": 0, "quantity": 1, "discount": 0}, 0),
            ({"unit_cents": 101, "quantity": 3, "discount": 100}, 0),
        ]
        for payload, expected in cases:
            with self.subTest(payload=payload):
                original = payload.copy()
                cart = Cart(**payload)
                fields = (cart.unit_cents, cart.quantity, cart.discount)
                web_quote = quote_web(payload)
                partner_quote = quote_partner(cart)
                self.assertEqual(web_quote, expected)
                self.assertEqual(partner_quote, expected)
                self.assertIs(type(web_quote), int)
                self.assertIs(type(partner_quote), int)
                self.assertEqual(payload, original)
                self.assertEqual(
                    (cart.unit_cents, cart.quantity, cart.discount), fields
                )

    def test_both_routes_reject_invalid_fields(self):
        invalid_values = {
            "unit_cents": [-1, True, False, 1.5, "100", None],
            "quantity": [-1, 0, True, False, 1.0, "1", None],
            "discount": [-1, 101, True, False, 1.0, "1", None],
        }
        for field, values in invalid_values.items():
            for value in values:
                payload = {"unit_cents": 100, "quantity": 2, "discount": 10}
                payload[field] = value
                for quote, argument in (
                    (quote_web, payload),
                    (quote_partner, Cart(**payload)),
                ):
                    with self.subTest(route=quote.__name__, field=field, value=value):
                        with self.assertRaises(CartError):
                            quote(argument)

    def test_partner_rejects_non_cart_inputs(self):
        for value in (None, {}, {"unit_cents": 100, "quantity": 2}):
            with self.subTest(value=value):
                with self.assertRaises(CartError):
                    quote_partner(value)

    def test_web_rejects_invalid_payloads(self):
        for payload in (None, [], {}, {"unit_cents": 100}, {"quantity": 2}):
            with self.subTest(payload=payload):
                with self.assertRaises(CartError):
                    quote_web(payload)

    def test_web_calls_its_parser_once_without_revalidating(self):
        payload = {"unit_cents": 101, "quantity": 3, "discount": 15}
        for parser in (
            Mock(wraps=parse_cart),
            Mock(return_value=parse_cart(payload)),
        ):
            with self.subTest(parser=parser):
                with patch("app.validate_cart") as validate:
                    self.assertEqual(quote_web(payload, parser=parser), 257)
                parser.assert_called_once_with(payload)
                self.assertIs(parser.call_args.args[0], payload)
                validate.assert_not_called()

    def test_web_propagates_parser_failures_without_retrying(self):
        payload = {"unit_cents": 100, "quantity": 2}
        for error in (CartError("invalid cart"), RuntimeError("parser failed")):
            with self.subTest(error=error):
                parser = Mock(side_effect=error)
                with self.assertRaises(type(error)) as raised:
                    quote_web(payload, parser=parser)
                self.assertIs(raised.exception, error)
                parser.assert_called_once_with(payload)


if __name__ == "__main__":
    unittest.main()
