import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock

from app import dispatch
from jobs import DeliveryUnknown, InProgress, NeedsReconciliation, NotAccepted, Store


class DispatchTests(unittest.TestCase):
    def test_success_reuses_original_receipt_without_sending_again(self):
        for receipt in (object(), None, False, 0, ""):
            with self.subTest(receipt=receipt):
                store = Store()
                send = Mock(return_value=receipt)

                self.assertIs(dispatch(store, send, "job", "document"), receipt)
                self.assertIs(dispatch(store, send, "job", "document"), receipt)

                send.assert_called_once_with("document")
                self.assertEqual(store.rows["job"].state, "sent")

    def test_invalid_input_does_not_claim_or_send(self):
        invalid_values = (None, 0, False, [], {}, "", " ", "\t\n")
        for field in ("job_id", "payload"):
            for value in invalid_values:
                with self.subTest(field=field, value=value):
                    store = Store()
                    send = Mock()
                    args = {"job_id": "job", "payload": "document"}
                    args[field] = value

                    with self.assertRaises(ValueError):
                        dispatch(store, send, **args)

                    self.assertEqual(store.rows, {})
                    send.assert_not_called()

    def test_payload_conflict_in_every_state_preserves_original_record(self):
        for state in ("pending", "sent", "unknown"):
            with self.subTest(state=state):
                store = Store()
                store.claim("job", "original")
                if state == "sent":
                    store.finish("job", "original", object())
                elif state == "unknown":
                    store.unknown("job", "original")
                original_record = store.rows["job"]
                send = Mock()

                with self.assertRaises(ValueError):
                    dispatch(store, send, "job", "changed")

                self.assertIs(store.rows["job"], original_record)
                send.assert_not_called()

    def test_not_accepted_releases_claim_and_allows_explicit_retry(self):
        store = Store()
        error = NotAccepted("printer rejected the request")
        receipt = object()
        send = Mock(side_effect=[error, receipt])

        with self.assertRaises(NotAccepted) as raised:
            dispatch(store, send, "job", "document")

        self.assertIs(raised.exception, error)
        self.assertNotIn("job", store.rows)
        send.assert_called_once_with("document")
        self.assertIs(dispatch(store, send, "job", "document"), receipt)
        self.assertEqual(send.call_count, 2)
        self.assertIs(dispatch(store, send, "job", "document"), receipt)
        self.assertEqual(send.call_count, 2)

    def test_repeated_rejections_each_require_an_explicit_call(self):
        store = Store()
        error = NotAccepted("still unavailable")
        send = Mock(side_effect=error)

        for expected_calls in (1, 2):
            with self.assertRaises(NotAccepted) as raised:
                dispatch(store, send, "job", "document")
            self.assertIs(raised.exception, error)
            self.assertEqual(send.call_count, expected_calls)
            self.assertNotIn("job", store.rows)

    def test_uncertain_delivery_requires_reconciliation_without_resending(self):
        errors = (
            DeliveryUnknown("printer response lost"),
            RuntimeError("unclassified failure"),
            ConnectionError("connection lost"),
            KeyboardInterrupt(),
            SystemExit(),
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
                for _ in range(2):
                    with self.assertRaises(NeedsReconciliation):
                        dispatch(store, send, "job", "document")
                send.assert_called_once_with("document")

    def test_concurrent_same_job_is_claimed_once(self):
        store = Store()
        entered = Event()
        release = Event()
        receipt = object()

        def print_document(payload):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("test did not release printer")
            return receipt

        send = Mock(side_effect=print_document)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(dispatch, store, send, "job", "document")
            try:
                self.assertTrue(entered.wait(3))
                second = pool.submit(dispatch, store, send, "job", "document")
                with self.assertRaises(InProgress):
                    second.result(timeout=3)
                with self.assertRaises(ValueError):
                    dispatch(store, send, "job", "changed")
                send.assert_called_once_with("document")
            finally:
                release.set()
            self.assertIs(first.result(timeout=3), receipt)

        self.assertIs(dispatch(store, send, "job", "document"), receipt)
        send.assert_called_once_with("document")

    def test_different_job_can_finish_while_first_job_is_sending(self):
        store = Store()
        entered = Event()
        release = Event()
        receipts = {"blocked": object(), "independent": object()}

        def send(payload):
            if payload == "blocked":
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("test did not release printer")
            return receipts[payload]

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(dispatch, store, send, "first", "blocked")
            try:
                self.assertTrue(entered.wait(3))
                second = pool.submit(dispatch, store, send, "second", "independent")
                self.assertIs(second.result(timeout=3), receipts["independent"])
                self.assertEqual(store.rows["first"].state, "pending")
                self.assertEqual(store.rows["second"].state, "sent")
            finally:
                release.set()
            self.assertIs(first.result(timeout=3), receipts["blocked"])

    def test_unknown_job_does_not_block_a_different_job(self):
        store = Store()
        error = DeliveryUnknown("response lost")
        receipt = object()
        send = Mock(side_effect=[error, receipt])

        with self.assertRaises(DeliveryUnknown):
            dispatch(store, send, "first", "document")
        self.assertIs(dispatch(store, send, "second", "document"), receipt)
        self.assertEqual(store.rows["first"].state, "unknown")
        self.assertEqual(store.rows["second"].state, "sent")
        self.assertEqual(send.call_count, 2)


if __name__ == "__main__":
    unittest.main()
