from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum


class PermissionResponse(Enum):
    ALLOW = "allow"
    DENY = "deny"
    ALLOW_ALWAYS = "allow_always"


@dataclass
class PermissionReply:
    """用户对一次授权请求的答复。

    feedback 是拒绝时顺带输入的话，会拼进拒绝结果交给模型，让模型按用户的要求换个做法。
    """

    response: PermissionResponse
    feedback: str = ""


@dataclass
class PermissionRequest:
    tool_name: str
    description: str
    future: asyncio.Future[PermissionReply]
