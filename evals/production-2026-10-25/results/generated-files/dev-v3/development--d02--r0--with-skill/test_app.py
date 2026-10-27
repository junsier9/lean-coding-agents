import unittest

from app import quote
from parser import QuoteError


class QuoteTests(unittest.TestCase):
    def test_valid_lists_are_quoted_and_audited_once(self):
        for mode in ("parsed", "raw"):
            for payload in ([], [0], [2, 0, 7], [10**100, 1]):
                with self.subTest(mode=mode, payload=payload):
                    original = payload.copy()
                    events = []
                    result = quote(mode, payload, lambda *event: events.append(event))
                    self.assertEqual(result, {"count": len(original), "total": sum(original)})
                    self.assertEqual(events, [("checked", len(original))])
                    self.assertEqual(payload, original)

    def test_audit_is_optional(self):
        for mode in ("parsed", "raw"):
            with self.subTest(mode=mode):
                self.assertEqual(quote(mode, [1, 2]), {"count": 2, "total": 3})

    def test_non_list_payloads_fail_without_audit(self):
        for mode in ("parsed", "raw"):
            for payload in (None, (), (1, 2), "12", {1: 2}, {1, 2}, 1, True):
                with self.subTest(mode=mode, payload=payload):
                    events = []
                    with self.assertRaises(QuoteError):
                        quote(mode, payload, lambda *event: events.append(event))
                    self.assertEqual(events, [])

    def test_invalid_elements_fail_without_audit_or_mutation(self):
        class IntSubclass(int):
            pass

        for mode in ("parsed", "raw"):
            for value in (-1, True, False, 1.0, "1", None, [], IntSubclass(1)):
                with self.subTest(mode=mode, value=value):
                    payload = [2, value, 3]
                    original = payload.copy()
                    events = []
                    with self.assertRaises(QuoteError):
                        quote(mode, payload, lambda *event: events.append(event))
                    self.assertEqual(events, [])
                    self.assertEqual(payload, original)

    def test_unknown_mode_fails_without_audit(self):
        events = []
        with self.assertRaisesRegex(ValueError, "unknown mode"):
            quote("unknown", [1], lambda *event: events.append(event))
        self.assertEqual(events, [])

    def test_audit_exception_propagates(self):
        for mode in ("parsed", "raw"):
            with self.subTest(mode=mode):
                events = []
                error = RuntimeError("audit failed")

                def audit(*event):
                    events.append(event)
                    raise error

                with self.assertRaises(RuntimeError) as raised:
                    quote(mode, [1, 2], audit)
                self.assertIs(raised.exception, error)
                self.assertEqual(events, [("checked", 2)])

    def test_audit_mutation_does_not_change_snapshot(self):
        for mode in ("parsed", "raw"):
            with self.subTest(mode=mode):
                payload = [2, 3]
                events = []

                def audit(*event):
                    events.append(event)
                    payload[:] = [False, -1]

                self.assertEqual(quote(mode, payload, audit), {"count": 2, "total": 5})
                self.assertEqual(events, [("checked", 2)])
                self.assertEqual(payload, [False, -1])


if __name__ == "__main__":
    unittest.main()
