import asyncio

class JobRejected(ValueError):
    pass
class LeaseBusy(RuntimeError):
    pass

class Lease:
    def __init__(self, pool, job):
        self.pool, self.job, self.closed = pool, job, False

    async def __aenter__(self):
        return self

    async def __aexit__(self, kind, value, traceback):
        await self.close()
        return False

    async def close(self):
        if not self.closed:
            self.closed = True
            self.pool.active.remove(self.job)
            self.pool.released.append(self.job)
            await asyncio.sleep(0)

class Pool:
    def __init__(self):
        self.active = set()
        self.acquired, self.released = [], []
        self.error = None

    async def acquire(self, job):
        if self.error:
            raise self.error
        if job in self.active:
            raise LeaseBusy(job)
        self.active.add(job)
        self.acquired.append(job)
        return Lease(self, job)
