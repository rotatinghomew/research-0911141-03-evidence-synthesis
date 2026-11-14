from copy import deepcopy
from pathlib import Path
import json
import unittest

from evidence_synthesis.domain import explore, validate
from evidence_synthesis.synthesis import synthesize, t_critical, chi2_sf, pool
from evidence_synthesis.service import Workbench
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]


class ReferenceShape(unittest.TestCase):
    def test_fixture_groups_only_share_scales(self):
        data = json.loads((ROOT / "fixtures" / "data.json").read_text())
        view = synthesize(data, {"outcome": "completion_score"})
        scales = {group["scale_id"] for group in view["groups"]}
        self.assertEqual(scales, {"v1", "v2"})
        for group in view["groups"]:
            for contribution in group["contributions"]:
                self.assertEqual(contribution["scale_id"], group["scale_id"])

    def test_summary_is_json_safe(self):
        data = json.loads((ROOT / "fixtures" / "data.json").read_text())
        for selection in [{"outcome": "completion_score"},
                          {"outcome": "error_count"},
                          {"outcome": "completion_score", "time_policy": "all_weeks"}]:
            json.dumps(synthesize(data, selection), allow_nan=False)

    def test_statistical_helpers_match_reference_values(self):
        self.assertAlmostEqual(t_critical(3), 3.182446, places=4)
        self.assertAlmostEqual(t_critical(10), 2.228139, places=4)
        self.assertAlmostEqual(chi2_sf(3.841459, 1), 0.05, places=6)
        self.assertAlmostEqual(chi2_sf(5.991465, 2), 0.05, places=6)

    def test_pool_matches_hand_computation(self):
        records = [{"effect": 1.0, "variance": 1.0}, {"effect": 3.0, "variance": 1.0}]
        result = pool(records, "fixed")
        self.assertAlmostEqual(result["fixed"]["estimate"], 2.0)
        self.assertAlmostEqual(result["fixed"]["se"], 2 ** 0.5 / 2.0)
        self.assertAlmostEqual(sum(result["weights"]), 100.0)

    def test_tau2_is_zero_for_identical_effects(self):
        records = [{"effect": 1.0, "variance": 1.0}, {"effect": 1.0, "variance": 1.0}]
        result = pool(records, "random")
        self.assertEqual(result["heterogeneity"]["tau2"], 0.0)
        self.assertEqual(result["heterogeneity"]["i2_percent"], 0.0)


class PoolingConditions(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((ROOT / "fixtures" / "data.json").read_text())

    def group(self, view, scale):
        return next(group for group in view["groups"] if group["scale_id"] == scale)

    def test_missing_dispersion_never_enters_pooling(self):
        data = deepcopy(self.data)
        for record in data["means"]:
            if record["arm_id"].startswith("study_1_") and record["outcome"] == "completion_score":
                record["sd"] = None
        view = synthesize(data, {"outcome": "completion_score", "contrast_rule": "last"})
        group = self.group(view, "v1")
        included = {item["study_id"] for item in group["contributions"]}
        self.assertNotIn("study_1", included)
        excluded = {item["study_id"]: item["status"] for item in group["excluded"]}
        self.assertEqual(excluded.get("study_1"), "no_dispersion")

    def test_unreported_is_not_zero_and_is_not_pooled(self):
        data = deepcopy(self.data)
        for record in data["means"]:
            if record["arm_id"].startswith("study_4_") and record["outcome"] == "error_count":
                if record["week"] == 12:
                    record["reported"] = False
                    record["mean"] = None
                    record["sd"] = None
        view = synthesize(data, {"outcome": "error_count", "contrast_rule": "last"})
        parallel = [row for row in view["parallel"]
                    if row["study_id"] == "study_4" and row["week"] == 12]
        self.assertEqual(parallel[0]["status"], "not_reported")
        self.assertIsNone(parallel[0]["raw_difference"])
        group = view["groups"][0]
        self.assertNotIn("study_4", {item["study_id"] for item in group["contributions"]})

    def test_missing_record_is_distinct_from_unreported(self):
        data = deepcopy(self.data)
        data["means"] = [record for record in data["means"]
                         if not (record["arm_id"] == "study_6_arm_1"
                                 and record["outcome"] == "completion_score")]
        view = synthesize(data, {"outcome": "completion_score", "contrast_rule": "last"})
        statuses = {row["status"] for row in view["parallel"] if row["study_id"] == "study_6"}
        self.assertIn("missing_record", statuses)
        self.assertNotIn("not_reported", statuses)

    def test_scales_are_never_averaged_together(self):
        view = synthesize(self.data, {"outcome": "completion_score"})
        self.assertEqual(len(view["groups"]), 2)
        for group in view["groups"]:
            scales = {item["scale_id"] for item in group["contributions"]}
            self.assertEqual(scales, {group["scale_id"]})
        self.assertTrue(any("多个量表" in warning for warning in view["warnings"]))

    def test_single_study_scale_is_not_pooled(self):
        data = deepcopy(self.data)
        data["comparisons"] = [item for item in data["comparisons"]
                               if not item["comparison_id"].startswith("cmp_11_")]
        view = synthesize(data, {"outcome": "completion_score"})
        group = self.group(view, "v2")
        self.assertFalse(group["poolable"])
        self.assertEqual(group["pooled"]["k"], 1)
        self.assertIsNone(group["pooled"].get("prediction_interval"))
        self.assertTrue(any("不足2项" in warning for warning in view["warnings"]))


class UnknownCorrelation(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((ROOT / "fixtures" / "data.json").read_text())

    def test_single_week_policy_does_not_need_assumption(self):
        view = synthesize(self.data, {"outcome": "completion_score", "time_policy": "latest"})
        assumption = view["sensitivity"]["correlation_assumption"]
        self.assertFalse(assumption["applicable"])
        self.assertEqual(assumption["scenarios"], [])

    def test_multi_week_reports_assumption_as_not_observed(self):
        view = synthesize(self.data, {"outcome": "completion_score", "time_policy": "all_weeks"})
        assumption = view["sensitivity"]["correlation_assumption"]
        self.assertTrue(assumption["applicable"])
        self.assertTrue(assumption["assumed"])
        self.assertFalse(assumption["known"])
        rhos = sorted({scenario["rho"] for scenario in assumption["scenarios"]})
        self.assertEqual(rhos, [0.2, 0.5, 0.8])
        self.assertTrue(all(scenario["assumed"] for scenario in assumption["scenarios"]))
        self.assertTrue(any("假设" in warning and "不是观测" in warning
                            for warning in view["warnings"]))
        for contribution in view["groups"][0]["contributions"]:
            self.assertEqual(contribution["assumed_rho"], 0.5)

    def test_user_supplied_assumption_changes_estimate(self):
        high = synthesize(self.data, {"outcome": "completion_score",
                                      "time_policy": "all_weeks", "rho": 0.9})
        low = synthesize(self.data, {"outcome": "completion_score",
                                     "time_policy": "all_weeks", "rho": 0.1})
        self.assertNotEqual(high["scope"]["rho"], low["scope"]["rho"])
        self.assertNotEqual(high["groups"][0]["pooled"]["se"],
                            low["groups"][0]["pooled"]["se"])

    def test_out_of_range_assumption_is_rejected(self):
        for rho in [-0.1, 1.5, "x", True]:
            with self.assertRaises(ValueError):
                synthesize(self.data, {"outcome": "completion_score",
                                       "time_policy": "all_weeks", "rho": rho})

    def test_known_correlation_is_marked_observed(self):
        data = deepcopy(self.data)
        data["correlations"]["same_arm_repeated"] = {"known": True, "value": 0.6}
        view = synthesize(data, {"outcome": "completion_score", "time_policy": "all_weeks"})
        assumption = view["sensitivity"]["correlation_assumption"]
        self.assertFalse(assumption["assumed"])
        self.assertEqual({scenario["rho"] for scenario in assumption["scenarios"]}, {0.6})


class DuplicateEvidence(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((ROOT / "fixtures" / "data.json").read_text())

    def test_shared_cohorts_are_detected_and_excluded_from_primary(self):
        view = synthesize(self.data, {"outcome": "completion_score", "contrast_rule": "last"})
        group = view["groups"][0]
        excluded = {item["study_id"]: item for item in group["excluded"]}
        self.assertTrue(excluded)
        overlapping = [item for item in excluded.values()
                       if item["status"] == "overlapping_cohort"]
        self.assertTrue(overlapping)
        self.assertTrue(any(item["overlap_with"] for item in overlapping))
        pairs = view["sensitivity"]["duplicate_reports"]["pairs"]
        self.assertTrue(pairs)

    def test_all_inclusive_policy_adds_studies(self):
        independent = synthesize(self.data, {"outcome": "completion_score", "contrast_rule": "last"})
        inclusive = synthesize(self.data, {"outcome": "completion_score", "contrast_rule": "last",
                                           "overlap_policy": "all"})
        self.assertGreater(inclusive["groups"][0]["pooled"]["k"],
                           independent["groups"][0]["pooled"]["k"])
        self.assertLess(inclusive["groups"][0]["pooled"]["se"],
                        independent["groups"][0]["pooled"]["se"])

    def test_strict_policy_removes_every_overlapping_study(self):
        view = synthesize(self.data, {"outcome": "completion_score", "contrast_rule": "last",
                                      "overlap_policy": "strict"})
        group = view["groups"][0]
        self.assertFalse(group["poolable"])
        self.assertEqual(group["contributions"], [])
        self.assertTrue(any(item["status"] == "overlapping_cohort" for item in group["excluded"]))

    def test_shared_control_is_not_double_counted(self):
        view = synthesize(self.data, {"outcome": "completion_score"})
        comparisons = {item["comparison_id"] for group in view["groups"]
                       for item in group["contributions"]}
        controls = {}
        for item in view["groups"][0]["contributions"]:
            controls.setdefault(item["control_arm"]["arm_id"], set()).add(item["study_id"])
        self.assertTrue(all(len(studies) == 1 for studies in controls.values()))
        self.assertIn("cmp_1_1", comparisons)
        self.assertNotIn("cmp_1_2", comparisons)

    def test_contrast_rule_changes_which_comparison_is_used(self):
        first = synthesize(self.data, {"outcome": "completion_score", "contrast_rule": "first"})
        last = synthesize(self.data, {"outcome": "completion_score", "contrast_rule": "last"})
        self.assertNotEqual(first["groups"][0]["pooled"]["k"], last["groups"][0]["pooled"]["k"])
        rule_rows = [row for row in last["sensitivity"]["rows"]
                     if row["analysis"] == "研究内比较规则"]
        self.assertTrue(rule_rows)


class RobustnessChecks(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((ROOT / "fixtures" / "data.json").read_text())

    def test_leave_one_out_covers_every_included_study(self):
        view = synthesize(self.data, {"outcome": "completion_score", "contrast_rule": "last"})
        group = view["groups"][0]
        omitted = {row["omitted_study"] for row in view["sensitivity"]["leave_one_out"]
                   if row["scale_id"] == group["scale_id"]}
        self.assertEqual(omitted, {item["study_id"] for item in group["contributions"]})

    def test_reporting_policy_never_turns_missing_into_zero(self):
        view = synthesize(self.data, {"outcome": "error_count",
                                      "reporting_policy": "available_case"})
        estimates = {row["policy"]: row["estimate"] for row in
                     [item for section in view["sensitivity"]["reporting_policy"].values()
                      for item in section if item["scale_id"] == "v1"]}
        # 两种策略都只合并完整配对；差异应来自纳入的研究集合，而不是补零。
        self.assertEqual(set(estimates), {"complete_only", "available_case"})
        self.assertTrue(all(value is not None for value in estimates.values()))
        self.assertIsNone(next(row for row in view["parallel"]
                               if row["status"] == "not_reported")["raw_difference"])

    def test_robustness_reports_instability(self):
        view = synthesize(self.data, {"outcome": "completion_score", "contrast_rule": "last"})
        group = view["groups"][0]
        self.assertTrue(group["robustness"]["available"])
        self.assertIn("direction_stable", group["robustness"])
        self.assertGreater(group["robustness"]["variant_count"], 1)

    def test_unreported_totals_are_counted(self):
        view = synthesize(self.data, {"outcome": "error_count"})
        totals = view["reporting"]["totals"]
        self.assertGreater(totals["not_reported"], 0)
        self.assertTrue(any("未报告" in warning for warning in view["warnings"]))
        self.assertTrue(any(not record["complete"] for record in view["reporting"]["by_study"]))


class SavedMaterials(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workbench = Workbench(ROOT, self.temporary.name)

    def test_saved_material_freezes_synthesis(self):
        saved = self.workbench.save({
            "title": "整合复核",
            "selection": {"outcome": "completion_score", "contrast_rule": "last",
                          "time_policy": "all_weeks", "rho": 0.7},
        })
        self.assertEqual(saved["selection"]["rho"], 0.7)
        synthesis = saved["result"]["view"]["synthesis"]
        self.assertEqual(synthesis["scope"]["rho"], 0.7)
        reopened = self.workbench.store.notebook(saved["id"])
        self.assertEqual(reopened, saved)

    def test_explore_keeps_raw_comparisons(self):
        view = explore(json.loads((ROOT / "fixtures" / "data.json").read_text()),
                       {"outcome": "completion_score"})
        self.assertTrue(view["rows"])
        self.assertIn("synthesis", view)
        self.assertEqual(view["synthesis"]["outcome"]["id"], "completion_score")


if __name__ == "__main__":
    unittest.main()


class EdgeConditions(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((ROOT / "fixtures" / "data.json").read_text())

    def test_empty_window_yields_no_groups_and_a_note(self):
        view = synthesize(self.data, {"outcome": "completion_score",
                                      "week_from": 100, "week_to": 200})
        self.assertEqual(view["groups"], [])
        self.assertEqual(view["parallel"], [])
        self.assertTrue(any("只能并列" in warning for warning in view["warnings"]))

    def test_scale_conflict_is_surfaced_not_silently_dropped(self):
        data = deepcopy(self.data)
        for record in data["means"]:
            if (record["arm_id"] == "study_1_arm_2"
                    and record["outcome"] == "completion_score" and record["week"] == 12):
                record["scale_id"] = "v2"
        view = synthesize(data, {"outcome": "completion_score", "contrast_rule": "last"})
        skipped = {item["study_id"]: item for item in view["skipped"]}
        self.assertEqual(skipped["study_1"]["status"], "scale_conflict")

    def test_zero_variance_is_not_pooled(self):
        data = deepcopy(self.data)
        for record in data["means"]:
            if record["arm_id"].startswith("study_1_") and record["outcome"] == "completion_score":
                record["sd"] = 0.0
        view = synthesize(data, {"outcome": "completion_score", "contrast_rule": "last"})
        group = next(group for group in view["groups"] if group["scale_id"] == "v1")
        self.assertNotIn("study_1", {item["study_id"] for item in group["contributions"]})
        statuses = {item["status"] for item in group["excluded"]}
        self.assertIn("zero_variance", statuses)

    def test_fixed_and_random_always_both_reported(self):
        view = synthesize(self.data, {"outcome": "completion_score", "contrast_rule": "last"})
        pooled = view["groups"][0]["pooled"]
        self.assertIsNotNone(pooled["fixed"])
        self.assertIsNotNone(pooled["random"])
        self.assertNotEqual(pooled["fixed"]["estimate"], pooled["random"]["estimate"])

    def test_prediction_interval_requires_three_studies(self):
        view = synthesize(self.data, {"outcome": "completion_score", "contrast_rule": "last"})
        for group in view["groups"]:
            pooled = group["pooled"]
            interval = (pooled.get("random") or {}).get("prediction_interval")
            if pooled["k"] >= 3:
                self.assertIsNotNone(interval)
                self.assertLessEqual(interval[0], pooled["estimate"])
                self.assertGreaterEqual(interval[1], pooled["estimate"])
            else:
                self.assertIsNone(interval)

    def test_weights_sum_to_hundred_per_scale(self):
        view = synthesize(self.data, {"outcome": "completion_score", "contrast_rule": "last"})
        for group in view["groups"]:
            if group["poolable"]:
                total = sum(item["weight_percent"] for item in group["contributions"])
                self.assertAlmostEqual(total, 100.0, places=0)

    def test_applicability_counts_unique_members(self):
        view = synthesize(self.data, {"outcome": "completion_score", "contrast_rule": "last"})
        group = view["groups"][0]
        applicability = group["applicability"]
        self.assertLessEqual(applicability["participants"],
                             applicability["participants_arm_sum"])
        self.assertEqual(applicability["outcome"], "completion_score")
        self.assertIn("同一量表", applicability["statement"])


class ValidationGuards(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((ROOT / "fixtures" / "data.json").read_text())

    def test_unknown_scale_is_rejected(self):
        data = deepcopy(self.data)
        data["means"][0]["scale_id"] = "v9"
        with self.assertRaises(ValueError):
            validate(data)

    def test_unknown_outcome_on_mean_is_rejected(self):
        data = deepcopy(self.data)
        data["means"][0]["outcome"] = "missing"
        with self.assertRaises(ValueError):
            validate(data)

    def test_known_correlation_requires_value_in_range(self):
        data = deepcopy(self.data)
        data["correlations"]["same_arm_repeated"] = {"known": True, "value": 1.4}
        with self.assertRaises(ValueError):
            validate(data)

    def test_inverted_correlation_range_is_rejected(self):
        data = deepcopy(self.data)
        data["correlations"]["same_arm_repeated"] = {"known": False, "range": [0.9, 0.1]}
        with self.assertRaises(ValueError):
            validate(data)

    def test_reported_without_declared_is_rejected(self):
        data = deepcopy(self.data)
        data["means"][0]["declared"] = False
        with self.assertRaises(ValueError):
            validate(data)

    def test_unknown_policies_are_rejected(self):
        for selection in [{"outcome": "completion_score", "reporting_policy": "guess"},
                          {"outcome": "completion_score", "overlap_policy": "guess"},
                          {"outcome": "completion_score", "contrast_rule": "guess"}]:
            with self.assertRaises(ValueError):
                synthesize(self.data, selection)
