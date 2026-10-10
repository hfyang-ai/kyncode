"""URL 安全校验：阻止 WebFetch/WebSearch 被诱导去访问内网或本机服务（SSRF）。

只做确定性判定，不做 DNS 之外的解析。域名先按字面挡一道，再解析成 IP 挡一道，
两道都过才放行——纯字符串判断挡不住 `http://2130706433/`（127.0.0.1 的十进制写法）
这类绕过，所以最终以解析出的 IP 为准。
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

ALLOWED_SCHEMES = {"http", "https"}


class BlockedURLError(ValueError):
    """URL 被安全策略拒绝。"""


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    # IPv4-mapped IPv6（::ffff:127.0.0.1）要拆出内层的 v4 再判一次，
    # 否则 ::ffff:127.0.0.1 会被当成普通 IPv6 放行。
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return _is_blocked_ip(ip.ipv4_mapped)
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def validate_url(url: str) -> str:
    """校验 url，返回规范化后的字符串；不合规抛 BlockedURLError。"""
    split = urlsplit(url)
    if split.scheme not in ALLOWED_SCHEMES:
        raise BlockedURLError(
            f"unsupported scheme '{split.scheme or '(none)'}', only http/https allowed"
        )
    host = split.hostname
    if not host:
        raise BlockedURLError("URL has no host")

    # 先把字面量主机名（含裸 IP、localhost）挡掉，省一次 DNS。
    if host.lower() == "localhost" or host.lower().endswith(".localhost"):
        raise BlockedURLError(f"blocked host: {host}")
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None:
        if _is_blocked_ip(literal):
            raise BlockedURLError(f"blocked address: {host}")
        return url

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise BlockedURLError(f"cannot resolve host '{host}': {e}") from e

    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if _is_blocked_ip(ip):
            raise BlockedURLError(f"blocked address: {host} -> {addr}")
    return url
