import unittest
from copy import deepcopy
from dataclasses import FrozenInstanceError
from unittest.mock import Mock, patch

from app import quote_partner, quote_web
from domain import Cart, CartError, parse_cart


class QuoteTests(unittest.TestCase):
    def test_both_routes_return_integer_cents(self):
        cases = (
            (Cart(199, 3, 10), 537),
            (Cart(1, 1, 1), 0),
            (Cart(125, 2, 0), 250),
            (Cart(125, 2, 100), 0),
            (Cart(0, 3, 50), 0),
            (Cart(9007199254740993, 3, 25), 20266198323167234),
        )
        for cart, expected in cases:
            payload = vars(cart).copy()
            for route, value in ((quote_web, payload), (quote_partner, cart)):
                with self.subTest(route=route.__name__, cart=cart):
                    actual = route(value)
                    self.assertEqual(actual, expected)
                    self.assertIs(type(actual), int)

    def test_both_routes_reject_invalid_fields(self):
        invalid_fields = {
            "unit_cents": (-1, True, False, 1.0, "100", None),
            "quantity": (0, -1, True, False, 1.0, "1", None),
            "discount": (-1, 101, True, False, 0.0, 100.0, "10", None),
        }
        for field, values in invalid_fields.items():
            for value in values:
                payload = {"unit_cents": 100, "quantity": 2, "discount": 10}
                payload[field] = value
                cart = Cart(**payload)
                before = payload.copy()
                for route, argument in ((quote_web, payload), (quote_partner, cart)):
                    with self.subTest(route=route.__name__, field=field, value=value):
                        with self.assertRaises(CartError):
                            route(argument)
                        self.assertEqual(payload, before)
                        self.assertEqual(vars(cart), before)

    def test_partner_rejects_non_cart_inputs(self):
        for value in (None, {}, {"unit_cents": 100, "quantity": 1}, object()):
            with self.subTest(value=value):
                with self.assertRaises(CartError):
                    quote_partner(value)

    def test_web_rejects_malformed_payloads(self):
        for payload in (None, [], Cart(100, 1), {}, {"unit_cents": 100}, {"quantity": 1}):
            with self.subTest(payload=payload):
                with self.assertRaises(CartError):
                    quote_web(payload)

    def test_web_defaults_discount_without_mutating_payload(self):
        payload = {"unit_cents": 125, "quantity": 2, "metadata": {"tags": ["new"]}}
        before = deepcopy(payload)
        self.assertEqual(quote_web(payload), 250)
        self.assertEqual(payload, before)

    def test_partner_does_not_mutate_cart(self):
        cart = Cart(199, 3, 10)
        before = vars(cart).copy()
        self.assertEqual(quote_partner(cart), 537)
        self.assertEqual(vars(cart), before)

    def test_parser_returns_validated_immutable_cart(self):
        cart = parse_cart({"unit_cents": 199, "quantity": 3, "discount": 10})
        self.assertEqual(cart, Cart(199, 3, 10))
        with self.assertRaises(FrozenInstanceError):
            cart.quantity = 4

    def test_web_calls_trusted_parser_once_without_revalidation(self):
        cart = parse_cart({"unit_cents": 199, "quantity": 3, "discount": 10})
        payload = object()
        parser = Mock(return_value=cart)
        with patch("app.validate_cart", side_effect=AssertionError("revalidation")):
            with patch("domain.validate_cart", side_effect=AssertionError("revalidation")):
                self.assertEqual(quote_web(payload, parser=parser), 537)
        parser.assert_called_once_with(payload)

    def test_web_preserves_parser_validation_errors(self):
        error = CartError("invalid cart")
        parser = Mock(side_effect=error)
        payload = object()
        with self.assertRaises(CartError) as caught:
            quote_web(payload, parser=parser)
        self.assertIs(caught.exception, error)
        parser.assert_called_once_with(payload)


if __name__ == "__main__":
    unittest.main()
