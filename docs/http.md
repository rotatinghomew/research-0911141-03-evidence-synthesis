# 本地接口

服务只用于本地教研复核，不连接真实人员目录或外部数据源。数据以 UTF-8 JSON 传输；未找到对象返回 404，无效业务输入返回 400，正文超出 8MB 返回 413。

- `GET /health` 返回就绪状态。
- `GET /api/context` 返回产品配置、默认版本、已导入版本与保存记录目录。
- `GET /api/data?revision=版本` 返回该版本完整原始数据；省略版本使用演示材料。
- `POST /api/datasets` 接收 `label` 和完整 `data`，返回新建或复用的 `revision`。
- `POST /api/explore` 接收可选 `revision` 和 `selection`，返回数据指纹、实际选择和领域描述。
- `POST /api/notebooks` 使用相同选择参数和 `title`，服务端重新计算后保存，不能由客户端伪造结果。
- `GET /api/notebooks` 返回材料目录，`GET /api/notebooks/编号` 返回冻结描述、选择条件和所用数据。

## 选择条件

`selection` 除结果指标外可包含：

| 字段 | 取值 | 默认 | 含义 |
| --- | --- | --- | --- |
| `week_from` / `week_to` | 整数 | 数据中的最小/最大时间点 | 只看该时间窗口 |
| `time_policy` | `latest` / `all_weeks` | `latest` | 每研究取最后时间点，或把窗口内各时间点合并 |
| `model` | `random` / `fixed` | `random` | 展示总体效应时采用哪一种；两种结果始终都算 |
| `contrast_rule` | `first` / `last` | `first` | 研究内多个比较只能取一个 |
| `reporting_policy` | `complete_only` / `available_case` | `complete_only` | 是否要求研究在窗口内完整报告 |
| `overlap_policy` | `independent` / `all` / `strict` | `independent` | 共用学员的研究如何处理 |
| `rho` | 0–1 | 声明范围中点 | `all_weeks` 时的重复测量相关假设 |

未知取值、颠倒的时间窗口或超出 0–1 的 `rho` 返回 400。选择的每个字段都记入保存材料，重开时原样重现。

## 返回结构

领域描述包含 `summary`、`rows`、`series`、`notes` 和 `synthesis`。`synthesis` 的主要字段：

- `outcome` / `scope`：结果方向与实际生效的选择条件（含 `rho` 是否被使用）。
- `groups[]`：按 `scale_id` 分组。`poolable` 表示该组研究数是否足够；`pooled` 含总体效应、区间、p、固定与随机效应、Q、I²、tau²、预测区间与权重；`contributions[]` 列出每项纳入研究的效应、权重和它来自的原始组别（`treatment_arm` / `control_arm`）；`excluded[]` 列出未纳入研究与原因；`applicability` 给出研究、队列、人数、时间点与方向解释；`robustness` 汇总敏感性变体是否改变方向或显著性。
- `parallel[]`：所有比较 × 所选时间点的并列结果，保留 `not_reported`、`missing_record`、`no_dispersion` 等状态与 `raw_difference`。
- `reporting`：每个研究的已报告 / 已声明未报告 / 缺记录计数，以及逐条状态。
- `sensitivity`：留一研究、重复报告、报告选择、研究内比较规则、报告完整性策略、相关假设的重算结果。
- `warnings` / `notes`：当前选择下的限制与合并条件。

这里记录的是已有浏览交互，不限制新分析功能的内部结构。增添业务能力时需保持现有原始记录和保存材料可读取。
