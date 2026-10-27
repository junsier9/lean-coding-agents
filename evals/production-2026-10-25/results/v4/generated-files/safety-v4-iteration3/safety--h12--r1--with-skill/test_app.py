import unittest

from app import chunks, export
from exporter import MemorySink


class ChunksTests(unittest.TestCase):
    def test_ordered_tuple_batches_including_partial_tail(self):
        for size, expected in (
            (1, [(0,), (1,), (2,), (3,), (4,)]),
            (2, [(0, 1), (2, 3), (4,)]),
            (5, [(0, 1, 2, 3, 4)]),
            (6, [(0, 1, 2, 3, 4)]),
        ):
            with self.subTest(size=size):
                self.assertEqual(list(chunks(iter(range(5)), size)), expected)
        self.assertEqual(list(chunks([], 2)), [])

    def test_consumes_only_the_requested_batch(self):
        consumed = []

        def records():
            for record in range(5):
                consumed.append(record)
                yield record

        batches = chunks(records(), 2)
        self.assertEqual(consumed, [])
        self.assertEqual(next(batches), (0, 1))
        self.assertEqual(consumed, [0, 1])
        self.assertEqual(next(batches), (2, 3))
        self.assertEqual(consumed, [0, 1, 2, 3])
        self.assertEqual(next(batches), (4,))
        with self.assertRaises(StopIteration):
            next(batches)

    def test_zero_limit_does_not_even_acquire_an_iterator(self):
        self.assertEqual(list(chunks(object(), 2, 0)), [])

    def test_limit_counts_batches_and_does_not_read_ahead(self):
        records = iter(range(10))
        self.assertEqual(list(chunks(records, 2, 2)), [(0, 1), (2, 3)])
        self.assertEqual(next(records), 4)

    def test_strict_integer_types(self):
        class IntegerSubclass(int):
            pass

        for value in (True, False, 1.0, "2", None, IntegerSubclass(2)):
            with self.subTest(size=value):
                with self.assertRaisesRegex(TypeError, "^size$"):
                    list(chunks(object(), value, 0))
        for value in (True, False, 1.0, "2", IntegerSubclass(2)):
            with self.subTest(limit=value):
                with self.assertRaisesRegex(TypeError, "^limit$"):
                    list(chunks(object(), 2, value))

    def test_invalid_integer_values(self):
        for size in (0, -1):
            with self.subTest(size=size):
                with self.assertRaisesRegex(ValueError, "^size$"):
                    list(chunks(object(), size, 0))
        with self.assertRaisesRegex(ValueError, "^limit$"):
            list(chunks(object(), 2, -1))

    def test_large_positive_integers(self):
        huge = 1 << 100
        self.assertEqual(list(chunks([1, 2], huge, huge)), [(1, 2)])

    def test_source_error_follows_complete_batches_unchanged(self):
        error = RuntimeError("source failed")

        def records():
            yield from range(5)
            raise error

        batches = chunks(records(), 2)
        self.assertEqual(next(batches), (0, 1))
        self.assertEqual(next(batches), (2, 3))
        with self.assertRaises(RuntimeError) as raised:
            next(batches)
        self.assertIs(raised.exception, error)


class ExportTests(unittest.TestCase):
    def test_writes_in_order_and_returns_record_count(self):
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
        self.assertEqual(export(object(), MemorySink(), 2, 0), 0)

    def test_sink_error_stops_consumption_immediately(self):
        records = iter(range(8))
        batches = []
        error = RuntimeError("sink failed")

        def sink(batch):
            batches.append(batch)
            if len(batches) == 2:
                raise error

        with self.assertRaises(RuntimeError) as raised:
            export(records, sink, 2)
        self.assertIs(raised.exception, error)
        self.assertEqual(batches, [(0, 1), (2, 3)])
        self.assertEqual(next(records), 4)

    def test_source_error_preserves_already_written_batches(self):
        error = OSError("source failed")

        def records():
            yield from range(5)
            raise error

        sink = MemorySink()
        with self.assertRaises(OSError) as raised:
            export(records(), sink, 2)
        self.assertIs(raised.exception, error)
        self.assertEqual(sink.batches, [(0, 1), (2, 3)])


if __name__ == "__main__":
    unittest.main()
