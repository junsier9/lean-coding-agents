import inspect
import unittest
from unittest.mock import Mock

from accounting import Account, QuoteError, RateUnavailable
from memo import CacheInfo


class MemoizedMethodTests(unittest.TestCase):
    def test_legal_call_forms_share_cache_and_defaults(self):
        account = Account(7)
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
            lambda: Account.quote(fee=0, units=3, account=account),
        ]
        for index, call in enumerate(calls):
            with self.subTest(call=index):
                self.assertEqual(call(), 21)
                self.assertEqual(account.evaluations, 1)
                self.assertEqual(
                    Account.quote.cache_info(account), CacheInfo(index, 1, 1)
                )

        self.assertEqual(account.quote(3, fee=2), 23)
        self.assertEqual(Account.quote(account=account, units=3, fee=2), 23)
        self.assertEqual(account.quote(4), 28)
        self.assertEqual(Account.quote.cache_info(account), CacheInfo(10, 3, 3))

    def test_instances_and_cache_clear_are_isolated(self):
        first, second = Account(5), Account(9)
        self.assertEqual(first.quote(2), 10)
        self.assertEqual(first.quote(2), 10)
        self.assertEqual(second.quote(2), 18)
        self.assertEqual(Account.quote.cache_info(first), CacheInfo(1, 1, 1))
        self.assertEqual(Account.quote.cache_info(second), CacheInfo(0, 1, 1))

        Account.quote.cache_clear(first)
        self.assertEqual(Account.quote.cache_info(first), CacheInfo(0, 0, 0))
        self.assertEqual(first.evaluations, 1)
        self.assertEqual(Account.quote.cache_info(second), CacheInfo(0, 1, 1))
        self.assertEqual(first.quote(2), 10)
        self.assertEqual(first.evaluations, 2)
        self.assertEqual(second.quote(2), 18)
        self.assertEqual(second.evaluations, 1)

    def test_invalid_business_values_reach_original_validation(self):
        account = Account(7)
        self.assertEqual(account.quote(1, fee=1), 8)
        invalid_values = [True, False, 1.0, 0.0, -1, "1", None, [], {}, {1}]
        attempts = 0
        for parameter in ("units", "fee"):
            for value in invalid_values:
                with self.subTest(parameter=parameter, value=value):
                    arguments = {"units": 1, "fee": 1, parameter: value}
                    # Repeating an invalid call must execute and fail each time.
                    for _ in range(2):
                        with self.assertRaises(QuoteError) as raised:
                            Account.quote(account=account, **arguments)
                        self.assertEqual(raised.exception.args, (parameter,))
                        attempts += 1
                        self.assertEqual(account.evaluations, attempts + 1)
                        self.assertEqual(
                            Account.quote.cache_info(account),
                            CacheInfo(0, attempts + 1, 1),
                        )

    def test_backend_exceptions_keep_identity_and_are_not_cached(self):
        for error in (RateUnavailable("offline"), RuntimeError("broken")):
            with self.subTest(error=type(error)):
                backend = Mock(side_effect=[error, error, 11])
                account = Account(7, backend)
                for _ in range(2):
                    with self.assertRaises(type(error)) as raised:
                        account.quote(2)
                    self.assertIs(raised.exception, error)
                self.assertEqual(Account.quote.cache_info(account), CacheInfo(0, 2, 0))
                self.assertEqual(account.quote(2), 22)
                self.assertEqual(account.quote(units=2, fee=0), 22)
                self.assertEqual(backend.call_count, 3)
                self.assertEqual(account.evaluations, 3)
                self.assertEqual(Account.quote.cache_info(account), CacheInfo(1, 3, 1))

    def test_backend_refresh_requires_cache_clear(self):
        backend = Mock(side_effect=[5, 9])
        account = Account(7, backend)
        self.assertEqual(account.quote(2), 10)
        self.assertEqual(account.quote(2), 10)
        backend.assert_called_once_with()
        Account.quote.cache_clear(account)
        self.assertEqual(account.evaluations, 1)
        self.assertEqual(account.quote(2), 18)
        self.assertEqual(backend.call_count, 2)
        self.assertEqual(Account.quote.cache_info(account), CacheInfo(0, 1, 1))

    def test_wrapped_method_preserves_metadata_and_bypasses_cache(self):
        backend = Mock(side_effect=[5, 7, 9])
        account = Account(11, backend)
        original = Account.quote.__wrapped__
        self.assertEqual(Account.quote.__name__, original.__name__)
        self.assertEqual(Account.quote.__qualname__, original.__qualname__)
        self.assertEqual(Account.quote.__doc__, original.__doc__)
        self.assertEqual(inspect.signature(Account.quote), inspect.signature(original))
        self.assertEqual(account.quote(2), 10)
        before = Account.quote.cache_info(account)
        self.assertEqual(original(account, 2), 14)
        self.assertEqual(original(account=account, units=2, fee=0), 18)
        with self.assertRaises(QuoteError):
            original(account=account, units=[])
        self.assertEqual(Account.quote.cache_info(account), before)
        self.assertEqual(account.evaluations, 4)
        self.assertEqual(account.quote(2), 10)
        self.assertEqual(backend.call_count, 3)

    def test_invalid_argument_binding_raises_without_execution(self):
        account = Account(7)
        calls = [
            lambda: account.quote(),
            lambda: account.quote(1, 2),
            lambda: account.quote(1, units=1),
            lambda: account.quote(1, account=account),
            lambda: Account.quote(),
            lambda: Account.quote(units=1),
            lambda: Account.quote(account=account),
            lambda: Account.quote(account, 1, account=account),
            lambda: Account.quote(owner=account, units=1),
            lambda: Account.quote(account, 1, unexpected=2),
        ]
        for index, call in enumerate(calls):
            with self.subTest(call=index):
                with self.assertRaises(TypeError):
                    call()
                self.assertEqual(account.evaluations, 0)
                self.assertEqual(Account.quote.cache_info(account), CacheInfo(0, 0, 0))


if __name__ == "__main__":
    unittest.main()
