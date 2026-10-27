import unittest
from unittest.mock import Mock, call

from app import create_invoice
from gateway import DeclinedError, TransientError


class CreateInvoiceTests(unittest.TestCase):
    def test_success_preserves_key_and_result_without_sleep(self):
        result = object()
        transport = Mock(return_value=result)
        sleep = Mock()

        self.assertIs(create_invoice(transport, 1, " invoice-key ", sleep=sleep), result)

        transport.assert_called_once_with(1, " invoice-key ")
        sleep.assert_not_called()

    def test_transient_failures_retry_same_request_with_backoff(self):
        result = object()
        transport = Mock(side_effect=[TransientError("first"), TransientError("second"), result])
        sleep = Mock()
        events = Mock()
        events.attach_mock(transport, "transport")
        events.attach_mock(sleep, "sleep")

        self.assertIs(create_invoice(transport, 123, "invoice-key", sleep=sleep), result)

        self.assertEqual(events.mock_calls, [
            call.transport(123, "invoice-key"),
            call.sleep(0.01),
            call.transport(123, "invoice-key"),
            call.sleep(0.02),
            call.transport(123, "invoice-key"),
        ])

    def test_exhaustion_raises_last_original_error(self):
        for attempts in (1, 2, 3, 4, 5):
            with self.subTest(max_attempts=attempts):
                errors = [TransientError(str(i)) for i in range(attempts)]
                transport = Mock(side_effect=errors)
                sleep = Mock()

                with self.assertRaises(TransientError) as raised:
                    create_invoice(transport, 123, "invoice-key", attempts, sleep)

                self.assertIs(raised.exception, errors[-1])
                self.assertEqual(transport.call_args_list, [call(123, "invoice-key")] * attempts)
                self.assertEqual(sleep.call_args_list, [
                    call(delay) for delay in (0.01, 0.02, 0.04, 0.08)[:attempts - 1]
                ])

    def test_non_transient_errors_propagate_without_retry(self):
        for error in (DeclinedError("declined"), RuntimeError("unexpected"),
                      ConnectionError("not transient"), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                transport = Mock(side_effect=error)
                sleep = Mock()

                with self.assertRaises(type(error)) as raised:
                    create_invoice(transport, 123, "invoice-key", sleep=sleep)

                self.assertIs(raised.exception, error)
                transport.assert_called_once_with(123, "invoice-key")
                sleep.assert_not_called()

    def test_non_transient_error_after_retry_stops_immediately(self):
        error = DeclinedError("declined")
        transport = Mock(side_effect=[TransientError("retry"), error])
        sleep = Mock()

        with self.assertRaises(DeclinedError) as raised:
            create_invoice(transport, 123, "invoice-key", sleep=sleep)

        self.assertIs(raised.exception, error)
        self.assertEqual(transport.call_args_list, [call(123, "invoice-key")] * 2)
        sleep.assert_called_once_with(0.01)

    def test_sleep_error_propagates_without_another_attempt(self):
        error = RuntimeError("sleep failed")
        transport = Mock(side_effect=TransientError("retry"))
        sleep = Mock(side_effect=error)

        with self.assertRaises(RuntimeError) as raised:
            create_invoice(transport, 123, "invoice-key", sleep=sleep)

        self.assertIs(raised.exception, error)
        transport.assert_called_once_with(123, "invoice-key")
        sleep.assert_called_once_with(0.01)

    def test_invalid_inputs_fail_before_transport_or_sleep(self):
        cases = [
            ("amount", amount, "invoice-key", 3)
            for amount in (0, -1, True, False, 1.0, "1", None)
        ] + [
            ("key", 123, key, 3)
            for key in ("", " ", "\t\n", None, 123, b"invoice-key")
        ] + [
            ("max_attempts", 123, "invoice-key", attempts)
            for attempts in (0, -1, 6, True, False, 1.0, "3", None)
        ]
        for field, amount, key, attempts in cases:
            with self.subTest(field=field, amount=amount, key=key, attempts=attempts):
                transport = Mock()
                sleep = Mock()

                with self.assertRaises(ValueError) as raised:
                    create_invoice(transport, amount, key, attempts, sleep)

                self.assertEqual(raised.exception.args, (field,))
                transport.assert_not_called()
                sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
