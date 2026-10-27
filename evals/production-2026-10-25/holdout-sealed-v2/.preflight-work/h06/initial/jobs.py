from dataclasses import dataclass
from threading import Lock

class DeliveryUnknown(RuntimeError):
    pass
class NotAccepted(ConnectionError):
    pass
class NeedsReconciliation(RuntimeError):
    pass
class InProgress(RuntimeError):
    pass

@dataclass(frozen=True)
class Record:
    payload: str
    state: str
    receipt: object = None

class Store:
    def __init__(self):
        self.rows = {}
        self.lock = Lock()

    def claim(self, job_id, payload):
        with self.lock:
            old = self.rows.get(job_id)
            if old is not None:
                if old.payload != payload:
                    raise ValueError("payload conflict")
                if old.state == "sent":
                    return old
                if old.state == "unknown":
                    raise NeedsReconciliation(job_id)
                raise InProgress(job_id)
            self.rows[job_id] = Record(payload, "pending")
            return None

    def finish(self, job_id, payload, receipt):
        with self.lock:
            self.rows[job_id] = Record(payload, "sent", receipt)

    def unknown(self, job_id, payload):
        with self.lock:
            self.rows[job_id] = Record(payload, "unknown")

    def release(self, job_id):
        with self.lock:
            self.rows.pop(job_id, None)
