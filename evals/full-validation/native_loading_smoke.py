"""Separate native repository-skill smoke; preparation never invokes codex exec.

--run is deliberately gated on completion of the main 300-run experiment.
Private prompts/events stay below runtime/native-loading-smoke/.local. This
script never reads auth, installs skills, or adds smoke usage to main records.
"""
from __future__ import annotations

import argparse
import ast
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

import run_experiment as runner


HERE = Path(__file__).resolve().parent
ARMS = ("explicit", "implicit", "negative")
FIXTURE_SUBJECT = "Frozen native skill smoke input"
SKILL_REL = ".agents/skills/lean-coding/SKILL.md"
SKILL_METADATA_REL = ".agents/skills/lean-coding/agents/openai.yaml"
OFFICIAL_SOURCE = "https://learn.chatgpt.com/docs/build-skills"
SCOPE = (
    "仅编辑当前仓库；不要访问同级目录、主实验、控制器或外部参考答案；"
    "禁止联网、安装依赖、委派、提交、发布或修改全局配置；"
    "不要修改 .agents/skills 或 test_logic.py。"
)
REFACTOR_TASK = (
    "请简化 logic.py 内部求和路径中，在现有生产者和调用关系下重复的检查。"
    "保持 sum_payload 和 validate_payload 的公开边界行为：只接受 list，"
    "元素必须严格为 int（不接受 bool、float 或字符串），非法输入抛 PayloadError；"
    "不修改调用者输入，validate_payload 返回拥有独立快照的 tuple，"
    "原 list 后续变化不影响快照，并且只迭代原 list 一次。"
    "根据现有 producer/caller 源码完成重构，不扩大私有函数的对外契约。\n"
    "使用现成解释器运行有意义的本地检查：\n{python} -B -m unittest -v test_logic\n"
    "最后用中文简要报告实际改动与检查。"
)
LOGIC = '''class PayloadError(ValueError):
    pass


def validate_payload(values):
    if not isinstance(values, list):
        raise PayloadError("expected a list")
    owned = tuple(values)
    if any(type(value) is not int for value in owned):
        raise PayloadError("expected integers")
    return owned


def _sum_validated(values):
    # Only validate_payload's owned immutable tuple reaches this function.
    if values is None:
        raise PayloadError("expected a list")
    for value in values:
        if type(value) is not int:
            raise PayloadError("expected integers")
    return sum(values)


def sum_payload(values):
    return _sum_validated(validate_payload(values))
'''
TESTS = '''import unittest
from logic import sum_payload, validate_payload, PayloadError


class SinglePassList(list):
    def __init__(self, values):
        super().__init__(values)
        self.iterations = 0

    def __iter__(self):
        self.iterations += 1
        if self.iterations > 1:
            raise AssertionError("input iterated more than once")
        return super().__iter__()


class Contract(unittest.TestCase):
    def test_normal(self):
        self.assertEqual(sum_payload([1, 2, -3]), 0)

    def test_empty(self):
        self.assertEqual(sum_payload([]), 0)

    def test_input_not_mutated(self):
        values = [2, 3]
        self.assertEqual(sum_payload(values), 5)
        self.assertEqual(values, [2, 3])

    def test_non_lists(self):
        for values in (None, 3, (1, 2), "12"):
            with self.subTest(values=values), self.assertRaisesRegex(PayloadError, "expected a list"):
                sum_payload(values)

    def test_non_integers(self):
        for values in ([True], [1.0], ["1"], [None]):
            with self.subTest(values=values), self.assertRaisesRegex(PayloadError, "expected integers"):
                sum_payload(values)

    def test_owned_tuple_snapshot(self):
        values = [2, 3]
        owned = validate_payload(values)
        self.assertIs(type(owned), tuple)
        values[:] = [99, 100]
        self.assertEqual(owned, (2, 3))

    def test_original_list_iterated_once(self):
        values = SinglePassList([1, 2, 3])
        self.assertEqual(sum_payload(values), 6)
        self.assertEqual(values.iterations, 1)


if __name__ == "__main__":
    unittest.main()
'''


def child(path, root):
    path, root = Path(path).resolve(), Path(root).resolve()
    if path == root or not path.is_relative_to(root):
        raise ValueError(f"Path escaped smoke runtime: {path}")
    return path


def private_root(root):
    return child(Path(root) / "native-loading-smoke" / ".local", root)


def git(workspace, *args):
    return subprocess.run([str(runner.GIT), "-C", str(workspace), *args],
                          check=True, capture_output=True, text=True, encoding="utf-8").stdout.strip()


def prompt_for(arm):
    if arm == "negative":
        task = "请将 README.md 中的 helo 改为 hello。"
    else:
        task = REFACTOR_TASK.format(python=runner.PYTHON)
    return ("$lean-coding\n\n" if arm == "explicit" else "") + task + "\n\n" + SCOPE


def skill_body(workspace):
    text = (workspace / SKILL_REL).read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    return re.sub(r"\A---\n.*?\n---\n", "", text, count=1, flags=re.S).strip()


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def catalog_summary(raw, workspace, prompt):
    # Only our own catalog entry/root alias is exported, never the full prompt.
    text = "\n".join(strings(json.loads(raw))).replace("\r\n", "\n")
    path = workspace / SKILL_REL
    alias_match = re.search(r"file:\s*(r\d+)/lean-coding/SKILL\.md", text)
    alias = alias_match.group(1) if alias_match else None
    root = str(path.parent.parent.resolve())
    normalized = text.replace("\\", "/")
    root_visible = str(Path(root)).replace("\\", "/") in normalized
    alias_root_verified = bool(alias and any(
        re.search(r"\b" + re.escape(alias) + r"\b", line) and root.replace("\\", "/") in line
        for line in normalized.splitlines()))
    direct_visible = str(path.resolve()).replace("\\", "/") in normalized
    body = skill_body(workspace)
    return {
        "repo_skill_catalog_metadata_present": bool(alias_root_verified or direct_visible),
        "resolved_catalog_alias": alias,
        "resolved_catalog_root": root,
        "catalog_alias_root_verified": alias_root_verified,
        "root_visible": root_visible,
        "full_body_visible_in_initial_prompt": bool(body and body in text),
        "utf8_user_prompt_preserved": prompt in text,
        "source": OFFICIAL_SOURCE,
        "prompt_probe_only_not_model_behavior_test": True,
    }


def command_prefix(workspace, root, model_settings):
    env = runner.environment(workspace, root)
    command = [
        str(runner.CODEX), "--no-daemon", "-a", "never", "-s", "workspace-write",
        "-c", 'model_provider="lean_eval_http"',
        "-c", 'model_providers.lean_eval_http.name="OpenAI evaluation HTTP"',
        "-c", "model_providers.lean_eval_http.requires_openai_auth=true",
        "-c", "model_providers.lean_eval_http.supports_websockets=false",
        "-c", "model=" + json.dumps(model_settings["model"]),
        "-c", "model_reasoning_effort=" + json.dumps(model_settings["model_reasoning_effort"]),
        "-c", "shell_environment_policy.set={" + ",".join(
            name + "=" + json.dumps(env[name]) for name in
            ("PYTHONPATH", "PYTEST_DISABLE_PLUGIN_AUTOLOAD", "PYTHONDONTWRITEBYTECODE", "PYTHONIOENCODING")) + "}",
    ]
    return command, env


def render_catalog(workspace, trace, prompt, root, model_settings):
    prefix, env = command_prefix(workspace, root, model_settings)
    command = prefix + ["-C", str(workspace), "debug", "prompt-input", prompt]
    completed = subprocess.run(command, cwd=workspace, env=env, capture_output=True,
                               text=True, encoding="utf-8", timeout=60)
    (trace / "catalog-raw.json").write_text(completed.stdout, encoding="utf-8")
    (trace / "catalog-stderr.txt").write_text(completed.stderr, encoding="utf-8")
    if completed.returncode:
        raise RuntimeError(f"Native catalog debug failed with exit {completed.returncode}; see {trace}")
    summary = catalog_summary(completed.stdout, workspace, prompt)
    summary["debug_exit_code"] = completed.returncode
    if not summary["repo_skill_catalog_metadata_present"] or not summary["utf8_user_prompt_preserved"]:
        raise RuntimeError("Own repository catalog or UTF-8 prompt could not be verified")
    return summary


def assert_owned_fixture(root, arm, *, allow_started=False):
    smoke = child(Path(root) / "native-loading-smoke", root)
    workspace = child(smoke / arm, smoke)
    if arm not in ARMS or not (workspace / ".git").is_dir():
        raise ValueError("Not one of the three owned fixture repositories")
    if Path(git(workspace, "rev-parse", "--show-toplevel")).resolve() != workspace:
        raise ValueError("Fixture repository root mismatch")
    if git(workspace, "rev-list", "--count", "HEAD") != "1":
        raise ValueError("Only a fixture's single initial commit may be amended")
    if git(workspace, "show", "-s", "--format=%s", "HEAD") != FIXTURE_SUBJECT:
        raise ValueError("Unrecognized fixture initial commit")
    tracked = set(git(workspace, "ls-files").splitlines())
    if tracked != {SKILL_REL, SKILL_METADATA_REL, "logic.py", "test_logic.py", "README.md"}:
        raise ValueError("Unexpected fixture tracked files")
    if runner.digest(workspace / SKILL_REL) != runner.EXPECTED_SKILL:
        raise ValueError("Frozen repository skill changed")
    if not allow_started:
        # Existence checks only: never read any earlier model record content.
        trace = private_root(root) / arm
        if any((trace / name).exists() for name in ("started.json", "events.jsonl", "cli.json", "record.json")):
            raise ValueError("Refusing to prepare/amend a fixture with model activity")
        if any((workspace / name).exists() for name in ("record.json", "events.jsonl", "final.txt")):
            raise ValueError("Unexpected model artifact in fixture")
    return workspace


def fixture_check(workspace, log):
    command = [str(runner.PYTHON), "-B", "-m", "unittest", "-v", "test_logic"]
    env = os.environ.copy()
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8", PYTHONPATH=str(workspace))
    completed = subprocess.run(command, cwd=workspace, env=env, capture_output=True,
                               text=True, encoding="utf-8", timeout=60)
    text = completed.stdout + completed.stderr
    log.write_text(text, encoding="utf-8")
    match = re.search(r"Ran (\d+) tests?", text)
    result = {"command": command, "exit_code": completed.returncode,
              "tests_ran": int(match.group(1)) if match else 0, "log_path": str(log)}
    return result


def prepare(root, *, debug_fn=render_catalog):
    root = Path(root).resolve()
    model_settings = runner.settings()  # Only the runner's model/effort allowlist; no auth access.
    if model_settings != {"model": "gpt-6.1-sol", "model_reasoning_effort": "xhigh"}:
        raise ValueError("Keep the configured benchmark model/reasoning settings")
    local = private_root(root)
    local.mkdir(parents=True, exist_ok=True)
    # Validate ALL three repositories before changing any initial commit.
    workspaces = {arm: assert_owned_fixture(root, arm) for arm in ARMS}
    for workspace in workspaces.values():
        if git(workspace, "status", "--porcelain"):
            raise ValueError("Refusing to amend a dirty fixture")
    manifest = {"version": 1, "prepared_at": runner.utc(), "runtime_root": str(root),
                "model_settings": model_settings, "skill_sha256": runner.EXPECTED_SKILL,
                "separate_from_main_300": True, "real_model_calls": 0, "arms": {}}
    for arm, workspace in workspaces.items():
        trace = child(local / arm, local)
        trace.mkdir(parents=True, exist_ok=True)
        before = git(workspace, "rev-parse", "HEAD")
        (workspace / "logic.py").write_text(LOGIC, encoding="utf-8")
        (workspace / "test_logic.py").write_text(TESTS, encoding="utf-8")
        (workspace / "README.md").write_text("helo\n", encoding="utf-8")
        if git(workspace, "status", "--porcelain"):
            git(workspace, "add", "--", "logic.py", "test_logic.py", "README.md")
            git(workspace, "-c", "user.name=Validation", "-c", "user.email=validation@example.invalid",
                "commit", "--amend", "--no-edit", "-q")
        check = fixture_check(workspace, trace / "initial-tests.log")
        if check["exit_code"] or check["tests_ran"] != 7:
            raise RuntimeError(f"Owned invariant fixture failed: {check}")
        prompt = prompt_for(arm)
        (trace / "prompt.txt").write_text(prompt, encoding="utf-8")
        (trace / "frozen-test_logic.py").write_text(TESTS, encoding="utf-8")
        catalog = debug_fn(workspace, trace, prompt, root, model_settings)
        manifest["arms"][arm] = {
            "workspace": str(workspace), "trace_dir": str(trace), "initial_commit_before": before,
            "initial_commit": git(workspace, "rev-parse", "HEAD"),
            "initial_files_sha256": {name: runner.digest(workspace / name)
                                     for name in ("logic.py", "test_logic.py", "README.md", SKILL_REL, SKILL_METADATA_REL)},
            "prompt_sha256": runner.digest(trace / "prompt.txt"),
            "fixture_check": check, "catalog": catalog,
        }
    runner.write(local / "prepared.json", manifest)
    return manifest


def completion_gate(root, conclusion_path=None):
    path = Path(conclusion_path) if conclusion_path else HERE / "results" / "conclusion.json"
    if not path.is_file() or not (Path(root) / "replication-records.json").is_file():
        raise RuntimeError("Main experiment is unfinished: require conclusion and completed replication marker")
    data = runner.read(path)
    expected = {"all_planned_phases_completed": True, "tasks_per_confirmatory_batch": 20,
                "skill_sha256": runner.EXPECTED_SKILL}
    if any(data.get(key) != value for key, value in expected.items()):
        raise RuntimeError("Main experiment completion gate does not match the frozen 120-run replication")
    return {"conclusion_path": str(path), **expected, "replication_marker_exists": True,
            "replication_array_not_read": True, "checked_at": runner.utc()}


def launch_cli(command, prompt, workspace, trace, env):
    started_at, start, timed_out = runner.utc(), time.monotonic(), False
    with (trace / "events.jsonl").open("w", encoding="utf-8") as output, \
            (trace / "stderr.txt").open("w", encoding="utf-8") as error:
        proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=output, stderr=error,
                                text=True, encoding="utf-8", cwd=workspace, env=env,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            proc.communicate(prompt, timeout=600)
        except subprocess.TimeoutExpired:
            timed_out = True
            if proc.poll() is None:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
                else:
                    proc.kill()
            proc.wait(timeout=20)
    return {"started_at": started_at, "client_seconds": time.monotonic() - start,
            "cli_exit_code": proc.returncode, "timeout": timed_out}


def private_guard_observation(source):
    try:
        tree = ast.parse(source)
        function = next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef) and node.name == "_sum_validated")
        return {"if_statements": sum(isinstance(node, ast.If) for node in ast.walk(function)),
                "for_statements": sum(isinstance(node, (ast.For, ast.AsyncFor)) for node in ast.walk(function)),
                "source": ast.get_source_segment(source, function), "static_only_not_acceptance_test": True}
    except (SyntaxError, StopIteration):
        return {"unavailable": True, "static_only_not_acceptance_test": True}


def observe_trace(path, body):
    turns, items, events = runner.parse_events(path)
    text = "\n".join(strings(events)).replace("\r\n", "\n")
    commands = [item.get("command", "") for item in items if item.get("type") == "command_execution"]
    def measured_total(name):
        values = [turn.get(name) for turn in turns]
        return sum(values) if values and all(type(value) is int and value >= 0 for value in values) else None
    input_tokens, output_tokens = measured_total("input_tokens"), measured_total("output_tokens")
    complete = input_tokens is not None and output_tokens is not None
    return {"usage": turns, "usage_complete": complete,
            "input_tokens": input_tokens, "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens if complete else None,
            "cached_input_tokens": measured_total("cached_input_tokens"),
            "turn_completed": any(e.get("type") == "turn.completed" for e in events),
            "stream_error_count": sum(e.get("type") == "error" for e in events),
            "body_visibility": {
                "full_body_text_observed_in_event_trace": bool(body and body in text),
                "skill_read_command_observed": any("lean-coding" in c and "SKILL.md" in c for c in commands),
                "full_model_input_visible": False,
                "limitation": "exec --json does not expose the complete model input; absence in events is not proof of nonactivation.",
            }}


def run_arm(root, arm, prepared, *, launch_fn=launch_cli):
    details = prepared["arms"][arm]
    workspace = assert_owned_fixture(root, arm, allow_started=True)
    trace = child(details["trace_dir"], private_root(root))
    if any((trace / name).exists() for name in ("started.json", "events.jsonl", "cli.json", "record.json")):
        raise RuntimeError("Refusing to repeat or overwrite a native model attempt: " + arm)
    if git(workspace, "rev-parse", "HEAD") != details["initial_commit"] or git(workspace, "status", "--porcelain"):
        raise ValueError("Prepared initial fixture changed")
    for name, value in details["initial_files_sha256"].items():
        if runner.digest(workspace / name) != value:
            raise ValueError("Prepared fixture file changed: " + name)
    prompt_path = trace / "prompt.txt"
    if runner.digest(prompt_path) != details["prompt_sha256"]:
        raise ValueError("Prepared prompt changed")
    frozen_body = skill_body(workspace)
    prefix, env = command_prefix(workspace, root, prepared["model_settings"])
    command = prefix + ["exec", "--ephemeral", "--skip-git-repo-check", "--json", "--color", "never",
                        "-C", str(workspace), "-o", str(trace / "final.txt"), "-"]
    runner.write(trace / "started.json", {"started_at": runner.utc(), "arm": arm, "command": command})
    result = launch_fn(command, prompt_path.read_text(encoding="utf-8"), workspace, trace, env)
    result.update(observe_trace(trace / "events.jsonl", frozen_body))
    if result["timeout"]:
        result["usage_complete"] = False
    result.update(arm=arm, command=command, trace_dir=str(trace), separate_from_main_300=True)
    # Persist paid work before independent fixture checks.
    runner.write(trace / "cli.json", result)
    changed = git(workspace, "diff", "--name-only", "HEAD").splitlines()
    result["changed_tracked_paths"] = changed
    result["untracked_paths"] = git(workspace, "ls-files", "--others", "--exclude-standard").splitlines()
    result["skill_unchanged"] = runner.digest(workspace / SKILL_REL) == runner.EXPECTED_SKILL
    result["skill_metadata_unchanged"] = runner.digest(workspace / SKILL_METADATA_REL) == details["initial_files_sha256"][SKILL_METADATA_REL]
    result["visible_tests_unchanged"] = runner.digest(workspace / "test_logic.py") == details["initial_files_sha256"]["test_logic.py"]
    if arm == "negative":
        result["readme_only_success"] = changed == ["README.md"] and not result["untracked_paths"] and (workspace / "README.md").read_text(encoding="utf-8").strip() == "hello"
    else:
        grade = child(trace / "grade", private_root(root))
        if grade.exists():
            raise FileExistsError("Do not reuse a prior native smoke grade")
        shutil.copytree(workspace, grade, ignore=shutil.ignore_patterns(".git", ".agents", "__pycache__", ".pytest_cache"))
        shutil.copy2(trace / "frozen-test_logic.py", grade / "test_logic.py")
        result["frozen_contract_check"] = fixture_check(grade, trace / "contract-tests.log")
        before, after = private_guard_observation(LOGIC), private_guard_observation((workspace / "logic.py").read_text(encoding="utf-8"))
        # Private full source/diff stays local; public result exports counts only.
        runner.write(trace / "private-guard-diff.json", {"before": before, "after": after,
                     "diff": git(workspace, "diff", "HEAD", "--", "logic.py")})
        result["private_guard_static_observation"] = {
            side: {key: value for key, value in observation.items() if key != "source"}
            for side, observation in (("before", before), ("after", after))}
    runner.write(trace / "record.json", result)
    return result


def run(root, *, conclusion_path=None, output_path=None, launch_fn=launch_cli):
    root = Path(root).resolve()
    gate = completion_gate(root, conclusion_path)
    prepared = runner.read(private_root(root) / "prepared.json")
    if prepared["runtime_root"] != str(root) or prepared["skill_sha256"] != runner.EXPECTED_SKILL:
        raise ValueError("Prepared native smoke identity mismatch")
    if runner.settings() != prepared["model_settings"]:
        raise ValueError("Configured model settings changed since preparation")
    # Check all arms before launching any of the three concurrent owned CLIs.
    for arm in ARMS:
        assert_owned_fixture(root, arm)
    results = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(run_arm, root, arm, prepared, launch_fn=launch_fn): arm for arm in ARMS}
        for future in as_completed(futures):
            arm = futures[future]
            try:
                results[arm] = future.result()
            except Exception as exc:
                results[arm] = {"arm": arm, "infrastructure_error": type(exc).__name__, "error": str(exc),
                                "trace_dir": prepared["arms"][arm]["trace_dir"]}
    report = {"completed_at": runner.utc(), "purpose": "Native repository skill loading smoke only",
              "separate_from_main_300": True, "included_in_statistics": False,
              "official_source": OFFICIAL_SOURCE, "gate": gate, "skill_sha256": runner.EXPECTED_SKILL,
              "model_settings": prepared["model_settings"], "parallel_workers": 3,
              "catalog": {arm: prepared["arms"][arm]["catalog"] for arm in ARMS},
              "arms": {arm: results[arm] for arm in ARMS}}
    runner.write(output_path or HERE / "results" / "native-loading-smoke.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare-only", action="store_true")
    mode.add_argument("--run", action="store_true")
    args = parser.parse_args()
    if args.prepare_only:
        result = prepare(args.root)
        print(json.dumps({"prepared": True, "real_model_calls": 0,
                          "manifest": str(private_root(args.root) / "prepared.json"),
                          "catalog": {arm: result["arms"][arm]["catalog"] for arm in ARMS}}, ensure_ascii=False))
    else:
        report = run(args.root)
        print(json.dumps({"completed": True, "included_in_statistics": False,
                          "output": str(HERE / "results" / "native-loading-smoke.json"),
                          "arms": {arm: {key: value for key, value in result.items()
                                         if key in ("cli_exit_code", "usage_complete", "infrastructure_error")}
                                   for arm, result in report["arms"].items()}}, ensure_ascii=False))
        return int(any("infrastructure_error" in r or r.get("cli_exit_code") != 0
                       for r in report["arms"].values()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
