import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app import run_job
from leases import JobRejected, Lease, LeaseBusy, Pool


class RunJobTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_returns_result_and_counts_completion(self):
        for initial in ({}, {"success": 3, "other": 9}):
            with self.subTest(initial=initial):
                pool = Pool()
                metrics = initial.copy()
                result = object()
                handler = AsyncMock(return_value=result)

                self.assertIs(await run_job(pool, handler, "job", metrics), result)

                handler.assert_awaited_once_with("job")
                self.assertEqual(metrics, {**initial, "success": initial.get("success", 0) + 1})
                self.assertEqual(pool.acquired, ["job"])
                self.assertEqual(pool.released, ["job"])
                self.assertEqual(pool.active, set())

    async def test_invalid_job_fails_before_acquisition(self):
        for job in (None, 0, b"job", "", " \t\n"):
            with self.subTest(job=job):
                pool = Pool()
                handler = AsyncMock()
                metrics = {}

                with self.assertRaisesRegex(ValueError, "^job$"):
                    await run_job(pool, handler, job, metrics)

                handler.assert_not_awaited()
                self.assertEqual(pool.acquired, [])
                self.assertEqual(pool.released, [])
                self.assertEqual(metrics, {})

    async def test_rejection_releases_lease_without_counting_success(self):
        pool = Pool()
        metrics = {"success": 2}
        handler = AsyncMock(side_effect=JobRejected("declined"))

        self.assertEqual(await run_job(pool, handler, "job", metrics), {"status": "rejected"})

        self.assertEqual(metrics, {"success": 2})
        self.assertEqual(pool.released, ["job"])
        self.assertEqual(pool.active, set())
        self.assertEqual(await run_job(pool, AsyncMock(return_value=7), "job", metrics), 7)
        self.assertEqual(metrics, {"success": 3})
        self.assertEqual(pool.released, ["job", "job"])

    async def test_handler_failures_propagate_unchanged_and_release_lease(self):
        for error in (ValueError("invalid"), RuntimeError("failed"), asyncio.CancelledError("cancelled")):
            with self.subTest(error=error):
                pool = Pool()
                metrics = {}

                with self.assertRaises(type(error)) as raised:
                    await run_job(pool, AsyncMock(side_effect=error), "job", metrics)

                self.assertIs(raised.exception, error)
                self.assertEqual(metrics, {})
                self.assertEqual(pool.active, set())
                self.assertEqual(pool.released, ["job"])

    async def test_task_cancellation_releases_lease(self):
        pool = Pool()
        metrics = {}
        started = asyncio.Event()
        blocker = asyncio.Event()

        async def handler(job):
            started.set()
            await blocker.wait()

        task = asyncio.create_task(run_job(pool, handler, "job", metrics))
        try:
            await started.wait()
            self.assertEqual(pool.active, {"job"})
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(task.cancelled())
            self.assertEqual(metrics, {})
            self.assertEqual(pool.active, set())
            self.assertEqual(pool.released, ["job"])
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_acquisition_failures_do_not_call_handler(self):
        for error in (RuntimeError("unavailable"), JobRejected("acquisition rejected"), asyncio.CancelledError()):
            with self.subTest(error=error):
                pool = Pool()
                pool.error = error
                handler = AsyncMock()
                metrics = {}

                with self.assertRaises(type(error)) as raised:
                    await run_job(pool, handler, "job", metrics)

                self.assertIs(raised.exception, error)
                handler.assert_not_awaited()
                self.assertEqual(pool.acquired, [])
                self.assertEqual(pool.released, [])
                self.assertEqual(pool.active, set())
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
            await started.wait()
            duplicate_handler = AsyncMock()
            with self.assertRaises(LeaseBusy):
                await run_job(pool, duplicate_handler, "job", metrics)
            duplicate_handler.assert_not_awaited()
            self.assertEqual(pool.active, {"job"})
            self.assertEqual(metrics, {})

            self.assertEqual(await run_job(pool, AsyncMock(return_value="other"), "other", metrics), "other")
            self.assertEqual(pool.active, {"job"})

            finish.set()
            self.assertEqual(await task, "first")
            self.assertEqual(metrics, {"success": 2})
            self.assertEqual(pool.active, set())
            self.assertEqual(pool.acquired, ["job", "other"])
            self.assertEqual(pool.released, ["other", "job"])
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_cleanup_failure_preserves_completed_handler_count(self):
        for error in (RuntimeError("cleanup failed"), JobRejected("cleanup rejected"), asyncio.CancelledError()):
            with self.subTest(error=error):
                pool = Pool()
                metrics = {}
                close = Lease.close

                async def failing_close(lease):
                    await close(lease)
                    raise error

                with patch.object(Lease, "close", failing_close):
                    with self.assertRaises(type(error)) as raised:
                        await run_job(pool, AsyncMock(return_value=1), "job", metrics)

                self.assertIs(raised.exception, error)
                self.assertEqual(metrics, {"success": 1})
                self.assertEqual(pool.active, set())
                self.assertEqual(pool.released, ["job"])


if __name__ == "__main__":
    unittest.main()
