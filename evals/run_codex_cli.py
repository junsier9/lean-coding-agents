"""Run paired Codex CLI trials without editing user configuration."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import shutil
import subprocess
import time


def run_one(root, logs, case_id, arm, skill):
    directory = root / arm / case_id
    logs.mkdir(parents=True, exist_ok=True)
    prefix = logs / (case_id + "-" + arm)
    common = (
        "请完成此独立 Python 项目的开发任务。读取当前目录的 TASK.md、README.md、AGENTS.md（如存在）和相关源文件，"
        "直接完成请求并运行有意义的本地检查。编辑范围限于当前项目目录；"
        "不要访问其他组、父项目研究或评分器，不要联网、安装依赖或再次委派。"
        "最后用中文报告改动、实际检查及未完成项，不需要另写报告文件。"
    )
    condition = "本次不加载额外 skill。" if arm == "baseline" else (
        "使用 $lean-coding，先读取 " + str(skill) + "。"
    )
    prompt = common + "\n" + condition
    prefix.with_suffix(".prompt.txt").write_text(prompt, encoding="utf-8")
    command = [
        shutil.which("codex"), "-a", "never", "-s", "workspace-write",
        "exec", "--ephemeral", "--skip-git-repo-check", "--json", "--color", "never",
        "-C", str(directory), "-o", str(prefix.with_suffix(".final.txt")), "-"
    ]
    start = time.monotonic()
    status = "completed"
    with prefix.with_suffix(".jsonl").open("w", encoding="utf-8") as stdout:
        with prefix.with_suffix(".stderr.txt").open("w", encoding="utf-8") as stderr:
            try:
                completed = subprocess.run(
                    command, input=prompt, text=True, encoding="utf-8",
                    stdout=stdout, stderr=stderr, timeout=600,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                )
                exit_code = completed.returncode
            except subprocess.TimeoutExpired:
                exit_code = None
                status = "timeout"
    events = []
    for line in prefix.with_suffix(".jsonl").read_text(encoding="utf-8").splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    completed_turns = [event for event in events if event.get("type") == "turn.completed"]
    items = [event["item"] for event in events if event.get("type") == "item.completed"]
    record = {
        "case": case_id, "arm": arm, "exit_code": exit_code, "status": status,
        "wall_seconds": round(time.monotonic() - start, 3),
        "usage": [event.get("usage") for event in completed_turns],
        "completed_command_items": sum(item.get("type") == "command_execution" for item in items),
        "completed_file_change_items": sum(item.get("type") == "file_change" for item in items),
        "error_items": [item.get("message") for item in items if item.get("type") == "error"],
        "terminal_errors": [event for event in events if event.get("type") in {"error", "turn.failed"}],
        "transport_override": None,
        "model_override": None,
    }
    prefix.with_suffix(".run.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(record, ensure_ascii=False), flush=True)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--logs", type=Path, required=True)
    parser.add_argument("--skill", type=Path, required=True)
    parser.add_argument("--cases", nargs="+", required=True)
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    jobs = []
    for i, case_id in enumerate(args.cases):
        arms = ["baseline", "with-skill"] if i % 2 == 0 else ["with-skill", "baseline"]
        jobs.extend((case_id, arm) for arm in arms)
    records = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(run_one, args.root, args.logs, case_id, arm, args.skill)
                   for case_id, arm in jobs]
        for future in as_completed(futures):
            records.append(future.result())
    (args.logs / "runs.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if all(record["exit_code"] == 0 and record["usage"] for record in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())

