import inspect
import unittest

from accounting import Account, QuoteError, RateUnavailable


class MemoizedMethodTests(unittest.TestCase):
    def test_equivalent_call_forms_share_cache(self):
        account = Account(7)
        for fee in (0, 4):
            calls = [
                lambda: account.quote(3, fee=fee),
                lambda: account.quote(units=3, fee=fee),
                lambda: Account.quote(account, 3, fee=fee),
                lambda: Account.quote(account, units=3, fee=fee),
                lambda: Account.quote(account=account, units=3, fee=fee),
                lambda: Account.quote(fee=fee, units=3, account=account),
            ]
            if fee == 0:
                calls.extend([
                    lambda: account.quote(3),
                    lambda: account.quote(units=3),
                    lambda: Account.quote(account, 3),
                    lambda: Account.quote(account, units=3),
                    lambda: Account.quote(account=account, units=3),
                ])
            for call in calls:
                with self.subTest(fee=fee, call=call):
                    self.assertEqual(call(), 21 + fee)

        self.assertEqual(account.evaluations, 2)
        self.assertEqual(Account.quote.cache_info(account), (15, 2, 2))

    def test_instances_and_clear_are_isolated(self):
        first = Account(7)
        second = Account(11)
        self.assertEqual(first.quote(2), 14)
        self.assertEqual(first.quote(units=2), 14)
        self.assertEqual(second.quote(2), 22)
        self.assertEqual(Account.quote.cache_info(first), (1, 1, 1))
        self.assertEqual(Account.quote.cache_info(second), (0, 1, 1))

        Account.quote.cache_clear(first)
        self.assertEqual(Account.quote.cache_info(first), (0, 0, 0))
        self.assertEqual(Account.quote.cache_info(second), (0, 1, 1))
        self.assertEqual((first.evaluations, second.evaluations), (1, 1))
        self.assertEqual(second.quote(2), 22)
        first.rate = 13
        self.assertEqual(first.quote(2), 26)
        self.assertEqual(Account.quote.cache_info(first), (0, 1, 1))
        self.assertEqual(Account.quote.cache_info(second), (1, 1, 1))

    def test_invalid_values_keep_business_validation_and_are_not_cached(self):
        class IntSubclass(int):
            pass

        invalid_values = (True, False, 1.0, 0.0, -1, "1", None,
                          [], {}, set(), [1], IntSubclass(1))
        for parameter in ("units", "fee"):
            for invalid in invalid_values:
                with self.subTest(parameter=parameter, invalid=invalid):
                    account = Account(7)
                    account.quote(0)
                    account.quote(1)
                    account.quote(1, fee=1)
                    arguments = {"units": 1, "fee": 0, parameter: invalid}
                    for _ in range(2):
                        with self.assertRaises(QuoteError) as caught:
                            Account.quote(account=account, **arguments)
                        self.assertEqual(caught.exception.args, (parameter,))
                    self.assertEqual(account.evaluations, 5)
                    self.assertEqual(Account.quote.cache_info(account), (0, 5, 3))

    def test_backend_errors_propagate_by_identity_and_can_be_retried(self):
        for error in (RateUnavailable("offline"), RuntimeError("broken"),
                      TypeError("backend")):
            with self.subTest(error=error):
                calls = []

                def backend():
                    calls.append(None)
                    if len(calls) <= 2:
                        raise error
                    return 5

                account = Account(7, backend=backend)
                for _ in range(2):
                    with self.assertRaises(type(error)) as caught:
                        account.quote(2)
                    self.assertIs(caught.exception, error)
                self.assertEqual(account.quote(2), 10)
                self.assertEqual(account.quote(units=2, fee=0), 10)
                self.assertEqual(len(calls), 3)
                self.assertEqual(account.evaluations, 3)
                self.assertEqual(Account.quote.cache_info(account), (1, 3, 1))

    def test_backend_refresh_requires_clear_and_zero_is_cached(self):
        calls = []
        rate = 0

        def backend():
            calls.append(None)
            if rate is None:
                raise RateUnavailable("offline")
            return rate

        account = Account(7, backend=backend)
        self.assertEqual(account.quote(2), 0)
        rate = None
        self.assertEqual(account.quote(units=2, fee=0), 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(Account.quote.cache_info(account), (1, 1, 1))
        Account.quote.cache_clear(account)
        rate = 9
        self.assertEqual(account.quote(2), 18)
        self.assertEqual(len(calls), 2)
        self.assertEqual(account.evaluations, 2)
        self.assertEqual(Account.quote.cache_info(account), (0, 1, 1))

    def test_wrapped_bypasses_cache_and_preserves_metadata(self):
        account = Account(7)
        self.assertEqual(account.quote(2), 14)
        original = Account.quote.__wrapped__
        self.assertEqual(str(inspect.signature(Account.quote)),
                         "(account, units, *, fee=0)")
        self.assertEqual(Account.quote.__name__, original.__name__)
        self.assertEqual(Account.quote.__doc__, original.__doc__)
        account.rate = 11
        self.assertEqual(original(account, 2), 22)
        self.assertEqual(original(account=account, units=2, fee=1), 23)
        with self.assertRaises(QuoteError):
            original(account=account, units=[])
        self.assertEqual(Account.quote.cache_info(account), (0, 1, 1))
        self.assertEqual(account.evaluations, 4)
        self.assertEqual(account.quote(2), 14)
        self.assertEqual(account.evaluations, 4)

    def test_invalid_python_binding_does_not_execute_business_method(self):
        account = Account(7)
        calls = [
            lambda: Account.quote(),
            lambda: account.quote(),
            lambda: account.quote(1, 2),
            lambda: account.quote(1, units=1),
            lambda: account.quote(1, extra=0),
            lambda: account.quote(1, account=account),
            lambda: Account.quote(account, 1, account=account),
            lambda: Account.quote(owner=account, units=1),
            lambda: Account.quote(self=account, units=1),
            lambda: Account.quote(account=account),
        ]
        for call in calls:
            with self.subTest(call=call):
                with self.assertRaises(TypeError):
                    call()
        self.assertEqual(account.evaluations, 0)
        info = Account.quote.cache_info(account)
        self.assertEqual((info.hits, info.misses, info.size), (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
