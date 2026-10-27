"""Regression tests for frozen overlays and candidate artifact copying.

Run with the bundled Python and ``-m unittest discover -s <this directory>
-p test_preflight.py -v``. Set PREFLIGHT_TEST_DEPS to an existing directory
containing pytest and pytest_timeout when using a different runtime. All
fixtures and pytest logs are created in a fresh temporary directory.
"""
from __future__ import annotations

import errno
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import textwrap
import unittest
from unittest import mock


_SPEC = importlib.util.spec_from_file_location(
    "preflight_under_test", Path(__file__).with_name("preflight.py"))
preflight = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(preflight)

_DEFAULT_RUNTIME = Path(
    r"C:\Users\ASUS\AppData\Local\Temp\lean-full-validation-b148d5d8f3a84c3d8b8ccbe5fc0c5c67")
DEPS_DIR = Path(os.environ.get("PREFLIGHT_TEST_DEPS", str(_DEFAULT_RUNTIME / "testdeps")))


def write_file(root, relative, content):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip("\n"), encoding="utf-8")
    return path


class PreflightRegressionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="lean-preflight-unittest-")).resolve()
        self.addCleanup(self._cleanup_runtime)

    def _cleanup_runtime(self):
        # Check the resolved deletion target before removing the fixture tree.
        temp_root = Path(tempfile.gettempdir()).resolve()
        if not self.root.is_relative_to(temp_root) or self.root == temp_root:
            raise ValueError("Fixture cleanup escaped the temporary directory")
        shutil.rmtree(self.root)

    def make_case(self, test_root="tests"):
        base, fix = self.root / "base", self.root / "fix"
        module_parts = Path(test_root).parts[:-1]
        production = Path(*module_parts, "logic.py").as_posix()
        module_name = ".".join((*module_parts, "logic"))
        test_file = test_root + "/test_logic.py"
        for tree, result in ((base, 1), (fix, 2)):
            tree.mkdir()
            for index in range(1, len(module_parts) + 1):
                write_file(tree, Path(*module_parts[:index], "__init__.py"), "")
            write_file(tree, production, f"def value():\n    return {result}\n")

        write_file(base, "new_module.py", "CANDIDATE_ADDITION = 42\n")
        write_file(base, test_file, f"""
            from {module_name} import value

            def test_normal_path():
                assert value() >= 1
        """)
        write_file(base, test_root + "/test_candidate_only.py", "raise RuntimeError('unfrozen test')\n")
        write_file(base, test_root + "/conftest.py", "raise RuntimeError('unfrozen fixture')\n")
        write_file(base, test_root + "/fixtures/expected.txt", "999\n")
        write_file(base, "conftest.py", "raise RuntimeError('unfrozen root plugin')\n")
        write_file(base, "pytest.ini", "[pytest]\naddopts = --collect-only\n")
        write_file(base, "tox.ini", "[pytest]\naddopts = --collect-only\n")

        write_file(fix, test_file, f"""
            from {module_name} import value

            def test_normal_path():
                assert value() >= 1

            def test_regression(expected_value):
                assert value() == expected_value
        """)
        write_file(fix, test_root + "/conftest.py", """
            from pathlib import Path
            import pytest

            @pytest.fixture
            def expected_value():
                return int((Path(__file__).parent / "fixtures" / "expected.txt").read_text())
        """)
        write_file(fix, test_root + "/fixtures/expected.txt", "2\n")
        write_file(fix, "conftest.py", "# Frozen root fixture configuration.\n")
        write_file(fix, "pytest.ini", "[pytest]\naddopts = --strict-markers\n")

        overlay, roots = preflight.overlay_manifest(fix, [test_file])
        case = {
            "id": "synthetic-overlay",
            "fix_dir": str(fix),
            "production_files": [production],
            "grade_overlay_files": overlay,
            "test_paths": [test_file],
            "preflight": {
                "runtime_root": str(self.root),
                "pytest_config": preflight.pytest_config(fix),
                "frozen_test_roots": roots,
                "overlay_sha256": {name: preflight.digest(fix / name) for name in overlay},
            },
        }
        return case, base, fix, production, test_file

    def assert_frozen_overlay(self, case, grade, candidate, production):
        self.assertEqual((grade / production).read_bytes(), (candidate / production).read_bytes())
        if (candidate / "new_module.py").is_file():
            self.assertEqual((grade / "new_module.py").read_bytes(),
                             (candidate / "new_module.py").read_bytes())
        for relative in case["grade_overlay_files"]:
            self.assertEqual((grade / relative).read_bytes(),
                             (Path(case["fix_dir"]) / relative).read_bytes(), relative)
        self.assertFalse((grade / "tox.ini").exists())
        for root in case["preflight"]["frozen_test_roots"]:
            self.assertFalse((grade / root / "test_candidate_only.py").exists())

    def test_base_fails_and_fix_passes_for_top_level_and_nested_tests(self):
        self.assertTrue((DEPS_DIR / "pytest").is_dir(),
                        "Set PREFLIGHT_TEST_DEPS to an existing pytest dependency directory")
        self.assertTrue((DEPS_DIR / "pytest_timeout.py").is_file())
        for layout in ("tests", "toolz/tests", "toolz/sandbox/tests"):
            with self.subTest(layout=layout):
                # Each layout has independent modules, overlays and subprocesses.
                layout_root = self.root / layout.replace("/", "-")
                layout_root.mkdir()
                original_root, self.root = self.root, layout_root
                try:
                    case, base, fix, production, test_file = self.make_case(layout)
                    self.assertEqual(case["preflight"]["frozen_test_roots"], [layout])
                    self.assertNotIn(production, case["grade_overlay_files"])
                    self.assertIn(layout + "/fixtures/expected.txt", case["grade_overlay_files"])
                    self.assertNotEqual((base / production).read_bytes(),
                                        (fix / production).read_bytes())

                    base_grade = preflight.prepare_grade(case, base, self.root / "grade-base")
                    fix_grade = preflight.prepare_grade(case, fix, self.root / "grade-fix")
                    self.assert_frozen_overlay(case, base_grade, base, production)
                    self.assert_frozen_overlay(case, fix_grade, fix, production)
                    self.assertNotEqual((base_grade / production).read_bytes(),
                                        (fix_grade / production).read_bytes())

                    base_result = preflight.run_tests(
                        case, base_grade, self.root / "logs", "base", sys.executable, DEPS_DIR)
                    fix_result = preflight.run_tests(
                        case, fix_grade, self.root / "logs", "fix", sys.executable, DEPS_DIR)
                    self.assertEqual(
                        {key: base_result[key] for key in
                         ("exit_code", "tests_collected", "tests_ran", "passed", "failed", "errors")},
                        dict(exit_code=1, tests_collected=2, tests_ran=2, passed=1,
                             failed=1, errors=0),
                        json.dumps(base_result, indent=2))
                    failure, = base_result["failures"]
                    self.assertEqual(failure["stage"], "call")
                    self.assertTrue(failure["test"].endswith("::test_regression"))
                    self.assertIn("assert 1 == 2", failure["message"])
                    self.assertEqual(
                        {key: fix_result[key] for key in
                         ("exit_code", "tests_collected", "tests_ran", "passed", "failed", "errors")},
                        dict(exit_code=0, tests_collected=2, tests_ran=2, passed=2,
                             failed=0, errors=0), json.dumps(fix_result, indent=2))
                finally:
                    self.root = original_root

    def deny_scanning(self, blocked, attempted):
        real_scandir = os.scandir

        def guarded_scandir(path):
            candidate = Path(path).absolute()
            if any(candidate == target or candidate.is_relative_to(target) for target in blocked):
                attempted.append(str(candidate))
                raise PermissionError(errno.EACCES, "simulated unreadable directory", str(candidate))
            return real_scandir(path)

        return mock.patch.object(preflight.os, "scandir", side_effect=guarded_scandir)

    def test_temporary_directories_are_not_visited_and_prefixed_files_survive(self):
        case, base, _, production, test_file = self.make_case()
        write_file(base, test_file, "def test_candidate_bypass():\n    pass\n")
        temporary_names = (
            ".pytest-tmp-maintenance", ".pytest-local-tmp", ".pytest-tmp",
            ".pytest-local-12345678", ".test-tmp-style-12345678",
        )
        blocked = []
        for name in temporary_names:
            write_file(base, name + "/unreadable.txt", "pytest artifact\n")
            blocked.append(base / name)
        prefixed_files = (".pytest-normal-file", ".test-tmp-normal-file")
        for name in prefixed_files:
            write_file(base, name, "ordinary file must survive\n")

        attempted = []
        captured = {}
        real_copytree = shutil.copytree

        def capture_copytree(*args, **kwargs):
            if "ignore" in kwargs:
                captured["ignore"] = kwargs["ignore"]
            return real_copytree(*args, **kwargs)

        with self.deny_scanning(blocked, attempted), mock.patch.object(
                preflight.shutil, "copytree", side_effect=capture_copytree):
            grade = preflight.prepare_grade(case, base, self.root / "grade")
        self.assertEqual(attempted, [], "Ignored temporary directories must never be scanned")
        for name in temporary_names:
            self.assertFalse((grade / name).exists(), name)
        for name in prefixed_files:
            self.assertEqual((grade / name).read_bytes(), (base / name).read_bytes())
        self.assert_frozen_overlay(case, grade, base, production)

        # Emulate a directory symlink's no-follow metadata without requiring
        # Windows symlink privileges. Its prefix must not cause exclusion.
        entry = mock.Mock()
        entry.name = ".pytest-linked-directory"
        entry.is_dir.return_value = False
        scan = mock.MagicMock()
        scan.__enter__.return_value = iter([entry])
        with mock.patch.object(preflight.os, "scandir", return_value=scan):
            ignored = captured["ignore"](base, [entry.name])
        self.assertNotIn(entry.name, ignored)
        entry.is_dir.assert_called_once_with(follow_symlinks=False)

    def test_other_unreadable_directories_still_raise(self):
        case, base, _, _, _ = self.make_case()
        blocked = base / "production-data"
        write_file(base, "production-data/important.txt", "must not be silently discarded\n")
        attempted = []
        with self.deny_scanning([blocked], attempted):
            with self.assertRaises(shutil.Error) as raised:
                preflight.prepare_grade(case, base, self.root / "grade")
        self.assertIn(str(blocked), attempted)
        self.assertIn("production-data", str(raised.exception))
        self.assertIn("simulated unreadable directory", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
