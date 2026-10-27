import unittest

from app import summarize
from boundary import PayloadError


class IntSubclass(int):
    pass


class SummarizeTests(unittest.TestCase):
    def test_valid_batches_are_summarized_and_audited_once(self):
        cases = (
            ([], {"count": 0, "total": 0}),
            ([0], {"count": 1, "total": 0}),
            ([125, -50, 0], {"count": 3, "total": 75}),
            ([-125, -50], {"count": 2, "total": -175}),
        )
        for payload, expected in cases:
            with self.subTest(payload=payload):
                original = payload.copy()
                events = []

                result = summarize(payload, lambda *event: events.append(event))

                self.assertEqual(result, expected)
                self.assertEqual(events, [("validated", expected["count"])])
                self.assertEqual(payload, original)

    def test_audit_is_optional(self):
        self.assertEqual(summarize([200, -75]), {"count": 2, "total": 125})

    def test_non_lists_are_rejected_without_success_audit(self):
        for payload in (None, 1, "12", (1, 2), {1, 2}, {"amounts": [1, 2]}):
            with self.subTest(payload=payload):
                events = []

                with self.assertRaisesRegex(PayloadError, "^expected list$"):
                    summarize(payload, lambda *event: events.append(event))

                self.assertEqual(events, [])

    def test_non_exact_integers_are_rejected_without_success_audit(self):
        for invalid in (True, False, 1.0, "1", None, IntSubclass(1)):
            with self.subTest(invalid=invalid, type=type(invalid)):
                payload = [10, invalid]
                original = payload.copy()
                events = []

                with self.assertRaisesRegex(PayloadError, "^amounts must be integers$"):
                    summarize(payload, lambda *event: events.append(event))

                self.assertEqual(events, [])
                self.assertEqual(payload, original)

    def test_audit_mutation_does_not_change_validated_summary(self):
        payload = [125, -50, 0]
        events = []

        def audit(event, count):
            events.append((event, count))
            payload[:] = [False, "changed"]

        self.assertEqual(summarize(payload, audit), {"count": 3, "total": 75})
        self.assertEqual(events, [("validated", 3)])
        self.assertEqual(payload, [False, "changed"])

    def test_audit_exception_propagates_unchanged(self):
        failure = RuntimeError("audit unavailable")
        events = []

        def audit(event, count):
            events.append((event, count))
            raise failure

        with self.assertRaises(RuntimeError) as caught:
            summarize([125, -50], audit)

        self.assertIs(caught.exception, failure)
        self.assertEqual(events, [("validated", 2)])


if __name__ == "__main__":
    unittest.main()
