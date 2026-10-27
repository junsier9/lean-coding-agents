from memo import memoized_method

class QuoteError(ValueError):
    pass

class RateUnavailable(RuntimeError):
    pass

class Account:
    def __init__(account, rate, backend=None):
        account.rate = rate
        account.backend = backend
        account.evaluations = 0

    @memoized_method
    def quote(account, units, *, fee=0):
        """Return a quote in integer cents using this account's rate."""
        account.evaluations += 1
        if type(units) is not int or units < 0:
            raise QuoteError("units")
        if type(fee) is not int or fee < 0:
            raise QuoteError("fee")
        rate = account.backend() if account.backend is not None else account.rate
        return rate * units + fee
