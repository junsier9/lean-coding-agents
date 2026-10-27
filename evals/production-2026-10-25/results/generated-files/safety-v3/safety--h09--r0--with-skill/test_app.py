import asyncio
import unittest
from unittest.mock import AsyncMock

from app import run_job
from leases import JobRejected, LeaseBusy, Pool


class RunJobTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_returns_result_and_increments_count(self):
        for initial_count in (None, 2):
            with self.subTest(initial_count=initial_count):
                pool = Pool()
                metrics = {} if initial_count is None else {"success": initial_count}
                result = object()
                handler = AsyncMock(return_value=result)

                self.assertIs(await run_job(pool, handler, "job", metrics), result)

                handler.assert_awaited_once_with("job")
                self.assertEqual(metrics, {"success": (initial_count or 0) + 1})
                self.assertEqual(pool.active, set())
                self.assertEqual(pool.acquired, ["job"])
                self.assertEqual(pool.released, ["job"])

    async def test_rejection_releases_lease_without_counting_success(self):
        pool = Pool()
        metrics = {"success": 2}
        handler = AsyncMock(side_effect=JobRejected("rejected"))

        self.assertEqual(
            await run_job(pool, handler, "job", metrics), {"status": "rejected"}
        )

        handler.assert_awaited_once_with("job")
        self.assertEqual(metrics, {"success": 2})
        self.assertEqual(pool.active, set())
        self.assertEqual(pool.released, ["job"])

    async def test_handler_errors_propagate_and_release_lease(self):
        for error in (RuntimeError("handler"), asyncio.CancelledError("handler")):
            with self.subTest(error=type(error).__name__):
                pool = Pool()
                metrics = {}
                handler = AsyncMock(side_effect=error)

                with self.assertRaises(type(error)) as raised:
                    await run_job(pool, handler, "job", metrics)

                self.assertIs(raised.exception, error)
                self.assertEqual(metrics, {})
                self.assertEqual(pool.active, set())
                self.assertEqual(pool.released, ["job"])

    async def test_acquisition_failure_propagates_without_calling_handler(self):
        for error in (
            LeaseBusy("job"),
            JobRejected("acquire"),
            RuntimeError("acquire"),
            asyncio.CancelledError("acquire"),
        ):
            with self.subTest(error=type(error).__name__):
                pool = Pool()
                pool.error = error
                metrics = {}
                handler = AsyncMock()

                with self.assertRaises(type(error)) as raised:
                    await run_job(pool, handler, "job", metrics)

                self.assertIs(raised.exception, error)
                handler.assert_not_called()
                self.assertEqual(metrics, {})
                self.assertEqual(pool.active, set())
                self.assertEqual(pool.acquired, [])
                self.assertEqual(pool.released, [])

    async def test_invalid_job_fails_before_acquiring_lease(self):
        for job in (None, 1, "", " \t\n"):
            with self.subTest(job=job):
                pool = Pool()
                handler = AsyncMock()

                with self.assertRaisesRegex(ValueError, "^job$"):
                    await run_job(pool, handler, job, {})

                handler.assert_not_called()
                self.assertEqual(pool.acquired, [])
                self.assertEqual(pool.released, [])

    async def test_task_cancellation_releases_lease_and_allows_reacquisition(self):
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
            task.cancel("stop")
            with self.assertRaises(asyncio.CancelledError) as raised:
                await task
            self.assertEqual(raised.exception.args, ("stop",))
            self.assertTrue(task.cancelled())
            self.assertEqual(metrics, {})
            self.assertEqual(pool.active, set())
            self.assertEqual(pool.released, ["job"])

            self.assertEqual(
                await run_job(pool, AsyncMock(return_value="done"), "job", metrics),
                "done",
            )
            self.assertEqual(metrics, {"success": 1})
            self.assertEqual(pool.active, set())
            self.assertEqual(pool.acquired, ["job", "job"])
            self.assertEqual(pool.released, ["job", "job"])
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

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
            await asyncio.wait_for(started.wait(), timeout=1)
            competing_handler = AsyncMock()
            with self.assertRaises(LeaseBusy):
                await run_job(pool, competing_handler, "job", metrics)

            competing_handler.assert_not_called()
            self.assertEqual(pool.active, {"job"})
            self.assertEqual(pool.acquired, ["job"])
            self.assertEqual(pool.released, [])
            self.assertEqual(metrics, {})
            finish.set()
            self.assertEqual(await task, "done")
            self.assertEqual(metrics, {"success": 1})
            self.assertEqual(pool.active, set())
            self.assertEqual(pool.released, ["job"])
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_completed_handler_is_counted_when_cleanup_is_cancelled(self):
        pool = Pool()
        metrics = {}

        async def handler(job):
            asyncio.current_task().cancel("cleanup")
            return "done"

        task = asyncio.create_task(run_job(pool, handler, "job", metrics))
        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertTrue(task.cancelled())
        self.assertEqual(metrics, {"success": 1})
        self.assertEqual(pool.active, set())
        self.assertEqual(pool.released, ["job"])


if __name__ == "__main__":
    unittest.main()
