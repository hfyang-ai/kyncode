from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from kyncode.conversation import ConversationManager, Message
from kyncode.skills.parser import SkillDef, substitute_arguments

if TYPE_CHECKING:
    from kyncode.agent import Agent
    from kyncode.client import LLMClient

log = logging.getLogger(__name__)

FORK_RECENT_COUNT = 5


class SkillExecutor:
    def __init__(
        self,
        agent: Agent,
        client: LLMClient,
        protocol: str,
    ) -> None:
        self.agent = agent
        self.client = client
        self.protocol = protocol

    def execute_inline(self, skill: SkillDef, args: str) -> None:
        prompt = substitute_arguments(skill.prompt_body, args)
        self.agent.activate_skill(skill.name, prompt)
        if getattr(self.agent, "recovery_state", None) is not None:
            self.agent.recovery_state.record_skill_invocation(skill.name, prompt)

    async def execute_fork(self, skill: SkillDef, args: str) -> str:
        prompt = substitute_arguments(skill.prompt_body, args)
        if getattr(self.agent, "recovery_state", None) is not None:
            self.agent.recovery_state.record_skill_invocation(
                skill.name, skill.prompt_body
            )

        fork_conv = ConversationManager()

        context_messages = self._build_fork_context(skill.context)
        for msg in context_messages:
            if msg.role == "user":
                fork_conv.add_user_message(msg.content)
            else:
                fork_conv.add_assistant_message(msg.content)

        fork_conv.add_user_message(prompt)

        from kyncode.agent import (
            Agent as AgentClass,
        )
        from kyncode.agent import (
            ErrorEvent,
            LoopComplete,
            StreamText,
        )
        from kyncode.permissions import (
            DangerousCommandDetector,
            PathSandbox,
            PermissionChecker,
            PermissionMode,
            RuleEngine,
        )

        # skill fork 是非交互 worker（没有 UI 弹窗），沿用 DEFAULT 会让写/命令
        # 落到 ask 后被非交互路径拒绝，因此用 BYPASS 放行。但仍保留三道闸：
        # 危险命令黑名单、受保护路径写入、父级 deny 规则（复用父 agent 的
        # 规则引擎，父级配置的 allow/deny/ask 同样约束 skill）。
        parent_checker = self.agent.permission_checker
        rule_engine = parent_checker.rule_engine if parent_checker else RuleEngine()
        checker = PermissionChecker(
            detector=DangerousCommandDetector(),
            sandbox=PathSandbox(self.agent.work_dir),
            rule_engine=rule_engine,
            mode=PermissionMode.BYPASS,
        )

        fork_agent = AgentClass(
            client=self.client,
            registry=self.agent.registry,
            protocol=self.protocol,
            work_dir=self.agent.work_dir,
            max_iterations=self.agent.max_iterations,
            permission_checker=checker,
            context_window=self.agent.context_window,
        )

        result_parts: list[str] = []
        async for event in fork_agent.run(fork_conv):
            if isinstance(event, StreamText):
                result_parts.append(event.text)
            elif isinstance(event, ErrorEvent):
                result_parts.append(f"\n[Error: {event.message}]")
            elif isinstance(event, LoopComplete):
                break

        return "".join(result_parts)

    def _build_fork_context(self, mode: str) -> list[Message]:
        if mode == "none":
            return []

        history = (
            self.agent._conversation.history
            if hasattr(self.agent, "_conversation")
            else []
        )
        if not history:
            main_history = []
        else:
            main_history = history

        if mode == "recent":
            content_messages = [
                m for m in main_history if m.content and not m.tool_results
            ]
            return content_messages[-FORK_RECENT_COUNT:]

        if mode == "full":
            content_messages = [
                m for m in main_history if m.content and not m.tool_results
            ]
            if not content_messages:
                return []
            summary_parts = []
            for m in content_messages:
                prefix = "User" if m.role == "user" else "Assistant"
                text = m.content[:200]
                if len(m.content) > 200:
                    text += "..."
                summary_parts.append(f"{prefix}: {text}")
            summary = "## Previous conversation summary\n\n" + "\n\n".join(
                summary_parts
            )
            return [Message(role="user", content=summary)]

        return []
