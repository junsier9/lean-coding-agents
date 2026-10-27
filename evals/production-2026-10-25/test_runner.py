"""Meaningful runner recovery/grade tests. No actual Codex invocation."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location("production_runner_under_test", Path(__file__).with_name("runner.py"))
r = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(r)

TEST = '''import unittest
from service import value

class Contract(unittest.TestCase):
    def test_value(self):
        self.assertEqual(value(), 2)
'''


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="lean-production-runner-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.skill = self.root / "frozen-skill.md"
        self.skill.write_text('---\nname: lean-coding\ndescription: Fixture\n---\n\n# Fixture\nUse contracts.\n', encoding="utf-8")
        self.case = {"id": "d01", "family": "contract", "task": "Correct value to two.",
                     "files": {"service.py": "def value():\n    return 1\n"},
                     "reference_files": {"service.py": "def value():\n    return 2\n"},
                     "hidden_tests": {"test_contract.py": TEST}, "critical": True, "checks": ["value is two"]}
        self.model_calls = []
        self.settings_patch = mock.patch.object(r.transport, "settings", return_value=r.SETTINGS.copy())
        self.settings_patch.start()
        self.addCleanup(self.settings_patch.stop)
        self.python_patch = mock.patch.object(r, "PYTHON", Path(sys.executable))
        self.python_patch.start()
        self.addCleanup(self.python_patch.stop)

    def fake_cli(self, workspace, logs, prompt, identity, *, fix=True, usage=True, malformed=False):
        self.model_calls.append(identity["key"])
        self.assertFalse((workspace / "test_contract.py").exists(), "Hidden test leaked into candidate")
        self.assertEqual(r.git(workspace, "remote"), "")
        self.assertEqual(r.git(workspace, "rev-list", "--count", "HEAD").strip(), "1")
        if fix:
            (workspace / "service.py").write_text(self.case["reference_files"]["service.py"], encoding="utf-8")
        events = [{"type": "thread.started", "thread_id": "thread-" + identity["key"]}]
        if identity["arm"] == "with-skill":
            events.append({"type": "item.completed", "item": {"type": "command_execution", "command": "Get-Content .agents/skills/lean-coding/SKILL.md", "exit_code": 0,
                           "aggregated_output": self.skill.read_text(encoding="utf-8")}})
        if usage:
            events.append({"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 40, "output_tokens": 20}})
        (logs / "events.jsonl").write_text("invalid-json\n" if malformed else "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
        (logs / "stderr.txt").write_text("", encoding="utf-8")
        return {**identity, "started_at": "2026-10-25T00:00:00+00:00", "client_seconds": 12.25,
                "cli_exit_code": 0 if usage else -1, "timeout": not usage}

    def run_trial(self, **kwargs):
        return r.run_suite(self.root, [self.case], "development", self.skill,
                           repeats=kwargs.pop("repeats", 1), arms=kwargs.pop("arms", ("with-skill",)), workers=kwargs.pop("workers", 1), **kwargs)

    def test_real_preflight_base_fails_reference_passes_and_bad_cache_stays_rejected(self):
        result = r.preflight_case(self.case, self.root)
        self.assertTrue(result["qualified"])
        self.assertEqual(result["base"]["failed"], 2)
        self.assertEqual(result["reference"]["passed"], 2)
        invalid = copy.deepcopy(self.case)
        invalid["id"] = "d02"
        invalid["reference_files"] = invalid["files"]
        for _ in range(2):
            with self.assertRaises(ValueError):
                r.preflight_case(invalid, self.root)
        self.assertEqual(self.model_calls, [])

    def test_fresh_pair_contexts_hidden_contracts_and_journal_exactly_once(self):
        with mock.patch.object(r, "launch_cli", side_effect=self.fake_cli):
            rows = self.run_trial(arms=("baseline", "with-skill"), repeats=2, workers=2)
            repeated = self.run_trial(arms=("baseline", "with-skill"), repeats=2, workers=2)
        self.assertEqual(len(self.model_calls), 4)
        self.assertEqual(len({row["workspace"] for row in rows}), 4)
        self.assertEqual(len({row["thread_ids"][0] for row in rows}), 4)
        self.assertTrue(all(row["accepted"] for row in rows))
        self.assertTrue(all(row["total_tokens"] == 120 for row in rows))
        self.assertTrue(all(row["skill_body_read_observed"] == (row["arm"] == "with-skill") for row in rows))
        self.assertEqual(len((self.root / "records.jsonl").read_text(encoding="utf-8").splitlines()), 4)
        self.assertEqual({row["key"] for row in repeated}, {row["key"] for row in rows})

    def test_grading_failure_resumes_checkpoint_without_repeating_paid_cli(self):
        original = r.synthetic_grade
        graded = []
        def fail_once(case, workspace, grade):
            if "work" in workspace.parts and not graded:
                graded.append(True)
                raise PermissionError("simulated independent grader copy failure")
            return original(case, workspace, grade)
        with mock.patch.object(r, "launch_cli", side_effect=self.fake_cli), mock.patch.object(r, "synthetic_grade", side_effect=fail_once):
            with self.assertRaises(RuntimeError):
                self.run_trial()
            rows = self.run_trial()
        self.assertEqual(len(self.model_calls), 1)
        row, = rows
        self.assertTrue(row["accepted"])
        self.assertEqual(row["client_seconds"], 12.25)
        self.assertEqual(row["total_tokens"], 120)
        self.assertEqual([attempt["status"] for attempt in row["grading_attempts"]], ["failed", "completed"])
        self.assertGreaterEqual(row["wall_seconds"], 12.25)

    def test_parse_error_keeps_paid_client_metadata_and_recovery_does_not_launch(self):
        def malformed(*args):
            return self.fake_cli(*args, malformed=True)
        with mock.patch.object(r, "launch_cli", side_effect=malformed):
            with self.assertRaises(RuntimeError):
                self.run_trial()
        log = self.root / "runs/development/development--d01--r0--with-skill"
        self.assertTrue((log / "client-done.json").is_file())
        self.assertFalse((log / "cli-checkpoint.json").exists())
        (log / "events.jsonl").write_text(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 20}}) + "\n", encoding="utf-8")
        with mock.patch.object(r, "launch_cli", side_effect=AssertionError("must not invoke again")):
            row, = self.run_trial()
        self.assertTrue(row["accepted"])
        self.assertEqual(len(self.model_calls), 1)
        self.assertEqual(row["total_tokens"], 120)
        self.assertIsNone(row["cached_input_tokens"])

    def test_timeout_usage_is_never_zero_even_if_artifact_passes(self):
        with mock.patch.object(r, "launch_cli", side_effect=lambda *args: self.fake_cli(*args, usage=False)):
            row, = self.run_trial()
        self.assertTrue(row["accepted"])
        self.assertTrue(row["timeout"])
        self.assertFalse(row["usage_complete"])
        for name in ("input_tokens", "output_tokens", "total_tokens", "cached_input_tokens"):
            self.assertIsNone(row[name])

    def test_candidate_test_or_unittest_shadow_cannot_replace_frozen_grader(self):
        def cheating(*args):
            result = self.fake_cli(*args, fix=False)
            workspace = args[0]
            (workspace / "test_contract.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
            (workspace / "unittest.py").write_text("print('Ran 1 test in 0.001s\\n\\nOK')\n", encoding="utf-8")
            (workspace / "conftest.py").write_text("raise RuntimeError('should never load')\n", encoding="utf-8")
            return result
        with mock.patch.object(r, "launch_cli", side_effect=cheating):
            row, = self.run_trial()
        self.assertFalse(row["accepted"])
        self.assertEqual(row["grade"]["failed"], 2)
        self.assertEqual(row["grade"]["tests_ran"], 2)
        self.assertFalse((self.root / "grades/development" / row["key"] / "unittest.py").exists())

    def test_exit_zero_without_running_contracts_cannot_pass(self):
        def exiting(*args):
            result = self.fake_cli(*args)
            (args[0] / "service.py").write_text("import os\nos._exit(0)\n", encoding="utf-8")
            return result
        with mock.patch.object(r, "launch_cli", side_effect=exiting):
            row, = self.run_trial()
        self.assertEqual(row["grade"]["exit_code"], 0)
        self.assertEqual(row["grade"]["tests_ran"], 0)
        self.assertFalse(row["accepted"])

    def test_runtime_boundary_assert_is_caught_in_optimized_mode(self):
        case = copy.deepcopy(self.case)
        case["files"]["service.py"] = "def value(x=None):\n    return 1\n"
        case["reference_files"]["service.py"] = "def value(x=None):\n    if x == 'bad':\n        raise AssertionError('bad input')\n    return 2\n"
        case["hidden_tests"]["test_contract.py"] = TEST + "\n    def test_bad(self):\n        with self.assertRaises(AssertionError):\n            value('bad')\n"
        self.case = case
        def asserting(*args):
            result = self.fake_cli(*args)
            (args[0] / "service.py").write_text("def value(x=None):\n    assert x != 'bad', 'bad input'\n    return 2\n", encoding="utf-8")
            return result
        with mock.patch.object(r, "launch_cli", side_effect=asserting):
            row, = self.run_trial()
        self.assertEqual(row["grade"]["modes"]["normal"]["exit_code"], 0)
        self.assertEqual(row["grade"]["modes"]["optimized"]["failed"], 1)
        self.assertFalse(row["accepted"])

    def test_partial_workspace_and_changed_manifest_refuse_implicit_rerun(self):
        workspace = self.root / "work/development/d01/r0/with-skill"
        workspace.mkdir(parents=True)
        with mock.patch.object(r, "launch_cli", side_effect=AssertionError("must not launch")):
            with self.assertRaises(RuntimeError):
                self.run_trial()
        self.skill.write_text(self.skill.read_text(encoding="utf-8") + "Changed.\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "manifest changed"):
            self.run_trial()
        self.assertFalse((self.root / "active-suite.lock").exists())

    def test_schedule_is_deterministic_balanced_and_case_order_independent(self):
        cases = [self.case, {**self.case, "id": "d02"}]
        plan = r.schedule(cases, "formal", 3, ("baseline", "with-skill"))
        self.assertEqual(plan, r.schedule(list(reversed(cases)), "formal", 3, ("baseline", "with-skill")))
        self.assertEqual(sum(pair["arms"][0] == "baseline" for pair in plan), 3)
        self.assertEqual(len({(pair["case_id"], pair["repeat"]) for pair in plan}), 6)

    def test_upstream_source_uses_same_checkpoint_and_existing_frozen_grader(self):
        source = self.root / "snapshots/source"
        source.mkdir(parents=True)
        (source / "service.py").write_text("def value():\n    return 1\n", encoding="utf-8")
        (self.root / "testdeps").mkdir()
        expected = {"tests_collected": 5, "tests_ran": 5, "skipped": 0}
        case = {"id": "real01", "family": "real", "repo": "example/library", "task": "Fix value.", "source_dir": str(source),
                "upstream": {"repo": "example/library", "fix_dir": str(source), "visible_test_paths": ["tests/test_existing.py"],
                             "preflight": {"guard_fix": expected, "overlay_sha256": {"service.py": r.digest(source / "service.py")}}}}
        grade = {**expected, "exit_code": 0, "failed": 0, "errors": 0, "passed": 5, "seconds": .25}
        with mock.patch.object(r, "launch_cli", side_effect=self.fake_cli), mock.patch.object(r.upstream_grader, "grade_case", return_value=grade) as grader:
            row, = r.run_suite(self.root, [case], "formal", self.skill, repeats=1, arms=("baseline",), workers=1)
        self.assertTrue(row["accepted"])
        self.assertEqual(row["repo"], "example/library")
        self.assertEqual(grader.call_count, 1)
        self.assertEqual(grader.call_args.args[-1], self.root / "testdeps")
        self.assertTrue((Path(row["workspace"]) / ".benchmark-deps").is_dir())
        self.assertNotIn("guard_fix", (Path(row["workspace"]) / "TASK.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
