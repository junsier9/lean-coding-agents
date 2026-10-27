from dataclasses import FrozenInstanceError
import unittest

from app import prepare_quote
from domain import Line, Quote, QuoteError


class PrepareQuoteTests(unittest.TestCase):
    def test_preserves_order_duplicates_and_integer_total(self):
        quote = prepare_quote(
            [
                {"sku": "b", "quantity": 2},
                {"sku": "a", "quantity": 3},
                {"sku": "b", "quantity": 1},
            ],
            {"a": 0, "b": 125},
        )

        self.assertEqual(
            quote,
            Quote((Line("b", 125, 2), Line("a", 0, 3), Line("b", 125, 1)), 375),
        )
        self.assertIs(type(quote.total), int)

    def test_empty_cart_is_valid(self):
        self.assertEqual(prepare_quote([], {}), Quote((), 0))

    def test_snapshot_survives_changes_to_all_input_containers(self):
        row = {"sku": "a", "quantity": 2}
        cart = [row]
        catalog = {"a": 125, "b": 900}
        quote = prepare_quote(cart, catalog)
        expected_lines = (Line("a", 125, 2),)

        row.update(sku="b", quantity=7)
        self.assertEqual(quote.lines, expected_lines)
        self.assertEqual(quote.total, 250)

        catalog["a"] = 500
        catalog["b"] = 1
        self.assertEqual(quote.lines, expected_lines)
        self.assertEqual(quote.total, 250)

        cart.append({"sku": "b", "quantity": 10})
        self.assertEqual(quote.lines, expected_lines)
        self.assertEqual(quote.total, 250)

        row.clear()
        cart.clear()
        catalog.clear()
        self.assertEqual(quote.lines, expected_lines)
        self.assertEqual(quote.total, 250)

    def test_quote_and_lines_are_immutable_without_changing_inputs(self):
        row = {"sku": "a", "quantity": 2}
        cart = [row]
        catalog = {"a": 125}
        quote = prepare_quote(cart, catalog)

        for field, value in (("lines", ()), ("total", 0)):
            with self.subTest(field=field):
                with self.assertRaises(FrozenInstanceError):
                    setattr(quote, field, value)
        for field, value in (("sku", "b"), ("unit_cents", 0), ("quantity", 9)):
            with self.subTest(field=field):
                with self.assertRaises(FrozenInstanceError):
                    setattr(quote.lines[0], field, value)
        with self.assertRaises(TypeError):
            quote.lines[0] = Line("b", 0, 9)

        self.assertEqual(cart, [{"sku": "a", "quantity": 2}])
        self.assertIs(cart[0], row)
        self.assertEqual(catalog, {"a": 125})

    def test_invalid_containers_raise_quote_error(self):
        for cart, catalog in ((None, {}), ((), {}), ({}, {}), ([], None), ([], [])):
            with self.subTest(cart=cart, catalog=catalog):
                with self.assertRaisesRegex(QuoteError, "^container$"):
                    prepare_quote(cart, catalog)

    def test_invalid_rows_raise_quote_error(self):
        for row in (None, [], ("a", 1), "a"):
            with self.subTest(row=row):
                with self.assertRaisesRegex(QuoteError, "^row$"):
                    prepare_quote([row], {"a": 125})

    def test_invalid_skus_raise_quote_error(self):
        for row in ({"quantity": 1}, {"sku": None, "quantity": 1},
                    {"sku": [], "quantity": 1}, {"sku": "missing", "quantity": 1}):
            with self.subTest(row=row):
                with self.assertRaisesRegex(QuoteError, "^sku$"):
                    prepare_quote([row], {"a": 125})

    def test_quantity_must_be_a_strict_positive_integer(self):
        for quantity in (None, False, True, 0, -1, 1.0, "1"):
            with self.subTest(quantity=quantity):
                with self.assertRaisesRegex(QuoteError, "^quantity$"):
                    prepare_quote([{"sku": "a", "quantity": quantity}], {"a": 125})
        with self.assertRaisesRegex(QuoteError, "^quantity$"):
            prepare_quote([{"sku": "a"}], {"a": 125})

    def test_price_must_be_a_strict_nonnegative_integer(self):
        for price in (None, False, True, -1, 1.0, "1"):
            with self.subTest(price=price):
                with self.assertRaisesRegex(QuoteError, "^price$"):
                    prepare_quote([{"sku": "a", "quantity": 1}], {"a": price})


if __name__ == "__main__":
    unittest.main()
