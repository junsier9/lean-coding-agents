"""Prepare isolated agent workspaces and grade observable contracts with stdlib only."""
import argparse
import ast
import difflib
import importlib.util
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
CASES = json.loads((HERE / "cases.json").read_text(encoding="utf-8"))["cases"]
RUN_ROOT = None


def load(case_id):
    path = RUN_ROOT / case_id / "app.py"
    name = "candidate_" + case_id
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class Contracts(unittest.TestCase):
    def test_t01_totals(self):
        app = load("t01")
        for rows, total in [([], 0), ([1], 1), ([-3, 0, 4, 7], 11)]:
            with self.subTest(rows=rows):
                self.assertEqual(app.total_positive(rows), total)

    def test_t01_ingress_errors(self):
        app = load("t01")
        for rows in [None, (1,), [True], [1.2], [None], ["3"]]:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                app.total_positive(rows)

    def test_t02_valid_order(self):
        self.assertEqual(load("t02").parse_order({"sku": " X ", "quantity": 2}), ("X", 2))

    def test_t02_invalid_external_values(self):
        app = load("t02")
        for value in [None, [], {}, {"sku": "X", "quantity": True},
                      {"sku": "X", "quantity": 0}, {"sku": "X", "quantity": 1.5},
                      {"sku": 4, "quantity": 1}, {"sku": " ", "quantity": 1}]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                app.parse_order(value)

    def test_t03_count_values(self):
        class DB:
            def count_accounts(self):
                return self.count
        for value in [0, 9]:
            db = DB()
            db.count = value
            self.assertEqual(load("t03").count_accounts(db), value)

    def test_t03_failures_remain_failures(self):
        app = load("t03")
        for failure in [ConnectionError("offline"), RuntimeError("bad query")]:
            class DB:
                def count_accounts(self):
                    raise failure
            with self.subTest(failure=failure):
                with self.assertRaises(type(failure)) as caught:
                    app.count_accounts(DB())
                self.assertIs(caught.exception, failure)

    def test_t04_valid_and_optional(self):
        app = load("t04")
        self.assertEqual(app.load_optional_config(lambda: '{"mode":"fast"}'), {"mode": "fast"})
        for text in ["{", "not json", ""]:
            with self.subTest(text=text):
                self.assertIsNone(app.load_optional_config(lambda: text))

    def test_t04_io_error(self):
        error = OSError("disk")
        def read():
            raise error
        with self.assertRaises(OSError) as caught:
            load("t04").load_optional_config(read)
        self.assertIs(caught.exception, error)

    def test_t05_result_and_input_ownership(self):
        app = load("t05")
        for values in [[], [1, -3, 2.5]]:
            original = values[:]
            result = app.transform(values)
            self.assertEqual(result, [value * 2 for value in original])
            result.append(100)
            self.assertEqual(values, original)

    def test_t06_preview_does_not_mutate_source(self):
        app = load("t06")
        for values in [[], [3, 1, 2, 1]]:
            original = values[:]
            result = app.sorted_preview(values)
            self.assertEqual(result, sorted(original))
            self.assertIsNot(result, values)
            result.append(100)
            self.assertEqual(values, original)

    def test_t07_request_and_result_identity(self):
        marker = object()
        calls = []
        class Client:
            def get(self, path, *, params):
                calls.append((path, params))
                return marker
        self.assertIs(load("t07").search(Client(), "x y"), marker)
        self.assertEqual(calls, [("/v2/search", {"query": "x y", "limit": 20})])

    def test_t07_failure_is_visible(self):
        error = TimeoutError("search unavailable")
        calls = []
        class Client:
            def get(self, path, *, params):
                calls.append(path)
                raise error
        with self.assertRaises(TimeoutError) as caught:
            load("t07").search(Client(), "abc")
        self.assertIs(caught.exception, error)
        self.assertEqual(len(calls), 1)

    def test_t08_transient_recovery(self):
        calls = []
        payload = {"x": [1]}
        marker = object()
        class Transport:
            def send(self, **kwargs):
                calls.append(kwargs)
                if len(calls) < 3:
                    raise TimeoutError("temporary")
                return marker
        result = load("t08").send_request(Transport(), "stable-id", payload)
        self.assertIs(result, marker)
        self.assertEqual(len(calls), 3)
        for call in calls:
            self.assertEqual(call["request_id"], "stable-id")
            self.assertIs(call["payload"], payload)

    def test_t08_success_stops(self):
        calls = []
        class Transport:
            def send(self, **kwargs):
                calls.append(kwargs)
                return 7
        self.assertEqual(load("t08").send_request(Transport(), "id", {}), 7)
        self.assertEqual(len(calls), 1)

    def test_t08_fatal_is_not_retried(self):
        calls = []
        error = ValueError("rejected")
        class Transport:
            def send(self, **kwargs):
                calls.append(kwargs)
                raise error
        with self.assertRaises(ValueError) as caught:
            load("t08").send_request(Transport(), "id", {})
        self.assertIs(caught.exception, error)
        self.assertEqual(len(calls), 1)

    def test_t08_final_failure_and_bound(self):
        calls = []
        error = TimeoutError("still unavailable")
        class Transport:
            def send(self, **kwargs):
                calls.append(kwargs)
                raise error
        with self.assertRaises(TimeoutError) as caught:
            load("t08").send_request(Transport(), "id", {})
        self.assertIs(caught.exception, error)
        self.assertEqual(len(calls), 3)

    def test_t09_every_sample(self):
        app = load("t09")
        for values, expected in [([], []), ([4], [4]), ([2, -1, 7], [2, 1, 8])]:
            with self.subTest(values=values):
                self.assertEqual(app.cumulative_samples(values), expected)

    def test_t10_success_closes_stream(self):
        class Stream:
            closed_count = 0
            def read(self, size):
                self.size = size
                return "abc"[:size]
            def close(self):
                self.closed_count += 1
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.close()
        stream = Stream()
        paths = []
        def open_stream(path):
            paths.append(path)
            return stream
        self.assertEqual(load("t10").read_prefix(open_stream, "doc.txt", 2), "ab")
        self.assertEqual(paths, ["doc.txt"])
        self.assertEqual(stream.size, 2)
        self.assertEqual(stream.closed_count, 1)

    def test_t10_failure_closes_stream(self):
        error = OSError("read failed")
        class Stream:
            closed_count = 0
            def read(self, size):
                raise error
            def close(self):
                self.closed_count += 1
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.close()
        stream = Stream()
        with self.assertRaises(OSError) as caught:
            load("t10").read_prefix(lambda path: stream, "x", 4)
        self.assertIs(caught.exception, error)
        self.assertEqual(stream.closed_count, 1)

    def test_t10_read_close_protocol(self):
        class Stream:
            closed_count = 0
            def read(self, size):
                return "abc"[:size]
            def close(self):
                self.closed_count += 1
        stream = Stream()
        self.assertEqual(load("t10").read_prefix(lambda path: stream, "x", 1), "a")
        self.assertEqual(stream.closed_count, 1)

    def test_t11_protocol_bytes(self):
        app = load("t11")
        prefix = "Return JSON only.\nKeep the keys: result, reason.\n\n"
        for text in ["", "hello\n", "你好"]:
            self.assertEqual(app.build_request(text), {"role": "user", "content": prefix + text})

    def test_t12_label(self):
        self.assertEqual(load("t12").login_label(), "Sign in")

    def test_t12_proportionate_delivery(self):
        audit = RUN_ROOT / "t12" / "checks.jsonl"
        records = [json.loads(line)["check"] for line in audit.read_text().splitlines()] if audit.exists() else []
        self.assertIn("focused", records)
        self.assertIn("dev-build", records)
        self.assertNotIn("release-ui", records)


class RecordingResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.records.append({"test": test._testMethodName, "status": "passed"})

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.records.append({"test": test._testMethodName, "status": "failed"})

    def addError(self, test, err):
        super().addError(test, err)
        self.records.append({"test": test._testMethodName, "status": "error"})

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None:
            self.records.append({"test": test._testMethodName, "status": "failed_subtest"})


def metrics(source):
    tree = ast.parse(source)
    return {
        "nonblank_lines": sum(bool(line.strip()) for line in source.splitlines()),
        "functions": sum(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) for n in ast.walk(tree)),
        "branches": sum(isinstance(n, (ast.If, ast.IfExp, ast.ExceptHandler)) for n in ast.walk(tree)),
        "except_handlers": sum(isinstance(n, ast.ExceptHandler) for n in ast.walk(tree)),
    }


def prepare(root):
    root.mkdir(parents=True, exist_ok=False)
    for arm in ["baseline", "with-skill"]:
        tasks = []
        for case in CASES:
            directory = root / arm / case["id"]
            directory.mkdir(parents=True)
            for name, content in case["files"].items():
                path = directory / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            (directory / "TASK.md").write_text(case["task"] + "\n", encoding="utf-8")
            tasks.append({"id": case["id"], "directory": str(directory), "task": case["task"]})
        (root / arm / "tasks.json").write_text(json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"root": str(root), "tasks_per_arm": len(CASES)}, ensure_ascii=False))


def score(root, arm, output):
    global RUN_ROOT
    RUN_ROOT = root / arm
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(Contracts)
    result = unittest.TextTestRunner(verbosity=1, resultclass=RecordingResult).run(suite)
    details = []
    patches = []
    for case in CASES:
        directory = RUN_ROOT / case["id"]
        original = case["files"]["app.py"]
        updated = (directory / "app.py").read_text(encoding="utf-8")
        patch = list(difflib.unified_diff(original.splitlines(), updated.splitlines(),
                     fromfile=case["id"] + "/before/app.py", tofile=case["id"] + "/after/app.py", lineterm=""))
        patches.extend(patch)
        other_source_changes = []
        for name, initial in case["files"].items():
            if name == "app.py":
                continue
            path = directory / name
            current = path.read_text(encoding="utf-8") if path.exists() else ""
            if current != initial:
                other_source_changes.append(name)
                patches.extend(difflib.unified_diff(initial.splitlines(), current.splitlines(),
                               fromfile=case["id"] + "/before/" + name,
                               tofile=case["id"] + "/after/" + name, lineterm=""))
        extra = sorted(p.relative_to(directory).as_posix() for p in directory.rglob("*")
                       if p.is_file() and "__pycache__" not in p.parts
                       and p.relative_to(directory).as_posix() not in {*case["files"], "TASK.md", "checks.jsonl"})
        try:
            after_metrics = metrics(updated)
        except SyntaxError:
            after_metrics = {"syntax_error": True}
        details.append({
            "id": case["id"], "before": metrics(original), "after": after_metrics,
            "added_lines": sum(line.startswith("+") and not line.startswith("+++") for line in patch),
            "deleted_lines": sum(line.startswith("-") and not line.startswith("---") for line in patch),
            "additional_files": extra,
            "other_source_changes": other_source_changes,
            "checks": (directory / "checks.jsonl").read_text(encoding="utf-8") if (directory / "checks.jsonl").exists() else None,
        })
    failed_methods = {getattr(test, "test_case", test)._testMethodName for test, trace in result.failures + result.errors}
    failed_cases = sorted({name.split("_")[1] for name in failed_methods})
    report = {
        "arm": arm, "test_methods_run": result.testsRun, "failed_methods": sorted(failed_methods),
        "passed_cases": len(CASES) - len(failed_cases), "failed_cases": failed_cases,
        "records": result.records, "cases": details,
        "arm_root_extra_files": sorted(p.name for p in RUN_ROOT.iterdir()
                                      if p.is_file() and p.name not in {"tasks.json", "session.md"}),
        "interpretation": "Structural counts are descriptive; they do not establish that a defense is redundant."
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    output.with_suffix(".diff").write_text("\n".join(patches) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ["arm", "test_methods_run", "passed_cases", "failed_cases"]}))
    return 0 if result.wasSuccessful() else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--root", type=Path, required=True)
    grade = sub.add_parser("score")
    grade.add_argument("--root", type=Path, required=True)
    grade.add_argument("--arm", choices=["baseline", "with-skill"], required=True)
    grade.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.root.resolve())
        return 0
    return score(args.root.resolve(), args.arm, args.output.resolve())


if __name__ == "__main__":
    raise SystemExit(main())

