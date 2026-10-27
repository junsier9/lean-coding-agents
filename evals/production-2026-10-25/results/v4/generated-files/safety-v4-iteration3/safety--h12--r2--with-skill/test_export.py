import unittest

from app import chunks, export
from exporter import MemorySink


class ChunkTests(unittest.TestCase):
    def test_batches_preserve_order_and_include_only_nonempty_tuples(self):
        for records, size, expected in (
            ([], 2, []),
            (range(4), 2, [(0, 1), (2, 3)]),
            (range(5), 2, [(0, 1), (2, 3), (4,)]),
            (range(3), 1, [(0,), (1,), (2,)]),
            (range(3), 10**100, [(0, 1, 2)]),
        ):
            with self.subTest(size=size, records=records):
                batches = list(chunks(iter(records), size))
                self.assertEqual(batches, expected)
                self.assertTrue(all(type(batch) is tuple for batch in batches))

    def test_construction_and_iteration_are_lazy(self):
        consumed = []

        def source():
            for record in range(5):
                consumed.append(record)
                yield record

        batches = chunks(source(), 2)
        self.assertEqual(consumed, [])
        first = next(batches)
        self.assertEqual(first, (0, 1))
        self.assertEqual(consumed, [0, 1])
        self.assertEqual(next(batches), (2, 3))
        self.assertEqual(first, (0, 1))
        self.assertEqual(consumed, [0, 1, 2, 3])

    def test_zero_limit_does_not_even_request_an_iterator(self):
        self.assertEqual(list(chunks(object(), 2, 0)), [])

    def test_limit_counts_batches_without_reading_ahead(self):
        for limit, expected in (
            (1, [(0, 1)]),
            (2, [(0, 1), (2, 3)]),
            (3, [(0, 1), (2, 3), (4,)]),
            (10**100, [(0, 1), (2, 3), (4,)]),
        ):
            with self.subTest(limit=limit):
                source = iter(range(5))
                self.assertEqual(list(chunks(source, 2, limit)), expected)
                self.assertEqual(list(source), list(range(min(limit * 2, 5), 5)))

    def test_limit_does_not_reach_a_source_failure(self):
        def source():
            yield 0
            yield 1
            raise AssertionError("read beyond the batch limit")

        self.assertEqual(list(chunks(source(), 2, 1)), [(0, 1)])

    def test_invalid_parameters_fail_before_touching_source(self):
        class IntegerSubclass(int):
            pass

        for name in ("size", "limit"):
            for value in (True, False, 1.0, "1", IntegerSubclass(1)):
                with self.subTest(name=name, value=value):
                    arguments = {"size": 2, "limit": None, name: value}
                    with self.assertRaisesRegex(TypeError, name):
                        list(chunks(object(), **arguments))
        with self.assertRaisesRegex(TypeError, "size"):
            list(chunks(object(), None))
        for arguments in ({"size": 0}, {"size": -1}, {"size": 2, "limit": -1}):
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValueError):
                    list(chunks(object(), **arguments))
        with self.assertRaisesRegex(ValueError, "size"):
            list(chunks(object(), 0, 0))

    def test_source_failure_follows_completed_batches_unchanged(self):
        failure = RuntimeError("source failed")

        def source():
            yield from range(5)
            raise failure

        batches = chunks(source(), 2)
        self.assertEqual(next(batches), (0, 1))
        self.assertEqual(next(batches), (2, 3))
        with self.assertRaises(RuntimeError) as caught:
            next(batches)
        self.assertIs(caught.exception, failure)


class ExportTests(unittest.TestCase):
    def test_writes_in_order_and_returns_record_count(self):
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

    def test_empty_and_zero_limit_do_not_call_sink(self):
        def sink(batch):
            self.fail("unexpected sink call")

        self.assertEqual(export([], sink, 2), 0)
        self.assertEqual(export(object(), sink, 2, 0), 0)

    def test_writes_each_batch_before_reading_the_next(self):
        events = []

        def source():
            for record in range(3):
                events.append(("read", record))
                yield record

        written = export(source(), lambda batch: events.append(("write", batch)), 2)
        self.assertEqual(written, 3)
        self.assertEqual(events, [
            ("read", 0), ("read", 1), ("write", (0, 1)),
            ("read", 2), ("write", (2,)),
        ])

    def test_sink_failure_propagates_and_stops_consumption(self):
        failure = OSError("sink failed")
        source = iter(range(6))
        batches = []

        def sink(batch):
            batches.append(batch)
            if len(batches) == 2:
                raise failure

        with self.assertRaises(OSError) as caught:
            export(source, sink, 2)
        self.assertIs(caught.exception, failure)
        self.assertEqual(batches, [(0, 1), (2, 3)])
        self.assertEqual(list(source), [4, 5])

    def test_source_failure_preserves_previously_written_batches(self):
        failure = LookupError("source failed")

        def source():
            yield from range(5)
            raise failure

        sink = MemorySink()
        with self.assertRaises(LookupError) as caught:
            export(source(), sink, 2)
        self.assertIs(caught.exception, failure)
        self.assertEqual(sink.batches, [(0, 1), (2, 3)])


if __name__ == "__main__":
    unittest.main()
