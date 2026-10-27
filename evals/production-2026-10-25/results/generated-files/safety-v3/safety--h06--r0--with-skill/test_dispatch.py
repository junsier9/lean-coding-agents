from concurrent.futures import ThreadPoolExecutor
from threading import Event
import unittest
from unittest.mock import Mock

from app import dispatch
from jobs import DeliveryUnknown, InProgress, NeedsReconciliation, NotAccepted, Store


class DispatchTests(unittest.TestCase):
    def test_success_returns_original_receipt_without_resending(self):
        for receipt in (object(), None, False, ""):
            with self.subTest(receipt=receipt):
                store = Store()
                send = Mock(return_value=receipt)

                self.assertIs(dispatch(store, send, "job", " payload "), receipt)
                self.assertIs(dispatch(store, send, "job", " payload "), receipt)

                send.assert_called_once_with(" payload ")
                self.assertEqual(store.rows["job"].state, "sent")
                self.assertIs(store.rows["job"].receipt, receipt)

    def test_successful_job_rejects_conflicting_payload(self):
        store = Store()
        send = Mock(return_value="receipt")
        dispatch(store, send, "job", "first")

        with self.assertRaises(ValueError):
            dispatch(store, send, "job", "second")

        send.assert_called_once_with("first")
        self.assertEqual(store.rows["job"].payload, "first")
        self.assertEqual(store.rows["job"].state, "sent")

    def test_not_accepted_releases_claim_for_an_explicit_retry(self):
        store = Store()
        error = NotAccepted("rejected before printing")
        receipt = object()
        send = Mock(side_effect=[error, receipt])

        with self.assertRaises(NotAccepted) as raised:
            dispatch(store, send, "job", "payload")

        self.assertIs(raised.exception, error)
        send.assert_called_once_with("payload")
        self.assertNotIn("job", store.rows)

        self.assertIs(dispatch(store, send, "job", "payload"), receipt)
        self.assertIs(dispatch(store, send, "job", "payload"), receipt)
        self.assertEqual(send.call_count, 2)
        self.assertEqual(store.rows["job"].state, "sent")

    def test_uncertain_failures_require_reconciliation_without_resending(self):
        for error in (
            DeliveryUnknown("receipt lost"),
            RuntimeError("unclassified failure"),
            ConnectionError("connection lost"),
            KeyboardInterrupt("interrupted"),
        ):
            with self.subTest(error=type(error).__name__):
                store = Store()
                send = Mock(side_effect=error)

                with self.assertRaises(type(error)) as raised:
                    dispatch(store, send, "job", "payload")

                self.assertIs(raised.exception, error)
                self.assertEqual(store.rows["job"].state, "unknown")
                self.assertEqual(store.rows["job"].payload, "payload")

                with self.assertRaises(NeedsReconciliation):
                    dispatch(store, send, "job", "payload")
                with self.assertRaises(ValueError):
                    dispatch(store, send, "job", "changed")

                send.assert_called_once_with("payload")
                other_send = Mock(return_value="other receipt")
                self.assertEqual(
                    dispatch(store, other_send, "other job", "payload"),
                    "other receipt",
                )
                other_send.assert_called_once_with("payload")
                self.assertEqual(store.rows["job"].state, "unknown")

    def test_pending_claim_blocks_duplicates_but_not_other_jobs(self):
        store = Store()
        entered = Event()
        release = Event()
        first_receipt = object()
        second_receipt = object()

        def print_payload(payload):
            if payload == "first":
                entered.set()
                if not release.wait(timeout=5):
                    raise TimeoutError("test did not release first send")
                return first_receipt
            return second_receipt

        send = Mock(side_effect=print_payload)
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(dispatch, store, send, "job", "first")
            try:
                self.assertTrue(entered.wait(timeout=5))
                with self.assertRaises(InProgress):
                    dispatch(store, send, "job", "first")
                with self.assertRaises(ValueError):
                    dispatch(store, send, "job", "changed")

                second = executor.submit(dispatch, store, send, "other", "second")
                self.assertIs(second.result(timeout=5), second_receipt)
                self.assertEqual(store.rows["job"].state, "pending")
            finally:
                release.set()

            self.assertIs(first.result(timeout=5), first_receipt)

        self.assertIs(dispatch(store, send, "job", "first"), first_receipt)
        self.assertEqual(send.call_count, 2)
        self.assertEqual(store.rows["job"].state, "sent")
        self.assertEqual(store.rows["other"].state, "sent")

    def test_invalid_inputs_fail_before_claiming_or_sending(self):
        for field in ("job_id", "payload"):
            for invalid in (None, 0, False, b"text", "", " \t\n"):
                with self.subTest(field=field, invalid=invalid):
                    store = Store()
                    send = Mock()
                    arguments = {"job_id": "job", "payload": "payload"}
                    arguments[field] = invalid

                    with self.assertRaisesRegex(ValueError, field):
                        dispatch(store, send, **arguments)

                    send.assert_not_called()
                    self.assertEqual(store.rows, {})


if __name__ == "__main__":
    unittest.main()
