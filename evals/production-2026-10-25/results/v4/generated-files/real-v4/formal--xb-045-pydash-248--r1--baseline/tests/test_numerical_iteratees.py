"""Rounding callback regressions, runnable with the standard library test runner."""

import unittest

import pydash as _


class TestRoundingIteratees(unittest.TestCase):
    def test_count_by_default_precision(self):
        values = [4.3, 6.1, 6.4, -1.2, -1.8, -1.2]
        cases = (
            (_.floor, {4: 1, 6: 2, -2: 3}),
            (_.ceil, {5: 1, 7: 2, -1: 3}),
            (_.round_, {4: 1, 6: 2, -1: 2, -2: 1}),
        )
        for func, expected in cases:
            collections = (
                ("list", values),
                ("tuple", tuple(values)),
                ("iterator", iter(values)),
                ("string keys", dict(zip("abcdef", values))),
                ("integer keys", dict(zip(range(-2, 10, 2), values))),
            )
            for name, collection in collections:
                with self.subTest(func=func.__name__, collection=name):
                    self.assertEqual(_.count_by(collection, func), expected)

    def test_other_collections_default_precision(self):
        values = [4.3, 6.1, 6.4]
        cases = (
            (_.floor, [4, 6, 6], {4: [4.3], 6: [6.1, 6.4]}, {4: 4.3, 6: 6.4}),
            (_.ceil, [5, 7, 7], {5: [4.3], 7: [6.1, 6.4]}, {5: 4.3, 7: 6.4}),
            (_.round_, [4, 6, 6], {4: [4.3], 6: [6.1, 6.4]}, {4: 4.3, 6: 6.4}),
        )
        for func, mapped, grouped, keyed in cases:
            for operation, expected in (
                (_.map_, mapped),
                (_.group_by, grouped),
                (_.key_by, keyed),
            ):
                with self.subTest(func=func.__name__, operation=operation.__name__):
                    self.assertEqual(operation(values, func), expected)

    def test_explicit_precision(self):
        values = [1234.567, -1234.567]
        cases = (
            (_.floor, 2, [1234.56, -1234.57]),
            (_.ceil, 2, [1234.57, -1234.56]),
            (_.round_, 2, [1234.57, -1234.57]),
            (_.floor, 0, [1234, -1235]),
            (_.ceil, 0, [1235, -1234]),
            (_.round_, 0, [1235, -1235]),
            (_.floor, -2, [1200, -1300]),
            (_.ceil, -2, [1300, -1200]),
            (_.round_, -2, [1200, -1200]),
        )
        for func, precision, expected in cases:
            with self.subTest(func=func.__name__, precision=precision):
                self.assertEqual(func(values, precision), expected)
                self.assertEqual(func(values, precision=precision), expected)
                for value, result in zip(values, expected):
                    self.assertEqual(func(value, precision), result)
                    self.assertEqual(func(value, precision=precision), result)

    def test_partial_precision(self):
        values = [1.234, -1.234]
        cases = (
            (_.floor, [1.23, -1.24]),
            (_.ceil, [1.24, -1.23]),
            (_.round_, [1.23, -1.23]),
        )
        for func, expected in cases:
            with self.subTest(func=func.__name__):
                self.assertEqual(_.map_(values, _.partial(func, precision=2)), expected)
                self.assertEqual(_.map_(values, _.partial_right(func, 2)), expected)

    def test_other_callback_arguments(self):
        values = [10, 20, 30]

        def callback(value, index=0, collection=None):
            self.assertIs(collection, values)
            return value + index

        self.assertEqual(_.map_(values, callback), [10, 21, 32])
