import asyncio
import unittest
from unittest.mock import AsyncMock

from app import run_job
from leases import JobRejected, Lease, LeaseBusy, Pool


class RunJobTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_returns_result_and_counts_each_completion(self):
        pool = Pool()
        metrics = {"success": 2}
        result = object()

        async def handler(job):
            self.assertEqual(job, "job")
            self.assertEqual(pool.active, {"job"})
            return result

        for expected in (3, 4):
            self.assertIs(await run_job(pool, handler, "job", metrics), result)
            self.assertEqual(metrics, {"success": expected})
            self.assertEqual(pool.active, set())
        self.assertEqual(pool.acquired, ["job", "job"])
        self.assertEqual(pool.released, pool.acquired)

    async def test_rejection_releases_without_counting_success(self):
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

    async def test_unexpected_exceptions_propagate_and_release(self):
        for error in (ValueError("handler"), RuntimeError("handler")):
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

    async def test_handler_cancellation_propagates_and_releases(self):
        pool = Pool()
        metrics = {}
        started = asyncio.Event()
        blocked = asyncio.Event()

        async def handler(job):
            started.set()
            await blocked.wait()

        task = asyncio.create_task(run_job(pool, handler, "job", metrics))
        try:
            await asyncio.wait_for(started.wait(), 5)
        finally:
            task.cancel("cancel job")
            with self.assertRaises(asyncio.CancelledError) as raised:
                await task

        self.assertEqual(raised.exception.args, ("cancel job",))
        self.assertEqual(metrics, {})
        self.assertEqual(pool.active, set())
        self.assertEqual(pool.released, ["job"])
        self.assertEqual(await run_job(pool, AsyncMock(return_value=7), "job", metrics), 7)
        self.assertEqual(metrics, {"success": 1})

    async def test_acquisition_failures_skip_handler(self):
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
                handler.assert_not_awaited()
                self.assertEqual(metrics, {})
                self.assertEqual(pool.acquired, [])
                self.assertEqual(pool.active, set())
                self.assertEqual(pool.released, [])

    async def test_same_job_exclusion_allows_other_jobs(self):
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
            await asyncio.wait_for(started.wait(), 5)
            duplicate = AsyncMock()
            with self.assertRaises(LeaseBusy):
                await run_job(pool, duplicate, "job", metrics)
            duplicate.assert_not_awaited()
            self.assertEqual(pool.active, {"job"})
            self.assertEqual(
                await run_job(pool, AsyncMock(return_value="other"), "other", metrics),
                "other",
            )
            self.assertEqual(pool.active, {"job"})
        finally:
            finish.set()
            result = await task

        self.assertEqual(result, "first")
        self.assertEqual(metrics, {"success": 2})
        self.assertEqual(pool.active, set())
        self.assertEqual(pool.acquired, ["job", "other"])
        self.assertEqual(pool.released, ["other", "job"])

    async def test_invalid_job_fails_before_acquisition(self):
        for job in (None, 0, "", " \t\n"):
            with self.subTest(job=job):
                pool = Pool()
                handler = AsyncMock()
                metrics = {}
                with self.assertRaisesRegex(ValueError, "^job$"):
                    await run_job(pool, handler, job, metrics)
                handler.assert_not_awaited()
                self.assertEqual(pool.acquired, [])
                self.assertEqual(metrics, {})

    async def test_cleanup_failure_preserves_handler_completion_count(self):
        for error in (JobRejected("cleanup"), asyncio.CancelledError("cleanup")):
            with self.subTest(error=type(error).__name__):
                pool = Pool()
                lease = await pool.acquire("job")
                close = lease.close

                async def failing_close():
                    await close()
                    raise error

                lease.close = failing_close
                pool.acquire = AsyncMock(return_value=lease)
                handler = AsyncMock(return_value="done")
                metrics = {}

                with self.assertRaises(type(error)) as raised:
                    await run_job(pool, handler, "job", metrics)

                self.assertIs(raised.exception, error)
                self.assertEqual(metrics, {"success": 1})
                self.assertEqual(pool.active, set())
                self.assertEqual(pool.released, ["job"])

    async def test_lease_context_lifecycle_is_used(self):
        pool = Pool()
        calls = []

        class RecordingLease(Lease):
            async def __aenter__(self):
                calls.append("enter")
                return await super().__aenter__()

            async def __aexit__(self, kind, value, traceback):
                calls.append(("exit", kind, value))
                return await super().__aexit__(kind, value, traceback)

        pool.active.add("job")
        pool.acquire = AsyncMock(return_value=RecordingLease(pool, "job"))
        error = RuntimeError("handler")
        handler = AsyncMock(side_effect=error)

        with self.assertRaises(RuntimeError):
            await run_job(pool, handler, "job", {})

        self.assertEqual(calls, ["enter", ("exit", RuntimeError, error)])
        self.assertEqual(pool.active, set())
        self.assertEqual(pool.released, ["job"])


if __name__ == "__main__":
    unittest.main()
