"""Frozen98.75% paired task-cluster assessment for this production iteration."""
from pathlib import Path
import argparse
import json
import math
import sys
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "full-validation"))
import analyze as core

ALPHA = .0125
DRAWS = 40000
SEED = 20261025


def assess(rows, phase, draws=DRAWS):
    prepared = core._prepare(rows, phase)
    result = {"phase": phase, "task_count": len(prepared["task_ids"]),
              "integrity": prepared["integrity"], "metrics": {},
              "confidence": 1 - ALPHA, "alpha": ALPHA, "draws": draws, "seed": SEED,
              "sampling_unit": "complete paired task with both arms and all3 repeats"}
    if not prepared["structure_ok"]:
        result["quality"] = {"gate_pass": False}
        return result
    n = len(prepared["task_ids"])
    accepted = np.array([[sum(int(row["accepted"]) for row in prepared["groups"][case]
                              if row["arm"] == arm) for arm in core.ARMS]
                         for case in prepared["task_ids"]], dtype=np.int64)
    totals = accepted.sum(axis=0)
    degraded = [case for i, case in enumerate(prepared["task_ids"]) if accepted[i, 1] < accepted[i, 0]]
    quality = bool(totals[1] >= totals[0] and not degraded)
    result["quality"] = {"baseline_accepted": int(totals[0]), "skill_accepted": int(totals[1]),
                         "runs_per_arm": n * 3, "degraded_tasks": degraded, "gate_pass": quality}
    costs, modes = {}, {}
    if prepared["time_ok"]:
        costs["time"], _ = core._arrays(prepared, "wall_seconds")
        modes["time"] = "exact"
    if prepared["token_ok"]:
        costs["token"], _ = core._arrays(prepared, "total_tokens")
        modes["token"] = "exact"
    else:
        missing = [row for row in prepared["rows"] if row.get("usage_complete") is not True
                   or not core._number(row.get("total_tokens"))]
        valid_bound = bool(missing) and all(row["arm"] == "baseline" and
                     row.get("usage_complete") is False and row.get("total_tokens") is None
                     for row in missing)
        known = [row for row in prepared["rows"] if row not in missing]
        valid_bound = valid_bound and all(row.get("usage_complete") is True and
                      core._number(row.get("total_tokens")) for row in known)
        if valid_bound:
            bound_costs = np.zeros((n, 2), dtype=float)
            for i, case in enumerate(prepared["task_ids"]):
                for row in prepared["groups"][case]:
                    if row["usage_complete"]:
                        bound_costs[i, core.ARMS.index(row["arm"])] += row["total_tokens"]
            costs["token"] = bound_costs
            modes["token"] = "baseline_cost_lower_bound_only"
        result["missing_usage"] = [{key: row[key] for key in ("case_id", "repeat", "arm")} for row in missing]
    samples = {metric: np.empty(draws, dtype=float) for metric in costs}
    rng = np.random.default_rng(SEED)
    for start in range(0, draws, 1000):
        stop = min(start + 1000, draws)
        indices = rng.integers(0, n, size=(stop - start, n))
        sampled_accepted = accepted[indices].sum(axis=1)
        for metric, values in costs.items():
            samples[metric][start:stop] = core._ratio(values[indices].sum(axis=1), sampled_accepted)
    for metric in ("token", "time"):
        if metric not in costs:
            result["metrics"][metric] = {"available": False, "batch_criterion_met": False}
            continue
        values = samples[metric]
        total_cost = costs[metric].sum(axis=0)
        observed = float(core._ratio(total_cost, totals))
        undefined = int(np.isnan(values).sum())
        upper = None if undefined else float(np.quantile(values, 1 - ALPHA, method="inverted_cdf"))
        positive = bool(np.all(totals > 0) and np.all(total_cost > 0) and math.isfinite(observed) and observed > 0)
        exact = modes[metric] == "exact"
        item = {"available": upper is not None, "mode": modes[metric],
                "actual_ratio": core._public_number(observed) if exact else None,
                "observed_ratio_upper_bound": core._public_number(observed) if not exact else None,
                "upper_one_sided_98_75": upper,
                "gain": 1 - observed if exact else None,
                "gain_lower_confidence_bound": 1 - upper if upper is not None else None,
                "undefined_draws": undefined, "positive_infinite_draws": int(np.isposinf(values).sum()),
                "cost_total": {arm: float(total_cost[i]) for i, arm in enumerate(core.ARMS)},
                "cost_total_is_lower_bound": {"baseline": not exact, "with-skill": False},
                "batch_criterion_met": bool(n >= 20 and quality and positive and upper is not None and upper < .90)}
        if exact and not undefined:
            bounds = np.quantile(values, [ALPHA, 1 - ALPHA], method="inverted_cdf")
            item["ratio_interval97_5"] = bounds.tolist()
            item["gain_interval97_5"] = [1 - float(bounds[1]), 1 - float(bounds[0])]
        result["metrics"][metric] = item
    return core._json_safe(result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--phase", choices=("formal", "replication"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    assert args.output.resolve() != args.records.resolve()
    rows = [json.loads(line) for line in args.records.read_text(encoding="utf-8").splitlines() if line.strip()]
    result = assess(rows, args.phase)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    print(json.dumps({"phase": args.phase, "quality": result["quality"], "metrics": result["metrics"]}))
