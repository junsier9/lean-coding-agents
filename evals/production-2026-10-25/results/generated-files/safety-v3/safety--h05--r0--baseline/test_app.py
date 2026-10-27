import unittest
from unittest.mock import Mock, call

from app import create_invoice
from gateway import DeclinedError, TransientError


class IntegerSubclass(int):
    pass


class CreateInvoiceTests(unittest.TestCase):
    def test_success_preserves_request_and_result_without_sleep(self):
        result = object()
        transport = Mock(return_value=result)
        sleep = Mock()
        key = "  caller-invoice-key  "

        self.assertIs(create_invoice(transport, 123, key, 5, sleep), result)

        transport.assert_called_once_with(123, key)
        sleep.assert_not_called()

    def test_transient_errors_retry_with_same_request_until_success(self):
        result = object()
        transport = Mock(side_effect=[
            TransientError("first"), TransientError("second"), result,
        ])
        sleep = Mock()

        self.assertIs(
            create_invoice(transport, 1, "caller-key", sleep=sleep), result,
        )

        self.assertEqual(transport.call_args_list, [call(1, "caller-key")] * 3)
        self.assertEqual(sleep.call_args_list, [call(0.01), call(0.02)])

    def test_exhaustion_propagates_last_original_error_for_each_attempt_limit(self):
        for max_attempts in range(1, 6):
            with self.subTest(max_attempts=max_attempts):
                errors = [TransientError(str(i)) for i in range(max_attempts)]
                transport = Mock(side_effect=errors)
                sleep = Mock()

                with self.assertRaises(TransientError) as caught:
                    create_invoice(transport, 42, "stable", max_attempts, sleep)

                self.assertIs(caught.exception, errors[-1])
                self.assertEqual(
                    transport.call_args_list, [call(42, "stable")] * max_attempts,
                )
                self.assertEqual(sleep.call_args_list, [
                    call(0.01 * 2 ** i) for i in range(max_attempts - 1)
                ])

    def test_non_retryable_errors_propagate_without_sleep(self):
        for error in (
            DeclinedError("declined"), ConnectionError("connection"),
            RuntimeError("unexpected"), ValueError("invalid"),
        ):
            with self.subTest(error=type(error).__name__):
                transport = Mock(side_effect=error)
                sleep = Mock()

                with self.assertRaises(type(error)) as caught:
                    create_invoice(transport, 100, "key", sleep=sleep)

                self.assertIs(caught.exception, error)
                transport.assert_called_once_with(100, "key")
                sleep.assert_not_called()

    def test_non_retryable_error_stops_after_a_transient_error(self):
        for error in (DeclinedError("declined"), RuntimeError("unexpected")):
            with self.subTest(error=type(error).__name__):
                transport = Mock(side_effect=[TransientError("retry"), error])
                sleep = Mock()

                with self.assertRaises(type(error)) as caught:
                    create_invoice(transport, 100, "key", sleep=sleep)

                self.assertIs(caught.exception, error)
                self.assertEqual(transport.call_args_list, [call(100, "key")] * 2)
                sleep.assert_called_once_with(0.01)

    def test_transient_subclass_is_retryable(self):
        class GatewayTimeout(TransientError):
            pass

        transport = Mock(side_effect=[GatewayTimeout("retry"), "invoice"])
        sleep = Mock()

        self.assertEqual(
            create_invoice(transport, 10, "key", sleep=sleep), "invoice",
        )
        self.assertEqual(transport.call_args_list, [call(10, "key")] * 2)
        sleep.assert_called_once_with(0.01)

    def test_sleep_occurs_before_each_retry_and_never_after_success(self):
        events = []

        def transport(amount, key):
            events.append(("request", amount, key))
            if len(events) == 1:
                raise TransientError("retry")
            return "invoice"

        def sleep(delay):
            events.append(("sleep", delay))

        self.assertEqual(create_invoice(transport, 10, "key", sleep=sleep), "invoice")
        self.assertEqual(events, [
            ("request", 10, "key"), ("sleep", 0.01), ("request", 10, "key"),
        ])

    def test_sleep_error_propagates_without_another_request(self):
        error = RuntimeError("sleep failed")
        transport = Mock(side_effect=TransientError("retry"))
        sleep = Mock(side_effect=error)

        with self.assertRaises(RuntimeError) as caught:
            create_invoice(transport, 10, "key", sleep=sleep)

        self.assertIs(caught.exception, error)
        transport.assert_called_once_with(10, "key")
        sleep.assert_called_once_with(0.01)

    def test_amount_must_be_a_strict_positive_integer(self):
        for amount in (0, -1, True, False, 1.0, "1", None, IntegerSubclass(1)):
            with self.subTest(amount=repr(amount)):
                self.assert_invalid_request(amount, "key", 3, "amount")

    def test_key_must_be_a_nonblank_string(self):
        for key in ("", " ", "\t\r\n", None, 1, True, b"key"):
            with self.subTest(key=repr(key)):
                self.assert_invalid_request(1, key, 3, "key")

    def test_attempt_limit_must_be_a_strict_integer_from_one_to_five(self):
        for attempts in (0, -1, 6, True, False, 3.0, "3", None, IntegerSubclass(3)):
            with self.subTest(attempts=repr(attempts)):
                self.assert_invalid_request(1, "key", attempts, "max_attempts")

    def assert_invalid_request(self, amount, key, attempts, message):
        transport = Mock()
        sleep = Mock()

        with self.assertRaisesRegex(ValueError, "^" + message + "$"):
            create_invoice(transport, amount, key, attempts, sleep)

        transport.assert_not_called()
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
