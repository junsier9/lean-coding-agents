import unittest
from threading import Event, Thread
from unittest.mock import Mock

from app import dispatch
from jobs import DeliveryUnknown, InProgress, NeedsReconciliation, NotAccepted, Store


class DispatchTests(unittest.TestCase):
    def test_success_reuses_original_receipt_without_sending(self):
        for receipt in (object(), None, 0, ""):
            with self.subTest(receipt=receipt):
                store = Store()
                send = Mock(return_value=receipt)
                self.assertIs(dispatch(store, send, "job", "payload"), receipt)
                self.assertIs(
                    dispatch(store=store, send=send, job_id="job", payload="payload"),
                    receipt,
                )
                send.assert_called_once_with("payload")
                self.assertEqual(store.rows["job"].state, "sent")

    def test_not_accepted_releases_claim_and_allows_explicit_retry(self):
        store = Store()
        error = NotAccepted("not submitted")
        receipt = object()
        send = Mock(side_effect=[error, receipt])

        with self.assertRaises(NotAccepted) as raised:
            dispatch(store, send, "job", "payload")

        self.assertIs(raised.exception, error)
        send.assert_called_once_with("payload")
        self.assertNotIn("job", store.rows)
        self.assertIs(dispatch(store, send, "job", "payload"), receipt)
        self.assertEqual(send.call_count, 2)
        self.assertIs(dispatch(store, send, "job", "payload"), receipt)
        self.assertEqual(send.call_count, 2)

    def test_uncertain_failure_blocks_resending_and_preserves_exception(self):
        errors = (
            DeliveryUnknown("connection lost after printing"),
            RuntimeError("unexpected failure"),
            ConnectionError("acceptance unknown"),
            ValueError("sender failure"),
            KeyboardInterrupt(),
            SystemExit(),
        )
        for error in errors:
            with self.subTest(error=type(error).__name__):
                store = Store()
                send = Mock(side_effect=error)

                with self.assertRaises(type(error)) as raised:
                    dispatch(store, send, "job", "payload")

                self.assertIs(raised.exception, error)
                self.assertEqual(store.rows["job"].state, "unknown")
                replacement_send = Mock()
                with self.assertRaises(NeedsReconciliation):
                    dispatch(store, replacement_send, "job", "payload")
                replacement_send.assert_not_called()
                send.assert_called_once_with("payload")

    def test_payload_conflict_in_each_existing_state(self):
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

    def test_invalid_inputs_fail_before_claim_or_send(self):
        for field in ("job_id", "payload"):
            for value in (None, 0, False, b"text", [], "", " \t\n"):
                with self.subTest(field=field, value=value):
                    store = Mock(spec=Store)
                    send = Mock()
                    arguments = {"job_id": "job", "payload": "payload"}
                    arguments[field] = value

                    with self.assertRaisesRegex(ValueError, field):
                        dispatch(store, send, **arguments)

                    store.claim.assert_not_called()
                    send.assert_not_called()

    def test_valid_inputs_are_not_normalized(self):
        store = Store()
        send = Mock(return_value="receipt")
        self.assertEqual(dispatch(store, send, " job ", " payload "), "receipt")
        send.assert_called_once_with(" payload ")
        self.assertEqual(store.rows[" job "].payload, " payload ")

    def test_pending_job_excludes_duplicate_and_leaves_other_jobs_independent(self):
        store = Store()
        entered = Event()
        complete = Event()
        receipt = object()
        results = []
        errors = []

        def send(payload):
            entered.set()
            if not complete.wait(5):
                raise TimeoutError("test did not release sender")
            return receipt

        sender = Mock(side_effect=send)

        def run():
            try:
                results.append(dispatch(store, sender, "job", "payload"))
            except BaseException as error:
                errors.append(error)

        worker = Thread(target=run)
        worker.start()
        try:
            self.assertTrue(entered.wait(5), "sender was not entered")
            duplicate_send = Mock()
            with self.assertRaises(InProgress):
                dispatch(store, duplicate_send, "job", "payload")
            with self.assertRaises(ValueError):
                dispatch(store, duplicate_send, "job", "different")
            duplicate_send.assert_not_called()
            other_send = Mock(return_value="other receipt")
            self.assertEqual(
                dispatch(store, other_send, "other job", "payload"), "other receipt"
            )
            other_send.assert_called_once_with("payload")
        finally:
            complete.set()
            worker.join(5)

        self.assertFalse(worker.is_alive(), "dispatch did not finish")
        self.assertEqual(errors, [])
        self.assertEqual(results, [receipt])
        self.assertIs(dispatch(store, sender, "job", "payload"), receipt)
        sender.assert_called_once_with("payload")

    def test_failed_job_does_not_change_other_jobs(self):
        for error in (NotAccepted("rejected"), DeliveryUnknown("uncertain")):
            with self.subTest(error=type(error).__name__):
                store = Store()
                receipt = object()
                dispatch(store, Mock(return_value=receipt), "successful", "payload")
                store.claim("pending", "pending payload")
                pending = store.rows["pending"]

                with self.assertRaises(type(error)):
                    dispatch(store, Mock(side_effect=error), "failed", "payload")

                send = Mock(return_value="new receipt")
                self.assertIs(dispatch(store, send, "successful", "payload"), receipt)
                self.assertIs(store.rows["pending"], pending)
                self.assertEqual(dispatch(store, send, "new", "payload"), "new receipt")
                send.assert_called_once_with("payload")


if __name__ == "__main__":
    unittest.main()
