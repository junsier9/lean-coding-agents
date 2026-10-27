from copy import deepcopy
from dataclasses import FrozenInstanceError
import unittest

from app import prepare_quote
from domain import Line, Quote, QuoteError


class QuoteTests(unittest.TestCase):
    def test_preserves_order_duplicates_and_integer_total(self):
        quote = prepare_quote(
            [
                {"sku": "B", "quantity": 2},
                {"sku": "A", "quantity": 3},
                {"sku": "B", "quantity": 1},
            ],
            {"A": 125, "B": 250},
        )

        self.assertIsInstance(quote, Quote)
        self.assertEqual(
            quote.lines,
            (Line("B", 250, 2), Line("A", 125, 3), Line("B", 250, 1)),
        )
        self.assertEqual(quote.total, 1125)
        self.assertIs(type(quote.total), int)

    def test_snapshot_survives_nested_rows_cart_and_catalog_changes(self):
        row = {"sku": "A", "quantity": 2}
        cart = [row, {"sku": "B", "quantity": 1}]
        catalog = {"A": 125, "B": 300}
        quote = prepare_quote(cart, catalog)
        expected_lines = (Line("A", 125, 2), Line("B", 300, 1))

        row.update(sku="B", quantity=99)
        self.assertEqual(quote.lines, expected_lines)
        self.assertEqual(quote.total, 550)

        cart.reverse()
        cart.append({"sku": "A", "quantity": 100})
        cart.clear()
        self.assertEqual(quote.lines, expected_lines)
        self.assertEqual(quote.total, 550)

        catalog["A"] = 999
        del catalog["B"]
        catalog.clear()
        self.assertEqual(quote.lines, expected_lines)
        self.assertEqual(quote.total, 550)

    def test_quote_is_immutable_and_does_not_expose_caller_containers(self):
        cart = [{"sku": "A", "quantity": 2}]
        catalog = {"A": 125}
        before = deepcopy((cart, catalog))
        quote = prepare_quote(cart, catalog)

        self.assertIs(type(quote.lines), tuple)
        for target, field, value in (
            (quote, "lines", ()),
            (quote, "total", 0),
            (quote.lines[0], "sku", "B"),
            (quote.lines[0], "unit_cents", 999),
            (quote.lines[0], "quantity", 99),
        ):
            with self.subTest(field=field):
                with self.assertRaises(FrozenInstanceError):
                    setattr(target, field, value)
        with self.assertRaises(TypeError):
            quote.lines[0] = Line("B", 0, 1)
        self.assertFalse(hasattr(quote, "cart"))
        self.assertFalse(hasattr(quote, "catalog"))
        self.assertEqual((cart, catalog), before)

    def test_separate_calls_capture_their_own_prices_and_quantities(self):
        cart = [{"sku": "A", "quantity": 2}]
        catalog = {"A": 125}
        first = prepare_quote(cart, catalog)
        cart[0]["quantity"] = 3
        catalog["A"] = 200
        second = prepare_quote(cart, catalog)

        self.assertEqual(first.lines, (Line("A", 125, 2),))
        self.assertEqual(first.total, 250)
        self.assertEqual(second.lines, (Line("A", 200, 3),))
        self.assertEqual(second.total, 600)

    def test_empty_cart_and_zero_price_are_valid(self):
        self.assertEqual(prepare_quote([], {}), Quote((), 0))
        self.assertEqual(
            prepare_quote([{"sku": "free", "quantity": 3}], {"free": 0}),
            Quote((Line("free", 0, 3),), 0),
        )

    def test_invalid_containers_and_rows_raise_quote_error(self):
        for cart, catalog in (
            (None, {}),
            ((), {}),
            ({}, {}),
            ([], None),
            ([], []),
            ([None], {}),
            ([[]], {}),
            (["A"], {}),
        ):
            with self.subTest(cart=cart, catalog=catalog):
                with self.assertRaises(QuoteError):
                    prepare_quote(cart, catalog)

    def test_invalid_skus_raise_quote_error(self):
        for row in (
            {"quantity": 1},
            {"sku": None, "quantity": 1},
            {"sku": 1, "quantity": 1},
            {"sku": [], "quantity": 1},
            {"sku": "missing", "quantity": 1},
        ):
            with self.subTest(row=row):
                with self.assertRaises(QuoteError):
                    prepare_quote([row], {"A": 125})

    def test_quantity_must_be_a_strict_positive_integer(self):
        class IntegerSubclass(int):
            pass

        for quantity in (None, False, True, 0, -1, 1.0, "1", IntegerSubclass(1)):
            with self.subTest(quantity=quantity):
                with self.assertRaises(QuoteError):
                    prepare_quote([{"sku": "A", "quantity": quantity}], {"A": 125})
        with self.assertRaises(QuoteError):
            prepare_quote([{"sku": "A"}], {"A": 125})

    def test_price_must_be_a_strict_nonnegative_integer(self):
        class IntegerSubclass(int):
            pass

        for price in (None, False, True, -1, 0.0, 1.0, "1", IntegerSubclass(1)):
            with self.subTest(price=price):
                with self.assertRaises(QuoteError):
                    prepare_quote([{"sku": "A", "quantity": 1}], {"A": price})

    def test_invalid_later_row_does_not_mutate_inputs(self):
        cart = [{"sku": "A", "quantity": 2}, {"sku": "A", "quantity": 0}]
        catalog = {"A": 125}
        before = deepcopy((cart, catalog))

        with self.assertRaises(QuoteError):
            prepare_quote(cart, catalog)
        self.assertEqual((cart, catalog), before)


if __name__ == "__main__":
    unittest.main()
