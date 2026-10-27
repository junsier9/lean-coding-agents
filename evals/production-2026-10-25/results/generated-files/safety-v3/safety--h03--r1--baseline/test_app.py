import unittest

from app import QuoteError, prepare_quote


class IntSubclass(int):
    pass


class PrepareQuoteTests(unittest.TestCase):
    def test_preserves_order_duplicates_prices_and_quantities(self):
        quote = prepare_quote(
            [
                {"sku": "pear", "quantity": 2},
                {"sku": "apple", "quantity": 3},
                {"sku": "pear", "quantity": 1},
            ],
            {"apple": 99, "pear": 125},
        )

        self.assertEqual(
            [(line.sku, line.unit_cents, line.quantity) for line in quote.lines],
            [("pear", 125, 2), ("apple", 99, 3), ("pear", 125, 1)],
        )
        self.assertEqual(quote.total, 672)
        self.assertIs(type(quote.total), int)

    def test_cart_and_catalog_mutations_do_not_change_snapshot(self):
        row = {"sku": "pear", "quantity": 2}
        cart = [row]
        catalog = {"pear": 125, "apple": 99}
        quote = prepare_quote(cart, catalog)

        row.update(sku="apple", quantity=10)
        cart.append({"sku": "pear", "quantity": 100})
        catalog["pear"] = 999
        self.assertEqual(
            [(line.sku, line.unit_cents, line.quantity) for line in quote.lines],
            [("pear", 125, 2)],
        )
        self.assertEqual(quote.total, 250)

        row.clear()
        cart.clear()
        catalog.clear()
        self.assertEqual(len(quote.lines), 1)
        self.assertEqual(quote.lines[0].sku, "pear")
        self.assertEqual(quote.lines[0].unit_cents, 125)
        self.assertEqual(quote.lines[0].quantity, 2)
        self.assertEqual(quote.total, 250)

    def test_quote_is_immutable_and_cannot_mutate_caller_data(self):
        cart = [{"sku": "pear", "quantity": 2}]
        catalog = {"pear": 125}
        quote = prepare_quote(cart, catalog)

        for field, value in (("lines", ()), ("total", 0)):
            with self.subTest(field=field):
                with self.assertRaises(AttributeError):
                    setattr(quote, field, value)
        self.assertIsInstance(quote.lines, tuple)
        with self.assertRaises(TypeError):
            quote.lines[0] = None
        for field, value in (("sku", "apple"), ("unit_cents", 0), ("quantity", 99)):
            with self.subTest(field=field):
                with self.assertRaises(AttributeError):
                    setattr(quote.lines[0], field, value)

        self.assertEqual(cart, [{"sku": "pear", "quantity": 2}])
        self.assertEqual(catalog, {"pear": 125})
        self.assertEqual(quote.total, 250)

    def test_empty_cart(self):
        quote = prepare_quote([], {})
        self.assertEqual(quote.lines, ())
        self.assertEqual(quote.total, 0)
        self.assertIs(type(quote.total), int)

    def test_zero_price(self):
        quote = prepare_quote([{"sku": "gift", "quantity": 3}], {"gift": 0})
        self.assertEqual(quote.lines[0].unit_cents, 0)
        self.assertEqual(quote.lines[0].quantity, 3)
        self.assertEqual(quote.total, 0)

    def test_separate_quotes_capture_values_at_creation(self):
        cart = [{"sku": "pear", "quantity": 2}]
        catalog = {"pear": 125}
        first = prepare_quote(cart, catalog)
        cart[0]["quantity"] = 3
        catalog["pear"] = 200
        second = prepare_quote(cart, catalog)

        self.assertEqual(first.total, 250)
        self.assertEqual(second.total, 600)
        self.assertEqual(first.lines[0].quantity, 2)
        self.assertEqual(first.lines[0].unit_cents, 125)
        self.assertEqual(second.lines[0].quantity, 3)
        self.assertEqual(second.lines[0].unit_cents, 200)

    def test_rejects_invalid_containers_and_rows(self):
        for cart, catalog in (
            (None, {}), ((), {}), ({}, {}), ([], None), ([], []),
            ([None], {}), ([[]], {}), (["pear"], {}),
        ):
            with self.subTest(cart=cart, catalog=catalog):
                with self.assertRaises(QuoteError):
                    prepare_quote(cart, catalog)

    def test_rejects_missing_unknown_or_non_string_skus(self):
        for row in ({"quantity": 1}, *(
            {"sku": sku, "quantity": 1} for sku in ("unknown", None, 1, [], {})
        )):
            with self.subTest(row=row):
                with self.assertRaises(QuoteError):
                    prepare_quote([row], {"pear": 125})

    def test_rejects_nonpositive_or_non_strict_integer_quantities(self):
        for quantity in (None, 0, -1, True, False, 1.0, "1", IntSubclass(1)):
            with self.subTest(quantity=quantity):
                with self.assertRaises(QuoteError):
                    prepare_quote([{"sku": "pear", "quantity": quantity}], {"pear": 125})
        with self.assertRaises(QuoteError):
            prepare_quote([{"sku": "pear"}], {"pear": 125})

    def test_rejects_negative_or_non_strict_integer_prices(self):
        for price in (None, -1, True, False, 1.0, "1", IntSubclass(1)):
            with self.subTest(price=price):
                with self.assertRaises(QuoteError):
                    prepare_quote([{"sku": "pear", "quantity": 1}], {"pear": price})


if __name__ == "__main__":
    unittest.main()
