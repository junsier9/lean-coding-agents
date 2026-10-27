import asyncio
import unittest
from unittest.mock import AsyncMock

from app import run_job
from leases import JobRejected, LeaseBusy, Pool


class RunJobTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_returns_result_and_counts_each_completion(self):
        pool = Pool()
        metrics = {}
        result = object()

        async def handler(job):
            self.assertIn(job, pool.active)
            return result

        for count in (1, 2):
            self.assertIs(await run_job(pool, handler, "job", metrics), result)
            self.assertEqual(metrics, {"success": count})
            self.assertEqual(pool.active, set())
            self.assertEqual(pool.acquired, ["job"] * count)
            self.assertEqual(pool.released, ["job"] * count)

    async def test_rejection_releases_lease_without_counting_success(self):
        pool = Pool()
        metrics = {"success": 3}
        handler = AsyncMock(side_effect=JobRejected("rejected"))

        self.assertEqual(
            await run_job(pool, handler, "job", metrics), {"status": "rejected"}
        )

        handler.assert_awaited_once_with("job")
        self.assertEqual(metrics, {"success": 3})
        self.assertEqual(pool.active, set())
        self.assertEqual(pool.released, ["job"])

    async def test_handler_failures_propagate_unchanged_and_release_lease(self):
        for error in (RuntimeError("failed"), ValueError("invalid"),
                      asyncio.CancelledError("cancelled")):
            with self.subTest(error=type(error).__name__):
                pool = Pool()
                metrics = {}
                handler = AsyncMock(side_effect=error)

                with self.assertRaises(type(error)) as caught:
                    await run_job(pool, handler, "job", metrics)

                self.assertIs(caught.exception, error)
                self.assertEqual(metrics, {})
                self.assertEqual(pool.active, set())
                self.assertEqual(pool.released, ["job"])

    async def test_task_cancellation_releases_lease(self):
        pool = Pool()
        metrics = {}
        started = asyncio.Event()

        async def handler(job):
            started.set()
            await asyncio.Future()

        task = asyncio.create_task(run_job(pool, handler, "job", metrics))
        try:
            await asyncio.wait_for(started.wait(), timeout=1)
            self.assertEqual(pool.active, {"job"})
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(task.cancelled())
            self.assertEqual(metrics, {})
            self.assertEqual(pool.active, set())
            self.assertEqual(pool.released, ["job"])
        finally:
            if not task.done():
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task

    async def test_acquire_failure_propagates_without_calling_handler(self):
        for error in (RuntimeError("unavailable"), JobRejected("acquisition"),
                      asyncio.CancelledError("cancelled")):
            with self.subTest(error=type(error).__name__):
                pool = Pool()
                pool.error = error
                metrics = {}
                handler = AsyncMock()

                with self.assertRaises(type(error)) as caught:
                    await run_job(pool, handler, "job", metrics)

                self.assertIs(caught.exception, error)
                handler.assert_not_awaited()
                self.assertEqual(metrics, {})
                self.assertEqual(pool.active, set())
                self.assertEqual(pool.acquired, [])
                self.assertEqual(pool.released, [])

    async def test_same_job_is_exclusive_and_available_after_release(self):
        pool = Pool()
        metrics = {}
        started = asyncio.Event()
        finish = asyncio.Event()
        blocked_handler = AsyncMock(return_value=None)

        async def handler(job):
            started.set()
            await finish.wait()
            return job

        task = asyncio.create_task(run_job(pool, handler, "job", metrics))
        try:
            await asyncio.wait_for(started.wait(), timeout=1)
            with self.assertRaises(LeaseBusy):
                await run_job(pool, blocked_handler, "job", metrics)
            blocked_handler.assert_not_awaited()
            self.assertEqual(pool.active, {"job"})
            self.assertEqual(pool.acquired, ["job"])
            self.assertEqual(pool.released, [])
            self.assertEqual(metrics, {})
        finally:
            finish.set()
            await task

        self.assertEqual(metrics, {"success": 1})
        self.assertIsNone(await run_job(pool, blocked_handler, "job", metrics))
        blocked_handler.assert_awaited_once_with("job")
        self.assertEqual(metrics, {"success": 2})
        self.assertEqual(pool.active, set())
        self.assertEqual(pool.acquired, ["job", "job"])
        self.assertEqual(pool.released, ["job", "job"])

    async def test_metrics_failure_releases_lease_and_propagates(self):
        pool = Pool()
        metrics = {"success": None}
        handler = AsyncMock()

        with self.assertRaises(TypeError):
            await run_job(pool, handler, "job", metrics)

        handler.assert_awaited_once_with("job")
        self.assertEqual(metrics, {"success": None})
        self.assertEqual(pool.active, set())
        self.assertEqual(pool.released, ["job"])

    async def test_invalid_job_fails_before_acquisition(self):
        for job in (None, 1, "", " \t\n"):
            with self.subTest(job=job):
                pool = Pool()
                metrics = {}
                handler = AsyncMock()

                with self.assertRaisesRegex(ValueError, "^job$"):
                    await run_job(pool, handler, job, metrics)

                handler.assert_not_awaited()
                self.assertEqual(metrics, {})
                self.assertEqual(pool.acquired, [])
                self.assertEqual(pool.released, [])


if __name__ == "__main__":
    unittest.main()
