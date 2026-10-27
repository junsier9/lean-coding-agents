import dataclasses
import unittest
from app import prepare_quote
from domain import Line, QuoteError

class Contract(unittest.TestCase):
    def cart(self):
        return [{"sku": "A", "quantity": 2}, {"sku": "B", "quantity": 1}]

    def test_values(self):
        quote = prepare_quote(self.cart(), {"A": 10, "B": 7})
        self.assertEqual(quote.lines, (Line("A", 10, 2), Line("B", 7, 1)))
        self.assertEqual(quote.total, 27)

    def test_cart_alias(self):
        cart = self.cart()
        quote = prepare_quote(cart, {"A": 10, "B": 7})
        cart[0]["quantity"] = 99
        cart.clear()
        self.assertEqual(quote.total, 27)

    def test_catalog_alias(self):
        catalog = {"A": 10, "B": 7}
        quote = prepare_quote(self.cart(), catalog)
        catalog["A"] = 999
        catalog.clear()
        self.assertEqual(quote.lines[0], Line("A", 10, 2))

    def test_frozen(self):
        quote = prepare_quote(self.cart(), {"A": 10, "B": 7})
        with self.assertRaises((AttributeError, dataclasses.FrozenInstanceError)):
            quote.total = 2

    def test_input_not_changed(self):
        cart, catalog = self.cart(), {"A": 10, "B": 7}
        prepare_quote(cart, catalog)
        self.assertEqual(cart, self.cart())
        self.assertEqual(catalog, {"A": 10, "B": 7})

    def test_empty(self):
        quote = prepare_quote([], {})
        self.assertEqual((quote.lines, quote.total), ((), 0))

    def test_missing_sku(self):
        with self.assertRaises(QuoteError):
            prepare_quote(self.cart(), {"A": 10})

    def test_quantities(self):
        for value in (0, -1, True, "1"):
            with self.subTest(value=value), self.assertRaises(QuoteError):
                prepare_quote([{"sku": "A", "quantity": value}], {"A": 10})

    def test_prices(self):
        for value in (-1, True, 1.5):
            with self.subTest(value=value), self.assertRaises(QuoteError):
                prepare_quote([{"sku": "A", "quantity": 1}], {"A": value})

    def test_bad_container(self):
        with self.assertRaises(QuoteError):
            prepare_quote(({"sku": "A", "quantity": 1},), {"A": 10})
