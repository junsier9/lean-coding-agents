"""Frozen candidate development gate, sealed contracts, then both real batches."""
from pathlib import Path
import argparse
import hashlib
import json
import time
import runner
from assess import assess

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
BASE = PROJECT / ".local/production-runtime"
SKILL = HERE / "candidates/v3-python-v2/SKILL.md"
SKILL_SHA = "026AC9BA9667BD0F1D369F2536D337AEAED14A275276BE347CEE940A8350F3AE"
PROTOCOL_SHA = "BE8C9BEC416B386980DD5F398B4A3E74C0C30F954A9863DCFE9F2173C3E2163F"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def status(stage, **fields):
    path = PROJECT / ".local/production-state.json"
    value = read(path) if path.exists() else {}
    value.update(stage=stage, updated_at=runner.utc(), **fields)
    runner.write(path, value)
    print(json.dumps({"stage": stage, **fields}, ensure_ascii=False), flush=True)


def cases(directory, prefix=None):
    paths = sorted(directory.glob((prefix or "*") + ".json"))
    values = []
    for path in paths:
        value = read(path)
        if isinstance(value, dict) and "id" in value and ("files" in value or "source_dir" in value):
            values.append(value)
    return values


def journal(root):
    return [json.loads(line) for line in (root / "records.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]


def main():
    assert digest(SKILL) == SKILL_SHA
    assert digest(HERE / "protocol.json") == PROTOCOL_SHA
    dev_root = BASE / "dev-v3-oracle-v2"
    status("waiting-for-development", dev_planned_runs=16)
    marker = dev_root / "development-records.json"
    while not marker.exists():
        if (dev_root / "development-failures.json").exists():
            status("development-infrastructure-recovery-needed")
            return 2
        time.sleep(10)
    development = read(marker)
    assert len(development) == len({row["key"] for row in development}) == 16
    if not all(row["accepted"] and row["skill_body_read_observed"] for row in development):
        status("development-revision-needed", dev_completed=16,
               failed_keys=[row["key"] for row in development if not row["accepted"]],
               unread_keys=[row["key"] for row in development if not row["skill_body_read_observed"]])
        return 3
    adjudication = read(HERE / "results/development-oracle-adjudication.json")
    assert adjudication["assertion_asts_unchanged"] and adjudication["model_reruns"] == 0
    assert adjudication["task_and_visible_sources_and_reference_unchanged"]
    assert adjudication["corrected_oracle_accepted"] == 16
    lineage = read(HERE / "results/candidate-lineage-v2.json")
    assert lineage["instruction_body_unchanged"] and lineage["confirmation_skill_sha256"] == SKILL_SHA
    old_body = runner.re.sub(r"\A---\n.*?\n---\n", "", (HERE / "candidates/v3/SKILL.md").read_text(encoding="utf-8"), count=1, flags=runner.re.S)
    new_body = runner.re.sub(r"\A---\n.*?\n---\n", "", SKILL.read_text(encoding="utf-8"), count=1, flags=runner.re.S)
    assert old_body == new_body
    routing_path = HERE / "results/routing-v3-python-v2.json"
    status("native-routing-running", dev_completed=16, original_dev_accepted=15, adjudicated_dev_accepted=16)
    while not routing_path.exists():
        time.sleep(10)
    routing = read(routing_path)
    assert routing["skill_sha256"] == SKILL_SHA
    if not routing["routing_qualified"]:
        status("native-routing-revision-needed")
        return 5
    assert digest(SKILL) == SKILL_SHA
    sealed = HERE / "holdout-sealed-v2"
    assert digest(sealed / "manifest.json") == "7FEEF291F22A0ABFA65E8B098157687BB6F4CFFF8247C951AD11120220EAC65A"
    freeze = {"frozen_at": runner.utc(), "skill_sha256": SKILL_SHA,
              "protocol_sha256": PROTOCOL_SHA, "development_valid_runs": 16,
              "development_marker_sha256": digest(marker),
              "original_development_accepted": 15,
              "development_oracle_adjudication_sha256": digest(HERE / "results/development-oracle-adjudication.json"),
              "candidate_lineage_sha256": digest(HERE / "results/candidate-lineage-v2.json"),
              "routing_summary_sha256": digest(routing_path),
              "runner_sha256": digest(HERE / "runner.py"),
              "assessment_sha256": digest(HERE / "assess.py"),
              "holdout_case_sha256": {path.name: digest(path) for path in sorted(sealed.glob("h[0-9][0-9].json"))},
              "scope": "Codex+Python production-maintenance candidate; no efficiency result used for selection"}
    assert len(freeze["holdout_case_sha256"]) == 12
    freeze_path = HERE / "results/candidate-v3-python-v2-freeze.json"
    if freeze_path.exists():
        previous = read(freeze_path)
        for key in freeze.keys() - {"frozen_at"}:
            assert previous[key] == freeze[key], key
    else:
        runner.write(freeze_path, freeze)
    safety_root = BASE / "safety-v3"
    status("production-contracts-running", dev_completed=16, safety_planned_runs=72)
    safety = runner.run_suite(safety_root, cases(sealed, "h[0-9][0-9]"), "safety", SKILL, 3,
                              ("baseline", "with-skill"), 3)
    treated = [row for row in safety if row["arm"] == "with-skill"]
    summary = {"runs": len(safety), "treatment_runs": len(treated),
               "baseline_accepted": sum(row["accepted"] for row in safety if row["arm"] == "baseline"),
               "skill_accepted": sum(row["accepted"] for row in treated),
               "treatment_skill_body_reads": sum(row["skill_body_read_observed"] for row in treated),
               "failed_keys": [row["key"] for row in treated if not row["accepted"]],
               "all_treatment_contracts_passed": len(treated) == 36 and all(row["accepted"] for row in treated)}
    runner.write(HERE / "results/safety-v3-summary.json", summary)
    if not summary["all_treatment_contracts_passed"] or summary["treatment_skill_body_reads"] != 36:
        status("production-contract-revision-needed", safety_summary=summary)
        return 4
    real_root = BASE / "real-v3"
    allocation = read(real_root / "real-selection.json")
    assert allocation["protocol_sha256"] == PROTOCOL_SHA
    analyses = {}
    for phase in ("formal", "replication"):
        assert digest(SKILL) == SKILL_SHA
        batch = cases(real_root / (phase + "-cases"))
        assert len(batch) == 20 and {case["id"] for case in batch} == set(allocation[phase])
        status("real-" + phase + "-running", tasks=20, expected_runs=120)
        runner.run_suite(real_root, batch, phase, SKILL, 3, ("baseline", "with-skill"), 3)
        analyses[phase] = assess(journal(real_root), phase)
        runner.write(HERE / ("results/" + phase + "-v3-analysis.json"), analyses[phase])
    claims = {metric: all(analyses[phase]["metrics"][metric]["batch_criterion_met"]
                         for phase in ("formal", "replication")) for metric in ("token", "time")}
    quality = all(result["quality"]["gate_pass"] and result["quality"]["skill_accepted"] / result["quality"]["runs_per_arm"] >= .95
                  for result in analyses.values())
    conclusion = {"completed_at": runner.utc(), "candidate": "v3-python-v2", "skill_sha256": SKILL_SHA,
                  "development_passed_after_documented_oracle_repair": True, "production_contracts_passed": True,
                  "real_quality_qualified": quality, "stable_at_least10percent_benefit": claims,
                  "real_valid_runs": 240, "real_task_count": 40,
                  "production_release_qualified": False,
                  "remaining_qualification": ["Independent blinded patch review", "Installation and rollback package verification"],
                  "scope": "Observed tested configuration; no universal correctness or unattended deployment claim"}
    runner.write(HERE / "results/v3-python-v2-confirmation-conclusion.json", conclusion)
    status("confirmation-complete-final-review-needed", **conclusion)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
