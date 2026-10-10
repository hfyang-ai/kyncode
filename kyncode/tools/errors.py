from __future__ import annotations

from enum import Enum


class ToolErrorCode(str, Enum):
    """稳定的工具错误码，供模型与上层按「类型」而不是「文案」识别失败。

    finalize 阶段会把散落在各环节的失败归一到这些码上，这样调用方不需要
    解析人类可读的错误字符串。error_code 只表达「发生了什么类别的问题」，
    详细上下文仍保留在 ToolResult.output 里。
    """

    UNKNOWN_TOOL = "unknown_tool"
    TOOL_DISABLED = "tool_disabled"
    INVALID_ARGUMENTS = "invalid_arguments"
    PERMISSION_DENIED = "permission_denied"
    TIMEOUT = "timeout"
    EXECUTION_ERROR = "execution_error"
    INVALID_OUTPUT = "invalid_output"
