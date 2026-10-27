import copy
import unittest
from unittest.mock import Mock

from app import quote_partner, quote_web
from domain import Cart, CartError, parse_cart


class QuoteTests(unittest.TestCase):
    def test_prices_on_both_routes(self):
        cases = [
            (0, 1, 0, 0),
            (199, 3, 0, 597),
            (199, 3, 100, 0),
            (123, 3, 25, 276),
            (101, 3, 50, 151),
            (10**20 + 1, 3, 1, 297000000000000000002),
        ]
        for unit_cents, quantity, discount, expected in cases:
            payload = dict(unit_cents=unit_cents, quantity=quantity, discount=discount)
            for quote, value in ((quote_web, payload), (quote_partner, Cart(**payload))):
                with self.subTest(route=quote.__name__, payload=payload):
                    price = quote(value)
                    self.assertEqual(price, expected)
                    self.assertIs(type(price), int)

    def test_invalid_fields_on_both_routes(self):
        invalid_values = {
            "unit_cents": (-1, True, False, 1.0, "1", None),
            "quantity": (-1, 0, True, False, 1.0, "1", None),
            "discount": (-1, 101, True, False, 1.0, "1", None),
        }
        for field, values in invalid_values.items():
            for invalid in values:
                payload = dict(unit_cents=199, quantity=3, discount=25)
                payload[field] = invalid
                cart = Cart(**payload)
                before = payload.copy()
                for quote, value in ((quote_web, payload), (quote_partner, cart)):
                    with self.subTest(route=quote.__name__, field=field, invalid=invalid):
                        with self.assertRaises(CartError):
                            quote(value)
                        self.assertEqual(payload, before)
                        self.assertEqual(vars(cart), before)

    def test_partner_rejects_non_cart(self):
        for value in (None, {}, dict(unit_cents=199, quantity=3), object()):
            with self.subTest(value=value):
                with self.assertRaises(CartError):
                    quote_partner(value)

    def test_web_rejects_invalid_payloads(self):
        for payload in (None, [], object(), {}, {"unit_cents": 199}, {"quantity": 3}):
            with self.subTest(payload=payload):
                with self.assertRaises(CartError):
                    quote_web(payload)

    def test_web_defaults_discount_to_zero(self):
        self.assertEqual(quote_web(dict(unit_cents=199, quantity=3)), 597)

    def test_inputs_remain_unchanged(self):
        payload = dict(unit_cents=199, quantity=3, discount=25, metadata={"tags": ["sale"]})
        before = copy.deepcopy(payload)
        self.assertEqual(quote_web(payload), 447)
        self.assertEqual(payload, before)
        cart = Cart(199, 3, 25)
        before_cart = vars(cart).copy()
        self.assertEqual(quote_partner(cart), 447)
        self.assertEqual(vars(cart), before_cart)

    def test_web_calls_injected_parser_once(self):
        payload = {"partner_format": "cart"}
        parser = Mock(return_value=parse_cart(dict(unit_cents=199, quantity=3, discount=25)))
        self.assertEqual(quote_web(payload, parser=parser), 447)
        parser.assert_called_once_with(payload)

    def test_web_preserves_parser_failures(self):
        payload = {}
        for error in (CartError("invalid cart"), RuntimeError("parser failed")):
            with self.subTest(error=error):
                parser = Mock(side_effect=error)
                with self.assertRaises(type(error)) as caught:
                    quote_web(payload, parser=parser)
                self.assertIs(caught.exception, error)
                parser.assert_called_once_with(payload)


if __name__ == "__main__":
    unittest.main()
