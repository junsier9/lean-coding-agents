"""Native implicit selection checks; separate from the paired efficiency study."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import shutil
import subprocess
import runner

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
ROOT = PROJECT / ".local/production-runtime/routing-v3-python-v2"
SKILL = HERE / "candidates/v3-python-v2/SKILL.md"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def route_cases():
    result = []
    for name in ("d01", "d04", "d05"):
        case = read(HERE / "dev-cases-v2" / (name + ".json"))
        case["id"] = "implicit-" + name
        case["expected_load"] = True
        result.append(case)
    result += [
        {"id": "negative-readme", "family": "documentation", "expected_load": False,
         "task": "仅将 README.md 的 helo 拼写更正为 hello。", "files": {"README.md": "helo\n"}, "hidden_tests": {}},
        {"id": "negative-explanation", "family": "read-only", "expected_load": False,
         "task": "只用中文解释 app.py 的 add 行为，不修改或新增任何文件。", "files": {"app.py": "def add(a, b):\n    return a + b\n"}, "hidden_tests": {}},
        {"id": "negative-javascript", "family": "non-python-edit", "expected_load": False,
         "task": "这是 JavaScript 小项目。只把 main.js 打印的 helo 改为 hello，保持其余行为。", "files": {"main.js": "console.log('helo');\n"}, "hidden_tests": {}},
    ]
    for path in sorted((HERE / "routing-holdout-v2").glob("n[0-9][0-9].json")):
        result.append(read(path))
    assert len(result) == 9
    return result


def run(case):
    key = case["id"]
    logs = ROOT / "runs" / key
    record_path = logs / "record.json"
    if record_path.exists():
        return read(record_path)
    done_path = logs / "client-done.json"
    workspace = ROOT / "work/routing" / key / "r0/with-skill"
    if done_path.exists():
        done = read(done_path)
    else:
        workspace = runner.prepare_workspace(case, ROOT, "routing", 0, "with-skill", SKILL)
        logs.mkdir(parents=True)
        prompt = ("请按 TASK.md 的请求处理，遵守其中的编辑范围；在有必要时运行本地检查，最后用中文报告实际结果。"
                  "不要访问其他实验、评分器或参考实现；不要联网、安装依赖、委派、提交、发布或修改skill。")
        identity = {"key": key, "phase": "routing", "case_id": key, "arm": "implicit",
                    "skill_sha256": runner.digest(SKILL), "workspace": str(workspace)}
        done = runner.launch_cli(workspace, logs, prompt, identity)
        runner.write(done_path, done)
    record = runner.parse_client(done, logs, SKILL)
    runner.write(logs / "cli-checkpoint.json", record)
    changed = runner.git(workspace, "diff", "--name-only", "HEAD").splitlines()
    untracked = runner.git(workspace, "ls-files", "--others", "--exclude-standard").splitlines()
    record.update(expected_load=case["expected_load"], changed_tracked_paths=changed, untracked_paths=untracked,
                  separate_from_efficiency_study=True, skill_unchanged=runner.digest(workspace / runner.SKILL_REL) == runner.digest(SKILL))
    if case["expected_load"]:
        expected = runner.preflight_case(case, ROOT)["expected"]
        grade = runner.synthetic_grade(case, workspace, ROOT / "grades" / key)
        record["grade"] = grade
        operation = grade["exit_code"] == 0 and grade["failed"] == grade["errors"] == grade["skipped"] == 0 and grade["tests_ran"] == expected["tests_ran"] > 0
    elif key == "negative-readme":
        operation = changed == ["README.md"] and not untracked and (workspace / "README.md").read_text(encoding="utf-8").strip() == "hello"
    elif case["family"] == "read-only":
        final = logs / "final.txt"
        operation = not changed and not untracked and final.is_file() and bool(final.read_text(encoding="utf-8").strip())
    else:
        node = shutil.which("node")
        result = subprocess.run([node, "main.js"], cwd=workspace, capture_output=True, text=True, encoding="utf-8", timeout=10)
        operation = changed == ["main.js"] and not untracked and result.returncode == 0 and result.stdout.strip() == "hello"
    record["operation_pass"] = bool(operation)
    record["routing_check_pass"] = bool(operation and record["skill_unchanged"] and record["cli_exit_code"] == 0
                                          and record["skill_body_read_observed"] == case["expected_load"])
    runner.write(record_path, record)
    return record


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    result = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(run, case): case["id"] for case in route_cases()}
        for future in as_completed(futures):
            result[futures[future]] = future.result()
    report = {"completed_at": runner.utc(), "skill_sha256": runner.digest(SKILL), "model_runs": 9,
              "included_in_efficiency_statistics": False,
              "routing_qualified": all(record["routing_check_pass"] for record in result.values()),
              "cases": result, "limitations": ["Small functional routing sample; no reliability-rate estimate",
              "Negative trace absence is not proof of invisible host nonactivation", "Positive fixtures reuse development cases; not independent generalization evidence"]}
    runner.write(HERE / "results/routing-v3-python-v2.json", report)
    print(json.dumps({"routing_qualified": report["routing_qualified"],
                      "cases": {key: value["routing_check_pass"] for key, value in result.items()}}))


if __name__ == "__main__":
    main()
