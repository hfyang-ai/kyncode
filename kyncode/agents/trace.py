from __future__ import annotations

import json
import logging
import os
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# 追踪树落盘目录。放在用户主目录而非项目目录，原因和 teams 配置一致：
# 子 agent 可能在 worktree / 独立进程里跑，工作目录会被换掉，主目录才能
# 保证 Lead 与子 agent 找到同一份落盘位置。
DEFAULT_TRACE_DIR = Path.home() / ".kyncode" / "trace"


@dataclass
class TraceNode:
    agent_id: str
    parent_id: str | None
    trace_id: str
    agent_type: str
    input_tokens: int = 0
    output_tokens: int = 0
    tool_call_count: int = 0
    start_time: float = field(default_factory=time.monotonic)
    end_time: float | None = None
    status: str = "running"

    def to_dict(self) -> dict:
        return {
            "agentId": self.agent_id,
            "parentId": self.parent_id,
            "traceId": self.trace_id,
            "agentType": self.agent_type,
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "toolCallCount": self.tool_call_count,
            "startTime": self.start_time,
            "endTime": self.end_time,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict) -> TraceNode:
        return cls(
            agent_id=data.get("agentId", ""),
            parent_id=data.get("parentId"),
            trace_id=data.get("traceId", ""),
            agent_type=data.get("agentType", ""),
            input_tokens=data.get("inputTokens", 0),
            output_tokens=data.get("outputTokens", 0),
            tool_call_count=data.get("toolCallCount", 0),
            start_time=data.get("startTime", 0.0),
            end_time=data.get("endTime"),
            status=data.get("status", "running"),
        )


class TraceManager:
    def __init__(self, state_dir: str | Path | None = None) -> None:
        self._nodes: dict[str, TraceNode] = {}
        self._state_dir = Path(state_dir) if state_dir else DEFAULT_TRACE_DIR

    # ------------------------------------------------------------------
    # 运行时父子树
    # ------------------------------------------------------------------

    def register_lead(self, agent_id: str) -> TraceNode:
        """把 lead 注册为追踪树的根节点。

        lead 是整棵树的根：parent_id 为空，trace_id 取它自己的 agent_id，
        后续所有子 agent 继承这个 trace_id（见 AgentTool），让 get_tree /
        落盘能把同一次会话派出的所有 agent 聚合到同一个 trace 文件里。
        """
        node = TraceNode(
            agent_id=agent_id,
            parent_id=None,
            trace_id=agent_id,
            agent_type="lead",
        )
        self._nodes[agent_id] = node
        self._persist()
        return node

    def create(
        self,
        agent_type: str,
        parent_id: str | None = None,
        trace_id: str | None = None,
    ) -> TraceNode:
        agent_id = uuid.uuid4().hex[:12]
        if trace_id is None:
            trace_id = uuid.uuid4().hex[:12]

        node = TraceNode(
            agent_id=agent_id,
            parent_id=parent_id,
            trace_id=trace_id,
            agent_type=agent_type,
        )
        self._nodes[agent_id] = node
        self._persist()
        return node

    def update(self, agent_id: str, **kwargs: int | str) -> None:
        node = self._nodes.get(agent_id)
        if node is None:
            return
        for key, value in kwargs.items():
            if hasattr(node, key):
                setattr(node, key, value)
        self._persist()

    def complete(self, agent_id: str, status: str = "completed") -> None:
        node = self._nodes.get(agent_id)
        if node is None:
            return
        node.end_time = time.monotonic()
        node.status = status
        self._persist()

    def get(self, agent_id: str) -> TraceNode | None:
        return self._nodes.get(agent_id)

    def get_tree(self, trace_id: str) -> list[TraceNode]:
        return [n for n in self._nodes.values() if n.trace_id == trace_id]

    def remove(self, agent_id: str) -> None:
        self._nodes.pop(agent_id, None)
        self._persist()

    def complete_all_running(self, parent_id: str) -> None:
        changed = False
        for node in self._nodes.values():
            if node.parent_id == parent_id and node.status == "running":
                node.status = "completed"
                node.end_time = time.monotonic()
                changed = True
        if changed:
            self._persist()

    def get_total_tokens(self, trace_id: str) -> tuple[int, int]:
        total_in = 0
        total_out = 0
        for node in self._nodes.values():
            if node.trace_id == trace_id:
                total_in += node.input_tokens
                total_out += node.output_tokens
        return total_in, total_out

    # ------------------------------------------------------------------
    # 落盘 / 跨会话读取
    # ------------------------------------------------------------------

    def _persist(self) -> None:
        """把当前内存树按 trace_id 分文件落盘，原子写。

        落盘是尽力而为：目录只读、磁盘满等失败只记日志，绝不影响主流程。
        """
        try:
            by_trace: dict[str, list[TraceNode]] = {}
            for node in self._nodes.values():
                by_trace.setdefault(node.trace_id, []).append(node)

            self._state_dir.mkdir(parents=True, exist_ok=True)
            for trace_id, nodes in by_trace.items():
                payload = {
                    "traceId": trace_id,
                    "updatedAt": int(time.time()),
                    "nodes": [n.to_dict() for n in nodes],
                }
                target = self._state_dir / f"{trace_id}.json"
                self._atomic_write(
                    target,
                    json.dumps(payload, ensure_ascii=False, indent=2),
                )
        except Exception as e:  # noqa: BLE001 - 落盘失败不应影响主流程
            log.warning("Failed to persist agent trace: %s", e)

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        """先写临时文件再原子替换，避免写一半崩溃留下损坏的 JSON。"""
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def load(self, trace_id: str) -> list[TraceNode] | None:
        """从磁盘加载某次会话的追踪树，用于跨会话查询。"""
        path = self._state_dir / f"{trace_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return [TraceNode.from_dict(d) for d in data.get("nodes", [])]
        except (OSError, json.JSONDecodeError) as e:
            log.warning("Failed to load trace %s: %s", trace_id, e)
            return None

    def list_traces(self) -> list[dict]:
        """列出所有落盘 trace 的元数据，按更新时间倒序。"""
        if not self._state_dir.exists():
            return []
        summaries: list[dict] = []
        for path in self._state_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            nodes = [TraceNode.from_dict(d) for d in data.get("nodes", [])]
            summaries.append(
                {
                    "trace_id": data.get("traceId", path.stem),
                    "updated_at": data.get("updatedAt", 0),
                    "node_count": len(nodes),
                    "total_in": sum(n.input_tokens for n in nodes),
                    "total_out": sum(n.output_tokens for n in nodes),
                }
            )
        summaries.sort(key=lambda s: s["updated_at"], reverse=True)
        return summaries
