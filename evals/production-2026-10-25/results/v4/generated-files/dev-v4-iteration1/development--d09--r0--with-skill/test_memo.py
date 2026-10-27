import inspect
import unittest
from unittest.mock import Mock

from accounting import Account, QuoteError, RateUnavailable


class MemoizedMethodTests(unittest.TestCase):
    def assert_cache_info(self, account, hits, misses, size):
        info = Account.quote.cache_info(account)
        self.assertEqual((info.hits, info.misses, info.size), (hits, misses, size))

    def test_equivalent_call_forms_share_cache(self):
        backend = Mock(return_value=11)
        account = Account(0, backend)
        calls = [
            lambda: account.quote(3),
            lambda: account.quote(units=3),
            lambda: account.quote(3, fee=0),
            lambda: account.quote(units=3, fee=0),
            lambda: Account.quote(account, 3),
            lambda: Account.quote(account, units=3, fee=0),
            lambda: Account.quote(account=account, units=3),
            lambda: Account.quote(units=3, fee=0, account=account),
        ]
        for index, call in enumerate(calls):
            with self.subTest(call=index):
                self.assertEqual(call(), 33)
        backend.assert_called_once_with()
        self.assertEqual(account.evaluations, 1)
        self.assert_cache_info(account, len(calls) - 1, 1, 1)

    def test_distinct_business_arguments_have_distinct_entries(self):
        account = Account(7)
        self.assertEqual(account.quote(0), 0)
        self.assertEqual(account.quote(2), 14)
        self.assertEqual(account.quote(2, fee=3), 17)
        self.assertEqual(Account.quote(account=account, units=2, fee=3), 17)
        self.assertEqual(account.evaluations, 3)
        self.assert_cache_info(account, 1, 3, 3)

    def test_instances_and_clear_are_isolated(self):
        first_backend = Mock(return_value=5)
        second_backend = Mock(return_value=50)
        first = Account(0, first_backend)
        second = Account(0, second_backend)
        self.assertEqual(first.quote(2), 10)
        self.assertEqual(second.quote(2), 100)
        first_backend.return_value = 7
        second_backend.return_value = 70
        self.assertEqual(first.quote(2), 10)
        self.assertEqual(second.quote(2), 100)
        first_backend.assert_called_once_with()
        second_backend.assert_called_once_with()

        Account.quote.cache_clear(first)
        self.assertEqual(first.evaluations, 1)
        self.assert_cache_info(first, 0, 0, 0)
        self.assert_cache_info(second, 1, 1, 1)
        self.assertEqual(first.quote(units=2), 14)
        self.assertEqual(second.quote(units=2), 100)
        self.assertEqual(first.evaluations, 2)
        self.assertEqual(second.evaluations, 1)
        self.assert_cache_info(first, 0, 1, 1)
        self.assert_cache_info(second, 2, 1, 1)

    def test_backend_exceptions_propagate_and_are_not_cached(self):
        for error in (RateUnavailable("offline"), RuntimeError("broken"),
                      QuoteError("backend"), TypeError("backend")):
            with self.subTest(error=type(error).__name__):
                backend = Mock(side_effect=[error, 12])
                account = Account(0, backend)
                with self.assertRaises(type(error)) as raised:
                    account.quote(3)
                self.assertIs(raised.exception, error)
                self.assertEqual(account.evaluations, 1)
                self.assert_cache_info(account, 0, 1, 0)
                self.assertEqual(Account.quote(account=account, units=3), 36)
                self.assertEqual(account.quote(3, fee=0), 36)
                self.assertEqual(backend.call_count, 2)
                self.assertEqual(account.evaluations, 2)
                self.assert_cache_info(account, 1, 2, 1)

    def test_invalid_business_values_reach_original_validation(self):
        backend = Mock(return_value=9)
        account = Account(0, backend)
        account.quote(0)
        account.quote(1)
        account.quote(1, fee=1)
        invalid = [True, False, 1.0, 0.0, -1, "1", None, [], {}, set()]
        for parameter in ("units", "fee"):
            for value in invalid:
                with self.subTest(parameter=parameter, value=value):
                    arguments = {"units": 1, "fee": 0, parameter: value}
                    with self.assertRaises(QuoteError) as raised:
                        Account.quote(account=account, **arguments)
                    self.assertEqual(raised.exception.args, (parameter,))
        attempts = 3 + 2 * len(invalid)
        self.assertEqual(account.evaluations, attempts)
        self.assertEqual(backend.call_count, 3)
        self.assert_cache_info(account, 0, attempts, 3)

    def test_invalid_bindings_raise_type_error(self):
        backend = Mock(return_value=9)
        account = Account(0, backend)
        calls = [
            lambda: Account.quote(),
            lambda: Account.quote(units=1),
            lambda: account.quote(),
            lambda: account.quote(1, 0),
            lambda: account.quote(1, units=1),
            lambda: Account.quote(account, 1, account=account),
            lambda: account.quote(account=account, units=1),
            lambda: Account.quote(owner=account, units=1),
            lambda: account.quote(1, extra=0),
        ]
        for index, call in enumerate(calls):
            with self.subTest(call=index):
                with self.assertRaises(TypeError):
                    call()
        backend.assert_not_called()
        self.assertEqual(account.evaluations, 0)

    def test_wrapped_preserves_signature_and_bypasses_cache(self):
        backend = Mock(side_effect=[10, 20, 30, 40])
        account = Account(0, backend)
        original = Account.quote.__wrapped__
        self.assertEqual(inspect.signature(Account.quote), inspect.signature(original))
        self.assertEqual(list(inspect.signature(original).parameters),
                         ["account", "units", "fee"])
        self.assertEqual(Account.quote.__name__, original.__name__)
        self.assertEqual(Account.quote.__doc__, original.__doc__)
        self.assertEqual(original(account=account, units=2), 20)
        self.assertEqual(original(account, 2, fee=0), 40)
        self.assert_cache_info(account, 0, 0, 0)
        self.assertEqual(account.quote(2), 60)
        self.assertEqual(original(account=account, units=2, fee=0), 80)
        self.assertEqual(account.quote(units=2), 60)
        self.assertEqual(account.evaluations, 4)
        self.assertEqual(backend.call_count, 4)
        self.assert_cache_info(account, 1, 1, 1)


if __name__ == "__main__":
    unittest.main()
