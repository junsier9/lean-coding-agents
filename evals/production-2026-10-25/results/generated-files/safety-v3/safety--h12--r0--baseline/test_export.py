import unittest

from app import chunks
from exporter import MemorySink, export


class TrackedSource:
    def __init__(self, records, *, error=None, max_reads=None):
        self.records = iter(records)
        self.error = error
        self.max_reads = max_reads
        self.iterations = 0
        self.reads = 0

    def __iter__(self):
        self.iterations += 1
        return self

    def __next__(self):
        self.reads += 1
        if self.max_reads is not None and self.reads > self.max_reads:
            raise AssertionError("source read beyond the requested batch")
        try:
            return next(self.records)
        except StopIteration:
            if self.error is not None:
                raise self.error
            raise


class ChunksTests(unittest.TestCase):
    def test_order_tuple_batches_and_incomplete_tail(self):
        for records, size, expected in [
            ([], 2, []),
            ([1], 2, [(1,)]),
            ([1, 2, 3, 4], 2, [(1, 2), (3, 4)]),
            ([1, 2, 3, 4, 5], 2, [(1, 2), (3, 4), (5,)]),
            ([1, 2], 1, [(1,), (2,)]),
            ([1, 2], 10**100, [(1, 2)]),
        ]:
            with self.subTest(records=records, size=size):
                batches = list(chunks(iter(records), size))
                self.assertEqual(batches, expected)
                self.assertTrue(all(type(batch) is tuple for batch in batches))

    def test_creation_and_each_yield_are_lazy(self):
        source = TrackedSource(range(6), max_reads=4)
        batches = chunks(source, 2)
        self.assertEqual((source.iterations, source.reads), (0, 0))
        self.assertEqual(next(batches), (0, 1))
        self.assertEqual((source.iterations, source.reads), (1, 2))
        self.assertEqual(next(batches), (2, 3))
        self.assertEqual(source.reads, 4)
        batches.close()
        self.assertEqual(source.reads, 4)

    def test_zero_limit_does_not_even_request_an_iterator(self):
        class UntouchedSource:
            def __iter__(self):
                raise AssertionError("zero limit touched the source")

        self.assertEqual(list(chunks(UntouchedSource(), 2, 0)), [])

    def test_limit_counts_batches_without_lookahead(self):
        source = TrackedSource(range(10), max_reads=6)
        self.assertEqual(list(chunks(source, 2, 3)), [(0, 1), (2, 3), (4, 5)])
        self.assertEqual(source.reads, 6)

    def test_limit_allows_a_short_final_batch(self):
        source = TrackedSource([1, 2, 3], max_reads=4)
        self.assertEqual(list(chunks(source, 2, 5)), [(1, 2), (3,)])
        self.assertEqual(source.reads, 4)

    def test_strict_argument_validation_precedes_source_consumption(self):
        class IntSubclass(int):
            pass

        cases = [
            (value, 0, TypeError)
            for value in (True, False, None, 2.0, "2", IntSubclass(2))
        ] + [
            (value, 0, ValueError) for value in (0, -1)
        ] + [
            (2, value, TypeError)
            for value in (True, False, 2.0, "2", IntSubclass(2))
        ] + [(2, -1, ValueError)]
        for size, limit, error_type in cases:
            with self.subTest(size=size, limit=limit):
                source = TrackedSource([1, 2], max_reads=0)
                with self.assertRaises(error_type):
                    list(chunks(source, size, limit))
                self.assertEqual((source.iterations, source.reads), (0, 0))

    def test_source_error_propagates_after_completed_batches(self):
        for records in ([], [1], [1, 2], [1, 2, 3]):
            with self.subTest(records=records):
                error = RuntimeError("source failed")
                batches = chunks(TrackedSource(records, error=error), 2)
                if len(records) >= 2:
                    self.assertEqual(next(batches), (1, 2))
                with self.assertRaises(RuntimeError) as caught:
                    next(batches)
                self.assertIs(caught.exception, error)

    def test_limit_does_not_encounter_a_later_source_error(self):
        source = TrackedSource([1, 2], error=RuntimeError("too far"))
        self.assertEqual(list(chunks(source, 2, 1)), [(1, 2)])
        self.assertEqual(source.reads, 2)


class ExportTests(unittest.TestCase):
    def test_writes_in_order_and_returns_record_count(self):
        for limit, expected in [
            (None, [(0, 1), (2, 3), (4,)]),
            (2, [(0, 1), (2, 3)]),
            (0, []),
        ]:
            with self.subTest(limit=limit):
                sink = MemorySink()
                written = export(iter(range(5)), sink, 2, limit)
                self.assertEqual(sink.batches, expected)
                self.assertEqual(written, sum(map(len, expected)))

    def test_sink_receives_each_batch_before_more_input_is_consumed(self):
        source = TrackedSource(range(5))
        observations = []

        def sink(batch):
            observations.append((batch, source.reads))

        self.assertEqual(export(source, sink, 2), 5)
        self.assertEqual(observations, [((0, 1), 2), ((2, 3), 4), ((4,), 6)])

    def test_sink_failure_propagates_and_stops_consumption(self):
        for failing_batch in (1, 2):
            with self.subTest(failing_batch=failing_batch):
                source = TrackedSource(range(10), max_reads=2 * failing_batch)
                error = OSError("sink failed")
                received = []

                def sink(batch):
                    received.append(batch)
                    if len(received) == failing_batch:
                        raise error

                with self.assertRaises(OSError) as caught:
                    export(source, sink, 2)
                self.assertIs(caught.exception, error)
                self.assertEqual(len(received), failing_batch)
                self.assertEqual(source.reads, 2 * failing_batch)

    def test_source_failure_preserves_completed_sink_writes(self):
        error = RuntimeError("source failed")
        source = TrackedSource([1, 2, 3], error=error)
        sink = MemorySink()
        with self.assertRaises(RuntimeError) as caught:
            export(source, sink, 2)
        self.assertIs(caught.exception, error)
        self.assertEqual(sink.batches, [(1, 2)])


if __name__ == "__main__":
    unittest.main()
