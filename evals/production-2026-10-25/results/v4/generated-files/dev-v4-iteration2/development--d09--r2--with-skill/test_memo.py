import inspect
import unittest
from unittest.mock import Mock

from accounting import Account, QuoteError, RateUnavailable


class MemoizedMethodTests(unittest.TestCase):
    def test_equivalent_call_forms_share_one_cache_entry(self):
        backend = Mock(return_value=100)
        account = Account(0, backend)
        calls = (
            lambda: Account.quote(account=account, units=2),
            lambda: account.quote(2),
            lambda: account.quote(units=2),
            lambda: account.quote(2, fee=0),
            lambda: account.quote(fee=0, units=2),
            lambda: Account.quote(account, 2),
            lambda: Account.quote(account, units=2, fee=0),
            lambda: Account.quote(fee=0, units=2, account=account),
        )
        for index, call in enumerate(calls):
            with self.subTest(call=index):
                self.assertEqual(call(), 200)
        self.assertEqual(account.evaluations, 1)
        backend.assert_called_once_with()
        self.assertEqual(Account.quote.cache_info(account), (len(calls) - 1, 1, 1))

    def test_different_business_arguments_have_separate_entries(self):
        account = Account(100)
        self.assertEqual(account.quote(2, fee=7), 207)
        self.assertEqual(Account.quote(account, fee=7, units=2), 207)
        self.assertEqual(account.quote(3, fee=7), 307)
        self.assertEqual(account.quote(2, fee=8), 208)
        self.assertEqual(account.evaluations, 3)
        info = Account.quote.cache_info(account)
        self.assertEqual((info.hits, info.misses, info.size), (1, 3, 3))

    def test_instances_and_cache_clear_are_isolated(self):
        first = Account(100)
        second = Account(200)
        self.assertEqual(first.quote(2), 200)
        self.assertEqual(first.quote(units=2), 200)
        self.assertEqual(second.quote(2), 400)
        self.assertEqual(Account.quote.cache_info(first), (1, 1, 1))
        self.assertEqual(Account.quote.cache_info(second), (0, 1, 1))

        Account.quote.cache_clear(first)
        self.assertEqual(Account.quote.cache_info(first), (0, 0, 0))
        self.assertEqual(first.evaluations, 1)
        self.assertEqual(Account.quote.cache_info(second), (0, 1, 1))
        self.assertEqual(second.evaluations, 1)
        self.assertEqual(first.quote(2), 200)
        self.assertEqual(Account.quote.cache_info(first), (0, 1, 1))
        self.assertEqual(first.evaluations, 2)

    def test_backend_changes_require_cache_clear(self):
        backend = Mock(return_value=100)
        account = Account(0, backend)
        self.assertEqual(account.quote(2), 200)
        backend.return_value = 300
        self.assertEqual(account.quote(2), 200)
        backend.assert_called_once_with()
        Account.quote.cache_clear(account)
        self.assertEqual(account.quote(2), 600)
        self.assertEqual(backend.call_count, 2)
        self.assertEqual(account.evaluations, 2)
        self.assertEqual(Account.quote.cache_info(account), (0, 1, 1))

    def test_wrapped_method_keeps_signature_and_bypasses_cache(self):
        backend = Mock(return_value=100)
        account = Account(0, backend)
        raw = Account.quote.__wrapped__
        self.assertEqual(str(inspect.signature(raw)), "(account, units, *, fee=0)")
        self.assertEqual(inspect.signature(Account.quote), inspect.signature(raw))
        self.assertEqual(str(inspect.signature(account.quote)), "(units, *, fee=0)")
        self.assertEqual(Account.quote.__name__, raw.__name__)
        self.assertEqual(Account.quote.__doc__, raw.__doc__)
        self.assertEqual(account.quote(2), 200)

        backend.return_value = 300
        self.assertEqual(raw(account, 2), 600)
        self.assertEqual(raw(account=account, units=2, fee=0), 600)
        with self.assertRaisesRegex(QuoteError, "^units$"):
            raw(account=account, units=[])
        self.assertEqual(Account.quote.cache_info(account), (0, 1, 1))
        self.assertEqual(account.evaluations, 4)
        self.assertEqual(backend.call_count, 3)
        self.assertEqual(account.quote(2), 200)
        self.assertEqual(Account.quote.cache_info(account), (1, 1, 1))

    def test_invalid_business_values_reach_original_validation(self):
        backend = Mock(return_value=100)
        account = Account(0, backend)
        account.quote(0)
        account.quote(1)
        account.quote(1, fee=1)
        misses = 3
        invalid_values = (True, False, 0.0, 1.0, -1, "1", None, [], {}, set())
        for field in ("units", "fee"):
            for value in invalid_values:
                arguments = {"units": 1, "fee": 0, field: value}
                for attempt in range(2):
                    with self.subTest(field=field, value=value, attempt=attempt):
                        with self.assertRaisesRegex(QuoteError, "^" + field + "$"):
                            account.quote(**arguments)
                        misses += 1
        self.assertEqual(account.evaluations, misses)
        self.assertEqual(Account.quote.cache_info(account), (0, misses, 3))
        self.assertEqual(backend.call_count, 3)

    def test_business_exceptions_propagate_unchanged_and_are_not_cached(self):
        for error in (RateUnavailable("offline"), ValueError("bad rate"), TypeError("backend")):
            with self.subTest(error=error):
                backend = Mock(side_effect=[error, error, 100])
                account = Account(0, backend)
                for attempt in range(2):
                    with self.assertRaises(type(error)) as caught:
                        account.quote(2)
                    self.assertIs(caught.exception, error)
                self.assertEqual(Account.quote.cache_info(account), (0, 2, 0))
                self.assertEqual(account.evaluations, 2)
                self.assertEqual(Account.quote(account=account, units=2), 200)
                self.assertEqual(account.quote(2, fee=0), 200)
                self.assertEqual(Account.quote.cache_info(account), (1, 3, 1))
                self.assertEqual(account.evaluations, 3)
                self.assertEqual(backend.call_count, 3)

    def test_invalid_python_binding_does_not_execute_business_method(self):
        account = Account(100)
        calls = (
            lambda: account.quote(),
            lambda: account.quote(1, 0),
            lambda: account.quote(1, units=1),
            lambda: account.quote(1, extra=0),
            lambda: account.quote(account=account, units=1),
            lambda: Account.quote(units=1),
            lambda: Account.quote(account, account=account, units=1),
            lambda: Account.quote(owner=account, units=1),
        )
        for index, call in enumerate(calls):
            with self.subTest(call=index):
                with self.assertRaises(TypeError):
                    call()
        self.assertEqual(account.evaluations, 0)
        self.assertEqual(Account.quote.cache_info(account), (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
