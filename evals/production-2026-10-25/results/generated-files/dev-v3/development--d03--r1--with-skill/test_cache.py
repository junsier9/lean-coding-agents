import unittest

from app import build_job, new_cache
from cache import ConfigError, RouteCache


class RouteCacheTests(unittest.TestCase):
    def test_new_cache_accepts_empty_configuration_and_target_lists(self):
        cache = new_cache({})
        with self.assertRaises(KeyError):
            cache.targets_for("missing")

        cache.configure({"empty": []})
        targets = cache.targets_for("empty")
        self.assertEqual(targets, [])
        targets.append("local")
        self.assertEqual(cache.targets_for("empty"), [])

    def test_configuration_owns_outer_dict_and_nested_lists(self):
        targets = ["beta", "alpha"]
        routes = {"primary": targets, "alias": targets}
        cache = new_cache(routes)

        targets.append("gamma")
        routes["primary"] = ["replacement"]
        routes.clear()

        self.assertEqual(cache.targets_for("primary"), ["beta", "alpha"])
        self.assertEqual(cache.targets_for("alias"), ["beta", "alpha"])

    def test_each_lookup_returns_an_independent_mutable_list(self):
        cache = new_cache({"primary": ["beta", "alpha"]})
        first = cache.targets_for("primary")
        second = cache.targets_for("primary")

        self.assertIsInstance(first, list)
        self.assertIsNot(first, second)
        first.sort()
        first.append("local")
        second.clear()

        self.assertEqual(cache.targets_for("primary"), ["beta", "alpha"])

    def test_build_job_sorts_only_its_owned_targets(self):
        cache = new_cache({"primary": ["beta", "alpha", "alpha"]})

        job = build_job(cache, "primary")

        self.assertEqual(job, {"route": "primary", "targets": ["alpha", "alpha", "beta"]})
        self.assertEqual(cache.targets_for("primary"), ["beta", "alpha", "alpha"])
        job["targets"].clear()
        self.assertEqual(cache.targets_for("primary"), ["beta", "alpha", "alpha"])

    def test_invalid_configuration_preserves_previous_configuration(self):
        invalid_configs = [
            None, [], (), "routes", 1,
            {"": []}, {1: []}, {None: []},
            {"primary": None}, {"primary": ()}, {"primary": "target"},
            {"primary": {}}, {"primary": [""]}, {"primary": [1]},
            {"primary": [None]}, {"primary": [[]]},
            {"new": ["valid"], "invalid": [""]},
        ]
        cache = new_cache({"previous": ["original"]})

        for routes in invalid_configs:
            with self.subTest(routes=routes):
                with self.assertRaises(ConfigError):
                    cache.configure(routes)
                self.assertEqual(cache.targets_for("previous"), ["original"])
                with self.assertRaises(KeyError):
                    cache.targets_for("new")

    def test_successful_reconfiguration_replaces_previous_routes(self):
        cache = new_cache({"previous": ["original"]})

        cache.configure({"new": ["replacement"]})

        self.assertEqual(cache.targets_for("new"), ["replacement"])
        with self.assertRaises(KeyError):
            cache.targets_for("previous")
        cache.configure({})
        with self.assertRaises(KeyError):
            cache.targets_for("new")

    def test_unknown_route_keeps_key_error(self):
        cache = RouteCache()

        with self.assertRaises(KeyError) as caught:
            cache.targets_for("missing")
        self.assertEqual(caught.exception.args, ("missing",))
        with self.assertRaises(KeyError):
            build_job(cache, "missing")

    def test_route_iteration_failure_propagates_and_preserves_configuration(self):
        failure = RuntimeError("route iteration failed")

        class FailingRoutes(dict):
            def items(self):
                yield "new", ["valid"]
                raise failure

        cache = new_cache({"previous": ["original"]})

        with self.assertRaises(RuntimeError) as caught:
            cache.configure(FailingRoutes())

        self.assertIs(caught.exception, failure)
        self.assertEqual(cache.targets_for("previous"), ["original"])
        with self.assertRaises(KeyError):
            cache.targets_for("new")

    def test_target_iteration_failure_propagates_and_preserves_configuration(self):
        failure = RuntimeError("target iteration failed")

        class FailingTargets(list):
            def __iter__(self):
                yield "valid"
                raise failure

        cache = new_cache({"previous": ["original"]})

        with self.assertRaises(RuntimeError) as caught:
            cache.configure({"new": ["valid"], "broken": FailingTargets()})

        self.assertIs(caught.exception, failure)
        self.assertEqual(cache.targets_for("previous"), ["original"])
        with self.assertRaises(KeyError):
            cache.targets_for("new")


if __name__ == "__main__":
    unittest.main()
