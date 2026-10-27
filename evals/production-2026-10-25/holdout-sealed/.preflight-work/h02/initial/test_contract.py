import unittest
from app import quote_web, quote_partner
from domain import Cart, CartError, parse_cart

class Contract(unittest.TestCase):
    def test_web_price(self):
        self.assertEqual(quote_web({"unit_cents": 199, "quantity": 3, "discount": 10}), 537)

    def test_partner_price(self):
        self.assertEqual(quote_partner(Cart(250, 2, 20)), 400)

    def test_partner_negative_price(self):
        with self.assertRaises(CartError):
            quote_partner(Cart(-1, 2))

    def test_partner_bad_quantity(self):
        for q in (True, 0, -1, 1.5):
            with self.subTest(q=q), self.assertRaises(CartError):
                quote_partner(Cart(10, q))

    def test_partner_bad_discount(self):
        for d in (True, -1, 101, "10"):
            with self.subTest(d=d), self.assertRaises(CartError):
                quote_partner(Cart(100, 1, d))

    def test_web_invalid(self):
        with self.assertRaises(CartError):
            quote_web({"unit_cents": 10, "quantity": False})

    def test_web_missing_fields(self):
        with self.assertRaises(CartError):
            quote_web({"quantity": 1})

    def test_web_parser_once(self):
        calls = []
        def parser(payload):
            calls.append(payload)
            return parse_cart(payload)
        self.assertEqual(quote_web({"unit_cents": 5, "quantity": 2}, parser), 10)
        self.assertEqual(len(calls), 1)

    def test_input_unchanged(self):
        payload = {"unit_cents": 10, "quantity": 2}
        quote_web(payload)
        self.assertEqual(payload, {"unit_cents": 10, "quantity": 2})

    def test_boundary_prices(self):
        self.assertEqual(quote_partner(Cart(0, 1)), 0)
        self.assertEqual(quote_partner(Cart(100, 1, 100)), 0)
