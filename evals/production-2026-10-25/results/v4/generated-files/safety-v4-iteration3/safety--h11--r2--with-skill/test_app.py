import unittest

from app import TenantService
from auth import Authorizer, PermissionDenied, Repository


class TenantServiceTests(unittest.TestCase):
    def setUp(self):
        self.repository = Repository()
        self.authorizer = Authorizer()
        self.authorizer.grants.add(("alice", "tenant-a"))
        self.service = TenantService(self.repository, self.authorizer)

    def test_cache_hits_authorize_each_supported_call_form(self):
        document = {"title": "Document A"}
        self.repository.rows[("tenant-a", "doc-1")] = document

        self.assertIs(self.service.get("alice", "tenant-a", "doc-1"), document)
        self.assertIs(
            self.service.get(principal="alice", tenant="tenant-a", doc_id="doc-1"),
            document,
        )
        self.assertIs(
            TenantService.get(
                self=self.service, principal="alice", tenant="tenant-a", doc_id="doc-1"
            ),
            document,
        )
        self.assertEqual(self.authorizer.calls, [("alice", "tenant-a")] * 3)
        self.assertEqual(self.repository.calls, [("tenant-a", "doc-1")])

    def test_revocation_blocks_cached_document(self):
        self.repository.rows[("tenant-a", "doc-1")] = "document"
        self.service.get("alice", "tenant-a", "doc-1")
        self.authorizer.grants.remove(("alice", "tenant-a"))

        with self.assertRaisesRegex(PermissionDenied, "tenant denied"):
            self.service.get("alice", "tenant-a", "doc-1")

        self.assertEqual(self.authorizer.calls, [("alice", "tenant-a")] * 2)
        self.assertEqual(self.repository.calls, [("tenant-a", "doc-1")])

    def test_other_principal_cannot_read_cached_document(self):
        self.repository.rows[("tenant-a", "doc-1")] = "document"
        self.service.get("alice", "tenant-a", "doc-1")

        with self.assertRaisesRegex(PermissionDenied, "tenant denied"):
            self.service.get("bob", "tenant-a", "doc-1")

        self.assertEqual(self.repository.calls, [("tenant-a", "doc-1")])

    def test_same_document_id_is_isolated_by_tenant(self):
        self.authorizer.grants.add(("alice", "tenant-b"))
        self.repository.rows[("tenant-a", "doc-1")] = "document A"
        self.repository.rows[("tenant-b", "doc-1")] = "document B"

        for _ in range(2):
            self.assertEqual(self.service.get("alice", "tenant-a", "doc-1"), "document A")
            self.assertEqual(self.service.get("alice", "tenant-b", "doc-1"), "document B")

        self.assertEqual(
            self.repository.calls, [("tenant-a", "doc-1"), ("tenant-b", "doc-1")]
        )
        self.assertEqual(len(self.authorizer.calls), 4)

    def test_invalid_principals_are_rejected_on_misses_and_hits(self):
        self.repository.rows[("tenant-a", "cached")] = "document"
        self.service.get("alice", "tenant-a", "cached")

        for principal in (None, "", " \t", 0, [], {}):
            for doc_id in ("missing", "cached"):
                with self.subTest(principal=principal, doc_id=doc_id):
                    with self.assertRaisesRegex(PermissionDenied, "authentication required"):
                        self.service.get(principal, "tenant-a", doc_id)

        self.assertEqual(self.repository.calls, [("tenant-a", "cached")])

    def test_invalid_tenant_or_document_id_is_rejected(self):
        for field in ("tenant", "doc_id"):
            for value in (None, "", " \t", 0, [], {}):
                with self.subTest(field=field, value=value):
                    arguments = {"principal": "alice", "tenant": "tenant-a", "doc_id": "doc-1"}
                    arguments[field] = value
                    with self.assertRaisesRegex(ValueError, f"^{field}$"):
                        self.service.get(**arguments)

        self.assertEqual(self.repository.calls, [])
        self.assertEqual(self.service.cache, {})

    def test_denied_cache_miss_does_not_read_or_populate_cache(self):
        with self.assertRaisesRegex(PermissionDenied, "tenant denied"):
            self.service.get("bob", "tenant-a", "doc-1")

        self.assertEqual(self.authorizer.calls, [("bob", "tenant-a")])
        self.assertEqual(self.repository.calls, [])
        self.assertEqual(self.service.cache, {})

    def test_missing_document_is_cached_but_still_requires_authorization(self):
        self.assertIsNone(self.service.get("alice", "tenant-a", "doc-1"))
        self.assertIsNone(self.service.get("alice", "tenant-a", "doc-1"))
        self.assertEqual(self.repository.calls, [("tenant-a", "doc-1")])
        self.assertEqual(self.authorizer.calls, [("alice", "tenant-a")] * 2)
        self.authorizer.grants.clear()

        with self.assertRaises(PermissionDenied):
            self.service.get("alice", "tenant-a", "doc-1")

        self.assertEqual(self.repository.calls, [("tenant-a", "doc-1")])

    def test_missing_document_in_one_tenant_does_not_hide_another(self):
        self.authorizer.grants.add(("alice", "tenant-b"))
        self.repository.rows[("tenant-b", "doc-1")] = "document B"

        self.assertIsNone(self.service.get("alice", "tenant-a", "doc-1"))
        self.assertEqual(self.service.get("alice", "tenant-b", "doc-1"), "document B")
        self.assertIsNone(self.service.get("alice", "tenant-a", "doc-1"))
        self.assertEqual(
            self.repository.calls, [("tenant-a", "doc-1"), ("tenant-b", "doc-1")]
        )

    def test_repository_failure_propagates_and_is_not_cached(self):
        error = RuntimeError("repository unavailable")
        self.repository.error = error

        for _ in range(2):
            with self.assertRaises(RuntimeError) as raised:
                self.service.get("alice", "tenant-a", "doc-1")
            self.assertIs(raised.exception, error)
            self.assertEqual(self.service.cache, {})

        self.repository.error = None
        self.repository.rows[("tenant-a", "doc-1")] = "recovered document"
        self.assertEqual(
            self.service.get("alice", "tenant-a", "doc-1"), "recovered document"
        )
        self.assertEqual(self.repository.calls, [("tenant-a", "doc-1")] * 3)
        self.assertEqual(self.authorizer.calls, [("alice", "tenant-a")] * 3)


if __name__ == "__main__":
    unittest.main()
