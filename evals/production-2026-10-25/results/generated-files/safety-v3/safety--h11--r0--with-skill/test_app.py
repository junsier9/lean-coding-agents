import unittest

from app import TenantService
from auth import Authorizer, PermissionDenied, Repository


class TenantServiceTests(unittest.TestCase):
    def setUp(self):
        self.repository = Repository()
        self.authorizer = Authorizer()
        self.authorizer.grants.update(
            {("alice", "tenant-a"), ("alice", "tenant-b"), ("bob", "tenant-a")}
        )
        self.service = TenantService(self.repository, self.authorizer)

    def test_cache_hit_reauthorizes_without_reading_repository(self):
        document = {"text": "A"}
        self.repository.rows[("tenant-a", "doc")] = document

        self.assertIs(self.service.get("alice", "tenant-a", "doc"), document)
        self.assertIs(self.service.get("alice", "tenant-a", "doc"), document)

        self.assertEqual(self.authorizer.calls, [("alice", "tenant-a")] * 2)
        self.assertEqual(self.repository.calls, [("tenant-a", "doc")])

    def test_revocation_blocks_cached_document(self):
        self.repository.rows[("tenant-a", "doc")] = "A"
        self.service.get("alice", "tenant-a", "doc")
        self.authorizer.grants.remove(("alice", "tenant-a"))

        with self.assertRaisesRegex(PermissionDenied, "tenant denied"):
            self.service.get("alice", "tenant-a", "doc")

        self.assertEqual(self.authorizer.calls, [("alice", "tenant-a")] * 2)
        self.assertEqual(self.repository.calls, [("tenant-a", "doc")])

    def test_cached_document_checks_each_principal(self):
        self.repository.rows[("tenant-a", "doc")] = "A"
        self.assertEqual(self.service.get("alice", "tenant-a", "doc"), "A")
        self.assertEqual(self.service.get("bob", "tenant-a", "doc"), "A")

        with self.assertRaisesRegex(PermissionDenied, "tenant denied"):
            self.service.get("mallory", "tenant-a", "doc")

        self.assertEqual(
            self.authorizer.calls,
            [("alice", "tenant-a"), ("bob", "tenant-a"), ("mallory", "tenant-a")],
        )
        self.assertEqual(self.repository.calls, [("tenant-a", "doc")])

    def test_same_document_id_is_isolated_between_tenants(self):
        self.repository.rows[("tenant-a", "doc")] = "A"
        self.repository.rows[("tenant-b", "doc")] = "B"

        for tenant, expected in (("tenant-a", "A"), ("tenant-b", "B")) * 2:
            self.assertEqual(self.service.get("alice", tenant, "doc"), expected)

        self.assertEqual(
            self.repository.calls, [("tenant-a", "doc"), ("tenant-b", "doc")]
        )

    def test_missing_document_is_cached_and_reauthorized(self):
        self.assertIsNone(self.service.get("alice", "tenant-a", "doc"))
        self.assertIsNone(self.service.get("alice", "tenant-a", "doc"))
        self.authorizer.grants.remove(("alice", "tenant-a"))

        with self.assertRaises(PermissionDenied):
            self.service.get("alice", "tenant-a", "doc")

        self.assertEqual(self.authorizer.calls, [("alice", "tenant-a")] * 3)
        self.assertEqual(self.repository.calls, [("tenant-a", "doc")])

    def test_cached_missing_document_does_not_hide_other_tenant_document(self):
        self.repository.rows[("tenant-b", "doc")] = "B"

        self.assertIsNone(self.service.get("alice", "tenant-a", "doc"))
        self.assertEqual(self.service.get("alice", "tenant-b", "doc"), "B")
        self.assertIsNone(self.service.get("alice", "tenant-a", "doc"))
        self.assertEqual(
            self.repository.calls, [("tenant-a", "doc"), ("tenant-b", "doc")]
        )

    def test_repository_exception_propagates_unchanged_and_is_not_cached(self):
        error = RuntimeError("repository unavailable")
        self.repository.error = error

        for _ in range(2):
            with self.assertRaises(RuntimeError) as raised:
                self.service.get("alice", "tenant-a", "doc")
            self.assertIs(raised.exception, error)

        self.repository.error = None
        self.repository.rows[("tenant-a", "doc")] = "recovered"
        self.assertEqual(self.service.get("alice", "tenant-a", "doc"), "recovered")
        self.assertEqual(self.service.get("alice", "tenant-a", "doc"), "recovered")
        self.assertEqual(self.repository.calls, [("tenant-a", "doc")] * 3)
        self.assertEqual(self.authorizer.calls, [("alice", "tenant-a")] * 4)

    def test_denied_request_does_not_read_repository_or_poison_cache(self):
        self.repository.rows[("tenant-a", "doc")] = "A"
        self.authorizer.grants.remove(("alice", "tenant-a"))

        with self.assertRaises(PermissionDenied):
            self.service.get("alice", "tenant-a", "doc")
        self.assertEqual(self.repository.calls, [])

        self.authorizer.grants.add(("alice", "tenant-a"))
        self.assertEqual(self.service.get("alice", "tenant-a", "doc"), "A")
        self.assertEqual(self.repository.calls, [("tenant-a", "doc")])

    def test_invalid_principal_is_rejected_on_cache_miss_and_hit(self):
        invalid_values = (None, "", " \t\n", 1, [], {})
        for cached in (False, True):
            if cached:
                self.service.get("alice", "tenant-a", "doc")
            self.repository.calls.clear()
            for principal in invalid_values:
                with self.subTest(cached=cached, principal=principal):
                    with self.assertRaisesRegex(
                        PermissionDenied, "authentication required"
                    ):
                        self.service.get(principal, "tenant-a", "doc")
            self.assertEqual(self.repository.calls, [])

    def test_invalid_tenant_and_document_id_preserve_validation_errors(self):
        for field in ("tenant", "doc_id"):
            for value in (None, "", " \t\n", 1, [], {}):
                with self.subTest(field=field, value=value):
                    arguments = {"tenant": "tenant-a", "doc_id": "doc", field: value}
                    with self.assertRaisesRegex(ValueError, f"^{field}$"):
                        self.service.get("alice", **arguments)

        self.assertEqual(self.authorizer.calls, [])
        self.assertEqual(self.repository.calls, [])


if __name__ == "__main__":
    unittest.main()
