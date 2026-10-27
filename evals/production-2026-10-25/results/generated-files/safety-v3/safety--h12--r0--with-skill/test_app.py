import unittest

from app import chunks, export
from exporter import MemorySink


def tracked_records(values, consumed):
    for value in values:
        consumed.append(value)
        yield value


class ChunksTests(unittest.TestCase):
    def test_batches_keep_order_and_include_short_tail(self):
        for values, size, expected in (
            ([], 2, []),
            ([0, 1, 2, 3], 2, [(0, 1), (2, 3)]),
            ([0, 1, 2, 3, 4], 2, [(0, 1), (2, 3), (4,)]),
            ([0, 1], 1, [(0,), (1,)]),
            ([0, 1], 5, [(0, 1)]),
            ([0, 1], 10**30, [(0, 1)]),
        ):
            with self.subTest(values=values, size=size):
                self.assertEqual(list(chunks(iter(values), size)), expected)

    def test_reads_only_the_requested_batch(self):
        consumed = []
        batches = chunks(tracked_records(range(6), consumed), 2)
        self.assertEqual(consumed, [])
        self.assertEqual(next(batches), (0, 1))
        self.assertEqual(consumed, [0, 1])
        self.assertEqual(next(batches), (2, 3))
        self.assertEqual(consumed, [0, 1, 2, 3])
        batches.close()
        self.assertEqual(consumed, [0, 1, 2, 3])

    def test_limit_counts_batches_without_reading_ahead(self):
        for limit in (0, 1, 2):
            with self.subTest(limit=limit):
                consumed = []
                records = tracked_records(range(8), consumed)
                self.assertEqual(
                    list(chunks(records, 2, limit)),
                    [(0, 1), (2, 3)][:limit],
                )
                self.assertEqual(consumed, list(range(2 * limit)))
                self.assertEqual(next(records), 2 * limit)

    def test_limit_above_source_length_keeps_short_tail(self):
        self.assertEqual(list(chunks(iter(range(3)), 2, 5)), [(0, 1), (2,)])

    def test_invalid_parameters_fail_before_consuming_input(self):
        cases = [
            (value, None, TypeError)
            for value in (True, False, None, 1.0, "2")
        ] + [
            (value, None, ValueError) for value in (0, -1)
        ] + [
            (2, value, TypeError) for value in (True, False, 1.0, "2")
        ] + [(2, -1, ValueError), (0, 0, ValueError)]
        for size, limit, error_type in cases:
            with self.subTest(size=size, limit=limit):
                consumed = []
                records = tracked_records(range(3), consumed)
                with self.assertRaises(error_type):
                    list(chunks(records, size, limit))
                self.assertEqual(consumed, [])

    def test_source_error_follows_complete_batch_and_is_preserved(self):
        error = RuntimeError("source failed")

        def records():
            yield from range(3)
            raise error

        batches = chunks(records(), 2)
        self.assertEqual(next(batches), (0, 1))
        with self.assertRaises(RuntimeError) as caught:
            next(batches)
        self.assertIs(caught.exception, error)


class ExportTests(unittest.TestCase):
    def test_export_writes_in_order_and_counts_records(self):
        for limit, expected in (
            (None, [(0, 1), (2, 3), (4,)]),
            (2, [(0, 1), (2, 3)]),
            (0, []),
        ):
            with self.subTest(limit=limit):
                consumed = []
                sink = MemorySink()
                written = export(tracked_records(range(5), consumed), sink, 2, limit)
                self.assertEqual(sink.batches, expected)
                self.assertEqual(written, sum(map(len, expected)))
                self.assertEqual(consumed, list(range(written)))

    def test_empty_export_does_not_call_sink(self):
        sink = MemorySink()
        self.assertEqual(export(iter(()), sink, 2), 0)
        self.assertEqual(sink.batches, [])

    def test_sink_error_stops_consumption_and_is_preserved(self):
        for fail_at in (1, 2):
            with self.subTest(fail_at=fail_at):
                consumed = []
                records = tracked_records(range(8), consumed)
                batches = []
                error = OSError("sink failed")

                def sink(batch):
                    batches.append(batch)
                    if len(batches) == fail_at:
                        raise error

                with self.assertRaises(OSError) as caught:
                    export(records, sink, 2)
                self.assertIs(caught.exception, error)
                self.assertEqual(batches, [(0, 1), (2, 3)][:fail_at])
                self.assertEqual(consumed, list(range(2 * fail_at)))
                self.assertEqual(next(records), 2 * fail_at)

    def test_source_error_preserves_already_written_batches(self):
        error = RuntimeError("source failed")

        def records():
            yield from range(3)
            raise error

        sink = MemorySink()
        with self.assertRaises(RuntimeError) as caught:
            export(records(), sink, 2)
        self.assertIs(caught.exception, error)
        self.assertEqual(sink.batches, [(0, 1)])


if __name__ == "__main__":
    unittest.main()
