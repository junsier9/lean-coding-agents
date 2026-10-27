"""Run six development attempts only after the entire first confirmation."""
from pathlib import Path
import argparse
import json
import shutil
import runner
import resource_gate

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
BASE = PROJECT / ".local/production-runtime"


def run(iteration, skill):
    resource_gate.install()
    conclusion = HERE / "results/v3-python-v2-confirmation-conclusion.json"
    assert conclusion.exists() and runner.read(conclusion)["real_valid_runs"] == 240, "Complete the first240 real runs before v4 model work"
    assert not list(BASE.glob("*/active-suite.lock")), "Another native suite is active"
    assert not (PROJECT / ".local/production-v4-controller.lock").exists(), "The v4 pipeline is active"
    assert iteration > 0
    inputs = BASE / "development-v4-inputs"
    provenance = runner.read(inputs / "manifest.json")
    for name, expected in provenance["case_files_sha256"].items():
        assert runner.digest(inputs / "cases" / name) == expected
    assert runner.digest(HERE / "protocol-v4.json") == "D8C56960C4CAC7C4A6BA3E73ED8A4CEEF0F448BB6CCABA36E2A8077CA164E746"
    root = BASE / f"dev-v4-iteration{iteration}"
    snapshot = root / "skill-input/SKILL.md"
    if snapshot.exists():
        assert runner.digest(snapshot) == runner.digest(skill), "Use a new iteration directory for a revised skill"
    else:
        snapshot.parent.mkdir(parents=True)
        shutil.copyfile(skill, snapshot)
        (snapshot.parent / "agents").mkdir()
        shutil.copyfile(skill.parent / "agents/openai.yaml", snapshot.parent / "agents/openai.yaml")
    if not (root / "testdeps").exists():
        shutil.copytree(BASE / "real-v3/testdeps", root / "testdeps",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    cases = [runner.read(path) for path in sorted((inputs / "cases").glob("*.json"))]
    assert len(cases) == 2
    records = runner.run_suite(root, cases, "development", snapshot, 3, ("with-skill",), 3)
    summary = {"completed_at": runner.utc(), "iteration": iteration,
               "evidence_class": "development_regression_only", "skill_sha256": runner.digest(snapshot),
               "native_runs": len(records), "accepted": sum(row["accepted"] for row in records),
               "body_reads": sum(row["skill_body_read_observed"] for row in records),
               "failed_keys": [row["key"] for row in records if not row["accepted"]],
               "development_gate_pass": len(records) == 6 and all(row["accepted"] and row["skill_body_read_observed"] for row in records),
               "not_an_efficiency_or_fresh_holdout_claim": True}
    runner.write(HERE / f"results/v4/development-iteration{iteration}.json", summary)
    print(json.dumps(summary))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iteration", type=int, default=1)
    parser.add_argument("--skill", type=Path, default=HERE / "candidates/v4-python-draft/SKILL.md")
    args = parser.parse_args()
    run(args.iteration, args.skill)
