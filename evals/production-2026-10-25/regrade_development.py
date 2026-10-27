"""Versioned development-oracle repair; never reruns or edits model work."""
from pathlib import Path
import ast
import copy
import hashlib
import json
import subprocess
import runner

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
OLD = PROJECT / ".local/production-runtime/dev-v3"
NEW = PROJECT / ".local/production-runtime/dev-v3-oracle-v2"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def tests(source):
    return {node.name: ast.dump(node, include_attributes=False) for node in ast.walk(ast.parse(source))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")}


def main():
    rows = read(OLD / "development-records.json")
    assert len(rows) == 16 and len({row["key"] for row in rows}) == 16
    cases = {}
    changed = []
    for path in sorted((HERE / "dev-cases").glob("d[0-9][0-9].json")):
        original, corrected = read(path), read(HERE / "dev-cases-v2" / path.name)
        for key in set(original) | set(corrected):
            if key != "hidden_tests":
                assert original.get(key) == corrected.get(key), (path.name, key)
        for name, source in original["hidden_tests"].items():
            assert tests(source) == tests(corrected["hidden_tests"][name]), path.name
        if original != corrected:
            changed.append(original["id"])
        cases[corrected["id"]] = corrected
    assert changed == ["d06"]
    NEW.mkdir(parents=True, exist_ok=True)
    expected = {case_id: runner.preflight_case(case, NEW)["expected"] for case_id, case in cases.items()}
    output = []
    for original in rows:
        case = cases[original["case_id"]]
        workspace = Path(original["workspace"])
        before = subprocess.run(["git", "diff", "HEAD", "--binary"], cwd=workspace, capture_output=True, check=True).stdout
        grade = runner.synthetic_grade(case, workspace, NEW / "grades" / original["key"])
        count = expected[case["id"]]
        accepted = (grade["exit_code"] == 0 and grade["failed"] == grade["errors"] == grade["skipped"] == 0
                    and grade["tests_ran"] == count["tests_ran"] > 0
                    and all(grade["modes"][mode]["exit_code"] == 0 and grade["modes"][mode]["tests_ran"] == count["modes"][mode]["tests_ran"] > 0
                            for mode in ("normal", "optimized")))
        after = subprocess.run(["git", "diff", "HEAD", "--binary"], cwd=workspace, capture_output=True, check=True).stdout
        assert before == after
        row = copy.deepcopy(original)
        row.update(original_accepted=original["accepted"], original_grade=original["grade"],
                   grade=grade, accepted=accepted, oracle_version=2, model_rerun=False,
                   original_grade_seconds=original["grade_seconds"],
                   grade_seconds=original["grade_seconds"] + grade["seconds"],
                   wall_seconds=original["client_seconds"] + original["grade_seconds"] + grade["seconds"],
                   diagnostic_regrade_finished_at=runner.utc())
        output.append(row)
    runner.write(NEW / "development-records.json", output)
    summary = {"regraded_at": runner.utc(), "original_valid_model_runs": 16,
               "original_accepted": sum(row["accepted"] for row in rows),
               "corrected_oracle_accepted": sum(row["accepted"] for row in output),
               "changed_oracle_case_ids": changed, "assertion_asts_unchanged": True,
               "task_and_visible_sources_and_reference_unchanged": True,
               "old_model_worktrees_and_original_records_unchanged": True, "model_reruns": 0,
               "original_usage_and_client_seconds_preserved": True,
               "original_grading_cost_plus_regrade_cost_retained": True,
               "original_marker_sha256": hashlib.sha256((OLD / "development-records.json").read_bytes()).hexdigest().upper(),
               "corrected_case_manifest_sha256": hashlib.sha256((HERE / "dev-cases-v2/manifest.json").read_bytes()).hexdigest().upper(),
               "interpretation": "Development oracle fidelity correction only; original15/16 remains recorded. Not confirmatory data or an improvement of the skill body."}
    runner.write(HERE / "results/development-oracle-adjudication.json", summary)
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
