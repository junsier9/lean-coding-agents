import unittest
from unittest.mock import Mock

from app import summarize
from boundary import PayloadError


class SummarizeTests(unittest.TestCase):
    def test_valid_batches_are_unchanged_and_audited_once(self):
        for payload, expected in (
            ([], {"count": 0, "total": 0}),
            ([0], {"count": 1, "total": 0}),
            ([120, -20, 5], {"count": 3, "total": 105}),
        ):
            with self.subTest(payload=payload):
                original = payload.copy()
                audit = Mock()

                self.assertEqual(summarize(payload, audit), expected)

                self.assertEqual(payload, original)
                audit.assert_called_once_with("validated", len(original))

    def test_audit_is_optional(self):
        self.assertEqual(summarize([120, -20]), {"count": 2, "total": 100})

    def test_non_lists_are_rejected_without_audit(self):
        for payload in (None, 1, "1", {}, (), (1, 2), iter([1])):
            with self.subTest(payload=payload):
                audit = Mock()

                with self.assertRaisesRegex(PayloadError, "^expected list$"):
                    summarize(payload, audit)

                audit.assert_not_called()

    def test_only_exact_int_elements_are_accepted(self):
        class IntSubclass(int):
            pass

        for value in (True, False, 1.0, "1", None, IntSubclass(1)):
            with self.subTest(value=value, value_type=type(value)):
                audit = Mock()

                with self.assertRaisesRegex(PayloadError, "^amounts must be integers$"):
                    summarize([1, value], audit)

                audit.assert_not_called()

    def test_audit_exception_propagates_unchanged(self):
        failure = RuntimeError("audit failed")
        audit = Mock(side_effect=failure)

        with self.assertRaises(RuntimeError) as raised:
            summarize([1, 2], audit)

        self.assertIs(raised.exception, failure)
        audit.assert_called_once_with("validated", 2)

    def test_audit_mutation_does_not_change_validated_snapshot(self):
        payload = [120, -20, 5]

        def mutate_payload(event, count):
            payload[:] = [True, "changed"]

        audit = Mock(side_effect=mutate_payload)

        self.assertEqual(summarize(payload, audit), {"count": 3, "total": 105})
        audit.assert_called_once_with("validated", 3)
        self.assertEqual(payload, [True, "changed"])


if __name__ == "__main__":
    unittest.main()
