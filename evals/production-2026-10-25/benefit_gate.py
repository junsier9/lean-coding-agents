"""Apply the pre-frozen positive-gain threshold to an assessment, including bounds."""
from assess import core


def credible_benefit(batch, metric):
    quality = batch["quality"]
    if not quality["gate_pass"] or batch["task_count"] != 20:
        return False
    if min(quality["baseline_accepted"], quality["skill_accepted"]) <= 0:
        return False
    value = batch["metrics"][metric]
    if not value["available"]:
        return False
    upper = value["upper_one_sided_98_75"]
    observed = (value["actual_ratio"] if value["mode"] == "exact"
                else value["observed_ratio_upper_bound"])
    if not core._number(upper) or not core._number(observed):
        return False
    return bool(0 < upper < 1 and observed > 0
                and all(core._number(cost) and cost > 0 for cost in value["cost_total"].values()))
