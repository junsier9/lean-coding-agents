import unittest

from app import TenantService
from auth import Authorizer, PermissionDenied, Repository


class TenantServiceTests(unittest.TestCase):
    def setUp(self):
        self.repository = Repository()
        self.authorizer = Authorizer()
        self.authorizer.grants.add(("alice", "tenant-a"))
        self.service = TenantService(self.repository, self.authorizer)

    def test_cache_hit_rechecks_permission_without_reading_repository(self):
        document = {"title": "Document A"}
        self.repository.rows[("tenant-a", "doc")] = document

        self.assertIs(self.service.get("alice", "tenant-a", "doc"), document)
        self.assertIs(self.service.get("alice", "tenant-a", "doc"), document)

        self.assertEqual(self.authorizer.calls, [("alice", "tenant-a")] * 2)
        self.assertEqual(self.repository.calls, [("tenant-a", "doc")])

    def test_revocation_blocks_cached_document(self):
        self.repository.rows[("tenant-a", "doc")] = "document"
        self.service.get("alice", "tenant-a", "doc")
        self.authorizer.grants.remove(("alice", "tenant-a"))

        with self.assertRaises(PermissionDenied):
            self.service.get("alice", "tenant-a", "doc")

        self.assertEqual(self.authorizer.calls, [("alice", "tenant-a")] * 2)
        self.assertEqual(self.repository.calls, [("tenant-a", "doc")])

    def test_cached_document_requires_current_principal_permission(self):
        self.repository.rows[("tenant-a", "doc")] = "document"
        self.service.get("alice", "tenant-a", "doc")

        with self.assertRaises(PermissionDenied):
            self.service.get("bob", "tenant-a", "doc")

        self.authorizer.grants.add(("bob", "tenant-a"))
        self.assertEqual(self.service.get("bob", "tenant-a", "doc"), "document")
        self.assertEqual(self.repository.calls, [("tenant-a", "doc")])

    def test_same_document_id_is_isolated_between_tenants(self):
        self.authorizer.grants.add(("alice", "tenant-b"))
        self.repository.rows[("tenant-a", "doc")] = "document A"
        self.repository.rows[("tenant-b", "doc")] = "document B"

        for _ in range(2):
            self.assertEqual(self.service.get("alice", "tenant-a", "doc"), "document A")
            self.assertEqual(self.service.get("alice", "tenant-b", "doc"), "document B")

        self.assertEqual(self.repository.calls, [("tenant-a", "doc"), ("tenant-b", "doc")])
        self.assertEqual(len(self.authorizer.calls), 4)

    def test_missing_document_is_cached_and_still_requires_permission(self):
        self.assertIsNone(self.service.get("alice", "tenant-a", "missing"))
        self.assertIsNone(self.service.get("alice", "tenant-a", "missing"))
        self.assertEqual(self.repository.calls, [("tenant-a", "missing")])
        self.assertEqual(self.authorizer.calls, [("alice", "tenant-a")] * 2)

        self.authorizer.grants.clear()
        with self.assertRaises(PermissionDenied):
            self.service.get("alice", "tenant-a", "missing")

    def test_repository_errors_propagate_unchanged_and_are_not_cached(self):
        for error in (RuntimeError("offline"), KeyError("backend failure")):
            with self.subTest(error=type(error).__name__):
                repository = Repository()
                service = TenantService(repository, self.authorizer)
                repository.error = error

                for _ in range(2):
                    with self.assertRaises(type(error)) as caught:
                        service.get("alice", "tenant-a", "doc")
                    self.assertIs(caught.exception, error)

                repository.error = None
                repository.rows[("tenant-a", "doc")] = "recovered"
                self.assertEqual(service.get("alice", "tenant-a", "doc"), "recovered")
                self.assertEqual(service.get("alice", "tenant-a", "doc"), "recovered")
                self.assertEqual(repository.calls, [("tenant-a", "doc")] * 3)

    def test_invalid_principal_is_denied_on_cache_misses_and_hits(self):
        for warm_cache in (False, True):
            for principal in (None, "", " \t\n", 42, [], {}):
                with self.subTest(warm_cache=warm_cache, principal=principal):
                    repository = Repository()
                    service = TenantService(repository, self.authorizer)
                    if warm_cache:
                        service.get("alice", "tenant-a", "doc")
                    calls_before = list(repository.calls)

                    with self.assertRaises(PermissionDenied):
                        service.get(principal, "tenant-a", "doc")

                    self.assertEqual(repository.calls, calls_before)
                    self.assertEqual(self.authorizer.calls[-1], (principal, "tenant-a"))

    def test_tenant_and_document_id_must_be_nonempty_strings(self):
        for field in ("tenant", "doc_id"):
            for invalid in (None, "", " \t\n", 42, [], {}):
                with self.subTest(field=field, invalid=invalid):
                    arguments = {"principal": "alice", "tenant": "tenant-a", "doc_id": "doc"}
                    arguments[field] = invalid
                    with self.assertRaises(ValueError) as caught:
                        self.service.get(**arguments)
                    self.assertEqual(str(caught.exception), field)

        self.assertEqual(self.repository.calls, [])

    def test_authorization_runs_before_any_cache_or_repository_access(self):
        service = self.service

        class GuardedCache(dict):
            def __contains__(self, key):
                if not service.authorizer.calls:
                    raise AssertionError("cache accessed before authorization")
                return super().__contains__(key)

        service.cache = GuardedCache()
        self.assertIsNone(service.get("alice", "tenant-a", "doc"))

        service.authorizer.calls.clear()
        self.assertIsNone(service.get("alice", "tenant-a", "doc"))
        self.assertEqual(self.repository.calls, [("tenant-a", "doc")])


if __name__ == "__main__":
    unittest.main()
