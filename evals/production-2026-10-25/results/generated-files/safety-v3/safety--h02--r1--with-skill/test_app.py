import unittest
from unittest.mock import Mock

from app import quote_partner, quote_web
from domain import Cart, CartError, parse_cart


class QuoteTests(unittest.TestCase):
    def test_quotes_calculate_integer_cents_without_changing_inputs(self):
        cases = (
            (199, 3, 15, 507),
            (100, 2, 0, 200),
            (100, 2, 100, 0),
            (0, 1, 50, 0),
            (1, 1, 1, 0),
        )
        for unit_cents, quantity, discount, expected in cases:
            with self.subTest(case=(unit_cents, quantity, discount)):
                payload = dict(unit_cents=unit_cents, quantity=quantity, discount=discount)
                original_payload = payload.copy()
                cart = Cart(**payload)
                for actual in (quote_web(payload), quote_partner(cart)):
                    self.assertIs(type(actual), int)
                    self.assertEqual(actual, expected)
                self.assertEqual(payload, original_payload)
                self.assertEqual(
                    (cart.unit_cents, cart.quantity, cart.discount),
                    (unit_cents, quantity, discount),
                )

    def test_web_defaults_discount_without_changing_payload(self):
        payload = {"unit_cents": 125, "quantity": 2}
        self.assertEqual(quote_web(payload), 250)
        self.assertEqual(payload, {"unit_cents": 125, "quantity": 2})

    def test_both_routes_reject_invalid_fields(self):
        invalid_values = {
            "unit_cents": (-1, True, False, 1.0, "1", None),
            "quantity": (-1, 0, True, False, 1.0, "1", None),
            "discount": (-1, 101, True, False, 1.0, "1", None),
        }
        for field, values in invalid_values.items():
            for value in values:
                payload = {"unit_cents": 100, "quantity": 2, "discount": 10}
                payload[field] = value
                original_payload = payload.copy()
                cart = Cart(**payload)
                for quote, argument in ((quote_web, payload), (quote_partner, cart)):
                    with self.subTest(route=quote.__name__, field=field, value=value):
                        with self.assertRaises(CartError):
                            quote(argument)
                self.assertEqual(payload, original_payload)
                self.assertEqual(
                    (cart.unit_cents, cart.quantity, cart.discount),
                    tuple(original_payload[key] for key in ("unit_cents", "quantity", "discount")),
                )

    def test_web_rejects_invalid_payloads_and_missing_fields(self):
        for payload in (None, [], "cart", {}, {"unit_cents": 100}, {"quantity": 1}):
            with self.subTest(payload=payload):
                with self.assertRaises(CartError):
                    quote_web(payload)

    def test_partner_requires_a_cart(self):
        for cart in (None, {"unit_cents": 100, "quantity": 1}, object()):
            with self.subTest(cart=cart):
                with self.assertRaises(CartError):
                    quote_partner(cart)

    def test_web_calls_injected_parser_once(self):
        payload = {"unit_cents": 199, "quantity": 3, "discount": 15}
        parser = Mock(wraps=parse_cart)
        self.assertEqual(quote_web(payload, parser=parser), 507)
        parser.assert_called_once_with(payload)

    def test_web_preserves_parser_errors(self):
        payload = {"unit_cents": 100, "quantity": 1}
        for error in (CartError("invalid cart"), RuntimeError("parser failed")):
            with self.subTest(error=type(error).__name__):
                parser = Mock(side_effect=error)
                with self.assertRaises(type(error)) as caught:
                    quote_web(payload, parser=parser)
                self.assertIs(caught.exception, error)
                parser.assert_called_once_with(payload)


if __name__ == "__main__":
    unittest.main()
