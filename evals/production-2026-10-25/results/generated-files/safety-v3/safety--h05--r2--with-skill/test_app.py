import unittest
from unittest.mock import Mock, call

from app import create_invoice
from gateway import DeclinedError, TransientError


class CreateInvoiceTests(unittest.TestCase):
    def test_success_returns_result_without_sleep(self):
        result = object()
        transport = Mock(return_value=result)
        sleep = Mock()

        self.assertIs(create_invoice(transport, 125, " invoice-1 ", sleep=sleep), result)

        transport.assert_called_once_with(125, " invoice-1 ")
        sleep.assert_not_called()

    def test_transient_failures_retry_the_same_request(self):
        result = object()
        transport = Mock(side_effect=[TransientError("first"), TransientError("second"), result])
        sleep = Mock()

        self.assertIs(create_invoice(transport, 125, "invoice-1", sleep=sleep), result)

        self.assertEqual(transport.call_args_list, [call(125, "invoice-1")] * 3)
        self.assertEqual(sleep.call_args_list, [call(0.01), call(0.02)])

    def test_exhaustion_propagates_last_error_with_bounded_backoff(self):
        for attempts in (1, 3, 5):
            with self.subTest(attempts=attempts):
                errors = [TransientError(str(index)) for index in range(attempts)]
                transport = Mock(side_effect=errors)
                sleep = Mock()

                with self.assertRaises(TransientError) as caught:
                    create_invoice(transport, 1, "invoice-1", attempts, sleep)

                self.assertIs(caught.exception, errors[-1])
                self.assertEqual(transport.call_args_list, [call(1, "invoice-1")] * attempts)
                self.assertEqual(
                    sleep.call_args_list,
                    [call(delay) for delay in (0.01, 0.02, 0.04, 0.08)[:attempts - 1]],
                )

    def test_other_failures_propagate_without_retry(self):
        for error in (DeclinedError("declined"), ConnectionError("connection"),
                      RuntimeError("unexpected"), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                transport = Mock(side_effect=error)
                sleep = Mock()

                with self.assertRaises(type(error)) as caught:
                    create_invoice(transport, 1, "invoice-1", sleep=sleep)

                self.assertIs(caught.exception, error)
                transport.assert_called_once_with(1, "invoice-1")
                sleep.assert_not_called()

    def test_decline_after_transient_failure_stops_retrying(self):
        declined = DeclinedError("declined")
        transport = Mock(side_effect=[TransientError("temporary"), declined])
        sleep = Mock()

        with self.assertRaises(DeclinedError) as caught:
            create_invoice(transport, 1, "invoice-1", sleep=sleep)

        self.assertIs(caught.exception, declined)
        self.assertEqual(transport.call_args_list, [call(1, "invoice-1")] * 2)
        sleep.assert_called_once_with(0.01)

    def test_sleep_occurs_between_attempts_only(self):
        events = []

        def transport(amount, key):
            events.append(("request", amount, key))
            if len(events) == 1:
                raise TransientError("temporary")
            return "invoice"

        result = create_invoice(
            transport, 1, "invoice-1", sleep=lambda delay: events.append(("sleep", delay))
        )

        self.assertEqual(result, "invoice")
        self.assertEqual(events, [
            ("request", 1, "invoice-1"), ("sleep", 0.01), ("request", 1, "invoice-1")
        ])

    def test_sleep_failure_propagates_without_another_request(self):
        error = TransientError("sleep failed")
        transport = Mock(side_effect=TransientError("temporary"))
        sleep = Mock(side_effect=error)

        with self.assertRaises(TransientError) as caught:
            create_invoice(transport, 1, "invoice-1", sleep=sleep)

        self.assertIs(caught.exception, error)
        transport.assert_called_once_with(1, "invoice-1")
        sleep.assert_called_once_with(0.01)

    def test_invalid_arguments_fail_before_transport_or_sleep(self):
        class IntSubclass(int):
            pass

        invalid_values = {
            "amount": (0, -1, True, False, 1.0, "1", None, IntSubclass(1)),
            "key": ("", " \t\n", 1, None, b"invoice-1"),
            "max_attempts": (0, -1, 6, True, False, 3.0, "3", None, IntSubclass(3)),
        }
        for name, values in invalid_values.items():
            for value in values:
                with self.subTest(name=name, value=value, value_type=type(value).__name__):
                    arguments = {"amount": 1, "key": "invoice-1", "max_attempts": 3}
                    arguments[name] = value
                    transport = Mock()
                    sleep = Mock()

                    with self.assertRaisesRegex(ValueError, f"^{name}$"):
                        create_invoice(transport, sleep=sleep, **arguments)

                    transport.assert_not_called()
                    sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
