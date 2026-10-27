import unittest

from app import summarize
from boundary import PayloadError


class IntegerSubclass(int):
    pass


class SummarizeTests(unittest.TestCase):
    def test_signed_amounts_are_audited_once_without_changing_input(self):
        payload = [120, -35, 0, 5]
        events = []

        result = summarize(payload, lambda *event: events.append(event))

        self.assertEqual(result, {"count": 4, "total": 90})
        self.assertEqual(events, [("validated", 4)])
        self.assertEqual(payload, [120, -35, 0, 5])

    def test_empty_batch_is_audited_once(self):
        events = []

        result = summarize([], lambda *event: events.append(event))

        self.assertEqual(result, {"count": 0, "total": 0})
        self.assertEqual(events, [("validated", 0)])

    def test_audit_is_optional(self):
        self.assertEqual(summarize([10, -3]), {"count": 2, "total": 7})

    def test_non_lists_are_rejected_without_audit(self):
        for payload in (None, 1, True, "12", (1, 2), {1, 2}, {"amount": 1}):
            with self.subTest(payload=payload):
                events = []
                with self.assertRaisesRegex(PayloadError, "^expected list$"):
                    summarize(payload, lambda *event: events.append(event))
                self.assertEqual(events, [])

    def test_non_integer_elements_are_rejected_without_audit(self):
        for value in (True, False, 1.0, "1", None, IntegerSubclass(1)):
            with self.subTest(value=value, value_type=type(value)):
                payload = [2, value]
                events = []
                with self.assertRaisesRegex(PayloadError, "^amounts must be integers$"):
                    summarize(payload, lambda *event: events.append(event))
                self.assertEqual(events, [])
                self.assertEqual(payload, [2, value])

    def test_audit_mutation_does_not_change_validated_snapshot(self):
        payload = [10, -2, 0]
        events = []

        def audit(*event):
            events.append(event)
            payload[:] = [True, "changed"]

        result = summarize(payload, audit)

        self.assertEqual(result, {"count": 3, "total": 8})
        self.assertEqual(events, [("validated", 3)])
        self.assertEqual(payload, [True, "changed"])

    def test_audit_exception_propagates_unchanged(self):
        failure = RuntimeError("audit failed")
        events = []

        def audit(*event):
            events.append(event)
            raise failure

        with self.assertRaises(RuntimeError) as caught:
            summarize([1], audit)

        self.assertIs(caught.exception, failure)
        self.assertEqual(events, [("validated", 1)])


if __name__ == "__main__":
    unittest.main()
