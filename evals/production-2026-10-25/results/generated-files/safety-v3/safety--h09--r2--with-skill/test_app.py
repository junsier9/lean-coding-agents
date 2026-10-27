import asyncio
import unittest
from unittest.mock import AsyncMock

from app import run_job
from leases import JobRejected, LeaseBusy, Pool


class RunJobTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_returns_result_counts_and_releases(self):
        for metrics, expected_count in (({}, 1), ({"success": 3}, 4)):
            with self.subTest(metrics=metrics):
                pool = Pool()
                result = object()
                handler = AsyncMock(return_value=result)

                self.assertIs(await run_job(pool, handler, "job", metrics), result)

                handler.assert_awaited_once_with("job")
                self.assertEqual(metrics, {"success": expected_count})
                self.assertEqual(pool.acquired, ["job"])
                self.assertEqual(pool.released, ["job"])
                self.assertEqual(pool.active, set())

    async def test_rejection_does_not_count_and_releases(self):
        for metrics in ({}, {"success": 3}):
            with self.subTest(metrics=metrics):
                pool = Pool()
                handler = AsyncMock(side_effect=JobRejected("rejected"))
                before = metrics.copy()

                result = await run_job(pool, handler, "job", metrics)

                self.assertEqual(result, {"status": "rejected"})
                self.assertEqual(metrics, before)
                self.assertEqual(pool.released, ["job"])
                self.assertEqual(pool.active, set())

    async def test_handler_errors_propagate_unchanged_and_release(self):
        for error in (RuntimeError("failed"), ValueError("invalid"),
                      asyncio.CancelledError("cancelled")):
            with self.subTest(error=type(error).__name__):
                pool = Pool()
                metrics = {}
                handler = AsyncMock(side_effect=error)

                with self.assertRaises(type(error)) as raised:
                    await run_job(pool, handler, "job", metrics)

                self.assertIs(raised.exception, error)
                self.assertEqual(metrics, {})
                self.assertEqual(pool.released, ["job"])
                self.assertEqual(pool.active, set())

    async def test_task_cancellation_propagates_and_releases(self):
        pool = Pool()
        metrics = {}
        entered = asyncio.Event()

        async def handler(job):
            entered.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(run_job(pool, handler, "job", metrics))
        await entered.wait()
        task.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertEqual(metrics, {})
        self.assertEqual(pool.released, ["job"])
        self.assertEqual(pool.active, set())

    async def test_completed_handler_counts_even_if_release_is_cancelled(self):
        pool = Pool()
        metrics = {}

        async def handler(job):
            asyncio.current_task().cancel()
            return "done"

        task = asyncio.create_task(run_job(pool, handler, "job", metrics))
        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertEqual(metrics, {"success": 1})
        self.assertEqual(pool.released, ["job"])
        self.assertEqual(pool.active, set())

    async def test_acquisition_failure_propagates_without_calling_handler(self):
        for error in (RuntimeError("unavailable"), JobRejected("unavailable"),
                      asyncio.CancelledError("cancelled")):
            with self.subTest(error=type(error).__name__):
                pool = Pool()
                pool.error = error
                metrics = {}
                handler = AsyncMock()

                with self.assertRaises(type(error)) as raised:
                    await run_job(pool, handler, "job", metrics)

                self.assertIs(raised.exception, error)
                handler.assert_not_awaited()
                self.assertEqual(metrics, {})
                self.assertEqual(pool.acquired, [])
                self.assertEqual(pool.released, [])
                self.assertEqual(pool.active, set())

    async def test_invalid_job_is_rejected_before_acquisition(self):
        for job in (None, 1, b"job", "", " \t"):
            with self.subTest(job=job):
                pool = Pool()
                pool.error = AssertionError("must not acquire")
                handler = AsyncMock()
                metrics = {}

                with self.assertRaisesRegex(ValueError, "^job$"):
                    await run_job(pool, handler, job, metrics)

                handler.assert_not_awaited()
                self.assertEqual(metrics, {})
                self.assertEqual(pool.acquired, [])

    async def test_same_job_is_exclusive_until_release_and_can_be_reused(self):
        pool = Pool()
        metrics = {}
        entered = asyncio.Event()
        finish = asyncio.Event()
        blocked_handler = AsyncMock()

        async def handler(job):
            entered.set()
            await finish.wait()
            return "done"

        task = asyncio.create_task(run_job(pool, handler, "job", metrics))
        try:
            await entered.wait()
            self.assertEqual(pool.active, {"job"})
            with self.assertRaises(LeaseBusy):
                await run_job(pool, blocked_handler, "job", metrics)
            blocked_handler.assert_not_awaited()
            self.assertEqual(metrics, {})
        finally:
            finish.set()
            await task

        self.assertEqual(task.result(), "done")
        self.assertEqual(metrics, {"success": 1})
        self.assertEqual(pool.active, set())
        self.assertEqual(
            await run_job(pool, AsyncMock(return_value="again"), "job", metrics),
            "again",
        )
        self.assertEqual(metrics, {"success": 2})
        self.assertEqual(pool.acquired, ["job", "job"])
        self.assertEqual(pool.released, ["job", "job"])
        self.assertEqual(pool.active, set())
