import unittest
from dataclasses import FrozenInstanceError

from app import prepare_quote
from domain import Line, Quote, QuoteError


class PrepareQuoteTests(unittest.TestCase):
    def test_preserves_order_duplicates_and_integer_total(self):
        cart = [
            {"sku": "b", "quantity": 2},
            {"sku": "a", "quantity": 3},
            {"sku": "b", "quantity": 1},
        ]

        quote = prepare_quote(cart, {"a": 125, "b": 40})

        self.assertIsInstance(quote, Quote)
        self.assertEqual(
            quote.lines,
            (Line("b", 40, 2), Line("a", 125, 3), Line("b", 40, 1)),
        )
        self.assertEqual(quote.total, 495)
        self.assertIs(type(quote.total), int)

    def test_empty_cart_and_zero_price_are_valid(self):
        self.assertEqual(prepare_quote([], {}), Quote((), 0))
        self.assertEqual(
            prepare_quote([{"sku": "free", "quantity": 2}], {"free": 0}),
            Quote((Line("free", 0, 2),), 0),
        )

    def test_snapshot_survives_nested_row_and_container_changes(self):
        row = {"sku": "a", "quantity": 2}
        cart = [row]
        catalog = {"a": 125, "b": 999}
        quote = prepare_quote(cart, catalog)

        row["sku"] = "b"
        row["quantity"] = 10
        catalog["a"] = 500
        cart.append({"sku": "b", "quantity": 1})
        self.assertEqual(quote, Quote((Line("a", 125, 2),), 250))

        row.clear()
        cart.clear()
        catalog.clear()
        self.assertEqual(quote, Quote((Line("a", 125, 2),), 250))

    def test_quote_is_immutable_and_does_not_mutate_inputs(self):
        cart = [{"sku": "a", "quantity": 2}]
        catalog = {"a": 125}
        quote = prepare_quote(cart, catalog)

        with self.assertRaises(FrozenInstanceError):
            quote.total = 0
        with self.assertRaises(FrozenInstanceError):
            quote.lines = ()
        with self.assertRaises(TypeError):
            quote.lines[0] = Line("a", 0, 1)
        for field, value in (("sku", "b"), ("unit_cents", 0), ("quantity", 1)):
            with self.subTest(field=field):
                with self.assertRaises(FrozenInstanceError):
                    setattr(quote.lines[0], field, value)

        self.assertEqual(cart, [{"sku": "a", "quantity": 2}])
        self.assertEqual(catalog, {"a": 125})

    def test_invalid_containers_rows_and_skus_raise_quote_error(self):
        cases = [
            (None, {}, "container"),
            ((), {}, "container"),
            ([], [], "container"),
            ([None], {}, "row"),
            ([[]], {}, "row"),
            ([{}], {}, "sku"),
            ([{"sku": "missing", "quantity": 1}], {}, "sku"),
            ([{"sku": [], "quantity": 1}], {}, "sku"),
        ]
        for cart, catalog, error in cases:
            with self.subTest(cart=cart, catalog=catalog):
                with self.assertRaisesRegex(QuoteError, f"^{error}$"):
                    prepare_quote(cart, catalog)

    def test_quantity_requires_a_strictly_positive_int(self):
        for quantity in (None, False, True, 0, -1, 1.0, "1"):
            with self.subTest(quantity=quantity):
                with self.assertRaisesRegex(QuoteError, "^quantity$"):
                    prepare_quote([{"sku": "a", "quantity": quantity}], {"a": 125})
        with self.assertRaisesRegex(QuoteError, "^quantity$"):
            prepare_quote([{"sku": "a"}], {"a": 125})

    def test_price_requires_a_nonnegative_strict_int(self):
        for price in (None, False, True, -1, 1.0, "1"):
            with self.subTest(price=price):
                with self.assertRaisesRegex(QuoteError, "^price$"):
                    prepare_quote([{"sku": "a", "quantity": 1}], {"a": price})

    def test_later_invalid_row_is_not_skipped(self):
        cart = [{"sku": "a", "quantity": 1}, {"sku": "a", "quantity": 0}]
        with self.assertRaisesRegex(QuoteError, "^quantity$"):
            prepare_quote(cart, {"a": 125})
        self.assertEqual(cart[1]["quantity"], 0)


if __name__ == "__main__":
    unittest.main()
