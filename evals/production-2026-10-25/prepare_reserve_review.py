"""Freeze outcome-blind contract screening of the untouched87-case reserve."""
from pathlib import Path
import argparse
import json
import runner

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent


def prepare():
    selection = runner.read(PROJECT / ".local/production-runtime/real-v3/real-selection.json")
    old = Path(runner.read(PROJECT / ".local/state.json")["runtime_root"])
    corpus = runner.read(old / "eligible.json")
    by_id = {case["id"]: case for case in corpus["cases"]}
    ids = selection["remaining_reserve"]
    assert len(ids) == len(set(ids)) == 87
    assert not set(ids) & set(selection["old_case_ids_excluded"] + selection["formal"] + selection["replication"])
    packet = []
    for case_id in ids:
        case = by_id[case_id]
        assert Path(case["base_dir"]).is_dir() and Path(case["fix_dir"]).is_dir()
        for relative, expected in case["preflight"]["overlay_sha256"].items():
            assert runner.digest(Path(case["fix_dir"]) / relative) == expected.upper()
        for relative, expected in case["preflight"]["base_production_sha256"].items():
            assert runner.digest(Path(case["base_dir"]) / relative) == expected.upper()
        packet.append({key: case[key] for key in ("id", "repo", "task", "base_dir", "fix_dir",
                                                  "production_files", "guard_paths", "regression_test_files", "issue_or_pr_url")})
    target = PROJECT / ".local/production-reserve-review/tasks.json"
    assert not target.exists(), "Do not silently replace a screening packet"
    runner.write(target, {"tasks": packet, "scope": "Untouched reserve only; no solver outcomes or skill provided"})
    manifest = {"frozen_at": runner.utc(), "reserve_count": len(ids), "reserve_native_model_calls": 0,
                "packet_sha256": runner.digest(target), "case_order": ids,
                "screening": "Independent outcome-blind review of task/base contract against pinned guard; adequate only",
                "future_selection_if_a_second_confirmation_is_needed": "First40 adequate cases in this already-frozen reserve order; first20 formal, next20 replication. Require at least40; no outcome-driven replacement.",
                "no_second_confirmation_started": True,
                "existing40_cases_and_grades_unchanged": True,
                "alpha": "Any second confirmation consumes the remaining existing candidate/confirmation slot; no extra tests or alpha budget."}
    runner.write(HERE / "results/reserve-screening-plan.json", manifest)
    print(json.dumps({"reserve_cases": len(packet), "packet": str(target), "native_model_calls": 0}))


def select():
    from export_results import public_value
    packet_path = PROJECT / ".local/production-reserve-review/tasks.json"
    report_path = packet_path.parent / "contract-review.json"
    plan = runner.read(HERE / "results/reserve-screening-plan.json")
    report = runner.read(report_path)
    assert runner.digest(packet_path) == plan["packet_sha256"] == report["packet_sha256"].upper()
    reviewed = {case["id"]: case["status"] for case in report["reviews"]}
    assert len(reviewed) == len(report["reviews"]) == 87
    assert set(reviewed) == set(plan["case_order"])
    eligible = [case for case in plan["case_order"] if reviewed[case] == "adequate"]
    assert len(eligible) >= 40
    selected = {"formal": eligible[:20], "replication": eligible[20:40]}
    result = {"selected_at": runner.utc(), "native_model_calls_on_selected_cases": 0,
              "screening_plan_sha256": runner.digest(HERE / "results/reserve-screening-plan.json"),
              "independent_raw_review_sha256": runner.digest(report_path),
              "adequate_count": len(eligible), "status_counts": report["counts"]["statuses"],
              **selected, "remaining_adequate": eligible[40:],
              "selection_rule": "First40 adequate in already-frozen reserve order; no model outcomes used",
              "conditional_second_confirmation_only": True,
              "current_experiment_inputs_and_scores_unchanged": True}
    path = HERE / "results/second-corpus-selection.json"
    assert not path.exists(), "Do not overwrite a selection"
    runner.write(HERE / "results/reserve-task-contract-review.json", public_value(report))
    runner.write(path, result)
    print(json.dumps({"adequate": len(eligible), "selected_tasks": 40, "native_model_calls": 0}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--select", action="store_true", help="Apply frozen screening rule after blind review")
    if parser.parse_args().select:
        select()
    else:
        prepare()
