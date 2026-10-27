import unittest
from itertools import count

from app import chunks
from exporter import MemorySink, export


class ChunksTests(unittest.TestCase):
    def test_batches_preserve_order_and_include_partial_tail(self):
        for records, size, expected in (
            ([], 2, []),
            ([1], 2, [(1,)]),
            ([1, 2, 3, 4], 2, [(1, 2), (3, 4)]),
            ([1, 2, 3, 4, 5], 2, [(1, 2), (3, 4), (5,)]),
            ([1, 2], 1, [(1,), (2,)]),
            ([1, 2], 10**100, [(1, 2)]),
        ):
            with self.subTest(records=records, size=size):
                self.assertEqual(list(chunks(iter(records), size)), expected)

    def test_source_is_only_consumed_when_a_batch_is_requested(self):
        consumed = []

        def source():
            for record in range(5):
                consumed.append(record)
                yield record

        batches = chunks(source(), 2)
        self.assertEqual(consumed, [])
        self.assertEqual(next(batches), (0, 1))
        self.assertEqual(consumed, [0, 1])
        self.assertEqual(next(batches), (2, 3))
        self.assertEqual(consumed, [0, 1, 2, 3])
        batches.close()
        self.assertEqual(consumed, [0, 1, 2, 3])

    def test_zero_limit_does_not_open_source(self):
        class UnopenedSource:
            def __iter__(self):
                raise AssertionError("source must not be opened")

        self.assertEqual(list(chunks(UnopenedSource(), 2, 0)), [])

    def test_limit_counts_batches_without_reading_ahead(self):
        records = iter(range(10))
        self.assertEqual(list(chunks(records, 2, 2)), [(0, 1), (2, 3)])
        self.assertEqual(next(records), 4)

    def test_can_stop_on_an_unbounded_source(self):
        batches = chunks(count(), 2)
        self.assertEqual(next(batches), (0, 1))
        batches.close()
        self.assertEqual(list(chunks(count(), 2, 2)), [(0, 1), (2, 3)])

    def test_parameters_are_strict_integers_with_valid_ranges(self):
        class IntSubclass(int):
            pass

        for size in (None, True, False, 1.0, "2", IntSubclass(2)):
            with self.subTest(size=size):
                with self.assertRaisesRegex(TypeError, "^size$"):
                    list(chunks([], size))
        for size in (0, -1):
            with self.subTest(size=size):
                with self.assertRaisesRegex(ValueError, "^size$"):
                    list(chunks([], size, 0))
        for limit in (True, False, 1.0, "2", IntSubclass(2)):
            with self.subTest(limit=limit):
                with self.assertRaisesRegex(TypeError, "^limit$"):
                    list(chunks([], 2, limit))
        with self.assertRaisesRegex(ValueError, "^limit$"):
            list(chunks([], 2, -1))

    def test_source_error_follows_complete_batches_and_preserves_identity(self):
        for record_count in (0, 2, 3):
            with self.subTest(record_count=record_count):
                failure = RuntimeError("source failed")

                def source():
                    yield from range(record_count)
                    raise failure

                batches = chunks(source(), 2)
                if record_count >= 2:
                    self.assertEqual(next(batches), (0, 1))
                with self.assertRaises(RuntimeError) as caught:
                    next(batches)
                self.assertIs(caught.exception, failure)

    def test_limit_does_not_probe_a_pending_source_error(self):
        failure = RuntimeError("source failed")

        def source():
            yield 1
            yield 2
            raise failure

        records = source()
        self.assertEqual(list(chunks(records, 2, 1)), [(1, 2)])
        with self.assertRaises(RuntimeError) as caught:
            next(records)
        self.assertIs(caught.exception, failure)


class ExportTests(unittest.TestCase):
    def test_writes_batches_in_order_and_counts_records(self):
        for limit, expected in (
            (None, [(0, 1), (2, 3), (4,)]),
            (2, [(0, 1), (2, 3)]),
            (0, []),
        ):
            with self.subTest(limit=limit):
                sink = MemorySink()
                written = export(iter(range(5)), sink, 2, limit)
                self.assertEqual(sink.batches, expected)
                self.assertEqual(written, sum(map(len, expected)))
        self.assertEqual(export([], MemorySink(), 2), 0)

    def test_sink_failure_stops_consumption_immediately(self):
        for failing_batch in (1, 2):
            with self.subTest(failing_batch=failing_batch):
                records = iter(range(10))
                failure = RuntimeError("sink failed")
                written = []

                def sink(batch):
                    written.append(batch)
                    if len(written) == failing_batch:
                        raise failure

                with self.assertRaises(RuntimeError) as caught:
                    export(records, sink, 2)
                self.assertIs(caught.exception, failure)
                self.assertEqual(written, [(0, 1), (2, 3)][:failing_batch])
                self.assertEqual(next(records), failing_batch * 2)

    def test_complete_batches_are_written_before_source_failure(self):
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
