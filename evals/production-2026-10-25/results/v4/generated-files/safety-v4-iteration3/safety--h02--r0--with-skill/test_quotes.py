import unittest
from unittest.mock import Mock, patch

from app import quote_partner, quote_web
from domain import Cart, CartError, parse_cart, validate_cart


class QuoteTests(unittest.TestCase):
    def test_valid_quotes_and_unchanged_inputs(self):
        cases = (
            ({"unit_cents": 125, "quantity": 2}, 250),
            ({"unit_cents": 0, "quantity": 3, "discount": 20}, 0),
            ({"unit_cents": 199, "quantity": 3, "discount": 15}, 507),
            ({"unit_cents": 1, "quantity": 3, "discount": 50}, 1),
            ({"unit_cents": 199, "quantity": 3, "discount": 0}, 597),
            ({"unit_cents": 199, "quantity": 3, "discount": 100}, 0),
        )
        for payload, expected in cases:
            with self.subTest(payload=payload):
                original_payload = dict(payload)
                cart = Cart(**payload)
                original_fields = (cart.unit_cents, cart.quantity, cart.discount)
                web_price = quote_web(payload)
                partner_price = quote_partner(cart)
                self.assertEqual(web_price, expected)
                self.assertEqual(partner_price, expected)
                self.assertIs(type(web_price), int)
                self.assertIs(type(partner_price), int)
                self.assertEqual(payload, original_payload)
                self.assertEqual(
                    (cart.unit_cents, cart.quantity, cart.discount), original_fields
                )

    def test_both_routes_reject_invalid_fields_without_mutation(self):
        invalid_fields = (
            ("unit_cents", (-1, 1.5, "100", None, True, False)),
            ("quantity", (0, -1, 1.0, "1", None, True, False)),
            ("discount", (-1, 101, 1.0, "0", None, True, False)),
        )
        for field, values in invalid_fields:
            for value in values:
                payload = {"unit_cents": 100, "quantity": 2, "discount": 10}
                payload[field] = value
                cart = Cart(**payload)
                for route, argument in ((quote_web, payload), (quote_partner, cart)):
                    with self.subTest(route=route.__name__, field=field, value=value):
                        original_payload = dict(payload)
                        original_fields = (
                            cart.unit_cents, cart.quantity, cart.discount
                        )
                        with self.assertRaises(CartError):
                            route(argument)
                        self.assertEqual(payload, original_payload)
                        self.assertEqual(
                            (cart.unit_cents, cart.quantity, cart.discount),
                            original_fields,
                        )

    def test_partner_rejects_non_cart(self):
        for argument in (None, {}, {"unit_cents": 100, "quantity": 1}, object()):
            with self.subTest(argument=argument):
                with self.assertRaisesRegex(CartError, "^cart$"):
                    quote_partner(argument)

    def test_web_rejects_invalid_payloads(self):
        for payload in (None, [], (), "cart", {}, {"unit_cents": 100}, {"quantity": 1}):
            with self.subTest(payload=payload):
                with self.assertRaises(CartError):
                    quote_web(payload)

    def test_web_parser_is_called_once_and_owns_validation(self):
        payload = {"unit_cents": 199, "quantity": 3, "discount": 15}
        parser = Mock(wraps=parse_cart)
        with patch("domain.validate_cart", wraps=validate_cart) as validator:
            with patch("app.validate_cart", side_effect=AssertionError("revalidated")):
                self.assertEqual(quote_web(payload, parser=parser), 507)
        parser.assert_called_once_with(payload)
        validator.assert_called_once_with(Cart(199, 3, 15))

    def test_web_accepts_trusted_parser_positionally(self):
        payload = {"partner_format": "trusted"}
        parser = Mock(return_value=validate_cart(Cart(1, 3, 50)))
        with patch("app.validate_cart", side_effect=AssertionError("revalidated")):
            self.assertEqual(quote_web(payload, parser), 1)
        parser.assert_called_once_with(payload)
        self.assertEqual(payload, {"partner_format": "trusted"})

    def test_web_propagates_parser_failures_without_retry(self):
        payload = {"unit_cents": 100, "quantity": 1}
        for error in (CartError("invalid cart"), RuntimeError("parser failed")):
            with self.subTest(error=error):
                parser = Mock(side_effect=error)
                with self.assertRaises(type(error)) as raised:
                    quote_web(payload, parser=parser)
                self.assertIs(raised.exception, error)
                parser.assert_called_once_with(payload)


if __name__ == "__main__":
    unittest.main()
