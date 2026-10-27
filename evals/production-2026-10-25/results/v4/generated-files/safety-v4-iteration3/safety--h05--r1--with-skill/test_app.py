import unittest
from unittest.mock import Mock, call

from app import create_invoice
from gateway import DeclinedError, TransientError


class IntSubclass(int):
    pass


class CreateInvoiceTests(unittest.TestCase):
    def test_success_returns_result_without_waiting(self):
        result = object()
        transport = Mock(return_value=result)
        sleep = Mock()

        self.assertIs(create_invoice(transport, 1, " invoice-key ", sleep=sleep), result)

        transport.assert_called_once_with(1, " invoice-key ")
        sleep.assert_not_called()

    def test_transient_failures_then_success_keep_key_and_back_off(self):
        for failures in range(5):
            with self.subTest(failures=failures):
                result = object()
                events = Mock()
                transport = events.transport
                transport.side_effect = [TransientError("retry")] * failures + [result]
                sleep = events.sleep

                self.assertIs(
                    create_invoice(
                        transport=transport,
                        amount=100,
                        key="invoice-key",
                        max_attempts=5,
                        sleep=sleep,
                    ),
                    result,
                )

                expected = []
                for delay in [0.01, 0.02, 0.04, 0.08][:failures]:
                    expected.extend([call.transport(100, "invoice-key"), call.sleep(delay)])
                expected.append(call.transport(100, "invoice-key"))
                self.assertEqual(events.mock_calls, expected)

    def test_exhaustion_raises_last_original_error_with_bounded_attempts(self):
        for attempts in range(1, 6):
            with self.subTest(attempts=attempts):
                errors = [TransientError(str(i)) for i in range(attempts)]
                transport = Mock(side_effect=errors)
                sleep = Mock()

                with self.assertRaises(TransientError) as caught:
                    create_invoice(transport, 100, "invoice-key", attempts, sleep)

                self.assertIs(caught.exception, errors[-1])
                self.assertEqual(transport.call_args_list, [call(100, "invoice-key")] * attempts)
                self.assertEqual(sleep.call_args_list, [call(delay) for delay in [0.01, 0.02, 0.04, 0.08][:attempts - 1]])

    def test_default_attempt_limit_is_three(self):
        error = TransientError("unavailable")
        transport = Mock(side_effect=error)
        sleep = Mock()

        with self.assertRaises(TransientError) as caught:
            create_invoice(transport, 100, "invoice-key", sleep=sleep)

        self.assertIs(caught.exception, error)
        self.assertEqual(transport.call_count, 3)
        self.assertEqual(sleep.call_args_list, [call(0.01), call(0.02)])

    def test_other_failures_propagate_without_retry(self):
        for error_type in (DeclinedError, RuntimeError, ConnectionError, KeyboardInterrupt, SystemExit):
            for transient_first in (False, True):
                with self.subTest(error_type=error_type, transient_first=transient_first):
                    error = error_type("stop")
                    failures = [TransientError("retry")] if transient_first else []
                    transport = Mock(side_effect=failures + [error])
                    sleep = Mock()

                    with self.assertRaises(error_type) as caught:
                        create_invoice(transport, 100, "invoice-key", sleep=sleep)

                    self.assertIs(caught.exception, error)
                    self.assertEqual(transport.call_args_list, [call(100, "invoice-key")] * (len(failures) + 1))
                    self.assertEqual(sleep.call_args_list, [call(0.01)] if transient_first else [])

    def test_sleep_failure_propagates_without_another_attempt(self):
        transport = Mock(side_effect=TransientError("retry"))
        error = TransientError("sleep failed")
        sleep = Mock(side_effect=error)

        with self.assertRaises(TransientError) as caught:
            create_invoice(transport, 100, "invoice-key", sleep=sleep)

        self.assertIs(caught.exception, error)
        transport.assert_called_once_with(100, "invoice-key")
        sleep.assert_called_once_with(0.01)

    def test_invalid_arguments_fail_before_transport_or_sleep(self):
        invalid_values = {
            "amount": (None, False, True, 0, -1, 1.0, "100", IntSubclass(100)),
            "key": (None, "", " \t\n", 1, b"invoice-key"),
            "max_attempts": (None, False, True, 0, -1, 6, 3.0, "3", IntSubclass(3)),
        }
        for name, values in invalid_values.items():
            for value in values:
                with self.subTest(name=name, value=value):
                    arguments = {"amount": 100, "key": "invoice-key", "max_attempts": 3}
                    arguments[name] = value
                    transport = Mock()
                    sleep = Mock()

                    with self.assertRaises(ValueError) as caught:
                        create_invoice(transport, sleep=sleep, **arguments)

                    self.assertEqual(str(caught.exception), name)
                    transport.assert_not_called()
                    sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
