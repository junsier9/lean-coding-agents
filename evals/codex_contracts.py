"""Holdout contracts for multi-file maintenance tasks; executors do not see this."""
import importlib
import contextlib
import io
import json
import sys
import unittest
from pathlib import Path

import benchmark as b

b.CASES = json.loads((b.HERE / "cases-codex.json").read_text(encoding="utf-8"))["cases"]


def load(case_id):
    directory = b.RUN_ROOT / case_id
    sys.path.insert(0, str(directory))
    for path in directory.glob("*.py"):
        sys.modules.pop(path.stem, None)
    module = b.load(case_id)
    sys.modules["app"] = module
    return module


class Contracts(unittest.TestCase):
    def test_h01_normal(self):
        app = load("h01")
        self.assertEqual(app.summarize([2, 0, 5]), {"before": 7, "after": 7})
        self.assertEqual(app.summarize([]), {"before": 0, "after": 0})

    def test_h01_plugin_mutation(self):
        app = load("h01")
        def adjust(rows):
            rows[0].amount += 3
        self.assertEqual(app.summarize([2, 5], adjust), {"before": 7, "after": 10})

    def test_h01_invalid_after_plugin(self):
        app = load("h01")
        def negative(rows):
            rows[0].amount = -1
        def invalid_type(rows):
            rows[0].amount = "3"
        def invalid_record(rows):
            rows.append(None)
        for adjust in [negative, invalid_type, invalid_record]:
            with self.subTest(adjust=adjust.__name__), self.assertRaises(ValueError):
                app.summarize([2], adjust)

    def test_h01_invalid_ingress_and_plugin_failure(self):
        app = load("h01")
        for raw in [None, [True], [-1], ["2"]]:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                app.summarize(raw)
        error = OSError("plugin failed")
        def fail(rows):
            raise error
        with self.assertRaises(OSError) as caught:
            app.summarize([1], fail)
        self.assertIs(caught.exception, error)

    def test_h02_zero_and_value(self):
        app = load("h02")
        for count in [0, 12]:
            class SDK:
                def fetch_count(self):
                    return count
            self.assertEqual(app.order_count(SDK()), count)

    def test_h02_existing_retry_owner(self):
        load("h02")
        sdk_module = importlib.import_module("orders_sdk")
        page = importlib.import_module("page")
        calls = []
        class Transport:
            def count(self):
                calls.append(1)
                if len(calls) < 3:
                    raise TimeoutError("temporary")
                return 9
        result = page.render_count(sdk_module.OrdersSDK(Transport()))
        self.assertEqual(result, {"status": "ready", "count": 9})
        self.assertEqual(len(calls), 3)

    def test_h02_unavailable_and_retry_bound(self):
        load("h02")
        sdk_module = importlib.import_module("orders_sdk")
        page = importlib.import_module("page")
        calls = []
        class Transport:
            def count(self):
                calls.append(1)
                raise TimeoutError("offline")
        self.assertEqual(page.render_count(sdk_module.OrdersSDK(Transport())),
                         {"status": "unavailable", "count": None})
        self.assertEqual(len(calls), 3)

    def test_h02_other_failure_is_visible(self):
        app = load("h02")
        calls = []
        error = ValueError("bad schema")
        class SDK:
            def fetch_count(self):
                calls.append(1)
                raise error
        with self.assertRaises(ValueError) as caught:
            app.order_count(SDK())
        self.assertIs(caught.exception, error)
        self.assertEqual(len(calls), 1)

    def test_h03_options_and_output(self):
        app = load("h03")
        rows = list(range(105))
        self.assertEqual(len(app.run([], rows).splitlines()), 100)
        self.assertEqual(app.run(["--max-rows", "2"], rows), "0\n1")
        self.assertEqual(app.run(["--max-rows", "0"], rows), "")
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                app.run(["--max-rows", "-1"], rows)
        self.assertEqual(caught.exception.code, 2)

    def test_h03_direct_options(self):
        app = load("h03")
        options = importlib.import_module("report_options")
        self.assertEqual(app.render_rows(["a", "b", "c"], options.Options(1)), "a")

    def test_h03_missing_required_attribute(self):
        app = load("h03")
        with self.assertRaises(AttributeError):
            app.render_rows([1, 2], object())

    def test_h04_both_reducers_and_ownership(self):
        app = load("h04")
        reducers = importlib.import_module("preview_reducers")
        for cls, factor in [(reducers.IdentityReducer, 1), (reducers.DoubleReducer, 2)]:
            for values in [[], [3, 1, 2, 1]]:
                with self.subTest(reducer=cls.__name__, values=values):
                    original = values[:]
                    result = app.sorted_preview(values, cls())
                    self.assertEqual(result, sorted(value * factor for value in original))
                    self.assertIsNot(result, values)
                    result.append(100)
                    self.assertEqual(values, original)

    def test_h04_sort_failure_does_not_mutate_source(self):
        app = load("h04")
        reducers = importlib.import_module("preview_reducers")
        error = RuntimeError("cannot compare")
        class Value:
            def __lt__(self, other):
                raise error
        values = [Value(), Value()]
        original = values[:]
        with self.assertRaises(RuntimeError) as caught:
            app.sorted_preview(values, reducers.IdentityReducer())
        self.assertIs(caught.exception, error)
        self.assertEqual(values, original)

    def test_h05_label(self):
        self.assertEqual(load("h05").save_label(), "Save changes")

    def test_h05_delivery_scope(self):
        audit = b.RUN_ROOT / "h05" / "checks.jsonl"
        checks = [json.loads(line)["check"] for line in audit.read_text().splitlines()] if audit.exists() else []
        self.assertIn("focused", checks)
        self.assertIn("dev-build", checks)
        self.assertNotIn("release-ui", checks)

    def test_h06_valid_and_falsey_payload(self):
        app = load("h06")
        consumer = importlib.import_module("cache_consumer")
        for text, expected in [('{"theme":"dark"}', {"theme": "dark"}), ("0", 0), ("false", False)]:
            class Stream:
                closed = 0
                def read(self):
                    return text
                def close(self):
                    self.closed += 1
            stream = Stream()
            self.assertEqual(consumer.read_settings(lambda path: stream, "p"), expected)
            self.assertEqual(stream.closed, 1)

    def test_h06_expected_misses(self):
        app = load("h06")
        def missing(path):
            raise FileNotFoundError(path)
        self.assertIsNone(app.read_optional_cache(missing, "p"))
        class Stream:
            closed = 0
            def read(self):
                return "{"
            def close(self):
                self.closed += 1
        stream = Stream()
        self.assertIsNone(app.read_optional_cache(lambda path: stream, "p"))
        self.assertEqual(stream.closed, 1)

    def test_h06_open_permission_error(self):
        app = load("h06")
        error = PermissionError("denied")
        def denied(path):
            raise error
        with self.assertRaises(PermissionError) as caught:
            app.read_optional_cache(denied, "p")
        self.assertIs(caught.exception, error)

    def test_h06_read_failure_closes(self):
        app = load("h06")
        for error in [OSError("read failed"), FileNotFoundError("lost after open"),
                      PermissionError("read denied")]:
            class Stream:
                closed = 0
                def read(self):
                    raise error
                def close(self):
                    self.closed += 1
            stream = Stream()
            with self.subTest(error=error):
                with self.assertRaises(type(error)) as caught:
                    app.read_optional_cache(lambda path: stream, "p")
                self.assertIs(caught.exception, error)
                self.assertEqual(stream.closed, 1)


if __name__ == "__main__":
    b.Contracts = Contracts
    raise SystemExit(b.main())

