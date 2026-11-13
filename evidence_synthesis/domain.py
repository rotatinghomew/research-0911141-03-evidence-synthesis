from collections import Counter, defaultdict
from statistics import mean, median
import math

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
    for record in data["means"]:
        if record["reported"]:
            finite(record["mean"])
            if record["sd"] is not None:
                finite(record["sd"], minimum=0)
        elif record["mean"] is not None or record["sd"] is not None:
            raise ValueError("未报告的结果不能包含统计量")


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
    return {"summary": {"比较数": len(data["comparisons"]), "结果条目": len(rows), "未报告配对": sum(not row["reported_pair"] for row in rows)},
            "rows": rows, "series": [{"label": row["comparison_id"] + ":" + str(row["week"]), "y": row["raw_difference"]} for row in rows],
            "notes": ["不同时间、量表和共享组别在此并列列出，不作独立性假定。", "原始差值不是跨研究合并效应。"]}
