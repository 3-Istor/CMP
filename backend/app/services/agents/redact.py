"""
Secret values out of everything the MCP server returns to a model (CNP-311).

Whatever reaches an assistant ends up in a model context the platform does
not control. A tool answer can carry a secret it never meant to: a Vault
value in an app's config, a connection string in an error, a token in a log
line. Every answer goes through ``mask`` on the way out, whatever the tool.

Two signals, either is enough: the key says it holds a secret (password,
token, api_key...), or the value has the shape of one (a GitHub or Vault
token, a JWT, a private key, the password part of a URL). Names, booleans,
URLs without credentials and the rest stay readable: an answer masked to
nothing is useless to the user.
"""

import re
from typing import Any

MASK = "[redacted]"

_SECRET_KEY = re.compile(
    r"(^|_)(password|passwd|pwd|secret|token|api_?key|private_?key"
    r"|secret_?key|access_?key|signing_?key|encryption_?key|master_?key"
    r"|client_?secret|credentials?|dsn|connection_?string|conn_?str"
    r"|authorization|cookie)$"
)
# Environment variable names, as in {"name": "DB_PASSWORD", "value": "..."}.
_SECRET_ENV_NAME = re.compile(
    r"(PASSWORD|PASSWD|SECRET|TOKEN|_KEY$|^KEY$|CREDENTIAL|_DSN$|_PASS$)"
)
_ENV_STYLE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_SHAPES = (
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----"
        r"(?:[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----|[\s\S]*)"
    ),
    re.compile(r"\bgh[opsur]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+"),
    re.compile(r"\bhv[sbr]\.[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"),
    re.compile(r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{32,}"),
    re.compile(r"\bxox[abpr]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}"),
)
# The password of scheme://user:password@host, the user and host kept.
_URL_PASSWORD = re.compile(r"\b([a-z][a-z0-9+.-]*://[^\s:/@]+:)[^\s@/]+@")
# Bearer <token>, and NAME=value or NAME: value where NAME says secret.
_BEARER = re.compile(r"\b([Bb]earer|[Bb]asic)\s+[A-Za-z0-9._~+/=-]{8,}")
_ASSIGNED = re.compile(
    r"(?i)\b([a-z0-9_]*(?:password|passwd|secret|token|api_?key)[a-z0-9_]*"
    r"[\"']?\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;&\"'}]+)"
)


def _normalized(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")


def secret_key(key: str) -> bool:
    """A key that holds a secret: api_key, or an env name like STRIPE_KEY."""
    if _ENV_STYLE.match(key) and _SECRET_ENV_NAME.search(key):
        return True
    return bool(_SECRET_KEY.search(_normalized(key)))


def mask_text(text: str) -> str:
    for shape in _SHAPES:
        text = shape.sub(MASK, text)
    text = _URL_PASSWORD.sub(rf"\1{MASK}@", text)
    text = _BEARER.sub(rf"\1 {MASK}", text)
    return _ASSIGNED.sub(_assigned, text)


def _assigned(match: re.Match) -> str:
    value = match.group(2)
    return match.group(1) + (value if value in ('""', "''") else MASK)


def _env_pair(value: dict) -> bool:
    name = value.get("name", value.get("key"))
    return (
        isinstance(name, str)
        and isinstance(value.get("value"), str)
        and bool(_SECRET_ENV_NAME.search(name.upper()))
    )


def mask(value: Any) -> Any:
    """``value`` with every secret replaced by ``MASK``, structure kept."""
    if isinstance(value, str):
        return mask_text(value)
    if isinstance(value, (list, tuple)):
        return [mask(item) for item in value]
    if isinstance(value, dict):
        hidden_value = _env_pair(value)
        return {
            key: (
                MASK
                if isinstance(item, str)
                and item
                and (
                    (isinstance(key, str) and secret_key(key))
                    or (hidden_value and key == "value")
                )
                else mask(item)
            )
            for key, item in value.items()
        }
    return value
