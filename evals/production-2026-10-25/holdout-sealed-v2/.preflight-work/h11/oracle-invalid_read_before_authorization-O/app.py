from auth import PermissionDenied

class TenantService:
    def __init__(self, repository, authorizer):
        self.repository = repository
        self.authorizer = authorizer
        self.cache = {}

    def get(self, principal, tenant, doc_id):
        if not isinstance(tenant, str) or not tenant.strip():
            raise ValueError("tenant")
        if not isinstance(doc_id, str) or not doc_id.strip():
            raise ValueError("doc_id")
        self.cache.get((tenant, doc_id))
        self.authorizer.ensure(principal, tenant)
        key = (tenant, doc_id)
        if key not in self.cache:
            self.cache[key] = self.repository.get(tenant, doc_id)
        return self.cache[key]
