import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock

from app import dispatch
from jobs import DeliveryUnknown, InProgress, NeedsReconciliation, NotAccepted, Store


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.store = Store()

    def test_success_returns_original_receipt_without_resending(self):
        receipt = object()
        send = Mock(return_value=receipt)

        self.assertIs(dispatch(self.store, send, "job", "document"), receipt)
        self.assertIs(dispatch(self.store, send, "job", "document"), receipt)

        send.assert_called_once_with("document")
        self.assertEqual(self.store.rows["job"].state, "sent")
        self.assertIs(self.store.rows["job"].receipt, receipt)

    def test_none_receipt_is_also_cached(self):
        send = Mock(return_value=None)

        self.assertIsNone(dispatch(self.store, send, "job", "document"))
        self.assertIsNone(dispatch(self.store, send, "job", "document"))

        send.assert_called_once_with("document")

    def test_not_accepted_releases_claim_and_allows_explicit_retry(self):
        error = NotAccepted("not queued")
        receipt = object()
        send = Mock(side_effect=[error, receipt])

        with self.assertRaises(NotAccepted) as caught:
            dispatch(self.store, send, "job", "document")

        self.assertIs(caught.exception, error)
        send.assert_called_once_with("document")
        self.assertNotIn("job", self.store.rows)
        self.assertIs(dispatch(self.store, send, "job", "document"), receipt)
        self.assertEqual(send.call_count, 2)

    def test_uncertain_failures_are_preserved_and_block_resending(self):
        for error in (
            DeliveryUnknown("connection lost after submission"),
            ConnectionError("connection lost"),
            RuntimeError("unexpected failure"),
            KeyboardInterrupt(),
            SystemExit(),
        ):
            with self.subTest(error=type(error).__name__):
                store = Store()
                send = Mock(side_effect=error)

                with self.assertRaises(type(error)) as caught:
                    dispatch(store, send, "job", "document")

                self.assertIs(caught.exception, error)
                self.assertEqual(store.rows["job"].state, "unknown")
                self.assertEqual(store.rows["job"].payload, "document")
                with self.assertRaises(NeedsReconciliation):
                    dispatch(store, send, "job", "document")
                send.assert_called_once_with("document")

                other_receipt = object()
                other_send = Mock(return_value=other_receipt)
                self.assertIs(
                    dispatch(store, other_send, "other-job", "document"),
                    other_receipt,
                )
                other_send.assert_called_once_with("document")

    def test_payload_conflicts_are_rejected_in_every_state(self):
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

                with self.assertRaises(ValueError):
                    dispatch(store, send, "job", "different")

                send.assert_not_called()
                self.assertIs(store.rows["job"], original)

    def test_invalid_inputs_are_rejected_before_claiming_or_sending(self):
        for field in ("job_id", "payload"):
            for value in (None, 1, b"text", "", " \t\n"):
                with self.subTest(field=field, value=value):
                    args = {"job_id": "job", "payload": "document"}
                    args[field] = value
                    send = Mock()

                    with self.assertRaisesRegex(ValueError, "^" + field + "$"):
                        dispatch(self.store, send, **args)

                    send.assert_not_called()
                    self.assertEqual(self.store.rows, {})

    def test_pending_claim_blocks_duplicates_but_not_other_jobs(self):
        started = Event()
        finish = Event()
        receipt = object()

        def send(payload):
            started.set()
            if not finish.wait(5):
                raise RuntimeError("test send was not released")
            return receipt

        first_send = Mock(side_effect=send)
        duplicate_send = Mock()
        other_receipt = object()
        other_send = Mock(return_value=other_receipt)
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(dispatch, self.store, first_send, "job", "document")
            try:
                self.assertTrue(started.wait(5))
                duplicate = executor.submit(
                    dispatch, self.store, duplicate_send, "job", "document"
                )
                with self.assertRaises(InProgress):
                    duplicate.result(timeout=5)
                other = executor.submit(
                    dispatch, self.store, other_send, "other-job", "other document"
                )
                self.assertIs(other.result(timeout=5), other_receipt)
                self.assertEqual(self.store.rows["job"].state, "pending")
            finally:
                finish.set()
            self.assertIs(first.result(timeout=5), receipt)

        duplicate_send.assert_not_called()
        first_send.assert_called_once_with("document")
        other_send.assert_called_once_with("other document")
        self.assertIs(
            dispatch(self.store, duplicate_send, "job", "document"), receipt
        )
        duplicate_send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
