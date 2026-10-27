from dataclasses import FrozenInstanceError
import unittest

from app import prepare_quote
from domain import Line, Quote, QuoteError


class QuoteTests(unittest.TestCase):
    def test_snapshot_retains_order_prices_quantities_and_total(self):
        quote = prepare_quote(
            [
                {"sku": "b", "quantity": 2},
                {"sku": "a", "quantity": 3},
                {"sku": "b", "quantity": 1},
            ],
            {"a": 125, "b": 50},
        )

        self.assertIsInstance(quote, Quote)
        self.assertEqual(
            quote.lines,
            (Line("b", 50, 2), Line("a", 125, 3), Line("b", 50, 1)),
        )
        self.assertEqual(quote.total, 525)
        self.assertIs(type(quote.total), int)

    def test_caller_mutations_do_not_change_delayed_quote(self):
        row = {"sku": "a", "quantity": 2}
        cart = [row]
        catalog = {"a": 125, "b": 999}
        quote = prepare_quote(cart, catalog)

        # Change every input before reading the quote for the first time.
        row.update(sku="b", quantity=100)
        cart.append({"sku": "b", "quantity": 3})
        catalog["a"] = 500
        catalog.clear()
        cart.clear()

        self.assertEqual(quote.lines, (Line("a", 125, 2),))
        self.assertEqual(quote.total, 250)

    def test_quote_is_immutable_and_does_not_expose_caller_containers(self):
        cart = [{"sku": "a", "quantity": 2}]
        catalog = {"a": 125}
        quote = prepare_quote(cart, catalog)

        self.assertIsInstance(quote.lines, tuple)
        for attribute, value in (("lines", ()), ("total", 0)):
            with self.subTest(attribute=attribute):
                with self.assertRaises(FrozenInstanceError):
                    setattr(quote, attribute, value)
        for attribute, value in (
            ("sku", "b"), ("unit_cents", 0), ("quantity", 100)
        ):
            with self.subTest(attribute=attribute):
                with self.assertRaises(FrozenInstanceError):
                    setattr(quote.lines[0], attribute, value)
        with self.assertRaises(TypeError):
            quote.lines[0] = Line("b", 0, 100)
        self.assertFalse(hasattr(quote, "cart"))
        self.assertFalse(hasattr(quote, "catalog"))
        self.assertEqual(cart, [{"sku": "a", "quantity": 2}])
        self.assertEqual(catalog, {"a": 125})

    def test_empty_cart_and_zero_price_are_valid(self):
        empty = prepare_quote([], {})
        self.assertEqual(empty.lines, ())
        self.assertEqual(empty.total, 0)
        self.assertIs(type(empty.total), int)
        free = prepare_quote([{"sku": "free", "quantity": 3}], {"free": 0})
        self.assertEqual(free.lines, (Line("free", 0, 3),))
        self.assertEqual(free.total, 0)

    def test_invalid_containers_rows_and_skus_raise_quote_error(self):
        cases = [
            (None, {}),
            ((), {}),
            ([], None),
            ([], []),
            ([None], {}),
            ([[]], {}),
            ([{}], {}),
            ([{"sku": None, "quantity": 1}], {}),
            ([{"sku": [], "quantity": 1}], {}),
            ([{"sku": "missing", "quantity": 1}], {}),
            ([{"sku": "a"}], {"a": 1}),
        ]
        for cart, catalog in cases:
            with self.subTest(cart=cart, catalog=catalog):
                with self.assertRaises(QuoteError):
                    prepare_quote(cart, catalog)

    def test_quantities_must_be_strict_positive_integers(self):
        class IntSubclass(int):
            pass

        for quantity in (None, False, True, 0, -1, 1.0, "1", IntSubclass(1)):
            with self.subTest(quantity=quantity, kind=type(quantity)):
                with self.assertRaises(QuoteError):
                    prepare_quote([{"sku": "a", "quantity": quantity}], {"a": 1})

    def test_prices_must_be_strict_nonnegative_integers(self):
        class IntSubclass(int):
            pass

        for price in (None, False, True, -1, 1.0, "1", IntSubclass(1)):
            with self.subTest(price=price, kind=type(price)):
                with self.assertRaises(QuoteError):
                    prepare_quote([{"sku": "a", "quantity": 1}], {"a": price})


if __name__ == "__main__":
    unittest.main()
