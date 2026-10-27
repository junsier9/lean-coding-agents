import asyncio
import unittest
from unittest.mock import AsyncMock

from app import read_page
from stream import MemorySource


class ReadPageTests(unittest.IsolatedAsyncioTestCase):
    async def test_limit_leaves_remaining_values_unconsumed(self):
        source = MemorySource([1, 2, 3])

        self.assertEqual(await read_page(source, 2), [1, 2])

        self.assertEqual(source.reads, 2)
        self.assertEqual(next(source.values), 3)
        self.assertEqual(source.opens, 1)
        self.assertEqual(source.closes, 1)
        self.assertFalse(source.opened)

    async def test_exhaustion_returns_collected_prefix(self):
        for values in ([], [1, 2]):
            with self.subTest(values=values):
                source = MemorySource(values)

                self.assertEqual(await read_page(source, 3), values)

                self.assertEqual(source.reads, len(values) + 1)
                self.assertEqual(source.opens, 1)
                self.assertEqual(source.closes, 1)
                self.assertFalse(source.opened)

    async def test_exact_limit_does_not_probe_for_exhaustion(self):
        source = MemorySource([1, 2])

        self.assertEqual(await read_page(source, 2), [1, 2])

        self.assertEqual(source.reads, 2)
        self.assertEqual(source.closes, 1)
        self.assertFalse(source.opened)

    async def test_zero_limit_does_not_touch_source(self):
        self.assertEqual(await read_page(None, 0), [])

    async def test_invalid_limits_raise_before_touching_source(self):
        class IntSubclass(int):
            pass

        for limit in (-1, True, False, 1.0, "1", None, IntSubclass(1)):
            with self.subTest(limit=limit, type=type(limit)):
                with self.assertRaises(ValueError):
                    await read_page(None, limit)

    async def test_open_failure_propagates_without_read_or_close(self):
        source = MemorySource([1])
        error = RuntimeError("open failed")
        source.open = AsyncMock(side_effect=error)

        with self.assertRaises(RuntimeError) as raised:
            await read_page(source, 1)

        self.assertIs(raised.exception, error)
        source.open.assert_awaited_once_with()
        self.assertEqual(source.reads, 0)
        self.assertEqual(source.closes, 0)

    async def test_read_failure_propagates_after_closing(self):
        source = MemorySource([])
        error = RuntimeError("read failed")
        source.read = AsyncMock(side_effect=[1, error])

        with self.assertRaises(RuntimeError) as raised:
            await read_page(source, 3)

        self.assertIs(raised.exception, error)
        self.assertEqual(source.read.await_count, 2)
        self.assertEqual(source.opens, 1)
        self.assertEqual(source.closes, 1)
        self.assertFalse(source.opened)

    async def test_cancellation_during_read_awaits_close_and_propagates(self):
        source = MemorySource([])
        reading = asyncio.Event()

        async def blocking_read():
            reading.set()
            await asyncio.Future()

        source.read = AsyncMock(side_effect=blocking_read)
        task = asyncio.create_task(read_page(source, 1))
        try:
            await asyncio.wait_for(reading.wait(), timeout=1)
            task.cancel("cancel pending read")

            with self.assertRaises(asyncio.CancelledError) as raised:
                await asyncio.wait_for(task, timeout=1)

            self.assertEqual(raised.exception.args, ("cancel pending read",))
            self.assertTrue(task.cancelled())
            source.read.assert_awaited_once_with()
            self.assertEqual(source.opens, 1)
            self.assertEqual(source.closes, 1)
            self.assertFalse(source.opened)
        finally:
            if not task.done():
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task

    async def test_close_failure_is_observable(self):
        source = MemorySource([1])
        error = RuntimeError("close failed")
        source.close = AsyncMock(side_effect=error)

        with self.assertRaises(RuntimeError) as raised:
            await read_page(source, 1)

        self.assertIs(raised.exception, error)
        self.assertEqual(source.reads, 1)
        source.close.assert_awaited_once_with()


if __name__ == "__main__":
    unittest.main()
