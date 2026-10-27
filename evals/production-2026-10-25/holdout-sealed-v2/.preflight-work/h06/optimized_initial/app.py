from jobs import DeliveryUnknown, NotAccepted, NeedsReconciliation, InProgress

def dispatch(store, send, job_id, payload):
    if not isinstance(job_id, str) or not job_id.strip():
        raise ValueError("job_id")
    if not isinstance(payload, str) or not payload.strip():
        raise ValueError("payload")
    old = store.claim(job_id, payload)
    if old is not None:
        return send(payload)
    for attempt in range(2):
        try:
            receipt = send(payload)
            store.finish(job_id, payload, receipt)
            return receipt
        except Exception:
            if attempt == 1:
                store.release(job_id)
                raise
