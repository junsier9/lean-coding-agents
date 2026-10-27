import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock

from app import dispatch
from jobs import DeliveryUnknown, InProgress, NeedsReconciliation, NotAccepted, Store


class DispatchTests(unittest.TestCase):
    def test_success_returns_cached_receipt_without_resending(self):
        for receipt in (object(), None, False, 0, "", []):
            with self.subTest(receipt=receipt):
                store = Store()
                send = Mock(return_value=receipt)

                self.assertIs(dispatch(store, send, "job", "payload"), receipt)
                self.assertIs(dispatch(store, send, "job", "payload"), receipt)

                send.assert_called_once_with("payload")
                self.assertEqual(store.rows["job"].state, "sent")

    def test_invalid_inputs_fail_before_claim_or_send(self):
        for field in ("job_id", "payload"):
            for value in (None, "", " \t", 0, b"payload"):
                with self.subTest(field=field, value=value):
                    store = Mock()
                    send = Mock()
                    args = {"job_id": "job", "payload": "payload", field: value}

                    with self.assertRaisesRegex(ValueError, f"^{field}$"):
                        dispatch(store, send, **args)

                    store.claim.assert_not_called()
                    send.assert_not_called()

    def test_payload_conflict_is_rejected_in_every_existing_state(self):
        for state in ("pending", "sent", "unknown"):
            with self.subTest(state=state):
                store = Store()
                store.claim("job", "original")
                if state == "sent":
                    store.finish("job", "original", object())
                elif state == "unknown":
                    store.unknown("job", "original")
                original = store.rows["job"]
                send = Mock()

                with self.assertRaisesRegex(ValueError, "payload conflict"):
                    dispatch(store, send, "job", "different")

                send.assert_not_called()
                self.assertIs(store.rows["job"], original)

    def test_not_accepted_releases_claim_for_an_explicit_retry(self):
        store = Store()
        error = NotAccepted("printer rejected the job")
        receipt = object()
        send = Mock(side_effect=[error, receipt])

        with self.assertRaises(NotAccepted) as caught:
            dispatch(store, send, "job", "payload")

        self.assertIs(caught.exception, error)
        send.assert_called_once_with("payload")
        self.assertNotIn("job", store.rows)

        self.assertIs(dispatch(store, send, "job", "payload"), receipt)
        self.assertIs(dispatch(store, send, "job", "payload"), receipt)
        self.assertEqual(send.call_count, 2)

    def test_uncertain_failures_preserve_error_and_block_resending(self):
        for error in (
            DeliveryUnknown("acknowledgement lost"),
            RuntimeError("unclassified failure"),
            ConnectionError("connection lost"),
            TimeoutError("timed out"),
            KeyboardInterrupt(),
            SystemExit(),
        ):
            with self.subTest(error=type(error).__name__):
                store = Store()
                send = Mock(side_effect=error)

                with self.assertRaises(type(error)) as caught:
                    dispatch(store, send, "job", "payload")

                self.assertIs(caught.exception, error)
                self.assertEqual(store.rows["job"].state, "unknown")
                self.assertEqual(store.rows["job"].payload, "payload")
                with self.assertRaises(NeedsReconciliation) as blocked:
                    dispatch(store, send, "job", "payload")
                self.assertEqual(blocked.exception.args, ("job",))
                send.assert_called_once_with("payload")

    def test_finish_failure_marks_delivery_unknown(self):
        store = Store()
        error = RuntimeError("receipt could not be recorded")
        store.finish = Mock(side_effect=error)
        send = Mock(return_value=object())

        with self.assertRaises(RuntimeError) as caught:
            dispatch(store, send, "job", "payload")

        self.assertIs(caught.exception, error)
        self.assertEqual(store.rows["job"].state, "unknown")
        with self.assertRaises(NeedsReconciliation):
            dispatch(store, send, "job", "payload")
        send.assert_called_once_with("payload")

    def test_pending_job_blocks_duplicates_while_other_jobs_can_finish(self):
        store = Store()
        entered = Event()
        release = Event()
        first_receipt = object()
        second_receipt = object()

        def first_send(payload):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("test did not release the first job")
            return first_receipt

        send = Mock(side_effect=first_send)
        duplicate_send = Mock()
        other_send = Mock(return_value=second_receipt)
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(dispatch, store, send, "first", "payload")
            try:
                self.assertTrue(entered.wait(5))
                duplicate = executor.submit(
                    dispatch, store, duplicate_send, "first", "payload"
                )
                with self.assertRaises(InProgress) as blocked:
                    duplicate.result(timeout=5)
                self.assertEqual(blocked.exception.args, ("first",))
                other = executor.submit(
                    dispatch, store, other_send, "second", "other payload"
                )
                self.assertIs(other.result(timeout=5), second_receipt)
            finally:
                release.set()
            self.assertIs(first.result(timeout=5), first_receipt)

        duplicate_send.assert_not_called()
        self.assertIs(dispatch(store, send, "first", "payload"), first_receipt)
        send.assert_called_once_with("payload")
        other_send.assert_called_once_with("other payload")


if __name__ == "__main__":
    unittest.main()
