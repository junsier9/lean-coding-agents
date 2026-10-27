import unittest

from app import summarize
from boundary import PayloadError, validated_amounts


class IntegerSubclass(int):
    pass


class SummarizeTests(unittest.TestCase):
    def test_valid_batches_are_audited_once(self):
        for payload, expected in (
            ([], {"count": 0, "total": 0}),
            ([125], {"count": 1, "total": 125}),
            ([125, -30, 0], {"count": 3, "total": 95}),
        ):
            with self.subTest(payload=payload):
                events = []
                result = summarize(payload, lambda *event: events.append(event))
                self.assertEqual(result, expected)
                self.assertEqual(events, [("validated", len(payload))])

    def test_audit_is_optional(self):
        self.assertEqual(summarize([125, -30]), {"count": 2, "total": 95})

    def test_non_lists_are_rejected_without_success_audit(self):
        for payload in (None, (), (1, 2), "12", {"amount": 1}, iter([1])):
            with self.subTest(payload=payload):
                events = []
                with self.assertRaisesRegex(PayloadError, "^expected list$"):
                    summarize(payload, lambda *event: events.append(event))
                self.assertEqual(events, [])

    def test_non_integers_are_rejected_without_success_audit(self):
        for value in (True, False, 1.0, "1", None, IntegerSubclass(1)):
            with self.subTest(value=value):
                events = []
                payload = [10, value]
                with self.assertRaisesRegex(PayloadError, "^amounts must be integers$"):
                    summarize(payload, lambda *event: events.append(event))
                self.assertEqual(events, [])
                self.assertEqual(payload, [10, value])

    def test_caller_input_is_unchanged(self):
        payload = [125, -30, 0]
        summarize(payload)
        self.assertEqual(payload, [125, -30, 0])

    def test_audit_mutation_does_not_change_the_validated_snapshot(self):
        payload = [125, -30, 0]
        events = []

        def audit(*event):
            events.append(event)
            payload[:] = [False, "changed"]

        self.assertEqual(summarize(payload, audit), {"count": 3, "total": 95})
        self.assertEqual(events, [("validated", 3)])
        self.assertEqual(payload, [False, "changed"])

    def test_audit_exception_propagates_unchanged(self):
        error = RuntimeError("audit failed")
        events = []

        def audit(*event):
            events.append(event)
            raise error

        with self.assertRaises(RuntimeError) as caught:
            summarize([125], audit)
        self.assertIs(caught.exception, error)
        self.assertEqual(events, [("validated", 1)])

    def test_internal_tuple_producers_still_validate_and_audit(self):
        events = []
        self.assertEqual(
            validated_amounts((125, -30), lambda *event: events.append(event)),
            (125, -30),
        )
        self.assertEqual(events, [("validated", 2)])
        with self.assertRaises(PayloadError):
            validated_amounts((125, True), lambda *event: events.append(event))
        self.assertEqual(events, [("validated", 2)])


if __name__ == "__main__":
    unittest.main()
