import asyncio
import unittest
from app import run_job
from leases import Pool, JobRejected, LeaseBusy

class Contract(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pool, self.metrics = Pool(), {}

    async def test_success(self):
        async def handler(job):
            return "result:" + job
        self.assertEqual(await run_job(self.pool, handler, "A", self.metrics), "result:A")
        self.assertEqual(self.metrics, {"success": 1})
        self.assertEqual(self.pool.released, ["A"])
        self.assertEqual(self.pool.active, set())

    async def test_expected_rejection(self):
        async def handler(job):
            raise JobRejected("policy")
        self.assertEqual(await run_job(self.pool, handler, "A", self.metrics), {"status": "rejected"})
        self.assertEqual(self.pool.released, ["A"])
        self.assertEqual(self.metrics.get("success", 0), 0)

    async def test_unexpected_propagates(self):
        error = RuntimeError("bug")
        async def handler(job):
            raise error
        with self.assertRaises(RuntimeError) as found:
            await run_job(self.pool, handler, "A", self.metrics)
        self.assertIs(found.exception, error)
        self.assertEqual(self.pool.released, ["A"])

    async def test_cancelled_error_propagates(self):
        async def handler(job):
            raise asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await run_job(self.pool, handler, "A", self.metrics)
        self.assertEqual(self.pool.released, ["A"])
        self.assertEqual(self.metrics.get("success", 0), 0)

    async def test_real_task_cancellation(self):
        entered = asyncio.Event()
        async def handler(job):
            entered.set()
            await asyncio.Event().wait()
        task = asyncio.create_task(run_job(self.pool, handler, "A", self.metrics))
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.pool.active, set())
        self.assertEqual(self.pool.released, ["A"])

    async def test_acquire_failure_no_handler(self):
        calls = []
        self.pool.error = OSError("pool")
        async def handler(job):
            calls.append(job)
        with self.assertRaises(OSError):
            await run_job(self.pool, handler, "A", self.metrics)
        self.assertEqual(calls, [])
        self.assertEqual(self.pool.released, [])

    async def test_existing_lease_preserved(self):
        outer = await self.pool.acquire("A")
        async def handler(job):
            self.fail("handler cannot run")
        with self.assertRaises(LeaseBusy):
            await run_job(self.pool, handler, "A", self.metrics)
        self.assertEqual(self.pool.active, {"A"})
        await outer.close()

    async def test_reacquire_after_failure(self):
        async def fail(job):
            raise JobRejected()
        async def succeed(job):
            return 3
        await run_job(self.pool, fail, "A", self.metrics)
        self.assertEqual(await run_job(self.pool, succeed, "A", self.metrics), 3)
        self.assertEqual(self.pool.released, ["A", "A"])

    async def test_metrics_not_reset(self):
        self.metrics["success"] = 4
        async def handler(job):
            return 1
        await run_job(self.pool, handler, "A", self.metrics)
        self.assertEqual(self.metrics["success"], 5)

    async def test_invalid_job(self):
        async def handler(job):
            self.fail("invalid input")
        for job in (None, "", " ", 3):
            with self.subTest(job=job), self.assertRaises(ValueError):
                await run_job(self.pool, handler, job, self.metrics)
        self.assertEqual(self.pool.acquired, [])
