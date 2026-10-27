import unittest
from app import TenantService
from auth import Authorizer, Repository, PermissionDenied

class Contract(unittest.TestCase):
    def setUp(self):
        self.auth, self.repo = Authorizer(), Repository()
        self.auth.grants.update({("u", "A"), ("u", "B")})
        self.repo.rows.update({("A", "doc"): "A-value", ("B", "doc"): "B-value"})
        self.service = TenantService(self.repo, self.auth)

    def test_authorized(self):
        self.assertEqual(self.service.get("u", "A", "doc"), "A-value")

    def test_fresh_cache_no_repository(self):
        self.service.get("u", "A", "doc")
        self.service.get("u", "A", "doc")
        self.assertEqual(self.repo.calls, [("A", "doc")])

    def test_tenant_collision(self):
        self.assertEqual(self.service.get("u", "A", "doc"), "A-value")
        self.assertEqual(self.service.get("u", "B", "doc"), "B-value")

    def test_revoke_warm_cache(self):
        self.service.get("u", "A", "doc")
        self.auth.grants.remove(("u", "A"))
        with self.assertRaises(PermissionDenied):
            self.service.get("u", "A", "doc")

    def test_anonymous_warm_cache(self):
        self.service.get("u", "A", "doc")
        with self.assertRaises(PermissionDenied):
            self.service.get(None, "A", "doc")

    def test_denied_before_repository(self):
        with self.assertRaises(PermissionDenied):
            self.service.get("other", "A", "doc")
        self.assertEqual(self.repo.calls, [])

    def test_none_cached(self):
        self.assertIsNone(self.service.get("u", "A", "missing"))
        self.assertIsNone(self.service.get("u", "A", "missing"))
        self.assertEqual(self.repo.calls, [("A", "missing")])

    def test_authorizer_every_request(self):
        for _ in range(3):
            self.service.get("u", "A", "doc")
        self.assertEqual(self.auth.calls, [("u", "A")] * 3)

    def test_repository_failure_not_cached(self):
        error = OSError("database")
        self.repo.error = error
        with self.assertRaises(OSError) as found:
            self.service.get("u", "A", "doc")
        self.assertIs(found.exception, error)
        self.assertEqual(self.service.cache, {})

    def test_invalid_keys(self):
        for tenant, doc in (("", "x"), (None, "x"), ("A", ""), ("A", 1)):
            with self.subTest(tenant=tenant, doc=doc), self.assertRaises(ValueError):
                self.service.get("u", tenant, doc)
        self.assertEqual(self.repo.calls, [])
