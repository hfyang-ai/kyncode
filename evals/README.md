# KynCode Evals

KynCode 的自动化测评框架。用 50 道题覆盖 KynCode 的核心能力（文件读取、跨文件修改、Bug 修复、测试失败处理、工具错误恢复、长上下文），驱动 agent headless 执行，自动评分并产出指标报告。

## 目录结构

```
evals/
├── datasets/            # 题面数据（按类别归组）
│   ├── basic/           # 基础：文件读取 + 跨文件修改（15 题）
│   ├── bugfix/          # Bug 修复 + 测试失败处理（15 题）
│   ├── tool_error/      # 工具错误恢复（5 题）
│   ├── long_context/    # 长上下文（10 题）
│   └── refactor/        # 重构 / 多文件综合（5 题）
│       └── <task_id>/
│           ├── setup/   # 初始代码（agent 的初始工作目录）
│           └── tests/   # 隐藏测试（grader 才用，agent 不可见）
├── tasks/               # 每道题的任务定义 <task_id>.yaml（prompt + 判分配置）
├── scripts/
│   └── generate_tasks.py  # 数据驱动生成 50 道题（幂等）
├── tasklib.py           # 核心模型：任务加载 / 状态机 / 结果 dataclass
├── adapter.py           # Agent Adapter：统一 CLI 调用接口（Protocol）
├── environment.py       # Environment Manager：隔离工作区 + 初始快照 + diff
├── orchestrator.py      # Evaluation Orchestrator：状态机编排 + 故障归因
├── store.py             # Run Store：manifest / summary / trace / 产物落盘
├── grader.py            # 独立判分（分层：执行有效性 / 修改约束 / 功能正确性）
├── metrics.py           # 指标统计
├── report.py            # Markdown 报告生成
├── runner.py            # CLI 入口（参数解析 + 驱动编排）
├── results/             # 每次测评的输出（见下方「产物目录」）
├── config.yaml          # 测评配置
└── pyproject.toml
```

## 快速开始

```bash
# 1. 生成 50 道题（幂等，可重复执行）
python scripts/generate_tasks.py

# 2a. 离线 mock 模式：不调真实 API，用合成数据跑通全流程并出示例报告
python runner.py --mock

# 2b. 真实模式：调用 config.yaml 里 agent_bin 指定的 CLI（默认 kyncode）
python runner.py --live --category bugfix

# 3. 只看报告（从已有结果目录重算）
python runner.py --report-only results/<run_id>
```

## 任务格式

每道题 `tasks/<task_id>.yaml`：

```yaml
id: bugfix_001
category: bugfix            # basic | bugfix | tool_error | long_context | refactor
kind: code_change           # code_change | answer_match | file_assert
title: 修复除零错误
prompt: |                   # 给 agent 的自然语言指令
  在 work.py 的 divide 函数里，当除数为 0 时会崩溃，请修复。
timeout_seconds: 300
max_retries: 0              # 允许的重试次数（0 = 只跑一次）
grader:
  type: fail_to_pass        # fail_to_pass | answer_match | file_assert
  test_group: tests         # 隐藏测试所在的 pytest 标记/目录
  forbidden_files: []       # 可选：禁止 agent 修改的文件（相对 setup 根）
```

- `kind=code_change`：真实代码修改任务，用隐藏测试评分。
- `kind=answer_match`：能力探测（文件读取 / 问答），按答案匹配评分。
- `kind=file_assert`：按最终文件内容断言评分。

## 核心指标

| 指标 | 计算方式 | 用途 |
| --- | --- | --- |
| 任务解决率 | 解决任务数 ÷ 有效提交任务数 | 核心能力 |
| Pass@1 | 单次尝试成功任务数 ÷ 任务数 | 单次执行成功率 |
| 多次尝试成功率 | 最终（含重试）成功任务数 ÷ 任务数 | 重试带来的收益 |
| 回归通过率 | PASS_TO_PASS 全过的任务数 ÷ 有效任务数 | 防止破坏旧功能 |
| 超时率 | 超时任务数 ÷ 任务总数 | 执行稳定性 |
| 平均耗时 | 所有任务耗时总和 ÷ 任务数 | 效率 |
| 平均 Token | 总 Token ÷ 任务数 | 模型开销 |
| 单任务成本 | 可计量成本总和 ÷ 任务数 | 预算控制 |

两个关键约定：

1. **Pass@1 与多次尝试分开报告**：若某任务允许重试，第 1 次尝试成功才计入 Pass@1；重试后成功只计入「多次尝试成功率」，不冒充实为单次成功。
2. **基础设施故障单独统计**：Docker 启动失败、API 服务中断等基础设施问题单独列一行，既不算进 Agent 能力失败，也不计入「有效提交任务数」的分母。

## 隐藏测试分组（真实代码修改任务）

- **FAIL_TO_PASS (F2P)**：修复前失败、正确修复后应通过的测试——衡量「修没修对」。
- **PASS_TO_PASS (P2P)**：修复前后都应通过的回归测试——衡量「有没有改坏别处」。

一个任务 `resolved` 当且仅当：**所有 F2P 通过 && 所有 P2P 通过**。

## 执行状态机

每个任务按状态机推进，终止状态严格区分四种性质：

| 状态 | 含义 |
| --- | --- |
| `completed` | 评分器已完成判分（可能通过，也可能未通过） |
| `agent_error` | Agent 本身运行失败（内部异常 / 无效工具调用终止） |
| `infra_error` | 环境 / 网络 / 镜像构建等基础设施出错，无法可靠判定 Agent 能力 |
| `timeout` | 超时（单独记录，不并入上面三类） |

基础设施故障任务**不计入 Agent 能力统计**，但在报告中单独披露数量和比例，不会悄悄消失。

## 产物目录

每次测评产出 `results/<run_id>/`：

```
results/<run_id>/
├── manifest.json          # 完整实验配置（agent 版本 / 模型 / 数据集版本 / 配置 / 任务清单）
├── summary.json           # 运行汇总（状态分布 / 解决数）
├── grades.json            # 评分结果（向后兼容 --report-only）
├── metrics.json           # 指标汇总
├── report.md              # Markdown 报告
└── tasks/<task_id>/
    ├── trace.jsonl        # 结构化事件流（attempt_started / retry / agent_finished / ...）
    ├── agent_stdout.attempt<N>.log   # Agent 原始 stdout（分存，不混入内存）
    ├── agent_stderr.attempt<N>.log   # Agent 原始 stderr
    ├── patch.attempt<N>.diff         # Agent 生成的补丁（相对初始快照）
    ├── grader.attempt<N>.json        # 独立评分结果（与执行事实分离）
    └── attempts/<N>/
        ├── work/          # agent 工作区（setup 副本）
        └── snapshot/      # setup 不可变快照（用于 diff / 复现）
```

关键设计：**attempt（执行事实）与 result（评分判定）分离**——agent 日志与补丁
先落盘，再由独立 Grader 判定，二者互不依赖，便于排查重试与复评。

## 报告

每次测评产出 `results/<run_id>/report.md`，包含：

- 追踪指标汇总表（含实测数据）
- 分类别明细（basic / bugfix / tool_error / long_context / refactor）
- 基础设施故障清单
- 未解决任务清单 + 失败原因
- **项目功能不足总结**（从失败任务归因出的能力短板）
