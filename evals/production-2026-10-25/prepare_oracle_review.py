"""Prepare outcome-free task and pinned source locations for contract review."""
from pathlib import Path
import json
import runner

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
REAL = PROJECT / ".local/production-runtime/real-v3"


def prepare():
    tasks = []
    for phase in ("formal", "replication"):
        for path in sorted((REAL / (phase + "-cases")).glob("*.json")):
            case = runner.read(path)
            upstream = case["upstream"]
            tasks.append({"id": case["id"], "phase": phase, "task": case["task"],
                          "base_dir": upstream["base_dir"], "fix_dir": upstream["fix_dir"],
                          "production_files": upstream["production_files"],
                          "guard_paths": upstream["guard_paths"],
                          "regression_test_files": upstream["regression_test_files"],
                          "issue_or_pr_url": upstream["issue_or_pr_url"]})
    output = PROJECT / ".local/production-oracle/tasks.json"
    runner.write(output, {"tasks": tasks,
                         "scope": "Independent interpretation only; frozen confirmatory tasks and grading cannot be changed."})
    print(json.dumps({"tasks": len(tasks), "packet": str(output)}))


if __name__ == "__main__":
    prepare()
