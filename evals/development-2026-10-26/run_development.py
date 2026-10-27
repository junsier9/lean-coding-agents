"""Six Codex development attempts over revealed contracts, without a new confirmation."""
from pathlib import Path
import argparse
import json
import re
import shutil
import sys

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
OLD = HERE.parent / "production-2026-10-25"
sys.path.insert(0, str(OLD))
import runner
import resource_gate
from export_results import FIELDS, public_value
from audit_production_trace import scan


def freeze(iteration):
    root = PROJECT / f".local/production-runtime/dev-v5-iteration{iteration}"
    source = HERE / "candidates/v5-python"
    prior = runner.read(OLD / "results/v4/release-qualification.json")
    for name, expected in prior["source_report_sha256"].items():
        assert runner.digest(OLD / "results/v4" / name) == expected
    assert runner.transport.settings() == runner.SETTINGS
    assert not list((PROJECT / ".local/production-runtime").glob("*/active-suite.lock"))
    assert not (PROJECT / ".local/production-v4-controller.lock").exists()
    assert not resource_gate.STOP.exists()
    preflight = runner.read(HERE / "results/preflight.json")
    cases = [runner.read(HERE / "cases" / name) for name in ("d09.json", "d10.json")]
    assert preflight["initial_fails_reference_passes"] and preflight["all_negative_controls_rejected"]
    for case in cases:
        assert preflight["cases"][case["id"]]["case_sha256"] == runner.case_digest(case)
        assert runner.preflight_case(case, root)["qualified"]
    snapshot = root / "skill-input"
    snapshot.mkdir(parents=True, exist_ok=True)
    for name in ("SKILL.md", "agents/openai.yaml"):
        destination = snapshot / name
        if destination.exists():
            assert runner.digest(destination) == runner.digest(source / name)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, destination)
    report = {
        "frozen_at": runner.utc(), "iteration": iteration,
        "evidence_class": "revealed_development_regression_only",
        "planned_native_attempts": 6, "repeats_per_case": 3, "max_concurrent_clients": 3,
        "cases_sha256": {case["id"]: runner.case_digest(case) for case in cases},
        "fixture_files_sha256": {name: runner.digest(HERE / "cases" / name)
                                  for name in ("d09.json", "d10.json")},
        "skill_sha256": runner.digest(snapshot / "SKILL.md"),
        "ui_sha256": runner.digest(snapshot / "agents/openai.yaml"),
        "runner_sha256": runner.digest(Path(runner.__file__)),
        "driver_sha256": runner.digest(Path(__file__)),
        "preflight_report_sha256": runner.digest(HERE / "results/preflight.json"),
        "settings": runner.SETTINGS, "python_version": sys.version.split()[0],
        "python_executable_sha256": runner.digest(Path(sys.executable)),
        "cli_binary_sha256": runner.digest(runner.CODEX),
        "closed_v4_qualification_sha256": runner.digest(OLD / "results/v4/release-qualification.json"),
        "closed_v4_source_reports": prior["source_report_sha256"],
        "gate": "All6 contract grades and full skill-body observations pass, CLI exits0 without timeout",
        "not_a_fresh_holdout_or_efficiency_claim": True,
    }
    path = HERE / f"results/candidate-freeze-iteration{iteration}.json"
    if path.exists():
        previous = runner.read(path)
        assert all(previous[k] == value for k, value in report.items() if k != "frozen_at")
        report = previous
    else:
        runner.write(path, report)
    return root, snapshot / "SKILL.md", cases, report


def export_and_summarize(root, skill, records, frozen):
    iteration = frozen["iteration"]
    out = HERE / "results"
    exported, flags, threads, problems = [], [], [], []
    body = re.sub(r"\A---\n.*?\n---\n", "", skill.read_text(encoding="utf-8"), count=1, flags=re.S).strip()
    for record in records:
        key = record["key"]
        logs = root / "runs/development" / key
        observed = scan((logs / "events.jsonl").read_text(encoding="utf-8"), body)
        threads += observed["thread_ids"]
        flags += [{"key": key, **f} for f in observed["flags"]]
        if observed["body_visible"] != record["skill_body_read_observed"]:
            problems.append({"key": key, "issue": "body_visibility_mismatch"})
        workspace = Path(record["workspace"])
        if runner.digest(workspace / runner.SKILL_REL) != frozen["skill_sha256"]:
            problems.append({"key": key, "issue": "installed_skill_hash"})
        patch = runner.git(workspace, "diff", "--no-ext-diff", "--binary", "HEAD")
        patch_path = out / "patches" / f"iteration{iteration}" / (key + ".patch")
        patch_path.parent.mkdir(parents=True, exist_ok=True)
        patch_path.write_text(patch, encoding="utf-8")
        row = {name: record.get(name) for name in FIELDS}
        row.update(raw_artifact_sha256={name: runner.digest(logs / name) if (logs / name).is_file() else None
                                       for name in ("started.json", "prompt.txt", "events.jsonl", "stderr.txt",
                                                    "final.txt", "client-done.json", "cli-checkpoint.json", "record.json")},
                   patch=patch_path.relative_to(out).as_posix(), patch_sha256=runner.digest(patch_path))
        exported.append(public_value(row))
    unique = len(threads) == len(set(threads)) == len(records) and None not in threads
    if not unique:
        problems.append({"issue": "missing_or_reused_thread"})
    for name, expected in frozen["closed_v4_source_reports"].items():
        assert runner.digest(OLD / "results/v4" / name) == expected
    assert runner.digest(OLD / "results/v4/release-qualification.json") == frozen["closed_v4_qualification_sha256"]
    assert runner.digest(skill) == frozen["skill_sha256"]
    (out / f"runs-iteration{iteration}.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in exported), encoding="utf-8")
    runner.write(PROJECT / f".local/production-v5-trace-flags-iteration{iteration}.json", flags)
    summary = {
        "completed_at": runner.utc(), "iteration": iteration,
        "evidence_class": "known_development_regression_only",
        "skill_sha256": frozen["skill_sha256"], "native_attempts": len(records),
        "contract_accepted": sum(row["accepted"] for row in records),
        "body_reads_observed": sum(row["skill_body_read_observed"] for row in records),
        "usage_complete": sum(row["usage_complete"] for row in records),
        "cli_completed_without_timeout": sum(not row["timeout"] and row["cli_exit_code"] == 0 for row in records),
        "cases": {case_id: {"attempts": sum(row["case_id"] == case_id for row in records),
                            "accepted": sum(row["case_id"] == case_id and row["accepted"] for row in records)}
                  for case_id in ("d09", "d10")},
        "development_gate_pass": len(records) == 6 and all(
            row["accepted"] and row["skill_body_read_observed"] and not row["timeout"]
            and row["cli_exit_code"] == 0 for row in records),
        "trace_integrity_issues": problems, "potential_trace_flags": len(flags),
        "closed_v4_evidence_unchanged": True,
        "not_a_v4_comparison_new_holdout_efficiency_or_production_claim": True,
    }
    runner.write(out / f"development-iteration{iteration}.json", summary)
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iteration", type=int, default=1)
    parser.add_argument("--prepare-only", action="store_true")
    arguments = parser.parse_args()
    root, skill, cases, frozen = freeze(arguments.iteration)
    if arguments.prepare_only:
        print(json.dumps({"frozen": True, "native_model_calls": 0, "planned": 6}))
    else:
        resource_gate.install()
        records = runner.run_suite(root, cases, "development", skill, 3, ("with-skill",), 3)
        export_and_summarize(root, skill, records, frozen)
