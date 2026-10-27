import unittest
from unittest.mock import Mock, call

from app import create_invoice
from gateway import DeclinedError, TransientError


class IntSubclass(int):
    pass


class CreateInvoiceTests(unittest.TestCase):
    def test_success_preserves_arguments_and_result_without_waiting(self):
        result = object()
        transport = Mock(return_value=result)
        sleep = Mock()

        actual = create_invoice(transport, 125, " caller-key ", sleep=sleep)

        self.assertIs(actual, result)
        transport.assert_called_once_with(125, " caller-key ")
        sleep.assert_not_called()

    def test_transient_failures_retry_with_same_key_and_backoff(self):
        transport = Mock(side_effect=[TransientError("first"),
                                      TransientError("second"), "invoice"])
        sleep = Mock()

        result = create_invoice(transport, 125, "invoice-key", sleep=sleep)

        self.assertEqual(result, "invoice")
        self.assertEqual(transport.call_args_list, [call(125, "invoice-key")] * 3)
        self.assertEqual(sleep.call_args_list, [call(0.01), call(0.02)])

    def test_all_attempt_budgets_include_first_request_and_propagate_last_error(self):
        for budget in range(1, 6):
            with self.subTest(max_attempts=budget):
                errors = [TransientError(str(index)) for index in range(budget)]
                transport = Mock(side_effect=errors)
                sleep = Mock()

                with self.assertRaises(TransientError) as caught:
                    create_invoice(transport, 1, "key", budget, sleep)

                self.assertIs(caught.exception, errors[-1])
                self.assertEqual(transport.call_args_list, [call(1, "key")] * budget)
                self.assertEqual(sleep.call_args_list,
                                 [call(0.01 * 2 ** index) for index in range(budget - 1)])

    def test_fifth_attempt_can_succeed_without_extra_wait(self):
        transport = Mock(side_effect=[TransientError("retry")] * 4 + ["invoice"])
        sleep = Mock()

        result = create_invoice(transport, 1, "key", max_attempts=5, sleep=sleep)

        self.assertEqual(result, "invoice")
        self.assertEqual(transport.call_args_list, [call(1, "key")] * 5)
        self.assertEqual(sleep.call_args_list,
                         [call(0.01), call(0.02), call(0.04), call(0.08)])

    def test_non_transient_failures_propagate_immediately(self):
        for error in (DeclinedError("declined"), ConnectionError("connection"),
                      RuntimeError("unexpected"), ValueError("bad response"),
                      KeyboardInterrupt(), SystemExit()):
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
        transport = Mock(side_effect=[TransientError("retry"), error, "invoice"])
        sleep = Mock()

        with self.assertRaises(DeclinedError) as caught:
            create_invoice(transport, 1, "key", sleep=sleep)

        self.assertIs(caught.exception, error)
        self.assertEqual(transport.call_args_list, [call(1, "key")] * 2)
        sleep.assert_called_once_with(0.01)

    def test_sleep_failure_propagates_without_another_request(self):
        error = TransientError("sleep failed")
        transport = Mock(side_effect=TransientError("transport failed"))
        sleep = Mock(side_effect=error)

        with self.assertRaises(TransientError) as caught:
            create_invoice(transport, 1, "key", sleep=sleep)

        self.assertIs(caught.exception, error)
        transport.assert_called_once_with(1, "key")
        sleep.assert_called_once_with(0.01)

    def test_invalid_inputs_fail_before_transport_or_sleep(self):
        invalid_inputs = {
            "amount": [0, -1, True, False, 1.0, "1", None, IntSubclass(1)],
            "key": ["", "   ", "\t\n", None, 1, b"key"],
            "max_attempts": [0, -1, 6, True, False, 3.0, "3", None, IntSubclass(3)],
        }
        for name, values in invalid_inputs.items():
            for value in values:
                with self.subTest(argument=name, value=value):
                    arguments = {"amount": 1, "key": "key", "max_attempts": 3}
                    arguments[name] = value
                    transport = Mock()
                    sleep = Mock()

                    with self.assertRaisesRegex(ValueError, f"^{name}$"):
                        create_invoice(transport, sleep=sleep, **arguments)

                    transport.assert_not_called()
                    sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
