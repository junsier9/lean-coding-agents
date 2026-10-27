from dataclasses import FrozenInstanceError
import unittest

from app import prepare_quote
from domain import Line, Quote, QuoteError


class PrepareQuoteTests(unittest.TestCase):
    def test_preserves_order_duplicates_and_integer_total(self):
        cart = [
            {"sku": "pear", "quantity": 2},
            {"sku": "apple", "quantity": 3},
            {"sku": "pear", "quantity": 1},
        ]

        quote = prepare_quote(cart, {"pear": 125, "apple": 0})

        self.assertEqual(
            quote,
            Quote((Line("pear", 125, 2), Line("apple", 0, 3), Line("pear", 125, 1)), 375),
        )
        self.assertIs(type(quote.total), int)

    def test_empty_cart_is_valid(self):
        self.assertEqual(prepare_quote([], {}), Quote((), 0))

    def test_snapshot_survives_changes_to_all_input_containers(self):
        row = {"sku": "pear", "quantity": 2}
        cart = [row]
        catalog = {"pear": 125}
        quote = prepare_quote(cart, catalog)
        expected = Quote((Line("pear", 125, 2),), 250)

        row.update(sku="apple", quantity=10)
        self.assertEqual(quote, expected)
        cart.append({"sku": "pear", "quantity": 3})
        self.assertEqual(quote, expected)
        cart.clear()
        self.assertEqual(quote, expected)
        catalog["pear"] = 999
        self.assertEqual(quote, expected)
        catalog.clear()
        self.assertEqual(quote, expected)

    def test_quote_cannot_modify_caller_data(self):
        cart = [{"sku": "pear", "quantity": 2}]
        catalog = {"pear": 125}
        quote = prepare_quote(cart, catalog)

        for attribute, value in (("sku", "apple"), ("unit_cents", 999), ("quantity", 9)):
            with self.subTest(attribute=attribute):
                with self.assertRaises(FrozenInstanceError):
                    setattr(quote.lines[0], attribute, value)
        for attribute, value in (("lines", ()), ("total", 0)):
            with self.subTest(attribute=attribute):
                with self.assertRaises(FrozenInstanceError):
                    setattr(quote, attribute, value)
        with self.assertRaises(TypeError):
            quote.lines[0] = Line("apple", 999, 9)

        self.assertEqual(cart, [{"sku": "pear", "quantity": 2}])
        self.assertEqual(catalog, {"pear": 125})

    def test_invalid_containers_rows_and_skus_raise_quote_error(self):
        cases = [
            (None, {}, "container"),
            ((), {}, "container"),
            ([], None, "container"),
            ([], [], "container"),
            ([None], {}, "row"),
            ([[]], {}, "row"),
            ([{}], {}, "sku"),
            ([{"sku": 1, "quantity": 1}], {1: 125}, "sku"),
            ([{"sku": "missing", "quantity": 1}], {"pear": 125}, "sku"),
        ]
        for cart, catalog, message in cases:
            with self.subTest(cart=cart, catalog=catalog):
                with self.assertRaisesRegex(QuoteError, f"^{message}$"):
                    prepare_quote(cart, catalog)

    def test_quantity_requires_strict_positive_integer(self):
        for quantity in (None, False, True, 0, -1, 1.0, "1"):
            with self.subTest(quantity=quantity):
                with self.assertRaisesRegex(QuoteError, "^quantity$"):
                    prepare_quote([{"sku": "pear", "quantity": quantity}], {"pear": 125})
        with self.assertRaisesRegex(QuoteError, "^quantity$"):
            prepare_quote([{"sku": "pear"}], {"pear": 125})

    def test_price_requires_strict_nonnegative_integer(self):
        for price in (None, False, True, -1, 1.0, "125"):
            with self.subTest(price=price):
                with self.assertRaisesRegex(QuoteError, "^price$"):
                    prepare_quote([{"sku": "pear", "quantity": 1}], {"pear": price})


if __name__ == "__main__":
    unittest.main()
