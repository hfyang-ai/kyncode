# KynCode Evals

KynCode 的自动化测评框架。用 40 道高难度题覆盖 KynCode 的核心能力（多文件复合 bug 定位、非平凡实现补全、跨文件重构、失败测试驱动修复、导入错误恢复、算法、并发、解析、性能优化、长任务、权限约束、事件钩子约束），驱动 agent headless 执行，自动评分并产出指标报告。

## 目录结构

```
evals/
├── datasets/            # 题面数据（按类别归组）
│   ├── bugfix/          # 多文件复合 bug 定位（4 题）
│   ├── feature/         # 非平凡实现补全（4 题）
│   ├── refactor/        # 跨文件重构保行为（3 题）
│   ├── debug/           # 失败测试驱动修复（4 题）
│   ├── tool_recovery/   # 导入错误恢复（3 题）
│   ├── algorithm/       # 算法实现（4 题）
│   ├── concurrency/     # 并发（4 题）
│   ├── parsing/         # 解析/状态机（4 题）
│   ├── optimization/    # 性能优化（4 题）
│   ├── long_task/       # 长任务（2 题）
│   ├── permission/      # 权限约束下完成任务（2 题）
│   └── hook/            # 钩子约束下完成任务（2 题）
│       └── <task_id>/
│           ├── setup/   # 初始代码（agent 的初始工作目录）
│           └── tests/   # 隐藏测试（grader 才用，agent 不可见）
├── tasks/               # 每道题的任务定义 <task_id>.yaml（prompt + 判分配置）
├── scripts/
│   └── generate_tasks.py  # 数据驱动生成 40 道题（幂等）
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
# 1. 生成 40 道题（幂等，可重复执行）
python scripts/generate_tasks.py

# 2a. 离线 mock 模式：不调真实 API，用合成数据跑通全流程并出示例报告
python runner.py --mock

# 2b. 真实模式：调用 config.yaml 里 agent_bin 指定的 CLI（默认 kyncode）
python runner.py --live --category bugfix

# 3. 只看报告（从已有结果目录重算）
python runner.py --report-only results/<run_id>
```

## 任务类别

共 40 题，全部为高难度（`difficulty: hard`）：

| 类别 | 数量 | 考察点 |
| --- | --- | --- |
| `bugfix` | 4 | 多文件复合 bug 跨层追根（类型污染 / 状态机非法转换 / 缓存引用泄漏 / 递归遍历） |
| `feature` | 4 | 非平凡实现（任务调度器 / 配置合并 / 事件总线 / semver 比较） |
| `refactor` | 3 | 跨文件重构保行为（模板方法 / 过程式转类 / 拆模块消循环依赖） |
| `debug` | 4 | 失败测试驱动修复（快排分区 / 闰年二月 / 贪心找零 / 字节解码） |
| `tool_recovery` | 3 | 导入错误恢复（三方循环导入 / 包内相对导入 / 弃用 API shim） |
| `algorithm` | 4 | 算法实现（拓扑排序 / 并查集 / 滑动窗口 / 区间合并） |
| `concurrency` | 4 | 并发（线程安全计数器 / 线程安全 KV / asyncio 聚合 / 生产者消费者） |
| `parsing` | 4 | 解析/状态机（表达式求值 / INI / 有限状态机 / CSV） |
| `optimization` | 4 | 性能（两数之和 / 前缀和 / 斐波那契 / 有序去重，大数据量约束复杂度） |
| `long_task` | 2 | 长任务（多模块迷你框架 + 长流水线交叉依赖） |
| `permission` | 2 | 权限规则约束下完成任务（deny 写受保护文件 / deny 读敏感文件） |
| `hook` | 2 | 钩子约束下完成任务（reject Bash / reject WriteFile） |

## 任务格式

每道题 `tasks/<task_id>.yaml`：

```yaml
id: bugfix_001
category: bugfix            # bugfix | feature | refactor | debug | tool_recovery | algorithm | concurrency | parsing | optimization | long_task | permission | hook
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

每次测评产出 `results/<run_id>/report.md`，**默认由 LLM 生成**（`report_llm.py`），
失败时自动回退到规则模板（`report.py`）。

### LLM 报告（默认）

把结构化测评数据（实验信息 + 指标 + 分类明细 + 逐任务结果 + **执行轨迹**）传给大模型，
生成 10 部分报告：

1. 实验信息 · 2. 执行摘要 · 3. 核心指标 · 4. 任务分类分析 · 5. 版本对比 ·
6. **失败分析**（每个失败任务一条可追踪记录：失败分类[主+次标签]、直接证据[来自工具轨迹/测试日志]、
根因假设、后续行动）· 7. 成本效率 · 8. 可靠性与有效性 · 9. 后续行动（可验证改进项）· 10. 附录。

失败分析采用统一 8 类分类体系（Planning / Context / Tool / Recovery / Coding /
Verification / Budget / Infrastructure Failure），并在末尾给「失败分类汇总表」，
回答究竟哪种问题导致了最多的任务失败。

- LLM provider 复用被测 agent config 里的第一个 openai-compat / openai provider。
- 执行轨迹来自 stream-json 的 `tool_use`（含参数）+ `tool_result`（含输出/是否报错），
  由 `adapter.parse_trace` 提取，失败任务保留末尾 30 个工具调用。
- 禁用 LLM（离线/调试）加 `--no-llm-report`，回退到规则模板。

### 规则模板（回退）

- 追踪指标汇总表（含实测数据）
- 分类别明细（bugfix / feature / refactor / debug / tool_recovery / algorithm / concurrency / parsing / optimization / long_task / permission / hook）
- 基础设施故障清单
- 未解决任务清单 + 失败原因
- **项目功能不足总结**（从失败任务归因出的能力短板）

## Terminal-Bench 接入（真实终端任务）

除自建合成题外，`evals` 还接入了 [Terminal-Bench](https://github.com/laude-institute/t-bench)
（Stanford + Laude Institute 的终端 agent 基准），用 Docker 容器跑真实 Linux 终端任务
（编译 / 配置服务 / 数据处理 / 安全审计 / 科学计算等），测 kyncode 的**终端自主执行**能力。

### 与自建题的差异

| 维度 | 自建合成题 | Terminal-Bench |
| --- | --- | --- |
| 任务来源 | `generate_tasks.py` 生成 | 真实任务（241 个，官方 curated） |
| 执行环境 | 本地 workspace | Docker 容器（Linux，arm64 原生） |
| 判分 | 隐藏 pytest（F2P/P2P） | 容器内 `run-tests.sh`（pytest，全过 = resolved） |
| 测的能力 | 编码 / 重构 / 算法等 | 终端操作 / 系统管理 / 网络 / 数据管道 |

### 前置条件

1. **Docker** 已启动。
2. **基础镜像**：任务 `Dockerfile` 的 `FROM` 指向 `ghcr.io`，需先通过加速器拉取并 tag 成原名
   （`ghcr.nju.edu.cn` 提供 arm64 镜像，Apple Silicon 原生运行，无需 QEMU）：

```bash
# 拉取两个常用基础镜像（走 ghcr 加速器，避免直连断流）
docker pull ghcr.nju.edu.cn/laude-institute/t-bench/python-3-13:20250620
docker pull ghcr.nju.edu.cn/laude-institute/t-bench/ubuntu-24-04:20250624
# tag 成 ghcr.io 原名，让任务 Dockerfile 的 FROM 命中本地镜像
docker tag ghcr.nju.edu.cn/laude-institute/t-bench/python-3-13:20250620 ghcr.io/laude-institute/t-bench/python-3-13:20250620
docker tag ghcr.nju.edu.cn/laude-institute/t-bench/ubuntu-24-04:20250624 ghcr.io/laude-institute/t-bench/ubuntu-24-04:20250624
```

3. **给 ubuntu 基础镜像装 pip**（官方 ubuntu-24-04 镜像无 pip，kyncode 安装需要）：

```bash
mkdir -p /tmp/ubuntupip && cat > /tmp/ubuntupip/Dockerfile <<'EOF'
FROM ghcr.io/laude-institute/t-bench/ubuntu-24-04:20250624
RUN apt-get update && apt-get install -y python3-pip && rm -rf /var/lib/apt/lists/*
EOF
docker build -t ghcr.io/laude-institute/t-bench/ubuntu-24-04:20250624 /tmp/ubuntupip
```

4. **kyncode wheel**：先 `cd .. && uv build` 生成 `dist/kyncode-0.1.0-py3-none-any.whl`。
5. **任务数据**：`--tasks-root` 指向 Terminal-Bench 任务目录（含 `task.yaml` / `Dockerfile` /
   `tests/` / `run-tests.sh`），默认 `/tmp/tb_repo/original-tasks`。

### 用法

```bash
# 列出任务（默认任务根目录 /tmp/tb_repo/original-tasks）
python terminal_bench_runner.py --list

# 跑单个任务（验证全链路）
python terminal_bench_runner.py --tasks hello-world

# 跑指定类别 / 数量
python terminal_bench_runner.py --category file-operations --n 5

# 复用已构建镜像（跳过 build）
python terminal_bench_runner.py --tasks hello-world --no-rebuild
```

产出与自建题一致：`results/<run_id>/report.md`（八部分报告，复用同一套 `compute_metrics` +
`render_report`）。

### 实现（复用现有 adapter + 报告链路）

- `terminal_bench.py`：数据层（解析 `task.yaml`）+ 容器执行器
  （构建期预装 kyncode → compose 起容器 → 注入精简 config → headless 跑 → 跑 `run-tests.sh` 判分）。
- `terminal_bench_runner.py`：CLI 入口，结果适配成 `TaskOutcome` 后复用
  `metrics.compute_metrics` + `report.render_report`。
- kyncode headless 协议与自建题一致（`kyncode -p "<instruction>" --output-format stream-json`），
  复用 `adapter._parse_ndjson` 解析。
- 判分复现官方 `PytestParser`：解析 pytest 输出的 `short test summary info` 段，
  `resolved = 所有测试 PASSED`（与 leaderboard 口径一致，可对外比）。

#### 构建期预装 kyncode（对齐官方 AbstractInstalledAgent）

kyncode 在**镜像构建期**就预装进 client 镜像（而非运行时 pip 装），好处有二：

1. **支持「破坏环境」类任务**（如 `broken-python` 故意删掉 pip/setuptools/wheel）：kyncode
   在任务 Dockerfile 删 pip **之前**装好，运行时无需 pip，agent 才能自己修环境。
2. **build 走 `--network=host` + 失败重试**，规避 compose bridge 网络下 apt 源的偶发抖动
   （如 `aimo-airline-departures` 的 `apt-get update` 断流）。

流程：解析 compose 的 client build context → 读 Dockerfile 提取 `FROM` 基础镜像 →
为每个基础镜像构建「base + kyncode」缓存镜像（`tb-kyncode-base:<sha256>`）→
生成 wrapper Dockerfile（仅替换 `FROM` 行，其余指令照常）→ host 网络 build client 镜像 →
`compose up -d`（不带 build）→ 运行时只注入 config（API key 不进镜像层）。

**fallback 边界**（以下情况退回运行时 pip 装，维持现状）：多 build 服务任务、
多阶段 Dockerfile、`--platform=linux/amd64`、`FROM` 带 ARG 变量、localstack/taichi 等特殊基础镜像。

> 注意：判分口径为「测试全过 = resolved」，不区分 F2P/P2P；报告里「回归通过率」对
> Terminal-Bench 不适用（恒为 0），看「任务解决率」即可。
