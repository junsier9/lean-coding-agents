import asyncio
import concurrent.futures
import unittest
from unittest.mock import Mock

from app import save_document
from storage import AmbiguousWriteError, DocumentStore, TransientWriteError, WriteRejected


class SaveDocumentTests(unittest.TestCase):
    def test_success_returns_original_result_and_arguments(self):
        payload = {"title": "example"}
        key = "  document-key  "
        result = object()
        client = Mock()
        client.write_document.return_value = result
        sleep = Mock()

        self.assertIs(save_document(client, payload, key, sleep=sleep), result)
        client.write_document.assert_called_once_with(payload, idempotency_key=key)
        self.assertIs(client.write_document.call_args.args[0], payload)
        self.assertIs(client.write_document.call_args.kwargs["idempotency_key"], key)
        sleep.assert_not_called()

    def test_transient_errors_retry_with_same_arguments_and_backoff(self):
        payload = {"title": "example"}
        key = "  document-key  "
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
        for call in client.write_document.call_args_list:
            self.assertIs(call.args[0], payload)
            self.assertIs(call.kwargs["idempotency_key"], key)
        self.assertEqual(sleep.call_args_list, [unittest.mock.call(0.1), unittest.mock.call(0.2)])

    def test_exhaustion_raises_last_original_error_without_final_sleep(self):
        for attempts in range(1, 6):
            with self.subTest(attempts=attempts):
                errors = [TransientWriteError(str(i)) for i in range(attempts)]
                client = Mock()
                client.write_document.side_effect = errors
                sleep = Mock()

                with self.assertRaises(TransientWriteError) as raised:
                    save_document(client, {}, "key", max_attempts=attempts, sleep=sleep)

                self.assertIs(raised.exception, errors[-1])
                self.assertEqual(client.write_document.call_count, attempts)
                self.assertEqual(
                    sleep.call_args_list,
                    [unittest.mock.call(0.1 * 2**i) for i in range(attempts - 1)],
                )

    def test_unkeyed_transient_failure_is_not_retried(self):
        error = TransientWriteError("unkeyed")
        client = Mock()
        client.write_document.side_effect = error
        sleep = Mock()

        with self.assertRaises(TransientWriteError) as raised:
            save_document(client, {}, max_attempts=5, sleep=sleep)

        self.assertIs(raised.exception, error)
        client.write_document.assert_called_once_with({}, idempotency_key=None)
        sleep.assert_not_called()

    def test_other_errors_and_cancellation_are_not_retried(self):
        for error in (
            WriteRejected("rejected"),
            AmbiguousWriteError("ambiguous"),
            OSError("other I/O error"),
            RuntimeError("unexpected"),
            concurrent.futures.CancelledError(),
            asyncio.CancelledError(),
        ):
            with self.subTest(error=type(error).__name__):
                client = Mock()
                client.write_document.side_effect = error
                sleep = Mock()

                with self.assertRaises(type(error)) as raised:
                    save_document(client, {}, "key", max_attempts=5, sleep=sleep)

                self.assertIs(raised.exception, error)
                client.write_document.assert_called_once()
                sleep.assert_not_called()

    def test_nontransient_error_after_retry_stops_immediately(self):
        error = AmbiguousWriteError("may already be written")
        client = Mock()
        client.write_document.side_effect = [TransientWriteError("temporary"), error]
        sleep = Mock()

        with self.assertRaises(AmbiguousWriteError) as raised:
            save_document(client, {}, "key", max_attempts=5, sleep=sleep)

        self.assertIs(raised.exception, error)
        self.assertEqual(client.write_document.call_count, 2)
        sleep.assert_called_once_with(0.1)

    def test_sleep_cancellation_propagates_without_another_write(self):
        error = asyncio.CancelledError()
        client = Mock()
        client.write_document.side_effect = TransientWriteError("temporary")
        sleep = Mock(side_effect=error)

        with self.assertRaises(asyncio.CancelledError) as raised:
            save_document(client, {}, "key", sleep=sleep)

        self.assertIs(raised.exception, error)
        client.write_document.assert_called_once()
        sleep.assert_called_once_with(0.1)

    def test_invalid_max_attempts_are_rejected_before_side_effects(self):
        class IntSubclass(int):
            pass

        for attempts in (True, False, 0, -1, 6, 1.0, "3", None, IntSubclass(3)):
            with self.subTest(attempts=attempts):
                client = Mock()
                sleep = Mock()
                with self.assertRaises(ValueError):
                    save_document(client, {}, "key", max_attempts=attempts, sleep=sleep)
                client.write_document.assert_not_called()
                sleep.assert_not_called()

    def test_invalid_keys_are_rejected_before_side_effects(self):
        for key in ("", " \t\n", 0, False, b"key", [], {}):
            with self.subTest(key=key):
                client = Mock()
                sleep = Mock()
                with self.assertRaises(ValueError):
                    save_document(client, {}, key, sleep=sleep)
                client.write_document.assert_not_called()
                sleep.assert_not_called()

    def test_store_accepts_unkeyed_and_keyed_writes(self):
        client = DocumentStore()
        sleep = Mock()

        self.assertEqual(save_document(client, {"value": 1}, sleep=sleep), "doc-1")
        self.assertEqual(save_document(client, {"value": 2}, " key ", sleep=sleep), "doc-2")
        self.assertEqual(save_document(client, {"value": 3}, " key ", sleep=sleep), "doc-2")
        self.assertEqual(client.documents, [{"value": 1}, {"value": 2}])
        self.assertEqual(client.receipts, {" key ": "doc-2"})
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
