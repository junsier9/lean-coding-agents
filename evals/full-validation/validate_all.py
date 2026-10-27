"""Continue pilot planning, fixed-size confirmation and independent replication."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import time

from analyze import analyze_records, sample_size, _json_safe
from bound_analysis import analyze_token_bound
from extend_corpus import extend
from run_experiment import HERE, configure, read, run_phase, utc, write


def records(root):
    path = root / "records.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def status(root, stage, **details):
    value = {"updated_at": utc(), "runtime_root": str(root), "stage": stage, **details}
    write(HERE.parent.parent / ".local" / "state.json", value)
    print(json.dumps(value, ensure_ascii=False), flush=True)


def main(root):
    write(HERE / "results" / "controller-launch.json", {
        "started_at": utc(), "pid": os.getpid(),
        "script_sha256": {name: hashlib.sha256((HERE / name).read_bytes()).hexdigest().upper()
                          for name in ("validate_all.py", "extend_corpus.py", "run_experiment.py", "preflight.py", "analyze.py", "bound_analysis.py")},
        "purpose": "Pilot planning and both confirmation batches; pilot solver process already running separately."
    })
    status(root, "pilot-running", expected_runs=60)
    while not (root / "pilot-records.json").exists():
        time.sleep(10)
    rows = records(root)
    pilot = analyze_records(rows, "pilot")
    plan = sample_size(rows)
    write(HERE / "results" / "pilot-analysis.json", _json_safe(pilot))
    write(HERE / "results" / "sample-plan.json", _json_safe(plan))
    if not plan["planning_available"]:
        status(root, "planning-unavailable", reason="Missing or nonestimable pilot measurements; no default sample size.")
        return 2
    target = plan["target_tasks_per_confirmatory_batch"]
    status(root, "preparing-confirmatory-corpus", tasks_per_batch=target, underpowered=plan["underpowered"])
    while True:
        partition = read(root / "partition.json")
        if len(partition["reserve"]) >= 2 * target:
            break
        additions = []
        for name in ("eligible-extra-a.json", "eligible-extra-a2.json", "eligible-extra-b.json"):
            path = root / name
            if path.exists():
                metadata = read(path)["metadata"]
                if metadata.get("complete") and metadata.get("guard_complete"):
                    additions.append(path)
        if additions:
            extension = extend(root, additions)
            status(root, "preparing-confirmatory-corpus", tasks_per_batch=target, **extension)
        if len(read(root / "partition.json")["reserve"]) >= 2 * target:
            break
        time.sleep(15)
    if not read(root / "partition.json")["formal"]:
        configure(root, HERE / "results" / "sample-plan.json")
    allocation = read(root / "partition.json")
    assert len(allocation["formal"]) == len(allocation["replication"]) == target
    assert not set(allocation["pilot"]) & set(allocation["formal"] + allocation["replication"])
    assert not set(allocation["formal"]) & set(allocation["replication"])
    analyses = {}
    token_bounds = {}
    token_proofs = {}
    for phase in ("formal", "replication"):
        status(root, phase + "-running", tasks=target, expected_runs=6 * target)
        run_phase(root, phase, 3)
        phase_rows = records(root)
        result = analyze_records(phase_rows, phase)
        assert result["task_count"] == target
        assert result["integrity"]["structure_ok"]
        assert set(result["bootstrap"]["task_ids_in_sampling_order"]) == set(allocation[phase])
        write(HERE / "results" / (phase + "-analysis.json"), _json_safe(result))
        bound = analyze_token_bound(phase_rows, phase)
        assert bound["task_count"] == target
        assert set(bound["bootstrap"]["task_ids_in_sampling_order"]) == set(allocation[phase])
        write(HERE / "results" / (phase + "-token-bound.json"), _json_safe(bound))
        analyses[phase] = result
        token_bounds[phase] = bound
        token_proofs[phase] = bool(result["metrics"]["token"]["batch_criterion_met"] or (
            not result["metrics"]["token"]["available"] and bound["batch_criterion_met"]))
    exact_claims = {metric: all(analyses[phase]["metrics"][metric]["batch_criterion_met"]
                               for phase in ("formal", "replication")) for metric in ("token", "time")}
    claims = {"token": all(token_proofs.values()), "time": exact_claims["time"]}
    conclusion = {"completed_at": utc(), "all_planned_phases_completed": True,
                  "tasks_per_confirmatory_batch": target, "underpowered_planning": plan["underpowered"],
                  "stable_benefit_demonstrated": claims,
                  "exact_usage_analysis_only_claims": exact_claims,
                  "token_inference": {phase: {
                      "method": "exact_usage" if analyses[phase]["metrics"]["token"]["available"] else
                                "conservative_baseline_cost_bound" if token_bounds[phase]["status"] == "bound_available" else "unavailable",
                      "batch_criterion_met": token_proofs[phase],
                      "actual_ratio_identified_from_exact_usage": analyses[phase]["metrics"]["token"]["available"],
                      "missing_counts": token_bounds[phase]["missing_counts"]
                  } for phase in ("formal", "replication")},
                  "conservative_bound_scope": "Pre-registered missing-usage exception only; preserves original nulls, uses baseline cost lower bound, never supplies an actual-ratio point estimate or two-sided CI.",
                  "scope": "Explicit frozen skill, configured Codex model/reasoning and curated Python maintenance corpus only.",
                  "protocol_sha256": hashlib.sha256((HERE / "preregistered-protocol.json").read_bytes()).hexdigest().upper(),
                  "skill_sha256": allocation["skill_sha256"]}
    write(HERE / "results" / "conclusion.json", conclusion)
    status(root, "evaluation-complete", **conclusion)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(main(args.root.resolve()))
