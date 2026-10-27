import unittest
from unittest.mock import Mock, call

from app import create_invoice
from gateway import DeclinedError, TransientError


class CreateInvoiceTests(unittest.TestCase):
    def test_success_preserves_supplied_key_and_result_without_sleep(self):
        result = object()
        transport = Mock(return_value=result)
        sleep = Mock()

        self.assertIs(create_invoice(transport, 100, " invoice-key ", sleep=sleep), result)

        transport.assert_called_once_with(100, " invoice-key ")
        sleep.assert_not_called()

    def test_transient_failures_retry_with_same_request_and_backoff(self):
        result = object()
        transport = Mock(side_effect=[TransientError(str(i)) for i in range(4)] + [result])
        sleep = Mock()

        self.assertIs(create_invoice(transport, 100, "key", 5, sleep), result)

        self.assertEqual(transport.call_args_list, [call(100, "key")] * 5)
        self.assertEqual(sleep.call_args_list, [call(0.01), call(0.02), call(0.04), call(0.08)])

    def test_exhaustion_raises_last_original_error_with_exact_attempt_limit(self):
        for max_attempts in range(1, 6):
            with self.subTest(max_attempts=max_attempts):
                errors = [TransientError(str(i)) for i in range(max_attempts)]
                transport = Mock(side_effect=errors)
                sleep = Mock()

                with self.assertRaises(TransientError) as caught:
                    create_invoice(transport, 100, "key", max_attempts, sleep)

                self.assertIs(caught.exception, errors[-1])
                self.assertEqual(transport.call_args_list, [call(100, "key")] * max_attempts)
                self.assertEqual(
                    sleep.call_args_list,
                    [call(0.01 * 2 ** i) for i in range(max_attempts - 1)],
                )

    def test_default_limit_includes_first_attempt(self):
        error = TransientError("unavailable")
        transport = Mock(side_effect=error)
        sleep = Mock()

        with self.assertRaises(TransientError) as caught:
            create_invoice(transport, 100, "key", sleep=sleep)

        self.assertIs(caught.exception, error)
        self.assertEqual(transport.call_count, 3)
        self.assertEqual(sleep.call_args_list, [call(0.01), call(0.02)])

    def test_non_transient_errors_propagate_without_retry(self):
        errors = [
            DeclinedError("declined"),
            RuntimeError("unexpected"),
            ConnectionError("not guaranteed retryable"),
            KeyboardInterrupt(),
        ]
        for error in errors:
            with self.subTest(error=type(error).__name__):
                transport = Mock(side_effect=error)
                sleep = Mock()

                with self.assertRaises(type(error)) as caught:
                    create_invoice(transport, 100, "key", sleep=sleep)

                self.assertIs(caught.exception, error)
                transport.assert_called_once_with(100, "key")
                sleep.assert_not_called()

    def test_decline_after_transient_error_stops_immediately(self):
        error = DeclinedError("declined")
        transport = Mock(side_effect=[TransientError("timeout"), error])
        sleep = Mock()

        with self.assertRaises(DeclinedError) as caught:
            create_invoice(transport, 100, "key", sleep=sleep)

        self.assertIs(caught.exception, error)
        self.assertEqual(transport.call_args_list, [call(100, "key")] * 2)
        sleep.assert_called_once_with(0.01)

    def test_sleep_failure_propagates_without_another_request(self):
        error = RuntimeError("sleep interrupted")
        transport = Mock(side_effect=TransientError("timeout"))
        sleep = Mock(side_effect=error)

        with self.assertRaises(RuntimeError) as caught:
            create_invoice(transport, 100, "key", sleep=sleep)

        self.assertIs(caught.exception, error)
        transport.assert_called_once_with(100, "key")
        sleep.assert_called_once_with(0.01)

    def test_invalid_amount_rejected_before_side_effects(self):
        for amount in (0, -1, True, False, 1.0, "100", None):
            with self.subTest(amount=amount):
                transport = Mock()
                sleep = Mock()

                with self.assertRaisesRegex(ValueError, "^amount$"):
                    create_invoice(transport, amount, "key", sleep=sleep)

                transport.assert_not_called()
                sleep.assert_not_called()

    def test_invalid_key_rejected_before_side_effects(self):
        for key in ("", " \t\n", None, 123, b"key"):
            with self.subTest(key=key):
                transport = Mock()
                sleep = Mock()

                with self.assertRaisesRegex(ValueError, "^key$"):
                    create_invoice(transport, 100, key, sleep=sleep)

                transport.assert_not_called()
                sleep.assert_not_called()

    def test_invalid_attempt_limit_rejected_before_side_effects(self):
        for max_attempts in (0, -1, 6, True, False, 1.0, "3", None):
            with self.subTest(max_attempts=max_attempts):
                transport = Mock()
                sleep = Mock()

                with self.assertRaisesRegex(ValueError, "^max_attempts$"):
                    create_invoice(transport, 100, "key", max_attempts, sleep)

                transport.assert_not_called()
                sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
