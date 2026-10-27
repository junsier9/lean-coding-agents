import asyncio
import unittest
from unittest.mock import AsyncMock

from app import read_page
from stream import MemorySource


class ReadPageTests(unittest.IsolatedAsyncioTestCase):
    async def test_limit_leaves_remaining_values_for_next_page(self):
        source = MemorySource([1, 2, 3, 4, 5])

        self.assertEqual(await read_page(source, 2), [1, 2])
        self.assertEqual(source.reads, 2)
        self.assertEqual((source.opens, source.closes), (1, 1))
        self.assertFalse(source.opened)

        self.assertEqual(await read_page(source, 3), [3, 4, 5])
        self.assertEqual(source.reads, 5)
        self.assertEqual((source.opens, source.closes), (2, 2))
        self.assertFalse(source.opened)

    async def test_exhaustion_returns_collected_prefix(self):
        for values in ([], [1, None, 3]):
            with self.subTest(values=values):
                source = MemorySource(values)

                self.assertEqual(await read_page(source, 5), values)
                self.assertEqual(source.reads, len(values) + 1)
                self.assertEqual((source.opens, source.closes), (1, 1))
                self.assertFalse(source.opened)

    async def test_zero_limit_does_not_touch_source(self):
        source = MemorySource([1])

        self.assertEqual(await read_page(source, 0), [])
        self.assertEqual((source.opens, source.reads, source.closes), (0, 0, 0))

    async def test_invalid_limits_fail_before_opening(self):
        class IntSubclass(int):
            pass

        for limit in (-1, True, False, 1.0, "1", None, IntSubclass(1)):
            with self.subTest(limit=limit, type=type(limit)):
                source = MemorySource([1])

                with self.assertRaises(ValueError):
                    await read_page(source, limit)

                self.assertEqual(
                    (source.opens, source.reads, source.closes), (0, 0, 0)
                )

    async def test_open_failure_propagates_without_closing(self):
        for error in (RuntimeError("open failed"), asyncio.CancelledError("open")):
            with self.subTest(error=type(error)):
                source = MemorySource([1])
                source.open = AsyncMock(side_effect=error)

                with self.assertRaises(type(error)) as caught:
                    await read_page(source, 1)

                self.assertIs(caught.exception, error)
                source.open.assert_awaited_once_with()
                self.assertEqual((source.reads, source.closes), (0, 0))

    async def test_read_failure_propagates_after_closing(self):
        for prefix in ([], [1]):
            with self.subTest(prefix=prefix):
                error = RuntimeError("read failed")
                source = MemorySource([])
                source.read = AsyncMock(side_effect=[*prefix, error])

                with self.assertRaises(RuntimeError) as caught:
                    await read_page(source, 3)

                self.assertIs(caught.exception, error)
                self.assertEqual(source.read.await_count, len(prefix) + 1)
                self.assertEqual((source.opens, source.closes), (1, 1))
                self.assertFalse(source.opened)

    async def test_cancel_during_read_propagates_after_closing(self):
        class BlockingSource(MemorySource):
            def __init__(self):
                super().__init__([1])
                self.read_started = asyncio.Event()

            async def read(self):
                if self.reads == 0:
                    return await super().read()
                self.read_started.set()
                await asyncio.Future()

        source = BlockingSource()
        task = asyncio.create_task(read_page(source, 2))
        await asyncio.wait_for(source.read_started.wait(), timeout=5)
        self.assertTrue(task.cancel("cancel during read"))

        with self.assertRaises(asyncio.CancelledError) as caught:
            await asyncio.wait_for(task, timeout=5)

        self.assertEqual(caught.exception.args, ("cancel during read",))
        self.assertTrue(task.cancelled())
        self.assertEqual((source.opens, source.closes), (1, 1))
        self.assertFalse(source.opened)

    async def test_close_failure_propagates_without_retry(self):
        error = RuntimeError("close failed")
        source = MemorySource([1])
        source.close = AsyncMock(side_effect=error)

        with self.assertRaises(RuntimeError) as caught:
            await read_page(source, 1)

        self.assertIs(caught.exception, error)
        source.close.assert_awaited_once_with()


if __name__ == "__main__":
    unittest.main()
