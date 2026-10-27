import unittest

from app import TenantService
from auth import Authorizer, PermissionDenied, Repository


class TenantServiceTests(unittest.TestCase):
    def setUp(self):
        self.repository = Repository()
        self.authorizer = Authorizer()
        self.authorizer.grants.add(("alice", "tenant-a"))
        self.service = TenantService(self.repository, self.authorizer)

    def test_cache_hit_rechecks_permission_without_requerying_repository(self):
        document = object()
        self.repository.rows[("tenant-a", "doc-1")] = document

        self.assertIs(self.service.get("alice", "tenant-a", "doc-1"), document)
        self.assertIs(self.service.get("alice", "tenant-a", "doc-1"), document)

        self.assertEqual(self.authorizer.calls, [("alice", "tenant-a")] * 2)
        self.assertEqual(self.repository.calls, [("tenant-a", "doc-1")])

    def test_revocation_blocks_cached_document(self):
        self.repository.rows[("tenant-a", "doc-1")] = "document"
        self.service.get("alice", "tenant-a", "doc-1")
        self.authorizer.grants.remove(("alice", "tenant-a"))

        with self.assertRaises(PermissionDenied):
            self.service.get("alice", "tenant-a", "doc-1")

        self.assertEqual(self.authorizer.calls, [("alice", "tenant-a")] * 2)
        self.assertEqual(self.repository.calls, [("tenant-a", "doc-1")])

    def test_unauthorized_principal_cannot_read_another_principals_cache(self):
        self.repository.rows[("tenant-a", "doc-1")] = "document"
        self.service.get("alice", "tenant-a", "doc-1")

        with self.assertRaises(PermissionDenied):
            self.service.get("bob", "tenant-a", "doc-1")

        self.assertEqual(self.authorizer.calls[-1], ("bob", "tenant-a"))
        self.assertEqual(self.repository.calls, [("tenant-a", "doc-1")])

    def test_authorization_precedes_cache_access(self):
        class UnreadableCache(dict):
            def __contains__(self, key):
                raise AssertionError("cache read before authorization")

            def __getitem__(self, key):
                raise AssertionError("cache read before authorization")

        self.service.cache = UnreadableCache()

        with self.assertRaises(PermissionDenied):
            self.service.get("bob", "tenant-a", "doc-1")

        self.assertEqual(self.authorizer.calls, [("bob", "tenant-a")])
        self.assertEqual(self.repository.calls, [])

    def test_cache_separates_tenants_and_document_ids(self):
        self.authorizer.grants.add(("alice", "tenant-b"))
        documents = {
            ("tenant-a", "doc-1"): "a1",
            ("tenant-b", "doc-1"): "b1",
            ("tenant-a", "doc-2"): "a2",
        }
        self.repository.rows.update(documents)

        for _ in range(2):
            for (tenant, doc_id), expected in documents.items():
                with self.subTest(tenant=tenant, doc_id=doc_id):
                    self.assertEqual(
                        self.service.get("alice", tenant, doc_id), expected
                    )

        self.assertEqual(self.repository.calls, list(documents))
        self.assertEqual(len(self.authorizer.calls), 6)
        self.assertEqual(self.service.cache, documents)

    def test_missing_document_is_cached_and_still_requires_authorization(self):
        self.assertIsNone(self.service.get("alice", "tenant-a", "missing"))
        self.assertIsNone(self.service.get("alice", "tenant-a", "missing"))
        self.authorizer.grants.clear()

        with self.assertRaises(PermissionDenied):
            self.service.get("alice", "tenant-a", "missing")

        self.assertEqual(self.authorizer.calls, [("alice", "tenant-a")] * 3)
        self.assertEqual(self.repository.calls, [("tenant-a", "missing")])
        self.assertIn(("tenant-a", "missing"), self.service.cache)

    def test_repository_failures_propagate_unchanged_and_are_not_cached(self):
        for error in (RuntimeError("offline"), KeyError("missing"), OSError("io")):
            with self.subTest(error=type(error).__name__):
                self.service.cache.clear()
                self.repository.calls.clear()
                self.repository.error = error

                with self.assertRaises(type(error)) as caught:
                    self.service.get("alice", "tenant-a", "doc-1")

                self.assertIs(caught.exception, error)
                self.assertEqual(self.service.cache, {})
                self.repository.error = None
                self.repository.rows[("tenant-a", "doc-1")] = "recovered"
                self.assertEqual(
                    self.service.get("alice", "tenant-a", "doc-1"), "recovered"
                )
                self.assertEqual(self.repository.calls, [("tenant-a", "doc-1")] * 2)

    def test_invalid_principal_is_rejected_on_cold_and_warm_cache(self):
        for warm_cache in (False, True):
            for principal in (None, "", " \t\n", 0, False, b"alice", []):
                with self.subTest(warm_cache=warm_cache, principal=principal):
                    self.service.cache.clear()
                    if warm_cache:
                        self.service.get("alice", "tenant-a", "doc-1")
                    self.authorizer.calls.clear()
                    self.repository.calls.clear()

                    with self.assertRaisesRegex(
                        PermissionDenied, "authentication required"
                    ):
                        self.service.get(principal, "tenant-a", "doc-1")

                    self.assertEqual(self.authorizer.calls, [(principal, "tenant-a")])
                    self.assertEqual(self.repository.calls, [])

    def test_invalid_tenant_and_document_id_are_rejected(self):
        for field in ("tenant", "doc_id"):
            for value in (None, "", " \t\n", 0, False, b"value", []):
                with self.subTest(field=field, value=value):
                    arguments = {
                        "principal": "alice",
                        "tenant": "tenant-a",
                        "doc_id": "doc-1",
                    }
                    arguments[field] = value

                    with self.assertRaisesRegex(ValueError, f"^{field}$"):
                        self.service.get(**arguments)

        self.assertEqual(self.repository.calls, [])
        self.assertEqual(self.service.cache, {})


if __name__ == "__main__":
    unittest.main()
