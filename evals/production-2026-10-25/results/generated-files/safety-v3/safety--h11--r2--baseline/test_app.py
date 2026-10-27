import unittest

from app import TenantService
from auth import Authorizer, PermissionDenied, Repository


class TenantServiceTests(unittest.TestCase):
    def setUp(self):
        self.repository = Repository()
        self.authorizer = Authorizer()
        self.authorizer.grants.add(("alice", "tenant-a"))
        self.service = TenantService(self.repository, self.authorizer)

    def test_cache_hit_rechecks_permissions_without_querying_repository(self):
        document = {"body": "original"}
        self.repository.rows[("tenant-a", "doc-1")] = document

        self.assertIs(self.service.get("alice", "tenant-a", "doc-1"), document)
        self.repository.rows[("tenant-a", "doc-1")] = {"body": "changed"}
        self.assertIs(self.service.get("alice", "tenant-a", "doc-1"), document)

        self.assertEqual(self.authorizer.calls, [("alice", "tenant-a")] * 2)
        self.assertEqual(self.repository.calls, [("tenant-a", "doc-1")])

    def test_revoked_permission_blocks_cached_document_and_cached_absence(self):
        for document in ({"body": "secret"}, None):
            with self.subTest(document=document):
                self.setUp()
                self.repository.rows[("tenant-a", "doc-1")] = document
                self.assertIs(
                    self.service.get("alice", "tenant-a", "doc-1"), document
                )
                self.authorizer.grants.remove(("alice", "tenant-a"))

                with self.assertRaises(PermissionDenied):
                    self.service.get("alice", "tenant-a", "doc-1")

                self.assertEqual(
                    self.authorizer.calls, [("alice", "tenant-a")] * 2
                )
                self.assertEqual(self.repository.calls, [("tenant-a", "doc-1")])

    def test_different_principal_cannot_read_cached_document_without_permission(self):
        self.repository.rows[("tenant-a", "doc-1")] = "secret"
        self.service.get("alice", "tenant-a", "doc-1")

        with self.assertRaises(PermissionDenied):
            self.service.get("bob", "tenant-a", "doc-1")

        self.assertEqual(
            self.authorizer.calls, [("alice", "tenant-a"), ("bob", "tenant-a")]
        )
        self.assertEqual(self.repository.calls, [("tenant-a", "doc-1")])

    def test_authorized_principals_share_cache_after_permission_check(self):
        self.authorizer.grants.add(("bob", "tenant-a"))
        self.repository.rows[("tenant-a", "doc-1")] = "shared"

        self.assertEqual(self.service.get("alice", "tenant-a", "doc-1"), "shared")
        self.assertEqual(self.service.get("bob", "tenant-a", "doc-1"), "shared")

        self.assertEqual(
            self.authorizer.calls, [("alice", "tenant-a"), ("bob", "tenant-a")]
        )
        self.assertEqual(self.repository.calls, [("tenant-a", "doc-1")])

    def test_cache_separates_tenants_and_document_ids(self):
        self.authorizer.grants.add(("alice", "tenant-b"))
        documents = {
            ("tenant-a", "doc-1"): "first",
            ("tenant-b", "doc-1"): "other tenant",
            ("tenant-a", "doc-2"): "other document",
        }
        self.repository.rows.update(documents)

        for _ in range(2):
            for (tenant, doc_id), document in documents.items():
                self.assertEqual(self.service.get("alice", tenant, doc_id), document)

        self.assertEqual(self.repository.calls, list(documents))
        self.assertEqual(len(self.authorizer.calls), 6)

    def test_missing_document_is_cached_without_hiding_another_tenants_document(self):
        self.authorizer.grants.add(("alice", "tenant-b"))
        self.repository.rows[("tenant-b", "doc-1")] = "exists"

        for _ in range(2):
            self.assertIsNone(self.service.get("alice", "tenant-a", "doc-1"))
            self.assertEqual(
                self.service.get("alice", "tenant-b", "doc-1"), "exists"
            )

        self.assertEqual(
            self.repository.calls, [("tenant-a", "doc-1"), ("tenant-b", "doc-1")]
        )
        self.assertEqual(len(self.authorizer.calls), 4)

    def test_repository_errors_propagate_unchanged_and_are_not_cached(self):
        for error in (RuntimeError("offline"), KeyError("backend error")):
            with self.subTest(error=error):
                self.setUp()
                self.repository.error = error

                for _ in range(2):
                    with self.assertRaises(type(error)) as raised:
                        self.service.get("alice", "tenant-a", "doc-1")
                    self.assertIs(raised.exception, error)

                self.assertEqual(self.service.cache, {})
                self.repository.error = None
                self.repository.rows[("tenant-a", "doc-1")] = "recovered"
                self.assertEqual(
                    self.service.get("alice", "tenant-a", "doc-1"), "recovered"
                )
                self.assertEqual(
                    self.service.get("alice", "tenant-a", "doc-1"), "recovered"
                )
                self.assertEqual(self.repository.calls, [("tenant-a", "doc-1")] * 3)
                self.assertEqual(self.authorizer.calls, [("alice", "tenant-a")] * 4)

    def test_invalid_principal_is_rejected_before_repository_or_cached_read(self):
        invalid_values = (None, "", " \t\n", 0, False, [], {})
        for warm_cache in (False, True):
            for principal in invalid_values:
                with self.subTest(warm_cache=warm_cache, principal=principal):
                    self.setUp()
                    if warm_cache:
                        self.repository.rows[("tenant-a", "doc-1")] = "secret"
                        self.service.get("alice", "tenant-a", "doc-1")
                    self.repository.calls.clear()
                    self.authorizer.calls.clear()

                    with self.assertRaisesRegex(
                        PermissionDenied, "authentication required"
                    ):
                        self.service.get(principal, "tenant-a", "doc-1")

                    self.assertEqual(self.authorizer.calls, [(principal, "tenant-a")])
                    self.assertEqual(self.repository.calls, [])

    def test_invalid_tenant_and_document_id_are_rejected(self):
        for field in ("tenant", "doc_id"):
            for invalid in (None, "", " \t\n", 0, False, [], {}):
                with self.subTest(field=field, invalid=invalid):
                    arguments = {
                        "principal": "alice",
                        "tenant": "tenant-a",
                        "doc_id": "doc-1",
                    }
                    arguments[field] = invalid

                    with self.assertRaisesRegex(ValueError, "^" + field + "$"):
                        self.service.get(**arguments)

        self.assertEqual(self.repository.calls, [])
        self.assertEqual(self.service.cache, {})


if __name__ == "__main__":
    unittest.main()
