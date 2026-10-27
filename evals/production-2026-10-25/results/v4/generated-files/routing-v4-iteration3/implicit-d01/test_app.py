import unittest

from app import summarize
from boundary import PayloadError


class SummarizeTests(unittest.TestCase):
    def test_valid_batches_audit_once(self):
        for payload in ([], [0], [120, -45, 5]):
            with self.subTest(payload=payload):
                events = []
                result = summarize(payload, lambda *event: events.append(event))
                self.assertEqual(result, {"count": len(payload), "total": sum(payload)})
                self.assertEqual(events, [("validated", len(payload))])

    def test_requires_a_list(self):
        for payload in (None, 1, "12", (1, 2), {"amount": 1}, {1, 2}):
            with self.subTest(payload=payload):
                events = []
                with self.assertRaisesRegex(PayloadError, "^expected list$"):
                    summarize(payload, lambda *event: events.append(event))
                self.assertEqual(events, [])

    def test_requires_exact_integers_before_auditing(self):
        class IntSubclass(int):
            pass

        for value in (True, False, 1.0, "1", None, IntSubclass(1)):
            with self.subTest(value=value, value_type=type(value)):
                events = []
                with self.assertRaisesRegex(PayloadError, "^amounts must be integers$"):
                    summarize([10, value], lambda *event: events.append(event))
                self.assertEqual(events, [])

    def test_preserves_caller_input_without_audit(self):
        payload = [15, -3, 0]
        self.assertEqual(summarize(payload=payload), {"count": 3, "total": 12})
        self.assertEqual(payload, [15, -3, 0])

    def test_uses_snapshot_when_audit_mutates_input(self):
        payload = [15, -3, 0]
        events = []

        def audit(event, count):
            events.append((event, count))
            payload[:] = [False]

        self.assertEqual(summarize(payload=payload, audit=audit), {"count": 3, "total": 12})
        self.assertEqual(events, [("validated", 3)])
        self.assertEqual(payload, [False])

    def test_propagates_original_audit_exception(self):
        failure = RuntimeError("audit unavailable")
        events = []

        def audit(event, count):
            events.append((event, count))
            raise failure

        with self.assertRaises(RuntimeError) as caught:
            summarize([1, 2], audit)
        self.assertIs(caught.exception, failure)
        self.assertEqual(events, [("validated", 2)])


if __name__ == "__main__":
    unittest.main()
