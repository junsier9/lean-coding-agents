import unittest
from threading import Event, Thread

from app import dispatch
from jobs import (
    DeliveryUnknown,
    InProgress,
    NeedsReconciliation,
    NotAccepted,
    Store,
)


class DispatchTests(unittest.TestCase):
    def test_success_reuses_original_receipt_without_sending_again(self):
        for receipt in (object(), None, False, 0, ""):
            with self.subTest(receipt=receipt):
                store = Store()
                calls = []

                def send(payload):
                    calls.append(payload)
                    return receipt

                self.assertIs(dispatch(store, send, "job", "print"), receipt)
                self.assertIs(dispatch(store, send, "job", "print"), receipt)
                self.assertEqual(calls, ["print"])
                self.assertEqual(store.rows["job"].state, "sent")

    def test_success_rejects_a_conflicting_payload(self):
        store = Store()
        calls = []
        receipt = object()

        def send(payload):
            calls.append(payload)
            return receipt

        dispatch(store, send, "job", "print")
        with self.assertRaises(ValueError):
            dispatch(store, send, "job", "different")
        self.assertEqual(calls, ["print"])
        self.assertIs(store.rows["job"].receipt, receipt)

    def test_not_accepted_releases_claim_and_allows_an_explicit_retry(self):
        store = Store()
        error = NotAccepted("printer rejected request")
        receipt = object()
        calls = []

        def send(payload):
            calls.append(payload)
            if len(calls) == 1:
                raise error
            return receipt

        with self.assertRaises(NotAccepted) as caught:
            dispatch(store, send, "job", "print")
        self.assertIs(caught.exception, error)
        self.assertEqual(calls, ["print"])
        self.assertNotIn("job", store.rows)

        self.assertIs(dispatch(store, send, "job", "print"), receipt)
        self.assertEqual(calls, ["print", "print"])

    def test_uncertain_failure_preserves_error_and_blocks_resending(self):
        errors = (
            DeliveryUnknown("response lost"),
            ConnectionError("connection lost"),
            TimeoutError("response timed out"),
            RuntimeError("unexpected failure"),
            KeyboardInterrupt(),
            SystemExit(),
        )
        for error in errors:
            with self.subTest(error=type(error).__name__):
                store = Store()
                calls = []

                def send(payload):
                    calls.append(payload)
                    raise error

                with self.assertRaises(type(error)) as caught:
                    dispatch(store, send, "job", "print")
                self.assertIs(caught.exception, error)
                self.assertEqual(calls, ["print"])
                self.assertEqual(store.rows["job"].state, "unknown")
                self.assertEqual(store.rows["job"].payload, "print")

                with self.assertRaises(NeedsReconciliation):
                    dispatch(store, send, "job", "print")
                with self.assertRaises(ValueError):
                    dispatch(store, send, "job", "different")
                self.assertEqual(calls, ["print"])

    def test_pending_job_is_reserved_without_blocking_another_job(self):
        store = Store()
        entered = Event()
        release = Event()
        receipt = object()
        results = []
        errors = []
        calls = []

        def send(payload):
            calls.append(payload)
            if payload == "first":
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("test did not release sender")
            return receipt

        def run_first():
            try:
                results.append(dispatch(store, send, "job", "first"))
            except BaseException as error:
                errors.append(error)

        worker = Thread(target=run_first, daemon=True)
        worker.start()
        try:
            self.assertTrue(entered.wait(5), "sender did not start")
            with self.assertRaises(InProgress):
                dispatch(store, send, "job", "first")
            with self.assertRaises(ValueError):
                dispatch(store, send, "job", "different")
            self.assertIs(dispatch(store, send, "other", "second"), receipt)
            self.assertEqual(store.rows["job"].state, "pending")
            self.assertEqual(store.rows["other"].state, "sent")
        finally:
            release.set()
            worker.join(5)

        self.assertFalse(worker.is_alive(), "sender did not finish")
        self.assertEqual(errors, [])
        self.assertEqual(results, [receipt])
        self.assertIs(dispatch(store, send, "job", "first"), receipt)
        self.assertEqual(calls, ["first", "second"])

    def test_invalid_inputs_do_not_claim_or_send(self):
        invalid = (None, 0, False, [], "", " \t\n")
        for field in ("job_id", "payload"):
            for value in invalid:
                with self.subTest(field=field, value=value):
                    store = Store()
                    calls = []
                    arguments = {"job_id": "job", "payload": "print"}
                    arguments[field] = value

                    with self.assertRaisesRegex(ValueError, field):
                        dispatch(store, calls.append, **arguments)
                    self.assertEqual(calls, [])
                    self.assertEqual(store.rows, {})


if __name__ == "__main__":
    unittest.main()
