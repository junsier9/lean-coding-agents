import unittest

from app import chunks
from exporter import MemorySink, export


class TrackedSource:
    def __init__(self, records, error=None):
        self.records = iter(records)
        self.error = error
        self.iter_calls = 0
        self.next_calls = 0
        self.consumed = []

    def __iter__(self):
        self.iter_calls += 1
        return self

    def __next__(self):
        self.next_calls += 1
        try:
            record = next(self.records)
        except StopIteration:
            if self.error is not None:
                raise self.error
            raise
        self.consumed.append(record)
        return record


class ChunksTests(unittest.TestCase):
    def test_batches_preserve_order_identity_and_tail(self):
        records = [object() for _ in range(5)]
        batches = list(chunks(iter(records), 2))
        self.assertEqual(batches, [tuple(records[:2]), tuple(records[2:4]),
                                   tuple(records[4:])])
        self.assertTrue(all(type(batch) is tuple for batch in batches))
        self.assertTrue(all(actual is expected
                            for actual, expected in zip(
                                [item for batch in batches for item in batch],
                                records)))

    def test_empty_input_has_no_batches(self):
        self.assertEqual(list(chunks([], 3)), [])

    def test_exact_multiple_has_no_empty_batch(self):
        self.assertEqual(list(chunks(range(4), 2)), [(0, 1), (2, 3)])

    def test_size_one(self):
        self.assertEqual(list(chunks(range(3), 1)), [(0,), (1,), (2,)])

    def test_large_positive_integer_size(self):
        self.assertEqual(list(chunks([1, 2], 10 ** 100)), [(1, 2)])

    def test_construction_and_each_batch_are_lazy(self):
        source = TrackedSource(range(10))
        batches = chunks(source, 2)
        self.assertEqual(source.iter_calls, 0)
        self.assertEqual(source.next_calls, 0)
        self.assertEqual(next(batches), (0, 1))
        self.assertEqual(source.iter_calls, 1)
        self.assertEqual(source.next_calls, 2)
        self.assertEqual(next(batches), (2, 3))
        self.assertEqual(source.next_calls, 4)
        batches.close()
        self.assertEqual(source.next_calls, 4)

    def test_zero_limit_does_not_touch_source(self):
        source = TrackedSource([], error=RuntimeError("must not read"))
        self.assertEqual(list(chunks(source, 2, 0)), [])
        self.assertEqual(source.iter_calls, 0)
        self.assertEqual(source.next_calls, 0)

    def test_limit_counts_batches_without_probe_or_read_ahead(self):
        source = TrackedSource(range(4), error=RuntimeError("past limit"))
        self.assertEqual(list(chunks(source, 2, 2)), [(0, 1), (2, 3)])
        self.assertEqual(source.next_calls, 4)
        self.assertEqual(source.consumed, [0, 1, 2, 3])

    def test_limit_allows_a_short_final_batch(self):
        self.assertEqual(list(chunks(range(3), 2, 2)), [(0, 1), (2,)])
        self.assertEqual(list(chunks(range(3), 2, 10)), [(0, 1), (2,)])

    def test_finite_limit_on_infinite_source(self):
        def records():
            value = 0
            while True:
                yield value
                value += 1

        self.assertEqual(list(chunks(records(), 2, 3)),
                         [(0, 1), (2, 3), (4, 5)])

    def test_invalid_parameters_fail_without_touching_source(self):
        class IntSubclass(int):
            pass

        cases = [
            (size, None, TypeError)
            for size in (None, True, False, 1.0, "2", IntSubclass(2))
        ] + [
            (size, None, ValueError) for size in (0, -1)
        ] + [
            (1, limit, TypeError)
            for limit in (True, False, 1.0, "2", IntSubclass(2))
        ] + [
            (1, -1, ValueError), (0, 0, ValueError)
        ]
        for size, limit, expected in cases:
            with self.subTest(size=size, limit=limit):
                source = TrackedSource([1])
                with self.assertRaises(expected):
                    list(chunks(source, size, limit))
                self.assertEqual(source.iter_calls, 0)
                self.assertEqual(source.next_calls, 0)

    def test_source_errors_follow_complete_batches_and_keep_identity(self):
        for length in (0, 1, 4, 5):
            with self.subTest(length=length):
                error = RuntimeError("source failed")
                source = TrackedSource(range(length), error=error)
                batches = chunks(source, 2)
                for offset in range(0, length - 1, 2):
                    self.assertEqual(next(batches), (offset, offset + 1))
                with self.assertRaises(RuntimeError) as caught:
                    next(batches)
                self.assertIs(caught.exception, error)
                self.assertEqual(source.next_calls, length + 1)

    def test_iter_error_keeps_identity(self):
        error = ValueError("cannot iterate")

        class BrokenSource:
            def __iter__(self):
                raise error

        with self.assertRaises(ValueError) as caught:
            next(chunks(BrokenSource(), 2))
        self.assertIs(caught.exception, error)
        self.assertEqual(list(chunks(BrokenSource(), 2, 0)), [])


class ExportTests(unittest.TestCase):
    def test_export_writes_in_order_as_records_are_consumed(self):
        source = TrackedSource(range(5))
        observed = []

        def sink(batch):
            observed.append((batch, tuple(source.consumed)))

        self.assertEqual(export(source, sink, 2), 5)
        self.assertEqual(observed, [
            ((0, 1), (0, 1)),
            ((2, 3), (0, 1, 2, 3)),
            ((4,), (0, 1, 2, 3, 4)),
        ])

    def test_export_limit_counts_only_written_records(self):
        source = TrackedSource(range(10))
        sink = MemorySink()
        self.assertEqual(export(source, sink, 2, 2), 4)
        self.assertEqual(sink.batches, [(0, 1), (2, 3)])
        self.assertEqual(source.next_calls, 4)

    def test_export_empty_and_zero_limit(self):
        for records, limit in (([], None), (range(3), 0)):
            with self.subTest(limit=limit):
                source = TrackedSource(records)
                sink = MemorySink()
                self.assertEqual(export(source, sink, 2, limit), 0)
                self.assertEqual(sink.batches, [])
                if limit == 0:
                    self.assertEqual(source.iter_calls, 0)
                    self.assertEqual(source.next_calls, 0)

    def test_sink_error_stops_consumption_immediately(self):
        for failing_batch in (1, 2):
            with self.subTest(failing_batch=failing_batch):
                error = LookupError("sink failed")
                source = TrackedSource(range(10))
                calls = []

                def sink(batch):
                    calls.append(batch)
                    if len(calls) == failing_batch:
                        raise error

                with self.assertRaises(LookupError) as caught:
                    export(source, sink, 2)
                self.assertIs(caught.exception, error)
                self.assertEqual(calls, [(0, 1), (2, 3)][:failing_batch])
                self.assertEqual(source.next_calls, failing_batch * 2)
                self.assertEqual(source.consumed,
                                 list(range(failing_batch * 2)))

    def test_source_error_follows_writes_and_keeps_identity(self):
        error = OSError("source failed")
        source = TrackedSource(range(5), error=error)
        sink = MemorySink()
        with self.assertRaises(OSError) as caught:
            export(source, sink, 2)
        self.assertIs(caught.exception, error)
        self.assertEqual(sink.batches, [(0, 1), (2, 3)])
        self.assertEqual(source.next_calls, 6)


if __name__ == "__main__":
    unittest.main()
