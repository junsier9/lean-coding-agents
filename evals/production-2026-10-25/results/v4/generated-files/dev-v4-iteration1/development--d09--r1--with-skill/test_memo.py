import inspect
import unittest
from unittest.mock import Mock

from accounting import Account, QuoteError, RateUnavailable


class MemoizedQuoteTests(unittest.TestCase):
    def test_equivalent_call_forms_share_cache(self):
        backend = Mock(return_value=25)
        account = Account(10, backend)
        calls = [
            lambda: account.quote(4),
            lambda: account.quote(units=4),
            lambda: account.quote(4, fee=0),
            lambda: account.quote(units=4, fee=0),
            lambda: Account.quote(account, 4),
            lambda: Account.quote(account, units=4),
            lambda: Account.quote(account, 4, fee=0),
            lambda: Account.quote(account, units=4, fee=0),
            lambda: Account.quote(account=account, units=4),
            lambda: Account.quote(fee=0, units=4, account=account),
        ]

        for index, call in enumerate(calls):
            with self.subTest(call=index):
                self.assertEqual(call(), 100)

        self.assertEqual(Account.quote.cache_info(account), (len(calls) - 1, 1, 1))
        self.assertEqual(account.evaluations, 1)
        backend.assert_called_once_with()

    def test_business_arguments_distinguish_cache_entries(self):
        account = Account(25)
        self.assertEqual(account.quote(0), 0)
        self.assertEqual(account.quote(2), 50)
        self.assertEqual(account.quote(3), 75)
        self.assertEqual(account.quote(2, fee=7), 57)
        self.assertEqual(Account.quote(units=2, account=account, fee=7), 57)
        self.assertEqual(account.quote(units=2, fee=0), 50)

        info = Account.quote.cache_info(account)
        self.assertEqual((info.hits, info.misses, info.size), (2, 4, 4))
        self.assertEqual(account.evaluations, 4)

    def test_instance_isolation_and_explicit_refresh(self):
        first_backend = Mock(return_value=10)
        second_backend = Mock(return_value=20)
        first = Account(0, first_backend)
        second = Account(0, second_backend)
        self.assertEqual(Account.quote.cache_info(first), (0, 0, 0))
        self.assertEqual(Account.quote.cache_info(second), (0, 0, 0))
        self.assertEqual(first.quote(2), 20)
        self.assertEqual(second.quote(2), 40)

        first_backend.return_value = 30
        self.assertEqual(first.quote(2), 20)
        self.assertEqual(Account.quote.cache_info(first), (1, 1, 1))
        self.assertEqual(Account.quote.cache_info(second), (0, 1, 1))
        first_backend.assert_called_once_with()

        Account.quote.cache_clear(first)
        self.assertEqual(Account.quote.cache_info(first), (0, 0, 0))
        self.assertEqual(Account.quote.cache_info(second), (0, 1, 1))
        self.assertEqual(first.evaluations, 1)
        self.assertEqual(first.quote(2), 60)
        self.assertEqual(second.quote(2), 40)
        self.assertEqual(Account.quote.cache_info(first), (0, 1, 1))
        self.assertEqual(Account.quote.cache_info(second), (1, 1, 1))
        self.assertEqual(first.evaluations, 2)
        self.assertEqual(second.evaluations, 1)
        self.assertEqual(first_backend.call_count, 2)
        second_backend.assert_called_once_with()

    def test_unwrapped_preserves_signature_and_bypasses_cache(self):
        backend = Mock(side_effect=[10, 20, 30, 40])
        account = Account(0, backend)
        original = Account.quote.__wrapped__
        self.assertEqual(str(inspect.signature(Account.quote)), "(account, units, *, fee=0)")
        self.assertEqual(str(inspect.signature(account.quote)), "(units, *, fee=0)")
        self.assertEqual(inspect.signature(Account.quote), inspect.signature(original))
        self.assertEqual(Account.quote.__name__, original.__name__)
        self.assertEqual(Account.quote.__doc__, original.__doc__)

        self.assertEqual(account.quote(2), 20)
        self.assertEqual(original(account, 2), 40)
        self.assertEqual(original(account=account, units=2, fee=0), 60)
        self.assertEqual(account.quote.__wrapped__(account, 2, fee=1), 81)
        self.assertEqual(Account.quote.cache_info(account), (0, 1, 1))
        self.assertEqual(account.quote(2), 20)
        self.assertEqual(Account.quote.cache_info(account), (1, 1, 1))
        self.assertEqual(account.evaluations, 4)
        self.assertEqual(backend.call_count, 4)

    def test_invalid_business_values_reach_original_validation(self):
        class IntSubclass(int):
            pass

        invalid_values = [True, False, 0.0, 1.0, -1, "1", None, [], {}, set(), IntSubclass(1)]
        backend = Mock(return_value=10)
        account = Account(0, backend)
        for units in (0, 1):
            for fee in (0, 1):
                account.quote(units, fee=fee)

        attempts = 0
        for parameter in ("units", "fee"):
            for value in invalid_values:
                for repeat in range(2):
                    with self.subTest(parameter=parameter, value=value, repeat=repeat):
                        arguments = {"units": 1, "fee": 0, parameter: value}
                        with self.assertRaises(QuoteError) as caught:
                            Account.quote(account=account, **arguments)
                        self.assertEqual(caught.exception.args, (parameter,))
                        attempts += 1
                        self.assertEqual(Account.quote.cache_info(account), (0, 4 + attempts, 4))
                        self.assertEqual(account.evaluations, 4 + attempts)

        self.assertEqual(backend.call_count, 4)

    def test_backend_failures_propagate_without_caching(self):
        for failure in (RateUnavailable("offline"), ValueError("broken rate"), KeyboardInterrupt()):
            with self.subTest(failure=type(failure).__name__):
                backend = Mock(side_effect=[failure, failure, 25])
                account = Account(0, backend)
                for attempt in (1, 2):
                    with self.assertRaises(type(failure)) as caught:
                        account.quote(2)
                    self.assertIs(caught.exception, failure)
                    self.assertEqual(Account.quote.cache_info(account), (0, attempt, 0))
                    self.assertEqual(account.evaluations, attempt)

                self.assertEqual(Account.quote(account=account, units=2), 50)
                self.assertEqual(account.quote(2, fee=0), 50)
                self.assertEqual(Account.quote.cache_info(account), (1, 3, 1))
                self.assertEqual(account.evaluations, 3)
                self.assertEqual(backend.call_count, 3)

    def test_invalid_call_binding_raises_without_execution(self):
        backend = Mock(return_value=25)
        account = Account(0, backend)
        invalid_calls = [
            lambda: account.quote(),
            lambda: account.quote(1, 2),
            lambda: account.quote(1, units=1),
            lambda: account.quote(1, unexpected=0),
            lambda: account.quote(account=account, units=1),
            lambda: Account.quote(units=1),
            lambda: Account.quote(account=account),
            lambda: Account.quote(account, account=account, units=1),
            lambda: Account.quote(owner=account, units=1),
        ]

        for index, call in enumerate(invalid_calls):
            with self.subTest(call=index):
                with self.assertRaises(TypeError):
                    call()
                self.assertEqual(Account.quote.cache_info(account), (0, 0, 0))
                self.assertEqual(account.evaluations, 0)

        backend.assert_not_called()


if __name__ == "__main__":
    unittest.main()
