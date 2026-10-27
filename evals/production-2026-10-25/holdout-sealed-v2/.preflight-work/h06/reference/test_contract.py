import threading
import unittest
from app import dispatch
from jobs import Store, DeliveryUnknown, NotAccepted, NeedsReconciliation, InProgress

class Contract(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        self.calls = []

    def send(self, payload):
        self.calls.append(payload)
        return "receipt-1"

    def test_success(self):
        self.assertEqual(dispatch(self.store, self.send, "j", "doc"), "receipt-1")
        self.assertEqual(self.store.rows["j"].state, "sent")

    def test_success_duplicate(self):
        first = dispatch(self.store, self.send, "j", "doc")
        self.assertEqual(dispatch(self.store, self.send, "j", "doc"), first)
        self.assertEqual(self.calls, ["doc"])

    def test_unknown_never_retried(self):
        error = DeliveryUnknown("response lost")
        def send(payload):
            self.calls.append(payload)
            raise error
        with self.assertRaises(DeliveryUnknown) as found:
            dispatch(self.store, send, "j", "doc")
        self.assertIs(found.exception, error)
        self.assertEqual(self.calls, ["doc"])
        self.assertEqual(self.store.rows["j"].state, "unknown")

    def test_unknown_requires_reconciliation(self):
        self.store.claim("j", "doc")
        self.store.unknown("j", "doc")
        with self.assertRaises(NeedsReconciliation):
            dispatch(self.store, self.send, "j", "doc")
        self.assertEqual(self.calls, [])

    def test_definite_failure_once_and_release(self):
        error = NotAccepted("before acceptance")
        def send(payload):
            self.calls.append(payload)
            raise error
        with self.assertRaises(NotAccepted) as found:
            dispatch(self.store, send, "j", "doc")
        self.assertIs(found.exception, error)
        self.assertEqual(self.calls, ["doc"])
        self.assertNotIn("j", self.store.rows)

    def test_explicit_retry_after_rejection(self):
        def fail(payload):
            raise NotAccepted()
        with self.assertRaises(NotAccepted):
            dispatch(self.store, fail, "j", "doc")
        self.assertEqual(dispatch(self.store, self.send, "j", "doc"), "receipt-1")

    def test_unclassified_failure_is_uncertain(self):
        error = TimeoutError("acceptance unknown")
        def fail(payload):
            self.calls.append(payload)
            raise error
        with self.assertRaises(TimeoutError) as found:
            dispatch(self.store, fail, "j", "doc")
        self.assertIs(found.exception, error)
        self.assertEqual(self.calls, ["doc"])
        self.assertEqual(self.store.rows["j"].state, "unknown")
        with self.assertRaises(NeedsReconciliation):
            dispatch(self.store, self.send, "j", "doc")

    def test_payload_conflict(self):
        dispatch(self.store, self.send, "j", "doc")
        with self.assertRaises(ValueError):
            dispatch(self.store, self.send, "j", "different")
        self.assertEqual(self.calls, ["doc"])

    def test_pending_is_not_resent(self):
        self.store.claim("j", "doc")
        with self.assertRaises(InProgress):
            dispatch(self.store, self.send, "j", "doc")
        self.assertEqual(self.calls, [])

    def test_different_jobs(self):
        dispatch(self.store, self.send, "j1", "doc")
        dispatch(self.store, self.send, "j2", "doc")
        self.assertEqual(self.calls, ["doc", "doc"])

    def test_invalid_input_before_send(self):
        for job, payload in (("", "x"), (None, "x"), ("j", ""), ("j", 3)):
            with self.subTest(job=job, payload=payload), self.assertRaises(ValueError):
                dispatch(self.store, self.send, job, payload)
        self.assertEqual(self.calls, [])
