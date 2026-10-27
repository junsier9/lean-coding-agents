"""Five synthetic recovery checks; no real CLI, grader, or experiment data.

Run:
    python -B test_run_experiment.py

All workspaces, event streams, checkpoints, records, and phase inputs below are
created inside TemporaryDirectory. The adjacent controller is imported with a
stub preflight module and fake executable paths.
"""

from __future__ import annotations

import contextlib
import datetime
import importlib.util
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
CONTROLLER_PATH = Path(__file__).with_name("run_experiment.py")
preflight_stub = types.ModuleType("preflight")
preflight_stub.grade_case = lambda *args, **kwargs: None

with (
    patch.dict(sys.modules, {"preflight": preflight_stub}),
    patch.object(
        shutil,
        "which",
        side_effect=lambda name: str(Path(tempfile.gettempdir()) / ("synthetic-" + name + ".exe")),
    ),
):
    spec = importlib.util.spec_from_file_location(
        "_synthetic_controller_under_test", CONTROLLER_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import {CONTROLLER_PATH}")
    controller = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(controller)

ORIGINAL_PREPARE = controller.prepare_workspace
EVENTS = [
    {
        "type": "turn.completed",
        "usage": {"input_tokens": 100, "cached_input_tokens": 25, "output_tokens": 10},
    },
    {
        "type": "turn.completed",
        "usage": {"input_tokens": 7, "cached_input_tokens": 2, "output_tokens": 3},
    },
    {"type": "item.completed", "item": {"type": "command_execution", "exit_code": 0}},
    {"type": "item.completed", "item": {"type": "command_execution", "exit_code": 1}},
    {"type": "error", "message": "synthetic stream error"},
]
GRADE = {
    "exit_code": 0,
    "errors": 0,
    "failed": 0,
    "tests_collected": 2,
    "tests_ran": 2,
    "seconds": 3.0,
}
EVIDENCE = []


class Clock:
    def __init__(self):
        self.value = 0.0

    def monotonic(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds

    def utc(self):
        instant = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
        return (instant + datetime.timedelta(seconds=self.value)).isoformat()


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="lean-controller-synthetic-")
        self.root = Path(self.temporary.name).resolve()
        self.assertTrue(self.root.name.startswith("lean-controller-synthetic-"))
        self.clock = Clock()
        self.case = {
            "id": "synthetic-case",
            "repo": "synthetic/no-network",
            "task": "synthetic",
            "visible_test_paths": [],
            "preflight": {"guard_fix": {"tests_collected": 2, "tests_ran": 2}},
        }
        self.frozen = {"settings": {"model": "synthetic-model"}}
        self.cli_calls = 0
        self.prepare_calls = 0
        self.stack = contextlib.ExitStack()
        self.stack.enter_context(
            patch.object(controller, "settings", return_value=self.frozen["settings"])
        )
        self.stack.enter_context(
            patch.object(controller, "digest", return_value=controller.EXPECTED_SKILL)
        )
        self.stack.enter_context(patch.object(controller, "utc", side_effect=self.clock.utc))
        self.stack.enter_context(
            patch.object(controller.time, "monotonic", side_effect=self.clock.monotonic)
        )

        test = self

        class FakeCLI:
            returncode = 0

            def __init__(self, command, **kwargs):
                test.cli_calls += 1
                kwargs["stdout"].write(
                    "\n".join(json.dumps(event) for event in EVENTS) + "\n"
                )

            def communicate(self, prompt, timeout):
                test.clock.advance(5.25)

        self.stack.enter_context(patch.object(controller.subprocess, "Popen", FakeCLI))
        self.stack.enter_context(
            patch.object(
                controller.subprocess,
                "run",
                return_value=types.SimpleNamespace(stdout="synthetic diff", returncode=0),
            )
        )

        def prepare(case, root, phase, repeat, arm):
            self.prepare_calls += 1
            workspace = root / "work" / phase / case["id"] / ("r" + str(repeat)) / arm
            workspace.mkdir(parents=True)
            (workspace / "candidate.txt").write_text(
                "synthetic production work", encoding="utf-8"
            )
            return workspace

        self.stack.enter_context(
            patch.object(controller, "prepare_workspace", side_effect=prepare)
        )
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def tearDown(self):
        self.stack.close()
        self.temporary.cleanup()

    def logs(self):
        return self.root / "runs" / "pilot" / "pilot--synthetic-case--r0--baseline"

    def run_one(self):
        return controller.run_one(
            self.case, self.root, "pilot", 0, "baseline", self.frozen
        )

    def test_grade_failure_resume_preserves_cli_and_all_failed_grade_time(self):
        calls = []

        def grade(case, workspace, grade_dir, *args):
            checkpoint_path = self.logs() / "cli-checkpoint.json"
            self.assertTrue(checkpoint_path.is_file())
            self.assertFalse(checkpoint_path.with_suffix(".json.tmp").exists())
            state = controller.read(checkpoint_path)
            self.assertEqual(state["client_seconds"], 5.25)
            self.assertEqual(state["total_tokens"], 120)
            self.assertEqual(state["cli_exit_code"], 0)
            self.assertFalse(state["timeout"])
            calls.append(1)
            if len(calls) == 1:
                self.clock.advance(1.5)
                raise PermissionError("synthetic grader copy failed")
            if len(calls) == 2:
                self.clock.advance(2.25)
                raise OSError("synthetic second grading failure")
            self.clock.advance(3)
            return dict(GRADE)

        with patch.object(controller.preflight, "grade_case", side_effect=grade):
            with self.assertRaises(PermissionError):
                self.run_one()
            first = controller.read(self.logs() / "cli-checkpoint.json")
            self.assertEqual(first["grading_attempts"][0]["seconds"], 1.5)
            self.assertEqual(first["grading_attempts"][0]["error_type"], "PermissionError")
            self.assertFalse((self.logs() / "record.json").exists())

            # Recovery must not require CLI parsing or even the raw event file.
            (self.logs() / "events.jsonl").unlink()
            with patch.object(
                controller,
                "parse_events",
                side_effect=AssertionError("must not reparse CLI on recovery"),
            ):
                with self.assertRaises(OSError):
                    self.run_one()
                result = self.run_one()

            self.assertEqual(self.cli_calls, 1)
            self.assertEqual(self.prepare_calls, 1)
            self.assertEqual(len(calls), 3)
            self.assertEqual(result["started_at"], first["started_at"])
            self.assertEqual(result["usage"], first["usage"])
            self.assertEqual(
                (
                    result["input_tokens"],
                    result["cached_input_tokens"],
                    result["output_tokens"],
                    result["total_tokens"],
                ),
                (107, 27, 13, 120),
            )
            self.assertEqual(result["client_seconds"], 5.25)
            self.assertEqual(result["grade_seconds"], 6.75)
            self.assertEqual(result["wall_seconds"], 12)
            self.assertEqual(
                [attempt["status"] for attempt in result["grading_attempts"]],
                ["failed", "failed", "completed"],
            )
            self.assertEqual(
                [attempt["seconds"] for attempt in result["grading_attempts"]],
                [1.5, 2.25, 3],
            )
            self.assertEqual((result["command_items"], result["command_failures"]), (2, 1))
            self.assertEqual(result["stream_errors"], ["synthetic stream error"])
            self.assertTrue(result["accepted"] and result["completed"])
            self.assertEqual(self.run_one(), result)
            self.assertEqual(self.cli_calls, 1)
            self.assertEqual(len(calls), 3)
            self.assertEqual(
                len((self.root / "records.jsonl").read_text(encoding="utf-8").splitlines()),
                1,
            )
            EVIDENCE.append({
                "check": "two_failed_gradings_then_resume",
                "cli_calls": self.cli_calls,
                "client_seconds": result["client_seconds"],
                "grade_attempt_seconds": [
                    attempt["seconds"] for attempt in result["grading_attempts"]
                ],
                "grade_seconds": result["grade_seconds"],
                "wall_seconds": result["wall_seconds"],
                "total_tokens": result["total_tokens"],
            })

    def test_normal_path_retains_original_fields_and_timing(self):
        def grade(*args):
            self.clock.advance(3)
            return dict(GRADE)

        with patch.object(controller.preflight, "grade_case", side_effect=grade) as grader:
            result = self.run_one()
        self.assertEqual(self.cli_calls, 1)
        self.assertEqual(grader.call_count, 1)
        self.assertEqual(result["total_tokens"], 120)
        self.assertEqual(result["grade_seconds"], 3)
        self.assertEqual(result["wall_seconds"], 8.25)
        self.assertTrue(
            result["usage_complete"] and result["accepted"] and result["completed"]
        )
        EVIDENCE.append({
            "check": "normal_path",
            "cli_calls": self.cli_calls,
            "grade_seconds": result["grade_seconds"],
            "wall_seconds": result["wall_seconds"],
        })

    def test_workspace_without_checkpoint_is_still_rejected(self):
        workspace = self.root / "work" / "pilot" / self.case["id"] / "r0" / "baseline"
        workspace.mkdir(parents=True)
        with patch.object(controller, "prepare_workspace", ORIGINAL_PREPARE):
            with self.assertRaises(FileExistsError):
                self.run_one()
        self.assertEqual(self.cli_calls, 0)

    def test_checkpoint_identity_mismatch_cannot_trigger_cli(self):
        self.logs().mkdir(parents=True)
        controller.write(
            self.logs() / "cli-checkpoint.json",
            {"cli_completed": True, "key": "another-run"},
        )
        with self.assertRaises(ValueError):
            self.run_one()
        self.assertEqual(self.cli_calls, 0)


class PhaseTests(unittest.TestCase):
    def test_independent_pairs_complete_but_failed_phase_has_no_complete_summary(self):
        with tempfile.TemporaryDirectory(prefix="lean-phase-synthetic-") as directory:
            root = Path(directory).resolve()
            self.assertTrue(root.name.startswith("lean-phase-synthetic-"))
            controller.write(
                root / "partition.json",
                {"pilot": ["synthetic-broken", "synthetic-good"]},
            )
            controller.write(
                root / "eligible.json",
                {"cases": [{"id": "synthetic-broken"}, {"id": "synthetic-good"}]},
            )
            calls = []
            lock = threading.Lock()

            def run(case, root, phase, repeat, arm, frozen):
                with lock:
                    calls.append((case["id"], repeat, arm))
                if case["id"] == "synthetic-broken" and repeat == 0:
                    raise OSError("synthetic failure")
                return {"accepted": True, "usage_complete": True}

            with (
                patch.object(controller, "run_one", side_effect=run),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                with self.assertRaisesRegex(RuntimeError, "pilot incomplete: 1 pair"):
                    controller.run_phase(root, "pilot", 3)

            self.assertFalse((root / "pilot-records.json").exists())
            failure = controller.read(root / "pilot-failures.json")
            self.assertEqual(failure["status"], "incomplete")
            self.assertEqual(len(failure["failed_pairs"]), 1)
            self.assertEqual(len([call for call in calls if call[0] == "synthetic-good"]), 6)
            self.assertEqual(len(calls), 11)

            def success(*args):
                return {"accepted": True, "usage_complete": True}

            with (
                patch.object(controller, "run_one", side_effect=success),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                controller.run_phase(root, "pilot", 3)
            self.assertEqual(len(controller.read(root / "pilot-records.json")), 12)
            EVIDENCE.append({
                "check": "phase_failure_is_explicit",
                "independent_good_case_runs": 6,
                "failed_phase_complete_summary_written": False,
                "subsequent_successful_phase_runs": 12,
            })


if __name__ == "__main__":
    suite = unittest.TestSuite([
        unittest.defaultTestLoader.loadTestsFromTestCase(RecoveryTests),
        unittest.defaultTestLoader.loadTestsFromTestCase(PhaseTests),
    ])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print(json.dumps({
        "synthetic_evidence": EVIDENCE,
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
    }, ensure_ascii=False))
    raise SystemExit(not result.wasSuccessful())

