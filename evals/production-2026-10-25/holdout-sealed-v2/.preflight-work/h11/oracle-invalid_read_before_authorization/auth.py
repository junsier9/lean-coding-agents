class PermissionDenied(PermissionError):
    pass

class Authorizer:
    def __init__(self):
        self.grants = set()
        self.calls = []

    def ensure(self, principal, tenant):
        self.calls.append((principal, tenant))
        if not isinstance(principal, str) or not principal.strip():
            raise PermissionDenied("authentication required")
        if (principal, tenant) not in self.grants:
            raise PermissionDenied("tenant denied")

class Repository:
    def __init__(self):
        self.rows = {}
        self.calls = []
        self.error = None

    def get(self, tenant, doc_id):
        self.calls.append((tenant, doc_id))
        if self.error:
            raise self.error
        return self.rows.get((tenant, doc_id))
