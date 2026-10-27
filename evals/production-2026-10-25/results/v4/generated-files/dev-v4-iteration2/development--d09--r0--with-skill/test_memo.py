import inspect
import unittest
from unittest.mock import Mock

from accounting import Account, QuoteError, RateUnavailable
from memo import CacheInfo


class MemoizedQuoteTests(unittest.TestCase):
    def test_default_fee_call_forms_share_one_entry(self):
        backend = Mock(return_value=7)
        account = Account(1, backend)
        calls = [
            lambda: account.quote(3),
            lambda: account.quote(units=3),
            lambda: account.quote(3, fee=0),
            lambda: account.quote(units=3, fee=0),
            lambda: Account.quote(account, 3),
            lambda: Account.quote(account, units=3),
            lambda: Account.quote(account, 3, fee=0),
            lambda: Account.quote(account, units=3, fee=0),
            lambda: Account.quote(account=account, units=3),
            lambda: Account.quote(units=3, fee=0, account=account),
        ]

        for index, call in enumerate(calls):
            with self.subTest(call=index):
                self.assertEqual(call(), 21)

        self.assertEqual(Account.quote.cache_info(account), CacheInfo(9, 1, 1))
        self.assertEqual(account.evaluations, 1)
        backend.assert_called_once_with()

    def test_nonzero_fee_call_forms_share_one_entry(self):
        account = Account(7)
        calls = [
            lambda: account.quote(3, fee=4),
            lambda: account.quote(units=3, fee=4),
            lambda: Account.quote(account, 3, fee=4),
            lambda: Account.quote(account, units=3, fee=4),
            lambda: Account.quote(fee=4, units=3, account=account),
        ]

        for index, call in enumerate(calls):
            with self.subTest(call=index):
                self.assertEqual(call(), 25)

        self.assertEqual(Account.quote.cache_info(account), CacheInfo(4, 1, 1))
        self.assertEqual(account.evaluations, 1)

    def test_distinct_arguments_have_distinct_entries(self):
        account = Account(7)
        for units, fee, expected in [(0, 0, 0), (3, 0, 21), (3, 4, 25)]:
            with self.subTest(units=units, fee=fee):
                self.assertEqual(account.quote(units, fee=fee), expected)
                self.assertEqual(account.quote(units=units, fee=fee), expected)

        self.assertEqual(Account.quote.cache_info(account), CacheInfo(3, 3, 3))
        self.assertEqual(account.evaluations, 3)

    def test_instances_and_cache_clear_are_isolated(self):
        first = Account(7)
        second = Account(11)
        self.assertEqual(Account.quote.cache_info(first), CacheInfo(0, 0, 0))
        self.assertEqual(Account.quote.cache_info(second), CacheInfo(0, 0, 0))
        self.assertEqual(first.quote(3), 21)
        self.assertEqual(first.quote(3), 21)
        self.assertEqual(second.quote(3), 33)
        Account.quote.cache_clear(first)

        self.assertEqual(Account.quote.cache_info(first), CacheInfo(0, 0, 0))
        self.assertEqual(Account.quote.cache_info(second), CacheInfo(0, 1, 1))
        self.assertEqual(first.evaluations, 1)
        self.assertEqual(second.evaluations, 1)
        self.assertEqual(second.quote(3), 33)
        self.assertEqual(first.quote(3), 21)
        self.assertEqual(Account.quote.cache_info(first), CacheInfo(0, 1, 1))
        self.assertEqual(Account.quote.cache_info(second), CacheInfo(1, 1, 1))
        self.assertEqual(first.evaluations, 2)

    def test_cache_clear_refreshes_backend_rate(self):
        backend = Mock(return_value=7)
        account = Account(1, backend)
        self.assertEqual(account.quote(3), 21)
        backend.return_value = 11
        self.assertEqual(account.quote(3), 21)
        backend.assert_called_once_with()

        Account.quote.cache_clear(account)
        self.assertEqual(account.quote(3), 33)
        self.assertEqual(backend.call_count, 2)
        self.assertEqual(account.evaluations, 2)
        self.assertEqual(Account.quote.cache_info(account), CacheInfo(0, 1, 1))

    def test_invalid_business_values_reach_original_validation(self):
        class IntegerSubclass(int):
            pass

        invalid_values = [
            -1, True, False, 1.0, 0.0, IntegerSubclass(1),
            "1", None, [], {}, set(), (),
        ]
        for parameter in ("units", "fee"):
            for value in invalid_values:
                with self.subTest(parameter=parameter, value=value):
                    backend = Mock(return_value=7)
                    account = Account(1, backend)
                    # Warm entries that bool, float and int subclasses compare equal to.
                    account.quote(0)
                    account.quote(1)
                    account.quote(1, fee=1)
                    arguments = {"units": 1, "fee": 0, parameter: value}
                    for _ in range(2):
                        with self.assertRaises(QuoteError) as raised:
                            account.quote(**arguments)
                        self.assertEqual(str(raised.exception), parameter)

                    self.assertEqual(account.evaluations, 5)
                    self.assertEqual(backend.call_count, 3)
                    self.assertEqual(
                        Account.quote.cache_info(account), CacheInfo(0, 5, 3)
                    )

    def test_backend_exceptions_propagate_by_identity_and_are_not_cached(self):
        for error in (
            RateUnavailable("offline"), RuntimeError("business error"),
            TypeError("backend error"),
        ):
            with self.subTest(error=error):
                backend = Mock(side_effect=[error, error, 7])
                account = Account(1, backend)
                for _ in range(2):
                    with self.assertRaises(type(error)) as raised:
                        account.quote(3)
                    self.assertIs(raised.exception, error)
                self.assertEqual(Account.quote.cache_info(account), CacheInfo(0, 2, 0))
                self.assertEqual(account.quote(3), 21)
                self.assertEqual(account.quote(units=3, fee=0), 21)
                self.assertEqual(Account.quote.cache_info(account), CacheInfo(1, 3, 1))
                self.assertEqual(account.evaluations, 3)
                self.assertEqual(backend.call_count, 3)

    def test_wrapped_bypasses_cache_and_statistics(self):
        backend = Mock(return_value=7)
        account = Account(1, backend)
        self.assertEqual(account.quote(3), 21)
        self.assertEqual(account.quote(3), 21)
        before = Account.quote.cache_info(account)
        backend.return_value = 11

        original = Account.quote.__wrapped__
        self.assertEqual(original(account, 3), 33)
        self.assertEqual(original(account, units=3), 33)
        self.assertEqual(original(account=account, units=3, fee=0), 33)
        self.assertEqual(Account.quote.cache_info(account), before)
        self.assertEqual(account.evaluations, 4)
        self.assertEqual(backend.call_count, 4)
        self.assertEqual(account.quote(3), 21)
        backend.assert_called_with()

    def test_wrapped_errors_do_not_change_cache_statistics(self):
        error = RateUnavailable("offline")
        backend = Mock(side_effect=error)
        account = Account(1, backend)
        for _ in range(2):
            with self.assertRaises(RateUnavailable) as raised:
                Account.quote.__wrapped__(account=account, units=3)
            self.assertIs(raised.exception, error)
        with self.assertRaises(QuoteError):
            Account.quote.__wrapped__(account, [])

        self.assertEqual(Account.quote.cache_info(account), CacheInfo(0, 0, 0))
        self.assertEqual(account.evaluations, 3)
        self.assertEqual(backend.call_count, 2)

    def test_invalid_python_bindings_do_not_execute_business_logic(self):
        account = Account(7)
        calls = [
            lambda: Account.quote(),
            lambda: Account.quote(units=3),
            lambda: account.quote(),
            lambda: account.quote(3, 0),
            lambda: Account.quote(account, 3, 0),
            lambda: account.quote(3, units=3),
            lambda: Account.quote(account, 3, account=account),
            lambda: account.quote(3, account=account),
            lambda: account.quote(3, owner=account),
            lambda: Account.quote(owner=account, units=3),
            lambda: Account.quote(account, 3, extra=0),
        ]
        for index, call in enumerate(calls):
            with self.subTest(call=index):
                with self.assertRaises(TypeError):
                    call()

        self.assertEqual(account.evaluations, 0)
        self.assertEqual(Account.quote.cache_info(account), CacheInfo(0, 0, 0))

    def test_metadata_and_original_signature_are_preserved(self):
        original = Account.quote.__wrapped__
        self.assertEqual(Account.quote.__name__, "quote")
        self.assertEqual(Account.quote.__doc__, original.__doc__)
        self.assertEqual(Account.quote.__qualname__, original.__qualname__)
        self.assertEqual(inspect.signature(Account.quote), inspect.signature(original))
        self.assertEqual(str(inspect.signature(original)), "(account, units, *, fee=0)")
        self.assertEqual(str(inspect.signature(Account(7).quote)), "(units, *, fee=0)")


if __name__ == "__main__":
    unittest.main()
