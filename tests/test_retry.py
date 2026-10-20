"""kyncode/retry.py 通用退避重试的单元测试。"""

from __future__ import annotations

import pytest

from kyncode.retry import (
    BackoffConfig,
    _retry_decision,
    is_retryable,
    retry_async,
    retry_sync,
)


class RateLimitError(Exception):
    """模拟 SDK 限流异常，类型名被 is_retryable 识别。"""


class ServerError(Exception):
    status_code = 503


class ClientError(Exception):
    status_code = 400


def _cfg(**kw) -> BackoffConfig:
    """极短退避，避免测试真实等待。"""
    base: dict = dict(base=0.001, cap=0.001, max_retries=3)
    base.update(kw)
    return BackoffConfig(**base)


def _is_timeout(exc: BaseException) -> bool:
    return isinstance(exc, TimeoutError)


# ---- is_retryable 分类 ----


def test_is_retryable_timeout_and_connection():
    assert is_retryable(TimeoutError())
    assert is_retryable(ConnectionError())


def test_is_retryable_by_type_name():
    assert is_retryable(RateLimitError())


def test_is_retryable_by_status_code():
    assert is_retryable(ServerError())
    assert not is_retryable(ClientError())


def test_is_retryable_rejects_domain_errors():
    assert not is_retryable(ValueError("bad"))
    assert not is_retryable(KeyError("missing"))


# ---- retry_async ----


@pytest.mark.asyncio
async def test_retry_async_succeeds_first_try():
    calls: list[int] = []

    async def fn():
        calls.append(1)
        return "ok"

    assert await retry_async(fn, config=_cfg(max_retries=2)) == "ok"
    assert calls == [1]


@pytest.mark.asyncio
async def test_retry_async_retries_until_success():
    calls: list[int] = []

    async def fn():
        calls.append(1)
        if len(calls) < 3:
            raise RateLimitError()
        return "recovered"

    assert await retry_async(fn, config=_cfg(max_retries=5)) == "recovered"
    assert calls == [1, 1, 1]


@pytest.mark.asyncio
async def test_retry_async_raises_non_retryable_immediately():
    calls: list[int] = []

    async def fn():
        calls.append(1)
        raise ValueError("bad")

    with pytest.raises(ValueError):
        await retry_async(fn, config=_cfg(max_retries=3))
    assert calls == [1]


@pytest.mark.asyncio
async def test_retry_async_exhausts_max_retries():
    calls: list[int] = []

    async def fn():
        calls.append(1)
        raise RateLimitError()

    with pytest.raises(RateLimitError):
        await retry_async(fn, config=_cfg(max_retries=2))
    assert len(calls) == 3  # 1 次初始 + 2 次重试


@pytest.mark.asyncio
async def test_retry_async_custom_retryable_predicate():
    calls: list[int] = []

    async def fn():
        calls.append(1)
        if len(calls) < 2:
            raise RuntimeError("transient")
        return "ok"

    # RuntimeError 不是默认可重试，但自定义谓词放行
    result = await retry_async(
        fn,
        config=_cfg(max_retries=3),
        retryable=lambda exc: isinstance(exc, RuntimeError),
    )
    assert result == "ok"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_retry_async_wait_override(monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(wait: float):
        sleeps.append(wait)

    monkeypatch.setattr("kyncode.retry.asyncio.sleep", fake_sleep)

    async def fn():
        raise RateLimitError()

    with pytest.raises(RateLimitError):
        await retry_async(
            fn,
            config=_cfg(max_retries=1),
            wait_override=lambda exc: 0.5,
        )
    # 最终等待取 max(退避, override=0.5) >= 0.5
    assert sleeps and sleeps[0] >= 0.5


@pytest.mark.asyncio
async def test_retry_async_deadline_stops():
    calls: list[int] = []

    async def fn():
        calls.append(1)
        raise RateLimitError()

    # deadline=0 意味着首次失败后无剩余时间，立即抛
    with pytest.raises(RateLimitError):
        await retry_async(
            fn,
            config=BackoffConfig(base=1.0, cap=1.0, max_retries=None, deadline=0.0),
        )
    assert len(calls) == 1


# ---- retry_sync ----


def test_retry_sync_retries_until_success():
    calls: list[int] = []

    def fn():
        calls.append(1)
        if len(calls) < 2:
            raise RateLimitError()
        return "ok"

    assert retry_sync(fn, config=_cfg(max_retries=3)) == "ok"
    assert len(calls) == 2


def test_retry_sync_raises_non_retryable():
    calls: list[int] = []

    def fn():
        calls.append(1)
        raise ValueError("bad")

    with pytest.raises(ValueError):
        retry_sync(fn, config=_cfg(max_retries=3))
    assert len(calls) == 1


# ---- guard（领域守卫，与错误分类正交） ----


@pytest.mark.asyncio
async def test_retry_async_guard_false_blocks_retry():
    calls: list[int] = []

    async def fn():
        calls.append(1)
        raise RateLimitError()

    # 错误可重试，但领域守卫拒绝 → 不重试
    with pytest.raises(RateLimitError):
        await retry_async(fn, config=_cfg(max_retries=3), guard=lambda: False)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_retry_async_guard_does_not_override_classify():
    calls: list[int] = []

    async def fn():
        calls.append(1)
        raise ValueError("bad")

    # guard=True 也不能放行不可重试的错误：错误分类仍生效（AND 而非替换）
    with pytest.raises(ValueError):
        await retry_async(fn, config=_cfg(max_retries=3), guard=lambda: True)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_retry_async_guard_true_allows_retryable():
    calls: list[int] = []

    async def fn():
        calls.append(1)
        if len(calls) < 2:
            raise RateLimitError()
        return "ok"

    result = await retry_async(fn, config=_cfg(max_retries=3), guard=lambda: True)
    assert result == "ok"
    assert len(calls) == 2


# ---- _retry_decision（共享决策点，纯函数可独立测试） ----


def test_retry_decision_returns_wait():
    cfg = BackoffConfig(base=0.0, cap=0.0, max_retries=1)

    wait = _retry_decision(
        TimeoutError(), 0, cfg, _is_timeout, None, None, None, "t"
    )
    assert wait == 0.0  # base=cap=0 → full jitter 恒为 0


def test_retry_decision_raises_on_exhausted():
    cfg = BackoffConfig(base=0.0, cap=0.0, max_retries=1)

    # attempt(1) >= max_retries(1) → 放弃
    with pytest.raises(TimeoutError):
        _retry_decision(TimeoutError(), 1, cfg, _is_timeout, None, None, None, "t")


def test_retry_decision_raises_on_non_retryable():
    cfg = BackoffConfig(base=0.0, cap=0.0, max_retries=1)

    with pytest.raises(ValueError):
        _retry_decision(ValueError(), 0, cfg, _is_timeout, None, None, None, "t")
