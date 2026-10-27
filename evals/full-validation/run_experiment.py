"""Frozen paired native-Codex experiments over independently verified upstream bugs."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import threading
import time

import preflight

HERE = Path(__file__).resolve().parent
PYTHON = Path(r"C:\Users\ASUS\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe")
CODEX = Path(shutil.which("codex"))
GIT = Path(shutil.which("git"))
EXPECTED_SKILL = "8C757603BBADE8FF688FD0A08CC5F8A095893A2593363B51EC983F4F70FA74BF"
LOCK = threading.Lock()
IGNORE = shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", ".tox", ".venv")


def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest().upper()


def settings():
    config = Path.home() / ".codex" / "config.toml"
    import tomllib
    data = tomllib.loads(config.read_text(encoding="utf-8"))
    return {key: data.get(key) for key in ("model", "model_reasoning_effort")}


def partition(root):
    eligible = read(root / "eligible.json")
    assert eligible["metadata"]["complete"] and eligible["metadata"]["guard_complete"]
    assert digest(HERE / "skill-frozen.md") == EXPECTED_SKILL
    cases = eligible["cases"]
    assert len(cases) >= 50, "Need at least 50 qualified cases before the pilot."
    grouped = {}
    for case in cases:
        grouped.setdefault(case["repo"], []).append(case["id"])
    rng = random.Random(20261024)
    for group in grouped.values():
        group.sort()
        rng.shuffle(group)
    ordered = []
    while any(grouped.values()):
        repos = [repo for repo in sorted(grouped) if grouped[repo]]
        rng.shuffle(repos)
        for repo in repos:
            ordered.append(grouped[repo].pop())
    result = {
        "created_at": utc(), "seed": 20261024, "eligible_sha256": digest(root / "eligible.json"),
        "protocol_sha256": digest(HERE / "preregistered-protocol.json"),
        "skill_sha256": EXPECTED_SKILL, "settings": settings(),
        "pilot": ordered[:10], "reserve": ordered[10:],
        "formal": [], "replication": [], "case_count": len(cases)
    }
    assert result["settings"] == {"model": "gpt-6.1-sol", "model_reasoning_effort": "xhigh"}
    write(root / "partition.json", result)
    write(HERE / "results" / "partition-initial.json", {**result, "eligible_ids": ordered})
    print(json.dumps({"partition_created": True, "pilot": result["pilot"], "reserve": len(result["reserve"])}))


def configure(root, sample_plan):
    current = read(root / "partition.json")
    plan = read(sample_plan)
    assert plan["planning_available"], "Pilot does not support sample-size planning."
    assert plan["protocol_pilot_task_count_satisfied"], "Pilot task count differs from the frozen protocol."
    n = int(plan["target_tasks_per_confirmatory_batch"])
    assert not current["formal"] and not current["replication"], "Confirmatory allocation already frozen."
    assert len(current["reserve"]) >= 2 * n, "Need an independent corpus extension before allocating."
    current.update(formal=current["reserve"][:n], replication=current["reserve"][n:2*n],
                   reserve=current["reserve"][2*n:], sample_plan=plan, allocation_at=utc())
    write(root / "partition.json", current)
    write(HERE / "results" / "partition-confirmatory.json", current)
    print(json.dumps({"formal": n, "replication": n, "reserve": len(current["reserve"])}))


def environment(workspace, root):
    env = os.environ.copy()
    env.update(PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1",
               PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
               PYTHONPATH=os.pathsep.join([str(workspace / "src"), str(workspace), str(workspace / ".benchmark-deps")]))
    return env


def prepare_workspace(case, root, phase, repeat, arm):
    path = root / "work" / phase / case["id"] / ("r" + str(repeat)) / arm
    if path.exists():
        raise FileExistsError("Refusing to reuse an unfinished experimental workspace: " + str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(case["base_dir"], path, ignore=IGNORE)
    # The Windows sandbox cannot read the sibling temporary dependency target.
    # Grading continues to use the separate frozen dependency target.
    shutil.copytree(root / "testdeps", path / ".benchmark-deps", ignore=IGNORE)
    body = "# Task\n\n" + case["task"] + "\n\n"
    body += "# Test environment\n\n"
    body += "Use this available interpreter for local tests:\n\n" + str(PYTHON) + "\n\n"
    body += "pytest and required test dependencies are preinstalled; do not install packages.\n"
    body += "Use -m pytest -q -o addopts= -p no:cacheprovider -p pytest_timeout --timeout=60.\n"
    body += "Relevant existing test files: " + ", ".join(case["visible_test_paths"]) + "\n"
    (path / "BENCHMARK_TASK.md").write_text(body, encoding="utf-8")
    for command in [
        [str(GIT), "init", "-q"],
        [str(GIT), "add", "-A", "--", ".", ":(exclude).benchmark-deps"],
        [str(GIT), "-c", "user.name=Validation", "-c", "user.email=validation@example.invalid",
         "commit", "-qm", "Frozen input snapshot"],
    ]:
        subprocess.run(command, cwd=path, check=True, capture_output=True)
    (path / ".git" / "info" / "exclude").write_text(".benchmark-deps/\n", encoding="utf-8")
    return path


def parse_events(path):
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    turns = [event["usage"] for event in events if event.get("type") == "turn.completed" and event.get("usage")]
    items = [event["item"] for event in events if event.get("type") == "item.completed"]
    return turns, items, events


def run_one(case, root, phase, repeat, arm, frozen):
    key = f"{phase}--{case['id']}--r{repeat}--{arm}"
    logs = root / "runs" / phase / key
    record_path = logs / "record.json"
    checkpoint_path = logs / "cli-checkpoint.json"
    if record_path.exists():
        return read(record_path)
    assert settings() == frozen["settings"], "User model settings changed; pause rather than mix configurations."
    assert digest(HERE / "skill-frozen.md") == EXPECTED_SKILL
    if checkpoint_path.exists():
        checkpoint = read(checkpoint_path)
        identity = {"key": key, "phase": phase, "case_id": case["id"],
                    "repo": case["repo"], "repeat": repeat, "arm": arm}
        if checkpoint.get("cli_completed") is not True or any(
            checkpoint.get(name) != value for name, value in identity.items()
        ):
            raise ValueError("CLI checkpoint identity does not match this run: " + str(checkpoint_path))
        workspace = root / "work" / phase / case["id"] / ("r" + str(repeat)) / arm
        if Path(checkpoint["workspace"]).resolve() != workspace.resolve():
            raise ValueError("CLI checkpoint workspace does not match this run.")
        if not workspace.is_dir():
            raise FileNotFoundError("Checkpoint workspace is missing: " + str(workspace))
    else:
        workspace = prepare_workspace(case, root, phase, repeat, arm)
        logs.mkdir(parents=True)
        prompt = (
            "请完成当前项目 BENCHMARK_TASK.md 中的维护请求。阅读相关项目指令与源码，"
            "直接修改项目并运行有意义的本地检查，最后用中文报告实际改动、检查和未完成项。"
            "使用任务文件给出的现成测试解释器和依赖。你的编辑范围仅限当前项目；"
            "不要访问同级实验目录、控制器、评分器或外部参考补丁；不要联网、安装依赖、"
            "委派、提交或发布。可以新增有意义的测试，但不得削弱或禁用已有验收。"
        )
        if arm == "baseline":
            prompt += "\n本次不加载额外 skill。"
        else:
            prompt += "\n使用 $lean-coding，先读取 " + str(HERE / "skill-frozen.md") + "。"
        (logs / "prompt.txt").write_text(prompt, encoding="utf-8")
        command = [
            str(CODEX), "--no-daemon", "-a", "never", "-s", "workspace-write",
            "-c", 'model_provider="lean_eval_http"',
            "-c", 'model_providers.lean_eval_http.name="OpenAI evaluation HTTP"',
            "-c", "model_providers.lean_eval_http.requires_openai_auth=true",
            "-c", "model_providers.lean_eval_http.supports_websockets=false",
            "-c", "shell_environment_policy.set={" + ",".join(key + "=" + json.dumps(environment(workspace, root)[key])
                  for key in ["PYTHONPATH", "PYTEST_DISABLE_PLUGIN_AUTOLOAD", "PYTHONDONTWRITEBYTECODE", "PYTHONIOENCODING"]) + "}",
            "exec", "--ephemeral", "--skip-git-repo-check", "--json", "--color", "never",
            "-C", str(workspace), "-o", str(logs / "final.txt"), "-"
        ]
        started_at = utc()
        start = time.monotonic()
        timed_out = False
        with (logs / "events.jsonl").open("w", encoding="utf-8") as stdout:
            with (logs / "stderr.txt").open("w", encoding="utf-8") as stderr:
                proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr,
                                        text=True, encoding="utf-8", env=environment(workspace, root),
                                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                try:
                    proc.communicate(prompt, timeout=600)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    # Only the owned --no-daemon CLI process tree is targeted.
                    if proc.poll() is None:
                        if os.name == "nt":
                            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
                        else:
                            proc.kill()
                    proc.wait(timeout=20)
        client_seconds = time.monotonic() - start
        turns, items, events = parse_events(logs / "events.jsonl")
        usage_complete = bool(turns) and not timed_out
        total_tokens = sum(usage["input_tokens"] + usage["output_tokens"] for usage in turns) if usage_complete else None
        checkpoint = {
            "checkpoint_version": 1, "cli_completed": True,
            "key": key, "phase": phase, "case_id": case["id"], "repo": case["repo"],
            "repeat": repeat, "arm": arm, "started_at": started_at,
            "cli_exit_code": proc.returncode, "timeout": timed_out,
            "client_seconds": client_seconds, "workspace": str(workspace),
            "usage_complete": usage_complete, "usage": turns, "total_tokens": total_tokens,
            "input_tokens": sum(usage["input_tokens"] for usage in turns) if usage_complete else None,
            "cached_input_tokens": sum(usage.get("cached_input_tokens", 0) for usage in turns) if usage_complete else None,
            "output_tokens": sum(usage["output_tokens"] for usage in turns) if usage_complete else None,
            "command_items": sum(item.get("type") == "command_execution" for item in items),
            "command_failures": sum(item.get("type") == "command_execution" and item.get("exit_code") not in (0, None) for item in items),
            "stream_errors": [event.get("message") for event in events if event.get("type") == "error"],
            "grading_attempts": [],
        }
        # Persist paid CLI work before any copy/test work in the independent grader.
        write(checkpoint_path, checkpoint)

    grade_started_at = utc()
    grade_start = time.monotonic()
    try:
        grade = preflight.grade_case(case, workspace, root / "grades" / phase / key, PYTHON, root / "testdeps")
        expected = case["preflight"]["guard_fix"]
        accepted = (
            grade["exit_code"] == 0 and grade["errors"] == 0 and grade["failed"] == 0
            and grade["tests_collected"] == expected["tests_collected"]
            and grade["tests_ran"] >= expected["tests_ran"] > 0
        )
    except Exception as exc:
        checkpoint["grading_attempts"].append({
            "started_at": grade_started_at, "finished_at": utc(),
            "seconds": time.monotonic() - grade_start, "status": "failed",
            "error_type": type(exc).__name__, "error": str(exc),
        })
        write(checkpoint_path, checkpoint)
        raise
    checkpoint["grading_attempts"].append({
        "started_at": grade_started_at, "finished_at": utc(),
        "seconds": grade["seconds"], "status": "completed",
    })
    write(checkpoint_path, checkpoint)
    # Successful grading keeps the existing grader-reported timing basis;
    # failed grading calls contribute their full elapsed time, including copy.
    grade_seconds = sum(attempt["seconds"] for attempt in checkpoint["grading_attempts"])
    client_seconds = checkpoint["client_seconds"]
    diff = subprocess.run([str(GIT), "diff", "--no-ext-diff", "--stat"], cwd=workspace,
                          text=True, encoding="utf-8", capture_output=True).stdout
    record = {
        **checkpoint, "finished_at": utc(),
        "client_seconds": round(client_seconds, 3),
        "grade_seconds": round(grade_seconds, 3),
        "wall_seconds": round(client_seconds + grade_seconds, 3),
        "accepted": accepted, "completed": True, "grade": grade, "diff_stat": diff,
    }
    write(record_path, record)
    with LOCK:
        with (root / "records.jsonl").open("a", encoding="utf-8") as output:
            output.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(json.dumps({k: record[k] for k in ["key", "accepted", "usage_complete", "total_tokens", "wall_seconds"]}), flush=True)
    return record


def run_phase(root, phase, workers):
    frozen = read(root / "partition.json")
    ids = frozen[phase]
    assert ids
    eligible = {case["id"]: case for case in read(root / "eligible.json")["cases"]}
    pairs = [(case_id, repeat) for case_id in ids for repeat in range(3)]
    rng = random.Random(20261024 + {"pilot": 0, "formal": 1, "replication": 2}[phase])
    rng.shuffle(pairs)
    orders = {}
    for index, pair in enumerate(pairs):
        orders[pair] = ["baseline", "with-skill"] if index % 2 else ["with-skill", "baseline"]
    def run_pair(pair):
        case_id, repeat = pair
        return [run_one(eligible[case_id], root, phase, repeat, arm, frozen) for arm in orders[pair]]
    results = []
    failures = []
    first_error = None
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run_pair, pair): pair for pair in pairs}
        for future in as_completed(futures):
            try:
                results.extend(future.result())
            except Exception as exc:
                if first_error is None:
                    first_error = exc
                case_id, repeat = futures[future]
                failure = {"case_id": case_id, "repeat": repeat,
                           "error_type": type(exc).__name__, "error": str(exc)}
                failures.append(failure)
                print(json.dumps({"phase": phase, "pair_failed": failure}), flush=True)
    if failures:
        failure_path = root / (phase + "-failures.json")
        write(failure_path, {"phase": phase, "finished_at": utc(),
                             "status": "incomplete", "failed_pairs": failures})
        raise RuntimeError(
            f"{phase} incomplete: {len(failures)} pair(s) failed; see {failure_path}"
        ) from first_error
    write(root / (phase + "-records.json"), results)
    print(json.dumps({"phase":phase,"runs":len(results),"accepted":sum(row["accepted"] for row in results),
                      "usage_complete":sum(row["usage_complete"] for row in results)}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("partition")
    alloc = sub.add_parser("allocate")
    alloc.add_argument("--sample-plan", type=Path, required=True)
    run = sub.add_parser("run")
    run.add_argument("--phase", choices=["pilot", "formal", "replication"], required=True)
    run.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    root = args.root.resolve()
    if args.command == "partition":
        partition(root)
    elif args.command == "allocate":
        configure(root, args.sample_plan)
    else:
        run_phase(root, args.phase, args.workers)


if __name__ == "__main__":
    main()

