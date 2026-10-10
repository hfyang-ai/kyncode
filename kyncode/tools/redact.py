from __future__ import annotations

import re

# 敏感信息脱敏：Token / 密钥、邮箱、个人数据（手机号 / 身份证 / 银行卡）。
# 规则保持保守，宁可漏一点也不要把正常内容误伤；替换成带类别的标签，方便
# 审计时知道「这里原本是什么」，模型也能据此理解结果里缺了哪类信息。

# 有明确前缀的令牌 / 密钥。前缀特异性足够高，误伤概率低。
_TOKEN = re.compile(
    r"(?i)"
    r"(?:"
    r"sk-[a-z0-9_-]{8,}"              # OpenAI / Anthropic 风格
    r"|ghp_[a-z0-9]{20,}"             # GitHub classic PAT
    r"|github_pat_[a-z0-9_]{20,}"     # GitHub fine-grained PAT
    r"|xox[bpas]-[a-z0-9-]{10,}"      # Slack token
    r"|AKIA[0-9A-Z]{16}"              # AWS access key id
    r"|ASIA[0-9A-Z]{16}"              # AWS temporary key id
    r"|eyJ[a-z0-9_-]{8,}\.[a-z0-9_-]{8,}\.[a-z0-9_-]{4,}"  # JWT
    r")"
)

_BEARER = re.compile(r"(?i)\bBearer\s+[a-z0-9._~+/-]{16,}")

_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)

_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

# 中国手机号：1[3-9] 开头共 11 位。
_CN_MOBILE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")

# 中国身份证 18 位：6 位地区 + 1900~2099 生日 + 3 位顺序 + 校验位（含 X）。
# 带日期范围约束，比「18 位纯数字」严格，避免误伤普通 ID。
_CN_ID = re.compile(
    r"(?<!\d)\d{6}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)"
)

# 银行卡：16~19 位数字。放在身份证之后处理，避免重复命中。
_BANK_CARD = re.compile(r"(?<!\d)\d{16,19}(?!\d)")


def redact_sensitive(text: str) -> str:
    """对一段文本做保守脱敏，返回脱敏后的副本。"""
    if not text:
        return text

    # 顺序敏感：先整块密钥，再前缀 token，再邮箱，再身份证 → 手机号 → 银行卡。
    text = _PRIVATE_KEY_BLOCK.sub("[REDACTED_KEY]", text)
    text = _BEARER.sub("[REDACTED_TOKEN]", text)
    text = _TOKEN.sub("[REDACTED_TOKEN]", text)
    text = _EMAIL.sub("[REDACTED_EMAIL]", text)
    text = _CN_ID.sub("[REDACTED_ID]", text)
    text = _CN_MOBILE.sub("[REDACTED_PHONE]", text)
    text = _BANK_CARD.sub("[REDACTED_CARD]", text)
    return text
