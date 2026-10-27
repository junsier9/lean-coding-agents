import unittest
from accounting import Account, QuoteError, RateUnavailable

class Contract(unittest.TestCase):
    def info(self, account):
        value = Account.quote.cache_info(account)
        return value.hits, value.misses, value.size

    def test_bound_positional_call(self):
        account = Account(10)
        self.assertEqual(account.quote(3, fee=2), 32)
        self.assertEqual(self.info(account), (0, 1, 1))

    def test_bound_business_keyword_call(self):
        account = Account(10)
        self.assertEqual(account.quote(units=3, fee=2), 32)
        self.assertEqual(account.evaluations, 1)

    def test_unbound_receiver_positional_call(self):
        account = Account(10)
        self.assertEqual(Account.quote(account, 3, fee=2), 32)

    def test_unbound_receiver_keyword_call(self):
        account = Account(10)
        self.assertEqual(Account.quote(account=account, units=3, fee=2), 32)
        self.assertEqual(self.info(account), (0, 1, 1))

    def test_unbound_mixed_call(self):
        account = Account(10)
        self.assertEqual(Account.quote(account, units=3, fee=2), 32)

    def test_equivalent_forms_share_cache(self):
        account = Account(10)
        results = [account.quote(3), account.quote(units=3),
                   Account.quote(account, 3, fee=0),
                   Account.quote(account=account, units=3),
                   Account.quote(account, units=3, fee=0)]
        self.assertEqual(results, [30] * 5)
        self.assertEqual(account.evaluations, 1)
        self.assertEqual(self.info(account), (4, 1, 1))

    def test_cache_hit_skips_business_backend(self):
        calls = []
        def backend():
            calls.append(1)
            return 7
        account = Account(0, backend)
        self.assertEqual(account.quote(2), 14)
        self.assertEqual(account.quote(2), 14)
        self.assertEqual(calls, [1])
        self.assertEqual(self.info(account), (1, 1, 1))

    def test_public_wrapped_bypasses_cache_positionally(self):
        account = Account(10)
        self.assertEqual(account.quote(2), 20)
        before = self.info(account)
        self.assertEqual(Account.quote.__wrapped__(account, 2), 20)
        self.assertEqual(account.evaluations, 2)
        self.assertEqual(self.info(account), before)

    def test_public_wrapped_bypasses_cache_with_receiver_keyword(self):
        account = Account(10)
        self.assertEqual(account.quote(2), 20)
        before = self.info(account)
        self.assertEqual(account.quote.__wrapped__(account=account, units=2), 20)
        self.assertEqual(account.evaluations, 2)
        self.assertEqual(self.info(account), before)

    def test_instance_cache_and_statistics_are_isolated(self):
        first, second = Account(10), Account(20)
        self.assertEqual(first.quote(2), 20)
        self.assertEqual(first.quote(2), 20)
        self.assertEqual(second.quote(2), 40)
        self.assertEqual((first.evaluations, second.evaluations), (1, 1))
        self.assertEqual(self.info(first), (1, 1, 1))
        self.assertEqual(self.info(second), (0, 1, 1))

    def test_clear_affects_one_instance_only(self):
        first, second = Account(10), Account(20)
        first.quote(2)
        second.quote(2)
        Account.quote.cache_clear(first)
        self.assertEqual(self.info(first), (0, 0, 0))
        self.assertEqual(self.info(second), (0, 1, 1))
        self.assertEqual(first.quote(2), 20)
        self.assertEqual(first.evaluations, 2)
        self.assertEqual(second.evaluations, 1)

    def test_business_exceptions_propagate_and_are_not_cached(self):
        for error in (RateUnavailable("down"), TypeError("backend bug"), OSError("I/O")):
            with self.subTest(error=type(error).__name__):
                state = [error]
                def backend():
                    if state[0] is not None:
                        raise state[0]
                    return 8
                account = Account(0, backend)
                for _ in range(2):
                    with self.assertRaises(type(error)) as found:
                        account.quote(3)
                    self.assertIs(found.exception, error)
                self.assertEqual(self.info(account), (0, 2, 0))
                state[0] = None
                self.assertEqual(account.quote(3), 24)
                self.assertEqual(self.info(account), (0, 3, 1))

    def test_business_input_errors_not_replaced_by_cache_errors(self):
        for units, fee in ((True, 0), (-1, 0), (1.5, 0), ([], 0),
                           (2, True), (2, -1), (2, [])):
            with self.subTest(units=units, fee=fee):
                account = Account(10)
                with self.assertRaises(QuoteError):
                    account.quote(units, fee=fee)
                self.assertEqual(self.info(account), (0, 1, 0))

    def test_invalid_call_shape_is_not_silently_accepted(self):
        account = Account(10)
        for call in (lambda: account.quote(),
                     lambda: Account.quote(account=account),
                     lambda: account.quote(2, units=2),
                     lambda: account.quote(2, unexpected=True)):
            with self.subTest(call=call), self.assertRaises(TypeError):
                call()
        self.assertEqual(self.info(account)[2], 0)

    def test_distinct_business_parameters_have_distinct_entries(self):
        account = Account(10)
        self.assertEqual(account.quote(2), 20)
        self.assertEqual(account.quote(2, fee=5), 25)
        self.assertEqual(account.quote(units=2, fee=5), 25)
        self.assertEqual(self.info(account), (1, 2, 2))
        self.assertEqual(account.evaluations, 2)
