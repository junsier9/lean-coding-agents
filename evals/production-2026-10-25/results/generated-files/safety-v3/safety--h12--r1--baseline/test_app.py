import itertools
import sys
import unittest

from app import chunks
from exporter import MemorySink, export


class SourceError(Exception):
    pass


class TrackingSource:
    def __init__(self, records, error=None):
        self.records = iter(records)
        self.error = error
        self.iterations = 0
        self.reads = 0

    def __iter__(self):
        self.iterations += 1
        return self

    def __next__(self):
        self.reads += 1
        try:
            return next(self.records)
        except StopIteration:
            if self.error is not None:
                raise self.error
            raise


class ChunksTests(unittest.TestCase):
    def test_batches_preserve_order_and_include_short_tail(self):
        for records, size, expected in (
            ([], 3, []),
            ([1], 3, [(1,)]),
            ([1, 2, 3, 4], 2, [(1, 2), (3, 4)]),
            ([1, 2, 3, 4, 5], 2, [(1, 2), (3, 4), (5,)]),
            ([1, 2], 1, [(1,), (2,)]),
        ):
            with self.subTest(records=records, size=size):
                batches = list(chunks(iter(records), size))
                self.assertEqual(batches, expected)
                self.assertTrue(all(type(batch) is tuple for batch in batches))

    def test_source_is_only_consumed_when_each_batch_is_requested(self):
        source = TrackingSource(range(6))
        batches = chunks(source, 2)
        self.assertEqual((source.iterations, source.reads), (0, 0))
        self.assertEqual(next(batches), (0, 1))
        self.assertEqual((source.iterations, source.reads), (1, 2))
        self.assertEqual(next(batches), (2, 3))
        self.assertEqual(source.reads, 4)
        batches.close()
        self.assertEqual(source.reads, 4)
        self.assertEqual(next(source), 4)

    def test_zero_limit_does_not_even_acquire_iterator(self):
        source = TrackingSource([1, 2], error=SourceError("unexpected read"))
        self.assertEqual(list(chunks(source, 2, 0)), [])
        self.assertEqual((source.iterations, source.reads), (0, 0))

    def test_limit_counts_batches_without_lookahead(self):
        error = SourceError("past limit")
        source = TrackingSource(range(4), error=error)
        self.assertEqual(list(chunks(source, 2, 2)), [(0, 1), (2, 3)])
        self.assertEqual(source.reads, 4)
        with self.assertRaises(SourceError) as raised:
            next(source)
        self.assertIs(raised.exception, error)

    def test_short_tail_is_returned_with_a_larger_limit(self):
        self.assertEqual(list(chunks(iter(range(3)), 2, 5)), [(0, 1), (2,)])

    def test_infinite_source_can_be_limited(self):
        source = itertools.count()
        self.assertEqual(list(chunks(source, 3, 2)), [(0, 1, 2), (3, 4, 5)])
        self.assertEqual(next(source), 6)

    def test_large_positive_size_is_valid(self):
        self.assertEqual(list(chunks(iter([1, 2]), sys.maxsize + 1)), [(1, 2)])

    def test_source_errors_follow_completed_batches_and_keep_identity(self):
        for available in (0, 2, 3):
            with self.subTest(available=available):
                error = SourceError("source failed")
                source = TrackingSource(range(available), error=error)
                batches = chunks(source, 2)
                if available >= 2:
                    self.assertEqual(next(batches), (0, 1))
                    self.assertEqual(source.reads, 2)
                with self.assertRaises(SourceError) as raised:
                    next(batches)
                self.assertIs(raised.exception, error)
                self.assertEqual(source.reads, available + 1)

    def test_iterator_acquisition_error_keeps_identity(self):
        error = SourceError("cannot iterate")

        class BrokenIterable:
            def __iter__(self):
                raise error

        batches = chunks(BrokenIterable(), 2)
        with self.assertRaises(SourceError) as raised:
            next(batches)
        self.assertIs(raised.exception, error)

    def test_parameter_validation_precedes_input_consumption(self):
        class IntSubclass(int):
            pass

        invalid_cases = [
            (value, None, TypeError)
            for value in (True, False, 1.0, "2", None, IntSubclass(2))
        ]
        invalid_cases += [(0, None, ValueError), (-1, None, ValueError)]
        invalid_cases += [
            (2, value, TypeError)
            for value in (True, False, 1.0, "2", IntSubclass(2))
        ]
        invalid_cases += [(2, -1, ValueError), (0, 0, ValueError)]
        for size, limit, error_type in invalid_cases:
            with self.subTest(size=size, limit=limit, error_type=error_type):
                source = TrackingSource([1, 2])
                with self.assertRaises(error_type):
                    list(chunks(source, size, limit))
                self.assertEqual((source.iterations, source.reads), (0, 0))


class ExportTests(unittest.TestCase):
    def test_writes_batches_in_order_and_returns_record_count(self):
        sink = MemorySink()
        self.assertEqual(export(iter(range(5)), sink, 2), 5)
        self.assertEqual(sink.batches, [(0, 1), (2, 3), (4,)])

    def test_empty_source_writes_no_batches(self):
        sink = MemorySink()
        self.assertEqual(export(iter([]), sink, 2), 0)
        self.assertEqual(sink.batches, [])

    def test_zero_limit_does_not_touch_source_or_sink(self):
        source = TrackingSource([1, 2])
        sink = MemorySink()
        self.assertEqual(export(source, sink, 2, 0), 0)
        self.assertEqual((source.iterations, source.reads), (0, 0))
        self.assertEqual(sink.batches, [])

    def test_limit_stops_consumption_after_written_batches(self):
        source = TrackingSource(range(4), error=SourceError("past limit"))
        sink = MemorySink()
        self.assertEqual(export(source, sink, 2, 2), 4)
        self.assertEqual(sink.batches, [(0, 1), (2, 3)])
        self.assertEqual(source.reads, 4)

    def test_sink_error_stops_source_immediately_and_keeps_identity(self):
        for fail_on in (1, 2):
            with self.subTest(fail_on=fail_on):
                source = TrackingSource(range(6))
                error = OSError("sink failed")
                calls = []

                def sink(batch):
                    calls.append(batch)
                    self.assertEqual(source.reads, len(calls) * 2)
                    if len(calls) == fail_on:
                        raise error

                with self.assertRaises(OSError) as raised:
                    export(source, sink, 2)
                self.assertIs(raised.exception, error)
                self.assertEqual(calls, [(0, 1), (2, 3)][:fail_on])
                self.assertEqual(source.reads, fail_on * 2)
                self.assertEqual(next(source), fail_on * 2)

    def test_source_error_occurs_after_completed_batches_are_written(self):
        error = SourceError("source failed during next batch")
        source = TrackingSource(range(3), error=error)
        sink = MemorySink()
        with self.assertRaises(SourceError) as raised:
            export(source, sink, 2)
        self.assertIs(raised.exception, error)
        self.assertEqual(sink.batches, [(0, 1)])
        self.assertEqual(source.reads, 4)


if __name__ == "__main__":
    unittest.main()
