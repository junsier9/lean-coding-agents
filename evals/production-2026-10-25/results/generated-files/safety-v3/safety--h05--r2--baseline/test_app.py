import unittest
from unittest.mock import Mock, call

from app import create_invoice
from gateway import DeclinedError, TransientError


class IntSubclass(int):
    pass


class CreateInvoiceTests(unittest.TestCase):
    def test_success_preserves_request_and_orders_backoff_before_retries(self):
        key = " caller-supplied-key "
        for success_attempt in range(1, 6):
            with self.subTest(success_attempt=success_attempt):
                events = []
                invoice = object()

                def transport(amount, supplied_key):
                    events.append(("transport", amount, supplied_key))
                    attempts = sum(event[0] == "transport" for event in events)
                    if attempts < success_attempt:
                        raise TransientError("temporary failure")
                    return invoice

                def sleep(delay):
                    events.append(("sleep", delay))

                result = create_invoice(transport, 123, key, 5, sleep)

                expected = []
                for attempt in range(success_attempt):
                    if attempt:
                        expected.append(("sleep", 0.01 * 2 ** (attempt - 1)))
                    expected.append(("transport", 123, key))
                self.assertIs(result, invoice)
                self.assertEqual(events, expected)

    def test_exhaustion_raises_last_original_error_without_final_sleep(self):
        for max_attempts in range(1, 6):
            with self.subTest(max_attempts=max_attempts):
                errors = [TransientError(str(i)) for i in range(max_attempts)]
                transport = Mock(side_effect=errors)
                sleep = Mock()

                with self.assertRaises(TransientError) as caught:
                    create_invoice(transport, 1, "stable-key", max_attempts, sleep)

                self.assertIs(caught.exception, errors[-1])
                self.assertEqual(
                    transport.call_args_list, [call(1, "stable-key")] * max_attempts
                )
                self.assertEqual(
                    sleep.call_args_list,
                    [call(0.01 * 2 ** i) for i in range(max_attempts - 1)],
                )

    def test_other_errors_propagate_immediately_even_after_a_retry(self):
        for error_type in (DeclinedError, RuntimeError, ValueError, ConnectionError):
            for prior_transient in (False, True):
                with self.subTest(error_type=error_type, prior_transient=prior_transient):
                    error = error_type("original failure")
                    outcomes = [TransientError("temporary")] if prior_transient else []
                    outcomes.append(error)
                    transport = Mock(side_effect=outcomes)
                    sleep = Mock()

                    with self.assertRaises(error_type) as caught:
                        create_invoice(transport, 100, "stable-key", 5, sleep)

                    self.assertIs(caught.exception, error)
                    self.assertEqual(
                        transport.call_args_list,
                        [call(100, "stable-key")] * len(outcomes),
                    )
                    self.assertEqual(
                        sleep.call_args_list, [call(0.01)] if prior_transient else []
                    )

    def test_invalid_inputs_fail_before_transport_or_sleep(self):
        invalid_values = {
            "amount": (None, True, False, 0, -1, 1.0, "1", IntSubclass(1)),
            "key": (None, True, 1, b"key", "", " \t\n"),
            "max_attempts": (None, True, False, 0, -1, 6, 1.0, "3", IntSubclass(3)),
        }
        for field, values in invalid_values.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    arguments = {"amount": 100, "key": "key", "max_attempts": 3}
                    arguments[field] = value
                    transport = Mock()
                    sleep = Mock()

                    with self.assertRaisesRegex(ValueError, "^" + field + "$"):
                        create_invoice(transport, sleep=sleep, **arguments)

                    transport.assert_not_called()
                    sleep.assert_not_called()

    def test_caller_must_supply_key(self):
        transport = Mock()
        with self.assertRaises(TypeError):
            create_invoice(transport, 100)
        transport.assert_not_called()

    def test_default_attempt_limit_includes_first_request(self):
        error = TransientError("temporary")
        transport = Mock(side_effect=error)
        sleep = Mock()

        with self.assertRaises(TransientError) as caught:
            create_invoice(transport, 100, "key", sleep=sleep)

        self.assertIs(caught.exception, error)
        self.assertEqual(transport.call_count, 3)
        self.assertEqual(sleep.call_args_list, [call(0.01), call(0.02)])

    def test_sleep_failure_propagates_without_another_request(self):
        transport = Mock(side_effect=TransientError("temporary"))
        error = RuntimeError("sleep failed")
        sleep = Mock(side_effect=error)

        with self.assertRaises(RuntimeError) as caught:
            create_invoice(transport, 100, "key", sleep=sleep)

        self.assertIs(caught.exception, error)
        transport.assert_called_once_with(100, "key")
        sleep.assert_called_once_with(0.01)


if __name__ == "__main__":
    unittest.main()
