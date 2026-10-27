import unittest
from unittest.mock import patch

from app import TenantService
from auth import Authorizer, PermissionDenied, Repository


class TenantServiceTests(unittest.TestCase):
    def setUp(self):
        self.repository = Repository()
        self.authorizer = Authorizer()
        self.authorizer.grants.add(("alice", "tenant-a"))
        self.service = TenantService(self.repository, self.authorizer)
        self.repository.rows[("tenant-a", "doc")] = "document A"

    def test_cache_hit_rechecks_authorization_without_querying_repository(self):
        self.assertEqual(self.service.get("alice", "tenant-a", "doc"), "document A")
        self.assertEqual(self.service.get("alice", "tenant-a", "doc"), "document A")
        self.assertEqual(self.repository.calls, [("tenant-a", "doc")])
        self.assertEqual(self.authorizer.calls, [("alice", "tenant-a")] * 2)

    def test_revocation_blocks_cached_document(self):
        self.service.get("alice", "tenant-a", "doc")
        self.authorizer.grants.clear()

        with self.assertRaisesRegex(PermissionDenied, "tenant denied"):
            self.service.get("alice", "tenant-a", "doc")

        self.assertEqual(self.repository.calls, [("tenant-a", "doc")])
        self.assertEqual(self.authorizer.calls, [("alice", "tenant-a")] * 2)

    def test_another_principal_cannot_read_cached_document(self):
        self.service.get("alice", "tenant-a", "doc")

        with self.assertRaisesRegex(PermissionDenied, "tenant denied"):
            self.service.get("bob", "tenant-a", "doc")

        self.assertEqual(self.repository.calls, [("tenant-a", "doc")])
        self.assertEqual(self.authorizer.calls[-1], ("bob", "tenant-a"))

    def test_same_document_id_is_cached_separately_for_each_tenant(self):
        self.authorizer.grants.add(("alice", "tenant-b"))
        self.repository.rows[("tenant-b", "doc")] = "document B"

        for _ in range(2):
            self.assertEqual(self.service.get("alice", "tenant-a", "doc"), "document A")
            self.assertEqual(self.service.get("alice", "tenant-b", "doc"), "document B")

        self.assertEqual(self.repository.calls, [("tenant-a", "doc"), ("tenant-b", "doc")])
        self.assertEqual(len(self.authorizer.calls), 4)

    def test_missing_document_is_cached_and_reauthorized(self):
        self.assertIsNone(self.service.get("alice", "tenant-a", "missing"))
        self.assertIsNone(self.service.get("alice", "tenant-a", "missing"))
        self.assertEqual(self.repository.calls, [("tenant-a", "missing")])
        self.assertEqual(self.authorizer.calls, [("alice", "tenant-a")] * 2)

    def test_repository_failure_propagates_without_caching(self):
        error = RuntimeError("repository unavailable")
        self.repository.error = error

        for _ in range(2):
            with self.assertRaises(RuntimeError) as caught:
                self.service.get("alice", "tenant-a", "doc")
            self.assertIs(caught.exception, error)

        self.repository.error = None
        self.assertEqual(self.service.get("alice", "tenant-a", "doc"), "document A")
        self.assertEqual(self.repository.calls, [("tenant-a", "doc")] * 3)
        self.assertEqual(self.authorizer.calls, [("alice", "tenant-a")] * 3)

    def test_authorization_failure_propagates_on_cache_hit(self):
        self.service.get("alice", "tenant-a", "doc")
        error = RuntimeError("authorization unavailable")

        with patch.object(self.authorizer, "ensure", side_effect=error) as ensure:
            with self.assertRaises(RuntimeError) as caught:
                self.service.get("alice", "tenant-a", "doc")

        self.assertIs(caught.exception, error)
        ensure.assert_called_once_with("alice", "tenant-a")
        self.assertEqual(self.repository.calls, [("tenant-a", "doc")])

    def test_invalid_principal_is_rejected_with_or_without_cached_document(self):
        self.service.get("alice", "tenant-a", "doc")

        for doc_id in ("doc", "uncached"):
            for principal in (None, "", " \t", 0, [], {}):
                with self.subTest(principal=principal, doc_id=doc_id):
                    with self.assertRaisesRegex(PermissionDenied, "authentication required"):
                        self.service.get(principal, "tenant-a", doc_id)
                    self.assertEqual(self.authorizer.calls[-1], (principal, "tenant-a"))

        self.assertEqual(self.repository.calls, [("tenant-a", "doc")])

    def test_invalid_tenant_and_document_id_are_rejected_before_io(self):
        for invalid in (None, "", " \t", 0, [], {}):
            for tenant, doc_id, field in ((invalid, "doc", "tenant"), ("tenant-a", invalid, "doc_id")):
                with self.subTest(tenant=tenant, doc_id=doc_id):
                    with self.assertRaisesRegex(ValueError, "^" + field + "$"):
                        self.service.get("alice", tenant, doc_id)

        self.assertEqual(self.authorizer.calls, [])
        self.assertEqual(self.repository.calls, [])


if __name__ == "__main__":
    unittest.main()
