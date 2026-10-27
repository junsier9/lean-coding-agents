import unittest
from dataclasses import FrozenInstanceError

from app import prepare_quote
from domain import Line, Quote, QuoteError


class PrepareQuoteTests(unittest.TestCase):
    def test_preserves_order_duplicates_and_zero_prices(self):
        quote = prepare_quote(
            [
                {"sku": "b", "quantity": 2},
                {"sku": "a", "quantity": 3},
                {"sku": "b", "quantity": 1},
                {"sku": "free", "quantity": 4},
            ],
            {"a": 40, "b": 125, "free": 0},
        )

        self.assertEqual(
            quote.lines,
            (
                Line("b", 125, 2),
                Line("a", 40, 3),
                Line("b", 125, 1),
                Line("free", 0, 4),
            ),
        )
        self.assertEqual(quote.total, 495)
        self.assertIs(type(quote.total), int)

    def test_quote_retains_snapshot_after_inputs_change(self):
        row = {"sku": "a", "quantity": 2}
        cart = [row, {"sku": "b", "quantity": 3}]
        catalog = {"a": 125, "b": 40}
        quote = prepare_quote(cart, catalog)
        expected = (Line("a", 125, 2), Line("b", 40, 3))

        row["sku"] = "b"
        row["quantity"] = 100
        cart[1].clear()
        self.assertEqual(quote.lines, expected)
        self.assertEqual(quote.total, 370)

        cart[:] = [{"sku": "a", "quantity": 99}]
        catalog["a"] = 999
        del catalog["b"]
        self.assertEqual(quote.lines, expected)
        self.assertEqual(quote.total, 370)

        cart.clear()
        catalog.clear()
        self.assertEqual(quote.lines, expected)
        self.assertEqual(quote.total, 370)

    def test_quote_is_immutable_and_does_not_modify_inputs(self):
        cart = [{"sku": "a", "quantity": 2}]
        catalog = {"a": 125}
        quote = prepare_quote(cart, catalog)

        self.assertIsInstance(quote, Quote)
        for name, value in (("lines", ()), ("total", 0)):
            with self.subTest(field=name):
                with self.assertRaises(FrozenInstanceError):
                    setattr(quote, name, value)
        for name, value in (("sku", "b"), ("unit_cents", 0), ("quantity", 99)):
            with self.subTest(line_field=name):
                with self.assertRaises(FrozenInstanceError):
                    setattr(quote.lines[0], name, value)
        with self.assertRaises(TypeError):
            quote.lines[0] = Line("b", 0, 99)

        self.assertEqual(cart, [{"sku": "a", "quantity": 2}])
        self.assertEqual(catalog, {"a": 125})
        self.assertEqual(quote.lines, (Line("a", 125, 2),))
        self.assertEqual(quote.total, 250)

    def test_empty_cart_is_valid(self):
        quote = prepare_quote([], {})

        self.assertEqual(quote.lines, ())
        self.assertEqual(quote.total, 0)
        self.assertIs(type(quote.total), int)

    def test_invalid_inputs_raise_quote_error(self):
        cases = [
            (None, {}),
            ((), {}),
            ([], None),
            ([], []),
            ([None], {}),
            ([[]], {}),
            ([{}], {}),
            ([{"quantity": 1}], {"a": 0}),
            ([{"sku": [], "quantity": 1}], {"a": 0}),
            ([{"sku": 1, "quantity": 1}], {1: 0}),
            ([{"sku": "missing", "quantity": 1}], {"a": 0}),
            ([{"sku": "a"}], {"a": 0}),
        ]
        cases.extend(
            ([{"sku": "a", "quantity": quantity}], {"a": 0})
            for quantity in (None, True, False, 0, -1, 1.0, "1")
        )
        cases.extend(
            ([{"sku": "a", "quantity": 1}], {"a": price})
            for price in (None, True, False, -1, 1.0, "1")
        )

        for cart, catalog in cases:
            with self.subTest(cart=cart, catalog=catalog):
                with self.assertRaises(QuoteError):
                    prepare_quote(cart, catalog)


if __name__ == "__main__":
    unittest.main()
