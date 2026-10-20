from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# 内置可重试错误：按类型名匹配 SDK 常见的限流 / 连接异常，避免 import 具体 SDK。
# 超时与连接错误额外用 isinstance 精确匹配 Python 内置类型。
_RETRYABLE_TYPE_NAMES = frozenset(
    {
        "RateLimitError",
        "APIConnectionError",
        "APITimeoutError",
        "APIConnectionTimeoutError",
    }
)


@dataclass
class BackoffConfig:
    base: float = 0.5
    cap: float = 10.0
    max_retries: int | None = 3
    deadline: float | None = None


def is_retryable(exc: BaseException) -> bool:
    """默认可重试错误分类：超时、网络连接、限流、服务端 5xx。

    认证失败、参数错误、其他 4xx 不可重试。
    """
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    if type(exc).__name__ in _RETRYABLE_TYPE_NAMES:
        return True
    status = getattr(exc, "status_code", None)
    return isinstance(status, int) and 500 <= status < 600


def _compute_wait(attempt: int, config: BackoffConfig) -> float:
    """第 ``attempt`` 次重试前的等待时长（full jitter，秒）。"""
    raw = min(config.cap, config.base * (2**attempt))
    return random.uniform(0.0, raw)


def _log_retry(scope: str, attempt: int, exc: BaseException, wait: float) -> None:
    logger.info(
        "retry %s",
        json.dumps(
            {
                "scope": scope,
                "attempt": attempt,
                "error": type(exc).__name__,
                "wait_ms": round(wait * 1000, 2),
            },
            ensure_ascii=False,
        ),
    )


def _retry_decision(
    exc: BaseException,
    attempt: int,
    config: BackoffConfig,
    classify: Callable[[BaseException], bool],
    guard: Callable[[], bool] | None,
    wait_override: Callable[[BaseException], float | None] | None,
    deadline: float | None,
    scope: str,
) -> float:

    if not classify(exc) or (guard is not None and not guard()):
        raise exc
    if config.max_retries is not None and attempt >= config.max_retries:
        raise exc
    wait = _compute_wait(attempt, config)
    if wait_override is not None:
        override = wait_override(exc)
        if override is not None:
            wait = max(wait, override)
    if deadline is not None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise exc
        wait = min(wait, remaining)
    _log_retry(scope, attempt + 1, exc, wait)
    return wait


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    *,
    config: BackoffConfig | None = None,
    retryable: Callable[[BaseException], bool] | None = None,
    guard: Callable[[], bool] | None = None,
    wait_override: Callable[[BaseException], float | None] | None = None,
    scope: str = "retry_async",
) -> T:

    cfg = config or BackoffConfig()
    classify = retryable or is_retryable
    deadline = time.monotonic() + cfg.deadline if cfg.deadline is not None else None

    attempt = 0
    while True:
        try:
            return await fn()
        except Exception as exc:
            wait = _retry_decision(
                exc, attempt, cfg, classify, guard, wait_override, deadline, scope
            )
            await asyncio.sleep(wait)
            attempt += 1


def retry_sync(
    fn: Callable[[], T],
    *,
    config: BackoffConfig | None = None,
    retryable: Callable[[BaseException], bool] | None = None,
    guard: Callable[[], bool] | None = None,
    wait_override: Callable[[BaseException], float | None] | None = None,
    scope: str = "retry_sync",
) -> T:
    """同步重试，语义与 :func:`retry_async` 一致，用 ``time.sleep`` 等待。"""
    cfg = config or BackoffConfig()
    classify = retryable or is_retryable
    deadline = time.monotonic() + cfg.deadline if cfg.deadline is not None else None

    attempt = 0
    while True:
        try:
            return fn()
        except Exception as exc:
            wait = _retry_decision(
                exc, attempt, cfg, classify, guard, wait_override, deadline, scope
            )
            time.sleep(wait)
            attempt += 1
