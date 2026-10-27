import unittest
from unittest.mock import Mock, call

from app import create_invoice
from gateway import DeclinedError, TransientError


class CreateInvoiceTests(unittest.TestCase):
    def test_success_preserves_supplied_key_and_does_not_sleep(self):
        result = object()
        transport = Mock(return_value=result)
        sleep = Mock()

        self.assertIs(
            create_invoice(
                transport=transport, amount=125, key=" invoice-key ", sleep=sleep
            ),
            result,
        )

        transport.assert_called_once_with(125, " invoice-key ")
        sleep.assert_not_called()

    def test_transient_failures_retry_with_same_request_and_backoff(self):
        transport = Mock(
            side_effect=[TransientError("first"), TransientError("second"), "paid"]
        )
        sleep = Mock()

        self.assertEqual(create_invoice(transport, 125, "key", sleep=sleep), "paid")

        self.assertEqual(transport.call_args_list, [call(125, "key")] * 3)
        self.assertEqual(sleep.call_args_list, [call(0.01), call(0.02)])

    def test_exhaustion_reraises_last_original_error_for_each_attempt_limit(self):
        for max_attempts in range(1, 6):
            with self.subTest(max_attempts=max_attempts):
                errors = [TransientError(str(i)) for i in range(max_attempts)]
                transport = Mock(side_effect=errors)
                sleep = Mock()

                with self.assertRaises(TransientError) as caught:
                    create_invoice(transport, 1, "key", max_attempts, sleep)

                self.assertIs(caught.exception, errors[-1])
                self.assertEqual(
                    transport.call_args_list, [call(1, "key")] * max_attempts
                )
                self.assertEqual(
                    sleep.call_args_list,
                    [call(0.01 * (2 ** i)) for i in range(max_attempts - 1)],
                )

    def test_non_transient_errors_propagate_without_retry(self):
        for error in (
            DeclinedError("declined"),
            ConnectionError("unsupported connection failure"),
            RuntimeError("unexpected"),
            KeyboardInterrupt(),
        ):
            with self.subTest(error=type(error).__name__):
                transport = Mock(side_effect=error)
                sleep = Mock()

                with self.assertRaises(type(error)) as caught:
                    create_invoice(transport, 1, "key", sleep=sleep)

                self.assertIs(caught.exception, error)
                transport.assert_called_once_with(1, "key")
                sleep.assert_not_called()

    def test_decline_after_transient_failure_stops_retrying(self):
        error = DeclinedError("declined")
        transport = Mock(side_effect=[TransientError("retry"), error, "paid"])
        sleep = Mock()

        with self.assertRaises(DeclinedError) as caught:
            create_invoice(transport, 1, "key", sleep=sleep)

        self.assertIs(caught.exception, error)
        self.assertEqual(transport.call_args_list, [call(1, "key")] * 2)
        sleep.assert_called_once_with(0.01)

    def test_sleep_failure_propagates_without_another_attempt(self):
        error = RuntimeError("sleep failed")
        transport = Mock(side_effect=TransientError("retry"))
        sleep = Mock(side_effect=error)

        with self.assertRaises(RuntimeError) as caught:
            create_invoice(transport, 1, "key", sleep=sleep)

        self.assertIs(caught.exception, error)
        transport.assert_called_once_with(1, "key")
        sleep.assert_called_once_with(0.01)

    def test_invalid_arguments_fail_before_transport_or_sleep(self):
        class IntSubclass(int):
            pass

        invalid_values = {
            "amount": [0, -1, True, False, 1.0, "1", None, IntSubclass(1)],
            "key": ["", " \t\n", 1, True, None, b"key"],
            "max_attempts": [0, -1, 6, True, False, 3.0, "3", None, IntSubclass(3)],
        }
        for name, values in invalid_values.items():
            for value in values:
                with self.subTest(name=name, value=value):
                    arguments = {"amount": 1, "key": "key", "max_attempts": 3}
                    arguments[name] = value
                    transport = Mock()
                    sleep = Mock()

                    with self.assertRaisesRegex(ValueError, f"^{name}$"):
                        create_invoice(transport=transport, sleep=sleep, **arguments)

                    transport.assert_not_called()
                    sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
