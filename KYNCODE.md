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
uv run ruff check .                                   # 代码检查
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
