"""Native Codex trials over frozen Python contracts. One controller per root.

The synthetic grader restores hidden tests in a separate tree. Upstream cases
reuse the already validated whole-file grader. CLI completion is persisted
before usage parsing and grading, so recovery never repeats paid model work.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import contextlib
import datetime as dt
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import random
import re
import shutil
import subprocess
import sys
import threading
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "full-validation"))
import preflight as upstream_grader
import run_experiment as transport

PYTHON, CODEX, GIT = transport.PYTHON, transport.CODEX, transport.GIT
SETTINGS = {"model": "gpt-6.1-sol", "model_reasoning_effort": "xhigh"}
SEED = 20261025
LOCK = threading.Lock()
SKILL_REL = ".agents/skills/lean-coding/SKILL.md"
METADATA = 'interface:\n  display_name: "Lean Coding"\n  short_description: "Simplify redundant defenses using real contracts"\n'
COPY_IGNORE = shutil.ignore_patterns(".git", ".venv", "venv", ".tox", "__pycache__", ".pytest_cache", ".benchmark-deps")


def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest().upper()


def case_digest(case):
    return hashlib.sha256(json.dumps(case, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest().upper()


def component(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}", value):
        raise ValueError("Invalid trial path component: " + repr(value))
    return value


def inside(path, root):
    return upstream_grader.inside(path, root)


def put_files(directory, files):
    for relative, content in files.items():
        name = PurePosixPath(relative)
        if name.is_absolute() or ".." in name.parts or ":" in relative or "\\" in relative:
            raise ValueError("Unsafe fixture path: " + relative)
        destination = inside(directory / relative, directory)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")


def git(workspace, *args):
    return subprocess.run([str(GIT), *args], cwd=workspace, check=True,
                          capture_output=True, text=True, encoding="utf-8").stdout


def environment(workspace):
    env = os.environ.copy()
    env.update(PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1",
               PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
               PYTHONPATH=os.pathsep.join([str(workspace / "src"), str(workspace), str(workspace / ".benchmark-deps")]))
    return env


def prepare_workspace(case, root, phase, repeat, arm, skill):
    workspace = inside(root / "work" / phase / component(case["id"]) / f"r{repeat}" / arm, root)
    if workspace.exists():
        raise FileExistsError("Partial workspace has no recoverable CLI checkpoint: " + str(workspace))
    workspace.parent.mkdir(parents=True, exist_ok=True)
    if "upstream" in case:
        shutil.copytree(case["source_dir"], workspace, ignore=COPY_IGNORE)
        shutil.copytree(root / "testdeps", workspace / ".benchmark-deps", ignore=COPY_IGNORE)
        paths = case["upstream"]["visible_test_paths"]
        instructions = (f"Existing interpreter: {PYTHON}\n"
                        "pytest and dependencies are already available. Do not install anything.\n"
                        "Use -m pytest -q -o addopts= -p no:cacheprovider -p pytest_timeout --timeout=60.\n"
                        "Relevant existing tests: " + ", ".join(paths))
    else:
        workspace.mkdir()
        if set(case["files"]) & set(case["hidden_tests"]):
            raise ValueError("Hidden grader files must not appear in the visible fixture")
        put_files(workspace, case["files"])
        put_files(workspace, case.get("visible_tests", {}))
        instructions = (f"Existing standard-library interpreter: {PYTHON}\n"
                        "Use focused local checks and, where present, -B -m unittest discover -v.\n"
                        "You may add meaningful local tests. No third-party dependencies are needed.")
    (workspace / "TASK.md").write_text(case["task"] + "\n\n" + instructions + "\n", encoding="utf-8")
    if arm == "with-skill":
        destination = workspace / SKILL_REL
        destination.parent.mkdir(parents=True)
        shutil.copyfile(skill, destination)
        put_files(destination.parent, {"agents/openai.yaml": METADATA})
    git(workspace, "init", "-q")
    git(workspace, "add", "-A", "--", ".", ":(exclude).benchmark-deps")
    git(workspace, "-c", "user.name=Validation", "-c", "user.email=validation@example.invalid",
        "commit", "-qm", "Frozen production input")
    (workspace / ".git/info/exclude").write_text(".benchmark-deps/\n", encoding="utf-8")
    return workspace


def synthetic_grade(case, candidate, grade_dir):
    # This directory is wholly owned by this invocation. Never reuse candidates.
    root = grade_dir.parent
    inside(grade_dir, root)
    if grade_dir.exists():
        shutil.rmtree(grade_dir)
    grade_dir.mkdir(parents=True)
    hidden = set(case["hidden_tests"])
    excluded = {".git", ".agents", ".benchmark-deps", ".venv", "venv", "__pycache__", ".pytest_cache"}
    for source in candidate.rglob("*"):
        relative = source.relative_to(candidate)
        if any(part in excluded or part.startswith((".pytest-", ".test-tmp")) for part in relative.parts):
            continue
        if source.is_symlink():
            raise ValueError("Synthetic candidate symlinks are outside the fixture contract")
        if not source.is_file():
            continue
        if (relative.as_posix() in hidden or any(part in {"tests", "test", "unittest"} for part in relative.parts)
                or source.name.startswith("test_") or source.name.endswith("_test.py")
                or source.name in {"conftest.py", "sitecustomize.py", "usercustomize.py", "unittest.py",
                                   "pytest.ini", ".pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml"}
                or source.suffix == ".pth"):
            continue
        destination = inside(grade_dir / relative, grade_dir)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    put_files(grade_dir, case["hidden_tests"])
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.update(PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    modes = {}
    for mode, options in (("normal", []), ("optimized", ["-O"])):
        command = [str(PYTHON), "-I", "-B", *options, "-m", "unittest", "discover", "-s", ".", "-p", "test_contract.py", "-v"]
        start, timed_out = time.monotonic(), False
        try:
            completed = subprocess.run(command, cwd=grade_dir, env=env, capture_output=True,
                                       text=True, encoding="utf-8", timeout=60)
            stdout, stderr, exit_code = completed.stdout, completed.stderr, completed.returncode
        except subprocess.TimeoutExpired as exc:
            timed_out, exit_code = True, -1
            stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else exc.stdout or ""
            stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr or ""
        seconds = time.monotonic() - start
        output = stdout + "\n" + stderr
        (grade_dir.parent / (grade_dir.name + "-" + mode + "-tests.log")).write_text(output, encoding="utf-8")
        matches = re.findall(r"^Ran (\d+) tests? in [0-9.]+s$", output, re.M)
        count = int(matches[-1]) if matches else 0
        summary = re.findall(r"^(?:FAILED|OK)(?: \(([^\n]+)\))?\s*$", output, re.M)
        details = summary[-1] if summary else ""
        value = lambda name: int(re.search(r"\b" + name + r"=(\d+)", details).group(1)) if re.search(r"\b" + name + r"=(\d+)", details) else 0
        failed, errors, skipped = value("failures"), value("errors"), value("skipped")
        modes[mode] = {"exit_code": exit_code, "tests_collected": count, "tests_ran": count,
                       "passed": max(0, count - failed - errors - skipped), "failed": failed, "errors": errors,
                       "skipped": skipped, "timeout": timed_out, "seconds": seconds, "command": command}
    return {**{name: sum(value[name] for value in modes.values()) for name in
               ("tests_collected", "tests_ran", "passed", "failed", "errors", "skipped", "seconds")},
            "exit_code": next((value["exit_code"] for value in modes.values() if value["exit_code"]), 0),
            "timeout": any(value["timeout"] for value in modes.values()), "modes": modes}


def preflight_case(case, root):
    if "upstream" in case:
        expected = case["upstream"]["preflight"]["guard_fix"]
        if expected["tests_ran"] <= 0 or expected["tests_collected"] <= 0:
            raise ValueError("Upstream case has no nonzero frozen guard count")
        for relative, expected_hash in case["upstream"]["preflight"]["overlay_sha256"].items():
            if digest(Path(case["upstream"]["fix_dir"]) / relative).lower() != expected_hash.lower():
                raise ValueError("Frozen upstream overlay changed: " + relative)
        return {"case_sha256": case_digest(case), "expected": expected, "source": "upstream_preverified"}
    path = root / "preflight" / (component(case["id"]) + ".json")
    fingerprint = case_digest(case)
    if path.exists():
        result = read(path)
        if result["case_sha256"] != fingerprint:
            raise ValueError("Case changed after preflight: " + case["id"])
        if result.get("grading_modes") != ["normal", "optimized"]:
            raise ValueError("Cached preflight does not include both normal and -O grading modes")
        if not result["qualified"]:
            raise ValueError("Cached synthetic preflight did not qualify: " + case["id"])
        return result
    work = inside(root / "preflight" / case["id"], root)
    if work.exists():
        shutil.rmtree(work)
    (work / "base").mkdir(parents=True)
    (work / "reference").mkdir()
    put_files(work / "base", case["files"])
    put_files(work / "reference", {**case["files"], **case["reference_files"]})
    base = synthetic_grade(case, work / "base", work / "base-grade")
    fixed = synthetic_grade(case, work / "reference", work / "reference-grade")
    normal = base["modes"]["normal"]
    ok = (fixed["exit_code"] == 0 and fixed["tests_ran"] > 0 and fixed["errors"] == fixed["failed"] == fixed["skipped"] == 0
          and normal["exit_code"] != 0 and normal["tests_ran"] == fixed["modes"]["normal"]["tests_ran"]
          and fixed["modes"]["normal"]["tests_ran"] == fixed["modes"]["optimized"]["tests_ran"] > 0
          and base["tests_ran"] == fixed["tests_ran"] and not base["timeout"])
    result = {"case_id": case["id"], "case_sha256": fingerprint, "base": base,
              "reference": fixed, "expected": fixed, "qualified": ok, "grading_modes": ["normal", "optimized"]}
    write(path, result)
    if not ok:
        raise ValueError("Synthetic base-fail/reference-pass preflight failed: " + case["id"])
    return result


def schedule(cases, phase, repeats, arms):
    pairs = [(case["id"], repeat) for case in sorted(cases, key=lambda case: case["id"]) for repeat in range(repeats)]
    offset = int(hashlib.sha256(phase.encode("utf-8")).hexdigest()[:8], 16)
    random.Random(SEED + offset).shuffle(pairs)
    return [{"case_id": case_id, "repeat": repeat, "arms": list(arms if index % 2 else tuple(reversed(arms)))}
            for index, (case_id, repeat) in enumerate(pairs)]


def prompt_for(arm):
    prompt = ("完成 TASK.md 的维护需求。阅读相关源码和项目指令，直接修改并运行有意义的本地检查，"
              "最后用中文报告实际改动、检查和未完成项。仅编辑当前项目；不要访问其他实验、控制器、"
              "评分器或参考实现，不要联网、安装依赖、委派、提交、发布或修改 skill。可以补充有效测试，"
              "不得削弱已有验收。使用任务文件给出的解释器。\n")
    return prompt + ("使用 $lean-coding。" if arm == "with-skill" else "本次不加载额外 skill。")


def launch_cli(workspace, logs, prompt, identity):
    env = environment(workspace)
    command = [str(CODEX), "--no-daemon", "-a", "never", "-s", "workspace-write",
               "-c", 'model_provider="lean_eval_http"',
               "-c", 'model_providers.lean_eval_http.name="OpenAI evaluation HTTP"',
               "-c", "model_providers.lean_eval_http.requires_openai_auth=true",
               "-c", "model_providers.lean_eval_http.supports_websockets=false",
               "-c", "shell_environment_policy.set={" + ",".join(
                   name + "=" + json.dumps(env[name]) for name in
                   ("PYTHONPATH", "PYTEST_DISABLE_PLUGIN_AUTOLOAD", "PYTHONDONTWRITEBYTECODE", "PYTHONIOENCODING")) + "}",
               "exec", "--ephemeral", "--skip-git-repo-check", "--json", "--color", "never",
               "-C", str(workspace), "-o", str(logs / "final.txt"), "-"]
    write(logs / "started.json", {**identity, "started_at": utc(), "command": command})
    (logs / "prompt.txt").write_text(prompt, encoding="utf-8")
    start, timed_out = time.monotonic(), False
    with (logs / "events.jsonl").open("w", encoding="utf-8") as stdout, (logs / "stderr.txt").open("w", encoding="utf-8") as stderr:
        proc = subprocess.Popen(command, cwd=workspace, env=env, stdin=subprocess.PIPE,
                                stdout=stdout, stderr=stderr, text=True, encoding="utf-8",
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
    return {**identity, "started_at": read(logs / "started.json")["started_at"],
            "cli_exit_code": proc.returncode, "timeout": timed_out, "client_seconds": time.monotonic() - start}


def parse_client(done, logs, skill):
    events = [json.loads(line) for line in (logs / "events.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    turns = [event["usage"] for event in events if event.get("type") == "turn.completed" and event.get("usage")]
    items = [event["item"] for event in events if event.get("type") == "item.completed"]
    exact = bool(turns) and not done["timeout"] and done["cli_exit_code"] == 0 and all(
        type(turn.get(name)) is int and turn[name] >= 0 for turn in turns for name in ("input_tokens", "output_tokens"))
    cached = all(type(turn.get("cached_input_tokens")) is int and 0 <= turn["cached_input_tokens"] <= turn["input_tokens"] for turn in turns) if exact else False
    body = re.sub(r"\A---\n.*?\n---\n", "", skill.read_text(encoding="utf-8").replace("\r\n", "\n"), count=1, flags=re.S).strip()
    output = "\n".join(item.get("aggregated_output", "") for item in items).replace("\r\n", "\n")
    commands = [item for item in items if item.get("type") == "command_execution"]
    input_tokens = sum(turn["input_tokens"] for turn in turns) if exact else None
    output_tokens = sum(turn["output_tokens"] for turn in turns) if exact else None
    return {**done, "cli_completed": True, "usage": turns, "usage_complete": exact,
            "input_tokens": input_tokens, "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens if exact else None,
            "cached_input_tokens": sum(turn["cached_input_tokens"] for turn in turns) if cached else None,
            "command_items": len(commands), "command_failures": sum(item.get("exit_code") not in (0, None) for item in commands),
            "skill_body_read_observed": bool(body and body in output),
            "thread_ids": [event.get("thread_id") for event in events if event.get("type") == "thread.started"],
            "stream_errors": [event.get("message") for event in events if event.get("type") == "error"],
            "grading_attempts": []}


def journal(root, record):
    with LOCK:
        path = root / "records.jsonl"
        if path.exists():
            existing = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            same = [row for row in existing if row["key"] == record["key"]]
            if same:
                if len(same) != 1 or same[0] != record:
                    raise ValueError("Journal identity conflict: " + record["key"])
                return
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")


def run_one(case, root, phase, repeat, arm, skill, manifest, expected):
    key = f"{phase}--{case['id']}--r{repeat}--{arm}"
    logs = root / "runs" / phase / key
    identity = {"key": key, "phase": phase, "case_id": case["id"], "repeat": repeat, "arm": arm,
                "repo": case.get("repo", case.get("upstream", {}).get("repo", "synthetic/" + case.get("family", case["id"]))),
                "case_sha256": case_digest(case), "skill_sha256": manifest["skill_sha256"]}
    workspace = root / "work" / phase / case["id"] / f"r{repeat}" / arm
    record_path, checkpoint_path, done_path = logs / "record.json", logs / "cli-checkpoint.json", logs / "client-done.json"
    if record_path.exists():
        record = read(record_path)
        if any(record.get(name) != value for name, value in identity.items()):
            raise ValueError("Existing record identity changed")
        journal(root, record)
        return record
    if transport.settings() != manifest["settings"] or digest(skill) != manifest["skill_sha256"]:
        raise ValueError("Frozen skill or configured model settings changed")
    if checkpoint_path.exists():
        checkpoint = read(checkpoint_path)
    else:
        if done_path.exists():
            done = read(done_path)
        else:
            workspace = prepare_workspace(case, root, phase, repeat, arm, skill)
            logs.mkdir(parents=True)
            done = launch_cli(workspace, logs, prompt_for(arm), identity)
            # First persistent operation after CLI completion, before parsing.
            done = {**done, "workspace": str(workspace)}
            write(done_path, done)
        if any(done.get(name) != value for name, value in identity.items()):
            raise ValueError("Completed CLI identity changed")
        checkpoint = parse_client(done, logs, skill)
        write(checkpoint_path, checkpoint)
    if any(checkpoint.get(name) != value for name, value in identity.items()) or not checkpoint.get("cli_completed"):
        raise ValueError("CLI checkpoint identity changed")
    if Path(checkpoint["workspace"]).resolve() != workspace.resolve() or not workspace.is_dir():
        raise ValueError("CLI checkpoint workspace mismatch")
    start, started = time.monotonic(), utc()
    try:
        grade_dir = root / "grades" / phase / key
        if "upstream" in case:
            grade = upstream_grader.grade_case(case["upstream"], workspace, grade_dir, PYTHON, root / "testdeps")
        else:
            grade = synthetic_grade(case, workspace, grade_dir)
        skill_ok = not (workspace / SKILL_REL).exists() if arm == "baseline" else (digest(workspace / SKILL_REL) == manifest["skill_sha256"] and
                   (workspace / ".agents/skills/lean-coding/agents/openai.yaml").read_text(encoding="utf-8") == METADATA)
        accepted = (grade["exit_code"] == 0 and grade["failed"] == grade["errors"] == 0
                    and grade.get("skipped", 0) == expected.get("skipped", 0)
                    and grade["tests_collected"] == expected["tests_collected"]
                    and grade["tests_ran"] == expected["tests_ran"] > 0 and skill_ok)
        if "modes" in grade:
            accepted = accepted and all(grade["modes"][mode]["exit_code"] == 0
                and grade["modes"][mode]["tests_ran"] == expected["modes"][mode]["tests_ran"] > 0
                and grade["modes"][mode]["skipped"] == expected["modes"][mode]["skipped"]
                for mode in ("normal", "optimized"))
    except Exception as exc:
        checkpoint["grading_attempts"].append({"started_at": started, "seconds": time.monotonic() - start,
                                               "status": "failed", "error_type": type(exc).__name__, "error": str(exc)})
        write(checkpoint_path, checkpoint)
        raise
    checkpoint["grading_attempts"].append({"started_at": started, "seconds": grade["seconds"], "status": "completed"})
    write(checkpoint_path, checkpoint)
    grade_seconds = sum(attempt["seconds"] for attempt in checkpoint["grading_attempts"])
    record = {**checkpoint, "finished_at": utc(), "accepted": bool(accepted), "completed": True,
              "skill_files_unchanged": skill_ok, "grade": grade,
              "client_seconds": round(checkpoint["client_seconds"], 6), "grade_seconds": round(grade_seconds, 6),
              "wall_seconds": round(checkpoint["client_seconds"] + grade_seconds, 6),
              "diff_stat": git(workspace, "diff", "--no-ext-diff", "--stat", "HEAD")}
    write(record_path, record)
    journal(root, record)
    print(json.dumps({name: record[name] for name in ("key", "accepted", "usage_complete", "total_tokens", "wall_seconds")}), flush=True)
    return record


@contextlib.contextmanager
def controller_lock(root, phase):
    path = root / "active-suite.lock"
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump({"pid": os.getpid(), "phase": phase, "started_at": utc()}, stream)
    try:
        yield
    finally:
        path.unlink()


def run_suite(root: Path, cases: list[dict], phase: str, frozen_skill: Path,
              repeats=3, arms=("baseline", "with-skill"), workers=3):
    root, skill = Path(root).resolve(), Path(frozen_skill).resolve()
    component(phase)
    if not cases or len({case["id"] for case in cases}) != len(cases):
        raise ValueError("Cases must be nonempty with unique IDs")
    for case in cases:
        component(case["id"])
    if repeats < 1 or workers < 1 or workers > 3 or not arms or len(set(arms)) != len(arms) or set(arms) - {"baseline", "with-skill"}:
        raise ValueError("Invalid repeats, arms, or concurrency")
    root.mkdir(parents=True, exist_ok=True)
    settings = transport.settings()
    if settings != SETTINGS:
        raise ValueError("Keep configured gpt-6.1-sol/xhigh; do not substitute models")
    plan = schedule(cases, phase, repeats, tuple(arms))
    manifest = {"phase": phase, "settings": settings, "skill_sha256": digest(skill),
                "cases": {case["id"]: case_digest(case) for case in cases},
                "seed": SEED, "repeats": repeats, "arms": list(arms), "workers": workers, "schedule": plan,
                "grading_contract": "synthetic-unittest-normal-and-O-v1;upstream-frozen"}
    with controller_lock(root, phase):
        manifest_path = root / (phase + "-manifest.json")
        if manifest_path.exists() and read(manifest_path) != manifest:
            raise ValueError("Frozen suite manifest changed")
        write(manifest_path, manifest)
        expectations = {case["id"]: preflight_case(case, root)["expected"] for case in cases}
        by_id = {case["id"]: case for case in cases}
        def run_pair(pair):
            case = by_id[pair["case_id"]]
            return [run_one(case, root, phase, pair["repeat"], arm, skill, manifest, expectations[case["id"]]) for arm in pair["arms"]]
        results, failures = [], []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(run_pair, pair): pair for pair in plan}
            for future in as_completed(futures):
                try:
                    results.extend(future.result())
                except Exception as exc:
                    pair = futures[future]
                    failures.append({"case_id": pair["case_id"], "repeat": pair["repeat"],
                                     "error_type": type(exc).__name__, "error": str(exc)})
        if failures:
            write(root / (phase + "-failures.json"), failures)
            raise RuntimeError(f"{phase}: {len(failures)} pair(s) need checkpoint recovery; see phase failure log")
        write(root / (phase + "-records.json"), results)
        return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--cases-dir", required=True, type=Path)
    parser.add_argument("--phase", default="development")
    parser.add_argument("--skill", type=Path)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--arms", nargs="+", default=["baseline", "with-skill"], choices=["baseline", "with-skill"])
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args()
    cases = []
    for path in sorted(args.cases_dir.glob("*.json")):
        value = read(path)
        if isinstance(value, dict) and "id" in value and ("files" in value or "source_dir" in value):
            cases.append(value)
    if not cases:
        parser.error("--cases-dir contains no case definitions")
    if args.preflight:
        args.root.mkdir(parents=True, exist_ok=True)
        results = [preflight_case(case, args.root.resolve()) for case in cases]
        print(json.dumps({"cases": len(results), "real_model_calls": 0, "qualified": all(result.get("qualified", True) for result in results)}))
    else:
        if not args.skill:
            parser.error("--skill is required for model runs")
        results = run_suite(args.root, cases, args.phase, args.skill, args.repeats, tuple(args.arms), args.workers)
        print(json.dumps({"runs": len(results), "accepted": sum(row["accepted"] for row in results)}))


if __name__ == "__main__":
    main()
