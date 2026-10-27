import unittest
from copy import deepcopy
from dataclasses import FrozenInstanceError, asdict
from unittest.mock import Mock, patch

from app import quote_partner, quote_web
from domain import Cart, CartError, parse_cart


class QuoteTests(unittest.TestCase):
    def test_both_routes_return_integer_cents(self):
        cases = (
            (Cart(199, 3, 25), 447),
            (Cart(101, 3, 33), 203),
            (Cart(0, 5, 50), 0),
            (Cart(125, 2, 0), 250),
            (Cart(125, 2, 100), 0),
            (Cart(10**20 + 1, 3, 50), 150000000000000000001),
        )
        for cart, expected in cases:
            for route, argument in (
                (quote_web, asdict(cart)),
                (quote_partner, cart),
            ):
                with self.subTest(route=route.__name__, cart=cart):
                    result = route(argument)
                    self.assertIs(type(result), int)
                    self.assertEqual(result, expected)

    def test_both_routes_reject_invalid_fields(self):
        invalid_values = {
            "unit_cents": (-1, True, False, 1.5, "100", None),
            "quantity": (-1, 0, True, False, 1.0, 1.5, "2", None),
            "discount": (-1, 101, True, False, 0.0, 50.5, "50", None),
        }
        for field, values in invalid_values.items():
            for value in values:
                payload = {"unit_cents": 100, "quantity": 2, "discount": 10}
                payload[field] = value
                for route, argument in (
                    (quote_web, payload),
                    (quote_partner, Cart(**payload)),
                ):
                    with self.subTest(route=route.__name__, field=field, value=value):
                        with self.assertRaises(CartError):
                            route(argument)

    def test_partner_rejects_objects_that_are_not_carts(self):
        for value in (None, {}, {"unit_cents": 100, "quantity": 2}, (100, 2, 0)):
            with self.subTest(value=value):
                with self.assertRaises(CartError):
                    quote_partner(value)

    def test_web_rejects_malformed_payloads(self):
        for payload in (None, [], Cart(100, 2), {}, {"unit_cents": 100}, {"quantity": 2}):
            with self.subTest(payload=payload):
                with self.assertRaises(CartError):
                    quote_web(payload)

    def test_web_defaults_discount_to_zero(self):
        payload = {"unit_cents": 125, "quantity": 3}
        self.assertEqual(quote_web(payload), 375)
        self.assertEqual(payload, {"unit_cents": 125, "quantity": 3})

    def test_web_calls_parser_once(self):
        payload = {"unit_cents": 199, "quantity": 3, "discount": 25}
        parser = Mock(wraps=parse_cart)
        self.assertEqual(quote_web(payload, parser=parser), 447)
        parser.assert_called_once_with(payload)
        self.assertIs(parser.call_args.args[0], payload)

    def test_web_trusts_validated_cart_from_injected_parser(self):
        payload = {"unit_cents": 199, "quantity": 3, "discount": 25}
        parser = Mock(return_value=Cart(199, 3, 25))
        with patch("app.validate_cart", side_effect=AssertionError("redundant validation")):
            self.assertEqual(quote_web(payload, parser=parser), 447)
        parser.assert_called_once_with(payload)

    def test_web_propagates_parser_error_without_retry(self):
        parser = Mock(side_effect=CartError("invalid cart"))
        with self.assertRaises(CartError):
            quote_web({}, parser=parser)
        parser.assert_called_once_with({})

    def test_quotes_do_not_modify_inputs(self):
        payload = {
            "unit_cents": 199,
            "quantity": 3,
            "discount": 25,
            "metadata": {"tags": ["original"]},
        }
        original = deepcopy(payload)
        cart = parse_cart(payload)
        self.assertEqual(quote_web(payload), 447)
        self.assertEqual(quote_partner(cart), 447)
        self.assertEqual(payload, original)
        self.assertEqual(cart, Cart(199, 3, 25))
        with self.assertRaises(FrozenInstanceError):
            cart.quantity = 4

    def test_rejected_inputs_are_not_modified(self):
        payload = {"unit_cents": 199, "quantity": 0, "discount": 25}
        original = deepcopy(payload)
        cart = Cart(**payload)
        with self.assertRaises(CartError):
            quote_web(payload)
        with self.assertRaises(CartError):
            quote_partner(cart)
        self.assertEqual(payload, original)
        self.assertEqual(asdict(cart), original)


if __name__ == "__main__":
    unittest.main()
