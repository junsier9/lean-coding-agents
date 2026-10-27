import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock

from app import dispatch
from jobs import DeliveryUnknown, InProgress, NeedsReconciliation, NotAccepted, Store


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.store = Store()

    def test_success_reuses_original_receipt_without_resending(self):
        for receipt in (object(), None, False, ""):
            with self.subTest(receipt=receipt):
                store = Store()
                send = Mock(return_value=receipt)

                self.assertIs(dispatch(store, send, "job", "payload"), receipt)
                self.assertIs(dispatch(store, send, "job", "payload"), receipt)

                send.assert_called_once_with("payload")
                self.assertEqual(store.rows["job"].state, "sent")

    def test_not_accepted_releases_claim_and_allows_explicit_retry(self):
        error = NotAccepted("printer rejected the request")
        receipt = object()
        send = Mock(side_effect=[error, receipt])

        with self.assertRaises(NotAccepted) as raised:
            dispatch(self.store, send, "job", "payload")

        self.assertIs(raised.exception, error)
        send.assert_called_once_with("payload")
        self.assertNotIn("job", self.store.rows)
        self.assertIs(dispatch(self.store, send, "job", "payload"), receipt)
        self.assertEqual(send.call_count, 2)
        self.assertIs(dispatch(self.store, send, "job", "payload"), receipt)
        self.assertEqual(send.call_count, 2)

    def test_uncertain_errors_preserve_exception_and_block_resending(self):
        errors = (
            DeliveryUnknown("response lost"),
            TimeoutError("printer timed out"),
            ConnectionError("connection lost"),
            RuntimeError("unclassified failure"),
            KeyboardInterrupt(),
            SystemExit(1),
        )
        for error in errors:
            with self.subTest(error=type(error).__name__):
                store = Store()
                printed = []

                def send(payload):
                    printed.append(payload)
                    raise error

                with self.assertRaises(type(error)) as raised:
                    dispatch(store, send, "job", "payload")

                self.assertIs(raised.exception, error)
                self.assertEqual(store.rows["job"].state, "unknown")
                self.assertEqual(store.rows["job"].payload, "payload")
                with self.assertRaises(NeedsReconciliation):
                    dispatch(store, send, "job", "payload")
                self.assertEqual(printed, ["payload"])

    def test_payload_conflicts_preserve_existing_record_in_every_state(self):
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
                    dispatch(store, send, "job", "changed")

                self.assertIs(store.rows["job"], original)
                send.assert_not_called()

    def test_invalid_inputs_do_not_claim_or_send(self):
        invalid_values = (None, 0, False, [], b"text", "", " \t\n")
        store = Mock()
        send = Mock()
        for value in invalid_values:
            with self.subTest(field="job_id", value=value):
                with self.assertRaises(ValueError):
                    dispatch(store, send, value, "payload")
            with self.subTest(field="payload", value=value):
                with self.assertRaises(ValueError):
                    dispatch(store, send, "job", value)

        self.assertEqual(store.mock_calls, [])
        send.assert_not_called()

    def test_distinct_ids_and_payloads_are_not_normalized(self):
        send = Mock(side_effect=["receipt-1", "receipt-2"])

        self.assertEqual(dispatch(self.store, send, "job", " payload "), "receipt-1")
        self.assertEqual(dispatch(self.store, send, " job ", "payload"), "receipt-2")
        with self.assertRaises(ValueError):
            dispatch(self.store, send, "job", "payload")
        self.assertEqual(send.call_count, 2)
        self.assertEqual(send.call_args_list[0].args, (" payload ",))

    def test_pending_job_blocks_duplicates_but_not_other_jobs(self):
        entered = Event()
        release = Event()
        receipt = object()

        def slow_send(payload):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("test did not release sender")
            return receipt

        first_send = Mock(side_effect=slow_send)
        duplicate_send = Mock()
        other_send = Mock(return_value="other receipt")
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(dispatch, self.store, first_send, "job", "payload")
            try:
                self.assertTrue(entered.wait(5), "sender did not start")
                self.assertEqual(self.store.rows["job"].state, "pending")

                def check_pending():
                    with self.assertRaises(InProgress):
                        dispatch(self.store, duplicate_send, "job", "payload")
                    with self.assertRaises(ValueError):
                        dispatch(self.store, duplicate_send, "job", "changed")
                    return dispatch(self.store, other_send, "other", "other payload")

                second = executor.submit(check_pending)
                self.assertEqual(second.result(timeout=5), "other receipt")
                self.assertFalse(first.done())
                duplicate_send.assert_not_called()
                other_send.assert_called_once_with("other payload")
            finally:
                release.set()

            self.assertIs(first.result(timeout=5), receipt)
        self.assertIs(dispatch(self.store, duplicate_send, "job", "payload"), receipt)
        first_send.assert_called_once_with("payload")
        duplicate_send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
