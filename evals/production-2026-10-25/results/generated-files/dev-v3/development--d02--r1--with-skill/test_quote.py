import unittest

from app import quote
from parser import QuoteError


class QuoteTests(unittest.TestCase):
    def test_valid_requests_audit_once_and_preserve_payload(self):
        for mode in ("parsed", "raw"):
            for payload in ([], [0], [3, 0, 7]):
                with self.subTest(mode=mode, payload=payload):
                    original = payload.copy()
                    events = []
                    result = quote(mode, payload, lambda *event: events.append(event))
                    self.assertEqual(
                        result, {"count": len(original), "total": sum(original)}
                    )
                    self.assertEqual(events, [("checked", len(original))])
                    self.assertEqual(payload, original)

    def test_valid_requests_without_audit(self):
        for mode in ("parsed", "raw"):
            with self.subTest(mode=mode):
                self.assertEqual(quote(mode, [2, 4]), {"count": 2, "total": 6})

    def test_non_list_payloads_fail_without_success_audit(self):
        for mode in ("parsed", "raw"):
            for payload in (None, True, 1, "1", (1,), {1}, {"value": 1}):
                with self.subTest(mode=mode, payload=payload):
                    events = []
                    with self.assertRaisesRegex(QuoteError, "^expected list$"):
                        quote(mode, payload, lambda *event: events.append(event))
                    self.assertEqual(events, [])

    def test_invalid_elements_fail_without_success_audit(self):
        class IntSubclass(int):
            pass

        for mode in ("parsed", "raw"):
            for value in (-1, True, False, 1.0, "1", None, [], IntSubclass(1)):
                with self.subTest(mode=mode, value=value):
                    payload = [0, value]
                    original = payload.copy()
                    events = []
                    with self.assertRaisesRegex(
                        QuoteError, "^expected nonnegative integers$"
                    ):
                        quote(mode, payload, lambda *event: events.append(event))
                    self.assertEqual(events, [])
                    self.assertEqual(payload, original)

    def test_callback_errors_propagate(self):
        for mode in ("parsed", "raw"):
            with self.subTest(mode=mode):
                error = RuntimeError("audit unavailable")
                events = []

                def audit(*event):
                    events.append(event)
                    raise error

                with self.assertRaises(RuntimeError) as caught:
                    quote(mode, [1, 2], audit)
                self.assertIs(caught.exception, error)
                self.assertEqual(events, [("checked", 2)])

    def test_callback_mutation_does_not_change_validated_snapshot(self):
        for mode in ("parsed", "raw"):
            with self.subTest(mode=mode):
                payload = [1, 2]
                events = []

                def audit(*event):
                    events.append(event)
                    payload[:] = [False]

                self.assertEqual(
                    quote(mode, payload, audit), {"count": 2, "total": 3}
                )
                self.assertEqual(events, [("checked", 2)])

    def test_unknown_mode_fails_without_audit(self):
        events = []
        with self.assertRaisesRegex(ValueError, "^unknown mode$") as caught:
            quote("unknown", None, lambda *event: events.append(event))
        self.assertIs(type(caught.exception), ValueError)
        self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
