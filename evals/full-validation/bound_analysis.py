"""Conservative token upper bound for baseline-only missing usage.

This auxiliary analysis never imputes actual costs or changes input records.
It applies only when skill usage is entirely exact and baseline missing usage
is explicitly (usage_complete=False, total_tokens=None). Complete measurements
are delegated to analyze.py. API infinity values follow analyze.py's convention.

The result is a bound relative to the SAME complete-data task-cluster
percentile method, not a newly exact population confidence guarantee. It is
not a pilot variance estimator or a sample-size planning procedure.
"""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np

# Load only the adjacent, unchanged pure analysis implementation. This also
# supports callers importing this file by absolute path rather than sys.path.
_spec = importlib.util.spec_from_file_location(
    "_token_bound_original_analysis", Path(__file__).with_name("analyze.py")
)
if _spec is None or _spec.loader is None:
    raise ImportError("Cannot load the adjacent analyze.py")
_analysis = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_analysis)

__all__ = ["analyze_token_bound"]


def _quality(prepared: dict[str, Any], accepted: np.ndarray | None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "available": accepted is not None,
        "baseline_success_rate": None,
        "skill_success_rate": None,
        "success_rate_difference_skill_minus_baseline": None,
        "observed_gate_pass": False,
        "degraded_tasks": [],
        "degraded_task_count": 0,
        "interpretation": "Observed batch gate only; not universal safety or noninferiority.",
    }
    if accepted is None:
        return result
    totals = accepted.sum(axis=0)
    denominator = 3 * len(prepared["task_ids"])
    result.update(
        baseline_success_rate=float(totals[0] / denominator),
        skill_success_rate=float(totals[1] / denominator),
        success_rate_difference_skill_minus_baseline=float(
            (totals[1] - totals[0]) / denominator
        ),
        observed_gate_pass=bool(totals[1] >= totals[0]),
    )
    for index, case_id in enumerate(prepared["task_ids"]):
        if accepted[index, 1] < accepted[index, 0]:
            result["degraded_tasks"].append({
                "case_id": case_id,
                "repo": prepared["groups"][case_id][0]["repo"],
                "baseline_accepted": int(accepted[index, 0]),
                "skill_accepted": int(accepted[index, 1]),
                "success_rate_difference": float(
                    (accepted[index, 1] - accepted[index, 0]) / 3
                ),
            })
    result["degraded_task_count"] = len(result["degraded_tasks"])
    return result


def analyze_token_bound(
    records: Iterable[Mapping[str, Any]], phase: str, bootstrap_draws: int = 20000
) -> dict[str, Any]:
    """Bound token cost-per-accepted skill/baseline without changing any None.

    All failure costs and all three repeats in both arms stay in each task
    cluster. An unknown baseline cost contributes no positive information to
    the LOWER BOUND array; it is never reported as an actual zero cost.
    """
    if type(bootstrap_draws) is not int or bootstrap_draws < 1:
        raise ValueError("bootstrap_draws must be a positive integer")
    prepared = _analysis._prepare(records, phase)
    task_ids = prepared["task_ids"]
    n = len(task_ids)
    allowed_missing_indices = set()
    missing_keys = []
    missing_counts = {"baseline": 0, "with-skill": 0}
    skill_inexact = 0
    for row in prepared["rows"]:
        genuine_unknown = (
            "total_tokens" in row
            and row["total_tokens"] is None
            and row.get("usage_complete") is False
        )
        if genuine_unknown and isinstance(row.get("arm"), str) and row["arm"] in missing_counts:
            missing_counts[row["arm"]] += 1
            missing_keys.append({
                "case_id": row.get("case_id"),
                "repeat": row.get("repeat"),
                "arm": row["arm"],
            })
        if genuine_unknown and row.get("arm") == "baseline":
            allowed_missing_indices.add(row["_record_index"])
        if row.get("arm") == "with-skill" and not (
            row.get("usage_complete") is True
            and "total_tokens" in row
            and _analysis._number(row["total_tokens"])
        ):
            skill_inexact += 1

    # Suppress ONLY the original exact-usage warning for explicitly permitted
    # baseline None entries. Every other original issue, including invalid
    # time/identity/flags/partial numeric usage, blocks the auxiliary method.
    blocking = [
        issue for issue in prepared["integrity"]["issues"]
        if not (
            issue["code"] == "usage_unavailable"
            and issue.get("record_index") in allowed_missing_indices
        )
    ]
    input_ok = prepared["structure_ok"] and not blocking
    accepted = None
    if prepared["structure_ok"]:
        accepted = np.array([
            [
                sum(row["accepted"] for row in prepared["groups"][case_id]
                    if row["arm"] == arm)
                for arm in _analysis.ARMS
            ]
            for case_id in task_ids
        ], dtype=np.int64)
    quality = _quality(prepared, accepted)

    result: dict[str, Any] = {
        "schema_version": 1,
        "analysis_type": "conservative_token_upper_bound",
        "bound_label": "Baseline-cost LOWER bound implies a ratio UPPER bound; NOT an actual ratio or actual two-sided CI.",
        "phase": phase,
        "status": "blocked",
        "applicable": False,
        "task_count": n,
        "actual_ratio": None,
        "baseline_cost_lower_bound": None,
        "skill_exact_cost": None,
        "ratio_upper_bound": None,
        "upper_one_sided_97_5": None,
        "gain_lower_confidence_bound": None,
        "missing_counts": missing_counts,
        "missing_run_keys": missing_keys,
        "skill_inexact_run_count": skill_inexact,
        "integrity": {
            "bound_input_ok": bool(input_ok),
            "blocking_issues": blocking,
            "original_exact_analysis": prepared["integrity"],
        },
        "quality": quality,
        "bootstrap": {
            "unit": "paired_complete_task_cluster",
            "draws": bootstrap_draws,
            "seed": _analysis.BOOTSTRAP_SEED,
            "task_ids_in_sampling_order": task_ids,
            "repeats_per_arm": 3,
            "quantile_method": "inverted_cdf",
            "one_sided_confidence_level": 0.975,
            "computed": False,
            "undefined_draws_dropped": False,
        },
        "minimum_confirmatory_task_count_satisfied": n >= _analysis.MIN_TASKS,
        "finite_positive_comparison_bound": False,
        "bound_below_0_90": False,
        "batch_criterion_met": False,
        "threshold_ratio": _analysis.THRESHOLD,
        "one_sided_alpha": 0.025,
        "requires_other_disjoint_confirmatory_batch": True,
        "pilot_planned_task_count_match": "Parent controller must check the frozen pilot-derived batch size.",
        "planning_supported": False,
        "not_for_pilot_planning": True,
        "limitations": {
            "coverage_guarantee": "monotonic_conservatism_relative_to_same_complete_data_method_only",
            "population_percentile_bootstrap_coverage": "approximate",
            "mar_assumption_required": False,
            "cost_assumption": "Unknown baseline costs are finite and nonnegative; exact usage labels must cover all paid attempts/turns.",
            "power_scope": "Original 0.80 planning power is per confirmatory batch.",
            "joint_power_if_independent_and_each_at_0_80": 0.64,
            "planning_warning": "A cost lower bound does not bound jackknife SD and cannot replace pilot sample-size planning.",
            "actual_estimation": "Actual ratio and actual two-sided CI are generally unidentified.",
        },
    }
    if not input_ok:
        if skill_inexact:
            # A trivial unbounded envelope, not a computed confidence bound.
            result["ratio_upper_bound"] = math.inf
        result["reason"] = "Only baseline False/None usage gaps with every skill cost exact are supported; all other invalid input blocks the method."
        return result

    # This array is explicitly (baseline LOWER BOUND, skill EXACT COST).
    # It is not an actual-cost array and is never written into the records.
    bound_costs = np.zeros((n, 2), dtype=float)
    for index, case_id in enumerate(task_ids):
        for row in prepared["groups"][case_id]:
            if row["_record_index"] in allowed_missing_indices:
                continue
            bound_costs[index, _analysis.ARMS.index(row["arm"])] += float(
                row["total_tokens"]
            )
    cost_totals = bound_costs.sum(axis=0)
    result["baseline_cost_lower_bound"] = float(cost_totals[0])
    result["skill_exact_cost"] = float(cost_totals[1])
    result["accepted_total"] = {
        arm: int(accepted[:, index].sum())
        for index, arm in enumerate(_analysis.ARMS)
    }
    if not allowed_missing_indices:
        result["status"] = "not_needed"
        result["reason"] = "All usage is exact; use the original analyze_records result."
        return result

    result["applicable"] = True
    success_totals = accepted.sum(axis=0)
    ratio = float(_analysis._ratio(cost_totals, success_totals))
    result["ratio_upper_bound"] = _analysis._public_number(ratio)
    finite_positive = bool(
        np.all(success_totals > 0)
        and np.all(cost_totals > 0)
        and math.isfinite(ratio)
        and ratio > 0
    )
    result["finite_positive_comparison_bound"] = finite_positive

    samples = np.empty(bootstrap_draws, dtype=float)
    rng = np.random.default_rng(_analysis.BOOTSTRAP_SEED)
    # Match analyze_records exactly: sorted task IDs, fixed seed, same 1000-draw
    # chunks and one common index matrix for both arms and all three repeats.
    for start in range(0, bootstrap_draws, 1000):
        stop = min(start + 1000, bootstrap_draws)
        index = rng.integers(0, n, size=(stop - start, n))
        samples[start:stop] = _analysis._ratio(
            bound_costs[index].sum(axis=1), accepted[index].sum(axis=1)
        )
    distribution = _analysis._interval(samples)
    # Reuse the original quantile/undefined convention, exposing ONLY the upper
    # bound. In particular, never publish its interval as an actual-ratio CI.
    result["bootstrap"].update({
        key: distribution[key]
        for key in (
            "undefined_draws", "positive_infinite_draws",
            "negative_infinite_draws", "finite_draws",
        )
    })
    result["bootstrap"]["computed"] = True
    result["bootstrap"]["upper_bound_available"] = distribution["available"]
    if not distribution["available"]:
        result["status"] = "undefined"
        result["reason"] = distribution["reason"]
        return result

    upper = distribution["interval"][1]
    result.update(
        status="bound_available",
        upper_one_sided_97_5=upper,
        gain_lower_confidence_bound=1 - upper,
        bound_below_0_90=bool(upper < _analysis.THRESHOLD),
    )
    result["batch_criterion_met"] = bool(
        phase in ("formal", "replication")
        and n >= _analysis.MIN_TASKS
        and quality["observed_gate_pass"]
        and finite_positive
        and upper < _analysis.THRESHOLD
    )
    if not finite_positive:
        result["boundary_note"] = "Original zero-cost/zero-success boundary cannot become a savings proof."
    return result

