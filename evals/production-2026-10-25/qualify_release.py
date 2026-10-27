"""Combine the pre-frozen release requirements after the complete v4 study."""
from pathlib import Path
import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
import zipfile
import runner
import verify_evidence as evidence_checker
from benefit_gate import credible_benefit
from replay_analysis import replay
from study_config import study

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
FILES = ("SKILL.md", "agents/openai.yaml")


def quality_qualified(analyses):
    return len(analyses) == 2 and all(
        a["task_count"] == 20 and a["quality"]["runs_per_arm"] == 60
        and a["quality"]["gate_pass"] is True
        and a["quality"]["skill_accepted"] >= 57 for a in analyses.values())


def qualify(iteration, candidate):
    config = study("v4")
    out = config.out
    names = ["formal-analysis.json", "replication-analysis.json", "confirmation-conclusion.json",
             "candidate-freeze.json", f"development-iteration{iteration}.json",
             f"safety-iteration{iteration}.json", f"routing-iteration{iteration}.json",
             "blind-review-summary.json", "trace-audit.json", "trace-adjudication.json",
             "untracked-output-adjudication.json", "package-candidate.json",
             "evidence-integrity.json", "evidence-retention.json", "pre-model-qualification-control.json",
             "qualification-code-amendment.json", "qualification-fix-review.json"]
    missing = [name for name in names if not (out / name).is_file()]
    if missing:
        raise RuntimeError("Complete required evidence before qualification: " + ", ".join(missing))
    checked = evidence_checker.verify(require_archive=True, version="v4")
    reports = {name: runner.read(out / name) for name in names}
    integrity = reports["evidence-integrity.json"]
    if checked != integrity or not integrity["qualification_evidence_integrity_pass"]:
        raise ValueError("Current native evidence, archive and statistical replay must verify")
    if not integrity["all_planned_runs_present"]:
        raise RuntimeError("Finish every planned native attempt before qualification")
    candidate = candidate.resolve()
    hashes = {name: runner.digest(candidate / name) for name in FILES}
    freeze = reports["candidate-freeze.json"]
    if freeze["skill_sha256"] != hashes["SKILL.md"] or freeze["ui_sha256"] != hashes["agents/openai.yaml"]:
        raise ValueError("Package candidate differs from the tested frozen bundle")
    control = reports["pre-model-qualification-control.json"]
    amendment = reports["qualification-code-amendment.json"]
    fix_review = reports["qualification-fix-review.json"]
    current_code_sha = runner.digest(Path(__file__))
    policy_sha = runner.digest(HERE / "results/release-requirements.json")
    if not (policy_sha == "76BA888F7E756FEF188BE5EAD488CC8E065B5D5B0F04962E5E58A2AA53365797"
            and amendment["original_code_sha256"] == control["qualification_code_sha256"]
            and amendment["original_control_sha256"] == runner.digest(out / "pre-model-qualification-control.json")
            and amendment["fixed_code_sha256"] == current_code_sha == fix_review["tested_code_sha256"]
            and amendment["predeclared_requirements_sha256"] == policy_sha
            and amendment["thresholds_changed"] is False
            and amendment["model_inputs_grader_or_statistics_changed"] is False
            and amendment["independent_fix_review_sha256"] == runner.digest(out / "qualification-fix-review.json")
            and fix_review["unresolved_reproduced_implementation_defects"] == 0
            and fix_review["original_invalid_combinations_now_rejected"] is True
            and fix_review["valid_complete_fixture_accepted"] is True):
        raise ValueError("Document and independently retest the implementation-only repair")
    real_rows = [json.loads(line) for line in (out / "runs-real-v4.jsonl").read_text(encoding="utf-8").splitlines()]
    decision_fixed_at = datetime.fromisoformat(reports["pre-model-qualification-control.json"]["recorded_at"])
    if len(real_rows) != 240 or decision_fixed_at >= min(datetime.fromisoformat(row["started_at"]) for row in real_rows):
        raise ValueError("Fix the decision code before the first fresh native attempt")
    analyses = {phase: reports[phase + "-analysis.json"] for phase in ("formal", "replication")}
    if any(analyses[phase] != replay(real_rows, phase) or analyses[phase]["phase"] != phase for phase in analyses):
        raise ValueError("Current rows and phase analyses disagree")
    if not all(row["skill_sha256"] == hashes["SKILL.md"] for row in real_rows if row["arm"] == "with-skill"):
        raise ValueError("Real treatment rows belong to a different candidate")
    positive = {metric: all(credible_benefit(a, metric) for a in analyses.values())
                for metric in ("token", "time")}
    material = {metric: all(credible_benefit(a, metric) and a["metrics"][metric]["batch_criterion_met"] is True
                           and a["metrics"][metric]["upper_one_sided_98_75"] < .90 for a in analyses.values())
                for metric in ("token", "time")}
    development = reports[f"development-iteration{iteration}.json"]
    safety = reports[f"safety-iteration{iteration}.json"]
    routing = reports[f"routing-iteration{iteration}.json"]
    blind = reports["blind-review-summary.json"]
    trace = reports["trace-audit.json"]
    flags = reports["trace-adjudication.json"]
    raw_flags_path = PROJECT / ".local/production-trace-flags-v4.json"
    raw_flags = runner.read(raw_flags_path)
    flag_id = lambda flag: hashlib.sha256(json.dumps(flag, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest().upper()
    flag_reviews_match = (flags["trace_flags_sha256"] == runner.digest(raw_flags_path)
        and Counter(entry["flag_id"] for entry in flags["entries"]) == Counter(flag_id(flag) for flag in raw_flags)
        and all(entry["decision"] == "benign_keyword_match" for entry in flags["entries"]))
    outputs = reports["untracked-output-adjudication.json"]
    package = reports["package-candidate.json"]
    package_ok = (package["files_sha256"] == hashes and package["package_entries_verified"] == 2
                  and package["install_and_rollback_byte_checks_passed"] is True
                  and package["global_skill_directory_modified"] is False)
    archive = PROJECT / "dist" / package["archive"]
    package_ok = package_ok and runner.digest(archive) == package["archive_sha256"]
    with zipfile.ZipFile(archive) as z:
        package_ok = package_ok and z.testzip() is None and len(z.infolist()) == 2 and set(z.namelist()) == {"lean-coding/" + name for name in FILES}
        package_ok = package_ok and all(hashlib.sha256(z.read("lean-coding/" + name)).hexdigest().upper() == hashes[name] for name in FILES)
    plan = runner.read(HERE / "results/v4-blind-review-plan.json")
    selection = lambda entry: tuple(entry[key] for key in ("pair", "phase", "case_id", "repeat"))
    warning_keys = {(entry["branch"], entry["key"]) for entry in integrity["untracked_listing_limitations_for_explicit_review"]}
    reviewed_keys = {(entry["branch"], entry["key"]) for entry in outputs["entries"]}
    retention = reports["evidence-retention.json"]
    retained_archive = config.archive_path
    retention_ok = (retention["all_archive_entries_crc_and_sha256_verified"] is True
                    and retention["private_archive"] == retained_archive.relative_to(PROJECT).as_posix()
                    and runner.digest(retained_archive) == retention["archive_sha256"]
                    and retained_archive.stat().st_size == retention["archive_bytes"])
    with zipfile.ZipFile(retained_archive) as z:
        retention_ok = retention_ok and z.testzip() is None and len(z.infolist()) == retention["files"]
    frozen_summaries = all(report["skill_sha256"] == hashes["SKILL.md"]
        and runner.digest(out / name) == freeze[field]
        for report, name, field in ((development, f"development-iteration{iteration}.json", "development_summary_sha256"),
                                   (safety, f"safety-iteration{iteration}.json", "safety_summary_sha256"),
                                   (routing, f"routing-iteration{iteration}.json", "routing_summary_sha256")))
    gates = {
        "development": development["native_runs"] == development["accepted"] == development["body_reads"] == 6,
        "critical_contracts": safety["native_runs"] == safety["accepted"] == safety["body_reads"] == 36 and safety["critical_gate_pass"] is True,
        "native_routing": routing["model_runs"] == 9 and routing["routing_qualified"] is True,
        "candidate_bound_gate_reports": frozen_summaries,
        "real_quality": quality_qualified(analyses),
        "same_endpoint_positive": any(positive.values()),
        "blinded_review": blind["prespecified_pairs"] == blind["reviews_completed_before_parent_unblinding"] == 8
            and {selection(e) for e in blind["entries"]} == {selection(e) for e in plan["selected"]}
            and blind["parent_source_hashes_match"] is True
            and blind["reviewer_received_labels_costs_skill_or_hidden_fixes"] is False
            and blind["no_confirmed_unsafe_or_unjustified_treatment_machinery"] is True,
        "trace_integrity": trace["study_version"] == "v4" and trace["actual_native_runs"] == sum(config.expected_counts.values())
            and trace["all_planned_runs_present"] is True and not trace["integrity_issues"]
            and trace["distinct_thread_per_run"] is True and flags["pending_flag_count"] == 0
            and flags["reviewed_flag_count"] == trace["potential_flag_count"] == len(raw_flags) and flag_reviews_match,
        "output_limitations_reviewed": warning_keys == reviewed_keys
            and all(e["no_tracked_source_files_below_directory"] is True and e["not_a_symlink_or_junction"] is True
                        and e["original_workspace_retained"] is True and bool(e["setup_command_sha256"]) for e in outputs["entries"]),
        "two_file_package": package_ok,
        "evidence_integrity": integrity["qualification_evidence_integrity_pass"] is True
            and integrity["branch_counts"] == config.expected_counts and retention_ok,
    }
    qualified = all(gates.values())
    conclusion = reports["confirmation-conclusion.json"]
    if conclusion["real_valid_runs"] != 240 or conclusion["skill_sha256"] != hashes["SKILL.md"]:
        raise ValueError("Wrong confirmation conclusion")
    result = {"evaluated_at": runner.utc(), "candidate": "v4", "skill_sha256": hashes["SKILL.md"],
              "candidate_directory": candidate.relative_to(PROJECT).as_posix(),
              "production_assistant_qualified": qualified, "gates": gates,
              "same_endpoint_credible_positive": positive,
              "two_batch_material_metric_criterion": material,
              "stable_at_least10percent_benefit": {metric: qualified and value for metric, value in material.items()},
              "claim_requires_complete_qualification": True,
              "source_report_sha256": {name: runner.digest(out / name) for name in names},
              "qualification_code_sha256": runner.digest(Path(__file__)),
              "original_pre_model_code_sha256": control["qualification_code_sha256"],
              "implementation_only_amendment": "Original code/control and independent synthetic counterexamples retained; no model input, score, statistics or threshold changed; no timestamp backfill.",
              "supported_scope": "Tested Codex configuration and existing Python maintenance with project CI and review",
              "limits": "Observed guard acceptance and approximate paired task-cluster intervals; no universal defect, latency or benefit guarantee. Claude untested.",
              "global_skill_installation_or_external_publication": False}
    runner.write(out / "release-qualification.json", result)
    runner.write(HERE / "results/release-qualification.json", result)
    print(json.dumps({"production_assistant_qualified": qualified, "gates": gates}))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iteration", type=int, required=True)
    parser.add_argument("--candidate", type=Path, default=HERE / "candidates/v4-python")
    args = parser.parse_args()
    qualify(args.iteration, args.candidate)
