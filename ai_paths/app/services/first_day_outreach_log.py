from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_SECRET_KEYS = {
    "authorization",
    "cookie",
    "set-cookie",
    "api_key",
    "apikey",
    "access_key",
    "accesskey",
    "secret",
    "client_secret",
    "password",
    "access_token",
    "refresh_token",
    "id_token",
    "token",
    "bearer_token",
    "x_api_key",
    "ossaccesskeyid",
}
_SIGNED_QUERY_KEYS = {
    "ossaccesskeyid",
    "signature",
    "x-signature",
    "x-expires",
    "expires",
    "security-token",
    "x-oss-security-token",
    "token",
    "access_token",
    "api_key",
    "apikey",
}
_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)


def redact_first_day_log_value(value: Any) -> Any:
    """Return a JSON-compatible copy with credentials and signed URL secrets removed."""
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if normalized in {item.replace("-", "_") for item in _SECRET_KEYS}:
                result[str(key)] = "[REDACTED]"
            else:
                result[str(key)] = redact_first_day_log_value(item)
        return result
    if isinstance(value, list):
        return [redact_first_day_log_value(item) for item in value]
    if isinstance(value, tuple):
        return [redact_first_day_log_value(item) for item in value]
    if isinstance(value, str):
        return _URL_RE.sub(lambda match: _redact_url(match.group(0)), value)
    return value


def _redact_url(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not parts.query:
        return url
    query = []
    changed = False
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        normalized = key.lower()
        if normalized in _SIGNED_QUERY_KEYS or normalized.startswith("x-amz-"):
            query.append((key, "[REDACTED]"))
            changed = True
        else:
            query.append((key, value))
    if not changed:
        return url
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
