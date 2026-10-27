import unittest

from app import chunks
from exporter import MemorySink, export


class ChunksTests(unittest.TestCase):
    def test_order_tuples_and_partial_tail(self):
        records = [object() for _ in range(5)]
        batches = list(chunks(iter(records), 2))
        self.assertEqual(batches, [(records[0], records[1]),
                                   (records[2], records[3]), (records[4],)])
        self.assertTrue(all(type(batch) is tuple for batch in batches))

    def test_empty_input_has_no_batches(self):
        self.assertEqual(list(chunks([], 2)), [])

    def test_exact_batches_have_no_empty_tail(self):
        self.assertEqual(list(chunks(range(4), 2)), [(0, 1), (2, 3)])

    def test_consumes_only_the_requested_batch(self):
        consumed = []

        def records():
            for value in range(6):
                consumed.append(value)
                yield value

        batches = chunks(records(), 2)
        self.assertEqual(consumed, [])
        self.assertEqual(next(batches), (0, 1))
        self.assertEqual(consumed, [0, 1])
        self.assertEqual(next(batches), (2, 3))
        self.assertEqual(consumed, [0, 1, 2, 3])
        batches.close()

    def test_limit_counts_batches_without_reading_ahead(self):
        records = iter(range(8))
        self.assertEqual(list(chunks(records, 2, 2)), [(0, 1), (2, 3)])
        self.assertEqual(next(records), 4)

    def test_zero_limit_does_not_even_acquire_an_iterator(self):
        self.assertEqual(list(chunks(object(), 2, 0)), [])

    def test_limit_above_available_batches_preserves_tail(self):
        self.assertEqual(list(chunks(range(3), 2, 5)), [(0, 1), (2,)])

    def test_size_one(self):
        self.assertEqual(list(chunks(range(3), 1)), [(0,), (1,), (2,)])

    def test_large_positive_integer_size_is_valid(self):
        self.assertEqual(list(chunks(range(3), 10**100)), [(0, 1, 2)])

    def test_strict_integer_parameters(self):
        class IntSubclass(int):
            pass

        for value in (True, False, 1.0, "2", None, IntSubclass(2)):
            with self.subTest(parameter="size", value=value):
                with self.assertRaisesRegex(TypeError, "^size$"):
                    list(chunks([], value))
        for value in (True, False, 1.0, "2", IntSubclass(2)):
            with self.subTest(parameter="limit", value=value):
                with self.assertRaisesRegex(TypeError, "^limit$"):
                    list(chunks([], 2, value))

    def test_invalid_values(self):
        for size in (0, -1):
            with self.subTest(size=size):
                with self.assertRaisesRegex(ValueError, "^size$"):
                    list(chunks([], size))
        with self.assertRaisesRegex(ValueError, "^limit$"):
            list(chunks([], 2, -1))

    def test_zero_limit_still_validates_size(self):
        with self.assertRaisesRegex(TypeError, "^size$"):
            list(chunks([], True, 0))
        with self.assertRaisesRegex(ValueError, "^size$"):
            list(chunks([], 0, 0))

    def test_source_error_follows_complete_batches_unchanged(self):
        failure = RuntimeError("source failed")

        def records():
            yield from range(5)
            raise failure

        batches = chunks(records(), 2)
        self.assertEqual(next(batches), (0, 1))
        self.assertEqual(next(batches), (2, 3))
        with self.assertRaises(RuntimeError) as caught:
            next(batches)
        self.assertIs(caught.exception, failure)


class ExportTests(unittest.TestCase):
    def test_export_writes_in_order_and_counts_records(self):
        sink = MemorySink()
        self.assertEqual(export(range(5), sink, 2), 5)
        self.assertEqual(sink.batches, [(0, 1), (2, 3), (4,)])

    def test_export_respects_batch_limit(self):
        records = iter(range(8))
        sink = MemorySink()
        self.assertEqual(export(records, sink, 2, 2), 4)
        self.assertEqual(sink.batches, [(0, 1), (2, 3)])
        self.assertEqual(next(records), 4)

    def test_zero_limit_writes_nothing(self):
        sink = MemorySink()
        self.assertEqual(export(object(), sink, 2, 0), 0)
        self.assertEqual(sink.batches, [])

    def test_empty_source_writes_nothing(self):
        sink = MemorySink()
        self.assertEqual(export([], sink, 2), 0)
        self.assertEqual(sink.batches, [])

    def test_sink_failure_stops_consumption_and_propagates(self):
        failure = OSError("sink failed")
        records = iter(range(8))
        batches = []

        def sink(batch):
            batches.append(batch)
            if len(batches) == 2:
                raise failure

        with self.assertRaises(OSError) as caught:
            export(records, sink, 2)
        self.assertIs(caught.exception, failure)
        self.assertEqual(batches, [(0, 1), (2, 3)])
        self.assertEqual(next(records), 4)

    def test_source_failure_preserves_completed_writes(self):
        failure = RuntimeError("source failed")

        def records():
            yield from range(5)
            raise failure

        sink = MemorySink()
        with self.assertRaises(RuntimeError) as caught:
            export(records(), sink, 2)
        self.assertIs(caught.exception, failure)
        self.assertEqual(sink.batches, [(0, 1), (2, 3)])


if __name__ == "__main__":
    unittest.main()
