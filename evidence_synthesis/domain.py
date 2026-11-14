from collections import Counter, defaultdict
from statistics import mean, median
import math

from .synthesis import synthesize
from .validation import require_tables, unique, finite, references


def validate(data):
    require_tables(data, ["arms", "comparisons", "cohorts", "means", "outcomes"])
    unique(data["arms"], ["arm_id"])
    unique(data["cohorts"], ["cohort_id"])
    unique(data["comparisons"], ["comparison_id"])
    unique(data["means"], ["arm_id", "outcome", "week"])
    references(data["arms"], "cohort_id", data["cohorts"], "cohort_id")
    references(data["means"], "arm_id", data["arms"], "arm_id")
    for field in ["treatment", "control"]:
        references(data["comparisons"], field, data["arms"], "arm_id")
    for arm in data["arms"]:
        finite(arm["n"], minimum=1)
    outcome_ids = {record["id"] for record in data["outcomes"]}
    scales_by_outcome = {}
    for outcome in data["outcomes"]:
        if not isinstance(outcome.get("scales"), list):
            raise ValueError("结果指标应声明量表列表")
        scales_by_outcome[outcome["id"]] = set(outcome["scales"])
    for record in data["means"]:
        if record["outcome"] not in outcome_ids:
            raise ValueError("结果记录引用了未声明的结果指标")
        if not isinstance(record["week"], int) or isinstance(record["week"], bool):
            raise ValueError("时间点应为整数")
        if record["scale_id"] not in scales_by_outcome[record["outcome"]]:
            raise ValueError("结果记录的量表未在该结果指标中声明")
        if not isinstance(record["declared"], bool) or not isinstance(record["reported"], bool):
            raise ValueError("声明与报告标记应为布尔值")
        if record["reported"] and not record["declared"]:
            raise ValueError("报告了未声明的结果")
        if record["reported"]:
            finite(record["mean"])
            if record["sd"] is not None:
                finite(record["sd"], minimum=0)
        elif record["mean"] is not None or record["sd"] is not None:
            raise ValueError("未报告的结果不能包含统计量")
    correlations = data.get("correlations", {})
    if not isinstance(correlations, dict):
        raise ValueError("相关性声明必须是对象")
    repeated = correlations.get("same_arm_repeated")
    if repeated is not None:
        if not isinstance(repeated, dict) or not isinstance(repeated.get("known"), bool):
            raise ValueError("重复测量相关性声明不完整")
        if repeated["known"]:
            finite(repeated.get("value"), minimum=0, maximum=1)
        else:
            value_range = repeated.get("range")
            if not (isinstance(value_range, list) and len(value_range) == 2):
                raise ValueError("假设相关性应给出两值范围")
            for value in value_range:
                finite(value, minimum=0, maximum=1)
            if value_range[0] > value_range[1]:
                raise ValueError("相关性范围上下界颠倒")


def explore(data, selection):
    outcome = selection.get("outcome", "completion_score")
    if outcome not in {record["id"] for record in data["outcomes"]}:
        raise ValueError("未知结果指标")
    measures = {(record["arm_id"], record["week"]): record for record in data["means"] if record["outcome"] == outcome}
    rows = []
    for comparison in data["comparisons"]:
        for week in sorted({key[1] for key in measures}):
            treatment = measures.get((comparison["treatment"], week))
            control = measures.get((comparison["control"], week))
            available = bool(treatment and control and treatment["reported"] and control["reported"])
            rows.append({"comparison_id": comparison["comparison_id"], "study_id": comparison["study_id"], "week": week,
                         "reported_pair": available, "scale_id": treatment["scale_id"] if treatment else None,
                         "raw_difference": treatment["mean"] - control["mean"] if available else None,
                         "has_dispersion": bool(available and treatment["sd"] is not None and control["sd"] is not None)})
    synthesis = synthesize(data, selection)
    return {"summary": {"比较数": len(data["comparisons"]), "结果条目": len(rows), "未报告配对": sum(not row["reported_pair"] for row in rows),
                        "可合并量表": sum(1 for group in synthesis["groups"] if group["poolable"]),
                        "并列研究数": len({row["study_id"] for row in rows})},
            "rows": rows, "series": [{"label": row["comparison_id"] + ":" + str(row["week"]), "y": row["raw_difference"]} for row in rows],
            "notes": ["不同时间、量表和共享组别在此并列列出，不作独立性假定。", "原始差值不是跨研究合并效应。"],
            "synthesis": synthesis}
