import unittest
from unittest.mock import Mock

from app import TenantService
from auth import Authorizer, PermissionDenied, Repository


class TenantServiceTests(unittest.TestCase):
    def setUp(self):
        self.repository = Repository()
        self.authorizer = Authorizer()
        self.authorizer.grants.add(("alice", "tenant-a"))
        self.service = TenantService(self.repository, self.authorizer)

    def test_cache_hit_reauthorizes_without_reading_repository(self):
        document = {"title": "A"}
        self.repository.rows[("tenant-a", "doc-1")] = document

        self.assertIs(self.service.get("alice", "tenant-a", "doc-1"), document)
        self.assertIs(
            TenantService.get(
                self=self.service,
                principal="alice",
                tenant="tenant-a",
                doc_id="doc-1",
            ),
            document,
        )
        self.assertEqual(self.repository.calls, [("tenant-a", "doc-1")])
        self.assertEqual(self.authorizer.calls, [("alice", "tenant-a")] * 2)

    def test_revocation_blocks_cached_document(self):
        self.repository.rows[("tenant-a", "doc-1")] = "document"
        self.service.get("alice", "tenant-a", "doc-1")
        self.authorizer.grants.clear()

        with self.assertRaises(PermissionDenied):
            self.service.get("alice", "tenant-a", "doc-1")

        self.assertEqual(self.repository.calls, [("tenant-a", "doc-1")])
        self.assertEqual(self.authorizer.calls, [("alice", "tenant-a")] * 2)

    def test_other_principal_must_be_authorized_on_cache_hit(self):
        self.repository.rows[("tenant-a", "doc-1")] = "document"
        self.service.get("alice", "tenant-a", "doc-1")

        with self.assertRaises(PermissionDenied):
            self.service.get("bob", "tenant-a", "doc-1")

        self.authorizer.grants.add(("bob", "tenant-a"))
        self.assertEqual(self.service.get("bob", "tenant-a", "doc-1"), "document")
        self.assertEqual(self.repository.calls, [("tenant-a", "doc-1")])
        self.assertEqual(
            self.authorizer.calls,
            [("alice", "tenant-a"), ("bob", "tenant-a"), ("bob", "tenant-a")],
        )

    def test_same_document_id_is_isolated_by_tenant(self):
        self.authorizer.grants.add(("alice", "tenant-b"))
        self.repository.rows[("tenant-a", "doc-1")] = "A"
        self.repository.rows[("tenant-b", "doc-1")] = "B"

        for tenant, expected in (("tenant-a", "A"), ("tenant-b", "B")) * 2:
            with self.subTest(tenant=tenant):
                self.assertEqual(self.service.get("alice", tenant, "doc-1"), expected)

        self.assertEqual(
            self.repository.calls, [("tenant-a", "doc-1"), ("tenant-b", "doc-1")]
        )

    def test_missing_document_is_cached_and_still_requires_authorization(self):
        self.assertIsNone(self.service.get("alice", "tenant-a", "missing"))
        self.assertIsNone(self.service.get("alice", "tenant-a", "missing"))
        self.assertEqual(self.repository.calls, [("tenant-a", "missing")])
        self.assertEqual(self.authorizer.calls, [("alice", "tenant-a")] * 2)

        self.authorizer.grants.clear()
        with self.assertRaises(PermissionDenied):
            self.service.get("alice", "tenant-a", "missing")
        self.assertEqual(self.repository.calls, [("tenant-a", "missing")])

    def test_missing_document_in_one_tenant_does_not_hide_another(self):
        self.authorizer.grants.add(("alice", "tenant-b"))
        self.repository.rows[("tenant-b", "doc-1")] = "B"

        self.assertIsNone(self.service.get("alice", "tenant-a", "doc-1"))
        self.assertEqual(self.service.get("alice", "tenant-b", "doc-1"), "B")
        self.assertEqual(
            self.repository.calls, [("tenant-a", "doc-1"), ("tenant-b", "doc-1")]
        )

    def test_invalid_principal_is_rejected_on_cache_miss_and_hit(self):
        for cached in (False, True):
            if cached:
                self.service.get("alice", "tenant-a", "doc-1")
            calls_before = list(self.repository.calls)
            for principal in (None, "", " \t\n", 0, [], {}):
                with self.subTest(cached=cached, principal=principal):
                    with self.assertRaisesRegex(PermissionDenied, "authentication required"):
                        self.service.get(principal, "tenant-a", "doc-1")
                    self.assertEqual(self.repository.calls, calls_before)

    def test_invalid_identifiers_are_rejected_on_cache_miss_and_hit(self):
        for cached in (False, True):
            if cached:
                self.service.get("alice", "tenant-a", "doc-1")
            calls_before = list(self.repository.calls)
            for field in ("tenant", "doc_id"):
                for invalid in (None, "", " \t\n", 0, [], {}):
                    arguments = dict(principal="alice", tenant="tenant-a", doc_id="doc-1")
                    arguments[field] = invalid
                    with self.subTest(cached=cached, field=field, invalid=invalid):
                        with self.assertRaisesRegex(ValueError, f"^{field}$"):
                            self.service.get(**arguments)
                        self.assertEqual(self.repository.calls, calls_before)

    def test_repository_errors_propagate_unchanged_and_are_not_cached(self):
        for error in (RuntimeError("offline"), KeyError("broken lookup")):
            with self.subTest(error=error):
                self.repository.error = error
                calls_before = len(self.repository.calls)
                for _ in range(2):
                    with self.assertRaises(type(error)) as caught:
                        self.service.get("alice", "tenant-a", "doc-1")
                    self.assertIs(caught.exception, error)
                self.assertEqual(len(self.repository.calls), calls_before + 2)
                self.assertEqual(self.service.cache, {})

        self.repository.error = None
        self.repository.rows[("tenant-a", "doc-1")] = "recovered"
        self.assertEqual(self.service.get("alice", "tenant-a", "doc-1"), "recovered")

    def test_authorization_failure_precedes_any_cache_or_repository_access(self):
        for error in (PermissionDenied("denied"), RuntimeError("authorization offline")):
            with self.subTest(error=error):
                authorizer = Mock()
                authorizer.ensure.side_effect = error
                repository = Mock()
                service = TenantService(repository, authorizer)
                service.cache = Mock()

                with self.assertRaises(type(error)) as caught:
                    service.get("alice", "tenant-a", "doc-1")

                self.assertIs(caught.exception, error)
                authorizer.ensure.assert_called_once_with("alice", "tenant-a")
                self.assertEqual(service.cache.mock_calls, [])
                self.assertEqual(repository.mock_calls, [])


if __name__ == "__main__":
    unittest.main()
