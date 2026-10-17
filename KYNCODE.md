# KynCode

终端 AI 编程助手。把 LLM 接入命令行，通过内建工具、权限控制与多 Agent 协作，在项目目录里读代码、改文件、跑命令。完整文档见 [README.md](./README.md)。

## 技术栈

- Python >= 3.11
- uv（依赖与环境管理）
- Textual（TUI）
- pydantic（数据校验）
- pytest + pytest-asyncio（测试）

## 快速开始

```bash
uv sync                              # 安装依赖
cp config.yaml.example config.yaml   # 生成配置，填入 API Key
uv run kyncode                       # 启动交互式 TUI
```

## 常用命令

```bash
uv run kyncode                                        # 交互式 TUI
uv run kyncode -p "解释这个项目"                       # 非交互执行，打印结果
uv run kyncode -p "..." --output-format stream-json   # NDJSON 流式事件
uv run kyncode --remote                               # 远程模式（浏览器 :18888）
uv run pytest                                         # 全量测试
uv run ruff check --select F,I,E4,E7,E9 .             # 代码检查
```

## 目录结构

```
kyncode/
├── __main__.py     # CLI 入口（TUI / -p / --remote / teammate worker）
├── agent.py        # Agent 主循环与工具执行编排
├── client.py       # LLM 客户端（Anthropic / OpenAI / OpenAI 兼容）
├── config.py       # 配置加载、合并与校验
├── tools/          # 内建工具 + ToolRuntime 三段管线
├── permissions/    # 权限检查、审批、规则与路径沙箱
├── mcp/            # MCP 客户端与加载策略
├── memory/         # 长期记忆存储与召回
├── context/        # 上下文窗口管理（压缩、预算）
├── agents/         # 子 Agent 定义、加载与任务管理
├── teams/          # 多 Agent 团队协作
├── hooks/          # 事件钩子引擎
├── skills/         # 技能加载与执行
├── commands/       # 斜杠命令系统
├── web/            # 联网工具支撑（URL 防护、HTML 提取）
├── worktree/       # Git worktree 会话隔离
└── sandbox/        # 平台级命令沙箱
```

运行时数据统一放在项目根的 `.kyncode/`（日志、记忆、权限规则、技能缓存），已被 `.gitignore` 排除。

## 代码规范

### Python

- 最低版本 3.11。可用现代语法：`X | None`、`list[X]`、`from __future__ import annotations`（全库 138 个文件已统一使用）。`Generator` 等注解写全三参（`Generator[None, None, None]`），单参简写仅 3.13+。
- dataclass 优先于手写样板；跨模块类型用 `collections.abc`（如 `Mapping`、`Generator`），不用 `typing` 旧别名。

### Lint

- 用 ruff（当前 0.17.x），检查集合固定为 `--select F,I,E4,E7,E9`（未在 pyproject 配置 `[tool.ruff]`，手动传入）。
- 改完代码先 `uv run ruff check --select F,I,E4,E7,E9 <changed files>`，通过后再跑测试。

### 测试

- pytest + pytest-asyncio，`asyncio_mode = "auto"`（async 测试无需显式 `@pytest.mark.asyncio`）。
- 全量：`uv run pytest tests/ -q --basetemp=/tmp/kyn_<suffix>`（basetemp 必须用 /tmp 或每次换新名，见「注意事项」）。
- 每个新行为配对应测试；改阈值/常量时同步更新相关断言。

### 语言策略

- 代码、注释、commit message、工具 description、子 agent frontmatter 的 `description`：英文。
- KYNCODE.md、子 agent 正文、面向用户的提示词与提醒文本：简体中文。

### 提交

- 英文 Conventional Commits：`feat(scope):` / `fix(scope):` / `refactor(scope):` / `style(scope):`。
- 只提交已暂存内容，不代 `git add`；执行 `git commit` 前先展示 message 等待确认。

## 注意事项

- **测试 basetemp 不能落在项目目录内**：命令沙箱 shim 会拦截对大量文件的 `rm -rf`，`.tmp_*` 落在项目内还会被 `load_instructions` 向上读到根目录 KYNCODE.md 造成假失败。统一用 `/tmp/kyn_<suffix>` 或每次换新名。
- **工作区文件可能被外部进程还原**：大改动用单次整文件重写，不要依赖多次增量编辑的中间状态。
- **代码/配置里的引号、定界符、路径一律用 ASCII 直引号**（`"` / `'`）；仅中文自然语言内容（正文、注释、文档）才用全角标点。
- 代码与文档中不使用 emoji（除非明确要求）。
