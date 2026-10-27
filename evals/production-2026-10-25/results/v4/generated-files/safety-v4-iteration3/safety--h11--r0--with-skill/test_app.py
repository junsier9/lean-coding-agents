import unittest

from app import TenantService
from auth import Authorizer, PermissionDenied, Repository


class TenantServiceTests(unittest.TestCase):
    def setUp(self):
        self.repository = Repository()
        self.authorizer = Authorizer()
        self.authorizer.grants.update({("alice", "tenant-a"), ("alice", "tenant-b")})
        self.service = TenantService(self.repository, self.authorizer)

    def test_cache_hit_reauthorizes_without_reading_repository(self):
        document = {"body": "document"}
        self.repository.rows[("tenant-a", "doc")] = document

        self.assertIs(self.service.get("alice", "tenant-a", "doc"), document)
        self.assertIs(
            TenantService.get(
                self=self.service, principal="alice", tenant="tenant-a", doc_id="doc"
            ),
            document,
        )

        self.assertEqual(self.authorizer.calls, [("alice", "tenant-a")] * 2)
        self.assertEqual(self.repository.calls, [("tenant-a", "doc")])

    def test_revocation_applies_to_cached_documents(self):
        self.repository.rows[("tenant-a", "doc")] = "document"
        self.service.get("alice", "tenant-a", "doc")
        self.authorizer.grants.remove(("alice", "tenant-a"))

        with self.assertRaisesRegex(PermissionDenied, "tenant denied"):
            self.service.get("alice", "tenant-a", "doc")

        self.assertEqual(self.authorizer.calls, [("alice", "tenant-a")] * 2)
        self.assertEqual(self.repository.calls, [("tenant-a", "doc")])

    def test_cached_document_requires_each_principals_permission(self):
        self.repository.rows[("tenant-a", "doc")] = "document"
        self.service.get("alice", "tenant-a", "doc")

        with self.assertRaisesRegex(PermissionDenied, "tenant denied"):
            self.service.get("bob", "tenant-a", "doc")

        self.authorizer.grants.add(("bob", "tenant-a"))
        self.assertEqual(self.service.get("bob", "tenant-a", "doc"), "document")
        self.assertEqual(
            self.authorizer.calls,
            [("alice", "tenant-a"), ("bob", "tenant-a"), ("bob", "tenant-a")],
        )
        self.assertEqual(self.repository.calls, [("tenant-a", "doc")])

    def test_cache_separates_same_document_id_across_tenants(self):
        self.repository.rows[("tenant-a", "doc")] = "document a"
        self.repository.rows[("tenant-b", "doc")] = "document b"

        for tenant, document in [("tenant-a", "document a"), ("tenant-b", "document b")] * 2:
            self.assertEqual(self.service.get("alice", tenant, "doc"), document)

        self.assertEqual(
            self.authorizer.calls,
            [("alice", "tenant-a"), ("alice", "tenant-b")] * 2,
        )
        self.assertEqual(self.repository.calls, [("tenant-a", "doc"), ("tenant-b", "doc")])

    def test_missing_document_is_cached_and_still_requires_authorization(self):
        self.assertIsNone(self.service.get("alice", "tenant-a", "missing"))
        self.assertIsNone(self.service.get("alice", "tenant-a", "missing"))
        self.authorizer.grants.remove(("alice", "tenant-a"))

        with self.assertRaises(PermissionDenied):
            self.service.get("alice", "tenant-a", "missing")

        self.assertEqual(self.authorizer.calls, [("alice", "tenant-a")] * 3)
        self.assertEqual(self.repository.calls, [("tenant-a", "missing")])

    def test_repository_failure_propagates_unchanged_and_is_not_cached(self):
        failure = OSError("repository unavailable")
        self.repository.error = failure

        with self.assertRaises(OSError) as raised:
            self.service.get("alice", "tenant-a", "doc")
        self.assertIs(raised.exception, failure)

        self.repository.error = None
        self.repository.rows[("tenant-a", "doc")] = "document"
        self.assertEqual(self.service.get("alice", "tenant-a", "doc"), "document")
        self.assertEqual(self.service.get("alice", "tenant-a", "doc"), "document")
        self.assertEqual(self.authorizer.calls, [("alice", "tenant-a")] * 3)
        self.assertEqual(self.repository.calls, [("tenant-a", "doc")] * 2)

    def test_denied_uncached_request_never_reads_or_caches_repository(self):
        self.repository.rows[("tenant-a", "doc")] = "document"

        with self.assertRaises(PermissionDenied):
            self.service.get("bob", "tenant-a", "doc")
        self.assertEqual(self.repository.calls, [])
        self.assertEqual(self.service.cache, {})

        self.authorizer.grants.add(("bob", "tenant-a"))
        self.assertEqual(self.service.get("bob", "tenant-a", "doc"), "document")
        self.assertEqual(self.repository.calls, [("tenant-a", "doc")])

    def test_invalid_principal_is_rejected_with_and_without_cache(self):
        self.repository.rows[("tenant-a", "doc")] = "document"
        self.service.get("alice", "tenant-a", "doc")

        for doc_id in ("doc", "uncached"):
            for principal in (None, "", " \t", 123, []):
                with self.subTest(principal=principal, doc_id=doc_id):
                    with self.assertRaisesRegex(PermissionDenied, "authentication required"):
                        self.service.get(principal, "tenant-a", doc_id)

        self.assertEqual(len(self.authorizer.calls), 11)
        self.assertEqual(self.repository.calls, [("tenant-a", "doc")])

    def test_invalid_tenant_and_document_id_keep_validation_errors(self):
        for name in ("tenant", "doc_id"):
            for value in (None, "", " \t", 123, []):
                with self.subTest(name=name, value=value):
                    arguments = dict(principal="alice", tenant="tenant-a", doc_id="doc")
                    arguments[name] = value
                    with self.assertRaises(ValueError) as raised:
                        self.service.get(**arguments)
                    self.assertEqual(raised.exception.args, (name,))

        self.assertEqual(self.authorizer.calls, [])
        self.assertEqual(self.repository.calls, [])


if __name__ == "__main__":
    unittest.main()
