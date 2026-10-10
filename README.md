# KynCode

一个运行在终端里的 AI 编程助手。它把大语言模型接入命令行，通过丰富的内建工具、权限控制与多 Agent 协作，直接在项目目录里读代码、改文件、跑命令。

## 特性

- **终端 TUI**：基于 [Textual](https://textual.textualize.io/) 的交互式界面，流式输出、权限确认、子任务派发一目了然。
- **多 Provider**：支持 Anthropic、OpenAI 及任意 OpenAI 兼容端点（DeepSeek、Kimi、GLM、MiniMax 等），一处配置自由切换。
- **丰富的内建工具**：文件读写/编辑、Bash、Glob/Grep 检索、联网搜索（WebSearch/WebFetch，带 SSRF 防护）、MCP 扩展、Git worktree 隔离等。
- **MCP 集成**：Model Context Protocol 客户端，支持 stdio 与 Streamable HTTP 两种传输，按 schema 总量智能选择延迟加载策略。
- **四级权限控制**：`default` / `acceptEdits` / `plan` / `bypassPermissions`，配合危险命令检测、路径沙箱与本地规则持久化。
- **Hooks 事件引擎**：在工具调用前后插入自定义钩子，拦截、改写或观察 Agent 行为。
- **子 Agent 与团队协作**：Leader 调度、任务板、消息邮箱，可多 Agent 并行推进同一项目。
- **长期记忆**：跨会话记忆存储与召回，配合上下文窗口自动压缩管理 token 预算。
- **ToolRuntime 三段管线**：`prepare → execute → finalize`，统一处理参数校验、权限审批、超时/重试、结果封装、脱敏与审计。
- **多运行模式**：交互式 TUI、`-p` 非交互（支持 NDJSON 流式输出）、`--remote` 远程 WebSocket 模式。

## 技术栈

- Python >= 3.11
- [uv](https://github.com/astral-sh/uv)（依赖与环境管理）
- Textual（TUI）、pydantic（数据校验）
- pytest + pytest-asyncio（测试）

## 安装

```bash
https://github.com/hfyang-ai/kyncode.git
cd kyncode
uv sync
```

## 快速开始

1. 生成配置文件：

   ```bash
   cp config.yaml.example config.yaml
   ```

2. 编辑 `config.yaml`，填入你的 API Key：

   ```yaml
   providers:
     - name: anthropic-official
       protocol: anthropic
       base_url: https://api.anthropic.com
       api_key: "your-api-key-here"
       model: claude-sonnet-4-20250514
       thinking: true
   ```

3. 启动：

   ```bash
   uv run kyncode
   ```

## 使用

### 交互式 TUI

```bash
uv run kyncode                    # 默认权限模式
uv run kyncode --mode bypassPermissions   # 跳过权限确认（谨慎使用）
```

### 非交互模式

`-p` 直接执行一句指令并打印结果，适合脚本调用或 CI。非交互模式下所有权限请求自动批准。

```bash
uv run kyncode -p "解释一下这个项目的目录结构"
uv run kyncode -p "列出所有 TODO" --output-format stream-json   # NDJSON 流式事件
```

### 远程模式

```bash
uv run kyncode --remote
# 浏览器访问 http://localhost:18888
```

## 配置

配置文件为项目根目录下的 `config.yaml`，完整字段见 `config.yaml.example`。核心配置项：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `providers` | list | LLM Provider 列表，`protocol` 支持 `anthropic` / `openai` / `openai-compat` |
| `permission_mode` | str | 默认权限模式，见下表 |
| `mcp_servers` | list | MCP 服务器配置（stdio 的 `command`/`args` 或 HTTP 的 `url`/`headers`） |
| `raw_hooks` | list | 自定义钩子配置 |
| `web_access` | bool | 是否注册联网工具（WebSearch / WebFetch），默认 `true` |
| `enable_fork` | bool | 是否允许子 Agent fork 出独立进程 |
| `enable_verification_agent` | bool | 是否启用验证 Agent |
| `enable_coordinator_mode` | bool | 团队模式下 Lead 是否只调度、不写代码 |
| `worktree` | object | Git worktree 隔离配置（软链目录、过期清理） |
| `sandbox` | object | OS 级命令沙箱（`enabled` / `auto_allow` / `network_enabled`） |

Provider 支持环境变量引用：`api_key: "${OPENAI_API_KEY}"` 会在加载时解析。

### 权限模式

| 模式 | read | write | command |
| --- | --- | --- | --- |
| `default` | 允许 | 询问 | 询问 |
| `acceptEdits` | 允许 | 允许 | 询问 |
| `plan` | 允许 | 询问 | 询问 |
| `bypassPermissions` | 允许 | 允许 | 允许 |

`default` 与 `plan` 的当前策略一致，区别在于 `plan` 模式会附带只读规划的上下文提醒。权限的「总是允许」决策会写入项目本地规则文件 `.kyncode/permissions.local.yaml`，下次运行即刻生效。

## 内建工具

| 类别 | 工具 |
| --- | --- |
| 文件 | `ReadFile`、`WriteFile`、`EditFile` |
| 检索 | `Glob`、`Grep` |
| 命令 | `Bash` |
| 联网 | `WebSearch`、`WebFetch`（由 `web_access` 开关） |
| 任务 | `TaskCreate`、`TaskGet`、`TaskList`、`TaskUpdate`、`TaskStop` |
| 协作 | `TeamCreate`、`TeamDelete`、`SendMessage`、`Agent`、`SyntheticOutput` |
| 技能 | `LoadSkill`、`InstallSkill`、`ToolSearch` |
| 会话 | `EnterWorktree`、`ExitWorktree`、`ExitPlanMode`、`AskUser` |
| 扩展 | `mcp_call`（调用 MCP 服务器上的工具） |

## 项目结构

```
kyncode/
├── __main__.py         # CLI 入口：TUI / -p 非交互 / --remote 远程 / teammate worker
├── app.py              # Textual TUI 应用
├── agent.py            # Agent 主循环与工具执行编排
├── client.py           # LLM 客户端（Anthropic / OpenAI / OpenAI 兼容）
├── conversation.py     # 会话管理与上下文组装
├── config.py           # 配置加载、合并与校验
├── tools/              # 内建工具 + ToolRuntime 执行管线
├── permissions/        # 权限检查、审批、规则与路径沙箱
├── hooks/              # 事件钩子引擎
├── mcp/                # MCP 客户端与加载策略
├── memory/             # 长期记忆存储与召回
├── context/            # 上下文窗口管理（压缩、预算）
├── agents/             # 子 Agent 定义、加载与任务管理
├── teams/              # 多 Agent 团队协作（任务板、邮箱、spawn）
├── skills/             # 技能加载与执行
├── commands/           # 斜杠命令系统
├── sandbox/            # 平台级命令沙箱
├── web/                # 联网工具支撑（URL 防护、HTML 提取）
├── worktree/           # Git worktree 会话隔离
└── filehistory/        # 文件快照历史（rewind 支持）
```

运行时数据统一放在项目根目录的 `.kyncode/`（日志、记忆、权限规则、技能缓存等），该目录已被 `.gitignore` 排除。

## 开发与测试

```bash
uv sync              # 安装依赖（含 dev 依赖组）
uv run pytest        # 运行全量测试
uv run ruff check .  # 代码检查
```

测试使用 pytest + pytest-asyncio，`asyncio_mode = "auto"` 已启用，异步测试无需显式标记。

## License

本项目采用 [MIT License](./LICENSE)。
