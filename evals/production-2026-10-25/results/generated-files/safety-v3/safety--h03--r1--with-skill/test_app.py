from dataclasses import FrozenInstanceError
import unittest

from app import prepare_quote
from domain import Line, Quote, QuoteError


class PrepareQuoteTests(unittest.TestCase):
    def test_preserves_order_duplicates_and_zero_prices(self):
        cart = [
            {"sku": "b", "quantity": 2},
            {"sku": "a", "quantity": 3},
            {"sku": "b", "quantity": 1},
        ]
        catalog = {"a": 0, "b": 125}

        quote = prepare_quote(cart, catalog)

        self.assertEqual(
            quote,
            Quote((Line("b", 125, 2), Line("a", 0, 3), Line("b", 125, 1)), 375),
        )
        self.assertEqual(cart, [
            {"sku": "b", "quantity": 2},
            {"sku": "a", "quantity": 3},
            {"sku": "b", "quantity": 1},
        ])
        self.assertEqual(catalog, {"a": 0, "b": 125})

    def test_snapshot_survives_input_mutation(self):
        row = {"sku": "a", "quantity": 2}
        cart = [row, {"sku": "b", "quantity": 1}]
        catalog = {"a": 199, "b": 250}
        quote = prepare_quote(cart, catalog)
        expected_lines = (Line("a", 199, 2), Line("b", 250, 1))

        row.update(sku="b", quantity=99)
        self.assertEqual(quote.lines, expected_lines)
        self.assertEqual(quote.total, 648)

        catalog["a"] = 999
        del catalog["b"]
        self.assertEqual(quote.lines, expected_lines)
        self.assertEqual(quote.total, 648)

        cart.clear()
        cart.append({"sku": "missing", "quantity": 0})
        catalog.clear()
        self.assertEqual(quote.lines, expected_lines)
        self.assertEqual(quote.total, 648)

    def test_quote_and_lines_are_immutable(self):
        cart = [{"sku": "a", "quantity": 2}]
        catalog = {"a": 100}
        quote = prepare_quote(cart, catalog)

        for target, attribute, value in (
            (quote, "lines", ()),
            (quote, "total", 0),
            (quote.lines[0], "sku", "b"),
            (quote.lines[0], "unit_cents", 0),
            (quote.lines[0], "quantity", 99),
        ):
            with self.subTest(attribute=attribute):
                with self.assertRaises(FrozenInstanceError):
                    setattr(target, attribute, value)
        with self.assertRaises(TypeError):
            quote.lines[0] = Line("b", 0, 99)

        self.assertEqual(cart, [{"sku": "a", "quantity": 2}])
        self.assertEqual(catalog, {"a": 100})

    def test_empty_cart(self):
        self.assertEqual(prepare_quote([], {}), Quote((), 0))

    def test_invalid_inputs_raise_quote_error(self):
        cases = [
            ("cart container", (), {}, "container"),
            ("catalog container", [], [], "container"),
            ("row", [None], {}, "row"),
            ("missing sku", [{"quantity": 1}], {"a": 100}, "sku"),
            ("non-string sku", [{"sku": 1, "quantity": 1}], {1: 100}, "sku"),
            ("unknown sku", [{"sku": "b", "quantity": 1}], {"a": 100}, "sku"),
        ]
        for quantity in (None, False, True, 0, -1, 1.5, "2"):
            cases.append((
                f"quantity {quantity!r}",
                [{"sku": "a", "quantity": quantity}], {"a": 100}, "quantity",
            ))
        cases.append(("missing quantity", [{"sku": "a"}], {"a": 100}, "quantity"))
        for price in (None, False, True, -1, 1.5, "100"):
            cases.append((
                f"price {price!r}",
                [{"sku": "a", "quantity": 1}], {"a": price}, "price",
            ))

        for name, cart, catalog, message in cases:
            with self.subTest(name=name):
                with self.assertRaisesRegex(QuoteError, f"^{message}$"):
                    prepare_quote(cart, catalog)


if __name__ == "__main__":
    unittest.main()
