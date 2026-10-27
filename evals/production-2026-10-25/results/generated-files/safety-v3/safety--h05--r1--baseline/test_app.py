import unittest
from unittest.mock import Mock, call

from app import create_invoice
from gateway import DeclinedError, TransientError


class CreateInvoiceTests(unittest.TestCase):
    def test_first_attempt_success_preserves_request_and_result(self):
        result = object()
        transport = Mock(return_value=result)
        sleep = Mock()

        actual = create_invoice(transport, 125, "  caller-key  ", sleep=sleep)

        self.assertIs(actual, result)
        transport.assert_called_once_with(125, "  caller-key  ")
        sleep.assert_not_called()

    def test_transient_errors_retry_same_request_in_order(self):
        result = object()
        transport = Mock(side_effect=[TransientError("first"),
                                      TransientError("second"), result])
        sleep = Mock()
        events = Mock()
        events.attach_mock(transport, "transport")
        events.attach_mock(sleep, "sleep")

        self.assertIs(create_invoice(transport, 125, "caller-key", sleep=sleep),
                      result)

        self.assertEqual(events.mock_calls, [
            call.transport(125, "caller-key"),
            call.sleep(0.01),
            call.transport(125, "caller-key"),
            call.sleep(0.02),
            call.transport(125, "caller-key"),
        ])

    def test_attempt_limit_and_last_error_identity(self):
        for max_attempts in range(1, 6):
            with self.subTest(max_attempts=max_attempts):
                errors = [TransientError(str(i)) for i in range(max_attempts)]
                transport = Mock(side_effect=errors)
                sleep = Mock()

                with self.assertRaises(TransientError) as raised:
                    create_invoice(transport, 1, "key", max_attempts, sleep)

                self.assertIs(raised.exception, errors[-1])
                self.assertEqual(transport.call_args_list,
                                 [call(1, "key")] * max_attempts)
                self.assertEqual(sleep.call_args_list, [
                    call(delay) for delay in (0.01, 0.02, 0.04, 0.08)[:max_attempts - 1]
                ])

    def test_nontransient_errors_propagate_without_retry(self):
        for error in (DeclinedError("declined"), RuntimeError("unexpected"),
                      ConnectionError("not transient"), ValueError("invalid")):
            with self.subTest(error=type(error).__name__):
                transport = Mock(side_effect=error)
                sleep = Mock()

                with self.assertRaises(type(error)) as raised:
                    create_invoice(transport, 1, "key", sleep=sleep)

                self.assertIs(raised.exception, error)
                transport.assert_called_once_with(1, "key")
                sleep.assert_not_called()

    def test_nontransient_error_after_retry_stops_immediately(self):
        error = DeclinedError("declined after transient failure")
        transport = Mock(side_effect=[TransientError("retry"), error])
        sleep = Mock()

        with self.assertRaises(DeclinedError) as raised:
            create_invoice(transport, 1, "key", sleep=sleep)

        self.assertIs(raised.exception, error)
        self.assertEqual(transport.call_args_list, [call(1, "key")] * 2)
        sleep.assert_called_once_with(0.01)

    def test_sleep_error_propagates_without_another_attempt(self):
        error = RuntimeError("sleep failed")
        transport = Mock(side_effect=TransientError("retry"))
        sleep = Mock(side_effect=error)

        with self.assertRaises(RuntimeError) as raised:
            create_invoice(transport, 1, "key", sleep=sleep)

        self.assertIs(raised.exception, error)
        transport.assert_called_once_with(1, "key")
        sleep.assert_called_once_with(0.01)

    def test_invalid_parameters_have_no_side_effects(self):
        class IntSubclass(int):
            pass

        invalid_parameters = [
            ("amount", {"amount": value})
            for value in (0, -1, True, False, 1.0, "1", None, IntSubclass(1))
        ] + [
            ("key", {"key": value})
            for value in ("", " ", "\t\n", None, 1, b"key", True)
        ] + [
            ("max_attempts", {"max_attempts": value})
            for value in (0, -1, 6, True, False, 1.0, "3", None, IntSubclass(3))
        ]
        for field, override in invalid_parameters:
            with self.subTest(field=field, value=repr(override[field])):
                transport = Mock()
                sleep = Mock()
                parameters = {"amount": 1, "key": "key", "max_attempts": 3}
                parameters.update(override)

                with self.assertRaisesRegex(ValueError, "^" + field + "$"):
                    create_invoice(transport, sleep=sleep, **parameters)

                transport.assert_not_called()
                sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
