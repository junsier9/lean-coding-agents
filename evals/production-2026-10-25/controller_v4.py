"""Second preplanned confirmation: regressions, routing, then both fresh batches."""
from pathlib import Path
import argparse
import contextlib
import json
import os
import sys
import runner
from replay_analysis import replay
from assess import core, np
from benefit_gate import credible_benefit
import route_v4
import resource_gate

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
BASE = PROJECT / ".local/production-runtime"
PROTOCOL_SHA = "D8C56960C4CAC7C4A6BA3E73ED8A4CEEF0F448BB6CCABA36E2A8077CA164E746"
PIPELINE_LOCK = PROJECT / ".local/production-v4-controller.lock"


@contextlib.contextmanager
def pipeline_lock():
    with PIPELINE_LOCK.open("x", encoding="utf-8") as stream:
        json.dump({"pid": os.getpid(), "started_at": runner.utc()}, stream)
    try:
        yield
    finally:
        PIPELINE_LOCK.unlink()


def status(stage, **fields):
    runner.write(PROJECT / ".local/production-v4-state.json",
                 {"stage": stage, "updated_at": runner.utc(), **fields})
    print(json.dumps({"stage": stage, **fields}), flush=True)


def cases(directory):
    values = [runner.read(path) for path in sorted(directory.glob("*.json"))]
    return [case for case in values if "id" in case and ("files" in case or "source_dir" in case)]


def main(iteration, skill):
    resource_gate.install()
    prior = HERE / "results/v3-python-v2-confirmation-conclusion.json"
    assert prior.exists() and runner.read(prior)["real_valid_runs"] == 240, "Complete the first240 real runs before second confirmation"
    assert not list(BASE.glob("*/active-suite.lock")), "Another native suite is active"
    assert runner.digest(HERE / "protocol-v4.json") == PROTOCOL_SHA
    assert runner.digest(HERE / "results/release-requirements.json") == "76BA888F7E756FEF188BE5EAD488CC8E065B5D5B0F04962E5E58A2AA53365797"
    runtime = runner.read(HERE / "results/runtime-snapshot.json")
    assert runner.digest(runner.CODEX) == runtime["cli_binary_sha256"]
    assert sys.version.split()[0] == runtime["python_version"]
    assert runner.digest(Path(sys.executable)) == runtime["python_executable_sha256"]
    assert np.__version__ == runtime["numpy_version"]
    assert runner.digest(HERE / "runner.py") == "849C8631100DC83F6F11956F19C1404DD4222F2EF9B70BCF3DA13D206B728C46"
    assert runner.digest(HERE / "assess.py") == "123DAB93DF04CABE231FE1DB87E3EE856CFBA87FF42AC10A265B9D8A2B835B6A"
    development = runner.read(HERE / f"results/v4/development-iteration{iteration}.json")
    skill_sha = runner.digest(skill)
    assert development["development_gate_pass"] and development["skill_sha256"] == skill_sha
    assert development["native_runs"] == development["accepted"] == development["body_reads"] == 6
    sealed = HERE / "holdout-sealed-v2"
    assert runner.digest(sealed / "manifest.json") == "7FEEF291F22A0ABFA65E8B098157687BB6F4CFFF8247C951AD11120220EAC65A"
    origin_freeze = HERE / "results/candidate-v3-python-v2-freeze.json"
    critical_hashes = runner.read(origin_freeze)["holdout_case_sha256"]
    assert len(critical_hashes) == 12
    for name, expected in critical_hashes.items():
        assert runner.digest(sealed / name) == expected
    critical_cases = [runner.read(sealed / name) for name in sorted(critical_hashes)]
    selection = runner.read(HERE / "results/second-corpus-selection.json")
    real = BASE / "real-v4"
    allocation = runner.read(real / "real-selection.json")
    assert allocation["protocol_sha256"] == PROTOCOL_SHA
    assert allocation["selection_sha256"] == runner.digest(HERE / "results/second-corpus-selection.json")
    for phase in ("formal", "replication"):
        assert set(allocation[phase]) == set(selection[phase]) and len(allocation[phase]) == 20
        for case_id in allocation[phase]:
            assert runner.digest(real / (phase + "-cases") / (case_id + ".json")) == allocation["case_file_sha256"][case_id]
    with pipeline_lock():
        safety_root = BASE / f"safety-v4-iteration{iteration}"
        status("critical-regressions-running", skill_sha256=skill_sha, iteration=iteration)
        safety = runner.run_suite(safety_root, critical_cases, "safety", skill, 3, ("with-skill",), 3)
        safety_summary = {"completed_at": runner.utc(), "native_runs": len(safety),
                          "skill_sha256": skill_sha, "accepted": sum(row["accepted"] for row in safety),
                          "body_reads": sum(row["skill_body_read_observed"] for row in safety),
                          "critical_gate_pass": len(safety) == 36 and all(row["accepted"] and row["skill_body_read_observed"] for row in safety),
                          "evidence_class": "Known critical regression contracts, not a fresh independent holdout"}
        runner.write(HERE / f"results/v4/safety-iteration{iteration}.json", safety_summary)
        if not safety_summary["critical_gate_pass"]:
            status("critical-regression-revision-needed", **safety_summary)
            return 2
        status("native-routing-running", skill_sha256=skill_sha, iteration=iteration)
        routing = route_v4.run(skill, iteration)
        if not routing["routing_qualified"]:
            status("routing-revision-needed", skill_sha256=skill_sha, iteration=iteration)
            return 3
        assert runner.digest(skill) == skill_sha
        freeze = {"frozen_at": runner.utc(), "skill_sha256": skill_sha,
                  "ui_sha256": runner.digest(skill.parent / "agents/openai.yaml"),
                  "protocol_sha256": PROTOCOL_SHA, "confirmation_slot": 2,
                  "development_summary_sha256": runner.digest(HERE / f"results/v4/development-iteration{iteration}.json"),
                  "safety_summary_sha256": runner.digest(HERE / f"results/v4/safety-iteration{iteration}.json"),
                  "critical_case_origin_freeze_sha256": runner.digest(origin_freeze),
                  "routing_summary_sha256": runner.digest(HERE / f"results/v4/routing-iteration{iteration}.json"),
                  "real_selection_sha256": runner.digest(real / "real-selection.json"),
                  "blind_review_plan_sha256": runner.digest(HERE / "results/v4-blind-review-plan.json"),
                  "runner_sha256": runner.digest(HERE / "runner.py"),
                  "assessment_sha256": runner.digest(HERE / "assess.py"),
                  "assessment_replay_sha256": runner.digest(HERE / "replay_analysis.py"),
                  "statistics_dependency_sha256": runner.digest(Path(core.__file__)),
                  "numpy_version": np.__version__,
                  "benefit_gate_sha256": runner.digest(HERE / "benefit_gate.py"),
                  "controller_sha256": runner.digest(Path(__file__)),
                  "resource_gate_sha256": runner.digest(Path(resource_gate.__file__)),
                  "routing_code_sha256": runner.digest(HERE / "route_v4.py"),
                  "runtime_snapshot_sha256": runner.digest(HERE / "results/runtime-snapshot.json"),
                  "upstream_grader_sha256": runner.digest(Path(runner.upstream_grader.__file__)),
                  "transport_sha256": runner.digest(Path(runner.transport.__file__))}
        assert freeze["ui_sha256"] == "E786C393174A36E6CAD32DD46325844B3036FC65619117984EBA926A93274E1A"
        freeze_path = HERE / "results/v4/candidate-freeze.json"
        if freeze_path.exists():
            saved = runner.read(freeze_path)
            assert all(saved[key] == value for key, value in freeze.items() if key != "frozen_at")
        else:
            runner.write(freeze_path, freeze)
        analyses = {}
        for phase in ("formal", "replication"):
            assert runner.digest(skill) == skill_sha
            status("real-" + phase + "-running", skill_sha256=skill_sha, expected_runs=120)
            runner.run_suite(real, cases(real / (phase + "-cases")), phase, skill, 3, ("baseline", "with-skill"), 3)
            rows = [json.loads(line) for line in (real / "records.jsonl").read_text(encoding="utf-8").splitlines()]
            analyses[phase] = replay(rows, phase)
            runner.write(HERE / f"results/v4/{phase}-analysis.json", analyses[phase])
        quality = all(value["quality"]["gate_pass"] and value["quality"]["skill_accepted"] / value["quality"]["runs_per_arm"] >= .95
                      for value in analyses.values())
        positive = {metric: all(credible_benefit(value, metric) for value in analyses.values())
                    for metric in ("token", "time")}
        material = {metric: all(value["metrics"][metric]["batch_criterion_met"] for value in analyses.values())
                    for metric in ("token", "time")}
        conclusion = {"completed_at": runner.utc(), "candidate": "v4", "skill_sha256": skill_sha,
                      "real_valid_runs": 240, "real_tasks": 40, "real_quality_qualified": quality,
                      "same_endpoint_credible_positive": positive, "stable_at_least10percent_benefit": material,
                      "production_release_qualified": False,
                      "remaining": ["Prespecified independent blinded review", "Two-file package verification", "Data/raw evidence integrity and statistical reproduction"]}
        runner.write(HERE / "results/v4/confirmation-conclusion.json", conclusion)
        status("confirmation-complete-final-review-needed", **conclusion)
        return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iteration", type=int, default=1)
    parser.add_argument("--skill", type=Path, default=HERE / "candidates/v4-python/SKILL.md")
    args = parser.parse_args()
    raise SystemExit(main(args.iteration, args.skill))
