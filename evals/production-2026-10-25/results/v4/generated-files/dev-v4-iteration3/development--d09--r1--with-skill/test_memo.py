import inspect
import unittest

from accounting import Account, QuoteError, RateUnavailable


class MemoizedMethodTests(unittest.TestCase):
    def test_equivalent_call_forms_share_default_fee_entry(self):
        account = Account(10)
        calls = (
            lambda: account.quote(3),
            lambda: account.quote(units=3),
            lambda: account.quote(3, fee=0),
            lambda: account.quote(units=3, fee=0),
            lambda: Account.quote(account, 3),
            lambda: Account.quote(account, units=3),
            lambda: Account.quote(account, 3, fee=0),
            lambda: Account.quote(account, units=3, fee=0),
            lambda: Account.quote(account=account, units=3),
            lambda: Account.quote(units=3, account=account),
            lambda: Account.quote(account=account, units=3, fee=0),
            lambda: Account.quote(fee=0, units=3, account=account),
        )
        for index, call in enumerate(calls):
            with self.subTest(call=index):
                self.assertEqual(call(), 30)
        self.assertEqual(account.evaluations, 1)
        info = Account.quote.cache_info(account)
        self.assertEqual((info.hits, info.misses, info.size), (11, 1, 1))

    def test_nondefault_fee_call_forms_share_entry(self):
        account = Account(10)
        calls = (
            lambda: account.quote(3, fee=2),
            lambda: account.quote(fee=2, units=3),
            lambda: Account.quote(account, 3, fee=2),
            lambda: Account.quote(account, units=3, fee=2),
            lambda: Account.quote(fee=2, account=account, units=3),
        )
        for call in calls:
            self.assertEqual(call(), 32)
        self.assertEqual(account.evaluations, 1)
        self.assertEqual(Account.quote.cache_info(account), (4, 1, 1))

    def test_instance_caches_statistics_and_clear_are_isolated(self):
        account = Account(10)
        other = Account(20)
        self.assertEqual(account.quote(2), 20)
        self.assertEqual(account.quote(units=2), 20)
        self.assertEqual(other.quote(2), 40)
        self.assertEqual(other.quote(3), 60)
        self.assertEqual(other.quote(units=2), 40)
        self.assertEqual(Account.quote.cache_info(account), (1, 1, 1))
        self.assertEqual(Account.quote.cache_info(other), (1, 2, 2))

        Account.quote.cache_clear(account)
        self.assertEqual(Account.quote.cache_info(account), (0, 0, 0))
        self.assertEqual(account.evaluations, 1)
        self.assertEqual(Account.quote.cache_info(other), (1, 2, 2))
        self.assertEqual(other.evaluations, 2)
        self.assertEqual(account.quote(2), 20)
        self.assertEqual(account.evaluations, 2)
        self.assertEqual(Account.quote.cache_info(account), (0, 1, 1))

    def test_invalid_business_values_reach_validation_and_are_not_cached(self):
        invalid_values = (-1, True, False, 1.0, 0.0, "1", None, [], {}, set())
        for parameter in ("units", "fee"):
            for invalid in invalid_values:
                with self.subTest(parameter=parameter, invalid=invalid):
                    account = Account(10)
                    for units in (0, 1):
                        for fee in (0, 1):
                            account.quote(units, fee=fee)
                    arguments = {"units": 1, "fee": 0}
                    arguments[parameter] = invalid
                    for _ in range(2):
                        with self.assertRaises(QuoteError) as raised:
                            Account.quote(account=account, **arguments)
                        self.assertEqual(str(raised.exception), parameter)
                    self.assertEqual(account.evaluations, 6)
                    self.assertEqual(Account.quote.cache_info(account), (0, 6, 4))

    def test_invalid_python_binding_does_not_execute_or_change_statistics(self):
        account = Account(10)
        calls = (
            lambda: account.quote(),
            lambda: account.quote(1, 2),
            lambda: account.quote(1, units=1),
            lambda: account.quote(1, account=account),
            lambda: account.quote(1, self=account),
            lambda: account.quote(owner=account, units=1),
            lambda: Account.quote(units=1),
            lambda: Account.quote(account),
            lambda: Account.quote(account, 1, account=account),
            lambda: Account.quote(account=account, units=1, unknown=0),
            lambda: Account.quote(owner=account, units=1),
        )
        for index, call in enumerate(calls):
            with self.subTest(call=index):
                with self.assertRaises(TypeError):
                    call()
                self.assertEqual(account.evaluations, 0)
                self.assertEqual(Account.quote.cache_info(account), (0, 0, 0))

    def test_backend_failures_propagate_by_identity_and_can_be_retried(self):
        errors = (
            RateUnavailable("offline"),
            RuntimeError("backend failed"),
            TypeError("backend type error"),
        )
        for error in errors:
            with self.subTest(error=error):
                attempts = []

                def backend():
                    attempts.append(None)
                    if len(attempts) <= 2:
                        raise error
                    return 7

                account = Account(10, backend=backend)
                for attempt in (1, 2):
                    with self.assertRaises(type(error)) as raised:
                        account.quote(2)
                    self.assertIs(raised.exception, error)
                    self.assertEqual(account.evaluations, attempt)
                    self.assertEqual(Account.quote.cache_info(account), (0, attempt, 0))
                self.assertEqual(account.quote(2), 14)
                self.assertEqual(Account.quote(account=account, units=2), 14)
                self.assertEqual(len(attempts), 3)
                self.assertEqual(account.evaluations, 3)
                self.assertEqual(Account.quote.cache_info(account), (1, 3, 1))

    def test_backend_refresh_and_wrapped_bypass(self):
        rates = [10]
        backend_calls = []

        def backend():
            backend_calls.append(None)
            return rates[0]

        account = Account(1, backend=backend)
        self.assertEqual(account.quote(2), 20)
        rates[0] = 20
        self.assertEqual(account.quote(units=2, fee=0), 20)
        self.assertEqual(len(backend_calls), 1)

        original = Account.quote.__wrapped__
        self.assertEqual(original(account, 2), 40)
        self.assertEqual(original(account=account, units=2, fee=0), 40)
        self.assertEqual(len(backend_calls), 3)
        self.assertEqual(account.evaluations, 3)
        self.assertEqual(Account.quote.cache_info(account), (1, 1, 1))
        self.assertEqual(account.quote(2), 20)
        self.assertEqual(len(backend_calls), 3)

        Account.quote.cache_clear(account)
        self.assertEqual(account.evaluations, 3)
        self.assertEqual(Account.quote.cache_info(account), (0, 0, 0))
        self.assertEqual(account.quote(2), 40)
        self.assertEqual(len(backend_calls), 4)
        self.assertEqual(account.evaluations, 4)
        self.assertEqual(Account.quote.cache_info(account), (0, 1, 1))

    def test_signature_and_metadata_are_preserved(self):
        original = Account.quote.__wrapped__
        self.assertEqual(inspect.signature(Account.quote), inspect.signature(original))
        self.assertEqual(Account.quote.__name__, original.__name__)
        self.assertEqual(Account.quote.__qualname__, original.__qualname__)
        self.assertEqual(Account.quote.__doc__, original.__doc__)


if __name__ == "__main__":
    unittest.main()
