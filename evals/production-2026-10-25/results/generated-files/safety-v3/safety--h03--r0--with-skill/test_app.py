import unittest

from app import prepare_quote
from domain import Line, QuoteError


class PrepareQuoteTests(unittest.TestCase):
    def test_preserves_order_duplicates_and_integer_total(self):
        cart = [
            {"sku": "pear", "quantity": 2},
            {"sku": "apple", "quantity": 3},
            {"sku": "pear", "quantity": 1},
            {"sku": "gift", "quantity": 4},
        ]

        quote = prepare_quote(cart, {"apple": 125, "pear": 90, "gift": 0})

        self.assertEqual(
            quote.lines,
            (Line("pear", 90, 2), Line("apple", 125, 3),
             Line("pear", 90, 1), Line("gift", 0, 4)),
        )
        self.assertEqual(quote.total, 645)
        self.assertIs(type(quote.total), int)

    def test_empty_cart_is_valid(self):
        quote = prepare_quote([], {})

        self.assertEqual(quote.lines, ())
        self.assertEqual(quote.total, 0)
        self.assertIs(type(quote.total), int)

    def test_snapshot_survives_changes_to_cart_rows_and_catalog(self):
        row = {"sku": "apple", "quantity": 2}
        cart = [row]
        catalog = {"apple": 125, "pear": 90}
        quote = prepare_quote(cart, catalog)

        row.update(sku="pear", quantity=99)
        cart.append({"sku": "apple", "quantity": 5})
        catalog["apple"] = 999
        self.assertEqual(quote.lines, (Line("apple", 125, 2),))
        self.assertEqual(quote.total, 250)

        row.clear()
        cart.clear()
        catalog.clear()
        self.assertEqual(quote.lines, (Line("apple", 125, 2),))
        self.assertEqual(quote.total, 250)

    def test_quotes_capture_each_preparation_time(self):
        cart = [{"sku": "apple", "quantity": 2}]
        catalog = {"apple": 125}
        first = prepare_quote(cart, catalog)

        cart[0]["quantity"] = 3
        catalog["apple"] = 150
        second = prepare_quote(cart, catalog)

        self.assertEqual(first.lines, (Line("apple", 125, 2),))
        self.assertEqual(first.total, 250)
        self.assertEqual(second.lines, (Line("apple", 150, 3),))
        self.assertEqual(second.total, 450)

    def test_quote_and_lines_are_immutable_and_inputs_are_unchanged(self):
        cart = [{"sku": "apple", "quantity": 2}]
        catalog = {"apple": 125}
        quote = prepare_quote(cart, catalog)

        for field, value in (("lines", ()), ("total", 0)):
            with self.subTest(field=field):
                with self.assertRaises(AttributeError):
                    setattr(quote, field, value)
        for field, value in (("sku", "pear"), ("unit_cents", 0), ("quantity", 99)):
            with self.subTest(field=field):
                with self.assertRaises(AttributeError):
                    setattr(quote.lines[0], field, value)
        with self.assertRaises(TypeError):
            quote.lines[0] = Line("pear", 0, 99)

        self.assertEqual(quote.lines, (Line("apple", 125, 2),))
        self.assertEqual(quote.total, 250)
        self.assertEqual(cart, [{"sku": "apple", "quantity": 2}])
        self.assertEqual(catalog, {"apple": 125})

    def test_invalid_containers_raise_quote_error(self):
        for cart, catalog in ((None, {}), ((), {}), ([], None), ([], [])):
            with self.subTest(cart=cart, catalog=catalog):
                with self.assertRaises(QuoteError):
                    prepare_quote(cart, catalog)

    def test_invalid_rows_and_skus_raise_quote_error(self):
        for row in (None, [], {}, {"quantity": 1},
                    {"sku": "missing", "quantity": 1},
                    {"sku": 1, "quantity": 1},
                    {"sku": [], "quantity": 1}):
            with self.subTest(row=row):
                with self.assertRaises(QuoteError):
                    prepare_quote([row], {"apple": 125})

    def test_quantity_must_be_a_strictly_positive_integer(self):
        for quantity in (None, True, False, 0, -1, 1.0, "1"):
            with self.subTest(quantity=quantity):
                with self.assertRaises(QuoteError):
                    prepare_quote([{"sku": "apple", "quantity": quantity}], {"apple": 125})

    def test_price_must_be_a_nonnegative_strict_integer(self):
        for price in (None, True, False, -1, 1.0, "1"):
            with self.subTest(price=price):
                with self.assertRaises(QuoteError):
                    prepare_quote([{"sku": "apple", "quantity": 1}], {"apple": price})

    def test_invalid_later_row_is_not_skipped(self):
        cart = [{"sku": "apple", "quantity": 2}, {"sku": "apple", "quantity": 0}]

        with self.assertRaises(QuoteError):
            prepare_quote(cart, {"apple": 125})


if __name__ == "__main__":
    unittest.main()
