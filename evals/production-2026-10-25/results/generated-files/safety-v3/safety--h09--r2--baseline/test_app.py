import asyncio
import unittest
from unittest.mock import AsyncMock

from app import run_job
from leases import JobRejected, LeaseBusy, Pool


class RunJobTests(unittest.IsolatedAsyncioTestCase):
    def assert_released(self, pool):
        self.assertEqual(pool.active, set())
        self.assertEqual(pool.acquired, ["job"])
        self.assertEqual(pool.released, ["job"])

    async def test_success_returns_result_counts_completion_and_releases(self):
        pool = Pool()
        result = object()
        metrics = {"success": 4, "other": 2}

        async def handler(job):
            self.assertEqual(job, "job")
            self.assertEqual(pool.active, {"job"})
            return result

        self.assertIs(await run_job(pool, handler, "job", metrics), result)
        self.assertEqual(metrics, {"success": 5, "other": 2})
        self.assert_released(pool)

    async def test_rejection_returns_status_without_success_and_releases(self):
        pool = Pool()
        metrics = {"success": 4}
        handler = AsyncMock(side_effect=JobRejected("business rejection"))

        self.assertEqual(
            await run_job(pool, handler, "job", metrics), {"status": "rejected"}
        )
        handler.assert_awaited_once_with("job")
        self.assertEqual(metrics, {"success": 4})
        self.assert_released(pool)

    async def test_handler_exceptions_propagate_unchanged_and_release(self):
        for error in (ValueError("bad value"), RuntimeError("failure"),
                      asyncio.CancelledError("cancelled")):
            with self.subTest(error=type(error).__name__):
                pool = Pool()
                metrics = {}
                handler = AsyncMock(side_effect=error)

                with self.assertRaises(type(error)) as caught:
                    await run_job(pool, handler, "job", metrics)

                self.assertIs(caught.exception, error)
                self.assertEqual(metrics, {})
                self.assert_released(pool)
                self.assertEqual(
                    await run_job(pool, AsyncMock(return_value="retry"), "job", metrics),
                    "retry",
                )
                self.assertEqual(metrics, {"success": 1})
                self.assertEqual(pool.released, ["job", "job"])

    async def test_task_cancellation_propagates_and_releases(self):
        pool = Pool()
        metrics = {}
        started = asyncio.Event()

        async def handler(job):
            started.set()
            await asyncio.Future()

        task = asyncio.create_task(run_job(pool, handler, "job", metrics))
        try:
            await asyncio.wait_for(started.wait(), timeout=2)
            self.assertEqual(pool.active, {"job"})
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(task.cancelled())
            self.assertEqual(metrics, {})
            self.assert_released(pool)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_acquire_failure_never_calls_handler(self):
        for error in (LeaseBusy("busy"), JobRejected("acquire rejection"),
                      RuntimeError("unavailable"), asyncio.CancelledError()):
            with self.subTest(error=type(error).__name__):
                pool = Pool()
                pool.error = error
                metrics = {}
                handler = AsyncMock()

                with self.assertRaises(type(error)) as caught:
                    await run_job(pool, handler, "job", metrics)

                self.assertIs(caught.exception, error)
                handler.assert_not_called()
                self.assertEqual(pool.acquired, [])
                self.assertEqual(pool.released, [])
                self.assertEqual(pool.active, set())
                self.assertEqual(metrics, {})

    async def test_cancellation_during_release_still_counts_completed_handler(self):
        pool = Pool()
        metrics = {}

        async def handler(job):
            asyncio.get_running_loop().call_soon(asyncio.current_task().cancel)
            return "completed"

        task = asyncio.create_task(run_job(pool, handler, "job", metrics))
        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertTrue(task.cancelled())
        self.assertEqual(metrics, {"success": 1})
        self.assert_released(pool)

    async def test_invalid_job_is_rejected_before_acquiring(self):
        for job in (None, 1, [], "", " \t\n"):
            with self.subTest(job=job):
                pool = Pool()
                pool.acquire = AsyncMock(wraps=pool.acquire)
                handler = AsyncMock()
                metrics = {}

                with self.assertRaisesRegex(ValueError, "^job$"):
                    await run_job(pool, handler, job, metrics)

                pool.acquire.assert_not_called()
                handler.assert_not_called()
                self.assertEqual(metrics, {})

    async def test_same_job_is_exclusive_while_other_jobs_can_run(self):
        pool = Pool()
        metrics = {}
        started = asyncio.Event()
        finish = asyncio.Event()

        async def handler(job):
            started.set()
            await finish.wait()
            return "first"

        task = asyncio.create_task(run_job(pool, handler, "job", metrics))
        try:
            await asyncio.wait_for(started.wait(), timeout=2)
            blocked_handler = AsyncMock()
            with self.assertRaises(LeaseBusy):
                await run_job(pool, blocked_handler, "job", metrics)
            blocked_handler.assert_not_called()
            self.assertEqual(pool.active, {"job"})
            self.assertEqual(metrics, {})

            self.assertEqual(
                await run_job(pool, AsyncMock(return_value="other"), "other", metrics),
                "other",
            )
            self.assertEqual(pool.active, {"job"})
            finish.set()
            self.assertEqual(await asyncio.wait_for(task, timeout=2), "first")
            self.assertEqual(
                await run_job(pool, AsyncMock(return_value="retry"), "job", metrics),
                "retry",
            )
            self.assertEqual(pool.active, set())
            self.assertEqual(pool.acquired, ["job", "other", "job"])
            self.assertEqual(pool.released, ["other", "job", "job"])
            self.assertEqual(metrics, {"success": 3})
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
