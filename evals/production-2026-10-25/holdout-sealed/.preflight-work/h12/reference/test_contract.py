import unittest
from app import chunks
from exporter import export, MemorySink

class TailFailure(RuntimeError):
    pass

class Contract(unittest.TestCase):
    def test_normal_batches(self):
        self.assertEqual(list(chunks([1, 2, 3], 2)), [(1, 2), (3,)])

    def test_empty(self):
        self.assertEqual(list(chunks([], 2)), [])

    def test_zero_limit_no_consumption(self):
        consumed = []
        def source():
            consumed.append(1)
            yield 1
        self.assertEqual(list(chunks(source(), 2, 0)), [])
        self.assertEqual(consumed, [])

    def test_prefix_before_source_error(self):
        error = TailFailure("tail")
        def source():
            yield 1
            yield 2
            raise error
        iterator = chunks(source(), 2)
        self.assertEqual(next(iterator), (1, 2))
        with self.assertRaises(TailFailure) as found:
            next(iterator)
        self.assertIs(found.exception, error)

    def test_limit_does_not_read_tail(self):
        def source():
            yield 1
            yield 2
            raise TailFailure("must not read")
        self.assertEqual(list(chunks(source(), 2, 1)), [(1, 2)])

    def test_bad_size_type(self):
        for size in (True, "2", 2.0):
            with self.subTest(size=size), self.assertRaises(TypeError):
                list(chunks([], size))

    def test_bad_size_value(self):
        for size in (0, -1):
            with self.subTest(size=size), self.assertRaises(ValueError):
                list(chunks([], size))

    def test_bad_limit(self):
        with self.assertRaises(TypeError):
            list(chunks([], 1, True))
        with self.assertRaises(ValueError):
            list(chunks([], 1, -1))

    def test_sink_failure_stops_consumption(self):
        consumed, calls = [], []
        def source():
            for value in range(5):
                consumed.append(value)
                yield value
        error = OSError("sink")
        def sink(batch):
            calls.append(batch)
            raise error
        with self.assertRaises(OSError) as found:
            export(source(), sink, 2)
        self.assertIs(found.exception, error)
        self.assertEqual(consumed, [0, 1])
        self.assertEqual(calls, [(0, 1)])

    def test_export_count(self):
        sink = MemorySink()
        self.assertEqual(export([1, 2, 3], sink, 2), 3)
        self.assertEqual(sink.batches, [(1, 2), (3,)])
