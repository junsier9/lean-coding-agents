import asyncio
import unittest
from unittest.mock import Mock, call

from app import save_document
from storage import AmbiguousWriteError, DocumentStore, TransientWriteError, WriteRejected


class SaveDocumentTests(unittest.TestCase):
    def test_invalid_attempt_limits_fail_before_writing(self):
        class IntSubclass(int):
            pass

        for attempts in (None, False, True, 0, -1, 6, 1.0, "3", IntSubclass(3)):
            with self.subTest(attempts=attempts, type=type(attempts)):
                client, sleep = Mock(), Mock()
                with self.assertRaises(ValueError):
                    save_document(client, {}, max_attempts=attempts, sleep=sleep)
                client.write_document.assert_not_called()
                sleep.assert_not_called()

    def test_invalid_keys_fail_before_writing(self):
        for key in ("", " \t\n", False, 0, [], {}, object()):
            with self.subTest(key=key):
                client, sleep = Mock(), Mock()
                with self.assertRaises(ValueError):
                    save_document(client, {}, idempotency_key=key, sleep=sleep)
                client.write_document.assert_not_called()
                sleep.assert_not_called()

    def test_immediate_success_returns_original_result(self):
        for key in (None, "  original key  "):
            with self.subTest(key=key):
                payload, result = object(), object()
                client, sleep = Mock(), Mock()
                client.write_document.return_value = result
                self.assertIs(
                    save_document(client, payload, idempotency_key=key, sleep=sleep),
                    result,
                )
                args, kwargs = client.write_document.call_args
                self.assertIs(args[0], payload)
                self.assertIs(kwargs["idempotency_key"], key)
                client.write_document.assert_called_once()
                sleep.assert_not_called()

    def test_transient_failure_without_key_is_not_retried(self):
        error = TransientWriteError("temporary")
        client, sleep = Mock(), Mock()
        client.write_document.side_effect = error
        with self.assertRaises(TransientWriteError) as caught:
            save_document(client, {}, max_attempts=5, sleep=sleep)
        self.assertIs(caught.exception, error)
        client.write_document.assert_called_once()
        sleep.assert_not_called()

    def test_keyed_transient_failures_can_succeed_on_last_attempt(self):
        for attempts in range(1, 6):
            with self.subTest(attempts=attempts):
                payload, result = object(), object()
                key = "  original key  "
                client, sleep = Mock(), Mock()
                client.write_document.side_effect = [
                    TransientWriteError(str(i)) for i in range(attempts - 1)
                ] + [result]
                self.assertIs(
                    save_document(client, payload, key, attempts, sleep), result
                )
                self.assertEqual(client.write_document.call_count, attempts)
                for args, kwargs in client.write_document.call_args_list:
                    self.assertIs(args[0], payload)
                    self.assertIs(kwargs["idempotency_key"], key)
                self.assertEqual(
                    sleep.call_args_list,
                    [call(0.1 * (2 ** i)) for i in range(attempts - 1)],
                )

    def test_exhaustion_propagates_last_original_error_without_final_sleep(self):
        for attempts in range(1, 6):
            with self.subTest(attempts=attempts):
                errors = [TransientWriteError(str(i)) for i in range(attempts)]
                client, sleep = Mock(), Mock()
                client.write_document.side_effect = errors
                with self.assertRaises(TransientWriteError) as caught:
                    save_document(client, {}, "key", attempts, sleep)
                self.assertIs(caught.exception, errors[-1])
                self.assertEqual(client.write_document.call_count, attempts)
                self.assertEqual(
                    sleep.call_args_list,
                    [call(0.1 * (2 ** i)) for i in range(attempts - 1)],
                )

    def test_other_errors_and_cancellation_propagate_without_retry(self):
        for error in (
            WriteRejected("rejected"),
            AmbiguousWriteError("ambiguous"),
            OSError("other I/O error"),
            RuntimeError("unexpected"),
            asyncio.CancelledError(),
            KeyboardInterrupt(),
            SystemExit(),
        ):
            with self.subTest(error=type(error)):
                client, sleep = Mock(), Mock()
                client.write_document.side_effect = error
                with self.assertRaises(type(error)) as caught:
                    save_document(client, {}, "key", 5, sleep)
                self.assertIs(caught.exception, error)
                client.write_document.assert_called_once()
                sleep.assert_not_called()

    def test_permanent_error_after_transient_failure_stops_retries(self):
        error = WriteRejected("rejected")
        client, sleep = Mock(), Mock()
        client.write_document.side_effect = [TransientWriteError(), error, object()]
        with self.assertRaises(WriteRejected) as caught:
            save_document(client, {}, "key", 5, sleep)
        self.assertIs(caught.exception, error)
        self.assertEqual(client.write_document.call_count, 2)
        sleep.assert_called_once_with(0.1)

    def test_cancellation_during_backoff_stops_retries(self):
        error = asyncio.CancelledError()
        client, sleep = Mock(), Mock(side_effect=error)
        client.write_document.side_effect = TransientWriteError()
        with self.assertRaises(asyncio.CancelledError) as caught:
            save_document(client, {}, "key", 5, sleep)
        self.assertIs(caught.exception, error)
        client.write_document.assert_called_once()
        sleep.assert_called_once_with(0.1)

    def test_existing_store_preserves_key_and_one_off_write_behavior(self):
        store = DocumentStore()
        payload = {"text": "document"}
        key = "  original key  "
        first = save_document(store, payload, key)
        self.assertEqual(save_document(store, payload, key), first)
        self.assertEqual(store.receipts, {key: first})
        self.assertEqual(len(store.documents), 1)
        self.assertNotEqual(save_document(store, payload), save_document(store, payload))
        self.assertEqual(len(store.documents), 3)


if __name__ == "__main__":
    unittest.main()
