import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app import run_job
from leases import JobRejected, Lease, LeaseBusy, Pool


class RunJobTests(unittest.IsolatedAsyncioTestCase):
    def assert_released(self, pool, job="job"):
        self.assertEqual(pool.active, set())
        self.assertEqual(pool.acquired, [job])
        self.assertEqual(pool.released, [job])

    async def test_success_returns_result_and_increments_existing_count(self):
        pool = Pool()
        result = object()
        metrics = {"success": 3}

        async def handler(job):
            self.assertEqual(job, "job")
            self.assertEqual(pool.active, {job})
            return result

        self.assertIs(await run_job(pool, handler, "job", metrics), result)
        self.assertEqual(metrics, {"success": 4})
        self.assert_released(pool)

    async def test_lease_lifecycle_is_entered_and_exited(self):
        events = []

        class TrackingLease(Lease):
            async def __aenter__(self):
                events.append("enter")
                return await super().__aenter__()

            async def __aexit__(self, kind, value, traceback):
                events.append("exit")
                return await super().__aexit__(kind, value, traceback)

        async def handler(job):
            events.append("handler")

        pool = Pool()
        metrics = {}
        with patch("leases.Lease", TrackingLease):
            self.assertIsNone(await run_job(pool, handler, "job", metrics))
        self.assertEqual(events, ["enter", "handler", "exit"])
        self.assertEqual(metrics, {"success": 1})
        self.assert_released(pool)

    async def test_rejection_does_not_increment_success_and_releases(self):
        pool = Pool()
        metrics = {"success": 3}
        handler = AsyncMock(side_effect=JobRejected("rejected"))

        self.assertEqual(
            await run_job(pool, handler, "job", metrics), {"status": "rejected"}
        )
        handler.assert_awaited_once_with("job")
        self.assertEqual(metrics, {"success": 3})
        self.assert_released(pool)

    async def test_handler_errors_propagate_unchanged_and_release(self):
        for error in (ValueError("invalid"), RuntimeError("failed"), BaseException("fatal")):
            with self.subTest(error=type(error).__name__):
                pool = Pool()
                metrics = {}
                handler = AsyncMock(side_effect=error)
                with self.assertRaises(type(error)) as raised:
                    await run_job(pool, handler, "job", metrics)
                self.assertIs(raised.exception, error)
                self.assertEqual(metrics, {})
                self.assert_released(pool)

    async def test_handler_cancellation_propagates_unchanged_and_releases(self):
        pool = Pool()
        metrics = {}
        error = asyncio.CancelledError("cancelled")

        with self.assertRaises(asyncio.CancelledError) as raised:
            await run_job(pool, AsyncMock(side_effect=error), "job", metrics)
        self.assertIs(raised.exception, error)
        self.assertEqual(metrics, {})
        self.assert_released(pool)

    async def test_task_cancellation_releases_and_allows_same_job_again(self):
        pool = Pool()
        metrics = {}
        started = asyncio.Event()
        blocked = asyncio.Event()

        async def handler(job):
            started.set()
            await blocked.wait()

        task = asyncio.create_task(run_job(pool, handler, "job", metrics))
        self.addAsyncCleanup(self.cancel_if_pending, task)
        await asyncio.wait_for(started.wait(), timeout=1)
        self.assertEqual(pool.active, {"job"})
        task.cancel("stop")
        with self.assertRaises(asyncio.CancelledError) as raised:
            await task
        self.assertEqual(raised.exception.args, ("stop",))
        self.assertTrue(task.cancelled())
        self.assertEqual(metrics, {})
        self.assert_released(pool)

        self.assertEqual(await run_job(pool, AsyncMock(return_value=7), "job", metrics), 7)
        self.assertEqual(metrics, {"success": 1})
        self.assertEqual(pool.active, set())
        self.assertEqual(pool.acquired, ["job", "job"])
        self.assertEqual(pool.released, ["job", "job"])

    async def test_normal_completion_counts_even_if_cleanup_is_cancelled(self):
        pool = Pool()
        metrics = {}

        async def handler(job):
            asyncio.current_task().cancel("cancel during cleanup")
            return "completed"

        task = asyncio.create_task(run_job(pool, handler, "job", metrics))
        with self.assertRaises(asyncio.CancelledError) as raised:
            await task
        self.assertEqual(raised.exception.args, ("cancel during cleanup",))
        self.assertTrue(task.cancelled())
        self.assertEqual(metrics, {"success": 1})
        self.assert_released(pool)

    async def test_acquire_failure_never_calls_handler(self):
        errors = (JobRejected("unavailable"), RuntimeError("failed"), asyncio.CancelledError("stop"))
        for error in errors:
            with self.subTest(error=type(error).__name__):
                pool = Pool()
                pool.error = error
                handler = AsyncMock()
                metrics = {}
                with self.assertRaises(type(error)) as raised:
                    await run_job(pool, handler, "job", metrics)
                self.assertIs(raised.exception, error)
                handler.assert_not_awaited()
                self.assertEqual(metrics, {})
                self.assertEqual(pool.active, set())
                self.assertEqual(pool.acquired, [])
                self.assertEqual(pool.released, [])

    async def test_same_job_is_exclusive_until_handler_finishes(self):
        pool = Pool()
        metrics = {}
        blocked_handler = AsyncMock()

        async def handler(job):
            with self.assertRaises(LeaseBusy):
                await run_job(pool, blocked_handler, job, metrics)
            blocked_handler.assert_not_awaited()
            self.assertEqual(pool.active, {job})
            self.assertEqual(pool.released, [])
            self.assertEqual(metrics, {})

        await run_job(pool, handler, "job", metrics)
        self.assertEqual(metrics, {"success": 1})
        self.assert_released(pool)

    async def test_invalid_job_is_rejected_before_acquisition(self):
        for job in (None, 0, b"job", [], "", " ", "\t\n"):
            with self.subTest(job=job):
                pool = Pool()
                handler = AsyncMock()
                metrics = {}
                with self.assertRaisesRegex(ValueError, "^job$"):
                    await run_job(pool, handler, job, metrics)
                handler.assert_not_awaited()
                self.assertEqual(metrics, {})
                self.assertEqual(pool.active, set())
                self.assertEqual(pool.acquired, [])
                self.assertEqual(pool.released, [])

    async def test_valid_job_is_passed_through_without_stripping(self):
        pool = Pool()
        handler = AsyncMock(return_value="done")
        self.assertEqual(await run_job(pool, handler, " job ", {}), "done")
        handler.assert_awaited_once_with(" job ")
        self.assert_released(pool, " job ")

    async def cancel_if_pending(self, task):
        if not task.done():
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task


if __name__ == "__main__":
    unittest.main()
