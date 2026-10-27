"""Local/fake-CLI checks only; never invoke a real coding model."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import unittest
from unittest import mock

import native_loading_smoke as smoke


class NativeLoadingSmokeTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="lean-native-smoke-test-")).resolve()
        self.addCleanup(self.cleanup)
        skill = "---\nname: lean-coding\ndescription: local test catalog\n---\nOwned test skill body.\n"
        skill_hash = hashlib.sha256(skill.encode()).hexdigest().upper()
        patch = mock.patch.object(smoke.runner, "EXPECTED_SKILL", skill_hash)
        patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.object(smoke.runner, "settings", return_value={
            "model": "gpt-6.1-sol", "model_reasoning_effort": "xhigh"})
        patch.start()
        self.addCleanup(patch.stop)
        for arm in smoke.ARMS:
            workspace = self.root / "native-loading-smoke" / arm
            (workspace / smoke.SKILL_REL).parent.mkdir(parents=True)
            (workspace / smoke.SKILL_REL).write_bytes(skill.encode("utf-8"))
            metadata = workspace / smoke.SKILL_METADATA_REL
            metadata.parent.mkdir()
            metadata.write_text("interface:\n  display_name: Lean coding\n", encoding="utf-8")
            (workspace / "logic.py").write_text("# Initial toy fixture\n", encoding="utf-8")
            (workspace / "test_logic.py").write_text("# Initial toy fixture\n", encoding="utf-8")
            (workspace / "README.md").write_text("helo\n", encoding="utf-8")
            smoke.git(workspace, "init", "-q")
            smoke.git(workspace, "add", "-A")
            smoke.git(workspace, "-c", "user.name=Validation", "-c", "user.email=validation@example.invalid",
                      "commit", "-qm", smoke.FIXTURE_SUBJECT)

    def cleanup(self):
        temp = Path(tempfile.gettempdir()).resolve()
        if self.root == temp or not self.root.is_relative_to(temp):
            raise ValueError("Unsafe test cleanup")
        def remove_readonly_git_object(function, path, error):
            target = Path(path).resolve()
            if (not isinstance(error, PermissionError) or not target.is_relative_to(self.root)
                    or ".git/objects/" not in target.as_posix()):
                raise error
            os.chmod(target, stat.S_IWRITE)
            function(target)
        shutil.rmtree(self.root, onexc=remove_readonly_git_object)

    @staticmethod
    def fake_catalog(workspace, trace, prompt, root, model_settings):
        text = "- r5 = " + str(workspace / ".agents/skills") + "\n"
        text += "- lean-coding: local test catalog (file: r5/lean-coding/SKILL.md)\n" + prompt
        return smoke.catalog_summary(json.dumps({"input": [text]}), workspace, prompt)

    def prepare(self):
        with mock.patch.object(smoke, "launch_cli", side_effect=AssertionError("No real exec in preparation")):
            return smoke.prepare(self.root, debug_fn=self.fake_catalog)

    def test_prepare_amends_only_initial_fixture_and_keeps_utf8_prompts(self):
        prepared = self.prepare()
        self.assertEqual(prepared["real_model_calls"], 0)
        self.assertEqual(smoke.prompt_for("explicit"), "$lean-coding\n\n" + smoke.prompt_for("implicit"))
        self.assertNotIn("$lean-coding", smoke.prompt_for("implicit"))
        self.assertEqual(smoke.prompt_for("negative"), "请将 README.md 中的 helo 改为 hello。\n\n" + smoke.SCOPE)
        for arm in smoke.ARMS:
            details = prepared["arms"][arm]
            workspace = Path(details["workspace"])
            self.assertEqual(smoke.git(workspace, "rev-list", "--count", "HEAD"), "1")
            self.assertEqual(smoke.git(workspace, "status", "--porcelain"), "")
            self.assertEqual(details["fixture_check"]["exit_code"], 0)
            self.assertEqual(details["fixture_check"]["tests_ran"], 7)
            self.assertTrue(details["catalog"]["catalog_alias_root_verified"])
            self.assertTrue(details["catalog"]["utf8_user_prompt_preserved"])
            self.assertFalse(details["catalog"]["full_body_visible_in_initial_prompt"])
            trace = Path(details["trace_dir"])
            self.assertEqual((trace / "prompt.txt").read_text(encoding="utf-8"), smoke.prompt_for(arm))
            self.assertFalse((trace / "events.jsonl").exists())

    def test_completion_gate_is_closed_before_any_cli(self):
        conclusion = self.root / "conclusion.json"
        launcher = mock.Mock(side_effect=AssertionError("CLI must be gated"))
        with self.assertRaisesRegex(RuntimeError, "unfinished"):
            smoke.run(self.root, conclusion_path=conclusion, launch_fn=launcher)
        expected = dict(all_planned_phases_completed=True, tasks_per_confirmatory_batch=20,
                        skill_sha256=smoke.runner.EXPECTED_SKILL)
        smoke.runner.write(conclusion, expected)
        with self.assertRaisesRegex(RuntimeError, "unfinished"):
            smoke.run(self.root, conclusion_path=conclusion, launch_fn=launcher)
        # An invalid JSON marker proves the completed replication array is never read.
        (self.root / "replication-records.json").write_text("not inspected", encoding="utf-8")
        for changed in ({"all_planned_phases_completed": False},
                        {"tasks_per_confirmatory_batch": 19}, {"skill_sha256": "wrong"}):
            smoke.runner.write(conclusion, {**expected, **changed})
            with self.assertRaisesRegex(RuntimeError, "completion gate"):
                smoke.run(self.root, conclusion_path=conclusion, launch_fn=launcher)
        launcher.assert_not_called()

    def test_three_fake_clis_record_usage_and_private_traces_separately(self):
        self.prepare()
        conclusion = self.root / "conclusion.json"
        smoke.runner.write(conclusion, dict(all_planned_phases_completed=True,
            tasks_per_confirmatory_batch=20, skill_sha256=smoke.runner.EXPECTED_SKILL))
        (self.root / "replication-records.json").write_text("not inspected", encoding="utf-8")
        called = []

        def fake_cli(command, prompt, workspace, trace, env):
            called.append(workspace.name)
            self.assertIn("exec", command)
            self.assertIn("--no-daemon", command)
            self.assertIn('model_provider="lean_eval_http"', command)
            self.assertIn("model_providers.lean_eval_http.requires_openai_auth=true", command)
            self.assertEqual(env["PYTHONDONTWRITEBYTECODE"], "1")
            if workspace.name == "negative":
                (workspace / "README.md").write_text("hello\n", encoding="utf-8")
                events = []
            else:
                events = [{"type": "item.completed", "item": {"type": "command_execution",
                    "command": "read .agents/skills/lean-coding/SKILL.md",
                    "aggregated_output": smoke.skill_body(workspace)}}]
            events.append({"type": "turn.completed", "usage": {
                "input_tokens": 11, "cached_input_tokens": 2, "output_tokens": 3}})
            (trace / "events.jsonl").write_text("\n".join(map(json.dumps, events)), encoding="utf-8")
            (trace / "final.txt").write_text("fake local CLI", encoding="utf-8")
            return dict(started_at="fake", client_seconds=0, cli_exit_code=0, timeout=False)

        output = self.root / "smoke-result.json"
        report = smoke.run(self.root, conclusion_path=conclusion, output_path=output, launch_fn=fake_cli)
        self.assertCountEqual(called, smoke.ARMS)
        self.assertFalse(report["included_in_statistics"])
        self.assertEqual(report["parallel_workers"], 3)
        for arm, result in report["arms"].items():
            self.assertEqual(result["cli_exit_code"], 0)
            self.assertEqual(result["total_tokens"], 14)
            self.assertTrue(result["usage_complete"])
            self.assertTrue(result["skill_unchanged"])
            self.assertTrue(result["visible_tests_unchanged"])
            self.assertEqual(result["body_visibility"]["full_body_text_observed_in_event_trace"], arm != "negative")
            if arm != "negative":
                self.assertEqual(result["frozen_contract_check"]["exit_code"], 0)
                self.assertEqual(result["frozen_contract_check"]["tests_ran"], 7)
                self.assertTrue((Path(result["trace_dir"]) / "private-guard-diff.json").is_file())
            else:
                self.assertTrue(result["readme_only_success"])
        with self.assertRaisesRegex(ValueError, "model activity"):
            smoke.prepare(self.root, debug_fn=self.fake_catalog)

    def test_missing_or_partial_usage_stays_missing(self):
        path = self.root / "fake-events.jsonl"
        for event in ({"type": "turn.completed"},
                      {"type": "turn.completed", "usage": {"input_tokens": 5, "output_tokens": None}}):
            with self.subTest(event=event):
                path.write_text(json.dumps(event), encoding="utf-8")
                result = smoke.observe_trace(path, "Owned test skill body.")
                self.assertTrue(result["turn_completed"])
                self.assertFalse(result["usage_complete"])
                self.assertIsNone(result["total_tokens"])
                self.assertIsNone(result["cached_input_tokens"])


if __name__ == "__main__":
    unittest.main()
