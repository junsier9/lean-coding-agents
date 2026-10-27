import unittest
from asyncio import CancelledError
from unittest.mock import Mock, call

from app import save_document
from storage import AmbiguousWriteError, TransientWriteError, WriteRejected


class SaveDocumentTests(unittest.TestCase):
    def test_success_returns_original_result(self):
        for key in (None, " request-1 "):
            with self.subTest(key=key):
                payload = {"title": "document"}
                result = object()
                client = Mock()
                client.write_document.return_value = result
                sleep = Mock()

                self.assertIs(save_document(client, payload, key, sleep=sleep), result)

                client.write_document.assert_called_once_with(payload, idempotency_key=key)
                self.assertIs(client.write_document.call_args.args[0], payload)
                self.assertIs(client.write_document.call_args.kwargs["idempotency_key"], key)
                sleep.assert_not_called()

    def test_transient_errors_with_key_retry_until_success(self):
        payload = {"title": "document"}
        key = " request-1 "
        result = object()
        client = Mock()
        client.write_document.side_effect = [
            TransientWriteError("first"),
            TransientWriteError("second"),
            result,
        ]
        sleep = Mock()

        self.assertIs(save_document(client, payload, key, sleep=sleep), result)

        self.assertEqual(client.write_document.call_count, 3)
        for write_call in client.write_document.call_args_list:
            self.assertIs(write_call.args[0], payload)
            self.assertIs(write_call.kwargs["idempotency_key"], key)
        self.assertEqual(sleep.call_args_list, [call(0.1), call(0.2)])

    def test_exhaustion_propagates_last_error_without_final_sleep(self):
        for attempts in range(1, 6):
            with self.subTest(attempts=attempts):
                errors = [TransientWriteError(str(i)) for i in range(attempts)]
                client = Mock()
                client.write_document.side_effect = errors
                sleep = Mock()

                with self.assertRaises(TransientWriteError) as caught:
                    save_document(client, {}, "key", attempts, sleep)

                self.assertIs(caught.exception, errors[-1])
                self.assertEqual(client.write_document.call_count, attempts)
                self.assertEqual(
                    sleep.call_args_list,
                    [call(0.1 * (2 ** i)) for i in range(attempts - 1)],
                )

    def test_transient_error_without_key_is_not_retried(self):
        error = TransientWriteError("one-shot failure")
        client = Mock()
        client.write_document.side_effect = error
        sleep = Mock()

        with self.assertRaises(TransientWriteError) as caught:
            save_document(client, {}, max_attempts=5, sleep=sleep)

        self.assertIs(caught.exception, error)
        client.write_document.assert_called_once_with({}, idempotency_key=None)
        sleep.assert_not_called()

    def test_other_errors_and_cancellation_are_not_retried(self):
        for error in (
            WriteRejected("rejected"),
            AmbiguousWriteError("ambiguous"),
            RuntimeError("unexpected"),
            OSError("other I/O error"),
            CancelledError(),
            KeyboardInterrupt(),
        ):
            with self.subTest(error=type(error).__name__):
                client = Mock()
                client.write_document.side_effect = error
                sleep = Mock()

                with self.assertRaises(type(error)) as caught:
                    save_document(client, {}, "key", max_attempts=5, sleep=sleep)

                self.assertIs(caught.exception, error)
                client.write_document.assert_called_once_with({}, idempotency_key="key")
                sleep.assert_not_called()

    def test_permanent_error_after_transient_error_stops_retries(self):
        error = WriteRejected("rejected after transient failure")
        client = Mock()
        client.write_document.side_effect = [TransientWriteError("temporary"), error]
        sleep = Mock()

        with self.assertRaises(WriteRejected) as caught:
            save_document(client, {}, "key", max_attempts=5, sleep=sleep)

        self.assertIs(caught.exception, error)
        self.assertEqual(client.write_document.call_count, 2)
        sleep.assert_called_once_with(0.1)

    def test_invalid_attempt_counts_fail_before_writing(self):
        class IntSubclass(int):
            pass

        for attempts in (0, -1, 6, True, False, 3.0, "3", None, IntSubclass(3)):
            with self.subTest(attempts=attempts):
                client = Mock()
                sleep = Mock()

                with self.assertRaises(ValueError):
                    save_document(client, {}, "key", attempts, sleep)

                client.write_document.assert_not_called()
                sleep.assert_not_called()

    def test_invalid_keys_fail_before_writing(self):
        for key in ("", " \t\n", 0, False, b"key", [], {}):
            with self.subTest(key=key):
                client = Mock()
                sleep = Mock()

                with self.assertRaises(ValueError):
                    save_document(client, {}, key, sleep=sleep)

                client.write_document.assert_not_called()
                sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
