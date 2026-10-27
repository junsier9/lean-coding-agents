import unittest
from app import create_invoice
from gateway import TransientError, DeclinedError

class Transport:
    def __init__(self, outcomes):
        self.outcomes, self.calls = list(outcomes), []
    def __call__(self, amount, key):
        self.calls.append((amount, key))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

class Contract(unittest.TestCase):
    def test_success_without_wait(self):
        transport, waits = Transport(["receipt"]), []
        self.assertEqual(create_invoice(transport, 100, "k", sleep=waits.append), "receipt")
        self.assertEqual(waits, [])

    def test_stable_key(self):
        transport = Transport([TransientError("once"), "ok"])
        create_invoice(transport, 100, "caller-key", sleep=lambda _: None)
        self.assertEqual(transport.calls, [(100, "caller-key"), (100, "caller-key")])

    def test_two_retries(self):
        transport, waits = Transport([TransientError(), TransientError(), "ok"]), []
        self.assertEqual(create_invoice(transport, 1, "k", sleep=waits.append), "ok")
        self.assertEqual(waits, [0.01, 0.02])

    def test_decline_not_retried(self):
        error = DeclinedError("no")
        transport = Transport([error, "wrong"])
        with self.assertRaises(DeclinedError) as found:
            create_invoice(transport, 1, "k", sleep=lambda _: None)
        self.assertIs(found.exception, error)
        self.assertEqual(len(transport.calls), 1)

    def test_bug_not_retried(self):
        error = ValueError("bug")
        transport = Transport([error, "wrong"])
        with self.assertRaises(ValueError) as found:
            create_invoice(transport, 1, "k", sleep=lambda _: None)
        self.assertIs(found.exception, error)
        self.assertEqual(len(transport.calls), 1)

    def test_budget_exhaustion(self):
        error = TransientError("last")
        transport, waits = Transport([TransientError(), error]), []
        with self.assertRaises(TransientError) as found:
            create_invoice(transport, 1, "k", max_attempts=2, sleep=waits.append)
        self.assertIs(found.exception, error)
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(waits, [0.01])

    def test_one_attempt(self):
        transport, waits = Transport([TransientError("only")]), []
        with self.assertRaises(TransientError):
            create_invoice(transport, 1, "k", max_attempts=1, sleep=waits.append)
        self.assertEqual(waits, [])

    def test_missing_key_before_io(self):
        for key in (None, "", " ", 3):
            transport = Transport(["bad"])
            with self.subTest(key=key), self.assertRaises(ValueError):
                create_invoice(transport, 1, key)
            self.assertEqual(transport.calls, [])

    def test_invalid_amount(self):
        for amount in (True, 0, -1, 1.2):
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                create_invoice(Transport(["bad"]), amount, "k")

    def test_invalid_budget(self):
        for budget in (True, 0, 6, 1.5):
            with self.subTest(budget=budget), self.assertRaises(ValueError):
                create_invoice(Transport(["bad"]), 1, "k", max_attempts=budget)
