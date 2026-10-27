import asyncio
import unittest
from concurrent.futures import CancelledError
from unittest.mock import Mock, call, sentinel

from app import save_document
from storage import AmbiguousWriteError, DocumentStore, TransientWriteError, WriteRejected


class SaveDocumentTests(unittest.TestCase):
    def test_success_returns_original_result_without_waiting(self):
        for key in (None, "  document-key  "):
            with self.subTest(key=key):
                client = Mock()
                client.write_document.return_value = sentinel.result
                sleep = Mock()
                payload = {"title": "example"}

                result = save_document(client, payload, key, sleep=sleep)

                self.assertIs(result, sentinel.result)
                self.assertIs(client.write_document.call_args.args[0], payload)
                self.assertIs(client.write_document.call_args.kwargs["idempotency_key"], key)
                client.write_document.assert_called_once_with(payload, idempotency_key=key)
                sleep.assert_not_called()

    def test_transient_errors_with_key_retry_then_return_original_result(self):
        client = Mock()
        client.write_document.side_effect = [
            TransientWriteError("first"),
            TransientWriteError("second"),
            sentinel.result,
        ]
        sleep = Mock()
        payload = {"title": "example"}
        key = "  document-key  "

        result = save_document(client, payload, key, sleep=sleep)

        self.assertIs(result, sentinel.result)
        self.assertEqual(client.write_document.call_count, 3)
        for args in client.write_document.call_args_list:
            self.assertIs(args.args[0], payload)
            self.assertIs(args.kwargs["idempotency_key"], key)
        self.assertEqual(sleep.call_args_list, [call(0.1), call(0.2)])

    def test_exhaustion_obeys_attempt_limit_and_raises_last_original_error(self):
        for attempts in range(1, 6):
            with self.subTest(attempts=attempts):
                errors = [TransientWriteError(str(i)) for i in range(attempts)]
                client = Mock()
                client.write_document.side_effect = errors
                sleep = Mock()

                with self.assertRaises(TransientWriteError) as raised:
                    save_document(
                        client, sentinel.payload, "key", max_attempts=attempts, sleep=sleep
                    )

                self.assertIs(raised.exception, errors[-1])
                self.assertEqual(
                    client.write_document.call_args_list,
                    [call(sentinel.payload, idempotency_key="key")] * attempts,
                )
                self.assertEqual(
                    sleep.call_args_list, [call(delay) for delay in (0.1, 0.2, 0.4, 0.8)[:attempts - 1]]
                )

    def test_transient_error_without_key_is_not_retried(self):
        error = TransientWriteError("one-shot write")
        client = Mock()
        client.write_document.side_effect = [error, sentinel.result]
        sleep = Mock()

        with self.assertRaises(TransientWriteError) as raised:
            save_document(client, sentinel.payload, max_attempts=5, sleep=sleep)

        self.assertIs(raised.exception, error)
        client.write_document.assert_called_once_with(sentinel.payload, idempotency_key=None)
        sleep.assert_not_called()

    def test_other_errors_and_cancellation_are_not_retried(self):
        errors = (
            WriteRejected("rejected"),
            AmbiguousWriteError("unknown outcome"),
            OSError("permanent I/O error"),
            RuntimeError("unexpected"),
            CancelledError("cancelled"),
            asyncio.CancelledError("cancelled"),
        )
        for error in errors:
            for key in (None, "key"):
                with self.subTest(error=type(error).__name__, key=key):
                    client = Mock()
                    client.write_document.side_effect = [error, sentinel.result]
                    sleep = Mock()

                    with self.assertRaises(type(error)) as raised:
                        save_document(client, sentinel.payload, key, sleep=sleep)

                    self.assertIs(raised.exception, error)
                    client.write_document.assert_called_once_with(
                        sentinel.payload, idempotency_key=key
                    )
                    sleep.assert_not_called()

    def test_permanent_failure_after_transient_error_stops_retrying(self):
        error = AmbiguousWriteError("unknown outcome")
        client = Mock()
        client.write_document.side_effect = [TransientWriteError("temporary"), error, sentinel.result]
        sleep = Mock()

        with self.assertRaises(AmbiguousWriteError) as raised:
            save_document(client, sentinel.payload, "key", sleep=sleep)

        self.assertIs(raised.exception, error)
        self.assertEqual(client.write_document.call_count, 2)
        sleep.assert_called_once_with(0.1)

    def test_sleep_failure_propagates_without_another_write(self):
        for error in (RuntimeError("sleep failed"), asyncio.CancelledError("cancelled")):
            with self.subTest(error=type(error).__name__):
                client = Mock()
                client.write_document.side_effect = TransientWriteError("temporary")
                sleep = Mock(side_effect=error)

                with self.assertRaises(type(error)) as raised:
                    save_document(client, sentinel.payload, "key", sleep=sleep)

                self.assertIs(raised.exception, error)
                client.write_document.assert_called_once_with(sentinel.payload, idempotency_key="key")
                sleep.assert_called_once_with(0.1)

    def test_invalid_attempt_limits_fail_before_writing(self):
        class IntSubclass(int):
            pass

        for attempts in (True, False, 0, -1, 6, 3.0, "3", None, IntSubclass(3)):
            with self.subTest(attempts=attempts):
                client = Mock()
                sleep = Mock()

                with self.assertRaises(ValueError):
                    save_document(client, sentinel.payload, "key", attempts, sleep)

                client.write_document.assert_not_called()
                sleep.assert_not_called()

    def test_invalid_keys_fail_before_writing(self):
        for key in ("", " \t\n", 0, False, b"key", [], {}):
            with self.subTest(key=key):
                client = Mock()
                sleep = Mock()

                with self.assertRaises(ValueError):
                    save_document(client, sentinel.payload, key, sleep=sleep)

                client.write_document.assert_not_called()
                sleep.assert_not_called()

    def test_retry_after_write_reuses_store_receipt(self):
        store = DocumentStore()
        payload = {"title": "example"}
        key = "  document-key  "
        calls = []

        def write_document(payload, idempotency_key=None):
            receipt = store.write_document(payload, idempotency_key=idempotency_key)
            calls.append(receipt)
            if len(calls) == 1:
                raise TransientWriteError("receipt unavailable")
            return receipt

        client = Mock()
        client.write_document.side_effect = write_document
        sleep = Mock()

        result = save_document(client, payload, key, sleep=sleep)

        self.assertEqual(result, "doc-1")
        self.assertEqual(calls, ["doc-1", "doc-1"])
        self.assertEqual(store.documents, [payload])
        self.assertEqual(store.receipts, {key: result})
        sleep.assert_called_once_with(0.1)


if __name__ == "__main__":
    unittest.main()
