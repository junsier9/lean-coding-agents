from leases import JobRejected, LeaseBusy

async def run_job(pool, handler, job, metrics):
    if not isinstance(job, str) or not job.strip():
        raise ValueError("job")
    lease = await pool.acquire(job)
    async with lease:
        try:
            result = await handler(job)
        except JobRejected:
            return {"status": "rejected"}
        metrics["success"] = metrics.get("success", 0) + 1
        return result
