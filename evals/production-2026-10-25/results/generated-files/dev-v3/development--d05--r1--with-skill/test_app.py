import asyncio
from concurrent.futures import CancelledError
import unittest
from unittest.mock import Mock, call

from app import save_document
from storage import (
    AmbiguousWriteError,
    DocumentStore,
    TransientWriteError,
    WriteRejected,
)


class SaveDocumentTests(unittest.TestCase):
    def test_success_returns_original_result_and_arguments(self):
        for key in (None, "  document-key\t"):
            with self.subTest(key=key):
                payload = {"title": "example"}
                receipt = object()
                client = Mock()
                client.write_document.return_value = receipt
                sleep = Mock()

                result = save_document(client, payload, key, sleep=sleep)

                self.assertIs(result, receipt)
                client.write_document.assert_called_once_with(
                    payload, idempotency_key=key
                )
                args, kwargs = client.write_document.call_args
                self.assertIs(args[0], payload)
                self.assertIs(kwargs["idempotency_key"], key)
                sleep.assert_not_called()

    def test_keyed_transient_errors_retry_with_original_arguments(self):
        payload = {"title": "example"}
        key = "  document-key\t"
        receipt = object()
        client = Mock()
        client.write_document.side_effect = [
            TransientWriteError("first"),
            TransientWriteError("second"),
            receipt,
        ]
        sleep = Mock()

        self.assertIs(save_document(client, payload, key, sleep=sleep), receipt)

        self.assertEqual(client.write_document.call_count, 3)
        for args, kwargs in client.write_document.call_args_list:
            self.assertIs(args[0], payload)
            self.assertIs(kwargs["idempotency_key"], key)
        self.assertEqual(sleep.call_args_list, [call(0.1), call(0.2)])

    def test_transient_error_without_key_propagates_after_one_attempt(self):
        error = TransientWriteError("not safe to repeat")
        client = Mock()
        client.write_document.side_effect = error
        sleep = Mock()

        with self.assertRaises(TransientWriteError) as raised:
            save_document(client, {}, max_attempts=5, sleep=sleep)

        self.assertIs(raised.exception, error)
        client.write_document.assert_called_once_with({}, idempotency_key=None)
        sleep.assert_not_called()

    def test_exhaustion_propagates_last_error_without_final_sleep(self):
        for attempts in range(1, 6):
            with self.subTest(attempts=attempts):
                errors = [TransientWriteError(str(i)) for i in range(attempts)]
                client = Mock()
                client.write_document.side_effect = errors
                sleep = Mock()

                with self.assertRaises(TransientWriteError) as raised:
                    save_document(client, {}, "key", attempts, sleep)

                self.assertIs(raised.exception, errors[-1])
                self.assertEqual(client.write_document.call_count, attempts)
                self.assertEqual(
                    sleep.call_args_list,
                    [call(0.1 * (2 ** i)) for i in range(attempts - 1)],
                )

    def test_other_errors_and_cancellation_never_retry(self):
        errors = (
            WriteRejected("rejected"),
            AmbiguousWriteError("may have committed"),
            OSError("permanent"),
            RuntimeError("unexpected"),
            ValueError("invalid"),
            CancelledError(),
            asyncio.CancelledError(),
            KeyboardInterrupt(),
            SystemExit(),
        )
        for key in (None, "key"):
            for error in errors:
                with self.subTest(key=key, error=type(error).__name__):
                    client = Mock()
                    client.write_document.side_effect = error
                    sleep = Mock()

                    with self.assertRaises(type(error)) as raised:
                        save_document(client, {}, key, max_attempts=5, sleep=sleep)

                    self.assertIs(raised.exception, error)
                    client.write_document.assert_called_once_with(
                        {}, idempotency_key=key
                    )
                    sleep.assert_not_called()

    def test_invalid_attempt_limits_fail_before_writing(self):
        class IntSubclass(int):
            pass

        for attempts in (False, True, 0, -1, 6, 3.0, "3", None, IntSubclass(3)):
            with self.subTest(attempts=attempts, type=type(attempts).__name__):
                client = Mock()
                sleep = Mock()

                with self.assertRaises(ValueError):
                    save_document(client, {}, "key", attempts, sleep)

                client.write_document.assert_not_called()
                sleep.assert_not_called()

    def test_invalid_keys_fail_before_writing(self):
        for key in ("", " \t\r\n", False, 0, 1, b"key", [], {}, object()):
            with self.subTest(key=key):
                client = Mock()
                sleep = Mock()

                with self.assertRaises(ValueError):
                    save_document(client, {}, key, sleep=sleep)

                client.write_document.assert_not_called()
                sleep.assert_not_called()

    def test_sleep_failure_propagates_without_another_write(self):
        client = Mock()
        client.write_document.side_effect = TransientWriteError("write")
        error = TransientWriteError("sleep")
        sleep = Mock(side_effect=error)

        with self.assertRaises(TransientWriteError) as raised:
            save_document(client, {}, "key", sleep=sleep)

        self.assertIs(raised.exception, error)
        client.write_document.assert_called_once_with({}, idempotency_key="key")
        sleep.assert_called_once_with(0.1)

    def test_ambiguous_unkeyed_commit_is_not_duplicated(self):
        error = AmbiguousWriteError("committed before connection failed")

        class AmbiguousStore(DocumentStore):
            def write_document(self, payload, idempotency_key=None):
                super().write_document(payload, idempotency_key=idempotency_key)
                raise error

        client = AmbiguousStore()
        payload = {"title": "example"}
        sleep = Mock()

        with self.assertRaises(AmbiguousWriteError) as raised:
            save_document(client, payload, sleep=sleep)

        self.assertIs(raised.exception, error)
        self.assertEqual(client.documents, [payload])
        sleep.assert_not_called()

    def test_document_store_preserves_idempotency(self):
        client = DocumentStore()
        payload = {"title": "example"}
        key = "  document-key\t"
        sleep = Mock()

        receipt = save_document(client, payload, key, sleep=sleep)
        repeated = save_document(client, payload, key, sleep=sleep)

        self.assertIs(repeated, receipt)
        self.assertEqual(client.documents, [payload])
        self.assertEqual(client.receipts, {key: receipt})
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
