import time
from gateway import TransientError, DeclinedError, InvoiceRequest, validate_attempts

def create_invoice(transport, amount, key, max_attempts=3, sleep=time.sleep):
    request = InvoiceRequest(amount, key)
    validate_attempts(max_attempts)
    for attempt in range(max_attempts):
        try:
            return transport(request.amount, request.key)
        except TransientError:
            if attempt + 1 == max_attempts:
                raise
            sleep(0.01 * (2 ** attempt))
