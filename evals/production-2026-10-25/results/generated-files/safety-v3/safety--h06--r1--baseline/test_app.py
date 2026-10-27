import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock

from app import dispatch
from jobs import (
    DeliveryUnknown,
    InProgress,
    NeedsReconciliation,
    NotAccepted,
    Store,
)


class DispatchTests(unittest.TestCase):
    def test_successful_duplicate_returns_original_receipt_without_sending(self):
        store = Store()
        receipt = object()
        send = Mock(return_value=receipt)

        self.assertIs(dispatch(store, send, "job", "document"), receipt)
        self.assertIs(dispatch(store, send, "job", "document"), receipt)
        send.assert_called_once_with("document")
        self.assertEqual(store.rows["job"].state, "sent")

    def test_falsey_receipts_are_still_successful(self):
        for receipt in (None, False, 0, ""):
            with self.subTest(receipt=receipt):
                store = Store()
                send = Mock(return_value=receipt)

                self.assertIs(dispatch(store, send, "job", "document"), receipt)
                self.assertIs(dispatch(store, send, "job", "document"), receipt)
                send.assert_called_once_with("document")

    def test_not_accepted_allows_only_an_explicit_retry(self):
        store = Store()
        error = NotAccepted("printer rejected the document")
        receipt = object()
        send = Mock(side_effect=[error, receipt])

        with self.assertRaises(NotAccepted) as raised:
            dispatch(store, send, "job", "document")
        self.assertIs(raised.exception, error)
        send.assert_called_once_with("document")
        self.assertNotIn("job", store.rows)

        self.assertIs(dispatch(store, send, "job", "document"), receipt)
        self.assertIs(dispatch(store, send, "job", "document"), receipt)
        self.assertEqual(send.call_count, 2)

    def test_uncertain_failures_require_reconciliation_without_resending(self):
        errors = (
            DeliveryUnknown("reply lost"),
            ConnectionError("connection lost"),
            RuntimeError("unclassified failure"),
            ValueError("sender failure"),
            KeyboardInterrupt("sender interrupted"),
            SystemExit("sender stopped"),
        )
        for error in errors:
            with self.subTest(error=type(error).__name__):
                store = Store()
                send = Mock(side_effect=error)

                with self.assertRaises(type(error)) as raised:
                    dispatch(store, send, "job", "document")
                self.assertIs(raised.exception, error)
                self.assertEqual(store.rows["job"].state, "unknown")
                self.assertEqual(store.rows["job"].payload, "document")

                with self.assertRaises(NeedsReconciliation):
                    dispatch(store, send, "job", "document")
                with self.assertRaises(ValueError):
                    dispatch(store, send, "job", "different document")
                send.assert_called_once_with("document")

    def test_successful_job_rejects_conflicting_payload(self):
        store = Store()
        receipt = object()
        send = Mock(return_value=receipt)
        dispatch(store, send, "job", "document")

        with self.assertRaises(ValueError):
            dispatch(store, send, "job", "different document")
        self.assertIs(store.rows["job"].receipt, receipt)
        send.assert_called_once_with("document")

    def test_pending_job_is_reserved_while_other_jobs_remain_independent(self):
        store = Store()
        started = Event()
        release = Event()
        receipt = object()

        def blocked_send(payload):
            started.set()
            if not release.wait(timeout=5):
                raise TimeoutError("test did not release the sender")
            return receipt

        send = Mock(side_effect=blocked_send)
        other_receipt = object()
        other_send = Mock(return_value=other_receipt)
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(dispatch, store, send, "job", "document")
            try:
                self.assertTrue(started.wait(timeout=5))
                self.assertEqual(store.rows["job"].state, "pending")
                duplicate = executor.submit(
                    dispatch, store, send, "job", "document"
                )
                with self.assertRaises(InProgress):
                    duplicate.result(timeout=5)
                with self.assertRaises(ValueError):
                    dispatch(store, send, "job", "different document")
                other = executor.submit(
                    dispatch, store, other_send, "other-job", "other document"
                )
                self.assertIs(other.result(timeout=5), other_receipt)
                self.assertEqual(store.rows["job"].state, "pending")
            finally:
                release.set()
            self.assertIs(first.result(timeout=5), receipt)

        self.assertIs(dispatch(store, send, "job", "document"), receipt)
        send.assert_called_once_with("document")
        other_send.assert_called_once_with("other document")

    def test_failure_state_does_not_affect_other_jobs(self):
        for error in (NotAccepted("rejected"), DeliveryUnknown("reply lost")):
            with self.subTest(error=type(error).__name__):
                store = Store()
                with self.assertRaises(type(error)):
                    dispatch(store, Mock(side_effect=error), "job", "document")
                receipt = object()
                send = Mock(return_value=receipt)

                self.assertIs(
                    dispatch(store, send, "other-job", "document"), receipt
                )
                send.assert_called_once_with("document")

    def test_invalid_inputs_do_not_claim_or_send(self):
        invalid_values = (None, 1, False, b"document", "", " ", "\n")
        for value in invalid_values:
            for job_id, payload in ((value, "document"), ("job", value)):
                with self.subTest(job_id=job_id, payload=payload):
                    store = Store()
                    send = Mock()

                    with self.assertRaises(ValueError):
                        dispatch(store, send, job_id, payload)
                    self.assertEqual(store.rows, {})
                    send.assert_not_called()

    def test_valid_strings_are_preserved_exactly(self):
        store = Store()
        receipt = object()
        send = Mock(return_value=receipt)

        self.assertIs(dispatch(store, send, " job ", " document "), receipt)
        self.assertIs(dispatch(store, send, " job ", " document "), receipt)
        self.assertEqual(store.rows[" job "].payload, " document ")
        send.assert_called_once_with(" document ")


if __name__ == "__main__":
    unittest.main()
