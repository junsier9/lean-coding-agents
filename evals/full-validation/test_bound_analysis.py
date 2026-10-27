"""Pure synthetic invariants for the conservative token-bound auxiliary API.

Run: python -B test_bound_analysis.py
No experiment records, model outputs, CLI processes, or network are accessed.
"""

from __future__ import annotations

import copy
import importlib.util
import math
from pathlib import Path
import sys
import unittest
from unittest import mock

import numpy as np

sys.dont_write_bytecode = True
_spec = importlib.util.spec_from_file_location(
    "_synthetic_bound_analysis", Path(__file__).with_name("bound_analysis.py")
)
if _spec is None or _spec.loader is None:
    raise ImportError("Cannot import bound_analysis.py")
bound_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bound_module)
analyze_token_bound = bound_module.analyze_token_bound
original_analysis = bound_module._analysis
DRAWS = 2000


def fixture(tasks=20, phase="formal"):
    """Each task has baseline known cost200, skill exact cost120, successes3."""
    result = []
    for task in range(tasks):
        scale = (task + 1) ** 2
        for repeat in range(3):
            for arm in ("baseline", "with-skill"):
                unknown = arm == "baseline" and repeat == 2
                cost = (100 if arm == "baseline" else 40) * scale
                result.append({
                    "phase": phase,
                    "case_id": f"synthetic-{task:03}",
                    "repo": "synthetic/bound-only",
                    "repeat": repeat,
                    "arm": arm,
                    "total_tokens": None if unknown else cost,
                    "wall_seconds": 1,
                    "usage_complete": not unknown,
                    "accepted": True,
                    "completed": True,
                })
    return result


def complete(rows, hidden_cost):
    result = copy.deepcopy(rows)
    for row in result:
        if row["arm"] == "baseline" and row["total_tokens"] is None:
            task = int(row["case_id"].split("-")[1])
            row["total_tokens"] = hidden_cost(task)
            row["usage_complete"] = True
    return result


class TokenBoundTests(unittest.TestCase):
    def analyze(self, rows, phase="formal"):
        return analyze_token_bound(rows, phase, bootstrap_draws=DRAWS)

    def test_known_bound_keeps_none_and_does_not_report_actual_ci(self):
        rows = fixture()
        before = copy.deepcopy(rows)
        result = self.analyze(rows)
        self.assertEqual(rows, before)
        self.assertEqual(result["status"], "bound_available")
        self.assertTrue(result["integrity"]["bound_input_ok"])
        self.assertFalse(result["integrity"]["original_exact_analysis"]["ok"])
        self.assertEqual(result["missing_counts"], {"baseline": 20, "with-skill": 0})
        # Sum of squares1..20 is2870; each task has2 known baseline repeats
        # costing100*scale and3 exact skill repeats costing40*scale.
        self.assertEqual(result["baseline_cost_lower_bound"], 574000)
        self.assertEqual(result["skill_exact_cost"], 344400)
        self.assertAlmostEqual(result["ratio_upper_bound"], 0.6, places=12)
        self.assertAlmostEqual(result["upper_one_sided_97_5"], 0.6, places=12)
        self.assertAlmostEqual(result["gain_lower_confidence_bound"], 0.4, places=12)
        self.assertTrue(result["batch_criterion_met"])
        self.assertIsNone(result["actual_ratio"])
        self.assertNotIn("ratio_ci95", result)
        self.assertNotIn("actual_ci95", result)
        self.assertNotIn("interval", result["bootstrap"])

    def test_complete_data_same_draw_upper_bound_never_exceeds_auxiliary_bound(self):
        rows = fixture()
        # Heterogeneous task and repeat costs plus differing success counts
        # make the per-draw ratio vary. Independent-arm/run resampling cannot
        # hide behind a constant-ratio fixture in this comparison.
        for row in rows:
            task = int(row["case_id"].split("-")[1])
            if row["arm"] == "with-skill":
                row["total_tokens"] = (25 + task % 7 * 5) * (task + 1) ** 2 * (row["repeat"] + 1)
                row["accepted"] = row["repeat"] < 1 + (task + 1) % 3
            else:
                row["accepted"] = row["repeat"] < 1 + task % 3
        interval_samples = []
        original_interval = original_analysis._interval

        def capture_interval(values):
            interval_samples.append(values.copy())
            return original_interval(values)

        with mock.patch.object(original_analysis, "_interval", side_effect=capture_interval):
            envelope = self.analyze(rows)
        bound_draws = interval_samples[0]
        self.assertGreater(float(np.std(bound_draws)), 0)
        completions = (
            lambda task: 0,
            lambda task: 10,
            lambda task: (task + 1) * 53,
            lambda task: 1000000 if task == 19 else 0,
        )
        for index, hidden_cost in enumerate(completions):
            with self.subTest(completion=index):
                interval_samples.clear()
                with mock.patch.object(original_analysis, "_interval", side_effect=capture_interval):
                    full = original_analysis.analyze_records(
                        complete(rows, hidden_cost), "formal", bootstrap_draws=DRAWS
                    )
                # Original analysis supplies quality, token, then time samples
                # to its percentile function. These are actual API draws,
                # not an independently mirrored calculation of the formula.
                token_draws = interval_samples[1]
                self.assertTrue(np.all(token_draws <= bound_draws + 1e-12))
                if index == 0:
                    np.testing.assert_allclose(token_draws, bound_draws, rtol=0, atol=1e-12)
                self.assertLessEqual(
                    full["metrics"]["token"]["upper_one_sided_97_5"],
                    envelope["upper_one_sided_97_5"] + 1e-12,
                )
                self.assertEqual(full["bootstrap"]["seed"], envelope["bootstrap"]["seed"])
                self.assertEqual(
                    full["bootstrap"]["task_ids_in_sampling_order"],
                    envelope["bootstrap"]["task_ids_in_sampling_order"],
                )

    def test_missing_skill_or_other_invalid_input_blocks(self):
        mutations = (
            ("skill_unknown", lambda row: row.update(
                arm="with-skill", usage_complete=False, total_tokens=None
            )),
            ("partial_numeric_usage", lambda row: row.update(
                usage_complete=False, total_tokens=17
            )),
            ("null_with_complete_flag", lambda row: row.update(
                usage_complete=True, total_tokens=None
            )),
            ("negative_cost", lambda row: row.update(
                usage_complete=True, total_tokens=-1
            )),
            ("boolean_cost", lambda row: row.update(
                usage_complete=True, total_tokens=True
            )),
            ("nonfinite_cost", lambda row: row.update(
                usage_complete=True, total_tokens=float("nan")
            )),
            ("missing_cost_field", lambda row: (
                row.update(usage_complete=False), row.pop("total_tokens")
            )),
            ("invalid_time", lambda row: row.update(wall_seconds=None)),
            ("unhashable_arm", lambda row: row.update(
                arm=[], usage_complete=False, total_tokens=None
            )),
        )
        for name, mutate in mutations:
            with self.subTest(kind=name):
                rows = fixture()
                if name == "skill_unknown":
                    target = next(row for row in rows if row["arm"] == "with-skill")
                else:
                    target = rows[0]
                mutate(target)
                before = copy.deepcopy(rows)
                result = self.analyze(rows)
                self.assertEqual(result["status"], "blocked")
                self.assertFalse(result["integrity"]["bound_input_ok"])
                self.assertFalse(result["batch_criterion_met"])
                self.assertIsNone(result["upper_one_sided_97_5"])
                # NaN is not equal to itself; compare the exact input objects
                # and unchanged identity for this deliberately malformed value.
                if name == "nonfinite_cost":
                    self.assertTrue(math.isnan(rows[0]["total_tokens"]))
                    self.assertIs(rows[0]["total_tokens"], before[0]["total_tokens"])
                else:
                    self.assertEqual(rows, before)

    def test_zero_baseline_lower_bound_stays_infinite(self):
        rows = fixture()
        for row in rows:
            if row["arm"] == "baseline":
                row["total_tokens"] = None
                row["usage_complete"] = False
        result = self.analyze(rows)
        self.assertEqual(result["baseline_cost_lower_bound"], 0)
        self.assertTrue(math.isinf(result["ratio_upper_bound"]))
        self.assertEqual(result["upper_one_sided_97_5"], math.inf)
        self.assertEqual(result["gain_lower_confidence_bound"], -math.inf)
        self.assertEqual(result["bootstrap"]["undefined_draws"], 0)
        self.assertFalse(result["batch_criterion_met"])

    def test_zero_over_zero_draw_cannot_be_changed_to_infinity(self):
        rows = fixture()
        # Sixteen zero-cost tasks make all-zero bootstrap samples uncommon
        # (<2.5%) but observable. Replacing their NaN with inf would allow a
        # finite97.5% bound and falsely bypass the original undefined rule.
        for row in rows:
            task = int(row["case_id"].split("-")[1])
            if task < 16 and row["total_tokens"] is not None:
                row["total_tokens"] = 0
        result = self.analyze(rows)
        self.assertTrue(result["quality"]["observed_gate_pass"])
        self.assertTrue(result["minimum_confirmatory_task_count_satisfied"])
        self.assertAlmostEqual(result["ratio_upper_bound"], 0.6, places=12)
        self.assertGreater(result["bootstrap"]["undefined_draws"], 0)
        self.assertLess(result["bootstrap"]["undefined_draws"], DRAWS * 0.025)
        self.assertEqual(result["status"], "undefined")
        self.assertIsNone(result["upper_one_sided_97_5"])
        self.assertFalse(result["batch_criterion_met"])
        full = original_analysis.analyze_records(
            complete(rows, lambda task: 0), "formal", bootstrap_draws=DRAWS
        )
        self.assertEqual(
            full["metrics"]["token"]["ratio_ci95"]["undefined_draws"],
            result["bootstrap"]["undefined_draws"],
        )

    def test_zero_success_boundaries_never_become_proof(self):
        for zero_arm in ("baseline", "with-skill", "both"):
            with self.subTest(zero_arm=zero_arm):
                rows = fixture()
                for row in rows:
                    if zero_arm == "both" or row["arm"] == zero_arm:
                        row["accepted"] = False
                result = self.analyze(rows)
                self.assertFalse(result["batch_criterion_met"])
                if zero_arm == "baseline":
                    self.assertEqual(result["ratio_upper_bound"], 0)
                    self.assertFalse(result["finite_positive_comparison_bound"])
                elif zero_arm == "with-skill":
                    self.assertEqual(result["ratio_upper_bound"], math.inf)
                else:
                    self.assertIsNone(result["ratio_upper_bound"])
                    self.assertEqual(result["status"], "undefined")

    def test_small_batch_and_pilot_are_not_confirmatory(self):
        for tasks, phase in ((19, "formal"), (20, "pilot")):
            with self.subTest(tasks=tasks, phase=phase):
                result = self.analyze(fixture(tasks, phase), phase)
                self.assertLess(result["upper_one_sided_97_5"], 0.9)
                self.assertFalse(result["batch_criterion_met"])
                self.assertFalse(result["planning_supported"])
                self.assertTrue(result["not_for_pilot_planning"])
        replication = self.analyze(fixture(20, "replication"), "replication")
        self.assertTrue(replication["batch_criterion_met"])
        self.assertTrue(replication["requires_other_disjoint_confirmatory_batch"])

    def test_quality_regression_blocks_a_favorable_bound(self):
        rows = fixture()
        for row in rows:
            if row["case_id"] == "synthetic-000" and row["arm"] == "with-skill":
                row["accepted"] = False
        result = self.analyze(rows)
        self.assertLess(result["upper_one_sided_97_5"], 0.9)
        self.assertFalse(result["quality"]["observed_gate_pass"])
        self.assertEqual(result["quality"]["degraded_task_count"], 1)
        self.assertFalse(result["batch_criterion_met"])

    def test_duplicate_or_missing_run_is_not_silently_removed(self):
        rows = fixture()
        duplicate = rows + [copy.deepcopy(rows[0])]
        for kind, malformed in (("duplicate", duplicate), ("missing", rows[:-1])):
            with self.subTest(kind=kind):
                result = self.analyze(malformed)
                self.assertEqual(result["status"], "blocked")
                self.assertFalse(result["integrity"]["bound_input_ok"])
                self.assertEqual(result["task_count"], 20)
                self.assertIsNone(result["ratio_upper_bound"])
                self.assertFalse(result["batch_criterion_met"])

    def test_complete_usage_is_not_needed_and_delegates_actual_analysis(self):
        rows = complete(fixture(), lambda task: 100 * (task + 1) ** 2)
        result = self.analyze(rows)
        self.assertEqual(result["status"], "not_needed")
        self.assertFalse(result["applicable"])
        self.assertIsNone(result["actual_ratio"])
        self.assertIsNone(result["ratio_upper_bound"])
        self.assertIsNone(result["upper_one_sided_97_5"])
        self.assertFalse(result["bootstrap"]["computed"])
        self.assertFalse(result["batch_criterion_met"])

    def test_failed_skill_costs_remain_in_the_upper_bound(self):
        rows = fixture()
        for row in rows:
            row["accepted"] = row["repeat"] < 2
            row["completed"] = row["accepted"]
            if row["arm"] == "baseline" and row["repeat"] < 2:
                row["total_tokens"] = 100
            elif row["arm"] == "with-skill":
                row["total_tokens"] = 1000 if row["repeat"] == 2 else 10
        result = self.analyze(rows)
        self.assertEqual(result["baseline_cost_lower_bound"], 4000)
        self.assertEqual(result["skill_exact_cost"], 20400)
        self.assertEqual(result["accepted_total"], {"baseline": 40, "with-skill": 40})
        self.assertAlmostEqual(result["ratio_upper_bound"], 5.1)
        self.assertFalse(result["batch_criterion_met"])

    def test_method_does_not_overstate_coverage_or_power(self):
        result = self.analyze(fixture())
        limits = result["limitations"]
        self.assertEqual(
            limits["coverage_guarantee"],
            "monotonic_conservatism_relative_to_same_complete_data_method_only",
        )
        self.assertEqual(limits["population_percentile_bootstrap_coverage"], "approximate")
        self.assertFalse(limits["mar_assumption_required"])
        self.assertEqual(limits["joint_power_if_independent_and_each_at_0_80"], 0.64)
        self.assertEqual(result["one_sided_alpha"], 0.025)
        self.assertEqual(result["threshold_ratio"], 0.90)


if __name__ == "__main__":
    unittest.main(verbosity=2)

