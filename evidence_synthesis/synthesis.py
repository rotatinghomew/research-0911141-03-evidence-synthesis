"""跨研究整合。

这里不预设唯一的合并方法：固定效应与随机效应并列计算，无法合并的资料
一律并列呈现。所有依赖假设（重复测量相关性、独立研究的选择）都明确标注，
不把假设值写成观测值。
"""

from math import erfc, exp, isfinite, lgamma, log, sqrt

Z_975 = 1.959963984540054

STATUS_LABELS = {
    "ok": "报告完整",
    "not_reported": "已声明但未报告（不是零效应）",
    "missing_record": "缺少对应记录",
    "no_dispersion": "未报告离散程度",
    "zero_variance": "方差为零，无法计算权重",
    "scale_conflict": "同一比较内量表不一致",
    "scale_mixed": "所选时间点跨量表",
    "missing_comparison": "缺少可比比较",
    "incomplete_reporting": "所选范围内有已声明未报告的结果",
    "overlapping_cohort": "与已纳入研究共用学员（重复证据）",
}


# ---------- 基础分布函数（标准库实现，避免新增依赖） ----------

def _gser(a, x):
    if x <= 0:
        return 0.0
    total = 1.0 / a
    term = total
    ap = a
    for _ in range(1000):
        ap += 1.0
        term *= x / ap
        total += term
        if abs(term) < abs(total) * 1e-14:
            break
    return total * exp(-x + a * log(x) - lgamma(a))


def _gcf(a, x):
    tiny = 1e-300
    b = x + 1.0 - a
    c = 1.0 / tiny
    d = 1.0 / b
    h = d
    for index in range(1, 1000):
        an = -index * (index - a)
        b += 2.0
        d = an * d + b
        if abs(d) < tiny:
            d = tiny
        c = b + an / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-14:
            break
    return exp(-x + a * log(x) - lgamma(a)) * h


def chi2_sf(value, degrees):
    if degrees <= 0:
        return None
    if value <= 0:
        return 1.0
    a, x = degrees / 2.0, value / 2.0
    if x < a + 1.0:
        return max(0.0, min(1.0, 1.0 - _gser(a, x)))
    return max(0.0, min(1.0, _gcf(a, x)))


def _betacf(a, b, x):
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, 500):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-14:
            break
    return h


def _betai(a, b, x):
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    front = exp(lgamma(a + b) - lgamma(a) - lgamma(b) + a * log(x) + b * log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def t_sf(value, degrees):
    if degrees <= 0:
        return None
    x = degrees / (degrees + value * value)
    tail = 0.5 * _betai(degrees / 2.0, 0.5, x)
    return tail if value >= 0 else 1.0 - tail


def t_critical(degrees, tail=0.025):
    """双侧 95% 临界值；用二分求解，避免依赖外部统计包。"""
    if degrees <= 0:
        return None
    low, high = 0.0, 200.0
    for _ in range(200):
        middle = (low + high) / 2.0
        if t_sf(middle, degrees) > tail:
            low = middle
        else:
            high = middle
    return (low + high) / 2.0


def normal_sf(value):
    return 0.5 * erfc(value / sqrt(2.0))


def _round(value, digits=6):
    if value is None:
        return None
    return round(value, digits)


def _finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


# ---------- 选择条件 ----------

def _selection_scope(data, selection):
    outcome_id = selection.get("outcome", "completion_score")
    outcomes = {record["id"]: record for record in data["outcomes"]}
    if outcome_id not in outcomes:
        raise ValueError("未知结果指标")
    outcome = outcomes[outcome_id]
    weeks = sorted({record["week"] for record in data["means"] if record["outcome"] == outcome_id})
    week_from = selection.get("week_from", weeks[0] if weeks else 0)
    week_to = selection.get("week_to", weeks[-1] if weeks else 0)
    if isinstance(week_from, bool) or not isinstance(week_from, int):
        raise ValueError("起始时间点应为整数")
    if isinstance(week_to, bool) or not isinstance(week_to, int):
        raise ValueError("结束时间点应为整数")
    if week_from > week_to:
        raise ValueError("起始时间点不能晚于结束时间点")
    time_policy = selection.get("time_policy", "latest")
    if time_policy not in {"latest", "all_weeks"}:
        raise ValueError("未知时间点策略")
    model = selection.get("model", "random")
    if model not in {"random", "fixed"}:
        raise ValueError("未知合并模型")
    contrast_rule = selection.get("contrast_rule", "first")
    if contrast_rule not in {"first", "last"}:
        raise ValueError("未知研究内比较选择规则")
    return outcome_id, outcome, week_from, week_to, time_policy, model, contrast_rule


class Context:
    def __init__(self, data, outcome_id, week_from, week_to):
        self.data = data
        self.outcome_id = outcome_id
        self.week_from = week_from
        self.week_to = week_to
        self.arms = {record["arm_id"]: record for record in data["arms"]}
        self.cohorts = {record["cohort_id"]: set(record["members"]) for record in data["cohorts"]}
        self.comparisons = sorted(data["comparisons"], key=lambda record: record["comparison_id"])
        self.by_study = {}
        for comparison in self.comparisons:
            self.by_study.setdefault(comparison["study_id"], []).append(comparison)
        self.means = {}
        for record in data["means"]:
            if record["outcome"] == outcome_id:
                self.means[(record["arm_id"], record["week"])] = record
        self.weeks = sorted({record["week"] for record in data["means"] if record["outcome"] == outcome_id})
        self.range_weeks = [week for week in self.weeks if week_from <= week <= week_to]
        self.study_arms = {}
        for arm_id, arm in self.arms.items():
            self.study_arms.setdefault(arm["study_id"], set()).add(arm_id)

    def cohort_members(self, study_id):
        members = set()
        for arm_id in self.study_arms.get(study_id, set()):
            members |= self.cohorts.get(self.arms[arm_id]["cohort_id"], set())
        return members

    def arm_members(self, arm_id):
        return self.cohorts.get(self.arms[arm_id]["cohort_id"], set())

    def candidate_members(self, candidate):
        """只取该比较实际用到的两个组别，避免把研究内其他组别算成重复证据。"""
        members = set()
        for arm_id in (candidate["treatment"], candidate["control"]):
            members |= self.arm_members(arm_id)
        return members

    def candidate_shared_cohorts(self, candidate):
        return sorted({self.arms[candidate["treatment"]]["cohort_id"],
                       self.arms[candidate["control"]]["cohort_id"]})

    def candidates_overlap(self, first, second):
        shared = self.candidate_members(first) & self.candidate_members(second)
        shared_cohorts = sorted(set(self.candidate_shared_cohorts(first))
                                & set(self.candidate_shared_cohorts(second)))
        return shared, shared_cohorts



    def studies(self):
        return sorted(self.study_arms)


def _pair_status(treatment, control):
    if treatment is None or control is None:
        return "missing_record"
    if not treatment["reported"] or not control["reported"]:
        return "not_reported"
    if treatment["sd"] is None or control["sd"] is None:
        return "no_dispersion"
    if treatment["sd"] <= 0 and control["sd"] <= 0:
        return "zero_variance"
    return "ok"


def _week_effect(context, comparison, week):
    treatment = context.means.get((comparison["treatment"], week))
    control = context.means.get((comparison["control"], week))
    status = _pair_status(treatment, control)
    record = {
        "week": week,
        "status": status,
        "treatment": comparison["treatment"],
        "control": comparison["control"],
    }
    # 即使不能合并，也尽量保留量表，便于把排除原因归到正确量表。
    observed_scales = {item["scale_id"] for item in (treatment, control)
                       if item is not None and item.get("scale_id") is not None}
    if len(observed_scales) == 1:
        record["scale_id"] = next(iter(observed_scales))
    elif observed_scales:
        record["scale_ids"] = sorted(observed_scales)
    if status == "ok":
        scale = {treatment["scale_id"], control["scale_id"]}
        if len(scale) != 1:
            record["status"] = "scale_conflict"
        else:
            n_t = context.arms[comparison["treatment"]]["n"]
            n_c = context.arms[comparison["control"]]["n"]
            effect = treatment["mean"] - control["mean"]
            variance = treatment["sd"] ** 2 / n_t + control["sd"] ** 2 / n_c
            if variance <= 0:
                record["status"] = "zero_variance"
            else:
                record.update({
                    "scale_id": next(iter(scale)),
                    "n_treatment": n_t,
                    "n_control": n_c,
                    "treatment_mean": treatment["mean"],
                    "treatment_sd": treatment["sd"],
                    "control_mean": control["mean"],
                    "control_sd": control["sd"],
                    "effect": effect,
                    "variance": variance,
                    "se": sqrt(variance),
                })
    return record


def _combine_weeks(week_records, rho):
    rho = 0.0 if rho is None else rho
    effects = [record["effect"] for record in week_records]
    variances = [record["variance"] for record in week_records]
    count = len(effects)
    covariance_sum = sum(variances)
    for first in range(count):
        for second in range(first + 1, count):
            covariance_sum += 2.0 * rho * sqrt(variances[first] * variances[second])
    variance = covariance_sum / (count * count)
    return sum(effects) / count, variance


def build_candidate(context, comparison, time_policy, rho):
    """为一个研究选定的比较构建贡献；不满足合并条件时保留原因。"""
    study_id = comparison["study_id"]
    weeks = [week for week in context.range_weeks
             if (comparison["treatment"], week) in context.means
             or (comparison["control"], week) in context.means]
    candidate = {
        "study_id": study_id,
        "comparison_id": comparison["comparison_id"],
        "treatment": comparison["treatment"],
        "control": comparison["control"],
        "treatment_cohort": context.arms[comparison["treatment"]]["cohort_id"],
        "control_cohort": context.arms[comparison["control"]]["cohort_id"],
        "weeks": weeks,
    }
    if not weeks:
        candidate["status"] = "missing_record"
        return candidate
    if time_policy == "latest":
        week_records = [_week_effect(context, comparison, weeks[-1])]
    else:
        week_records = [_week_effect(context, comparison, week) for week in weeks]
    candidate["week_records"] = week_records
    candidate["week_used"] = [record["week"] for record in week_records]
    for record in week_records:
        if record["status"] != "ok":
            candidate["status"] = record["status"]
            return candidate
    scales = {record["scale_id"] for record in week_records}
    if len(scales) != 1:
        candidate["status"] = "scale_mixed"
        return candidate
    candidate["scale_id"] = next(iter(scales))
    candidate["n_treatment"] = week_records[0]["n_treatment"]
    candidate["n_control"] = week_records[0]["n_control"]
    if len(week_records) == 1:
        record = week_records[0]
        candidate["effect"] = record["effect"]
        candidate["variance"] = record["variance"]
        candidate["se"] = record["se"]
        candidate["assumed_rho"] = None
    else:
        effect, variance = _combine_weeks(week_records, rho)
        candidate["effect"] = effect
        candidate["variance"] = variance
        candidate["se"] = sqrt(variance)
        candidate["assumed_rho"] = rho
    candidate["treatment_arm"] = {
        "arm_id": comparison["treatment"], "cohort_id": candidate["treatment_cohort"],
        "n": candidate["n_treatment"], "mean": week_records[0]["treatment_mean"],
        "sd": week_records[0]["treatment_sd"], "weeks": [record["week"] for record in week_records],
        "means_by_week": {str(record["week"]): record["treatment_mean"] for record in week_records},
    }
    candidate["control_arm"] = {
        "arm_id": comparison["control"], "cohort_id": candidate["control_cohort"],
        "n": candidate["n_control"], "mean": week_records[0]["control_mean"],
        "sd": week_records[0]["control_sd"], "weeks": [record["week"] for record in week_records],
        "means_by_week": {str(record["week"]): record["control_mean"] for record in week_records},
    }
    candidate["status"] = "ok"
    return candidate


def _reporting_for_study(context, study_id):
    counts = {"reported": 0, "not_reported": 0, "missing_record": 0}
    details = []
    for arm_id in sorted(context.study_arms.get(study_id, set())):
        for week in context.range_weeks:
            record = context.means.get((arm_id, week))
            if record is None:
                status = "missing_record"
            elif record["declared"] and record["reported"]:
                status = "reported"
            elif record["declared"]:
                status = "not_reported"
            else:
                status = "missing_record"
            counts[status] += 1
            details.append({"arm_id": arm_id, "week": week, "status": status})
    return {
        "study_id": study_id,
        "counts": counts,
        "complete": counts["not_reported"] == 0 and counts["missing_record"] == 0,
        "records": details,
    }


def pool(records, model="random"):
    """逆方差合并；同时给出固定与随机效应，避免默认方法被当成唯一答案。"""
    count = len(records)
    result = {"k": count, "model": model, "fixed": None, "random": None}
    if count == 0:
        result["heterogeneity"] = None
        return result
    effects = [record["effect"] for record in records]
    variances = [record["variance"] for record in records]
    fixed_weights = [1.0 / variance for variance in variances]
    total = sum(fixed_weights)
    fixed_estimate = sum(weight * effect for weight, effect in zip(fixed_weights, effects)) / total
    fixed_se = sqrt(1.0 / total)
    fixed = {
        "estimate": fixed_estimate, "se": fixed_se,
        "ci": [fixed_estimate - Z_975 * fixed_se, fixed_estimate + Z_975 * fixed_se],
        "p": 2.0 * normal_sf(abs(fixed_estimate / fixed_se)),
    }
    if count >= 2:
        q_value = sum(weight * (effect - fixed_estimate) ** 2
                      for weight, effect in zip(fixed_weights, effects))
        degrees = count - 1
        scale = total - sum(weight ** 2 for weight in fixed_weights) / total
        tau2 = max(0.0, (q_value - degrees) / scale) if scale > 0 else 0.0
        q_p = chi2_sf(q_value, degrees)
        i2 = max(0.0, (q_value - degrees) / q_value) * 100.0 if q_value > 0 else 0.0
        random_weights = [1.0 / (variance + tau2) for variance in variances]
        random_total = sum(random_weights)
        random_estimate = sum(weight * effect for weight, effect in zip(random_weights, effects)) / random_total
        random_se = sqrt(1.0 / random_total)
        random = {
            "estimate": random_estimate, "se": random_se,
            "ci": [random_estimate - Z_975 * random_se, random_estimate + Z_975 * random_se],
            "p": 2.0 * normal_sf(abs(random_estimate / random_se)),
        }
        heterogeneity = {
            "q": q_value, "df": degrees, "p": q_p,
            "i2_percent": i2, "tau2": tau2, "tau": sqrt(tau2),
        }
        if count >= 3:
            critical = t_critical(count - 2)
            half = critical * sqrt(tau2 + random_se ** 2)
            random["prediction_interval"] = [random_estimate - half, random_estimate + half]
            random["prediction_df"] = count - 2
        else:
            random["prediction_interval"] = None
            random["prediction_note"] = "研究数少于3，无法给出研究间预测区间"
    else:
        random = dict(fixed)
        random["prediction_interval"] = None
        heterogeneity = None
    result["fixed"] = fixed
    result["random"] = random
    result["heterogeneity"] = heterogeneity
    selected = random if model == "random" else fixed
    if count < 2 and model == "random":
        selected = fixed
        result["model_note"] = "研究数少于2，随机效应无法估计，展示单项估计"
    result.update(selected)
    weights = random_weights if model == "random" and count >= 2 else fixed_weights
    weight_total = sum(weights)
    result["weights"] = [weight / weight_total * 100.0 for weight in weights]
    result["poolable"] = count >= 2
    return result


def _clean_pool(pooled):
    if pooled is None:
        return None
    cleaned = dict(pooled)
    if cleaned.get("k", 0) == 0:
        return cleaned
    for model in ["fixed", "random"]:
        entry = cleaned.get(model)
        if entry:
            entry["estimate"] = _round(entry["estimate"])
            entry["se"] = _round(entry["se"])
            entry["ci"] = [_round(value) for value in entry["ci"]]
            entry["p"] = _round(entry["p"], 10)
            if entry.get("prediction_interval"):
                entry["prediction_interval"] = [_round(value) for value in entry["prediction_interval"]]
    for key in ["estimate", "se", "p"]:
        cleaned[key] = _round(cleaned[key], 10 if key == "p" else 6)
    if cleaned.get("ci"):
        cleaned["ci"] = [_round(value) for value in cleaned["ci"]]
    heterogeneity = cleaned.get("heterogeneity")
    if heterogeneity:
        for key in ["q", "i2_percent", "tau2", "tau"]:
            heterogeneity[key] = _round(heterogeneity[key])
        heterogeneity["p"] = _round(heterogeneity["p"], 10)
    cleaned["weights"] = [_round(value, 3) for value in cleaned.get("weights", [])]
    return cleaned


def _evaluate(context, options):
    """按选项重新构建所有量表分组；同时记录未进入合并的研究与原因。"""
    time_policy = options["time_policy"]
    rho = options.get("rho")
    reporting_policy = options.get("reporting_policy", "complete_only")
    overlap_policy = options.get("overlap_policy", "independent")
    contrast_override = options.get("contrast_override", {})
    contrast_rule = options.get("contrast_rule", "first")
    reporting = {study: _reporting_for_study(context, study) for study in context.studies()}
    candidates = {}
    skipped = []
    for study in context.studies():
        comparisons = context.by_study.get(study, [])
        if not comparisons:
            skipped.append({"study_id": study, "status": "missing_comparison",
                            "reason": STATUS_LABELS["missing_comparison"]})
            continue
        comparison = None
        if study in contrast_override:
            comparison = next((item for item in comparisons
                               if item["comparison_id"] == contrast_override[study]), None)
        if comparison is None:
            comparison = comparisons[-1] if contrast_rule == "last" else comparisons[0]
        candidate = build_candidate(context, comparison, time_policy, rho)
        candidate["reporting_complete"] = reporting[study]["complete"]
        # data_ok 表示数据本身满足合并条件；是否真正进入合并还要看报告与重叠策略。
        candidate["data_ok"] = candidate["status"] == "ok"
        candidate["scale_for_group"] = candidate.get("scale_id")
        if not candidate["data_ok"] and candidate["scale_for_group"] is None:
            observed = {(record.get("scale_id") or "")
                        for record in candidate.get("week_records", [])}
            observed.discard("")
            if len(observed) == 1:
                candidate["scale_for_group"] = next(iter(observed))
            elif len(observed) > 1:
                candidate["scale_for_group"] = "+".join(sorted(observed))
        candidates[study] = candidate

    # 量表分组：有可确定量表的候选都进入对应分组，便于展示排除原因。
    buckets = {}
    for candidate in candidates.values():
        scale = candidate["scale_for_group"]
        if scale is None:
            candidate["included"] = False
            candidate["exclusion"] = candidate["status"]
            candidate["overlap_with"] = []
            skipped.append({"study_id": candidate["study_id"],
                            "comparison_id": candidate["comparison_id"],
                            "week": candidate.get("week_used", []),
                            "status": candidate["status"],
                            "reason": STATUS_LABELS.get(candidate["status"], candidate["status"])})
            continue
        buckets.setdefault(scale, []).append(candidate)

    results = {}
    for scale, records in buckets.items():
        records.sort(key=lambda item: item["study_id"])
        included = []
        included_ids = set()
        excluded = []
        for candidate in records:
            overlaps = []
            for other in records:
                if other is candidate:
                    continue
                shared, shared_cohorts = context.candidates_overlap(candidate, other)
                if shared or shared_cohorts:
                    overlaps.append({"study_id": other["study_id"],
                                     "comparison_id": other["comparison_id"],
                                     "shared_members": len(shared),
                                     "shared_cohorts": shared_cohorts})
            candidate["overlap_with"] = overlaps
            if not candidate["data_ok"]:
                candidate["included"] = False
                candidate["exclusion"] = candidate["status"]
                excluded.append(candidate)
            elif reporting_policy == "complete_only" and not candidate["reporting_complete"]:
                candidate["included"] = False
                candidate["exclusion"] = "incomplete_reporting"
                excluded.append(candidate)
            elif overlap_policy == "strict" and overlaps:
                candidate["included"] = False
                candidate["exclusion"] = "overlapping_cohort"
                excluded.append(candidate)
            elif overlap_policy == "independent" and any(
                    other["study_id"] in included_ids for other in overlaps):
                candidate["included"] = False
                candidate["exclusion"] = "overlapping_cohort"
                excluded.append(candidate)
            else:
                candidate["included"] = True
                candidate["exclusion"] = None
                included.append(candidate)
                included_ids.add(candidate["study_id"])
        pooled = _clean_pool(pool(included, options.get("model", "random")))
        for index, candidate in enumerate(included):
            if pooled and pooled.get("weights"):
                candidate["weight_percent"] = pooled["weights"][index]
        results[scale] = {
            "scale_id": scale,
            "included": included,
            "excluded": excluded,
            "pooled": pooled,
        }
    return {
        "groups": results,
        "skipped": skipped,
        "reporting": reporting,
        "candidates": candidates,
    }


def _summary(pooled):
    if pooled is None or pooled.get("k", 0) == 0:
        return {"k": 0, "model": (pooled or {}).get("model"), "estimate": None, "ci": None,
                "p": None, "poolable": False, "heterogeneity": None, "prediction_interval": None}
    return {
        "k": pooled["k"],
        "model": pooled["model"],
        "estimate": pooled["estimate"],
        "ci": pooled["ci"],
        "p": pooled["p"],
        "poolable": pooled["poolable"],
        "heterogeneity": pooled.get("heterogeneity"),
        "prediction_interval": (pooled.get("random") or {}).get("prediction_interval"),
    }


def _flat_row(label, detail, scale, pooled, note=""):
    if pooled is None or pooled.get("k", 0) == 0 or "estimate" not in pooled:
        return {"analysis": label, "detail": detail, "scale_id": scale, "k": 0,
                "estimate": None, "ci": None, "p": None, "note": note}
    return {
        "analysis": label,
        "detail": detail,
        "scale_id": scale,
        "k": pooled["k"],
        "estimate": pooled["estimate"],
        "ci": pooled["ci"],
        "p": pooled["p"],
        "note": note,
    }


def _candidate_overlap_pairs(primary):
    """列出主分析各量表内实际比较之间的重复证据对。"""
    pairs = []
    for scale, group in primary["groups"].items():
        records = sorted(group["included"] + group["excluded"],
                         key=lambda item: item["study_id"])
        for index, first in enumerate(records):
            for second in records[index + 1:]:
                for other in first.get("overlap_with", []):
                    if other["study_id"] == second["study_id"]:
                        pairs.append({
                            "scale_id": scale,
                            "studies": [first["study_id"], second["study_id"]],
                            "comparisons": [first["comparison_id"], second["comparison_id"]],
                            "shared_members": other["shared_members"],
                            "shared_cohorts": other["shared_cohorts"],
                        })
                        break
    return pairs


def _sensitivity(context, outcome, primary, week_from, week_to, time_policy, model, rho=None,
                 contrast_rule="first", reporting_policy="complete_only",
                 overlap_policy="independent"):
    rows = []
    groups = {}
    base_options = {"time_policy": time_policy, "rho": rho, "model": model,
                    "contrast_rule": contrast_rule,
                    "reporting_policy": reporting_policy,
                    "overlap_policy": overlap_policy}

    leave_one_out = []
    for scale, group in primary["groups"].items():
        for omit in [candidate["study_id"] for candidate in group["included"]]:
            subset = [candidate for candidate in group["included"] if candidate["study_id"] != omit]
            pooled = _clean_pool(pool(subset, model))
            entry = {"scale_id": scale, "omitted_study": omit, **_summary(pooled)}
            leave_one_out.append(entry)
            rows.append(_flat_row("留一研究", "排除 " + omit, scale, pooled,
                                  "检查单个研究对总体估计的影响"))
    groups["leave_one_out"] = leave_one_out

    duplicate = {"pairs": _candidate_overlap_pairs(primary), "primary_excluded": [], "by_scale": []}
    for scale, group in primary["groups"].items():
        duplicate["primary_excluded"].extend(
            {"study_id": candidate["study_id"], "comparison_id": candidate["comparison_id"],
             "overlap_with": candidate.get("overlap_with", [])}
            for candidate in group["excluded"]
            if candidate.get("exclusion") == "overlapping_cohort")
    for scale, group in primary["groups"].items():
        eligible = [candidate for candidate in group["included"] + group["excluded"]
                    if candidate.get("data_ok")
                    and (reporting_policy == "available_case"
                         or candidate.get("exclusion") != "incomplete_reporting")]
        all_pool = _clean_pool(pool(eligible, model))
        strict_pool = _clean_pool(pool([candidate for candidate in eligible
                                        if not candidate.get("overlap_with")], model))
        primary_pool = group["pooled"]
        duplicate["by_scale"].append({
            "scale_id": scale,
            "independent": _summary(primary_pool),
            "all_studies": _summary(all_pool),
            "all_studies_studies": [candidate["study_id"] for candidate in eligible],
            "exclude_all_overlapping": _summary(strict_pool),
            "exclude_all_overlapping_studies": [candidate["study_id"] for candidate in eligible
                                                if not candidate.get("overlap_with")],
        })
        rows.append(_flat_row("当前重复证据处理", "按所选策略纳入", scale,
                              primary_pool, "与上方总体效应使用同一策略"))
        rows.append(_flat_row("可疑重复报告全部纳入", "忽略队列重叠", scale, all_pool,
                              "若把重叠研究当作独立证据，权重与精度会被高估"))
        rows.append(_flat_row("剔除所有重叠研究", "只要与其他研究共用学员就排除", scale, strict_pool,
                              "最保守的重复报告处理"))
    groups["duplicate_reports"] = duplicate

    contrast_rows = []
    for scale, group in primary["groups"].items():
        for candidate in group["included"]:
            study = candidate["study_id"]
            alternatives = [item for item in context.by_study.get(study, [])
                            if item["comparison_id"] != candidate["comparison_id"]]
            for alternative in alternatives:
                rebuilt = _evaluate(context, {**base_options,
                                             "contrast_override": {study: alternative["comparison_id"]}})
                alternative_group = rebuilt["groups"].get(scale)
                if alternative_group is None:
                    # 换用另一比较后可能落入不同量表；不把不同量表硬凑进同一组。
                    rows.append(_flat_row("报告选择", study + " 改用 " + alternative["comparison_id"],
                                          scale, None,
                                          "换用后该研究不在同一量表，无法与本组并列合并"))
                    continue
                pooled = alternative_group["pooled"]
                note = "共享对照的不同比较不能同时计入；此处每次只替换一项研究"
                contrast_rows.append({
                    "scale_id": scale, "study_id": study,
                    "primary_comparison": candidate["comparison_id"],
                    "alternative_comparison": alternative["comparison_id"],
                    **_summary(pooled),
                })
                rows.append(_flat_row("报告选择", study + " 改用 " + alternative["comparison_id"],
                                      scale, pooled, note))
    groups["contrast_choice"] = contrast_rows

    global_rules = []
    for rule in ["first", "last"]:
        if rule == contrast_rule:
            continue
        evaluation = _evaluate(context, {**base_options, "contrast_rule": rule})
        for scale, group in evaluation["groups"].items():
            global_rules.append({"scale_id": scale, "contrast_rule": rule,
                                 **_summary(group["pooled"])})
            rows.append(_flat_row("研究内比较规则",
                                  "全部研究改用 " + ("第一个" if rule == "first" else "最后一个") + "比较",
                                  scale, group["pooled"],
                                  "共用对照的比较不能同时计入；规则本身会影响纳入哪项比较"))
    groups["contrast_rule"] = global_rules

    reporting = {}
    for policy in ["complete_only", "available_case"]:
        evaluation = _evaluate(context, {**base_options, "reporting_policy": policy})
        section = []
        for scale, group in evaluation["groups"].items():
            section.append({"scale_id": scale, "policy": policy, **_summary(group["pooled"])})
            rows.append(_flat_row("报告完整性策略",
                                  "仅完整报告" if policy == "complete_only" else "可用配对（不要求全部声明已报告）",
                                  scale, group["pooled"],
                                  "未报告不等于零效应；可用配对策略不补零"))
        reporting[policy] = section
    groups["reporting_policy"] = reporting

    correlation = _correlation_sensitivity(context, primary, week_from, week_to, time_policy, model,
                                           rho, contrast_rule, reporting_policy, overlap_policy)
    for scenario in correlation["scenarios"]:
        rows.append(_flat_row("重复测量相关假设", "rho=" + str(scenario["rho"]), scenario["scale_id"],
                              scenario.get("_pooled"),
                              "假设值，不是观测值" if scenario["assumed"] else "数据声明为已知"))
    for scenario in correlation["scenarios"]:
        scenario.pop("_pooled", None)
    groups["correlation_assumption"] = correlation
    groups["rows"] = rows
    return groups


def _correlation_sensitivity(context, primary, week_from, week_to, time_policy, model,
                             base_rho=None, contrast_rule="first",
                             reporting_policy="complete_only", overlap_policy="independent"):
    spec = context.data.get("correlations", {}).get("same_arm_repeated")
    correlation = {
        "source": "correlations.same_arm_repeated",
        "known": bool(spec.get("known")) if isinstance(spec, dict) else False,
        "range": spec.get("range") if isinstance(spec, dict) else None,
        "assumed": True,
        "note": "重复测量相关性未知；以下为假设值下的结果，不能当作已观测。",
        "scenarios": [],
    }
    if not isinstance(spec, dict):
        correlation["note"] = "数据未声明重复测量相关性，无法进行假设比较。"
        return correlation
    if spec.get("known") and _finite_number(spec.get("value")):
        values = [spec["value"]]
        correlation["assumed"] = False
        correlation["known_value"] = spec["value"]
        correlation["note"] = "数据声明相关性已知；仍应核对来源与适用范围。"
    else:
        value_range = spec.get("range")
        if not (isinstance(value_range, list) and len(value_range) == 2
                and all(_finite_number(value) and 0 <= value <= 1 for value in value_range)):
            correlation["note"] = "相关性声明不完整，无法进行假设比较。"
            return correlation
        low, high = value_range
        values = sorted({round(low, 6), round((low + high) / 2.0, 6), round(high, 6)})
    multi_week = any(len(candidate["week_records"]) > 1
                     for group in primary["groups"].values() for candidate in group["included"])
    if not multi_week:
        correlation["applicable"] = False
        correlation["note"] += " 当前时间策略每个研究只取一个时间点，不需要该假设。"
        return correlation
    correlation["applicable"] = True
    for rho in values:
        evaluation = _evaluate(context, {"time_policy": "all_weeks", "rho": rho, "model": model,
                                         "contrast_rule": contrast_rule,
                                         "reporting_policy": reporting_policy,
                                         "overlap_policy": overlap_policy})
        for scale, group in evaluation["groups"].items():
            correlation["scenarios"].append({
                "scale_id": scale, "rho": rho, "assumed": correlation["assumed"],
                "k": group["pooled"]["k"] if group["pooled"] else 0,
                "estimate": group["pooled"]["estimate"] if group["pooled"] else None,
                "ci": group["pooled"]["ci"] if group["pooled"] else None,
                "p": group["pooled"]["p"] if group["pooled"] else None,
                "_pooled": group["pooled"],
            })
    return correlation


def _applicability(context, group, outcome):
    included = group["included"]
    studies = sorted({candidate["study_id"] for candidate in included})
    cohorts = sorted({candidate["treatment_cohort"] for candidate in included}
                     | {candidate["control_cohort"] for candidate in included})
    members = set()
    for candidate in included:
        members |= context.candidate_members(candidate)
    total = sum((context.arms[candidate["treatment"]]["n"] + context.arms[candidate["control"]]["n"])
                for candidate in included)
    pooled = group["pooled"]
    return {
        "studies": studies,
        "study_count": len(studies),
        "cohorts": cohorts,
        "cohort_count": len(cohorts),
        "participants": len(members),
        "participants_arm_sum": total,
        "weeks": sorted({week for candidate in included for week in candidate["week_used"]}),
        "scale_id": group["scale_id"],
        "outcome": outcome["id"],
        "direction": outcome["direction"],
        "direction_note": ("本结果越低越好，因此负值表示干预组更低、按此方向为获益"
                           if outcome["direction"] == "lower"
                           else "本结果越高越好，因此正值表示干预组更高、按此方向为获益"),
        "prediction_interval": (pooled.get("random") or {}).get("prediction_interval") if pooled else None,
        "statement": (
            "仅适用于与纳入研究同一量表、同一结果方向和所选时间范围内的人群；"
            "超出这些条件时应并列查看，而不是外推总体效应。"
        ),
    }


def _robustness(sensitivity_rows, primary_pooled):
    estimates = [row["estimate"] for row in sensitivity_rows
                 if row["estimate"] is not None and row["k"] >= 2]
    if (primary_pooled is None or primary_pooled.get("k", 0) < 2
            or "estimate" not in primary_pooled or not estimates):
        return {"available": False, "note": "可比较的敏感性分析不足"}
    primary = primary_pooled["estimate"]
    significant = primary_pooled["p"] < 0.05
    same_direction = all((estimate > 0) == (primary > 0) or estimate == 0 for estimate in estimates)
    same_significance = all((row["p"] < 0.05) == significant
                            for row in sensitivity_rows
                            if row["estimate"] is not None and row["k"] >= 2 and row["p"] is not None)
    return {
        "available": True,
        "variant_count": len(estimates),
        "estimate_range": [_round(min(estimates)), _round(max(estimates))],
        "direction_stable": same_direction,
        "significance_stable": same_significance,
        "note": ("方向与显著性在不同敏感性分析下稳定"
                 if same_direction and same_significance
                 else "部分敏感性分析改变方向或显著性，结论依赖分析选择"),
    }


def _default_rho(data):
    """多时间点写入同一研究效应时所采用的相关假设；只在数据已声明时给出。"""
    spec = data.get("correlations", {}).get("same_arm_repeated")
    if isinstance(spec, dict) and spec.get("known") and _finite_number(spec.get("value")):
        return spec["value"]
    if isinstance(spec, dict):
        value_range = spec.get("range")
        if (isinstance(value_range, list) and len(value_range) == 2
                and all(_finite_number(value) and 0 <= value <= 1 for value in value_range)):
            return (value_range[0] + value_range[1]) / 2.0
    return 0.0


def synthesize(data, selection):
    outcome_id, outcome, week_from, week_to, time_policy, model, contrast_rule = _selection_scope(data, selection)
    rho = selection.get("rho")
    if rho is not None:
        if not _finite_number(rho) or not 0 <= rho <= 1:
            raise ValueError("相关性假设应在0到1之间")
    elif time_policy == "all_weeks":
        rho = _default_rho(data)
    context = Context(data, outcome_id, week_from, week_to)
    reporting_policy = selection.get("reporting_policy", "complete_only")
    overlap_policy = selection.get("overlap_policy", "independent")
    if reporting_policy not in {"complete_only", "available_case"}:
        raise ValueError("未知报告完整性策略")
    if overlap_policy not in {"independent", "all", "strict"}:
        raise ValueError("未知重复证据处理策略")
    primary = _evaluate(context, {"time_policy": time_policy, "rho": rho, "model": model,
                                  "contrast_rule": contrast_rule,
                                  "reporting_policy": reporting_policy,
                                  "overlap_policy": overlap_policy})

    # 主分析的贡献按量表分别展示；不同量表绝不进入同一平均数。
    groups = []
    for scale in sorted(primary["groups"]):
        raw_group = primary["groups"][scale]
        pooled = raw_group["pooled"]
        contributions = []
        for candidate in raw_group["included"]:
            contribution = {
                "study_id": candidate["study_id"],
                "comparison_id": candidate["comparison_id"],
                "scale_id": scale,
                "weeks": candidate["week_used"],
                "effect": _round(candidate["effect"]),
                "se": _round(candidate["se"]),
                "weight_percent": _round(candidate.get("weight_percent"), 3),
                "assumed_rho": candidate.get("assumed_rho"),
                "treatment_arm": candidate["treatment_arm"],
                "control_arm": candidate["control_arm"],
                "reporting_complete": candidate["reporting_complete"],
                "shared_control_with": sorted(
                    other["comparison_id"] for other in context.by_study[candidate["study_id"]]
                    if other["control"] == candidate["control"]
                    and other["comparison_id"] != candidate["comparison_id"]),
            }
            contributions.append(contribution)
        excluded = [{
            "study_id": candidate["study_id"],
            "comparison_id": candidate["comparison_id"],
            "status": candidate.get("exclusion") or candidate["status"],
            "reason": STATUS_LABELS.get(candidate.get("exclusion") or candidate["status"],
                                        candidate.get("exclusion") or candidate["status"]),
            "overlap_with": candidate.get("overlap_with", []),
        } for candidate in raw_group["excluded"]]
        groups.append({
            "scale_id": scale,
            "poolable": bool(pooled and pooled.get("poolable")),
            "pooled": pooled,
            "contributions": contributions,
            "excluded": excluded,
            "applicability": _applicability(context, raw_group, outcome),
        })

    sensitivity = _sensitivity(context, outcome, primary, week_from, week_to, time_policy, model, rho,
                               contrast_rule, reporting_policy, overlap_policy)
    for group in groups:
        relevant = [row for row in sensitivity["rows"]
                    if row["scale_id"] == group["scale_id"]
                    and row["analysis"] != "当前重复证据处理"]
        group["robustness"] = _robustness(relevant, group["pooled"])

    # 并列呈现：所有比较 × 所选时间点，保留“未报告/缺记录/无离散程度”等状态。
    parallel = []
    for comparison in context.comparisons:
        for week in context.range_weeks:
            treatment = context.means.get((comparison["treatment"], week))
            control = context.means.get((comparison["control"], week))
            status = _pair_status(treatment, control)
            parallel.append({
                "study_id": comparison["study_id"],
                "comparison_id": comparison["comparison_id"],
                "week": week,
                "scale_id": (treatment or control or {}).get("scale_id"),
                "status": status,
                "reason": STATUS_LABELS.get(status, status),
                "reported_pair": status in {"ok", "zero_variance"},
                "raw_difference": (_round(treatment["mean"] - control["mean"])
                                   if treatment and control and treatment["reported"]
                                   and control["reported"] else None),
                "has_dispersion": bool(treatment and control and treatment["reported"]
                                       and control["reported"] and treatment["sd"] is not None
                                       and control["sd"] is not None),
                "treatment": comparison["treatment"],
                "control": comparison["control"],
            })

    reporting = []
    counts = {"reported": 0, "not_reported": 0, "missing_record": 0}
    for study in context.studies():
        record = primary["reporting"][study]
        for key in counts:
            counts[key] += record["counts"][key]
        reporting.append({
            "study_id": study,
            "counts": record["counts"],
            "complete": record["complete"],
            "records": record["records"],
        })

    overlap_pairs = sensitivity["duplicate_reports"]["pairs"]
    warnings = []
    if not groups:
        warnings.append("所选范围内没有可按量表合并的研究；只能并列查看原始比较。")
    for group in groups:
        if not group["poolable"]:
            warnings.append("量表 " + group["scale_id"] + " 的可合并研究不足2项，不能估计总体效应与研究间差异。")
    if overlap_pairs:
        warnings.append("检测到跨研究共用学员：" + "、".join(
            pair["studies"][0] + "↔" + pair["studies"][1] for pair in overlap_pairs)
            + "。重复证据不能各算一次；处理方式见下方敏感性分析。")
        if overlap_policy == "all":
            warnings.append("当前选择把重叠研究当作独立证据；这会重复计算同一批学员，"
                            "权重与精度偏乐观，不应作为默认结论。")
        elif overlap_policy == "strict":
            warnings.append("当前选择剔除了所有与其他研究有重叠的研究；"
                            "剩余证据可能很少，估计范围较窄。")
    if counts["not_reported"]:
        warnings.append("存在已声明但未报告的结果；这些位置按缺失处理，不当作零效应。")
    if counts["missing_record"]:
        warnings.append("存在缺少对应记录的位置；这些位置与未报告、零效应三者含义不同。")
    if any(record["complete"] is False for record in reporting):
        if reporting_policy == "complete_only":
            warnings.append("部分研究在所选范围内未完整报告；当前只合并完整报告的研究。")
        else:
            warnings.append("部分研究在所选范围内未完整报告；当前按可用配对合并，"
                            "未报告位置不补零，但完整性差异本身仍是偏倚来源。")
    if time_policy == "all_weeks":
        correlation = sensitivity["correlation_assumption"]
        if correlation.get("applicable"):
            warnings.append("当前策略把多个时间点写入同一研究效应，使用了重复测量相关假设；"
                            "该值不是观测值，敏感性分析列出不同假设。")
    if len(groups) > 1:
        warnings.append("所选结果出现在多个量表上；各量表分别合并，没有跨量表的总平均数。")

    notes = [
        "合并条件：同一结果、同一量表、同一时间范围，处理组与对照组均报告均值与离散程度；"
        "不满足条件的单位只并列呈现，保留“未报告 / 缺记录 / 无离散程度”的区别。",
        "每项研究只取一个比较。共用同一对照的多个比较不能同时计入；当前按研究编号取"
        + ("第一个" if contrast_rule == "first" else "最后一个") + "比较，"
        "并列出替换其他比较后的结果。",
        "共用学员的研究视为可疑重复证据。"
        + {"independent": "当前按研究编号依次保留互不重叠的研究，其余并列显示。",
           "all": "当前忽略重叠、全部纳入；这是敏感性情形，不是主分析。",
           "strict": "当前剔除任何有重叠的研究。"}[overlap_policy]
        + "三种处理并列可查。",
        "已声明但未报告（not_reported）与缺少记录（missing_record）都不是零效应，"
        "不进入任何合并的平均数。",
        "固定效应与随机效应都给出；随机效应使用 DerSimonian–Laird 估计 tau²，"
        "方法选择本身也会影响区间与显著性，两种结果不应只取一个。",
        "不同量表没有换算依据，不能合并为同一个平均数。",
        "重复测量相关性未知时，范围内多个时间点的合并依赖假设；"
        "假设比较不是观测，也不表示相关性已经被测量。",
        "总体效应是观察性描述，不替代研究设计、偏倚评估或业务决定。",
    ]

    return {
        "skipped": primary["skipped"],
        "outcome": {"id": outcome["id"], "direction": outcome["direction"],
                    "scales": outcome.get("scales", [])},
        "scope": {
            "week_from": week_from, "week_to": week_to,
            "weeks_available": context.weeks,
            "time_policy": time_policy,
            "model": model,
            "reporting_policy": selection.get("reporting_policy", "complete_only"),
            "overlap_policy": selection.get("overlap_policy", "independent"),
            "contrast_rule": contrast_rule,
            "rho": rho if time_policy == "all_weeks" else None,
        },
        "groups": groups,
        "parallel": parallel,
        "reporting": {"by_study": reporting, "totals": counts},
        "sensitivity": {
            "rows": sensitivity["rows"],
            "leave_one_out": sensitivity["leave_one_out"],
            "duplicate_reports": sensitivity["duplicate_reports"],
            "contrast_choice": sensitivity["contrast_choice"],
            "reporting_policy": sensitivity["reporting_policy"],
            "correlation_assumption": sensitivity["correlation_assumption"],
        },
        "warnings": warnings,
        "notes": notes,
    }
