import asyncio
import unittest
from unittest.mock import AsyncMock

from app import run_job
from leases import JobRejected, LeaseBusy, Pool


class RunJobTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_returns_result_and_counts_completion(self):
        for metrics, expected in (({}, 1), ({"success": 4}, 5)):
            with self.subTest(metrics=metrics):
                pool = Pool()
                result = object()
                handler = AsyncMock(return_value=result)

                actual = await run_job(pool, handler, "job", metrics)

                self.assertIs(actual, result)
                handler.assert_awaited_once_with("job")
                self.assertEqual(metrics["success"], expected)
                self.assertEqual(pool.acquired, ["job"])
                self.assertEqual(pool.released, ["job"])
                self.assertEqual(pool.active, set())

    async def test_rejection_releases_lease_without_counting_success(self):
        pool = Pool()
        handler = AsyncMock(side_effect=JobRejected("rejected"))
        metrics = {"success": 3}

        result = await run_job(pool, handler, "job", metrics)

        self.assertEqual(result, {"status": "rejected"})
        handler.assert_awaited_once_with("job")
        self.assertEqual(metrics, {"success": 3})
        self.assertEqual(pool.released, ["job"])
        self.assertEqual(pool.active, set())

    async def test_unexpected_errors_propagate_and_release_lease(self):
        for error in (ValueError("invalid"), RuntimeError("failed"),
                      asyncio.CancelledError("cancelled")):
            with self.subTest(error=error):
                pool = Pool()
                handler = AsyncMock(side_effect=error)
                metrics = {}

                with self.assertRaises(type(error)) as raised:
                    await run_job(pool, handler, "job", metrics)

                self.assertIs(raised.exception, error)
                self.assertEqual(metrics, {})
                self.assertEqual(pool.released, ["job"])
                self.assertEqual(pool.active, set())

    async def test_task_cancellation_releases_lease(self):
        pool = Pool()
        metrics = {}
        started = asyncio.Event()

        async def handler(job):
            started.set()
            await asyncio.Future()

        task = asyncio.create_task(run_job(pool, handler, "job", metrics))
        try:
            await started.wait()
            self.assertEqual(pool.active, {"job"})
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(task.cancelled())
            self.assertEqual(metrics, {})
            self.assertEqual(pool.released, ["job"])
            self.assertEqual(pool.active, set())
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_acquire_failure_never_calls_handler(self):
        for error in (LeaseBusy("busy"), JobRejected("unavailable"),
                      RuntimeError("failed"), asyncio.CancelledError("cancelled")):
            with self.subTest(error=error):
                pool = Pool()
                pool.error = error
                handler = AsyncMock()
                metrics = {}

                with self.assertRaises(type(error)) as raised:
                    await run_job(pool, handler, "job", metrics)

                self.assertIs(raised.exception, error)
                handler.assert_not_called()
                self.assertEqual(metrics, {})
                self.assertEqual(pool.acquired, [])
                self.assertEqual(pool.released, [])
                self.assertEqual(pool.active, set())

    async def test_invalid_job_never_acquires_or_calls_handler(self):
        for job in (None, 0, [], "", " \t\n"):
            with self.subTest(job=job):
                pool = AsyncMock()
                handler = AsyncMock()
                metrics = {}

                with self.assertRaisesRegex(ValueError, "^job$"):
                    await run_job(pool, handler, job, metrics)

                pool.acquire.assert_not_called()
                handler.assert_not_called()
                self.assertEqual(metrics, {})

    async def test_same_job_is_exclusive_until_handler_finishes(self):
        pool = Pool()
        metrics = {}
        started = asyncio.Event()
        finish = asyncio.Event()

        async def handler(job):
            started.set()
            await finish.wait()
            return "done"

        task = asyncio.create_task(run_job(pool, handler, "job", metrics))
        try:
            await started.wait()
            competing_handler = AsyncMock()
            with self.assertRaises(LeaseBusy):
                await run_job(pool, competing_handler, "job", metrics)
            competing_handler.assert_not_called()
            self.assertEqual(pool.active, {"job"})
            self.assertEqual(pool.released, [])
            finish.set()
            self.assertEqual(await task, "done")
            self.assertEqual(metrics, {"success": 1})

            next_handler = AsyncMock(return_value="next")
            self.assertEqual(await run_job(pool, next_handler, "job", metrics), "next")
            self.assertEqual(metrics, {"success": 2})
            self.assertEqual(pool.acquired, ["job", "job"])
            self.assertEqual(pool.released, ["job", "job"])
            self.assertEqual(pool.active, set())
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_cancellation_during_release_still_counts_completed_handler(self):
        pool = Pool()
        metrics = {}
        result = object()

        async def handler(job):
            asyncio.get_running_loop().call_soon(asyncio.current_task().cancel)
            return result

        task = asyncio.create_task(run_job(pool, handler, "job", metrics))
        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertTrue(task.cancelled())
        self.assertEqual(metrics, {"success": 1})
        self.assertEqual(pool.released, ["job"])
        self.assertEqual(pool.active, set())


if __name__ == "__main__":
    unittest.main()
