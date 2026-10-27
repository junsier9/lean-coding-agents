import unittest

from app import build_job, new_cache
from cache import ConfigError, RouteCache


class RouteCacheTests(unittest.TestCase):
    def setUp(self):
        self.cache = new_cache({"active": ["z", "a"]})

    def test_configuration_owns_dict_and_nested_lists(self):
        targets = ["z", "a"]
        routes = {"active": targets, "shared": targets, "empty": []}
        self.cache.configure(routes)

        targets.append("later")
        routes["empty"].append("later")
        routes.clear()

        self.assertEqual(self.cache.targets_for("active"), ["z", "a"])
        self.assertEqual(self.cache.targets_for("shared"), ["z", "a"])
        self.assertEqual(self.cache.targets_for("empty"), [])

    def test_each_read_returns_an_independent_mutable_list(self):
        first = self.cache.targets_for("active")
        second = self.cache.targets_for("active")
        self.assertIsInstance(first, list)
        self.assertIsNot(first, second)

        first.sort()
        first.append("later")
        second.clear()

        self.assertEqual(self.cache.targets_for("active"), ["z", "a"])

    def test_invalid_configuration_preserves_active_snapshot(self):
        invalid_routes = [
            None, [], (), "active", 42,
            {"": []}, {None: []}, {1: []},
            {"new": None}, {"new": "target"}, {"new": ("target",)},
            {"new": {"target"}}, {"new": {}},
            {"new": [""]}, {"new": [None]}, {"new": [1]},
            {"new": [["nested"]]},
            {"active": ["replacement"], "later": ["valid", ""]},
        ]
        for routes in invalid_routes:
            with self.subTest(routes=routes):
                with self.assertRaises(ConfigError):
                    self.cache.configure(routes)
                self.assertEqual(self.cache.targets_for("active"), ["z", "a"])
                with self.assertRaises(KeyError):
                    self.cache.targets_for("new")

    def test_successful_configuration_replaces_previous_routes(self):
        self.cache.configure({"empty": []})
        self.assertEqual(self.cache.targets_for("empty"), [])
        with self.assertRaises(KeyError):
            self.cache.targets_for("active")

        self.cache.configure({})
        with self.assertRaises(KeyError):
            self.cache.targets_for("empty")

    def test_unknown_route_raises_key_error(self):
        with self.assertRaises(KeyError) as raised:
            self.cache.targets_for("missing")
        self.assertEqual(raised.exception.args, ("missing",))
        with self.assertRaises(KeyError):
            RouteCache().targets_for("missing")

    def test_dict_iteration_failure_propagates_without_replacing_snapshot(self):
        failure = RuntimeError("route iteration failed")

        class FailingRoutes(dict):
            def items(self):
                yield "active", ["replacement"]
                raise failure

        with self.assertRaises(RuntimeError) as raised:
            self.cache.configure(FailingRoutes())
        self.assertIs(raised.exception, failure)
        self.assertEqual(self.cache.targets_for("active"), ["z", "a"])

    def test_list_iteration_failure_propagates_without_replacing_snapshot(self):
        failure = RuntimeError("target iteration failed")

        class FailingTargets(list):
            def __iter__(self):
                yield "valid"
                raise failure

        with self.assertRaises(RuntimeError) as raised:
            self.cache.configure({
                "active": ["replacement"],
                "later": FailingTargets(),
            })
        self.assertIs(raised.exception, failure)
        self.assertEqual(self.cache.targets_for("active"), ["z", "a"])

    def test_build_job_sorts_owned_targets_and_preserves_cache_order(self):
        job = build_job(self.cache, "active")
        self.assertEqual(job, {"route": "active", "targets": ["a", "z"]})
        self.assertEqual(self.cache.targets_for("active"), ["z", "a"])

        job["targets"].clear()
        self.assertEqual(self.cache.targets_for("active"), ["z", "a"])
        self.assertEqual(build_job(self.cache, "active")["targets"], ["a", "z"])


if __name__ == "__main__":
    unittest.main()
