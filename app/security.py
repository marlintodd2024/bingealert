"""Small security helpers shared by request and background code."""
from __future__ import annotations

import ipaddress
import logging
import socket
from email.utils import parseaddr
from html import escape
from urllib.parse import urlparse, urlunparse


logger = logging.getLogger(__name__)

_BLOCKED_HOSTS = {"localhost", "metadata.google.internal"}
_BLOCKED_IP_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "0.0.0.0/8",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "::/128",
        "::1/128",
        "fe80::/10",
        "fc00::/7",
    )
)


def sanitize_for_log(value: object, max_len: int = 200) -> str:
    """Return a single-line representation safe to interpolate into logs."""
    text = str(value) if value is not None else ""
    text = text.replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t")
    return text[:max_len]


def html_escape(value: object) -> str:
    return escape(str(value) if value is not None else "", quote=True)


def clean_email_address(value: str | None) -> str:
    """Return a simple addr-spec from a setting, or empty string if invalid."""
    if not value:
        return ""
    _, addr = parseaddr(value)
    if not addr or "@" not in addr or any(c in addr for c in "\r\n"):
        return ""
    return addr


def validate_ip_or_cidr_csv(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    cleaned: list[str] = []
    for item in raw.split(","):
        part = item.strip()
        if not part:
            continue
        try:
            if "/" in part:
                ipaddress.ip_network(part, strict=False)
            else:
                ipaddress.ip_address(part)
        except ValueError as e:
            raise ValueError(f"Invalid IP/CIDR value: {part}") from e
        cleaned.append(part)
    return ",".join(cleaned)


def _host_is_blocked(host: str) -> bool:
    host_l = host.strip("[]").lower().rstrip(".")
    if not host_l or host_l in _BLOCKED_HOSTS or host_l.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(host_l)
        return any(ip in network for network in _BLOCKED_IP_NETWORKS)
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(host_l, None)
    except socket.gaierror:
        # Let connection-time DNS errors surface normally; we still block
        # obvious local names above and any literal blocked IPs.
        return False
    except OSError as e:
        logger.debug("URL host resolution failed for %s: %s", host_l, e)
        return False

    for info in infos:
        resolved = info[4][0]
        try:
            ip = ipaddress.ip_address(resolved)
        except ValueError:
            continue
        if any(ip in network for network in _BLOCKED_IP_NETWORKS):
            return True
    return False


def normalize_http_url(value: str | None, *, allow_empty: bool = False) -> str:
    """Validate and normalize a user-configured outbound HTTP(S) base URL.

    BingeAlert is often deployed on a home LAN, so RFC1918 addresses are valid
    integration targets. We block loopback, link-local, wildcard, localhost,
    metadata-service hosts, non-HTTP schemes, fragments, and embedded userinfo.
    """
    raw = (value or "").strip()
    if not raw:
        if allow_empty:
            return ""
        raise ValueError("URL is required")

    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("URL must start with http:// or https://")
    if not parsed.netloc or not parsed.hostname:
        raise ValueError("URL must include a host")
    if parsed.username or parsed.password:
        raise ValueError("URL must not include username or password")
    if parsed.fragment:
        raise ValueError("URL must not include a fragment")
    if _host_is_blocked(parsed.hostname):
        raise ValueError("URL host is not allowed")

    normalized = parsed._replace(path=parsed.path.rstrip("/"), params="", query="", fragment="")
    return urlunparse(normalized).rstrip("/")
