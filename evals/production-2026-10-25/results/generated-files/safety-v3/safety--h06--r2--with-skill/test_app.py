import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock

from app import dispatch
from jobs import DeliveryUnknown, InProgress, NeedsReconciliation, NotAccepted, Store


class DispatchTests(unittest.TestCase):
    def test_success_reuses_original_receipt_without_sending_again(self):
        for receipt in (object(), None, "", 0):
            with self.subTest(receipt=receipt):
                store = Store()
                send = Mock(return_value=receipt)

                self.assertIs(dispatch(store, send, "job", "page"), receipt)
                self.assertIs(dispatch(store, send, "job", "page"), receipt)

                send.assert_called_once_with("page")
                self.assertEqual(store.rows["job"].state, "sent")
                self.assertIs(store.rows["job"].receipt, receipt)

    def test_invalid_inputs_do_not_claim_or_send(self):
        for field in ("job_id", "payload"):
            for invalid in (None, "", " \t", 1, b"page"):
                with self.subTest(field=field, invalid=invalid):
                    store = Store()
                    send = Mock()
                    arguments = {"job_id": "job", "payload": "page"}
                    arguments[field] = invalid

                    with self.assertRaisesRegex(ValueError, f"^{field}$"):
                        dispatch(store, send, **arguments)

                    self.assertEqual(store.rows, {})
                    send.assert_not_called()

    def test_payload_conflicts_in_every_existing_state(self):
        for state in ("pending", "sent", "unknown"):
            with self.subTest(state=state):
                store = Store()
                store.claim("job", "original")
                if state == "sent":
                    store.finish("job", "original", "receipt")
                elif state == "unknown":
                    store.unknown("job", "original")
                original = store.rows["job"]
                send = Mock()

                with self.assertRaisesRegex(ValueError, "payload conflict"):
                    dispatch(store, send, "job", "different")

                self.assertIs(store.rows["job"], original)
                send.assert_not_called()

    def test_not_accepted_releases_claim_for_an_explicit_retry(self):
        store = Store()
        error = NotAccepted("printer refused")
        receipt = object()
        send = Mock(side_effect=[error, receipt])

        with self.assertRaises(NotAccepted) as raised:
            dispatch(store, send, "job", "page")

        self.assertIs(raised.exception, error)
        self.assertNotIn("job", store.rows)
        send.assert_called_once_with("page")

        self.assertIs(dispatch(store, send, "job", "page"), receipt)
        self.assertEqual(send.call_count, 2)
        self.assertEqual(store.rows["job"].state, "sent")

    def test_uncertain_failures_require_reconciliation_without_resending(self):
        errors = (
            DeliveryUnknown("receipt lost"),
            ConnectionError("connection lost"),
            RuntimeError("unexpected failure"),
            KeyboardInterrupt(),
            SystemExit(),
        )
        for error in errors:
            with self.subTest(error=type(error).__name__):
                store = Store()
                send = Mock(side_effect=error)

                with self.assertRaises(type(error)) as raised:
                    dispatch(store, send, "job", "page")

                self.assertIs(raised.exception, error)
                self.assertEqual(store.rows["job"].state, "unknown")
                self.assertEqual(store.rows["job"].payload, "page")
                with self.assertRaises(NeedsReconciliation):
                    dispatch(store, send, "job", "page")
                send.assert_called_once_with("page")

    def test_failure_recording_success_does_not_resend(self):
        store = Store()
        error = RuntimeError("recording failed")
        store.finish = Mock(side_effect=error)
        send = Mock(return_value="receipt")

        with self.assertRaises(RuntimeError) as raised:
            dispatch(store, send, "job", "page")

        self.assertIs(raised.exception, error)
        self.assertEqual(store.rows["job"].state, "unknown")
        with self.assertRaises(NeedsReconciliation):
            dispatch(store, send, "job", "page")
        send.assert_called_once_with("page")

    def test_pending_job_blocks_duplicates_but_other_jobs_can_finish(self):
        store = Store()
        entered = Event()
        release = Event()
        receipt = object()

        def blocked_send(payload):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("test did not release send")
            return receipt

        send = Mock(side_effect=blocked_send)
        other_send = Mock(return_value="other receipt")
        duplicate_send = Mock()
        with ThreadPoolExecutor(max_workers=2) as executor:
            pending = executor.submit(dispatch, store, send, "job", "page")
            try:
                self.assertTrue(entered.wait(5), "send did not start")
                duplicate = executor.submit(
                    dispatch, store, duplicate_send, "job", "page"
                )
                with self.assertRaises(InProgress):
                    duplicate.result(timeout=5)
                other = executor.submit(
                    dispatch, store, other_send, "other job", "other page"
                )
                self.assertEqual(other.result(timeout=5), "other receipt")
                self.assertEqual(store.rows["job"].state, "pending")
            finally:
                release.set()
            self.assertIs(pending.result(timeout=5), receipt)

        self.assertIs(dispatch(store, send, "job", "page"), receipt)
        send.assert_called_once_with("page")
        other_send.assert_called_once_with("other page")
        duplicate_send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
