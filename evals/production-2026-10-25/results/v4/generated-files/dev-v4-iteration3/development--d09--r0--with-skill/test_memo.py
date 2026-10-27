import inspect
import unittest
from unittest.mock import Mock

from accounting import Account, QuoteError, RateUnavailable


class MemoizedMethodTests(unittest.TestCase):
    def test_equivalent_call_forms_share_one_entry(self):
        backend = Mock(return_value=7)
        account = Account(0, backend)
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
            lambda: Account.quote(units=3, account=account, fee=0),
            lambda: Account.quote(fee=0, units=3, account=account),
        ]

        for index, call in enumerate(calls):
            with self.subTest(call=index):
                self.assertEqual(call(), 21)

        self.assertEqual(account.evaluations, 1)
        backend.assert_called_once_with()
        info = Account.quote.cache_info(account)
        self.assertEqual((info.hits, info.misses, info.size), (len(calls) - 1, 1, 1))

    def test_arguments_and_instances_have_separate_entries_and_statistics(self):
        first = Account(7)
        second = Account(13)

        self.assertEqual(first.quote(2), 14)
        self.assertEqual(first.quote(2, fee=3), 17)
        self.assertEqual(first.quote(3), 21)
        self.assertEqual(first.quote(units=2), 14)
        self.assertEqual(second.quote(2), 26)
        self.assertEqual(Account.quote(account=second, units=2, fee=0), 26)

        self.assertEqual(Account.quote.cache_info(first), (1, 3, 3))
        self.assertEqual(Account.quote.cache_info(second), (1, 1, 1))
        self.assertEqual((first.evaluations, second.evaluations), (3, 1))

    def test_clear_refreshes_only_the_selected_instance(self):
        backend = Mock(side_effect=[7, 11])
        first = Account(0, backend)
        second = Account(13)
        self.assertEqual(first.quote(2), 14)
        self.assertEqual(first.quote(2), 14)
        self.assertEqual(second.quote(2), 26)
        self.assertEqual(second.quote(2), 26)
        backend.assert_called_once_with()

        Account.quote.cache_clear(first)

        self.assertEqual(Account.quote.cache_info(first), (0, 0, 0))
        self.assertEqual(first.evaluations, 1)
        self.assertEqual(Account.quote.cache_info(second), (1, 1, 1))
        self.assertEqual(second.evaluations, 1)
        self.assertEqual(first.quote(2), 22)
        self.assertEqual(first.evaluations, 2)
        self.assertEqual(Account.quote.cache_info(first), (0, 1, 1))
        self.assertEqual(backend.call_count, 2)
        self.assertEqual(second.quote(2), 26)
        self.assertEqual(second.evaluations, 1)

    def test_wrapped_method_preserves_signature_and_bypasses_cache(self):
        backend = Mock(return_value=7)
        account = Account(0, backend)
        original = Account.quote.__wrapped__
        self.assertEqual(inspect.signature(Account.quote), inspect.signature(original))
        self.assertEqual(Account.quote.__name__, original.__name__)
        self.assertEqual(Account.quote.__doc__, original.__doc__)
        self.assertEqual(account.quote(2), 14)

        self.assertEqual(original(account, 2), 14)
        self.assertEqual(original(account, units=2, fee=0), 14)
        self.assertEqual(original(account=account, units=2), 14)
        self.assertEqual(account.quote.__wrapped__(account=account, units=2, fee=3), 17)

        self.assertEqual(account.evaluations, 5)
        self.assertEqual(backend.call_count, 5)
        self.assertEqual(Account.quote.cache_info(account), (0, 1, 1))
        self.assertEqual(account.quote(2), 14)
        self.assertEqual(account.evaluations, 5)
        self.assertEqual(Account.quote.cache_info(account), (1, 1, 1))

    def test_invalid_business_values_cannot_hit_cache_or_mask_quote_error(self):
        class IntSubclass(int):
            pass

        backend = Mock(return_value=7)
        account = Account(0, backend)
        account.quote(0)
        account.quote(1)
        account.quote(1, fee=1)
        invalid_values = [-1, True, False, 0.0, 1.0, "1", None, [], {}, set(), IntSubclass(1)]
        attempts = 0

        for field in ("units", "fee"):
            for value in invalid_values:
                for repeat in range(2):
                    with self.subTest(field=field, value=value, repeat=repeat):
                        arguments = {"units": 1, "fee": 0, field: value}
                        with self.assertRaises(QuoteError) as caught:
                            account.quote(**arguments)
                        self.assertEqual(str(caught.exception), field)
                        attempts += 1
                        self.assertEqual(account.evaluations, 3 + attempts)
                        self.assertEqual(Account.quote.cache_info(account), (0, 3 + attempts, 3))

        self.assertEqual(backend.call_count, 3)
        self.assertEqual(account.quote(1), 7)
        self.assertEqual(Account.quote.cache_info(account), (1, 3 + attempts, 3))

    def test_backend_exceptions_propagate_unchanged_and_are_not_cached(self):
        errors = [
            RateUnavailable("offline"),
            RuntimeError("backend failure"),
            TypeError("backend type error"),
            KeyError("missing rate"),
            KeyboardInterrupt(),
        ]
        for error in errors:
            with self.subTest(error=type(error).__name__):
                backend = Mock(side_effect=[error, error, 9])
                account = Account(0, backend)
                for attempt in range(2):
                    with self.assertRaises(type(error)) as caught:
                        Account.quote(account=account, units=2)
                    self.assertIs(caught.exception, error)
                    self.assertEqual(account.evaluations, attempt + 1)
                    self.assertEqual(Account.quote.cache_info(account), (0, attempt + 1, 0))

                self.assertEqual(account.quote(2), 18)
                self.assertEqual(account.quote(units=2, fee=0), 18)
                self.assertEqual(account.evaluations, 3)
                self.assertEqual(backend.call_count, 3)
                self.assertEqual(Account.quote.cache_info(account), (1, 3, 1))

    def test_invalid_python_bindings_do_not_execute_or_change_statistics(self):
        backend = Mock(return_value=7)
        account = Account(0, backend)
        account.quote(2)
        calls = [
            lambda: Account.quote(),
            lambda: Account.quote(units=2),
            lambda: account.quote(),
            lambda: Account.quote(account=account),
            lambda: account.quote(2, 3),
            lambda: Account.quote(account, 2, 3),
            lambda: account.quote(2, units=2),
            lambda: Account.quote(account, 2, account=account),
            lambda: account.quote(account=account, units=2),
            lambda: account.quote(2, extra=1),
            lambda: Account.quote(owner=account, units=2),
            lambda: Account.quote(account=account, units=2, owner=account),
            lambda: Account.quote(2, account=account),
        ]

        for index, call in enumerate(calls):
            with self.subTest(call=index):
                with self.assertRaises(TypeError):
                    call()
                self.assertEqual(Account.quote.cache_info(account), (0, 1, 1))
                self.assertEqual(account.evaluations, 1)
                backend.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
