import asyncio
import unittest
from unittest.mock import patch

from app import run_job
from leases import JobRejected, Lease, LeaseBusy, Pool


class RunJobTests(unittest.IsolatedAsyncioTestCase):
    def assert_released(self, pool, job="job"):
        self.assertEqual(pool.active, set())
        self.assertEqual(pool.acquired, [job])
        self.assertEqual(pool.released, [job])

    async def test_success_returns_result_and_counts_each_completion(self):
        pool = Pool()
        metrics = {}
        result = object()

        async def handler(job):
            self.assertEqual(job, "job")
            self.assertEqual(pool.active, {job})
            return result

        self.assertIs(await run_job(pool, handler, "job", metrics), result)
        self.assert_released(pool)
        self.assertEqual(metrics, {"success": 1})
        self.assertIs(await run_job(pool, handler, "job", metrics), result)
        self.assertEqual(metrics, {"success": 2})
        self.assertEqual(pool.active, set())
        self.assertEqual(pool.released, ["job", "job"])

    async def test_rejection_releases_without_counting_success(self):
        for initial in ({}, {"success": 4}):
            with self.subTest(initial=initial):
                pool = Pool()
                metrics = initial.copy()

                async def handler(job):
                    raise JobRejected(job)

                self.assertEqual(
                    await run_job(pool, handler, "job", metrics),
                    {"status": "rejected"},
                )
                self.assertEqual(metrics, initial)
                self.assert_released(pool)

    async def test_other_exceptions_propagate_unchanged_and_release(self):
        class HandlerAbort(BaseException):
            pass

        for error in (ValueError("bad"), RuntimeError("failed"),
                      HandlerAbort("abort"), asyncio.CancelledError("cancelled")):
            with self.subTest(error=type(error).__name__):
                pool = Pool()
                metrics = {"success": 4}

                async def handler(job):
                    raise error

                with self.assertRaises(type(error)) as raised:
                    await run_job(pool, handler, "job", metrics)
                self.assertIs(raised.exception, error)
                self.assertEqual(metrics, {"success": 4})
                self.assert_released(pool)

    async def test_task_cancellation_releases_and_allows_same_job_again(self):
        pool = Pool()
        metrics = {}
        started = asyncio.Event()
        blocker = asyncio.Event()

        async def handler(job):
            started.set()
            await blocker.wait()

        task = asyncio.create_task(run_job(pool, handler, "job", metrics))
        try:
            await asyncio.wait_for(started.wait(), 1)
            task.cancel("stop")
            with self.assertRaises(asyncio.CancelledError) as raised:
                await task
            self.assertEqual(raised.exception.args, ("stop",))
            self.assertTrue(task.cancelled())
            self.assert_released(pool)
            self.assertEqual(metrics, {})
            blocker.set()
            self.assertIsNone(await run_job(pool, handler, "job", metrics))
            self.assertEqual(metrics, {"success": 1})
            self.assertEqual(pool.active, set())
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_acquire_failure_never_calls_handler(self):
        for error in (LeaseBusy("job"), JobRejected("acquisition"),
                      RuntimeError("acquisition"), asyncio.CancelledError("stop")):
            with self.subTest(error=type(error).__name__):
                pool = Pool()
                pool.error = error
                metrics = {"success": 4}

                async def handler(job):
                    self.fail("handler called after acquisition failure")

                with self.assertRaises(type(error)) as raised:
                    await run_job(pool, handler, "job", metrics)
                self.assertIs(raised.exception, error)
                self.assertEqual(metrics, {"success": 4})
                self.assertEqual(pool.active, set())
                self.assertEqual(pool.acquired, [])
                self.assertEqual(pool.released, [])

    async def test_cancellation_during_release_counts_completed_handler(self):
        pool = Pool()
        metrics = {}

        async def handler(job):
            asyncio.get_running_loop().call_soon(
                asyncio.current_task().cancel, "during release"
            )
            return "done"

        task = asyncio.create_task(run_job(pool, handler, "job", metrics))
        with self.assertRaises(asyncio.CancelledError) as raised:
            await task
        self.assertEqual(raised.exception.args, ("during release",))
        self.assertEqual(metrics, {"success": 1})
        self.assert_released(pool)

    async def test_invalid_jobs_fail_before_acquiring(self):
        pool = Pool()
        metrics = {}

        async def handler(job):
            self.fail("handler called for invalid job")

        for job in (None, 1, b"job", "", " \t\n"):
            with self.subTest(job=job):
                with self.assertRaisesRegex(ValueError, "^job$"):
                    await run_job(pool, handler, job, metrics)
        self.assertEqual(metrics, {})
        self.assertEqual(pool.acquired, [])
        self.assertEqual(pool.released, [])

    async def test_same_job_is_exclusive_while_other_jobs_can_run(self):
        pool = Pool()
        metrics = {}
        started = asyncio.Event()
        unblock = asyncio.Event()
        calls = []

        async def handler(job):
            calls.append(job)
            if job == "job":
                started.set()
                await unblock.wait()
            return job

        task = asyncio.create_task(run_job(pool, handler, "job", metrics))
        try:
            await asyncio.wait_for(started.wait(), 1)
            with self.assertRaises(LeaseBusy):
                await run_job(pool, handler, "job", metrics)
            self.assertEqual(calls, ["job"])
            self.assertEqual(pool.active, {"job"})
            self.assertEqual(await run_job(pool, handler, "other", metrics), "other")
            self.assertEqual(pool.active, {"job"})
            unblock.set()
            self.assertEqual(await task, "job")
            self.assertEqual(calls, ["job", "other"])
            self.assertEqual(metrics, {"success": 2})
            self.assertEqual(pool.active, set())
            self.assertEqual(pool.acquired, ["job", "other"])
            self.assertEqual(pool.released, ["other", "job"])
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_lease_context_receives_handler_exception(self):
        events = []
        error = RuntimeError("handler failed")

        class ObservedLease(Lease):
            async def __aenter__(self):
                events.append("enter")
                return await super().__aenter__()

            async def __aexit__(self, kind, value, traceback):
                events.append((kind, value))
                return await super().__aexit__(kind, value, traceback)

        pool = Pool()

        async def handler(job):
            events.append("handler")
            raise error

        with patch("leases.Lease", ObservedLease):
            with self.assertRaises(RuntimeError) as raised:
                await run_job(pool, handler, "job", {})
        self.assertIs(raised.exception, error)
        self.assertEqual(events, ["enter", "handler", (RuntimeError, error)])
        self.assert_released(pool)

    async def test_metrics_failure_still_releases_lease(self):
        error = RuntimeError("metrics unavailable")

        class BrokenMetrics(dict):
            def __setitem__(self, key, value):
                raise error

        pool = Pool()

        async def handler(job):
            return "done"

        with self.assertRaises(RuntimeError) as raised:
            await run_job(pool, handler, "job", BrokenMetrics())
        self.assertIs(raised.exception, error)
        self.assert_released(pool)


if __name__ == "__main__":
    unittest.main()
