import unittest

from app import chunks
from exporter import MemorySink, export


class ChunksTests(unittest.TestCase):
    def test_batches_preserve_order_and_include_partial_tail(self):
        for records, size, expected in (
            ([], 2, []),
            ([1, 2, 3, 4], 2, [(1, 2), (3, 4)]),
            ([1, 2, 3, 4, 5], 2, [(1, 2), (3, 4), (5,)]),
            ([1, 2], 1, [(1,), (2,)]),
            ([1, 2], 10**100, [(1, 2)]),
        ):
            with self.subTest(records=records, size=size):
                batches = list(chunks(iter(records), size))
                self.assertEqual(batches, expected)
                self.assertTrue(all(type(batch) is tuple for batch in batches))

    def test_source_is_consumed_only_when_a_batch_is_requested(self):
        consumed = []

        def source():
            for value in range(5):
                consumed.append(value)
                yield value

        batches = chunks(source(), 2)
        self.assertEqual(consumed, [])
        self.assertEqual(next(batches), (0, 1))
        self.assertEqual(consumed, [0, 1])
        self.assertEqual(next(batches), (2, 3))
        self.assertEqual(consumed, [0, 1, 2, 3])
        batches.close()
        self.assertEqual(consumed, [0, 1, 2, 3])

    def test_zero_limit_does_not_access_source(self):
        class UntouchedSource:
            def __iter__(self):
                raise AssertionError("source must not be accessed")

        self.assertEqual(list(chunks(UntouchedSource(), 2, limit=0)), [])

    def test_limit_counts_batches_without_reading_ahead(self):
        def source():
            yield from range(4)
            raise AssertionError("read beyond batch limit")

        self.assertEqual(list(chunks(source(), 2, limit=2)), [(0, 1), (2, 3)])

    def test_limited_source_retains_unconsumed_records(self):
        source = iter(range(5))
        self.assertEqual(list(chunks(source, 2, limit=1)), [(0, 1)])
        self.assertEqual(list(source), [2, 3, 4])

    def test_source_errors_follow_completed_batches(self):
        for partial_tail in ([], [2]):
            with self.subTest(partial_tail=partial_tail):
                failure = RuntimeError("source failed")

                def source():
                    yield 0
                    yield 1
                    yield from partial_tail
                    raise failure

                batches = chunks(source(), 2)
                self.assertEqual(next(batches), (0, 1))
                with self.assertRaises(RuntimeError) as caught:
                    next(batches)
                self.assertIs(caught.exception, failure)

    def test_invalid_parameters_are_rejected_before_source_access(self):
        class IntegerSubclass(int):
            pass

        class UntouchedSource:
            def __iter__(self):
                raise AssertionError("invalid arguments must be rejected first")

        invalid_types = (True, False, 1.5, "2", IntegerSubclass(2), object())
        cases = [(value, 0, TypeError, "size") for value in invalid_types]
        cases.append((None, 0, TypeError, "size"))
        cases.extend((value, 0, ValueError, "size") for value in (0, -1))
        cases.extend((2, value, TypeError, "limit") for value in invalid_types)
        cases.append((2, -1, ValueError, "limit"))
        for size, limit, error_type, parameter in cases:
            with self.subTest(size=size, limit=limit):
                with self.assertRaises(error_type) as caught:
                    list(chunks(UntouchedSource(), size, limit))
                self.assertEqual(caught.exception.args, (parameter,))


class ExportTests(unittest.TestCase):
    def test_export_writes_in_order_and_returns_record_count(self):
        for limit, expected in (
            (None, [(0, 1), (2, 3), (4,)]),
            (1, [(0, 1)]),
            (0, []),
        ):
            with self.subTest(limit=limit):
                sink = MemorySink()
                written = export(iter(range(5)), sink, 2, limit)
                self.assertEqual(sink.batches, expected)
                self.assertEqual(written, sum(map(len, expected)))

    def test_empty_export_does_not_call_sink(self):
        sink = MemorySink()
        self.assertEqual(export(iter(()), sink, 2), 0)
        self.assertEqual(sink.batches, [])

    def test_source_reads_and_sink_writes_are_interleaved(self):
        events = []

        def source():
            for value in range(3):
                events.append(("read", value))
                yield value

        def sink(batch):
            events.append(("write", batch))

        self.assertEqual(export(source(), sink, 2), 3)
        self.assertEqual(events, [
            ("read", 0), ("read", 1), ("write", (0, 1)),
            ("read", 2), ("write", (2,)),
        ])

    def test_sink_error_propagates_without_reading_another_batch(self):
        source = iter(range(10))
        writes = []
        failure = OSError("sink failed")

        def sink(batch):
            writes.append(batch)
            if len(writes) == 2:
                raise failure

        with self.assertRaises(OSError) as caught:
            export(source, sink, 2)
        self.assertIs(caught.exception, failure)
        self.assertEqual(writes, [(0, 1), (2, 3)])
        self.assertEqual(next(source), 4)

    def test_source_error_preserves_completed_writes(self):
        failure = RuntimeError("source failed")

        def source():
            yield from range(3)
            raise failure

        sink = MemorySink()
        with self.assertRaises(RuntimeError) as caught:
            export(source(), sink, 2)
        self.assertIs(caught.exception, failure)
        self.assertEqual(sink.batches, [(0, 1)])


if __name__ == "__main__":
    unittest.main()
