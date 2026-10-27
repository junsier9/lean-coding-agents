"""Preregistered paired task-cluster analysis; standard library plus NumPy only.

No solver, grader, network, or package-install calls are made here. Failed and
unfinished attempts retain their recorded costs. Repeats are never sampling
units: one bootstrap draw selects whole tasks, keeping both arms and all three
repeats. API results use float infinity for extended-real boundaries and None
for undefined values. The CLI writes strict JSON, encoding infinities as the
strings "Infinity" and "-Infinity".

The two-sided 95% percentile interval's upper endpoint is also the one-sided
97.5% bound. A batch threshold is not a final claim: the parent controller must
require the same metric to pass in both disjoint confirmatory batches.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
from numbers import Real
from pathlib import Path
from statistics import NormalDist
from typing import Any, Iterable, Mapping

import numpy as np

ARMS = ("baseline", "with-skill")
PHASES = ("pilot", "formal", "replication")
REPEATS = (0, 1, 2)
BOOTSTRAP_SEED = 20261024
EXPECTED_PILOT_TASKS = 10
MIN_TASKS = 20
MAX_TASKS = 80
CURRENT_ELIGIBLE_TASKS = 61
THRESHOLD = 0.90
PLANNING_ALTERNATIVE = 0.80
SD_FLOOR = 0.05
EXPECTED_KEYS = {(arm, repeat) for arm in ARMS for repeat in REPEATS}


def _number(value: Any) -> bool:
    return (
        isinstance(value, Real)
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and value >= 0
    )


def _prepare(records: Iterable[Mapping[str, Any]], phase: str) -> dict[str, Any]:
    if phase not in PHASES:
        raise ValueError(f"phase must be one of {PHASES}")
    rows = list(records)
    selected: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    excluded = Counter()

    def issue(code: str, scope: str, message: str, **details: Any) -> None:
        issues.append({"code": code, "scope": scope, "message": message, **details})

    for index, row in enumerate(rows, 1):
        if not isinstance(row, Mapping):
            issue("invalid_record", "all", "Record is not an object.", record_index=index)
            continue
        record_phase = row.get("phase")
        if not isinstance(record_phase, str) or record_phase not in PHASES:
            issue("invalid_phase", "all", "Missing or unknown phase.", record_index=index)
            continue
        if record_phase != phase:
            excluded[record_phase] += 1
            continue
        r = dict(row)
        r["_record_index"] = index
        selected.append(r)
        case_id = r.get("case_id")
        detail = {"record_index": index, "case_id": case_id}
        if not isinstance(case_id, str) or not case_id.strip():
            issue("invalid_case_id", "all", "case_id must be a nonempty string.", **detail)
        if not isinstance(r.get("repo"), str) or not r["repo"].strip():
            issue("invalid_repo", "all", "repo must be a nonempty string.", **detail)
        if not isinstance(r.get("arm"), str) or r["arm"] not in ARMS:
            issue("invalid_arm", "all", "arm is not baseline or with-skill.", **detail)
        if type(r.get("repeat")) is not int or r["repeat"] not in REPEATS:
            issue("invalid_repeat", "all", "repeat must be an integer from 0 to 2.", **detail)
        for name in ("accepted", "completed"):
            if type(r.get(name)) is not bool:
                issue("invalid_" + name, "all", f"{name} must be Boolean.", **detail)
        if r.get("accepted") is True and r.get("completed") is False:
            issue("accepted_unfinished", "all", "An unfinished run cannot be accepted.", **detail)
        if not _number(r.get("wall_seconds")):
            issue("invalid_wall_seconds", "time", "Wall time must be finite and nonnegative.", **detail)
        if type(r.get("usage_complete")) is not bool:
            issue("invalid_usage_complete", "token", "usage_complete must be Boolean.", **detail)
        if not _number(r.get("total_tokens")) or r.get("usage_complete") is not True:
            issue(
                "usage_unavailable", "token",
                "Exact token usage is unavailable; missing or partial usage is never zero.",
                **detail,
            )

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in selected:
        if isinstance(row.get("case_id"), str) and row["case_id"].strip():
            groups[row["case_id"]].append(row)
    task_ids = sorted(groups)
    duplicate_keys = []
    incomplete = []
    repo_conflicts = []
    complete_task_ids = []
    for case_id in task_ids:
        task = groups[case_id]
        valid_key_rows = [
            r for r in task
            if isinstance(r.get("arm"), str) and r["arm"] in ARMS
            and type(r.get("repeat")) is int and r["repeat"] in REPEATS
        ]
        counts = Counter((r["arm"], r["repeat"]) for r in valid_key_rows)
        for (arm, repeat), count in sorted(counts.items()):
            if count > 1:
                duplicate_keys.append(
                    {"case_id": case_id, "arm": arm, "repeat": repeat, "count": count}
                )
        missing = sorted(EXPECTED_KEYS - set(counts))
        if missing or len(task) != 6 or any(count != 1 for count in counts.values()):
            incomplete.append({
                "case_id": case_id,
                "record_count": len(task),
                "missing_run_keys": [{"arm": arm, "repeat": repeat} for arm, repeat in missing],
            })
        else:
            complete_task_ids.append(case_id)
        repos = sorted({r["repo"] for r in task if isinstance(r.get("repo"), str)})
        if len(repos) != 1:
            repo_conflicts.append({"case_id": case_id, "repos": repos})
    if duplicate_keys:
        issue("duplicate_run_keys", "all", "Duplicate run keys are not deduplicated or averaged.")
    if incomplete:
        issue("incomplete_task_clusters", "all", "Every task must contain both arms and all three repeats.")
    if repo_conflicts:
        issue("repo_conflict", "all", "A case_id refers to conflicting repositories.")
    if not selected or not task_ids:
        issue("empty_phase", "all", "No analyzable task records in the selected phase.")

    structure_ok = not any(x["scope"] == "all" for x in issues)
    integrity = {
        "ok": not issues,
        "structure_ok": structure_ok,
        "input_record_count": len(rows),
        "selected_record_count": len(selected),
        "other_phase_record_counts": dict(sorted(excluded.items())),
        "task_count": len(task_ids),
        "complete_task_count": len(complete_task_ids),
        "expected_runs_per_task": 6,
        "expected_selected_run_count": 6 * len(task_ids),
        "duplicate_run_keys": duplicate_keys,
        "incomplete_tasks": incomplete,
        "repo_conflicts": repo_conflicts,
        "issues": issues,
        "no_record_repair_or_silent_deduplication": True,
        "incomplete_tasks_not_silently_removed": True,
    }
    return {
        "rows": selected, "groups": groups, "task_ids": task_ids,
        "integrity": integrity, "structure_ok": structure_ok,
        "token_ok": structure_ok and not any(x["scope"] == "token" for x in issues),
        "time_ok": structure_ok and not any(x["scope"] == "time" for x in issues),
    }


def _arrays(prepared: dict[str, Any], field: str) -> tuple[np.ndarray, np.ndarray]:
    ids = prepared["task_ids"]
    costs = np.zeros((len(ids), 2), dtype=float)
    accepted = np.zeros((len(ids), 2), dtype=np.int64)
    for i, case_id in enumerate(ids):
        for row in prepared["groups"][case_id]:
            j = ARMS.index(row["arm"])
            costs[i, j] += float(row[field])
            accepted[i, j] += int(row["accepted"])
    return costs, accepted


def _ratio(costs: np.ndarray, accepted: np.ndarray) -> np.ndarray:
    """Last axis is baseline, skill; retain infinity and undefined 0/0."""
    costs = np.asarray(costs, dtype=float)
    accepted = np.asarray(accepted, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        per_accepted = costs / accepted
        return per_accepted[..., 1] / per_accepted[..., 0]


def _public_number(value: Any) -> float | None:
    value = float(value)
    return None if math.isnan(value) else value


def _interval(values: np.ndarray) -> dict[str, Any]:
    """Do not drop undefined draws or interpolate across infinity."""
    undefined = int(np.isnan(values).sum())
    result: dict[str, Any] = {
        "level": 0.95,
        "quantile_method": "inverted_cdf",
        "undefined_draws": undefined,
        "positive_infinite_draws": int(np.isposinf(values).sum()),
        "negative_infinite_draws": int(np.isneginf(values).sum()),
        "finite_draws": int(np.isfinite(values).sum()),
        "draws": len(values),
        "interval": None,
        "available": False,
    }
    if undefined:
        result["reason"] = "Undefined bootstrap draws retained; percentile CI is not defined."
        return result
    q = np.quantile(values, [0.025, 0.975], method="inverted_cdf")
    result.update(available=True, interval=[float(q[0]), float(q[1])])
    return result


def _observed_totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    totals = {}
    for arm in ARMS:
        arm_rows = [r for r in rows if r.get("arm") == arm]
        token_rows = [r for r in arm_rows if _number(r.get("total_tokens"))]
        time_rows = [r for r in arm_rows if _number(r.get("wall_seconds"))]
        totals[arm] = {
            "record_count": len(arm_rows),
            "accepted": sum(r.get("accepted") is True for r in arm_rows),
            "completed": sum(r.get("completed") is True for r in arm_rows),
            "unfinished": sum(r.get("completed") is False for r in arm_rows),
            "rejected": sum(r.get("accepted") is False for r in arm_rows),
            "reported_token_total": (
                sum(float(r["total_tokens"]) for r in token_rows) if token_rows else None
            ),
            "reported_token_count": len(token_rows),
            "reported_token_total_is_partial": any(
                r.get("usage_complete") is not True or not _number(r.get("total_tokens"))
                for r in arm_rows
            ),
            "exact_usage_count": sum(
                r.get("usage_complete") is True and _number(r.get("total_tokens"))
                for r in arm_rows
            ),
            "reported_wall_seconds": (
                sum(float(r["wall_seconds"]) for r in time_rows) if time_rows else None
            ),
            "reported_wall_count": len(time_rows),
            "reported_wall_total_is_partial": len(time_rows) != len(arm_rows),
        }
    return totals


def analyze_records(
    records: Iterable[Mapping[str, Any]], phase: str, bootstrap_draws: int = 20000
) -> dict[str, Any]:
    """Analyze one phase; resample complete task clusters, not individual runs."""
    if type(bootstrap_draws) is not int or bootstrap_draws < 1:
        raise ValueError("bootstrap_draws must be a positive integer")
    p = _prepare(records, phase)
    n = len(p["task_ids"])
    totals = _observed_totals(p["rows"])
    quality: dict[str, Any] = {
        "available": p["structure_ok"],
        "baseline_success_rate": None, "skill_success_rate": None,
        "success_rate_difference_skill_minus_baseline": None,
        "observed_gate_pass": False, "success_rate_difference_ci95": None,
        "degraded_tasks": [], "worsened_repeat_pairs": [],
        "interpretation": "Observed batch gate only; not a universal safety or noninferiority guarantee.",
    }
    data: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for metric, field in (("token", "total_tokens"), ("time", "wall_seconds")):
        if p[metric + "_ok"]:
            data[metric] = _arrays(p, field)
    accepted = None
    if p["structure_ok"]:
        accepted = np.array([
            [sum(r["accepted"] for r in p["groups"][case_id] if r["arm"] == arm) for arm in ARMS]
            for case_id in p["task_ids"]
        ], dtype=np.int64)
        success_totals = accepted.sum(axis=0)
        rates = success_totals / (3 * n)
        quality.update(
            baseline_success_rate=float(rates[0]),
            skill_success_rate=float(rates[1]),
            success_rate_difference_skill_minus_baseline=float(rates[1] - rates[0]),
            observed_gate_pass=bool(success_totals[1] >= success_totals[0]),
        )
        for i, case_id in enumerate(p["task_ids"]):
            if accepted[i, 1] < accepted[i, 0]:
                quality["degraded_tasks"].append({
                    "case_id": case_id, "repo": p["groups"][case_id][0]["repo"],
                    "baseline_accepted": int(accepted[i, 0]),
                    "skill_accepted": int(accepted[i, 1]),
                    "success_rate_difference": float((accepted[i, 1] - accepted[i, 0]) / 3),
                })
            lookup = {(r["arm"], r["repeat"]): r for r in p["groups"][case_id]}
            for repeat in REPEATS:
                if lookup[("baseline", repeat)]["accepted"] and not lookup[("with-skill", repeat)]["accepted"]:
                    quality["worsened_repeat_pairs"].append({"case_id": case_id, "repeat": repeat})
    quality["degraded_task_count"] = len(quality["degraded_tasks"])
    quality["worsened_repeat_pair_count"] = len(quality["worsened_repeat_pairs"])

    samples = {name: np.empty(bootstrap_draws, dtype=float) for name in data}
    quality_samples = np.empty(bootstrap_draws, dtype=float) if accepted is not None else None
    if p["structure_ok"]:
        rng = np.random.default_rng(BOOTSTRAP_SEED)
        # Bounded allocations; the same task indices pair both arms and metrics.
        for start in range(0, bootstrap_draws, 1000):
            stop = min(start + 1000, bootstrap_draws)
            index = rng.integers(0, n, size=(stop - start, n))
            drawn_accepted = accepted[index].sum(axis=1)
            if quality_samples is not None:
                quality_samples[start:stop] = (drawn_accepted[:, 1] - drawn_accepted[:, 0]) / (3 * n)
            for name, (costs, _) in data.items():
                samples[name][start:stop] = _ratio(costs[index].sum(axis=1), drawn_accepted)
        quality["success_rate_difference_ci95"] = _interval(quality_samples)
    metrics: dict[str, Any] = {}
    minimum_count_ok = n >= MIN_TASKS
    for name in ("token", "time"):
        m: dict[str, Any] = {
            "available": name in data, "ratio": None, "ratio_status": "unavailable",
            "gain": None, "ratio_ci95": None, "gain_ci95": None,
            "upper_one_sided_97_5": None, "material_gain_threshold_ratio": THRESHOLD,
            "bound_below_0_90": False, "finite_estimand_observed": False,
            "batch_criterion_met": False,
        }
        if name not in data:
            m["reason"] = (
                "Task/run integrity failed." if not p["structure_ok"]
                else "Missing, partial, or invalid usage; no zero imputation." if name == "token"
                else "Missing or invalid wall time."
            )
            metrics[name] = m
            continue
        costs, successes = data[name]
        cost_sum = costs.sum(axis=0)
        accepted_sum = successes.sum(axis=0)
        r = float(_ratio(cost_sum, accepted_sum))
        with np.errstate(divide="ignore", invalid="ignore"):
            cpa = cost_sum / accepted_sum
        interval = _interval(samples[name])
        estimable = bool(
            np.all(accepted_sum > 0) and np.all(cost_sum > 0) and math.isfinite(r) and r > 0
        )
        m.update(
            ratio=_public_number(r),
            ratio_status="undefined" if math.isnan(r) else "infinite" if math.isinf(r) else "finite",
            gain=_public_number(1 - r),
            cost_total={arm: float(cost_sum[i]) for i, arm in enumerate(ARMS)},
            accepted_total={arm: int(accepted_sum[i]) for i, arm in enumerate(ARMS)},
            cost_per_accepted={arm: _public_number(cpa[i]) for i, arm in enumerate(ARMS)},
            ratio_ci95=interval,
            finite_estimand_observed=estimable,
        )
        if interval["available"]:
            low, high = interval["interval"]
            m["gain_ci95"] = [1 - high, 1 - low]
            m["upper_one_sided_97_5"] = high
            m["bound_below_0_90"] = bool(high < THRESHOLD)
        m["batch_criterion_met"] = bool(
            phase in ("formal", "replication") and minimum_count_ok
            and quality["observed_gate_pass"] and estimable
            and m["bound_below_0_90"] and interval["available"]
        )
        if not estimable:
            m["boundary_note"] = (
                "Zero accepted counts or zero cost make a finite positive comparison nonestimable; "
                "extended-real boundaries are reported, not treated as proof."
            )
        metrics[name] = m

    return {
        "schema_version": 1, "phase": phase, "task_count": n,
        "bootstrap": {
            "unit": "paired_complete_task_cluster", "draws": bootstrap_draws,
            "seed": BOOTSTRAP_SEED, "repeats_per_arm": 3,
            "task_ids_in_sampling_order": p["task_ids"],
            "both_arms_and_all_repeats_kept_together": True,
            "undefined_draws_dropped": False,
            "method": "percentile; inverted empirical CDF; undefined draws invalidate CI",
        },
        "integrity": p["integrity"], "observed_totals": totals,
        "quality": quality, "metrics": metrics,
        "minimum_confirmatory_task_count_satisfied": minimum_count_ok,
        "pilot_planned_task_count_match": "Parent controller must check the frozen pilot-derived batch size.",
        "final_stability_claim": "Not decided here; require same metric and quality gate in both formal and replication.",
        "interpretation": [
            "All failed and unfinished attempts contribute recorded costs; no task is removed for poor outcomes.",
            "Only the selected phase is analyzed. Duplicate/incomplete clusters block batch evidence.",
            "Missing usage blocks token evidence independently; valid wall-time evidence remains reportable.",
            "Quality intervals are descriptive; the observed gate is not a statistical no-regression guarantee.",
            "Percentile coverage and pilot-based power are approximations on this curated task corpus.",
        ],
    }


def sample_size(pilot_records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Task-delete jackknife SD of log overall cost-per-accepted ratio."""
    p = _prepare(pilot_records, "pilot")
    n = len(p["task_ids"])
    delta = math.log(THRESHOLD) - math.log(PLANNING_ALTERNATIVE)
    z_sum = NormalDist().inv_cdf(0.975) + NormalDist().inv_cdf(0.80)
    estimates: dict[str, Any] = {}
    for name, field in (("token", "total_tokens"), ("time", "wall_seconds")):
        result: dict[str, Any] = {"available": False, "required_n": None}
        if not p[name + "_ok"] or n < 2:
            result["reason"] = "Pilot has invalid/missing measurements or fewer than two complete task clusters."
            estimates[name] = result
            continue
        costs, accepted = _arrays(p, field)
        c_sum, a_sum = costs.sum(axis=0), accepted.sum(axis=0)
        leave_cost = c_sum - costs
        leave_accepted = a_sum - accepted
        valid = bool(
            np.all(c_sum > 0) and np.all(a_sum > 0)
            and np.all(leave_cost > 0) and np.all(leave_accepted > 0)
        )
        if not valid:
            result["reason"] = "Full or leave-one-task-out log ratio is undefined/nonfinite; planning cannot assume zero variance."
            estimates[name] = result
            continue
        theta = float(np.log(_ratio(c_sum, a_sum)))
        leave_theta = np.log(_ratio(leave_cost, leave_accepted))
        pseudovalues = n * theta - (n - 1) * leave_theta
        if not math.isfinite(theta) or not np.isfinite(pseudovalues).all():
            result["reason"] = "Jackknife log-ratio pseudovalues are nonfinite."
            estimates[name] = result
            continue
        raw_sd = float(np.std(pseudovalues, ddof=1))
        sd = max(SD_FLOOR, raw_sd)
        required = math.ceil((z_sum * sd / delta) ** 2)
        result.update(
            available=True, log_ratio=theta,
            jackknife_pseudovalues=[float(x) for x in pseudovalues],
            jackknife_pseudovalue_sd=raw_sd, planning_sd=sd,
            sd_floor=SD_FLOOR, required_n=required,
        )
        estimates[name] = result
    available = all(e["available"] for e in estimates.values())
    raw_n = max(e["required_n"] for e in estimates.values()) if available else None
    target = min(MAX_TASKS, max(MIN_TASKS, raw_n)) if raw_n is not None else None
    protocol_pilot_ok = n == EXPECTED_PILOT_TASKS
    shortfall = max(0, n + 2 * target - CURRENT_ELIGIBLE_TASKS) if target is not None else None
    return {
        "schema_version": 1, "pilot_task_count": n,
        "expected_pilot_task_count": EXPECTED_PILOT_TASKS,
        "protocol_pilot_task_count_satisfied": protocol_pilot_ok,
        "planning_available": available and protocol_pilot_ok,
        "planning_status": "available" if available and protocol_pilot_ok else "unavailable",
        "formula": "ceil(((z(.975)+z(.80))*SD/(log(.90)-log(.80)))**2)",
        "planning_alternative_ratio": PLANNING_ALTERNATIVE,
        "null_boundary_ratio": THRESHOLD, "nominal_power": 0.80,
        "power_scope": "per_confirmatory_batch",
        "two_batch_joint_power_if_independent_and_each_exactly_0_80": 0.64,
        "metrics": estimates, "required_n_before_cap": raw_n,
        "target_tasks_per_confirmatory_batch": target,
        "minimum_per_batch": MIN_TASKS, "maximum_per_batch": MAX_TASKS,
        "underpowered": bool(raw_n > MAX_TASKS) if raw_n is not None else None,
        "adequacy_claim_permitted": bool(available and protocol_pilot_ok and raw_n <= MAX_TASKS),
        "current_eligible_tasks": CURRENT_ELIGIBLE_TASKS,
        "independent_tasks_required_total": n + 2 * target if target is not None else None,
        "untouched_tasks_after_pilot": max(0, CURRENT_ELIGIBLE_TASKS - n),
        "additional_eligible_tasks_needed_before_formal": shortfall,
        "extension_required_before_formal": bool(shortfall) if shortfall is not None else None,
        "integrity": p["integrity"],
        "notes": [
            "Use the maximum required N across token and time, then apply minimum 20/maximum 80 per batch.",
            "If uncapped N exceeds 80, the cap is underpowered relative to the planning approximation.",
            "An unavailable metric or undefined log ratio does not default to N=20.",
            "Formal and replication require distinct tasks and the same frozen target size.",
            "Ten-task pilot variance is uncertain; nominal power is not guaranteed realized power.",
            "Requiring both batches to pass does not provide 80% joint power; .80 each gives approximately .64 before quality gates.",
        ],
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        if math.isnan(value):
            return None
        return "Infinity" if value > 0 else "-Infinity"
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", required=True, type=Path)
    parser.add_argument("--phase", required=True, choices=PHASES)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    records = []
    with args.records.open(encoding="utf-8-sig") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                parser.error(f"Invalid JSONL at line {line_number}: {exc}")
    result = analyze_records(records, args.phase)
    if args.phase == "pilot":
        result["sample_size"] = sample_size(records)
    # A mistaken output path must not overwrite the source records.
    if args.output.resolve() == args.records.resolve():
        parser.error("--output must differ from --records")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(_json_safe(result), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "phase": args.phase, "task_count": result["task_count"],
        "structure_ok": result["integrity"]["structure_ok"],
        "token_batch_criterion_met": result["metrics"]["token"]["batch_criterion_met"],
        "time_batch_criterion_met": result["metrics"]["time"]["batch_criterion_met"],
        "output": str(args.output),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

