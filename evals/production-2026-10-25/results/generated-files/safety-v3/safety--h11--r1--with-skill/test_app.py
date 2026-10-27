import unittest

from app import TenantService
from auth import Authorizer, PermissionDenied, Repository


class TenantServiceTests(unittest.TestCase):
    def setUp(self):
        self.repository = Repository()
        self.authorizer = Authorizer()
        self.authorizer.grants.update({("alice", "tenant-a"), ("alice", "tenant-b")})
        self.service = TenantService(self.repository, self.authorizer)

    def test_cache_hit_reauthorizes_without_querying_repository(self):
        value = object()
        self.repository.rows[("tenant-a", "doc")] = value

        self.assertIs(self.service.get("alice", "tenant-a", "doc"), value)
        self.assertIs(self.service.get("alice", "tenant-a", "doc"), value)

        self.assertEqual(self.authorizer.calls, [("alice", "tenant-a")] * 2)
        self.assertEqual(self.repository.calls, [("tenant-a", "doc")])

    def test_cached_document_and_missing_document_respect_revocation(self):
        self.repository.rows[("tenant-a", "doc")] = "document"
        for doc_id in ("doc", "missing"):
            with self.subTest(doc_id=doc_id):
                self.authorizer.grants.add(("alice", "tenant-a"))
                self.service.get("alice", "tenant-a", doc_id)
                self.authorizer.grants.remove(("alice", "tenant-a"))

                with self.assertRaises(PermissionDenied):
                    self.service.get("alice", "tenant-a", doc_id)

        self.assertEqual(self.authorizer.calls, [("alice", "tenant-a")] * 4)
        self.assertEqual(self.repository.calls, [("tenant-a", "doc"), ("tenant-a", "missing")])

    def test_unauthorized_principal_cannot_read_cached_or_uncached_document(self):
        self.repository.rows[("tenant-a", "doc")] = "document"
        self.service.get("alice", "tenant-a", "doc")

        for doc_id in ("doc", "missing"):
            with self.subTest(doc_id=doc_id):
                with self.assertRaises(PermissionDenied):
                    self.service.get("bob", "tenant-a", doc_id)

        self.assertEqual(self.authorizer.calls[-2:], [("bob", "tenant-a")] * 2)
        self.assertEqual(self.repository.calls, [("tenant-a", "doc")])
        self.assertEqual(len(self.service.cache), 1)

    def test_same_document_id_is_cached_separately_for_each_tenant(self):
        self.repository.rows[("tenant-a", "doc")] = "document a"
        self.repository.rows[("tenant-b", "doc")] = "document b"

        for _ in range(2):
            self.assertEqual(self.service.get("alice", "tenant-a", "doc"), "document a")
            self.assertEqual(self.service.get("alice", "tenant-b", "doc"), "document b")

        self.assertEqual(self.repository.calls, [("tenant-a", "doc"), ("tenant-b", "doc")])
        self.assertEqual(len(self.authorizer.calls), 4)

    def test_missing_document_is_cached_without_hiding_another_tenants_document(self):
        self.repository.rows[("tenant-b", "doc")] = "document b"

        self.assertIsNone(self.service.get("alice", "tenant-a", "doc"))
        self.assertIsNone(self.service.get("alice", "tenant-a", "doc"))
        self.assertEqual(self.service.get("alice", "tenant-b", "doc"), "document b")

        self.assertEqual(self.repository.calls, [("tenant-a", "doc"), ("tenant-b", "doc")])
        self.assertEqual(len(self.authorizer.calls), 3)

    def test_invalid_principal_is_rejected_on_cache_hits_and_misses(self):
        self.repository.rows[("tenant-a", "doc")] = "document"
        self.service.get("alice", "tenant-a", "doc")
        for principal in (None, "", " \t", 0, [], {}):
            for doc_id in ("doc", "missing"):
                with self.subTest(principal=principal, doc_id=doc_id):
                    with self.assertRaisesRegex(PermissionDenied, "authentication required"):
                        self.service.get(principal, "tenant-a", doc_id)
                    self.assertEqual(self.authorizer.calls[-1], (principal, "tenant-a"))

        self.assertEqual(self.repository.calls, [("tenant-a", "doc")])
        self.assertEqual(len(self.service.cache), 1)

    def test_invalid_tenant_and_document_id_are_rejected(self):
        for invalid in (None, "", " \t", 0, [], {}):
            with self.subTest(field="tenant", value=invalid):
                with self.assertRaisesRegex(ValueError, "^tenant$"):
                    self.service.get("alice", invalid, "doc")
            with self.subTest(field="doc_id", value=invalid):
                with self.assertRaisesRegex(ValueError, "^doc_id$"):
                    self.service.get("alice", "tenant-a", invalid)

        self.assertEqual(self.repository.calls, [])
        self.assertEqual(self.service.cache, {})

    def test_repository_errors_propagate_unchanged_without_being_cached(self):
        for error in (RuntimeError("unavailable"), KeyError("backend failure")):
            with self.subTest(error=error):
                self.repository.error = error
                with self.assertRaises(type(error)) as raised:
                    self.service.get("alice", "tenant-a", "doc")
                self.assertIs(raised.exception, error)
                self.assertEqual(self.service.cache, {})

        self.repository.error = None
        self.repository.rows[("tenant-a", "doc")] = "recovered"
        self.assertEqual(self.service.get("alice", "tenant-a", "doc"), "recovered")
        self.assertEqual(self.service.get("alice", "tenant-a", "doc"), "recovered")
        self.assertEqual(self.repository.calls, [("tenant-a", "doc")] * 3)
        self.assertEqual(self.authorizer.calls, [("alice", "tenant-a")] * 4)


if __name__ == "__main__":
    unittest.main()
