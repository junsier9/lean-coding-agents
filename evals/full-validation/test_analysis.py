"""Synthetic regression checks for analyze.py; no experiment files are read.

Run with the bundled Python (NumPy is the only non-stdlib dependency):
    python -B test_analysis.py

Fixtures specify known outcomes or exact small-cluster distributions. They do
not reimplement the estimator, jackknife formula, or bootstrap algorithm.
"""

from __future__ import annotations

import copy
import importlib.util
import math
from pathlib import Path
import sys
import unittest

# Import only the adjacent analysis module. In particular, never discover or
# load records.jsonl, protocol inputs, or any model-produced artifact.
sys.dont_write_bytecode = True
_ANALYSIS_PATH = Path(__file__).with_name("analyze.py")
_SPEC = importlib.util.spec_from_file_location("_synthetic_analysis_under_test", _ANALYSIS_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Cannot import {_ANALYSIS_PATH}")
analysis = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(analysis)

ARMS = ("baseline", "with-skill")
DRAWS = 2000


def records(
    task_costs: list[tuple[float, float]],
    *,
    phase: str = "formal",
) -> list[dict]:
    """Create exactly two arms and three repeats per synthetic task."""
    result = []
    for task, (baseline, skill) in enumerate(task_costs):
        for repeat in range(3):
            for arm, cost in zip(ARMS, (baseline, skill)):
                result.append({
                    "phase": phase,
                    "case_id": f"synthetic-{task:03}",
                    "repo": "synthetic/no-external-data",
                    "repeat": repeat,
                    "arm": arm,
                    "total_tokens": cost,
                    "wall_seconds": cost / 10,
                    "accepted": True,
                    "usage_complete": True,
                    "completed": True,
                })
    return result


def homogeneous(
    *,
    tasks: int = 20,
    skill_cost: float = 80,
    phase: str = "formal",
) -> list[dict]:
    return records([(100, skill_cost)] * tasks, phase=phase)


class AnalysisInvariants(unittest.TestCase):
    def analyze(self, rows: list[dict], phase: str = "formal") -> dict:
        return analysis.analyze_records(rows, phase, bootstrap_draws=DRAWS)

    def test_known_ratios_and_strict_material_boundary(self):
        # Heterogeneous paired tasks all have the same skill/baseline ratio.
        # Independent resampling of arms would spuriously widen this interval.
        for ratio, should_pass in ((0.8, True), (1.0, False), (0.9, False)):
            with self.subTest(ratio=ratio):
                rows = records([
                    (100 * (i + 1) ** 2, 100 * ratio * (i + 1) ** 2)
                    for i in range(20)
                ])
                result = self.analyze(rows)
                self.assertTrue(result["quality"]["observed_gate_pass"])
                for metric in result["metrics"].values():
                    self.assertAlmostEqual(metric["ratio"], ratio, places=12)
                    self.assertAlmostEqual(metric["gain"], 1 - ratio, places=12)
                    for endpoint in metric["ratio_ci95"]["interval"]:
                        self.assertAlmostEqual(endpoint, ratio, places=12)
                    self.assertEqual(metric["batch_criterion_met"], should_pass)

        # Exact integer costs make the strict 0.90 boundary unambiguous.
        boundary = self.analyze(homogeneous(skill_cost=90))
        self.assertEqual(boundary["metrics"]["token"]["upper_one_sided_97_5"], 0.9)
        self.assertFalse(boundary["metrics"]["token"]["bound_below_0_90"])

    def test_failed_and_unfinished_attempt_costs_are_retained(self):
        rows = homogeneous()
        for row in rows:
            failed = row["repeat"] == 2
            row["accepted"] = not failed
            row["completed"] = not failed
            if row["arm"] == "baseline":
                cost = 1000 if failed else 100
            else:
                cost = 2000 if failed else 80
            row["total_tokens"] = cost
            row["wall_seconds"] = cost / 10
        result = self.analyze(rows)
        metric = result["metrics"]["token"]
        # Each of 20 tasks has two successes and one costly failed attempt.
        self.assertEqual(metric["cost_total"]["baseline"], 24000)
        self.assertEqual(metric["cost_total"]["with-skill"], 43200)
        self.assertEqual(metric["accepted_total"], {"baseline": 40, "with-skill": 40})
        self.assertEqual(metric["cost_per_accepted"]["baseline"], 600)
        self.assertEqual(metric["cost_per_accepted"]["with-skill"], 1080)
        self.assertAlmostEqual(metric["ratio"], 1.8)
        self.assertFalse(metric["batch_criterion_met"])
        time_metric = result["metrics"]["time"]
        self.assertEqual(
            time_metric["cost_total"], {"baseline": 2400, "with-skill": 4320}
        )
        self.assertEqual(
            time_metric["cost_per_accepted"], {"baseline": 60, "with-skill": 108}
        )
        self.assertAlmostEqual(time_metric["ratio"], 1.8)
        self.assertEqual(result["observed_totals"]["baseline"]["unfinished"], 20)

    def test_missing_or_partial_usage_cannot_prove_token_savings(self):
        for token_value, usage_flag in ((None, False), (17, False), (None, True)):
            with self.subTest(tokens=token_value, usage_complete=usage_flag):
                rows = homogeneous()
                rows[0]["total_tokens"] = token_value
                rows[0]["usage_complete"] = usage_flag
                result = self.analyze(rows)
                self.assertTrue(result["integrity"]["structure_ok"])
                self.assertFalse(result["metrics"]["token"]["available"])
                self.assertIsNone(result["metrics"]["token"]["ratio"])
                self.assertFalse(result["metrics"]["token"]["batch_criterion_met"])
                self.assertTrue(result["metrics"]["time"]["batch_criterion_met"])

        rows = homogeneous()
        for row in rows:
            row["total_tokens"] = None
            row["usage_complete"] = False
        result = self.analyze(rows)
        for arm in ARMS:
            self.assertIsNone(result["observed_totals"][arm]["reported_token_total"])
        self.assertTrue(result["metrics"]["time"]["batch_criterion_met"])

    def test_resampling_unit_is_a_whole_task_not_three_repeats(self):
        # Two task clusters have ratios .5 and 1.5. Resampling two whole
        # clusters has exactly three possible ratios: .5, 1, 1.5.
        # The endpoint outcomes each have probability 1/4, so the 95% interval
        # is [.5, 1.5]. Treating six repeats as independent narrows it.
        result = self.analyze(records([(100, 50), (100, 150)]))
        for metric in result["metrics"].values():
            self.assertEqual(metric["ratio"], 1)
            self.assertEqual(metric["ratio_ci95"]["interval"], [0.5, 1.5])
            self.assertEqual(metric["gain_ci95"], [-0.5, 0.5])
        self.assertEqual(result["task_count"], 2)

    def test_duplicate_or_missing_run_blocks_the_batch(self):
        full = homogeneous()
        duplicate = copy.deepcopy(full)
        duplicate.append(copy.deepcopy(full[0]))
        missing = full[:-1]
        for name, rows in (("duplicate", duplicate), ("missing", missing)):
            with self.subTest(kind=name):
                result = self.analyze(rows)
                self.assertFalse(result["integrity"]["structure_ok"])
                self.assertEqual(result["task_count"], 20)
                for metric in result["metrics"].values():
                    self.assertFalse(metric["available"])
                    self.assertFalse(metric["batch_criterion_met"])
        self.assertEqual(
            len(self.analyze(duplicate)["integrity"]["duplicate_run_keys"]), 1
        )
        self.assertEqual(
            self.analyze(missing)["integrity"]["complete_task_count"], 19
        )

    def test_zero_success_boundaries_are_not_savings_proof(self):
        for zero_arm in ("baseline", "with-skill", "both"):
            with self.subTest(zero_arm=zero_arm):
                rows = homogeneous()
                for row in rows:
                    if zero_arm == "both" or row["arm"] == zero_arm:
                        row["accepted"] = False
                result = self.analyze(rows)
                metric = result["metrics"]["token"]
                self.assertEqual(metric["cost_total"]["baseline"], 6000)
                self.assertFalse(metric["batch_criterion_met"])
                if zero_arm == "with-skill":
                    self.assertTrue(math.isinf(metric["ratio"]))
                    self.assertGreater(metric["ratio"], 0)
                elif zero_arm == "baseline":
                    self.assertEqual(metric["ratio"], 0)
                    self.assertFalse(metric["finite_estimand_observed"])
                else:
                    self.assertIsNone(metric["ratio"])
                    self.assertEqual(metric["ratio_ci95"]["undefined_draws"], DRAWS)
                    self.assertFalse(metric["ratio_ci95"]["available"])

    def test_undefined_bootstrap_draws_are_not_dropped(self):
        rows = records([(100, 80), (100, 80)])
        for row in rows:
            if row["case_id"] == "synthetic-001":
                row["accepted"] = False
        result = self.analyze(rows)
        metric = result["metrics"]["token"]
        self.assertAlmostEqual(metric["ratio"], 0.8)
        ci = metric["ratio_ci95"]
        self.assertGreater(ci["undefined_draws"], 0)
        self.assertGreater(ci["finite_draws"], 0)
        self.assertEqual(ci["undefined_draws"] + ci["finite_draws"], DRAWS)
        self.assertFalse(ci["available"])
        self.assertIsNone(ci["interval"])

    def test_quality_gate_and_task_regressions_remain_visible(self):
        rows = homogeneous()
        for row in rows:
            if row["case_id"] == "synthetic-000" and row["arm"] == "with-skill":
                row["accepted"] = row["repeat"] != 2
        result = self.analyze(rows)
        self.assertFalse(result["quality"]["observed_gate_pass"])
        self.assertTrue(result["metrics"]["token"]["bound_below_0_90"])
        self.assertFalse(result["metrics"]["token"]["batch_criterion_met"])

        # Another task improves by one run: aggregate quality ties, but the
        # first task's regression must remain reported.
        for row in rows:
            if row["case_id"] == "synthetic-001" and row["arm"] == "baseline":
                row["accepted"] = row["repeat"] != 2
        tied = self.analyze(rows)
        self.assertTrue(tied["quality"]["observed_gate_pass"])
        self.assertEqual(tied["quality"]["degraded_task_count"], 1)
        self.assertEqual(
            tied["quality"]["degraded_tasks"][0]["case_id"], "synthetic-000"
        )
        self.assertEqual(
            tied["quality"]["success_rate_difference_skill_minus_baseline"], 0
        )

    def test_planning_floor_uses_task_totals_not_repeat_variation(self):
        rows = homogeneous(tasks=10, phase="pilot")
        # Every task has total cost 300 in each arm, despite huge variation
        # within the skill arm's three repeats. Task-level variance is zero.
        for row in rows:
            if row["arm"] == "with-skill":
                row["total_tokens"] = (298, 1, 1)[row["repeat"]]
                row["wall_seconds"] = row["total_tokens"] / 10
        plan = analysis.sample_size(rows)
        self.assertTrue(plan["planning_available"])
        for metric in plan["metrics"].values():
            self.assertAlmostEqual(metric["jackknife_pseudovalue_sd"], 0, places=12)
            self.assertEqual(metric["planning_sd"], 0.05)
            self.assertEqual(metric["required_n"], 2)
        self.assertEqual(plan["target_tasks_per_confirmatory_batch"], 20)
        self.assertFalse(plan["underpowered"])
        self.assertEqual(plan["independent_tasks_required_total"], 50)
        self.assertEqual(plan["additional_eligible_tasks_needed_before_formal"], 0)

    def test_planning_uses_worse_metric_caps_and_marks_underpowered(self):
        rows = homogeneous(tasks=10, phase="pilot")
        for row in rows:
            if row["arm"] == "with-skill":
                row["wall_seconds"] = (
                    10000 if row["case_id"] == "synthetic-009" else 1
                )
        plan = analysis.sample_size(rows)
        self.assertTrue(plan["planning_available"])
        self.assertEqual(plan["metrics"]["token"]["required_n"], 2)
        self.assertGreater(plan["metrics"]["time"]["required_n"], 80)
        self.assertEqual(
            plan["required_n_before_cap"], plan["metrics"]["time"]["required_n"]
        )
        self.assertEqual(plan["target_tasks_per_confirmatory_batch"], 80)
        self.assertTrue(plan["underpowered"])
        self.assertFalse(plan["adequacy_claim_permitted"])
        self.assertEqual(plan["additional_eligible_tasks_needed_before_formal"], 109)

    def test_unestimable_pilot_does_not_fall_back_to_minimum_size(self):
        rows = homogeneous(tasks=10, phase="pilot")
        # Removing the only successful task leaves zero accepted runs.
        for row in rows:
            row["accepted"] = row["case_id"] == "synthetic-000"
        plan = analysis.sample_size(rows)
        self.assertFalse(plan["planning_available"])
        self.assertIsNone(plan["target_tasks_per_confirmatory_batch"])

        missing = homogeneous(tasks=10, phase="pilot")
        missing[0]["total_tokens"] = None
        missing[0]["usage_complete"] = False
        plan = analysis.sample_size(missing)
        self.assertFalse(plan["planning_available"])
        self.assertIsNone(plan["target_tasks_per_confirmatory_batch"])

    def test_nominal_power_is_per_batch_and_pilot_is_not_confirmation(self):
        plan = analysis.sample_size(homogeneous(tasks=10, phase="pilot"))
        self.assertEqual(plan["nominal_power"], 0.8)
        self.assertEqual(plan["power_scope"], "per_confirmatory_batch")
        self.assertAlmostEqual(
            plan["two_batch_joint_power_if_independent_and_each_exactly_0_80"], 0.64
        )
        # Use 20 tasks to ensure this checks the phase gate, not just the
        # minimum confirmatory sample count.
        exploratory = self.analyze(homogeneous(phase="pilot"), phase="pilot")
        self.assertTrue(exploratory["metrics"]["token"]["bound_below_0_90"])
        self.assertFalse(exploratory["metrics"]["token"]["batch_criterion_met"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

