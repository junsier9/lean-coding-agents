import inspect
import unittest
from unittest.mock import Mock

from accounting import Account, QuoteError, RateUnavailable


class MemoizedMethodTests(unittest.TestCase):
    def test_equivalent_call_forms_share_cache(self):
        backend = Mock(return_value=12)
        account = Account(10, backend)
        for fee in (0, 7):
            calls = [
                lambda: account.quote(3, fee=fee),
                lambda: account.quote(units=3, fee=fee),
                lambda: Account.quote(account, 3, fee=fee),
                lambda: Account.quote(account, units=3, fee=fee),
                lambda: Account.quote(account=account, units=3, fee=fee),
                lambda: Account.quote(fee=fee, units=3, account=account),
            ]
            if fee == 0:
                calls += [
                    lambda: account.quote(3),
                    lambda: account.quote(units=3),
                    lambda: Account.quote(account, 3),
                    lambda: Account.quote(account, units=3),
                    lambda: Account.quote(account=account, units=3),
                ]
            for call in calls:
                self.assertEqual(call(), 36 + fee)
        info = Account.quote.cache_info(account)
        self.assertEqual((info.hits, info.misses, info.size), (15, 2, 2))
        self.assertEqual(account.evaluations, 2)
        self.assertEqual(backend.call_count, 2)

    def test_distinct_units_have_distinct_entries(self):
        account = Account(10)
        self.assertEqual(account.quote(2), 20)
        self.assertEqual(account.quote(3), 30)
        self.assertEqual(account.quote(units=2, fee=0), 20)
        self.assertEqual(Account.quote.cache_info(account), (1, 2, 2))

    def test_instance_state_and_clear_are_isolated(self):
        first = Account(10)
        second = Account(20)
        self.assertEqual(first.quote(3), 30)
        self.assertEqual(first.quote(3), 30)
        self.assertEqual(second.quote(3), 60)
        self.assertEqual(Account.quote.cache_info(first), (1, 1, 1))
        self.assertEqual(Account.quote.cache_info(second), (0, 1, 1))

        Account.quote.cache_clear(first)
        self.assertEqual(Account.quote.cache_info(first), (0, 0, 0))
        self.assertEqual(first.evaluations, 1)
        self.assertEqual(Account.quote.cache_info(second), (0, 1, 1))
        self.assertEqual(first.quote(3), 30)
        self.assertEqual(second.quote(3), 60)
        self.assertEqual((first.evaluations, second.evaluations), (2, 1))

    def test_backend_refresh_requires_clear(self):
        backend = Mock(return_value=10)
        account = Account(0, backend)
        self.assertEqual(account.quote(3), 30)
        backend.return_value = 20
        self.assertEqual(account.quote(units=3, fee=0), 30)
        self.assertEqual(backend.call_count, 1)
        Account.quote.cache_clear(account)
        self.assertEqual(account.quote(3), 60)
        self.assertEqual(backend.call_count, 2)
        self.assertEqual(Account.quote.cache_info(account), (0, 1, 1))
        self.assertEqual(account.evaluations, 2)

    def test_wrapped_method_bypasses_cache_and_statistics(self):
        backend = Mock(return_value=10)
        account = Account(0, backend)
        self.assertEqual(account.quote(3), 30)
        before = Account.quote.cache_info(account)
        original = Account.quote.__wrapped__
        backend.return_value = 20
        self.assertEqual(original(account, 3), 60)
        self.assertEqual(original(account=account, units=3, fee=2), 62)
        self.assertEqual(original(account, units=3), 60)
        self.assertEqual(Account.quote.cache_info(account), before)
        self.assertEqual(account.evaluations, 4)
        self.assertEqual(backend.call_count, 4)
        self.assertEqual(account.quote(3), 30)
        self.assertEqual(backend.call_count, 4)
        self.assertEqual(Account.quote.__name__, original.__name__)
        self.assertEqual(Account.quote.__doc__, original.__doc__)
        self.assertEqual(inspect.signature(Account.quote), inspect.signature(original))
        self.assertEqual(str(inspect.signature(original)), '(account, units, *, fee=0)')

    def test_invalid_business_values_reach_original_validation(self):
        backend = Mock(return_value=10)
        account = Account(0, backend)
        account.quote(0)
        account.quote(1)
        for parameter in ('units', 'fee'):
            for invalid in (True, False, -1, 1.0, 0.0, '1', None, [], {}, set()):
                for attempt in range(2):
                    with self.subTest(parameter=parameter, value=invalid, attempt=attempt):
                        before = Account.quote.cache_info(account)
                        evaluations = account.evaluations
                        arguments = {'units': 1, 'fee': 0, parameter: invalid}
                        with self.assertRaises(QuoteError) as caught:
                            account.quote(**arguments)
                        self.assertEqual(str(caught.exception), parameter)
                        self.assertEqual(
                            Account.quote.cache_info(account),
                            (before.hits, before.misses + 1, before.size),
                        )
                        self.assertEqual(account.evaluations, evaluations + 1)
        self.assertEqual(backend.call_count, 2)

    def test_business_exceptions_propagate_without_caching(self):
        for error in (RateUnavailable('offline'), RuntimeError('broken'),
                      QuoteError('backend'), TypeError('backend'), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                backend = Mock(side_effect=error)
                account = Account(0, backend)
                for attempt in range(2):
                    with self.assertRaises(type(error)) as caught:
                        Account.quote(account=account, units=3)
                    self.assertIs(caught.exception, error)
                    self.assertEqual(Account.quote.cache_info(account), (0, attempt + 1, 0))
                self.assertEqual(account.evaluations, 2)
                self.assertEqual(backend.call_count, 2)
                backend.side_effect = None
                backend.return_value = 10
                self.assertEqual(account.quote(3), 30)
                self.assertEqual(account.quote(3), 30)
                self.assertEqual(Account.quote.cache_info(account), (1, 3, 1))
                self.assertEqual(account.evaluations, 3)
                self.assertEqual(backend.call_count, 3)

    def test_invalid_argument_binding_does_not_execute_or_count(self):
        backend = Mock(return_value=10)
        account = Account(0, backend)
        account.quote(3)
        calls = [
            lambda: Account.quote(),
            lambda: Account.quote(units=3),
            lambda: account.quote(),
            lambda: account.quote(3, 1),
            lambda: account.quote(3, units=3),
            lambda: account.quote(3, extra=1),
            lambda: account.quote(3, account=account),
            lambda: Account.quote(account, 3, account=account),
            lambda: Account.quote(owner=account, units=3),
        ]
        for call in calls:
            with self.assertRaises(TypeError):
                call()
            self.assertEqual(Account.quote.cache_info(account), (0, 1, 1))
        self.assertEqual(account.evaluations, 1)
        self.assertEqual(backend.call_count, 1)


if __name__ == '__main__':
    unittest.main()
