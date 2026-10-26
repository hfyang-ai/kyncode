# KynCode 测评报告

## 实验信息

| 项 | 值 |
| --- | --- |
| Run ID | `20261026-013709` |
| 被测 Agent | `kyncode` |
| Agent 版本 | `未标注` |
| 模型 | `未标注` |
| 执行模式 | mock |
| 数据集版本 | 未版本化 |
| 任务总数 | 50 |
| 生成时间 | 2026-10-26 09:37:09 |

## 一、总体摘要

本次测评在 50 道任务上对 `kyncode` 进行了评估，任务解决率 **64.6%**（31/48 有效任务）。总体结论：**整体表现一般，存在明显短板需要针对性加固**。

| 任务解决率 | 回归通过率 | P50 耗时 | 单任务成本 |
| --- | --- | --- | --- |
| **64.6%** (31/48) | **86.5%** | **94.3s** | **未配置价格** |

**主要发现**：

- 分类别表现最弱的是 **long_context**：解决率 20.0%（2/10），是优先优化方向。
- 失败最集中的原因是 **Coding Failure**（12 题），详见「失败分析」。

**主要风险**：

- 存在**回归破坏**：`basic_013, basic_015, bugfix_011, long_context_010, refactor_004` 在修改过程中改坏了原有功能。
- 2 道题因基础设施故障未计入能力统计，需复核环境稳定性。
- 2 道题超时，长尾耗时（P95）需关注。

## 二、核心指标

**正确性**：

| 指标 | 实测值 | 说明 |
| --- | --- | --- |
| 任务解决率 | 64.6% | 解决任务 ÷ 有效任务 |
| Pass@1 | 62.0% | 单次尝试成功任务 ÷ 任务总数 |
| 多次尝试成功率 | 62.0% | 含重试的最终成功率 |
| 回归通过率 | 86.5% | P2P 回归测试通过率 |

**效率**：

| 指标 | 实测值 | 说明 |
| --- | --- | --- |
| 平均耗时 | 109.3s | 所有任务耗时均值 |
| P50 耗时 | 94.3s | 单任务中位数（更能反映典型体验） |
| P95 耗时 | 179.6s | 长尾任务（揭示慢任务） |
| 平均 Token | 19096 | 总 Token ÷ 任务数 |
| 平均 LLM 请求 | 7.2 | 每任务 agent 轮次（近似请求数） |
| 平均工具调用 | 7.2 | 每任务工具调用次数 |
| 单任务成本 | 未配置价格 | 全部有效运行的成本均值 |

**稳定性**：

| 指标 | 实测值 | 说明 |
| --- | --- | --- |
| 超时率 | 4.0% | 超时任务 ÷ 任务总数 |
| 基础设施错误率 | 4.0% | 基础设施故障 ÷ 任务总数 |

实测统计：总任务 **50** / 有效任务 **48** / 解决 **31** / 基础设施故障 **2** / 超时 **2**。
Token 开销：总 **954801**（输入 738896 / 输出 215905）；LLM 请求共 360 次，工具调用共 360 次。

> 口径说明：任务解决率 / 回归通过率的分母为**有效任务数**（总任务数 - 基础设施故障任务数）；Pass@1 与多次尝试成功率分开报告，重试后的成功不计入 Pass@1；基础设施故障与超时单独统计。

## 三、任务分类分析

| 类别 | 样本数 | 解决数 | 解决率 |
| --- | --- | --- | --- |
| basic | 13/15 | 10 | 76.9% |
| bugfix | 15/15 | 12 | 80.0% |
| tool_error | 5/5 | 4 | 80.0% |
| long_context | 10/10 | 2 | 20.0% |
| refactor | 5/5 | 3 | 60.0% |

> 排查方向（非因果结论，需结合失败轨迹验证）：
- **long_context** 较弱（20.0%），检查上下文压缩、历史保留与关键信息丢失。

> 样本数以 `N/M` 展示（N = 有效任务，M = 该类别总题数）；样本过少时比例的参考价值有限。

## 四、失败分析

**失败分类汇总**（启发式推断，真实根因需结合 trace.jsonl / grader.json）：

| 失败类型 | 任务数 | 任务 |
| --- | --- | --- |
| Coding Failure | 12 | `basic_013`, `basic_015`, `bugfix_003`, `bugfix_006`, `bugfix_011`, `long_context_001` |
| Planning Failure | 3 | `basic_002`, `long_context_006`, `tool_error_001` |
| Infrastructure Failure | 2 | `basic_005`, `basic_014` |
| Budget / Timeout | 2 | `long_context_004`, `long_context_005` |

**代表性失败案例**：

**`basic_013`** — Coding Failure

- 类别：`basic` · 修改接口签名并更新调用方
- 直接证据：mock: 修复过程中破坏了原有功能（P2P 失败）
- 失败测试：test_p2p_regression
- 尝试次数：1

**`basic_002`** — Planning Failure

- 类别：`basic` · 统计多文件总行数
- 直接证据：mock: answer mismatch
- 尝试次数：1

**`basic_005`** — Infrastructure Failure

- 类别：`basic` · 读取深层嵌套文件
- 直接证据：infra failure: API/Docker 服务不可用
- 报错信息：API service unavailable (503)
- 尝试次数：1

**`long_context_004`** — Budget / Timeout

- 类别：`long_context` · 汇总多模块公开函数数
- 直接证据：timed out (> 300s)
- 尝试次数：1


## 五、成本与效率

| 维度 | 指标 | 实测值 |
| --- | --- | --- |
| 耗时 | P50 / P95 | 94.3s / 179.6s |
| 耗时 | 平均 | 109.3s |
| Token | 输入 / 输出 | 738896 / 215905 |
| 请求 | LLM 请求总数 | 360 |
| 工具 | 工具调用总数 | 360 |

**成本**：

| 口径 | 金额 |
| --- | --- |
| 每次尝试成本 | 未配置价格 |
| 每个成功任务成本 | 未配置价格 |
| 单任务成本（全部有效运行） | 未配置价格 |
| 总成本 | 未配置价格 |

> 未配置 `pricing`（config.yaml 的 `input_per_1m` / `output_per_1m`），成本显示为「未配置价格」。

> 关注「每个成功任务成本」而非只看单任务成本：失败任务较多时，后者会低估真实交付成本。成功率提升是否值得额外成本，需结合任务重要性与延迟要求权衡。

> 逐任务的耗时 / Token / 请求 / 工具调用明细见「附录：逐任务结果」。

## 六、可靠性与评测有效性

**本次测评边界**：

- 样本量：**50** 道任务（自建测评集）。样本有限，几个百分点的变化不宜直接解读为显著提升。
- 结果分布：成功 31 / 能力失败 15 / 基础设施异常 2 / 超时 2 / Agent 异常终止 0。
- 评分器：分层判分（执行有效性 → 修改约束 → 功能正确性 F2P/P2P），与 Agent 解耦、独立判分。
- 隐藏测试：F2P（FAIL_TO_PASS）与 P2P（PASS_TO_PASS）按文件名前缀 `test_f2p_*` / `test_p2p_*` 分组，Agent 不可见。

**覆盖范围**：

- 已评估：文件读取、跨文件修改、Bug 修复、测试失败处理、工具错误恢复、长上下文、重构。
- 未评估：子 agent 委派、多 agent team、权限与钩子、checkpoint 恢复、LLM 降级 failover、真实仓库（SWE-bench）。

> ⚠️ 未标注 Agent 版本（config.yaml 的 `agent_version` 为空），不同运行之间无法可靠对比。


## 七、改进建议

| 发现 | 改进动作 | 验收方式 |
| --- | --- | --- |
| long_context 类任务解决率偏低（20.0%） | 针对该类任务优化 system prompt / 工具集 / 上下文策略 | long_context 类任务解决率提升且不引入回归 |
| 代码实现或回归破坏 | 加强修改前规划与回归自检 | F2P/P2P 通过率提升 |
| 出现回归破坏（5 题） | 分析版本差异与失败轨迹 | 回归任务重新通过 |

> 每项建议建议指定优先级与复测任务集，形成「发现问题 → 改进实现 → 回归验证」闭环。

## 附录：逐任务结果

| 任务 | 类别 | 状态 | 耗时 | Token | LLM 请求 | 工具调用 | 尝试 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `basic_001` | basic | completed | 164.8s | 9822 | 6 | 6 | 1 |
| `basic_002` | basic | completed | 16.2s | 12488 | 9 | 9 | 1 |
| `basic_003` | basic | completed | 55.8s | 24304 | 2 | 2 | 1 |
| `basic_004` | basic | completed | 153.0s | 32870 | 3 | 3 | 1 |
| `basic_005` | basic | infra_error | 177.8s | 10992 | 11 | 11 | 1 |
| `basic_006` | basic | completed | 164.5s | 30410 | 10 | 10 | 1 |
| `basic_007` | basic | completed | 65.8s | 4704 | 3 | 3 | 1 |
| `basic_008` | basic | completed | 174.7s | 14921 | 5 | 5 | 1 |
| `basic_009` | basic | completed | 61.8s | 20135 | 11 | 11 | 1 |
| `basic_010` | basic | completed | 37.0s | 24523 | 12 | 12 | 1 |
| `basic_011` | basic | completed | 130.1s | 4201 | 12 | 12 | 1 |
| `basic_012` | basic | completed | 88.6s | 30522 | 6 | 6 | 1 |
| `basic_013` | basic | completed | 97.1s | 30028 | 11 | 11 | 1 |
| `basic_014` | basic | infra_error | 75.7s | 24267 | 8 | 8 | 1 |
| `basic_015` | basic | completed | 159.1s | 14221 | 9 | 9 | 1 |
| `bugfix_001` | bugfix | completed | 167.7s | 21708 | 6 | 6 | 1 |
| `bugfix_002` | bugfix | completed | 60.4s | 27952 | 8 | 8 | 1 |
| `bugfix_003` | bugfix | completed | 117.2s | 14759 | 8 | 8 | 1 |
| `bugfix_004` | bugfix | completed | 49.3s | 24820 | 12 | 12 | 1 |
| `bugfix_005` | bugfix | completed | 54.3s | 31794 | 6 | 6 | 1 |
| `bugfix_006` | bugfix | completed | 90.7s | 33710 | 11 | 11 | 1 |
| `bugfix_007` | bugfix | completed | 120.0s | 20119 | 2 | 2 | 1 |
| `bugfix_008` | bugfix | completed | 79.0s | 22627 | 6 | 6 | 1 |
| `bugfix_009` | bugfix | completed | 52.5s | 30531 | 10 | 10 | 1 |
| `bugfix_010` | bugfix | completed | 114.1s | 15072 | 4 | 4 | 1 |
| `bugfix_011` | bugfix | completed | 89.5s | 5589 | 11 | 11 | 1 |
| `bugfix_012` | bugfix | completed | 29.3s | 11186 | 2 | 2 | 1 |
| `bugfix_013` | bugfix | completed | 179.5s | 31311 | 6 | 6 | 1 |
| `bugfix_014` | bugfix | completed | 167.5s | 10136 | 5 | 5 | 1 |
| `bugfix_015` | bugfix | completed | 39.5s | 22432 | 5 | 5 | 1 |
| `long_context_001` | long_context | completed | 163.4s | 33924 | 5 | 5 | 1 |
| `long_context_002` | long_context | completed | 120.2s | 9458 | 8 | 8 | 1 |
| `long_context_003` | long_context | completed | 109.8s | 16648 | 4 | 4 | 1 |
| `long_context_004` | long_context | timeout | 300.0s | 25747 | 7 | 7 | 1 |
| `long_context_005` | long_context | timeout | 300.0s | 16380 | 9 | 9 | 1 |
| `long_context_006` | long_context | completed | 168.6s | 13798 | 5 | 5 | 1 |
| `long_context_007` | long_context | completed | 11.8s | 13115 | 4 | 4 | 1 |
| `long_context_008` | long_context | completed | 167.3s | 28048 | 3 | 3 | 1 |
| `long_context_009` | long_context | completed | 101.9s | 17396 | 7 | 7 | 1 |
| `long_context_010` | long_context | completed | 179.7s | 4957 | 6 | 6 | 1 |
| `refactor_001` | refactor | completed | 37.3s | 29909 | 9 | 9 | 1 |
| `refactor_002` | refactor | completed | 70.5s | 10291 | 6 | 6 | 1 |
| `refactor_003` | refactor | completed | 131.5s | 5919 | 10 | 10 | 1 |
| `refactor_004` | refactor | completed | 91.4s | 10161 | 3 | 3 | 1 |
| `refactor_005` | refactor | completed | 18.0s | 22493 | 9 | 9 | 1 |
| `tool_error_001` | tool_error | completed | 66.2s | 18397 | 8 | 8 | 1 |
| `tool_error_002` | tool_error | completed | 161.3s | 11265 | 3 | 3 | 1 |
| `tool_error_003` | tool_error | completed | 84.5s | 12483 | 11 | 11 | 1 |
| `tool_error_004` | tool_error | completed | 79.4s | 36840 | 11 | 11 | 1 |
| `tool_error_005` | tool_error | completed | 72.0s | 5418 | 12 | 12 | 1 |
