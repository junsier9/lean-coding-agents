import math
import unittest
from assess import assess
from benefit_gate import credible_benefit


def data(ratio=.7):
    return [{"key": f"formal--c{i}--r{r}--{arm}", "phase": "formal", "case_id": f"c{i}",
             "repo": "fixture", "repeat": r, "arm": arm, "accepted": True, "completed": True,
             "wall_seconds": 100 * (ratio if arm == "with-skill" else 1),
             "total_tokens": 1000 * (ratio if arm == "with-skill" else 1), "usage_complete": True}
            for i in range(20) for r in range(3) for arm in ("baseline", "with-skill")]


class Assessment(unittest.TestCase):
    def test_uniform_reduction_passes_and_all_failed_costs_are_retained(self):
        rows = data()
        for row in rows[:2]:
            row["accepted"] = False
        result = assess(rows, "formal", 1000)
        self.assertTrue(result["metrics"]["token"]["batch_criterion_met"])
        self.assertEqual(result["quality"]["baseline_accepted"], 59)
        self.assertEqual(result["metrics"]["token"]["cost_total"]["baseline"], 60000)

    def test_under_ten_percent_does_not_pass(self):
        self.assertFalse(assess(data(.95), "formal", 1000)["metrics"]["token"]["batch_criterion_met"])

    def test_stricter_alpha_does_not_reuse_the_old_97_5_bound(self):
        rows = data(.6)
        for row in rows:
            if row["case_id"] == "c19" and row["arm"] == "with-skill":
                row.update(total_tokens=2500, wall_seconds=250)
        metric = assess(rows, "formal")["metrics"]["token"]
        self.assertGreater(metric["upper_one_sided_98_75"], .9)
        self.assertFalse(metric["batch_criterion_met"])

    def test_baseline_missing_is_only_a_bound_and_record_is_unchanged(self):
        rows = data()
        rows[0].update(usage_complete=False, total_tokens=None)
        result = assess(rows, "formal", 1000)["metrics"]["token"]
        self.assertIsNone(result["actual_ratio"])
        self.assertEqual(result["mode"], "baseline_cost_lower_bound_only")
        self.assertIsNone(rows[0]["total_tokens"])

    def test_skill_missing_cannot_prove_token_saving(self):
        rows = data()
        rows[1].update(usage_complete=False, total_tokens=None)
        self.assertFalse(assess(rows, "formal", 1000)["metrics"]["token"]["batch_criterion_met"])

    def test_task_regression_blocks_even_if_overall_rate_ties(self):
        rows = data()
        rows[1]["accepted"] = False
        rows[6]["accepted"] = False
        result = assess(rows, "formal", 1000)
        self.assertFalse(result["quality"]["gate_pass"])
        self.assertFalse(result["metrics"]["token"]["batch_criterion_met"])

    def test_zero_success_and_undefined_draws_never_pass(self):
        rows = data()
        for row in rows:
            row["accepted"] = False
        self.assertFalse(assess(rows, "formal", 1000)["metrics"]["token"]["batch_criterion_met"])

    def test_duplicate_key_is_not_silently_averaged(self):
        rows = data()
        rows.append(dict(rows[0]))
        result = assess(rows, "formal", 1000)
        self.assertFalse(result["integrity"]["structure_ok"])

    def test_positive_gain_is_separate_from_the_ten_percent_claim(self):
        small = assess(data(.95), "formal", 1000)
        self.assertTrue(credible_benefit(small, "token"))
        self.assertFalse(small["metrics"]["token"]["batch_criterion_met"])
        self.assertFalse(credible_benefit(assess(data(1), "formal", 1000), "token"))

    def test_zero_success_or_unusable_bound_cannot_be_a_positive_gain(self):
        for arm in ("baseline", "with-skill"):
            rows = data()
            for row in rows:
                if row["arm"] == arm:
                    row["accepted"] = False
            self.assertFalse(credible_benefit(assess(rows, "formal", 1000), "token"))
        value = assess(data(.95), "formal", 1000)
        for upper in (None, math.inf, math.nan, 0, "Infinity", "-Infinity"):
            value["metrics"]["token"]["upper_one_sided_98_75"] = upper
            self.assertFalse(credible_benefit(value, "token"))

    def test_serialized_infinite_baseline_only_bound_is_valid_but_no_gain(self):
        rows = data()
        for row in rows:
            if row["arm"] == "baseline" and row["case_id"] != "c19":
                row.update(usage_complete=False, total_tokens=None)
        result = assess(rows, "formal", 1000)
        self.assertEqual(result["metrics"]["token"]["upper_one_sided_98_75"], "Infinity")
        self.assertFalse(credible_benefit(result, "token"))

    def test_conservative_bound_can_pass_without_inventing_actual_usage(self):
        rows = data()
        rows[0].update(usage_complete=False, total_tokens=None)
        result = assess(rows, "formal", 1000)
        self.assertTrue(credible_benefit(result, "token"))
        self.assertIsNone(result["metrics"]["token"]["actual_ratio"])
        rows[1].update(usage_complete=False, total_tokens=None)
        self.assertFalse(credible_benefit(assess(rows, "formal", 1000), "token"))


if __name__ == "__main__":
    unittest.main()
